import requests
import sqlite3
import time
import functools
import os
import threading
import json
from datetime import datetime, date, timedelta
from flask import Flask, render_template, jsonify, request, send_from_directory, redirect
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers import (
    parse_registration_credential_json,
    parse_authentication_credential_json,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    PublicKeyCredentialDescriptor,
    AuthenticatorTransport,
    AttestationConveyancePreference,
    ResidentKeyRequirement,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier

app = Flask(__name__)

# --- CONFIGURATION ---
URL = "https://api.trycrew.com/willow/graphql"
# In app.py
DB_FILE = os.environ.get("DB_FILE", "savings_data.db")

def get_or_create_secret_key():
    """Get secret key from database, or generate and save a new one"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Create app_config table if it doesn't exist
    c.execute('''CREATE TABLE IF NOT EXISTS app_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Try to get existing secret key
    c.execute("SELECT value FROM app_config WHERE key = 'secret_key' LIMIT 1")
    row = c.fetchone()

    if row:
        secret_key = row[0]
    else:
        # Generate new secret key and save it
        secret_key = os.urandom(24).hex()
        c.execute("INSERT INTO app_config (key, value) VALUES ('secret_key', ?)", (secret_key,))
        conn.commit()
        print("✅ Generated and saved new SECRET_KEY to database")

    conn.close()
    return secret_key

# Configure Flask-Login
app.secret_key = get_or_create_secret_key()
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Redirect to login page

# User model
class User(UserMixin):
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    """Load user from database for Flask-Login session"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, email FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return User(row[0], row[1], row[2])
    return None

# WebAuthn configuration
RP_ID = os.environ.get('RP_ID', 'localhost')  # Relying Party ID (your domain)
RP_NAME = "SimpleCrew"
ORIGIN = os.environ.get('ORIGIN', 'http://localhost:8080')

# Global flag to ensure background thread starts only once
_background_thread_started = False
_background_thread_lock = threading.Lock()

# Track last SimpleFin sync time per account (limit to once per hour per account)
_last_simplefin_sync = {}  # Dictionary: account_id -> timestamp
_simplefin_sync_interval = 3600  # 1 hour in seconds

def get_simplefin_sync_interval():
    """Get the SimpleFin sync interval from database or return default"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT sync_interval FROM simplefin_config LIMIT 1")
        row = c.fetchone()
        conn.close()
        return int(row[0]) if row and row[0] else 3600
    except Exception as e:
        print(f"Error getting sync interval: {e}")
        return 3600

def should_sync_simplefin(account_id):
    """Check if SimpleFin account should sync now based on schedule or interval"""
    import json
    from datetime import datetime, timezone

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT sync_times, sync_timezone FROM simplefin_config LIMIT 1")
        row = c.fetchone()
        conn.close()

        # If scheduled times are configured, use time-based sync
        if row and row[0]:
            sync_times = json.loads(row[0])  # Array of UTC times like ["14:00", "02:00"]

            # Get current UTC time
            now_utc = datetime.now(timezone.utc)
            current_time = now_utc.strftime("%H:%M")

            # Check if we're within 5 minutes of any scheduled time
            for scheduled_time in sync_times:
                scheduled_hour, scheduled_min = map(int, scheduled_time.split(":"))

                # Calculate time difference in minutes
                current_minutes = now_utc.hour * 60 + now_utc.minute
                scheduled_minutes = scheduled_hour * 60 + scheduled_min
                diff = abs(current_minutes - scheduled_minutes)

                # Account for day wrap-around
                if diff > 720:  # More than 12 hours difference
                    diff = 1440 - diff

                # If within 5 minutes of scheduled time, check if we already synced recently
                if diff <= 5:
                    last_sync = _last_simplefin_sync.get(account_id, 0)
                    # Only sync if we haven't synced in the last 10 minutes
                    if time.time() - last_sync > 600:
                        return True, f"scheduled time {scheduled_time} UTC"

            return False, "not scheduled time"
        else:
            # Fall back to interval-based sync
            sync_interval = get_simplefin_sync_interval()
            current_time = time.time()
            last_sync = _last_simplefin_sync.get(account_id, 0)
            time_since_last_sync = current_time - last_sync

            if time_since_last_sync < sync_interval:
                minutes_remaining = int((sync_interval - time_since_last_sync) / 60)
                return False, f"next sync in {minutes_remaining} minutes"

            return True, "interval elapsed"
    except Exception as e:
        print(f"Error checking sync schedule: {e}")
        # Fall back to interval-based sync on error
        sync_interval = get_simplefin_sync_interval()
        current_time = time.time()
        last_sync = _last_simplefin_sync.get(account_id, 0)
        time_since_last_sync = current_time - last_sync

        if time_since_last_sync >= sync_interval:
            return True, "interval elapsed (fallback)"

        return False, "error, using interval fallback"

# --- CACHING SYSTEM ---
class SimpleCache:
    def __init__(self, ttl_seconds=300):
        self.store = {}
        self.ttl = ttl_seconds

    def get(self, key):
        if key in self.store:
            timestamp, data = self.store[key]
            if time.time() - timestamp < self.ttl:
                return data
            else:
                del self.store[key]  # Expired
        return None

    def set(self, key, data):
        self.store[key] = (time.time(), data)

    def clear(self):
        self.store = {}

cache = SimpleCache(ttl_seconds=300)

def cached(key_prefix):
    """Decorator to cache function results. Supports force_refresh=True kwarg."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            force_refresh = kwargs.pop('force_refresh', False)
            key_parts = [key_prefix] + [str(arg) for arg in args] + [f"{k}={v}" for k, v in kwargs.items()]
            cache_key = ":".join(key_parts)
            
            if not force_refresh:
                cached_data = cache.get(cache_key)
                if cached_data:
                    print(f"⚡ Serving {key_prefix} from cache")
                    return cached_data
            
            print(f"🌐 Fetching {key_prefix} from API (Fresh)...")
            result = func(*args, **kwargs)
            
            if isinstance(result, dict) and "error" not in result:
                cache.set(cache_key, result)
            return result
        return wrapper
    return decorator


# 1. UPDATE DATABASE SCHEMA
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history (date TEXT PRIMARY KEY, balance REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE)''')
    
    # Updated to include sort_order
    c.execute('''CREATE TABLE IF NOT EXISTS pocket_links (
        pocket_id TEXT PRIMARY KEY, 
        group_id INTEGER,
        sort_order INTEGER DEFAULT 0
    )''')
    
    # Migration helper: Check if sort_order exists, if not, add it (for existing DBs)
    try:
        c.execute("SELECT sort_order FROM pocket_links LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding sort_order column...")
        c.execute("ALTER TABLE pocket_links ADD COLUMN sort_order INTEGER DEFAULT 0")
    
    # SimpleFin global configuration (one access URL for all accounts)
    c.execute('''CREATE TABLE IF NOT EXISTS simplefin_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        access_url TEXT NOT NULL,
        is_valid INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Store credit card account selection from LunchFlow or SimpleFin
    c.execute('''CREATE TABLE IF NOT EXISTS credit_card_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT UNIQUE NOT NULL,
        account_name TEXT,
        pocket_id TEXT,
        provider TEXT DEFAULT 'lunchflow',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Migration: Add pocket_id column if it doesn't exist
    try:
        c.execute("SELECT pocket_id FROM credit_card_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding pocket_id column to credit_card_config...")
        c.execute("ALTER TABLE credit_card_config ADD COLUMN pocket_id TEXT")

    # Migration: Add provider column if it doesn't exist
    try:
        c.execute("SELECT provider FROM credit_card_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding provider column to credit_card_config...")
        c.execute("ALTER TABLE credit_card_config ADD COLUMN provider TEXT DEFAULT 'lunchflow'")

    # Migration: Add current_balance column if it doesn't exist (for tables already in new format)
    try:
        c.execute("SELECT current_balance FROM credit_card_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding current_balance column to credit_card_config...")
        c.execute("ALTER TABLE credit_card_config ADD COLUMN current_balance REAL DEFAULT 0")
        conn.commit()

    # Migration: Add batch_mode column if it doesn't exist (1 = batch transfers, 0 = individual transfers)
    try:
        c.execute("SELECT batch_mode FROM credit_card_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding batch_mode column to credit_card_config...")
        c.execute("ALTER TABLE credit_card_config ADD COLUMN batch_mode INTEGER DEFAULT 1")
        conn.commit()

    # Migration: Move simplefin_access_url to new simplefin_config table
    # First check if credit_card_config has the old column with data
    has_old_data = False
    old_access_url = None
    try:
        c.execute("SELECT simplefin_access_url FROM credit_card_config WHERE simplefin_access_url IS NOT NULL LIMIT 1")
        old_url_row = c.fetchone()
        if old_url_row and old_url_row[0]:
            has_old_data = True
            old_access_url = old_url_row[0]
            print(f"📦 Found SimpleFin access URL in old location: {old_access_url[:30]}...", flush=True)
    except sqlite3.OperationalError:
        # Column doesn't exist, no migration needed
        pass

    # If we have old data, migrate it to simplefin_config
    if has_old_data and old_access_url:
        # Check if simplefin_config already has data
        c.execute("SELECT access_url FROM simplefin_config LIMIT 1")
        existing_url = c.fetchone()
        if not existing_url:
            print("🔄 Migrating SimpleFin access URL to new table...", flush=True)
            c.execute("INSERT INTO simplefin_config (access_url) VALUES (?)", (old_access_url,))
            conn.commit()
            print("✅ Migrated SimpleFin access URL successfully", flush=True)
        else:
            print("⚠️ SimpleFin config already exists, skipping migration", flush=True)

    # Migration: Add is_valid column to simplefin_config if it doesn't exist
    try:
        c.execute("SELECT is_valid FROM simplefin_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding is_valid column to simplefin_config...")
        c.execute("ALTER TABLE simplefin_config ADD COLUMN is_valid INTEGER DEFAULT 1")
        conn.commit()

    # Migration: Add last_sync column to simplefin_config if it doesn't exist
    try:
        c.execute("SELECT last_sync FROM simplefin_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding last_sync column to simplefin_config...")
        c.execute("ALTER TABLE simplefin_config ADD COLUMN last_sync TEXT")
        conn.commit()

    # Migration: Add sync_interval column to simplefin_config if it doesn't exist
    try:
        c.execute("SELECT sync_interval FROM simplefin_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding sync_interval column to simplefin_config...")
        c.execute("ALTER TABLE simplefin_config ADD COLUMN sync_interval INTEGER DEFAULT 3600")
        conn.commit()

    # Migration: Add sync_times column to simplefin_config if it doesn't exist
    try:
        c.execute("SELECT sync_times FROM simplefin_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding sync_times column to simplefin_config...")
        c.execute("ALTER TABLE simplefin_config ADD COLUMN sync_times TEXT")
        conn.commit()

    # Migration: Add sync_timezone column to simplefin_config if it doesn't exist
    try:
        c.execute("SELECT sync_timezone FROM simplefin_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding sync_timezone column to simplefin_config...")
        c.execute("ALTER TABLE simplefin_config ADD COLUMN sync_timezone TEXT")
        conn.commit()

    # Store seen credit card transactions to avoid duplicates
    c.execute('''CREATE TABLE IF NOT EXISTS credit_card_transactions (
        transaction_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        amount REAL,
        date TEXT,
        merchant TEXT,
        description TEXT,
        is_pending INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Onboarding flow tables
    c.execute('''CREATE TABLE IF NOT EXISTS onboarding_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        is_completed INTEGER DEFAULT 0,
        completed_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS crew_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bearer_token TEXT NOT NULL,
        is_valid INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS lunchflow_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        api_key TEXT NOT NULL,
        is_valid INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Splitwise configuration (API key + user ID)
    c.execute('''CREATE TABLE IF NOT EXISTS splitwise_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        api_key TEXT NOT NULL,
        user_id INTEGER NOT NULL,
        is_valid INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_sync TEXT,
        sync_interval INTEGER DEFAULT 3600
    )''')

    # Splitwise pocket configuration (one pocket per friend)
    c.execute('''CREATE TABLE IF NOT EXISTS splitwise_pocket_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        friend_id INTEGER NOT NULL UNIQUE,
        friend_name TEXT NOT NULL,
        pocket_id TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Track processed Splitwise expenses (deduplication by expense_id and friend_id)
    c.execute('''CREATE TABLE IF NOT EXISTS splitwise_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_id TEXT NOT NULL,
        friend_id INTEGER NOT NULL,
        description TEXT,
        amount REAL,
        date TEXT,
        created_by_id INTEGER,
        created_by_name TEXT,
        currency_code TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(expense_id, friend_id)
    )''')

    # Migration: Add columns to splitwise_config if they don't exist
    try:
        c.execute("SELECT last_sync FROM splitwise_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding last_sync column to splitwise_config...")
        c.execute("ALTER TABLE splitwise_config ADD COLUMN last_sync TEXT")
        conn.commit()

    try:
        c.execute("SELECT sync_interval FROM splitwise_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding sync_interval column to splitwise_config...")
        c.execute("ALTER TABLE splitwise_config ADD COLUMN sync_interval INTEGER DEFAULT 3600")
        conn.commit()

    try:
        c.execute("SELECT tracked_friends FROM splitwise_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding tracked_friends column to splitwise_config...")
        c.execute("ALTER TABLE splitwise_config ADD COLUMN tracked_friends TEXT")
        conn.commit()

    # Migration: Add columns to splitwise_pocket_config if they don't exist
    try:
        c.execute("SELECT batch_mode FROM splitwise_pocket_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding batch_mode column to splitwise_pocket_config...")
        c.execute("ALTER TABLE splitwise_pocket_config ADD COLUMN batch_mode INTEGER DEFAULT 1")
        conn.commit()

    try:
        c.execute("SELECT tracked_friends FROM splitwise_pocket_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding tracked_friends column to splitwise_pocket_config...")
        c.execute("ALTER TABLE splitwise_pocket_config ADD COLUMN tracked_friends TEXT")
        conn.commit()

    # Migration: Add friend_id and friend_name to splitwise_pocket_config if needed
    try:
        c.execute("SELECT friend_id FROM splitwise_pocket_config LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating DB: Adding friend_id and friend_name columns to splitwise_pocket_config...")
        c.execute("ALTER TABLE splitwise_pocket_config ADD COLUMN friend_id INTEGER")
        c.execute("ALTER TABLE splitwise_pocket_config ADD COLUMN friend_name TEXT")
        conn.commit()

    # User authentication table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_login TEXT
    )''')

    # Passkey/WebAuthn credentials table
    c.execute('''CREATE TABLE IF NOT EXISTS passkey_credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        credential_id BLOB NOT NULL UNIQUE,
        public_key BLOB NOT NULL,
        sign_count INTEGER DEFAULT 0,
        transports TEXT,
        aaguid TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_used_at TEXT,
        nickname TEXT,
        backup_eligible INTEGER DEFAULT 0,
        backup_state INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')

    # WebAuthn session tracking (for registration/authentication ceremonies)
    c.execute('''CREATE TABLE IF NOT EXISTS webauthn_sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER,
        challenge BLOB NOT NULL,
        operation TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )''')

    # Create indexes for faster passkey lookups
    c.execute('''CREATE INDEX IF NOT EXISTS idx_passkey_user ON passkey_credentials(user_id)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_passkey_credential ON passkey_credentials(credential_id)''')

    # WebAuthn configuration (RP_ID and ORIGIN for passkeys)
    c.execute('''CREATE TABLE IF NOT EXISTS webauthn_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rp_id TEXT NOT NULL,
        origin TEXT NOT NULL,
        is_valid INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # Web Push (VAPID) configuration
    c.execute('''CREATE TABLE IF NOT EXISTS fcm_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vapid_public_key TEXT NOT NULL,
        vapid_private_key TEXT NOT NULL,
        firebase_project_id TEXT,
        service_account_json TEXT,
        is_valid INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')

    # FCM device tokens (one per browser/device)
    c.execute('''CREATE TABLE IF NOT EXISTS fcm_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token TEXT NOT NULL UNIQUE,
        device_name TEXT,
        user_agent TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_used_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')

    conn.commit()

    # Auto-migrate env vars to database on first run
    migrate_tokens_to_db(c, conn)

    conn.close()

def migrate_tokens_to_db(cursor, connection):
    """Auto-migrate env vars to database on first run"""
    # Check if already migrated Crew token
    cursor.execute("SELECT id FROM crew_config LIMIT 1")
    has_crew = cursor.fetchone()

    if not has_crew:
        bearer = os.environ.get("BEARER_TOKEN")
        if bearer:
            cursor.execute("INSERT INTO crew_config (bearer_token) VALUES (?)", (bearer,))
            cursor.execute("INSERT INTO onboarding_config (is_completed, completed_at) VALUES (1, CURRENT_TIMESTAMP)")
            print("✅ Migrated BEARER_TOKEN from env vars to database")

    # Check if already migrated LunchFlow API key
    cursor.execute("SELECT id FROM lunchflow_config LIMIT 1")
    has_lunchflow = cursor.fetchone()

    if not has_lunchflow:
        api_key = os.environ.get("LUNCHFLOW_API_KEY")
        if api_key and api_key != "none":
            cursor.execute("INSERT INTO lunchflow_config (api_key) VALUES (?)", (api_key,))
            print("✅ Migrated LUNCHFLOW_API_KEY from env vars to database")

    connection.commit()

def log_balance(balance):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        c.execute("INSERT OR REPLACE INTO history (date, balance) VALUES (?, ?)", (today, balance))
        conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()

def get_history():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT date, balance FROM history ORDER BY date ASC")
    data = c.fetchall()
    conn.close()
    return {
        "labels": [row[0] for row in data],
        "values": [row[1] for row in data]
    }

# --- WEBAUTHN HELPER FUNCTIONS ---
def generate_challenge():
    """Generate cryptographically secure 32-byte challenge"""
    return os.urandom(32)

def get_user_credentials(user_id):
    """Get all passkey credentials for a user"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT credential_id, public_key, sign_count, transports, nickname
        FROM passkey_credentials
        WHERE user_id = ?
        ORDER BY last_used_at DESC NULLS LAST, created_at DESC
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    credentials = []
    for row in rows:
        credentials.append({
            'credential_id': row[0],
            'public_key': row[1],
            'sign_count': row[2],
            'transports': json.loads(row[3]) if row[3] else [],
            'nickname': row[4]
        })
    return credentials

def save_credential(user_id, credential_data):
    """Save new passkey credential to database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        INSERT INTO passkey_credentials
        (user_id, credential_id, public_key, sign_count, transports, aaguid, backup_eligible, backup_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        credential_data['credential_id'],
        credential_data['public_key'],
        credential_data['sign_count'],
        json.dumps(credential_data.get('transports', [])),
        credential_data.get('aaguid', ''),
        credential_data.get('backup_eligible', 0),
        credential_data.get('backup_state', 0)
    ))

    conn.commit()
    conn.close()

def update_sign_count(credential_id, new_sign_count):
    """Update sign count after successful authentication"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        UPDATE passkey_credentials
        SET sign_count = ?, last_used_at = ?
        WHERE credential_id = ?
    """, (new_sign_count, datetime.now().isoformat(), credential_id))

    conn.commit()
    conn.close()

def cleanup_expired_sessions():
    """Remove expired WebAuthn challenges"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM webauthn_sessions WHERE expires_at < ?",
              (datetime.now().isoformat(),))
    conn.commit()
    conn.close()

# --- TOKEN RETRIEVAL HELPERS ---
def get_crew_bearer_token():
    """Get Crew bearer token (database first, then env var fallback)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT bearer_token FROM crew_config WHERE is_valid = 1 LIMIT 1")
    row = c.fetchone()
    conn.close()

    if row and row[0]:
        return row[0]

    # Fallback to env var for backward compatibility
    return os.environ.get("BEARER_TOKEN")

def get_lunchflow_api_key():
    """Get LunchFlow API key (database first, then env var fallback)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT api_key FROM lunchflow_config WHERE is_valid = 1 LIMIT 1")
    row = c.fetchone()
    conn.close()

    if row and row[0]:
        return row[0]

    # Fallback to env var
    env_key = os.environ.get("LUNCHFLOW_API_KEY")
    return env_key if env_key and env_key != "none" else None

def get_splitwise_api_key():
    """Get Splitwise API key from database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT api_key FROM splitwise_config WHERE is_valid = 1 LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_splitwise_user_id():
    """Get Splitwise user ID from database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM splitwise_config LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_webauthn_rp_id():
    """Get WebAuthn Relying Party ID (database first, then env var fallback)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT rp_id FROM webauthn_config WHERE is_valid = 1 ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()

    if row and row[0]:
        return row[0]

    # Fallback to env var or default
    return os.environ.get('RP_ID', 'localhost')

def get_webauthn_origin():
    """Get WebAuthn origin URL (database first, then env var fallback)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT origin FROM webauthn_config WHERE is_valid = 1 ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()

    if row and row[0]:
        return row[0]

    # Fallback to env var or default
    return os.environ.get('ORIGIN', 'http://localhost:8080')

def get_fcm_config():
    """Get VAPID configuration from database"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""SELECT vapid_public_key, vapid_private_key, is_valid FROM fcm_config LIMIT 1""")
    row = c.fetchone()
    conn.close()

    if row and row[2]:  # is_valid = 1
        return {
            'vapid_public_key': row[0],
            'vapid_private_key': row[1]
        }
    return None

def send_sync_complete_notification(user_id, transaction_count, account_names):
    """Send Web Push notification for sync completion"""
    fcm_config = get_fcm_config()
    if not fcm_config:
        return  # VAPID not configured, skip silently

    # Get active tokens for user
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT token FROM fcm_tokens WHERE user_id = ? AND is_active = 1", (user_id,))
    tokens = [row[0] for row in c.fetchall()]
    conn.close()

    if not tokens:
        print(f"📱 No FCM tokens registered for user {user_id}")
        return

    # Build notification
    if len(account_names) == 1:
        title = f"New transactions on {account_names[0]}"
        body = f"{transaction_count} new transaction(s) synced"
    else:
        title = "Credit card sync complete"
        body = f"{transaction_count} new transaction(s) across {len(account_names)} accounts"

    # Send via Web Push
    try:
        from pywebpush import webpush, WebPushException

        # Build notification payload
        payload = json.dumps({
            "notification": {
                "title": title,
                "body": body
            }
        })

        success_count = 0
        failed_tokens = []

        # Send to each subscription
        for token_json in tokens:
            try:
                # Parse subscription object
                subscription_info = json.loads(token_json)

                # Send push notification (pywebpush handles VAPID JWT automatically)
                # NOTE: Apple rejects "localhost" in VAPID subject - must use real domain
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=fcm_config['vapid_private_key'],
                    vapid_claims={"sub": "mailto:notifications@example.com"},
                    ttl=86400
                )
                success_count += 1

            except WebPushException as e:
                print(f"⚠️ WebPushException: {e}")
                if e.response:
                    print(f"   Status: {e.response.status_code}, Body: {e.response.text if hasattr(e.response, 'text') else 'N/A'}")
                print(f"   Endpoint: {subscription_info.get('endpoint', 'N/A')[:80]}...")
                # Mark as inactive if subscription expired (410 Gone or 404 Not Found)
                if e.response and e.response.status_code in [404, 410]:
                    failed_tokens.append(token_json)
            except json.JSONDecodeError as e:
                print(f"⚠️ Invalid token JSON: {e}")
                failed_tokens.append(token_json)
            except Exception as e:
                print(f"⚠️ Failed to send to token: {e}")

        print(f"✅ Sent notification to {success_count}/{len(tokens)} devices: {title}")

        # Mark invalid tokens as inactive
        if failed_tokens:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            for token in failed_tokens:
                c.execute("UPDATE fcm_tokens SET is_active = 0 WHERE token = ?", (token,))
            conn.commit()
            conn.close()
            print(f"⚠️ Marked {len(failed_tokens)} invalid tokens as inactive")

    except Exception as e:
        print(f"❌ Failed to send push notification: {e}")

# --- API HELPERS ---
def get_crew_headers():
    bearer_token = get_crew_bearer_token()


    return {
        "accept": "*/*",
        "content-type": "application/json",
        "authorization": bearer_token,
        "user-agent": "Crew/1 CFNetwork/3860.300.31 Darwin/25.2.0",
    }

# --- DATA FETCHERS ---
@cached("primary_account_id")
def get_primary_account_id():
    try:
        headers = get_crew_headers()
        if not headers: return None
        query_string = """ query CurrentUser { currentUser { accounts { id displayName } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        accounts = data.get("data", {}).get("currentUser", {}).get("accounts", [])
        for acc in accounts:
            if acc.get("displayName") == "Checking":
                return acc.get("id")
        if accounts: return accounts[0].get("id")
        return None
    except Exception as e:
        print(f"Error fetching Account ID: {e}")
        return None

@cached("financial_data")
def get_financial_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

        # We fetch all accounts and subaccounts
        query_string = """ query CurrentUser { currentUser { accounts { subaccounts { id goal overallBalance name } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()

        results = {
            "checking": None,
            "total_goals": 0.0  # This will hold the sum of ALL non-checking pockets
        }

        print("--- DEBUG: CALCULATING POCKETS ---")
        for account in data.get("data", {}).get("currentUser", {}).get("accounts", []):
            for sub in account.get("subaccounts", []):
                name = sub.get("name")
                # Crew API returns balance in cents, so we divide by 100
                balance_raw = sub.get("overallBalance", 0) / 100.0

                if name == "Checking":
                    # This is your main Safe-to-Spend source
                    results["checking"] = {
                        "name": name,
                        "balance": f"${balance_raw:.2f}",
                        "raw_balance": balance_raw
                    }
                else:
                    # If it is NOT "Checking", we treat it as a Pocket and add it to the total
                    results["total_goals"] += balance_raw
                    print(f"Adding Pocket '{name}': ${balance_raw}")

        print(f"TOTAL POCKETS: ${results['total_goals']}")
        print("----------------------------------")

        if not results["checking"]:
            return {"error": "Checking account not found"}

        return results

    except Exception as e:
        print(f"Error in get_financial_data: {e}")
        return {"error": str(e)}

@cached("transactions")
def get_transactions_data(search_term=None, min_date=None, max_date=None, min_amount=None, max_amount=None):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Could not find Checking Account ID"}
        query_string = """ query RecentActivity($accountId: ID!, $cursor: String, $pageSize: Int = 100, $searchFilters: CashTransactionFilter) { account: node(id: $accountId) { ... on Account { id cashTransactions(first: $pageSize, after: $cursor, searchFilters: $searchFilters) { edges { node { id amount description occurredAt title type memo externalMemo matchingName subaccount { id displayName isPrimary } transfer { id type } } } } } } } """
        filters = {}
        if search_term: filters["fuzzySearch"] = search_term
        variables = {"pageSize": 100, "accountId": account_id, "searchFilters": filters}
        response = requests.post(URL, headers=headers, json={"operationName": "RecentActivity", "variables": variables, "query": query_string})
        if response.status_code != 200: return {"error": f"API Error: {response.text}"}
        data = response.json()
        if 'errors' in data: return {"error": data['errors'][0]['message']}
        txs = []
        try:
            edges = data.get('data', {}).get('account', {}).get('cashTransactions', {}).get('edges', [])
            for edge in edges:
                node = edge['node']
                amt = node['amount'] / 100.0
                date_str = node['occurredAt']
                subaccount = node.get('subaccount') or {}
                sub_id = subaccount.get('id')
                sub_name = subaccount.get('displayName')
                is_primary = subaccount.get('isPrimary', False)
                transfer = node.get('transfer') or {}
                transfer_type = transfer.get('type')
                if min_date or max_date:
                    tx_date = date_str[:10]
                    if min_date and tx_date < min_date: continue
                    if max_date and tx_date > max_date: continue
                if min_amount or max_amount:
                    abs_amt = abs(amt)
                    if min_amount and abs_amt < float(min_amount): continue
                    if max_amount and abs_amt > float(max_amount): continue
                txs.append({
                    "id": node['id'],
                    "title": node['title'],
                    "description": node['description'],
                    "amount": amt,
                    "date": date_str,
                    "type": node['type'],
                    "subaccountId": sub_id,
                    "pocketName": sub_name if sub_id and not is_primary else None,
                    "memo": node.get('memo') or node.get('externalMemo') or '',
                    "matchingName": node.get('matchingName'),
                    "transferType": transfer_type
                })
        except Exception as e:
            return {"error": f"Parse Error: {str(e)}"}
        return {"transactions": txs}
    except Exception as e:
        return {"error": str(e)}

@cached("user_profile_info")
def get_user_profile_info():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # Updated Query to include imageUrl
        query_string = """ 
        query CurrentUser { 
            currentUser { 
                firstName 
                lastName
                imageUrl
            } 
        } 
        """
        
        response = requests.post(URL, headers=headers, json={
            "operationName": "CurrentUser", 
            "query": query_string
        })
        
        data = response.json()
        user = data.get("data", {}).get("currentUser", {})
        
        return {
            "firstName": user.get("firstName", ""),
            "lastName": user.get("lastName", ""),
            "imageUrl": user.get("imageUrl") # Can be None or a URL string
        }
    except Exception as e:
        return {"error": str(e)}


@cached("intercom_data")
def get_intercom_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # GraphQL Query
        query_string = """
        query IntercomToken($platform: IntercomPlatform!) {
          currentUser {
            id
            intercomJwt(platform: $platform)
          }
        }
        """
        
        variables = {"platform": "WEB"}
        
        response = requests.post(URL, headers=headers, json={
            "operationName": "IntercomToken",
            "variables": variables,
            "query": query_string
        })
        
        data = response.json()
        user = data.get("data", {}).get("currentUser", {})
        
        if not user:
            return {"error": "User data not found"}

        # Return the exact keys requested
        return {
            "user_data": {
                "user_id": user.get("id"),
                "intercom_user_jwt": user.get("intercomJwt")
            }
        }
    except Exception as e:
        return {"error": str(e)}

@cached("tx_detail")
def get_transaction_detail(activity_id):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query ActivityDetail($activityId: ID!, $isTransfer: Boolean = false) { cashTransaction: node(id: $activityId) @skip(if: $isTransfer) { ... on CashTransaction { ...CashTransactionActivity __typename } __typename } pendingTransfer: node(id: $activityId) @include(if: $isTransfer) { ... on Transfer { ...PendingTransferActivity __typename } __typename } } fragment CashTransactionFields on CashTransaction { id amount avatarFallbackColor currencyCode description externalMemo imageUrl isSplit note occurredAt quickCleanName ruleSuggestionString status title type __typename } fragment NameableAccount on Account { id displayName belongsToCurrentUser isChildAccount isExternalAccount avatarUrl icon type mask owner { displayName avatarUrl avatarColor __typename } __typename } fragment NameableSubaccount on Subaccount { id type belongsToCurrentUser isChildAccount isExternalAccount displayName avatarUrl icon piggyBanked isPrimary status account { id __typename } owner { displayName avatarUrl avatarColor __typename } primaryOwner { id __typename } __typename } fragment NameableCashTransaction on CashTransaction { __typename id amount description externalMemo avatarFallbackColor imageUrl quickCleanName title type account { ...NameableAccount __typename } subaccount { ...NameableSubaccount __typename } } fragment RelatedTransactions on CashTransaction { id status occurredAt relatedTransactions { id occurredAt __typename } transfer { id type status scheduledSettlement __typename } __typename } fragment TransferFields on Transfer { id amount formattedErrorCode isCancellable note occurredAt scheduledSettlement status type accountFrom { ...NameableAccount __typename } accountTo { ...NameableAccount __typename } subaccountFrom { ...NameableSubaccount __typename } subaccountTo { ...NameableSubaccount __typename } permittedActions { transferReassign __typename } __typename } fragment CashTransactionActivity on CashTransaction { ...CashTransactionFields ...NameableCashTransaction ...RelatedTransactions account { id subaccounts { id belongsToCurrentUser clearedBalance displayName isExternalAccount owner { displayName __typename } __typename } __typename } latestDebitCardTransactionDetail { id merchantAddress1 merchantCity merchantCountry merchantName merchantState merchantZip __typename } debitCard { id name type cardOwner: user { id displayedFirstName __typename } __typename } transfer { ...TransferFields accountTo { id primaryOwner { id displayedFirstName __typename } __typename } __typename } subaccount { id displayName __typename } permittedActions { cashTransactionReassign cashTransactionSplit cashTransactionUndo __typename } __typename } fragment PendingTransferActivity on Transfer { ...TransferFields __typename } """
        variables = {"isTransfer": False, "activityId": activity_id}
        response = requests.post(URL, headers=headers, json={"operationName": "ActivityDetail", "variables": variables, "query": query_string})
        data = response.json()
        node = data.get('data', {}).get('cashTransaction') or data.get('data', {}).get('pendingTransfer')
        if not node: return {"error": "Details not found"}
        merchant_info = node.get('latestDebitCardTransactionDetail') or {}
        return {"id": node.get('id'), "amount": node.get('amount', 0) / 100.0, "title": node.get('title'), "description": node.get('description'), "status": node.get('status'), "date": node.get('occurredAt'), "memo": node.get('externalMemo'), "merchant": {"name": merchant_info.get('merchantName'), "address": merchant_info.get('merchantAddress1'), "city": merchant_info.get('merchantCity'), "state": merchant_info.get('merchantState'), "zip": merchant_info.get('merchantZip')}}
    except Exception as e:
        return {"error": str(e)}

@cached("expenses")
def get_expenses_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # Updated query to include funding settings
        query_string = """ 
        query CurrentUser { 
            currentUser { 
                accounts { 
                    billReserve { 
                        nextFundingDate 
                        totalReservedAmount 
                        estimatedNextFundingAmount 
                        settings { 
                            funding { 
                                subaccount { 
                                    displayName 
                                } 
                            } 
                        }
                        bills { 
                            amount 
                            anchorDate 
                            autoAdjustAmount 
                            dayOfMonth 
                            daysOverdue 
                            estimatedNextFundingAmount 
                            frequency 
                            frequencyInterval 
                            id 
                            name 
                            paused 
                            reservedAmount 
                            reservedBy 
                            status 
                        } 
                    } 
                } 
            } 
        } 
        """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        accounts = data.get("data", {}).get("currentUser", {}).get("accounts", [])
        
        all_bills = []
        summary = {}
        
        for acc in accounts:
            bill_reserve = acc.get("billReserve")
            if bill_reserve:
                # Extract funding source name safely
                funding_name = "Checking"
                try:
                    funding_name = bill_reserve["settings"]["funding"]["subaccount"]["displayName"]
                except (KeyError, TypeError):
                    pass

                summary = {
                    "totalReserved": (bill_reserve.get("totalReservedAmount") or 0) / 100.0, 
                    "nextFundingDate": bill_reserve.get("nextFundingDate"), 
                    "estimatedFunding": (bill_reserve.get("estimatedNextFundingAmount") or 0) / 100.0,
                    "fundingSource": funding_name # <--- Added this
                }
                
                bills = bill_reserve.get("bills", [])
                for b in bills:
                    amt = (b.get("amount") or 0) / 100.0
                    res = (b.get("reservedAmount") or 0) / 100.0
                    est_fund = (b.get("estimatedNextFundingAmount") or 0) / 100.0
                    all_bills.append({
                        "id": b.get("id"), 
                        "name": b.get("name"), 
                        "amount": amt, 
                        "reserved": res, 
                        "estimatedFunding": est_fund, 
                        "frequency": b.get("frequency"), 
                        "dueDay": b.get("dayOfMonth"), 
                        "paused": b.get("paused"), 
                        "reservedBy": b.get("reservedBy")
                    })
        
        all_bills.sort(key=lambda x: x['reservedBy'] or "9999-12-31")
        return {"expenses": all_bills, "summary": summary}
    except Exception as e:
        return {"error": str(e)}
        
# --- DATA FETCHERS (Update get_goals_data) ---
# 2. UPDATE GET_GOALS TO SORT
@cached("goals")
def get_goals_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # 1. Fetch from API
        query_string = """ query CurrentUser { currentUser { accounts { subaccounts { goal overallBalance name id } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        
        # 2. Fetch Groups and Links from DB
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        c.execute("SELECT id, name FROM groups")
        group_rows = c.fetchall()
        groups_dict = {row[0]: row[1] for row in group_rows}
        
        # Get links with Sorting
        c.execute("SELECT pocket_id, group_id, sort_order FROM pocket_links")
        link_rows = c.fetchall()
        # Create lookups
        links_dict = {row[0]: row[1] for row in link_rows} 
        order_dict = {row[0]: row[2] for row in link_rows}
        
        # Get credit card pocket IDs and their current balances
        c.execute("SELECT pocket_id, current_balance FROM credit_card_config WHERE pocket_id IS NOT NULL")
        credit_card_data = {row[0]: row[1] for row in c.fetchall()}
        credit_card_pocket_ids = set(credit_card_data.keys())
        
        conn.close()

        goals = []
        for account in data.get("data", {}).get("currentUser", {}).get("accounts", []):
            for sub in account.get("subaccounts", []):
                name = sub.get("name")
                if name != "Checking":
                    balance = sub.get("overallBalance", 0) / 100.0
                    target = sub.get("goal", 0) / 100.0 if sub.get("goal") else 0
                    p_id = sub.get("id")
                    
                    g_id = links_dict.get(p_id)
                    g_name = groups_dict.get(g_id)
                    # Default sort order to 999 if not set, so new items appear at bottom
                    s_order = order_dict.get(p_id, 999)
                    
                    # Check if this is a credit card pocket
                    is_credit_card = p_id in credit_card_pocket_ids

                    goal_data = {
                        "id": p_id,
                        "name": name,
                        "balance": balance,
                        "target": target,
                        "status": "Active",
                        "groupId": g_id,
                        "groupName": g_name,
                        "sortOrder": s_order,
                        "isCreditCard": is_credit_card
                    }

                    # Add credit card balance if this is a credit card pocket
                    if is_credit_card:
                        goal_data["creditCardBalance"] = credit_card_data.get(p_id, 0)

                    goals.append(goal_data)
        
        # Python-side sort based on the DB order
        goals.sort(key=lambda x: x['sortOrder'])
        
        return {"goals": goals, "all_groups": [{"id": k, "name": v} for k,v in groups_dict.items()]}
    except Exception as e:
        return {"error": str(e)}

@cached("trends")
def get_monthly_trends():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Could not find Checking Account ID"}
        today = date.today()
        start_of_month = date(today.year, today.month, 1).strftime("%Y-%m-%dT00:00:00Z")
        query_string = """ query RecentActivity($accountId: ID!, $cursor: String, $pageSize: Int = 100) { account: node(id: $accountId) { ... on Account { cashTransactions(first: $pageSize, after: $cursor) { edges { node { amount occurredAt } } } } } } """
        variables = {"pageSize": 100, "accountId": account_id}
        response = requests.post(URL, headers=headers, json={"operationName": "RecentActivity", "variables": variables, "query": query_string})
        data = response.json()
        edges = data.get('data', {}).get('account', {}).get('cashTransactions', {}).get('edges', [])
        earned = 0.0
        spent = 0.0
        for edge in edges:
            node = edge['node']
            tx_date = node['occurredAt']
            amount = node['amount'] / 100.0
            if tx_date >= start_of_month:
                if amount > 0:
                    earned += amount
                else:
                    spent += abs(amount)
        return {"earned": earned, "spent": spent}
    except Exception as e:
        return {"error": str(e)}

@cached("subaccounts")
def get_subaccounts_list(force_refresh=False):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """
        query TransferScreen {
          currentUser {
            id
            family {
              id
              signerSpendAccount {
                ...AccountTransferFields
                subaccounts {
                  ...SubaccountTransferFields
                }
              }
              externalAccounts {
                ...AccountTransferFields
              }
              children {
                id
                dob
                ...AvatarFields
                spendAccount {
                  ...AccountTransferFields
                  subaccounts {
                    ...SubaccountTransferFields
                  }
                }
              }
            }
          }
        }

        fragment AccountTransferFields on Account {
          id
          displayName
          belongsToCurrentUser
          owner {
            displayName
          }
          overallBalance
          isExternalAccount
        }

        fragment SubaccountTransferFields on Subaccount {
          id
          displayName
          belongsToCurrentUser
          owner {
            displayName
          }
          clearedBalance
          isExternalAccount
          piggyBanked
        }

        fragment AvatarFields on User {
          id
          cardColor
          imageUrl
          displayedFirstName
        }
        """
        response = requests.post(URL, headers=headers, json={"operationName": "TransferScreen", "query": query_string})
        data = response.json()
        if 'errors' in data: return {"error": data['errors'][0]['message']}

        family = data.get("data", {}).get("currentUser", {}).get("family", {})
        accounts = []

        # Main spend account subaccounts (only non-piggyBanked = Free to Spend checking)
        spend = family.get("signerSpendAccount") or {}
        if spend:
            # Skip the main account, only add subaccounts
            for sub in spend.get("subaccounts", []):
                # Only include non-piggyBanked subaccounts (Free to Spend checking)
                if not sub.get("piggyBanked", False):
                    sub_balance = (sub.get("clearedBalance") or 0) / 100.0
                    sub_owner = sub.get("owner", {}).get("displayName", "")
                    accounts.append({"id": sub.get("id"), "name": sub.get("displayName"), "balance": sub_balance, "owner": sub_owner, "isExternal": sub.get("isExternalAccount", False), "type": "subaccount", "piggyBanked": False})

        # External accounts
        for ext in family.get("externalAccounts", []):
            balance = (ext.get("overallBalance") or 0) / 100.0
            owner = ext.get("owner", {}).get("displayName", "")
            accounts.append({"id": ext.get("id"), "name": ext.get("displayName"), "balance": balance, "owner": owner, "isExternal": True, "type": "external"})

        # Children's accounts subaccounts (only non-piggyBanked = Free to Spend checking)
        for child in family.get("children", []):
            child_name = child.get("displayedFirstName", "Child")
            child_spend = child.get("spendAccount") or {}
            if child_spend:
                # Skip the child's main account, only add subaccounts
                for sub in child_spend.get("subaccounts", []):
                    # Only include non-piggyBanked subaccounts (Free to Spend checking)
                    if not sub.get("piggyBanked", False):
                        sub_balance = (sub.get("clearedBalance") or 0) / 100.0
                        sub_owner = sub.get("owner", {}).get("displayName", "")
                        accounts.append({"id": sub.get("id"), "name": sub.get("displayName"), "balance": sub_balance, "owner": sub_owner, "isExternal": False, "type": "child_subaccount", "childName": child_name, "piggyBanked": False})

        return {"subaccounts": accounts}
    except Exception as e:
        return {"error": str(e)}


def get_family_subaccounts():
    """Get all family subaccounts including children's pockets, grouped by owner"""
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

        query_string = """
        query FamilySubaccounts {
            currentUser {
                id
                displayedFirstName
                accounts {
                    subaccounts {
                        id
                        displayName
                        clearedBalance
                    }
                }
                family {
                    children {
                        id
                        displayedFirstName
                        spendAccount {
                            subaccounts {
                                id
                                displayName
                                clearedBalance
                            }
                        }
                    }
                }
            }
        }
        """
        response = requests.post(URL, headers=headers, json={"operationName": "FamilySubaccounts", "query": query_string})
        data = response.json()

        current_user = data.get("data", {}).get("currentUser", {})
        family = current_user.get("family", {})

        result = {
            "groups": []
        }

        # Main account pockets
        main_pockets = []
        for account in current_user.get("accounts", []):
            for sub in account.get("subaccounts", []):
                balance = sub.get("clearedBalance", 0) / 100.0
                main_pockets.append({
                    "id": sub.get("id"),
                    "name": sub.get("displayName"),
                    "balance": balance
                })

        if main_pockets:
            result["groups"].append({
                "owner": "Main Account",
                "ownerType": "main",
                "pockets": main_pockets
            })

        # Children's pockets
        for child in family.get("children", []):
            child_name = child.get("displayedFirstName", "Child")
            child_pockets = []
            spend_account = child.get("spendAccount", {})
            for sub in spend_account.get("subaccounts", []):
                balance = sub.get("clearedBalance", 0) / 100.0
                child_pockets.append({
                    "id": sub.get("id"),
                    "name": sub.get("displayName"),
                    "balance": balance
                })

            if child_pockets:
                result["groups"].append({
                    "owner": child_name,
                    "ownerType": "child",
                    "pockets": child_pockets
                })

        return result
    except Exception as e:
        return {"error": str(e)}

def get_configured_timezone():
    """Get the user's configured timezone from database, defaults to local system time"""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from datetime import timezone as tz_module
        # Fallback for Python < 3.9
        return None

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT sync_timezone FROM simplefin_config LIMIT 1")
        row = c.fetchone()
        conn.close()

        if row and row[0]:
            try:
                return ZoneInfo(row[0])
            except:
                return None
        return None
    except:
        return None

def move_money(from_id, to_id, amount, memo=""):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ mutation InitiateTransferScottie($input: InitiateTransferInput!) { initiateTransfer(input: $input) { result { id __typename } __typename } } """
        amount_cents = int(round(float(amount) * 100))
        variables = {"input": {"amount": amount_cents, "accountFromId": from_id, "accountToId": to_id, "note": memo or "Transfer"}}
        response = requests.post(URL, headers=headers, json={"operationName": "InitiateTransferScottie", "variables": variables, "query": query_string})
        data = response.json()
        if 'errors' in data: return {"error": data['errors'][0]['message']}
        print("🧹 Clearing Cache after transaction...")
        cache.clear()
        return {"success": True, "result": data.get("data", {}).get("initiateTransfer", {})}
    except Exception as e:
        return {"error": str(e)}

@cached("family")
def get_family_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query FamilyScreen { currentUser { id family { id children { id dob cardColor imageUrl displayedFirstName spendAccount { id overallBalance subaccounts { id displayName clearedBalance } } scheduledAllowance { id totalAmount } } parents { id isApplying cardColor imageUrl displayedFirstName } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "FamilyScreen", "query": query_string})
        data = response.json()
        family_node = data.get("data", {}).get("currentUser", {}).get("family", {})
        children = []
        for child in family_node.get("children", []):
            balance = child.get("spendAccount", {}).get("overallBalance", 0) / 100.0
            allowance = "Not set"
            if child.get("scheduledAllowance"):
                amt = child["scheduledAllowance"].get("totalAmount", 0) / 100.0
                allowance = f"${amt:.2f}/week"
            children.append({"id": child.get("id"), "name": child.get("displayedFirstName"), "image": child.get("imageUrl"), "color": child.get("cardColor"), "dob": child.get("dob"), "balance": balance, "allowance": allowance, "role": "Child"})
        parents = []
        for parent in family_node.get("parents", []):
            parents.append({"id": parent.get("id"), "name": parent.get("displayedFirstName"), "image": parent.get("imageUrl"), "color": parent.get("cardColor"), "role": "Parent"})
        return {"children": children, "parents": parents}
    except Exception as e:
        return {"error": str(e)}

def create_pocket(name, target_amount, initial_amount, note):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # Get the main Account ID automatically
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Could not find Checking Account ID"}

        query_string = """
        mutation CreateSubaccount($input: CreateSubaccountInput!) {
            createSubaccount(input: $input) {
                result {
                    id
                    name
                    balance
                    goal
                    status
                    subaccountType
                }
            }
        }
        """
        
        # Convert amounts to cents (assuming API expects cents based on your move_money logic)
        target_cents = int(float(target_amount) * 100)
        initial_cents = int(float(initial_amount) * 100)

        variables = {
            "input": {
                "type": "SAVINGS",           # Hardcoded per instructions
                "piggyBanked": False,        # Hardcoded per instructions
                "accountId": account_id,     # Auto-filled
                "name": name,
                "targetAmount": target_cents,
                "initialTransferAmount": initial_cents,
                "note": note
            }
        }

        response = requests.post(URL, headers=headers, json={
            "operationName": "CreateSubaccount",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}
            
        # Clear cache so the new pocket appears immediately
        print("🧹 Clearing Cache after pocket creation...")
        cache.clear()
        
        return {"success": True, "result": data.get("data", {}).get("createSubaccount", {}).get("result")}

    except Exception as e:
        return {"error": str(e)}

@cached("cards")
def get_cards_data():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        # 1. New Query for Physical Cards (Parents & Children)
        query_phys = """ 
        query PhysicalCards {
          currentUser {
            id
            family {
              id
              parents {
                id
                activePhysicalDebitCard {
                  ...PhysicalDebitCardFields
                  __typename
                }
                issuingPhysicalDebitCard {
                  ...PhysicalDebitCardFields
                  __typename
                }
                __typename
              }
              __typename
            }
            __typename
          }
        }

        fragment PhysicalDebitCardFields on DebitCard {
          id
          color
          status
          lastFour
          user {
            id
            isChild
            firstName
            userSpendConfig {
              id
              selectedSpendSubaccount {
                id
                name
                __typename
              }
              __typename
            }
            __typename
          }
          __typename
        }
        """
        
        # We only execute the Physical card query for now as requested
        res_phys = requests.post(URL, headers=headers, json={"operationName": "PhysicalCards", "query": query_phys})
        data_phys = res_phys.json()
        
        all_cards = []
        
        # 2. Parse Parents Only (as requested)
        fam = data_phys.get("data", {}).get("currentUser", {}).get("family", {}) or {}
        parents = fam.get("parents") or []
        
        for parent in parents:
            # Active Card
            card = parent.get("activePhysicalDebitCard")
            if card:
                user_data = card.get("user", {})
                config = user_data.get("userSpendConfig")

                # Determine current spend source
                spend_source_id = "Checking"
                if config and config.get("selectedSpendSubaccount"):
                    spend_source_id = config["selectedSpendSubaccount"]["id"]

                all_cards.append({
                    "id": card.get("id"),
                    "userId": user_data.get("id"),
                    "type": "Physical",
                    "name": "Simple Visa® Card",
                    "holder": user_data.get("firstName"),
                    "last4": card.get("lastFour"),
                    "color": card.get("color"),
                    "status": card.get("status"),
                    "current_spend_id": spend_source_id
                })

        # 2. Query for Virtual Cards
        query_virtual = """
        query VirtualCards {
          currentUser {
            id
            family {
              id
              children {
                id
                virtualDebitCards {
                  ...VirtualDebitCardFields
                  __typename
                }
                __typename
              }
              parents {
                id
                virtualDebitCards {
                  ...VirtualDebitCardFields
                  __typename
                }
                __typename
              }
              __typename
            }
            __typename
          }
        }

        fragment VirtualDebitCardFields on DebitCard {
          id
          type
          color
          status
          lastFour
          frozenStatus
          name
          monthlyLimit
          monthlySpendToDate
          isAttachedToBill
          bills {
            id
            name
            __typename
          }
          subaccount {
            id
            displayName
            belongsToCurrentUser
            clearedBalance
            owner {
              displayName
              __typename
            }
            __typename
          }
          user {
            id
            isChild
            firstName
            userSpendConfig {
              id
              selectedSpendSubaccount {
                id
                displayName
                clearedBalance
                __typename
              }
              __typename
            }
            __typename
          }
          __typename
        }
        """

        res_virtual = requests.post(URL, headers=headers, json={"operationName": "VirtualCards", "query": query_virtual})
        data_virtual = res_virtual.json()

        virtual_cards = []

        # Parse virtual cards from parents and children
        fam_virtual = data_virtual.get("data", {}).get("currentUser", {}).get("family", {}) or {}

        # Process parents' virtual cards
        for parent in fam_virtual.get("parents", []):
            for vcard in parent.get("virtualDebitCards", []):
                if vcard.get("type") in ["VIRTUAL", "SINGLE_USE"]:
                    user_data = vcard.get("user", {})
                    config = user_data.get("userSpendConfig")

                    # Determine current spend source - prioritize linked subaccount
                    spend_source_id = "Checking"
                    linked_subaccount = vcard.get("subaccount")
                    if linked_subaccount and linked_subaccount.get("id"):
                        spend_source_id = linked_subaccount["id"]
                    elif config and config.get("selectedSpendSubaccount"):
                        spend_source_id = config["selectedSpendSubaccount"]["id"]

                    # Calculate remaining limit if applicable
                    monthly_limit = vcard.get("monthlyLimit")
                    monthly_spend = vcard.get("monthlySpendToDate") or 0
                    remaining = None
                    if monthly_limit:
                        # monthlySpendToDate is negative for spending
                        remaining = (monthly_limit + monthly_spend) / 100.0
                        monthly_limit = monthly_limit / 100.0

                    # Check if attached to a bill
                    is_attached_to_bill = vcard.get("isAttachedToBill", False)
                    attached_bill_name = None
                    if is_attached_to_bill:
                        bills = vcard.get("bills", [])
                        if bills and len(bills) > 0:
                            attached_bill_name = bills[0].get("name")

                    # Build linked subaccount display name with owner if not current user's
                    linked_subaccount_display = None
                    if linked_subaccount:
                        display_name = linked_subaccount.get("displayName")
                        belongs_to_current = linked_subaccount.get("belongsToCurrentUser", True)
                        owner = linked_subaccount.get("owner", {})
                        owner_name = owner.get("displayName") if owner else None

                        if belongs_to_current or not owner_name:
                            linked_subaccount_display = display_name
                        else:
                            # Show owner's name for child pockets
                            linked_subaccount_display = f"{owner_name}'s {display_name}"

                    virtual_cards.append({
                        "id": vcard.get("id"),
                        "userId": user_data.get("id"),
                        "type": "Virtual" if vcard.get("type") == "VIRTUAL" else "Single-Use",
                        "name": vcard.get("name") or "Virtual Card",
                        "holder": user_data.get("firstName"),
                        "last4": vcard.get("lastFour"),
                        "color": vcard.get("color"),
                        "status": vcard.get("status"),
                        "frozenStatus": vcard.get("frozenStatus"),
                        "monthlyLimit": monthly_limit,
                        "remaining": remaining,
                        "current_spend_id": spend_source_id,
                        "linkedSubaccount": linked_subaccount_display,
                        "isAttachedToBill": is_attached_to_bill,
                        "attachedBillName": attached_bill_name
                    })

        # Process children's virtual cards
        for child in fam_virtual.get("children", []):
            for vcard in child.get("virtualDebitCards", []):
                if vcard.get("type") in ["VIRTUAL", "SINGLE_USE"]:
                    user_data = vcard.get("user", {})
                    config = user_data.get("userSpendConfig")

                    # Determine current spend source - prioritize linked subaccount
                    spend_source_id = "Checking"
                    linked_subaccount = vcard.get("subaccount")
                    if linked_subaccount and linked_subaccount.get("id"):
                        spend_source_id = linked_subaccount["id"]
                    elif config and config.get("selectedSpendSubaccount"):
                        spend_source_id = config["selectedSpendSubaccount"]["id"]

                    monthly_limit = vcard.get("monthlyLimit")
                    monthly_spend = vcard.get("monthlySpendToDate") or 0
                    remaining = None
                    if monthly_limit:
                        remaining = (monthly_limit + monthly_spend) / 100.0
                        monthly_limit = monthly_limit / 100.0

                    # Check if attached to a bill
                    is_attached_to_bill = vcard.get("isAttachedToBill", False)
                    attached_bill_name = None
                    if is_attached_to_bill:
                        bills = vcard.get("bills", [])
                        if bills and len(bills) > 0:
                            attached_bill_name = bills[0].get("name")

                    # Build linked subaccount display name with owner if not current user's
                    linked_subaccount_display = None
                    if linked_subaccount:
                        display_name = linked_subaccount.get("displayName")
                        belongs_to_current = linked_subaccount.get("belongsToCurrentUser", True)
                        owner = linked_subaccount.get("owner", {})
                        owner_name = owner.get("displayName") if owner else None

                        if belongs_to_current or not owner_name:
                            linked_subaccount_display = display_name
                        else:
                            # Show owner's name for child pockets
                            linked_subaccount_display = f"{owner_name}'s {display_name}"

                    virtual_cards.append({
                        "id": vcard.get("id"),
                        "userId": user_data.get("id"),
                        "type": "Virtual" if vcard.get("type") == "VIRTUAL" else "Single-Use",
                        "name": vcard.get("name") or "Virtual Card",
                        "holder": user_data.get("firstName"),
                        "last4": vcard.get("lastFour"),
                        "color": vcard.get("color"),
                        "status": vcard.get("status"),
                        "frozenStatus": vcard.get("frozenStatus"),
                        "monthlyLimit": monthly_limit,
                        "remaining": remaining,
                        "current_spend_id": spend_source_id,
                        "linkedSubaccount": linked_subaccount_display,
                        "isAttachedToBill": is_attached_to_bill,
                        "attachedBillName": attached_bill_name
                    })

        return {"cards": all_cards, "virtualCards": virtual_cards}
    except Exception as e:
        print(f"Card Error: {e}")
        return {"error": str(e)}


def delete_subaccount_action(sub_id):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

        # Crew API Mutation
        query_string = """
        mutation DeleteSubaccount($id: ID!) {
            deleteSubaccount(input: { subaccountId: $id }) {
                result {
                    id
                    name
                    status
                }
            }
        }
        """
        
        variables = {"id": sub_id}

        response = requests.post(URL, headers=headers, json={
            "operationName": "DeleteSubaccount",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}

        # --- NEW: Clean up local DB ---
        # This ensures the deleted pocket is removed from your local grouping table
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("DELETE FROM pocket_groups WHERE pocket_id = ?", (sub_id,))
            conn.commit()
        except Exception as e:
            print(f"Warning: Failed to cleanup local DB group: {e}")
        finally:
            if conn: conn.close()
            
        print("🧹 Clearing Cache after deletion...")
        cache.clear()
        
        return {"success": True, "result": data.get("data", {}).get("deleteSubaccount", {}).get("result")}

    except Exception as e:
        return {"error": str(e)}

def delete_bill_action(bill_id):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

        # Mutation based on your input
        query_string = """
        mutation DeleteBill($id: ID!) {
            deleteBill(input: { billId: $id }) {
                result {
                    id
                    status
                    name
                }
            }
        }
        """
        
        variables = {"id": bill_id}

        response = requests.post(URL, headers=headers, json={
            "operationName": "DeleteBill",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}
            
        print("🧹 Clearing Cache after bill deletion...")
        cache.clear()
        
        return {"success": True, "result": data.get("data", {}).get("deleteBill", {}).get("result")}

    except Exception as e:
        return {"error": str(e)}

# Add this helper function to fetch the specific funding source name
def get_bill_funding_source():
    try:
        headers = get_crew_headers()
        if not headers: return "Checking"

        query_string = """
        query CurrentUser {
            currentUser {
                accounts {
                    billReserve {
                        settings {
                            funding {
                                subaccount {
                                    displayName
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        
        response = requests.post(URL, headers=headers, json={
            "operationName": "CurrentUser",
            "query": query_string
        })

        data = response.json()
        
        # Parse logic to find the active billReserve
        # We ignore 'errors' regarding nullables and just look for valid data
        accounts = data.get("data", {}).get("currentUser", {}).get("accounts", [])
        
        for acc in accounts:
            # We look for the first account that has a non-null billReserve
            if acc and acc.get("billReserve"):
                try:
                    return acc["billReserve"]["settings"]["funding"]["subaccount"]["displayName"]
                except (KeyError, TypeError):
                    continue
                    
        return "Checking" # Default fallback
    except Exception:
        return "Checking"


def set_spend_pocket_action(user_id, pocket_id, card_id=None):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

        # Resolve "Checking" to a real ID
        resolved_pocket_id = pocket_id
        if pocket_id == "Checking":
            all_subs = get_subaccounts_list()

            if "error" in all_subs:
                return {"error": "Could not resolve Checking ID"}

            found_id = None
            for sub in all_subs.get("subaccounts", []):
                if sub["name"] == "Checking":
                    found_id = sub["id"]
                    break

            if found_id:
                resolved_pocket_id = found_id
            else:
                return {"error": "Checking subaccount not found"}

        # Check if this is a virtual card by looking it up
        is_virtual_card = False
        if card_id:
            cards_data = get_cards_data(force_refresh=False)
            for vcard in cards_data.get("virtualCards", []):
                if vcard.get("id") == card_id:
                    is_virtual_card = True
                    break

        if is_virtual_card and card_id:
            # Use updateVirtualDebitCard mutation for virtual cards
            query_string = """
            mutation UpdateVirtualDebitCard($input: UpdateVirtualDebitCardInput!) {
              updateVirtualDebitCard(input: $input) {
                result {
                  id
                  subaccount {
                    id
                    displayName
                    __typename
                  }
                  __typename
                }
                __typename
              }
            }
            """

            variables = {
                "input": {
                    "debitCardId": card_id,
                    "subaccountId": resolved_pocket_id
                }
            }

            response = requests.post(URL, headers=headers, json={
                "operationName": "UpdateVirtualDebitCard",
                "variables": variables,
                "query": query_string
            })
        else:
            # Use setSpendSubaccount mutation for physical cards (user's global setting)
            query_string = """
            mutation SetActiveSpendPocketScottie($input: SetSpendSubaccountInput!) {
              setSpendSubaccount(input: $input) {
                result {
                  id
                  userSpendConfig {
                    id
                    selectedSpendSubaccount {
                      id
                      clearedBalance
                      __typename
                    }
                    __typename
                  }
                  __typename
                }
                __typename
              }
            }
            """

            variables = {
                "input": {
                    "userId": user_id,
                    "selectedSpendSubaccountId": resolved_pocket_id
                }
            }

            response = requests.post(URL, headers=headers, json={
                "operationName": "SetActiveSpendPocketScottie",
                "variables": variables,
                "query": query_string
            })

        data = response.json()

        if 'errors' in data:
            return {"error": data['errors'][0]['message']}

        print("🧹 Clearing Cache after spend pocket update...")
        cache.clear()

        if is_virtual_card:
            return {"success": True, "result": data.get("data", {}).get("updateVirtualDebitCard", {}).get("result")}
        else:
            return {"success": True, "result": data.get("data", {}).get("setSpendSubaccount", {}).get("result")}

    except Exception as e:
        return {"error": str(e)}

# Update the main action to use the helper
def create_bill_action(name, amount, frequency_key, day_of_month, match_string=None, min_amt=None, max_amt=None, is_variable=False):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        
        account_id = get_primary_account_id()
        if not account_id: return {"error": "Main Account ID not found"}

        # --- 1. Map Frequency & Interval ---
        freq_map = {
            "WEEKLY":        ("WEEKLY", 1),
            "BIWEEKLY":      ("WEEKLY", 2),
            "MONTHLY":       ("MONTHLY", 1),
            "QUARTERLY":     ("MONTHLY", 3),
            "SEMI_ANNUALLY": ("MONTHLY", 6),
            "ANNUALLY":      ("YEARLY", 1)
        }
        
        if frequency_key not in freq_map:
            return {"error": "Invalid frequency selected"}
            
        final_freq, final_interval = freq_map[frequency_key]

        # --- 2. Calculate Anchor Date ---
        today = date.today()
        last_day_prev_month = today.replace(day=1) - timedelta(days=1)
        try:
            anchor_date_obj = last_day_prev_month.replace(day=int(day_of_month))
        except ValueError:
            anchor_date_obj = last_day_prev_month
            
        anchor_date_str = anchor_date_obj.strftime("%Y-%m-%d")

        # --- 3. Build Reassignment Rule ---
        reassignment_rule = None
        if match_string:
            rule = {"match": match_string}
            if min_amt: rule["minAmount"] = int(float(min_amt) * 100)
            if max_amt: rule["maxAmount"] = int(float(max_amt) * 100)
            reassignment_rule = rule

        # --- 4. Mutation (Simplified, as we fetch name separately now) ---
        query_string = """
        mutation CreateBill($input: CreateBillInput!) {
            createBill(input: $input) {
                result {
                    id
                    name
                    status
                    amount
                    reservedAmount
                }
            }
        }
        """
        
        variables = {
            "input": {
                "accountId": account_id,
                "amount": int(float(amount) * 100),
                "anchorDate": anchor_date_str,
                "frequency": final_freq,
                "frequencyInterval": final_interval,
                "autoAdjustAmount": is_variable,
                "paused": False,
                "name": name,
                "reassignmentRule": reassignment_rule
            }
        }

        response = requests.post(URL, headers=headers, json={
            "operationName": "CreateBill",
            "variables": variables,
            "query": query_string
        })

        data = response.json()
        
        if 'errors' in data:
            return {"error": data['errors'][0]['message']}
            
        print("🧹 Clearing Cache after bill creation...")
        cache.clear()
        
        # --- 5. Fetch Funding Name & Combine ---
        result = data.get("data", {}).get("createBill", {}).get("result", {})
        
        # Fetch the name from the separate query you provided
        funding_name = get_bill_funding_source()
        
        # Inject it into the result for the frontend
        result['fundingDisplayName'] = funding_name
        
        return {"success": True, "result": result}

    except Exception as e:
        return {"error": str(e)}

# --- ROUTES ---

# --- AUTHENTICATION ROUTES ---
@app.route('/login')
def login():
    """Login page - shows registration form if no users exist"""
    if current_user.is_authenticated:
        return redirect('/')

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    user_count = c.fetchone()[0]
    conn.close()

    if user_count == 0:
        return render_template('register.html')
    return render_template('login.html')

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    """Handle login form submission"""
    data = request.json
    username = data.get('username')
    password = data.get('password')

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, email, password_hash FROM users WHERE username = ?", (username,))
    row = c.fetchone()

    if row and check_password_hash(row[3], password):
        user = User(row[0], row[1], row[2])
        login_user(user)

        # Update last login
        c.execute("UPDATE users SET last_login = ? WHERE id = ?",
                  (datetime.now().isoformat(), row[0]))
        conn.commit()
        conn.close()

        return jsonify({"success": True})

    conn.close()
    return jsonify({"success": False, "error": "Invalid username or password"}), 401

@app.route('/api/auth/logout', methods=['POST'])
@login_required
def api_logout():
    """Handle logout"""
    logout_user()
    return jsonify({"success": True})

@app.route('/api/auth/register', methods=['POST'])
def api_register():
    """Handle registration - only allowed if no users exist"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Check if users already exist (single-tenant model)
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] > 0:
        conn.close()
        return jsonify({"success": False, "error": "Registration is disabled"}), 403

    data = request.json
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    # Validate input
    if not username or not password:
        conn.close()
        return jsonify({"success": False, "error": "Username and password required"}), 400

    if len(password) < 8:
        conn.close()
        return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400

    # Create user
    password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    try:
        c.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                  (username, email, password_hash))
        conn.commit()
        user_id = c.lastrowid
        conn.close()

        # Auto-login
        user = User(user_id, username, email)
        login_user(user)

        return jsonify({"success": True})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "error": "Username already exists"}), 400

@app.route('/api/auth/change-password', methods=['POST'])
@login_required
def api_change_password():
    """Handle password change"""
    data = request.json
    current_password = data.get('current_password')
    new_password = data.get('new_password')

    if not current_password or not new_password:
        return jsonify({"success": False, "error": "Both current and new passwords required"}), 400

    if len(new_password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE id = ?", (current_user.id,))
    row = c.fetchone()

    if row and check_password_hash(row[0], current_password):
        new_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, current_user.id))
        conn.commit()
        conn.close()
        return jsonify({"success": True})

    conn.close()
    return jsonify({"success": False, "error": "Current password is incorrect"}), 401

# --- WEBAUTHN/PASSKEY ENDPOINTS ---

def base64url_to_bytes(base64url_string):
    """Convert base64url string to bytes"""
    import base64
    # Add padding if needed
    padding = 4 - (len(base64url_string) % 4)
    if padding != 4:
        base64url_string += '=' * padding
    # Convert base64url to base64
    base64_string = base64url_string.replace('-', '+').replace('_', '/')
    # Decode to bytes
    return base64.b64decode(base64_string)

@app.route('/api/auth/webauthn/register/options', methods=['POST'])
@login_required
def webauthn_register_options():
    """Generate options for passkey registration"""
    user = current_user
    rp_id = get_webauthn_rp_id()
    origin = get_webauthn_origin()

    print(f"[WebAuthn Register] User: {user.username}, RP_ID: {rp_id}, Origin: {origin}")
    print(f"[WebAuthn Register] User-Agent: {request.headers.get('User-Agent', 'Unknown')}")

    # Generate registration options
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=str(user.id).encode('utf-8'),
        user_name=user.username,
        user_display_name=user.username,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,  # Enable discoverable credentials
            user_verification=UserVerificationRequirement.PREFERRED
        ),
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )

    # Store challenge in database with 15-minute expiration
    session_id = os.urandom(16).hex()
    expires_at = datetime.now() + timedelta(minutes=15)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO webauthn_sessions (id, user_id, challenge, operation, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (session_id, user.id, options.challenge, 'register', expires_at.isoformat()))
    conn.commit()
    conn.close()

    # Cleanup expired sessions
    cleanup_expired_sessions()

    print(f"[WebAuthn Register] Generated session {session_id}")

    return jsonify({
        "sessionId": session_id,
        "options": options_to_json(options)
    })

@app.route('/api/auth/webauthn/register/verify', methods=['POST'])
@login_required
def webauthn_register_verify():
    """Verify passkey registration response"""
    data = request.json
    session_id = data.get('sessionId')
    credential = data.get('credential')
    nickname = data.get('nickname', 'Passkey')

    print(f"[WebAuthn Register Verify] Session: {session_id}, Nickname: {nickname}")
    print(f"[WebAuthn Register Verify] Credential ID: {credential.get('id', 'N/A')[:20]}...")
    print(f"[WebAuthn Register Verify] Credential type: {credential.get('type', 'N/A')}")

    # Retrieve challenge from database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT challenge, user_id, expires_at FROM webauthn_sessions
        WHERE id = ? AND operation = 'register'
    """, (session_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        print(f"[WebAuthn Register Verify] ERROR: Invalid session {session_id}")
        return jsonify({"success": False, "error": "Invalid session"}), 400

    challenge, user_id, expires_at = row

    # Check if session expired
    if datetime.fromisoformat(expires_at) < datetime.now():
        c.execute("DELETE FROM webauthn_sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()
        print(f"[WebAuthn Register Verify] ERROR: Session expired {session_id}")
        return jsonify({"success": False, "error": "Session expired"}), 400

    # Verify user matches
    if user_id != current_user.id:
        conn.close()
        print(f"[WebAuthn Register Verify] ERROR: User mismatch")
        return jsonify({"success": False, "error": "User mismatch"}), 403

    try:
        rp_id = get_webauthn_rp_id()
        origin = get_webauthn_origin()
        print(f"[WebAuthn Register Verify] Using RP_ID: {rp_id}, Origin: {origin}")

        # Parse credential JSON using webauthn library helper
        parsed_credential = parse_registration_credential_json(credential)

        # Verify registration response
        verification = verify_registration_response(
            credential=parsed_credential,
            expected_challenge=challenge,
            expected_origin=origin,
            expected_rp_id=rp_id,
        )

        print(f"[WebAuthn Register Verify] Verification successful!")

        # Save credential to database
        save_credential(current_user.id, {
            'credential_id': verification.credential_id,
            'public_key': verification.credential_public_key,
            'sign_count': verification.sign_count,
            'transports': credential.get('response', {}).get('transports', []),
            'aaguid': str(verification.aaguid) if verification.aaguid else '',
            'backup_eligible': getattr(verification, 'credential_backup_eligible', 0),
            'backup_state': getattr(verification, 'credential_backed_up', 0),
        })

        # Update credential nickname
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""
            UPDATE passkey_credentials
            SET nickname = ?
            WHERE credential_id = ?
        """, (nickname, verification.credential_id))
        conn.commit()

        # Delete used session
        c.execute("DELETE FROM webauthn_sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()

        print(f"[WebAuthn Register Verify] Passkey saved successfully")
        return jsonify({"success": True})

    except Exception as e:
        conn.close()
        print(f"[WebAuthn Register Verify] ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/auth/webauthn/authenticate/options', methods=['POST'])
def webauthn_authenticate_options():
    """Generate options for passkey authentication (supports username-less login)"""
    data = request.json
    username = data.get('username')

    print(f"[WebAuthn Auth] Username: {username if username else '(discoverable credential mode)'}")
    print(f"[WebAuthn Auth] User-Agent: {request.headers.get('User-Agent', 'Unknown')}")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    user_id = None
    allow_credentials = []

    if username:
        # Traditional mode: username provided, filter to user's credentials
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        row = c.fetchone()

        if not row:
            conn.close()
            print(f"[WebAuthn Auth] ERROR: User not found: {username}")
            return jsonify({"success": False, "error": "User not found"}), 404

        user_id = row[0]

        # Get user's registered credentials
        credentials = get_user_credentials(user_id)

        if not credentials:
            conn.close()
            print(f"[WebAuthn Auth] ERROR: No passkeys registered for user {username}")
            return jsonify({"success": False, "error": "No passkeys registered"}), 400

        print(f"[WebAuthn Auth] Found {len(credentials)} passkey(s) for user {username}")

        # Build allowed credentials list
        for cred in credentials:
            allow_credentials.append(
                PublicKeyCredentialDescriptor(
                    id=cred['credential_id'],
                    transports=[AuthenticatorTransport(t) for t in cred['transports']] if cred['transports'] else []
                )
            )
    else:
        # Discoverable credential mode: no username, browser will show all available passkeys
        print(f"[WebAuthn Auth] Using discoverable credentials (no username provided)")
        # allow_credentials remains empty - browser will prompt for any stored passkey

    # Generate authentication options
    rp_id = get_webauthn_rp_id()
    origin = get_webauthn_origin()
    print(f"[WebAuthn Auth] Using RP_ID: {rp_id}, Origin: {origin}")

    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow_credentials if allow_credentials else None,  # Empty list allows discoverable credentials
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    # Store challenge (user_id can be None for discoverable mode)
    session_id = os.urandom(16).hex()
    expires_at = datetime.now() + timedelta(minutes=15)

    c.execute("""
        INSERT INTO webauthn_sessions (id, user_id, challenge, operation, expires_at)
        VALUES (?, ?, ?, ?, ?)
    """, (session_id, user_id, options.challenge, 'authenticate', expires_at.isoformat()))
    conn.commit()
    conn.close()

    cleanup_expired_sessions()

    print(f"[WebAuthn Auth] Generated session {session_id}")

    return jsonify({
        "sessionId": session_id,
        "options": options_to_json(options)
    })

@app.route('/api/auth/webauthn/authenticate/verify', methods=['POST'])
def webauthn_authenticate_verify():
    """Verify passkey authentication response"""
    data = request.json
    session_id = data.get('sessionId')
    credential = data.get('credential')

    print(f"[WebAuthn Auth Verify] Session: {session_id}")
    print(f"[WebAuthn Auth Verify] Credential ID: {credential.get('id', 'N/A')[:20]}...")

    # Retrieve challenge
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT challenge, user_id, expires_at FROM webauthn_sessions
        WHERE id = ? AND operation = 'authenticate'
    """, (session_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        print(f"[WebAuthn Auth Verify] ERROR: Invalid session {session_id}")
        return jsonify({"success": False, "error": "Invalid session"}), 400

    challenge, user_id, expires_at = row

    # Check expiration
    if datetime.fromisoformat(expires_at) < datetime.now():
        c.execute("DELETE FROM webauthn_sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()
        print(f"[WebAuthn Auth Verify] ERROR: Session expired {session_id}")
        return jsonify({"success": False, "error": "Session expired"}), 400

    # Parse credential JSON using webauthn library helper
    try:
        parsed_credential = parse_authentication_credential_json(credential)
    except Exception as e:
        conn.close()
        print(f"[WebAuthn Auth Verify] ERROR: Failed to parse credential: {str(e)}")
        return jsonify({"success": False, "error": f"Failed to parse credential: {str(e)}"}), 400

    # Convert base64url credential_id to bytes for database lookup
    credential_id_bytes = base64url_to_bytes(credential['rawId'])

    # Get credential from database
    c.execute("""
        SELECT public_key, sign_count, user_id FROM passkey_credentials
        WHERE credential_id = ?
    """, (credential_id_bytes,))
    cred_row = c.fetchone()

    if not cred_row:
        conn.close()
        print(f"[WebAuthn Auth Verify] ERROR: Credential not found in database")
        return jsonify({"success": False, "error": "Credential not found"}), 404

    public_key, current_sign_count, cred_user_id = cred_row

    # Verify user matches (if user_id was provided in session)
    # If user_id is None, we're in discoverable credential mode - use credential's user_id
    if user_id is not None and cred_user_id != user_id:
        conn.close()
        print(f"[WebAuthn Auth Verify] ERROR: Credential/user mismatch")
        return jsonify({"success": False, "error": "Credential/user mismatch"}), 403

    # Use credential's user_id for discoverable mode
    if user_id is None:
        user_id = cred_user_id
        print(f"[WebAuthn Auth Verify] Discoverable mode: identified user_id {user_id}")

    try:
        rp_id = get_webauthn_rp_id()
        origin = get_webauthn_origin()
        print(f"[WebAuthn Auth Verify] Using RP_ID: {rp_id}, Origin: {origin}")

        # Verify authentication response
        verification = verify_authentication_response(
            credential=parsed_credential,
            expected_challenge=challenge,
            expected_origin=origin,
            expected_rp_id=rp_id,
            credential_public_key=public_key,
            credential_current_sign_count=current_sign_count,
        )

        print(f"[WebAuthn Auth Verify] Verification successful!")

        # Update sign count
        update_sign_count(credential_id_bytes, verification.new_sign_count)

        # Get user data and create session
        c.execute("SELECT id, username, email FROM users WHERE id = ?", (user_id,))
        user_row = c.fetchone()

        if user_row:
            user = User(user_row[0], user_row[1], user_row[2])
            login_user(user)

            # Update last login
            c.execute("UPDATE users SET last_login = ? WHERE id = ?",
                      (datetime.now().isoformat(), user_id))
            conn.commit()

        # Delete used session
        c.execute("DELETE FROM webauthn_sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()

        print(f"[WebAuthn Auth Verify] Login successful!")
        return jsonify({"success": True})

    except Exception as e:
        conn.close()
        print(f"[WebAuthn Auth Verify] ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/api/auth/passkeys/available')
def api_passkeys_available():
    """Check if any passkeys are registered in the system (public endpoint for login page)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM passkey_credentials")
    count = c.fetchone()[0]
    conn.close()

    return jsonify({"available": count > 0})

@app.route('/api/auth/passkeys')
@login_required
def api_list_passkeys():
    """List user's registered passkeys"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT id, credential_id, nickname, created_at, last_used_at, transports, backup_state
        FROM passkey_credentials
        WHERE user_id = ?
        ORDER BY last_used_at DESC NULLS LAST, created_at DESC
    """, (current_user.id,))

    passkeys = []
    for row in c.fetchall():
        passkeys.append({
            'id': row[0],
            'credentialId': row[1].hex(),
            'nickname': row[2] or 'Passkey',
            'createdAt': row[3],
            'lastUsedAt': row[4],
            'transports': json.loads(row[5]) if row[5] else [],
            'isSynced': bool(row[6])
        })

    conn.close()
    return jsonify({"passkeys": passkeys})

@app.route('/api/auth/passkeys/<int:passkey_id>', methods=['DELETE'])
@login_required
def api_delete_passkey(passkey_id):
    """Delete a passkey credential"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Verify ownership
    c.execute("SELECT user_id FROM passkey_credentials WHERE id = ?", (passkey_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Passkey not found"}), 404

    if row[0] != current_user.id:
        conn.close()
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    # Delete credential
    c.execute("DELETE FROM passkey_credentials WHERE id = ?", (passkey_id,))
    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route('/api/auth/passkeys/<int:passkey_id>', methods=['PATCH'])
@login_required
def api_update_passkey(passkey_id):
    """Update passkey nickname"""
    data = request.json
    nickname = data.get('nickname', '').strip()

    if not nickname:
        return jsonify({"success": False, "error": "Nickname required"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Verify ownership
    c.execute("SELECT user_id FROM passkey_credentials WHERE id = ?", (passkey_id,))
    row = c.fetchone()

    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Passkey not found"}), 404

    if row[0] != current_user.id:
        conn.close()
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    # Update nickname
    c.execute("UPDATE passkey_credentials SET nickname = ? WHERE id = ?",
              (nickname, passkey_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route('/')
@login_required
def index():
    # Check if onboarding is complete
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_completed FROM onboarding_config LIMIT 1")
    row = c.fetchone()
    conn.close()

    is_onboarding_complete = bool(row and row[0] == 1) if row else False

    if not is_onboarding_complete:
        return render_template('onboarding.html')

    return render_template('index.html')

@app.route('/debug')
@login_required
def debug(): return render_template('debug.html')

# --- PWA ROUTES ---
@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/json')

@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

# --- ONBOARDING API ENDPOINTS ---
@app.route('/api/onboarding/status')
@login_required
def api_onboarding_status():
    """Check if onboarding is complete"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_completed FROM onboarding_config LIMIT 1")
    row = c.fetchone()
    conn.close()

    is_complete = bool(row and row[0] == 1) if row else False
    has_crew = get_crew_bearer_token() is not None

    return jsonify({
        "isComplete": is_complete,
        "hasCrewToken": has_crew
    })

@app.route('/api/onboarding/crew/save-token', methods=['POST'])
@login_required
def api_save_crew_token():
    """Save and validate Crew bearer token"""
    data = request.get_json()
    bearer_token = data.get('bearerToken', '').strip()

    if not bearer_token:
        return jsonify({"success": False, "error": "Token is required"}), 400

    # Validate token by attempting to fetch user data
    try:
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "authorization": bearer_token,
            "user-agent": "Crew/1 CFNetwork/3860.300.31 Darwin/25.2.0"
        }
        query_string = """ query CurrentUser { currentUser { id accounts { id } } } """
        response = requests.post(
            "https://api.trycrew.com/willow/graphql",
            headers=headers,
            json={"operationName": "CurrentUser", "query": query_string},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": "Invalid token - authentication failed"}), 400

        # Check if response contains valid user data
        result = response.json()
        if "errors" in result or not result.get("data", {}).get("currentUser"):
            return jsonify({"success": False, "error": "Invalid token"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": f"Token validation failed: {str(e)}"}), 500

    # Save to database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT id FROM crew_config LIMIT 1")
    existing = c.fetchone()

    if existing:
        c.execute("UPDATE crew_config SET bearer_token = ?, is_valid = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (bearer_token, existing[0]))
    else:
        c.execute("INSERT INTO crew_config (bearer_token, is_valid) VALUES (?, 1)", (bearer_token,))

    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route('/api/onboarding/complete', methods=['POST'])
@login_required
def api_complete_onboarding():
    """Mark onboarding as complete"""
    # Verify token exists
    if not get_crew_bearer_token():
        return jsonify({"success": False, "error": "No Crew token configured"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT id FROM onboarding_config LIMIT 1")
    existing = c.fetchone()

    if existing:
        c.execute("UPDATE onboarding_config SET is_completed = 1, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (existing[0],))
    else:
        c.execute("INSERT INTO onboarding_config (is_completed, completed_at) VALUES (1, CURRENT_TIMESTAMP)")

    conn.commit()
    conn.close()

    return jsonify({"success": True})

# --- ACCOUNT SETTINGS API ROUTES ---
@app.route('/api/account/credentials/status')
@login_required
def api_get_credentials_status():
    """Get status of all configured credentials (without exposing actual values)"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Check Crew token
        c.execute("SELECT bearer_token, is_valid FROM crew_config WHERE is_valid = 1 LIMIT 1")
        crew_row = c.fetchone()
        crew_configured = crew_row is not None and crew_row[0] is not None

        # Check SimpleFin access URL
        c.execute("SELECT access_url, is_valid FROM simplefin_config LIMIT 1")
        simplefin_row = c.fetchone()
        simplefin_configured = simplefin_row is not None and simplefin_row[0] is not None

        # Check LunchFlow API key
        c.execute("SELECT api_key, is_valid FROM lunchflow_config WHERE is_valid = 1 LIMIT 1")
        lunchflow_row = c.fetchone()
        lunchflow_configured = lunchflow_row is not None and lunchflow_row[0] is not None

        conn.close()

        return jsonify({
            "success": True,
            "credentials": {
                "crew": {
                    "configured": crew_configured,
                    "valid": crew_row[1] if crew_row else False
                },
                "simplefin": {
                    "configured": simplefin_configured,
                    "valid": simplefin_row[1] if simplefin_row else False
                },
                "lunchflow": {
                    "configured": lunchflow_configured,
                    "valid": lunchflow_row[1] if lunchflow_row else False
                },
                "splitwise": {
                    "configured": bool(get_splitwise_api_key()),
                    "valid": True  # If it exists, it's valid (validated on save)
                }
            }
        })
    except Exception as e:
        print(f"Error getting credentials status: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/account/crew/update-token', methods=['POST'])
@login_required
def api_account_update_crew_token():
    """Update Crew bearer token from account settings"""
    data = request.get_json()
    bearer_token = data.get('token', '').strip()

    if not bearer_token:
        return jsonify({"success": False, "error": "Token is required"}), 400

    # Validate token by attempting to fetch user data
    try:
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "authorization": bearer_token,
            "user-agent": "Crew/1 CFNetwork/3860.300.31 Darwin/25.2.0"
        }
        query_string = """ query CurrentUser { currentUser { id accounts { id } } } """
        response = requests.post(
            "https://api.trycrew.com/willow/graphql",
            headers=headers,
            json={"operationName": "CurrentUser", "query": query_string},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": "Invalid token - authentication failed"}), 400

        # Check if response contains valid user data
        result = response.json()
        if "errors" in result or not result.get("data", {}).get("currentUser"):
            return jsonify({"success": False, "error": "Invalid token"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": f"Token validation failed: {str(e)}"}), 500

    # Save to database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT id FROM crew_config LIMIT 1")
    existing = c.fetchone()

    if existing:
        c.execute("UPDATE crew_config SET bearer_token = ?, is_valid = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (bearer_token, existing[0]))
    else:
        c.execute("INSERT INTO crew_config (bearer_token, is_valid) VALUES (?, 1)", (bearer_token,))

    conn.commit()
    conn.close()

    cache.clear()
    return jsonify({"success": True, "message": "Crew token updated successfully"})

@app.route('/api/account/crew/test', methods=['POST'])
@login_required
def api_account_test_crew():
    """Test Crew connection with stored token"""
    bearer_token = get_crew_bearer_token()

    if not bearer_token:
        return jsonify({"success": False, "error": "No Crew token configured"}), 400

    try:
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "authorization": bearer_token,
            "user-agent": "Crew/1 CFNetwork/3860.300.31 Darwin/25.2.0"
        }
        query_string = """ query CurrentUser { currentUser { id firstName lastName } } """
        response = requests.post(
            "https://api.trycrew.com/willow/graphql",
            headers=headers,
            json={"operationName": "CurrentUser", "query": query_string},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": "Connection failed - authentication error"}), 400

        result = response.json()
        if "errors" in result or not result.get("data", {}).get("currentUser"):
            return jsonify({"success": False, "error": "Invalid token"}), 400

        user = result["data"]["currentUser"]
        return jsonify({
            "success": True,
            "message": f"Connected successfully as {user.get('firstName', '')} {user.get('lastName', '')}".strip() or "User"
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"Connection test failed: {str(e)}"}), 500

@app.route('/api/account/bank-details', methods=['GET'])
@login_required
def api_account_bank_details():
    """Fetch account and routing numbers from Crew Banking GraphQL"""
    bearer_token = get_crew_bearer_token()

    if not bearer_token:
        return jsonify({"success": False, "error": "No Crew token configured"}), 400

    try:
        headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "authorization": bearer_token,
            "user-agent": "Crew/1 CFNetwork/3860.300.31 Darwin/25.2.0"
        }
        query_string = """
            query CashAccountDetails {
                currentUser {
                    spendAccount {
                        accountNumber
                        institution {
                            routingNumber
                        }
                    }
                    saveAccount {
                        accountNumber
                        institution {
                            routingNumber
                        }
                    }
                }
            }
        """
        response = requests.post(
            URL,
            headers=headers,
            json={"operationName": "CashAccountDetails", "query": query_string},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": "Failed to fetch bank details"}), 400

        result = response.json()
        if "errors" in result or not result.get("data", {}).get("currentUser"):
            return jsonify({"success": False, "error": "Could not retrieve account details"}), 400

        user = result["data"]["currentUser"]
        spend = user.get("spendAccount") or {}
        save = user.get("saveAccount") or {}

        return jsonify({
            "success": True,
            "spendAccountNumber": spend.get("accountNumber", ""),
            "spendRoutingNumber": (spend.get("institution") or {}).get("routingNumber", ""),
            "saveAccountNumber": save.get("accountNumber", ""),
            "saveRoutingNumber": (save.get("institution") or {}).get("routingNumber", "")
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to fetch bank details: {str(e)}"}), 500

@app.route('/api/account/simplefin/update-token', methods=['POST'])
@login_required
def api_account_update_simplefin_token():
    """Update SimpleFin access token from account settings"""
    data = request.get_json()
    setup_token = data.get('token', '').strip()

    if not setup_token:
        return jsonify({"success": False, "error": "Setup token is required"}), 400

    # Validate and claim token
    try:
        import base64

        # Decode the base64 token to get the claim URL
        decoded = base64.b64decode(setup_token).decode('utf-8')

        # Make a POST request to claim the token
        response = requests.post(decoded, timeout=10)

        if response.status_code != 200:
            return jsonify({"success": False, "error": "Invalid setup token"}), 400

        # The response contains the access URL
        access_url = response.text.strip()

        if not access_url or not access_url.startswith('http'):
            return jsonify({"success": False, "error": "Invalid response from SimpleFin"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": f"Token validation failed: {str(e)}"}), 500

    # Save access URL to database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT id FROM simplefin_config LIMIT 1")
    existing = c.fetchone()

    if existing:
        c.execute("UPDATE simplefin_config SET access_url = ?, is_valid = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (access_url, existing[0]))
    else:
        c.execute("INSERT INTO simplefin_config (access_url, is_valid) VALUES (?, 1)", (access_url,))

    conn.commit()
    conn.close()

    cache.clear()
    return jsonify({"success": True, "message": "SimpleFin token updated successfully"})

@app.route('/api/account/simplefin/test', methods=['POST'])
@login_required
def api_account_test_simplefin():
    """Test SimpleFin connection"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT access_url FROM simplefin_config LIMIT 1")
        row = c.fetchone()
        conn.close()

        if not row or not row[0]:
            return jsonify({"success": False, "error": "No SimpleFin access URL configured"}), 400

        access_url = row[0]

        # Test the connection by fetching accounts with balances-only flag
        response = requests.get(f"{access_url}/accounts?balances-only=1", timeout=10)

        if response.status_code != 200:
            return jsonify({"success": False, "error": f"Connection failed with status {response.status_code}"}), 400

        data = response.json()
        account_count = len(data.get('accounts', []))

        return jsonify({
            "success": True,
            "message": f"Connected successfully. Found {account_count} account(s)."
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"Connection test failed: {str(e)}"}), 500

@app.route('/api/account/lunchflow/update-key', methods=['POST'])
@login_required
def api_account_update_lunchflow_key():
    """Update LunchFlow API key from account settings"""
    data = request.get_json()
    api_key = data.get('apiKey', '').strip()

    if not api_key:
        return jsonify({"success": False, "error": "API key is required"}), 400

    # Validate key by attempting to fetch accounts
    try:
        response = requests.get(
            "https://www.lunchflow.app/api/v1/accounts",
            headers={"x-api-key": api_key},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": "Invalid API key"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": f"Validation failed: {str(e)}"}), 500

    # Save to database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT id FROM lunchflow_config LIMIT 1")
    existing = c.fetchone()

    if existing:
        c.execute("UPDATE lunchflow_config SET api_key = ?, is_valid = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (api_key, existing[0]))
    else:
        c.execute("INSERT INTO lunchflow_config (api_key, is_valid) VALUES (?, 1)", (api_key,))

    conn.commit()
    conn.close()

    cache.clear()
    return jsonify({"success": True, "message": "LunchFlow API key updated successfully"})

@app.route('/api/account/lunchflow/test', methods=['POST'])
@login_required
def api_account_test_lunchflow():
    """Test LunchFlow connection"""
    api_key = get_lunchflow_api_key()

    if not api_key:
        return jsonify({"success": False, "error": "No LunchFlow API key configured"}), 400

    try:
        response = requests.get(
            "https://www.lunchflow.app/api/v1/accounts",
            headers={"x-api-key": api_key},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": f"Connection failed with status {response.status_code}"}), 400

        data = response.json()
        account_count = len(data.get('accounts', []))

        return jsonify({
            "success": True,
            "message": f"Connected successfully. Found {account_count} account(s)."
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"Connection test failed: {str(e)}"}), 500

@app.route('/api/account/splitwise/update-key', methods=['POST'])
@login_required
def api_account_update_splitwise_key():
    """Update Splitwise API key from account settings"""
    data = request.get_json()
    api_key = data.get('apiKey', '').strip()

    if not api_key:
        return jsonify({"success": False, "error": "API key is required"}), 400

    # Validate by getting current user
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_current_user",
            headers=headers,
            timeout=30
        )
    except Exception as e:
        return jsonify({"success": False, "error": f"Network error: {str(e)}"}), 500

    if response.status_code == 200:
        user_data = response.json().get("user", {})
        user_id = user_data.get("id")

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM splitwise_config")  # Clear old
        c.execute("INSERT INTO splitwise_config (api_key, user_id, is_valid) VALUES (?, ?, 1)",
                  (api_key, user_id))
        conn.commit()
        conn.close()

        cache.clear()
        return jsonify({"success": True, "message": "Splitwise API key updated successfully"})
    else:
        return jsonify({"success": False, "error": "Invalid API key"}), 400

@app.route('/api/account/splitwise/test', methods=['POST'])
@login_required
def api_account_test_splitwise():
    """Test Splitwise connection"""
    api_key = get_splitwise_api_key()

    if not api_key:
        return jsonify({"success": False, "error": "No Splitwise API key configured"}), 400

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_current_user",
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": f"Connection failed with status {response.status_code}"}), 400

        user_data = response.json().get("user", {})
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        name = f"{first_name} {last_name}".strip() or "User"

        return jsonify({
            "success": True,
            "message": f"Connected successfully as {name}."
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"Connection test failed: {str(e)}"}), 500

@app.route('/api/account/webauthn/config', methods=['GET'])
@login_required
def api_account_get_webauthn_config():
    """Get WebAuthn configuration (RP_ID and ORIGIN)"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT rp_id, origin, is_valid FROM webauthn_config WHERE is_valid = 1 ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()

    if row:
        return jsonify({
            "configured": True,
            "rp_id": row[0],
            "origin": row[1]
        })
    else:
        # Return env var defaults if not configured in database
        return jsonify({
            "configured": False,
            "rp_id": os.environ.get('RP_ID', 'localhost'),
            "origin": os.environ.get('ORIGIN', 'http://localhost:8080')
        })

@app.route('/api/account/webauthn/update-config', methods=['POST'])
@login_required
def api_account_update_webauthn_config():
    """Update WebAuthn configuration (RP_ID and ORIGIN)"""
    data = request.json
    rp_id = data.get('rp_id', '').strip()
    origin = data.get('origin', '').strip()

    if not rp_id:
        return jsonify({"success": False, "error": "RP_ID is required"}), 400

    if not origin:
        return jsonify({"success": False, "error": "Origin is required"}), 400

    # Validate origin format (should start with http:// or https://)
    if not origin.startswith('http://') and not origin.startswith('https://'):
        return jsonify({"success": False, "error": "Origin must start with http:// or https://"}), 400

    # Validate that origin doesn't have trailing slash
    if origin.endswith('/'):
        origin = origin[:-1]

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Mark all existing configs as invalid
        c.execute("UPDATE webauthn_config SET is_valid = 0")

        # Insert new config
        c.execute("""
            INSERT INTO webauthn_config (rp_id, origin, is_valid)
            VALUES (?, ?, 1)
        """, (rp_id, origin))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "WebAuthn configuration updated successfully"
        })

    except Exception as e:
        return jsonify({"success": False, "error": f"Failed to update configuration: {str(e)}"}), 500

@app.route('/api/account/webauthn/test', methods=['POST'])
@login_required
def api_account_test_webauthn():
    """Test WebAuthn configuration (validates format)"""
    data = request.json
    rp_id = data.get('rp_id', '').strip()
    origin = data.get('origin', '').strip()

    if not rp_id:
        return jsonify({"success": False, "error": "RP_ID is required"}), 400

    if not origin:
        return jsonify({"success": False, "error": "Origin is required"}), 400

    # Validate origin format
    if not origin.startswith('http://') and not origin.startswith('https://'):
        return jsonify({"success": False, "error": "Origin must start with http:// or https://"}), 400

    # Validate production HTTPS requirement
    if 'localhost' not in rp_id and '127.0.0.1' not in rp_id:
        if not origin.startswith('https://'):
            return jsonify({
                "success": False,
                "error": "Production deployments require HTTPS. Origin must start with https://"
            }), 400

    # Validate RP_ID matches origin domain
    origin_domain = origin.replace('https://', '').replace('http://', '').split(':')[0]
    if rp_id != origin_domain and not origin_domain.endswith('.' + rp_id):
        return jsonify({
            "success": False,
            "error": f"RP_ID '{rp_id}' must match origin domain '{origin_domain}'"
        }), 400

    return jsonify({
        "success": True,
        "message": "Configuration is valid"
    })

# --- WEB PUSH TOKEN MANAGEMENT ---

@app.route('/api/fcm/register-token', methods=['POST'])
@login_required
def api_fcm_register_token():
    """Register Web Push subscription for push notifications"""
    try:
        data = request.json
        token = data.get('token')
        device_name = data.get('device_name', 'Unknown Device')
        user_agent = request.headers.get('User-Agent', '')

        if not token:
            return jsonify({"error": "Token required"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Insert or update token
        c.execute("""INSERT INTO fcm_tokens (user_id, token, device_name, user_agent, last_used_at)
                     VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                     ON CONFLICT(token) DO UPDATE SET
                     last_used_at = CURRENT_TIMESTAMP, is_active = 1""",
                  (current_user.id, token, device_name, user_agent))
        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Token registered"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- WEB PUSH CONFIGURATION (Account Settings) ---

@app.route('/api/account/fcm/config', methods=['GET'])
@login_required
def api_get_fcm_config():
    """Get VAPID configuration status (without exposing secrets)"""
    try:
        fcm_config = get_fcm_config()
        return jsonify({
            "success": True,
            "configured": fcm_config is not None,
            "vapid_public_key": fcm_config['vapid_public_key'] if fcm_config else None
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/account/fcm/update-config', methods=['POST'])
@login_required
def api_update_fcm_config():
    """Update VAPID configuration for Web Push"""
    try:
        data = request.json
        vapid_public = data.get('vapid_public_key', '').strip()
        vapid_private = data.get('vapid_private_key', '').strip()

        if not vapid_public or not vapid_private:
            return jsonify({"error": "Both VAPID keys required"}), 400

        # Basic validation - VAPID keys should be base64url strings
        if len(vapid_public) < 20 or len(vapid_private) < 20:
            return jsonify({"error": "Invalid VAPID key format"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Delete old config and insert new (keeping old columns empty for backward compatibility)
        c.execute("DELETE FROM fcm_config")
        c.execute("""INSERT INTO fcm_config (vapid_public_key, vapid_private_key,
                     firebase_project_id, service_account_json)
                     VALUES (?, ?, '', '')""",
                  (vapid_public, vapid_private))
        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "VAPID configuration saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/account/fcm/test', methods=['POST'])
@login_required
def api_test_fcm():
    """Test Web Push configuration by sending a test notification"""
    try:
        send_sync_complete_notification(
            current_user.id,
            1,
            ['Test Account']
        )
        return jsonify({"success": True, "message": "Test notification sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- API ROUTES ---
@app.route('/api/family')
@login_required
def api_family(): return jsonify(get_family_data())
@app.route('/api/cards')
@login_required
def api_cards():
    # Allow forcing a refresh if ?refresh=true is passed
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_cards_data(force_refresh=refresh))

# 3. CREATE THE MISSING MOVE/REORDER ENDPOINT
@app.route('/api/groups/move-pocket', methods=['POST'])
@login_required
def api_move_pocket():
    data = request.json
    
    # We expect: 
    # 1. targetGroupId (where it's going)
    # 2. orderedPocketIds (the full list of pocket IDs in that group, in order)
    
    target_group_id = data.get('targetGroupId') # Can be None (Ungrouped)
    ordered_ids = data.get('orderedPocketIds', [])
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        # Loop through the list provided by frontend and update both Group and Order
        for index, pocket_id in enumerate(ordered_ids):
            if target_group_id is None:
                # If ungrouped, we delete the link (or set group_id NULL if you prefer)
                # But to keep sorting in "Ungrouped" area, let's keep the row with NULL group_id
                # Check if exists
                c.execute("INSERT OR REPLACE INTO pocket_links (pocket_id, group_id, sort_order) VALUES (?, NULL, ?)", (pocket_id, index))
            else:
                c.execute("INSERT OR REPLACE INTO pocket_links (pocket_id, group_id, sort_order) VALUES (?, ?, ?)", (pocket_id, target_group_id, index))
        
        conn.commit()
        cache.clear()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)})
    finally:
        conn.close()

@app.route('/api/groups/manage', methods=['POST'])
@login_required
def api_manage_group():
    # Handles Create and Update
    data = request.json
    group_id = data.get('id') # None if creating
    name = data.get('name')
    pocket_ids = data.get('pockets', []) # List of pocket IDs to assign
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        if not group_id:
            # CREATE
            c.execute("INSERT INTO groups (name) VALUES (?)", (name,))
            group_id = c.lastrowid
        else:
            # UPDATE NAME
            c.execute("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))
            
        # UPDATE POCKET LINKS
        # 1. Remove all pockets currently assigned to this group (to handle unchecking)
        c.execute("DELETE FROM pocket_links WHERE group_id = ?", (group_id,))
        
        # 2. Assign selected pockets (Moving them from other groups if necessary)
        for pid in pocket_ids:
            # Remove from any other group first (implicit via REPLACE if we used that, but safer to delete old link)
            c.execute("DELETE FROM pocket_links WHERE pocket_id = ?", (pid,))
            c.execute("INSERT INTO pocket_links (pocket_id, group_id) VALUES (?, ?)", (pid, group_id))
            
        conn.commit()
        cache.clear()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)})
    finally:
        conn.close()

@app.route('/api/groups/delete', methods=['POST'])
@login_required
def api_delete_group():
    data = request.json
    group_id = data.get('id')
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        # Delete Group
        c.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        # Unlink pockets (they become ungrouped)
        c.execute("DELETE FROM pocket_links WHERE group_id = ?", (group_id,))
        conn.commit()
        cache.clear()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)})
    finally:
        conn.close()

# --- NEW API ROUTE: Assign Group ---
@app.route('/api/assign-group', methods=['POST'])
@login_required
def api_assign_group():
    data = request.json
    pocket_id = data.get('pocketId')
    group_name = data.get('groupName') # If empty string, we treat as ungroup
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        if not group_name or group_name.strip() == "":
            c.execute("DELETE FROM pocket_groups WHERE pocket_id = ?", (pocket_id,))
        else:
            c.execute("INSERT OR REPLACE INTO pocket_groups (pocket_id, group_name) VALUES (?, ?)", (pocket_id, group_name))
        conn.commit()
        
        # Clear cache to force UI update
        cache.clear()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)})
    finally:
        conn.close()

@app.route('/api/set-card-spend', methods=['POST'])
@login_required
def api_set_card_spend():
    data = request.json
    return jsonify(set_spend_pocket_action(
        data.get('userId'),
        data.get('pocketId'),
        data.get('cardId')
    ))

@app.route('/api/savings')
@login_required
def api_savings():
    # Check if the frontend is asking for a forced refresh
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_financial_data(force_refresh=refresh))

@app.route('/api/history')
@login_required
def api_history(): return jsonify(get_history())
@app.route('/api/transactions')
@login_required
def api_transactions():
    q = request.args.get('q')
    min_date = request.args.get('minDate')
    max_date = request.args.get('maxDate')
    min_amt = request.args.get('minAmt')
    max_amt = request.args.get('maxAmt')

    # Get regular transactions
    cached_result = get_transactions_data(q, min_date, max_date, min_amt, max_amt)

    # Create a new result dict to avoid mutating cached data
    result = {
        "transactions": list(cached_result.get("transactions", [])),  # Create a copy of the list
        "balance": cached_result.get("balance"),
        "allTransactions": cached_result.get("allTransactions", [])
    }

    # Get credit card transactions
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""SELECT ct.transaction_id, ct.amount, ct.date, ct.merchant, ct.description, ct.is_pending, ct.created_at, ccc.account_name
                     FROM credit_card_transactions ct
                     LEFT JOIN credit_card_config ccc ON ct.account_id = ccc.account_id
                     ORDER BY ct.date DESC, ct.created_at DESC""")
        rows = c.fetchall()
        conn.close()

        credit_card_txs = []
        for row in rows:
            tx_date = row[2]  # date field
            amount = row[1]  # amount (already in dollars)

            # Apply filters if provided
            if min_date and tx_date < min_date:
                continue
            if max_date and tx_date > max_date:
                continue
            if min_amt and abs(amount) < float(min_amt):
                continue
            if max_amt and abs(amount) > float(max_amt):
                continue
            # Search in merchant, description, and account name
            if q:
                search_term = q.lower()
                merchant = (row[3] or "").lower()
                description = (row[4] or "").lower()
                account_name = (row[7] or "").lower()
                if search_term not in merchant and search_term not in description and search_term not in account_name:
                    continue

            # Format as Crew transaction format
            credit_card_txs.append({
                "id": f"cc_{row[0]}",  # Prefix to avoid conflicts
                "title": row[3] or row[4] or "Credit Card Transaction",
                "description": row[4] or "",
                "amount": -abs(amount),  # Negative for expenses
                "date": tx_date,
                "type": "DEBIT",
                "subaccountId": None,
                "isCreditCard": True,
                "merchant": row[3],
                "isPending": bool(row[5]),
                "accountName": row[7] or "Credit Card"  # Add account name
            })

        # Merge and sort by pending status first, then by date
        if result["transactions"]:
            all_txs = result["transactions"] + credit_card_txs
            # Sort by: pending first (True > False), then by date (newest first)
            # Handle None dates by treating them as empty strings for sorting
            all_txs.sort(key=lambda x: (not x.get("isPending", False), x.get("date") or ""), reverse=True)
            result["transactions"] = all_txs
        elif credit_card_txs:
            # Sort credit card transactions only
            credit_card_txs.sort(key=lambda x: (not x.get("isPending", False), x.get("date") or ""), reverse=True)
            result["transactions"] = credit_card_txs

    except Exception as e:
        print(f"Error loading credit card transactions: {e}")

    return jsonify(result)

@app.route('/api/pocket-transactions/<path:pocket_id>')
@login_required
def api_pocket_transactions(pocket_id):
    """Fetch transactions for a specific pocket/subaccount"""
    try:
        headers = get_crew_headers()
        if not headers:
            return jsonify({"error": "Credentials not found"})

        account_id = get_primary_account_id()
        if not account_id:
            return jsonify({"error": "Could not find Account ID"})

        page_size = request.args.get('pageSize', 50, type=int)

        query_string = """
        query RecentActivity($accountId: ID!, $cursor: String, $pageSize: Int = 50, $searchFilters: CashTransactionFilter) {
          account: node(id: $accountId) {
            ... on Account {
              id
              cashTransactions(first: $pageSize, after: $cursor, searchFilters: $searchFilters) {
                pageInfo {
                  hasNextPage
                  endCursor
                }
                edges {
                  node {
                    id
                    amount
                    currencyCode
                    memo
                    externalMemo
                    imageUrl
                    occurredAt
                    matchingName
                    status
                    title
                    type
                    subaccount {
                      id
                      displayName
                    }
                    transfer {
                      id
                      type
                      status
                    }
                  }
                }
              }
            }
          }
        }
        """

        variables = {
            "pageSize": page_size,
            "accountId": account_id,
            "searchFilters": {
                "subaccountId": pocket_id
            }
        }

        response = requests.post(URL, headers=headers, json={
            "operationName": "RecentActivity",
            "variables": variables,
            "query": query_string
        })

        if response.status_code != 200:
            return jsonify({"error": f"API Error: {response.text}"})

        data = response.json()

        if 'errors' in data:
            return jsonify({"error": data['errors'][0].get('message', 'Unknown error')})

        edges = data.get('data', {}).get('account', {}).get('cashTransactions', {}).get('edges', [])
        page_info = data.get('data', {}).get('account', {}).get('cashTransactions', {}).get('pageInfo', {})

        transactions = []
        for edge in edges:
            node = edge.get('node', {})
            amt = node.get('amount', 0) / 100.0
            date_str = node.get('occurredAt', '')[:10] if node.get('occurredAt') else ''

            transactions.append({
                "id": node.get('id'),
                "title": node.get('title'),
                "matchingName": node.get('matchingName'),
                "amount": amt,
                "date": date_str,
                "occurredAt": node.get('occurredAt'),
                "type": node.get('type'),
                "status": node.get('status'),
                "memo": node.get('memo'),
                "externalMemo": node.get('externalMemo'),
                "imageUrl": node.get('imageUrl'),
                "transferType": node.get('transfer', {}).get('type') if node.get('transfer') else None
            })

        return jsonify({
            "transactions": transactions,
            "hasNextPage": page_info.get('hasNextPage', False),
            "endCursor": page_info.get('endCursor')
        })

    except Exception as e:
        print(f"Pocket transactions error: {e}")
        return jsonify({"error": str(e)})

@app.route('/api/transaction/<path:tx_id>')
@login_required
def api_transaction_detail(tx_id): return jsonify(get_transaction_detail(tx_id))

@app.route('/api/expenses')
@login_required
def api_expenses():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_expenses_data(force_refresh=refresh))

@app.route('/api/goals')
@login_required
def api_goals():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_goals_data(force_refresh=refresh))

@app.route('/api/trends')
@login_required
def api_trends(): return jsonify(get_monthly_trends())

@app.route('/api/subaccounts')
@login_required
def api_subaccounts():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_subaccounts_list(force_refresh=refresh))

@app.route('/api/family-subaccounts')
@login_required
def api_family_subaccounts():
    return jsonify(get_family_subaccounts())

@app.route('/api/move-money', methods=['POST'])
@login_required
def api_move_money():
    data = request.json
    return jsonify(move_money(data.get('fromId'), data.get('toId'), data.get('amount'), data.get('memo')))

@app.route('/api/delete-pocket', methods=['POST'])
@login_required
def api_delete_pocket():
    data = request.json
    return jsonify(delete_subaccount_action(data.get('id')))


@app.route('/api/create-pocket', methods=['POST'])
@login_required
def api_create_pocket():
    data = request.json
    result = create_pocket(
        data.get('name'), 
        data.get('amount'), 
        data.get('initial'), 
        data.get('note')
    )
    
    # If pocket creation was successful and groupId is provided, assign to group
    if result.get('success') and data.get('groupId'):
        pocket_id = result['result']['id']
        group_id = data.get('groupId')
        
        # Assign to group in database
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute("INSERT OR REPLACE INTO pocket_links (pocket_id, group_id, sort_order) VALUES (?, ?, ?)", 
                     (pocket_id, group_id, 0))
            conn.commit()
        except Exception as e:
            print(f"Warning: Failed to assign pocket to group: {e}")
        finally:
            conn.close()
    
    return jsonify(result)

@app.route('/api/delete-bill', methods=['POST'])
@login_required
def api_delete_bill():
    data = request.json
    return jsonify(delete_bill_action(data.get('id')))


@app.route('/api/create-bill', methods=['POST'])
@login_required
def api_create_bill():
    data = request.json
    return jsonify(create_bill_action(
        data.get('name'),
        data.get('amount'),
        data.get('frequency'),
        data.get('dayOfMonth'),
        data.get('matchString'),
        data.get('minAmount'),
        data.get('maxAmount'),
        data.get('variable')
    ))

@app.route('/api/user')
@login_required
def api_user():
    return jsonify(get_user_profile_info())

@app.route('/api/intercom')
@login_required
def api_intercom():
    return jsonify(get_intercom_data())

# --- LUNCHFLOW API ENDPOINTS ---
@app.route('/api/lunchflow/get-config')
@login_required
def api_get_lunchflow_config():
    """Get LunchFlow configuration status"""
    api_key = get_lunchflow_api_key()
    return jsonify({
        "hasApiKey": api_key is not None,
        "isConfigured": api_key is not None
    })

@app.route('/api/lunchflow/save-key', methods=['POST'])
@login_required
def api_save_lunchflow_key():
    """Save LunchFlow API key (called from Credit Cards section)"""
    data = request.get_json()
    api_key = data.get('apiKey', '').strip()

    if not api_key:
        return jsonify({"success": False, "error": "API key is required"}), 400

    # Validate key by attempting to fetch accounts
    try:
        response = requests.get(
            "https://www.lunchflow.app/api/v1/accounts",
            headers={"x-api-key": api_key},
            timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "error": "Invalid API key"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": f"Validation failed: {str(e)}"}), 500

    # Save to database
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT id FROM lunchflow_config LIMIT 1")
    existing = c.fetchone()

    if existing:
        c.execute("UPDATE lunchflow_config SET api_key = ?, is_valid = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                  (api_key, existing[0]))
    else:
        c.execute("INSERT INTO lunchflow_config (api_key, is_valid) VALUES (?, 1)", (api_key,))

    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route('/api/lunchflow/accounts')
@login_required
def api_lunchflow_accounts():
    """List all accounts from LunchFlow"""
    api_key = get_lunchflow_api_key()
    if not api_key:
        return jsonify({"error": "LunchFlow API key not configured. Please set LUNCHFLOW_API_KEY in docker-compose.yml"}), 400
    
    try:
        headers = {
            "x-api-key": api_key,
            "accept": "application/json"
        }
        # Use www.lunchflow.app as per documentation
        response = requests.get("https://www.lunchflow.app/api/v1/accounts", headers=headers, timeout=30)
        
        if response.status_code != 200:
            return jsonify({"error": f"LunchFlow API error: {response.status_code} - {response.text}"}), response.status_code
        
        data = response.json()
        # Return the data in the expected format with accounts array
        return jsonify(data)
    except requests.exceptions.ConnectionError as e:
        return jsonify({"error": f"Connection error: Unable to connect to LunchFlow API. Please check your internet connection and try again. ({str(e)})"}), 500
    except requests.exceptions.Timeout as e:
        return jsonify({"error": f"Request timeout: LunchFlow API took too long to respond. ({str(e)})"}), 500
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Request error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route('/api/lunchflow/set-credit-card', methods=['POST'])
@login_required
def api_set_credit_card():
    """Store the selected credit card account ID (without creating pocket yet)"""
    data = request.json
    account_id = data.get('accountId')
    account_name = data.get('accountName', '')

    if not account_id:
        return jsonify({"error": "accountId is required"}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Store the account info with provider='lunchflow'
        c.execute("""INSERT OR REPLACE INTO credit_card_config
                     (account_id, account_name, provider, created_at)
                     VALUES (?, ?, 'lunchflow', CURRENT_TIMESTAMP)""",
                  (account_id, account_name))
        conn.commit()
        conn.close()

        cache.clear()
        return jsonify({"success": True, "message": "Credit card account saved", "needsBalanceSync": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/lunchflow/get-balance/<account_id>')
@login_required
def api_get_balance(account_id):
    """Get the balance for a specific LunchFlow account"""
    api_key = get_lunchflow_api_key()
    if not api_key:
        return jsonify({"error": "LunchFlow API key not configured"}), 400
    
    try:
        headers = {
            "x-api-key": api_key,
            "accept": "application/json"
        }
        response = requests.get(f"https://www.lunchflow.app/api/v1/accounts/{account_id}/balance", headers=headers, timeout=30)
        
        if response.status_code != 200:
            return jsonify({"error": f"LunchFlow API error: {response.status_code} - {response.text}"}), response.status_code
        
        data = response.json()
        return jsonify(data)
    except requests.exceptions.ConnectionError as e:
        return jsonify({"error": f"Connection error: {str(e)}"}), 500
    except requests.exceptions.Timeout as e:
        return jsonify({"error": f"Request timeout: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/lunchflow/create-pocket-with-balance', methods=['POST'])
@login_required
def api_create_pocket_with_balance():
    """Create the credit card pocket and optionally sync balance"""
    data = request.json
    account_id = data.get('accountId')
    sync_balance = data.get('syncBalance', False)
    
    if not account_id:
        return jsonify({"error": "accountId is required"}), 400
    
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Get account name
        c.execute("SELECT account_name FROM credit_card_config WHERE account_id = ?", (account_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Account not found. Please select an account first."}), 400
        
        account_name = row[0]
        
        # Get current balance from LunchFlow (always fetch for progress bar, but only sync pocket if requested)
        initial_amount = "0"
        current_balance_value = 0
        api_key = get_lunchflow_api_key()
        if api_key:
            try:
                headers = {"x-api-key": api_key, "accept": "application/json"}
                response = requests.get(f"https://www.lunchflow.app/api/v1/accounts/{account_id}/balance", headers=headers, timeout=30)
                if response.status_code == 200:
                    balance_data = response.json()
                    # Balance is already in dollars
                    balance_amount = balance_data.get("balance", {}).get("amount", 0)
                    current_balance_value = abs(balance_amount)
                    # Only set initial pocket amount if sync_balance is True
                    if sync_balance:
                        initial_amount = str(current_balance_value)
            except Exception as e:
                print(f"Warning: Could not fetch balance: {e}")
        
        # Create the pocket
        pocket_name = f"Credit Card - {account_name}"
        pocket_result = create_pocket(pocket_name, "0", initial_amount, f"Credit card tracking pocket for {account_name}")
        
        if "error" in pocket_result:
            conn.close()
            return jsonify({"error": f"Failed to create pocket: {pocket_result['error']}"}), 500
        
        pocket_id = pocket_result.get("result", {}).get("id")
        if not pocket_id:
            conn.close()
            return jsonify({"error": "Pocket was created but no ID was returned"}), 500
        
        # Update the config with pocket_id and current_balance
        c.execute("UPDATE credit_card_config SET pocket_id = ?, current_balance = ? WHERE account_id = ?",
                 (pocket_id, current_balance_value, account_id))
        conn.commit()
        conn.close()
        
        cache.clear()
        return jsonify({"success": True, "message": "Credit card pocket created", "pocketId": pocket_id, "syncedBalance": sync_balance})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/lunchflow/credit-card-status')
@login_required
def api_credit_card_status():
    """Get the current credit card account configuration (unified for both providers)"""
    api_key = get_lunchflow_api_key()

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get first account for backward compatibility
        c.execute("SELECT account_id, account_name, pocket_id, created_at, provider FROM credit_card_config LIMIT 1")
        row = c.fetchone()

        # Get ALL SimpleFin accounts for multi-account support
        c.execute("SELECT account_id, account_name, pocket_id, created_at, provider FROM credit_card_config WHERE provider='simplefin'")
        simplefin_rows = c.fetchall()

        # Check if SimpleFin access URL exists and is valid, and get last sync time
        c.execute("SELECT access_url, is_valid, last_sync FROM simplefin_config LIMIT 1")
        simplefin_url = c.fetchone()
        has_simplefin_access_url = bool(simplefin_url and simplefin_url[0])
        simplefin_token_invalid = bool(simplefin_url and simplefin_url[0] and simplefin_url[1] == 0)
        last_sync = simplefin_url[2] if simplefin_url and len(simplefin_url) > 2 else None

        conn.close()

        result = {
            "hasApiKey": bool(api_key),
            "configured": False,
            "pocketCreated": False,
            "accountId": None,
            "accountName": None,
            "pocketId": None,
            "createdAt": None,
            "provider": None,
            "hasSimplefinAccessUrl": has_simplefin_access_url,
            "simplefinTokenInvalid": simplefin_token_invalid,
            "lastSync": last_sync,  # NEW: Last sync timestamp
            "accounts": []  # NEW: Array of all SimpleFin accounts
        }

        # Backward compatibility: populate single account fields
        if row:
            account_id = row[0]
            # Check if this is a real account or just a temp record from token claim
            is_temp_record = account_id == 'temp_simplefin'

            if not is_temp_record:
                result["configured"] = True
                result["accountId"] = account_id
                result["accountName"] = row[1]
                result["pocketId"] = row[2]
                result["pocketCreated"] = bool(row[2])
                result["createdAt"] = row[3]
                result["provider"] = row[4] if len(row) > 4 else "lunchflow"

        # Populate accounts array for SimpleFin
        for sf_row in simplefin_rows:
            account_id = sf_row[0]
            # Skip temp records
            if account_id == 'temp_simplefin':
                continue

            result["accounts"].append({
                "accountId": account_id,
                "accountName": sf_row[1],
                "pocketId": sf_row[2],
                "createdAt": sf_row[3],
                "provider": sf_row[4],
                "pocketCreated": bool(sf_row[2])
            })

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/lunchflow/sync-balance', methods=['POST'])
@login_required
def api_sync_balance():
    """Sync the pocket balance to match the credit card balance"""
    data = request.json
    account_id = data.get('accountId')
    
    if not account_id:
        return jsonify({"error": "accountId is required"}), 400
    
    try:
        # Get pocket_id from database
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT pocket_id FROM credit_card_config WHERE account_id = ?", (account_id,))
        row = c.fetchone()
        conn.close()
        
        if not row or not row[0]:
            return jsonify({"error": "No pocket found for this account"}), 400
        
        pocket_id = row[0]

        # Get balance from LunchFlow
        api_key = get_lunchflow_api_key()
        if not api_key:
            return jsonify({"error": "LunchFlow API key not configured"}), 400
        
        headers = {"x-api-key": api_key, "accept": "application/json"}
        response = requests.get(f"https://www.lunchflow.app/api/v1/accounts/{account_id}/balance", headers=headers, timeout=30)
        
        if response.status_code != 200:
            return jsonify({"error": f"Failed to get balance: {response.status_code}"}), response.status_code
        
        balance_data = response.json()
        # Balance is already in dollars
        balance_amount = balance_data.get("balance", {}).get("amount", 0)
        target_balance = abs(balance_amount)

        # Save current balance to database
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE credit_card_config SET current_balance = ? WHERE account_id = ?", (target_balance, account_id))
        conn.commit()
        conn.close()

        # Get current pocket balance
        headers_crew = get_crew_headers()
        if not headers_crew:
            return jsonify({"error": "Crew credentials not found"}), 400
        
        query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
        response_crew = requests.post(URL, headers=headers_crew, json={
            "operationName": "GetSubaccount",
            "variables": {"id": pocket_id},
            "query": query_string
        })
        
        crew_data = response_crew.json()
        current_balance = 0
        try:
            current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
        except:
            pass
        
        # Calculate difference
        difference = target_balance - current_balance
        
        # Get Checking subaccount ID (not Account ID)
        all_subs = get_subaccounts_list()
        if "error" in all_subs:
            return jsonify({"error": "Could not get subaccounts list"}), 400
        
        checking_subaccount_id = None
        for sub in all_subs.get("subaccounts", []):
            if sub["name"] == "Checking":
                checking_subaccount_id = sub["id"]
                break
        
        if not checking_subaccount_id:
            return jsonify({"error": "Could not find Checking subaccount"}), 400
        
        # Transfer money to/from pocket
        if abs(difference) > 0.01:  # Only transfer if difference is significant
            if difference > 0:
                # Need to move money from Checking to Pocket
                result = move_money(checking_subaccount_id, pocket_id, str(difference), f"Sync credit card balance")
            else:
                # Need to move money from Pocket to Checking
                result = move_money(pocket_id, checking_subaccount_id, str(abs(difference)), f"Sync credit card balance")
            
            if "error" in result:
                return jsonify({"error": f"Failed to sync balance: {result['error']}"}), 500
        
        cache.clear()
        return jsonify({"success": True, "message": "Balance synced", "targetBalance": target_balance, "previousBalance": current_balance})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/lunchflow/change-account', methods=['POST'])
@login_required
def api_change_account():
    """Delete the credit card pocket, return money to safe-to-spend, and clear config"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Get current config - find any configured account with a pocket
        c.execute("SELECT account_id, pocket_id FROM credit_card_config WHERE pocket_id IS NOT NULL LIMIT 1")
        row = c.fetchone()
        
        if not row:
            # Check if there's any config at all (even without pocket)
            c.execute("SELECT account_id, pocket_id FROM credit_card_config LIMIT 1")
            row = c.fetchone()
            if not row:
                conn.close()
                return jsonify({"error": "No credit card account configured"}), 400
            # Get account_id even if pocket_id is NULL
            account_id = row[0]
            pocket_id = row[1] if len(row) > 1 else None
        else:
            account_id, pocket_id = row[0], row[1]
        
        # Get current pocket balance and return it to Checking
        headers_crew = get_crew_headers()
        if headers_crew and pocket_id:
            try:
                query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                response_crew = requests.post(URL, headers=headers_crew, json={
                    "operationName": "GetSubaccount",
                    "variables": {"id": pocket_id},
                    "query": query_string
                })
                
                crew_data = response_crew.json()
                current_balance = 0
                try:
                    current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                except:
                    pass
                
                # Return money to Checking if there's a balance
                all_subs = get_subaccounts_list()
                if "error" not in all_subs:
                    checking_subaccount_id = None
                    for sub in all_subs.get("subaccounts", []):
                        if sub["name"] == "Checking":
                            checking_subaccount_id = sub["id"]
                            break
                    
                    if checking_subaccount_id and current_balance > 0.01:
                        move_money(pocket_id, checking_subaccount_id, str(current_balance), "Returning credit card pocket funds to Safe-to-Spend")
                
                # Delete the pocket
                delete_subaccount_action(pocket_id)
            except Exception as e:
                print(f"Warning: Error deleting pocket: {e}")
        
        # Delete ALL config rows for this account and transaction history (user will select a new account)
        # Delete all rows regardless of pocket_id status to ensure clean state
        c.execute("DELETE FROM credit_card_config WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM credit_card_transactions WHERE account_id = ?", (account_id,))
        conn.commit()
        conn.close()
        
        cache.clear()
        return jsonify({"success": True, "message": "Account changed. Pocket deleted and funds returned to Safe-to-Spend."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/lunchflow/stop-tracking', methods=['POST'])
@login_required
def api_stop_tracking():
    """Delete the credit card pocket, return money to safe-to-spend, and delete all config"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # Get current config
        c.execute("SELECT account_id, pocket_id FROM credit_card_config WHERE pocket_id IS NOT NULL LIMIT 1")
        row = c.fetchone()
        
        if not row:
            conn.close()
            return jsonify({"error": "No credit card account configured"}), 400
        
        account_id, pocket_id = row[0], row[1]
        
        # Get current pocket balance and return it to Checking
        headers_crew = get_crew_headers()
        if headers_crew and pocket_id:
            try:
                query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                response_crew = requests.post(URL, headers=headers_crew, json={
                    "operationName": "GetSubaccount",
                    "variables": {"id": pocket_id},
                    "query": query_string
                })
                
                crew_data = response_crew.json()
                current_balance = 0
                try:
                    current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                except:
                    pass
                
                # Return money to Checking if there's a balance
                all_subs = get_subaccounts_list()
                if "error" not in all_subs:
                    checking_subaccount_id = None
                    for sub in all_subs.get("subaccounts", []):
                        if sub["name"] == "Checking":
                            checking_subaccount_id = sub["id"]
                            break
                    
                    if checking_subaccount_id and current_balance > 0.01:
                        move_money(pocket_id, checking_subaccount_id, str(current_balance), "Returning credit card pocket funds to Safe-to-Spend")
                
                # Delete the pocket
                delete_subaccount_action(pocket_id)
            except Exception as e:
                print(f"Warning: Error deleting pocket: {e}")
        
        # Delete all credit card config and transactions
        c.execute("DELETE FROM credit_card_config WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM credit_card_transactions WHERE account_id = ?", (account_id,))
        conn.commit()
        conn.close()
        
        cache.clear()
        return jsonify({"success": True, "message": "Tracking stopped. Pocket deleted and funds returned to Safe-to-Spend."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- SPLITWISE EXPENSE SYNCING ---
# --- CREDIT CARD TRANSACTION SYNCING ---
def check_credit_card_transactions():
    """Check for new credit card transactions and update balance (supports both LunchFlow and SimpleFin)"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get ALL credit card account configs with provider info (no LIMIT 1)
        c.execute("SELECT account_id, pocket_id, provider FROM credit_card_config WHERE pocket_id IS NOT NULL")
        rows = c.fetchall()

        if not rows:
            # Debug: Check if there are any configs without pockets
            c.execute("SELECT account_id, provider FROM credit_card_config")
            all_configs = c.fetchall()
            if all_configs:
                print(f"⚠️ Found credit card configs but none have pocket_id set: {all_configs}")
            conn.close()
            return

        # Get SimpleFin access URL and last sync time
        c.execute("SELECT access_url, last_sync FROM simplefin_config LIMIT 1")
        url_row = c.fetchone()
        simplefin_access_url = url_row[0] if url_row and url_row[0] else None
        db_last_sync = url_row[1] if url_row and len(url_row) > 1 else None

        # Initialize in-memory rate limiter from database if not already set
        global _last_simplefin_sync
        if db_last_sync and not _last_simplefin_sync:
            # Parse ISO timestamp and convert to Unix timestamp
            from datetime import datetime
            try:
                last_sync_dt = datetime.fromisoformat(db_last_sync.replace('Z', '+00:00'))
                last_sync_timestamp = last_sync_dt.timestamp()
                # Pre-populate for all SimpleFin accounts with the global last sync
                for row in rows:
                    if row[2] == 'simplefin':  # provider
                        _last_simplefin_sync[row[0]] = last_sync_timestamp
                print(f"📊 Initialized SimpleFin rate limiter from database: last sync was {db_last_sync}", flush=True)
            except Exception as e:
                print(f"⚠️ Failed to parse last_sync from database: {e}", flush=True)

        # Determine which SimpleFin accounts are due for sync
        simplefin_to_sync = []
        if simplefin_access_url:
            for row in rows:
                if row[2] == 'simplefin':
                    should_sync, reason = should_sync_simplefin(row[0])
                    if should_sync:
                        simplefin_to_sync.append((row[0], row[1], reason))
                    else:
                        print(f"⏰ SimpleFin sync skipped for account {row[0]} ({reason})", flush=True)
        else:
            for row in rows:
                if row[2] == 'simplefin':
                    print(f"⚠️ SimpleFin access URL not found in simplefin_config", flush=True)
                    break

        # Batch fetch SimpleFin data for all due accounts in a single request
        simplefin_data = None
        if simplefin_to_sync:
            from datetime import datetime, timedelta
            tz = get_configured_timezone()
            now_local = datetime.now(tz) if tz else datetime.now()
            start_date = now_local - timedelta(days=30)
            start_timestamp = int(start_date.timestamp())
            end_timestamp = int(now_local.timestamp())
            params = [
                ('start-date', start_timestamp),
                ('end-date', end_timestamp),
                ('pending', 1),
            ]
            for acc_id, _, _ in simplefin_to_sync:
                params.append(('account', acc_id))
            print(f"📡 Batch fetching SimpleFin data for {len(simplefin_to_sync)} account(s) in one request", flush=True)
            response = requests.get(f"{simplefin_access_url}/accounts", params=params, timeout=60)
            if response.status_code == 200:
                simplefin_data = response.json()
                print(f"✅ SimpleFin batch fetch returned {len(simplefin_data.get('accounts', []))} accounts", flush=True)
            else:
                print(f"❌ SimpleFin API error: {response.status_code} - {response.text}", flush=True)
                if response.status_code == 403:
                    print("🚫 SimpleFin token has been revoked or is invalid", flush=True)
                    c.execute("UPDATE simplefin_config SET is_valid = 0")
                    conn.commit()

        # Process each account
        for row in rows:
            account_id, pocket_id, provider = row
            print(f"🔍 Checking transactions for {provider} account {account_id}, pocket {pocket_id}", flush=True)

            # Handle based on provider
            if provider == 'lunchflow':
                api_key = get_lunchflow_api_key()
                if not api_key:
                    print("⚠️ LUNCHFLOW_API_KEY not set")
                    continue
                check_lunchflow_transactions(conn, c, account_id, pocket_id, api_key)

            elif provider == 'simplefin':
                sf_entry = next((a for a in simplefin_to_sync if a[0] == account_id), None)
                if not sf_entry or simplefin_data is None:
                    continue  # Not due for sync, or batch fetch failed

                _, _, reason = sf_entry
                print(f"✅ Processing SimpleFin account {account_id} from batch data ({reason})", flush=True)
                check_simplefin_transactions(conn, c, account_id, pocket_id, simplefin_access_url, prefetched_data=simplefin_data)

                # Update per-account last sync time
                _last_simplefin_sync[account_id] = time.time()

        # Update global last sync timestamp if any SimpleFin accounts were synced
        if simplefin_to_sync and simplefin_data is not None:
            from datetime import datetime
            last_sync_iso = datetime.utcnow().isoformat() + 'Z'
            c.execute("UPDATE simplefin_config SET last_sync = ?", (last_sync_iso,))
            conn.commit()

        # Send notification if new transactions were found
        # Count total new transactions (those created in the last minute)
        from datetime import datetime, timedelta
        one_minute_ago = (datetime.now() - timedelta(minutes=1)).isoformat()
        c.execute("""
            SELECT COUNT(*), GROUP_CONCAT(DISTINCT account_id)
            FROM credit_card_transactions
            WHERE created_at >= ?
        """, (one_minute_ago,))
        count_row = c.fetchone()

        if count_row and count_row[0] and count_row[0] > 0:
            transaction_count = count_row[0]
            account_ids_str = count_row[1]

            # Get account names for the notification
            account_ids = account_ids_str.split(',') if account_ids_str else []
            account_names = []
            for acc_id in account_ids:
                c.execute("SELECT account_name FROM credit_card_config WHERE account_id = ?", (acc_id,))
                name_row = c.fetchone()
                if name_row and name_row[0]:
                    account_names.append(name_row[0])

            # Get user ID (single-tenant app)
            c.execute("SELECT id FROM users LIMIT 1")
            user_row = c.fetchone()
            if user_row and account_names:
                send_sync_complete_notification(
                    user_row[0],
                    transaction_count,
                    account_names
                )

        conn.close()
    except Exception as e:
        print(f"❌ Error checking credit card transactions: {e}", flush=True)
        import traceback
        traceback.print_exc()

def check_lunchflow_transactions(conn, c, account_id, pocket_id, api_key):
    """Check LunchFlow for new transactions"""
    try:
        # Get when credit card was added
        c.execute("SELECT created_at FROM credit_card_config WHERE account_id = ?", (account_id,))
        config_row = c.fetchone()
        added_date = config_row[0] if config_row else None

        # Fetch transactions from LunchFlow
        headers = {"x-api-key": api_key, "accept": "application/json"}
        try:
            response = requests.get(f"https://www.lunchflow.app/api/v1/accounts/{account_id}/transactions", headers=headers, timeout=30)
        except:
            response = requests.get(f"https://lunchflow.com/api/v1/accounts/{account_id}/transactions", headers=headers, timeout=30)

        if response.status_code != 200:
            return

        data = response.json()
        transactions = data.get("transactions", [])

        # Get list of already seen transaction IDs
        c.execute("SELECT transaction_id FROM credit_card_transactions WHERE account_id = ?", (account_id,))
        seen_ids = {row[0] for row in c.fetchall()}

        new_transactions = []
        for tx in transactions:
            tx_id = tx.get("id")
            if not tx_id or tx_id in seen_ids:
                continue

            amount = tx.get("amount", 0)
            c.execute("""INSERT OR IGNORE INTO credit_card_transactions
                         (transaction_id, account_id, amount, date, merchant, description, is_pending)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (tx_id, account_id, amount, tx.get("date"), tx.get("merchant"),
                      tx.get("description"), 1 if tx.get("isPending") else 0))

            if c.rowcount > 0:
                new_transactions.append(tx)

        conn.commit()

        # Update pocket balance
        if pocket_id:
            balance_headers = {"x-api-key": api_key, "accept": "application/json"}
            balance_response = requests.get(f"https://www.lunchflow.app/api/v1/accounts/{account_id}/balance", headers=balance_headers, timeout=30)
            if balance_response.status_code == 200:
                balance_data = balance_response.json()
                balance_amount = balance_data.get("balance", {}).get("amount", 0)
                target_balance = abs(balance_amount)

                # Save current balance to database
                c.execute("UPDATE credit_card_config SET current_balance = ? WHERE account_id = ?", (target_balance, account_id))
                conn.commit()

                headers_crew = get_crew_headers()
                if headers_crew:
                    query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                    response_crew = requests.post(URL, headers=headers_crew, json={
                        "operationName": "GetSubaccount",
                        "variables": {"id": pocket_id},
                        "query": query_string
                    })

                    crew_data = response_crew.json()
                    current_balance = 0
                    try:
                        current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                    except:
                        pass

                    difference = target_balance - current_balance
                    all_subs = get_subaccounts_list()
                    if "error" not in all_subs:
                        checking_subaccount_id = None
                        for sub in all_subs.get("subaccounts", []):
                            if sub["name"] == "Checking":
                                checking_subaccount_id = sub["id"]
                                break

                        if checking_subaccount_id and abs(difference) > 0.01:
                            if difference > 0:
                                move_money(checking_subaccount_id, pocket_id, str(difference), f"LunchFlow credit card sync")
                            else:
                                move_money(pocket_id, checking_subaccount_id, str(abs(difference)), f"LunchFlow credit card sync")
                        cache.clear()

        if new_transactions:
            print(f"✅ Found {len(new_transactions)} new LunchFlow credit card transactions")
        else:
            print(f"🔄 LunchFlow credit card balance checked (no new transactions)")

    except Exception as e:
        print(f"Error checking LunchFlow transactions: {e}")

def check_simplefin_transactions(conn, c, account_id, pocket_id, access_url, is_initial_sync=False, prefetched_data=None):
    """Check SimpleFin for new transactions

    Args:
        is_initial_sync: If True, don't move money for transactions (just store them)
        prefetched_data: Pre-fetched API response to avoid duplicate calls when syncing multiple accounts
    """
    try:
        if prefetched_data is not None:
            data = prefetched_data
            print(f"🔍 check_simplefin_transactions: Using prefetched data for account {account_id} (initial={is_initial_sync})", flush=True)
        else:
            print(f"🔍 check_simplefin_transactions: Fetching from {access_url[:30]}... for account {account_id} (initial={is_initial_sync})", flush=True)

            # Calculate date range: last 30 days (using configured timezone)
            from datetime import datetime, timedelta
            tz = get_configured_timezone()
            now_local = datetime.now(tz) if tz else datetime.now()
            start_date = now_local - timedelta(days=30)
            start_timestamp = int(start_date.timestamp())
            end_timestamp = int(now_local.timestamp())

            # Fetch account data from SimpleFin, filtered to just this account
            params = {
                'start-date': start_timestamp,
                'end-date': end_timestamp,
                'pending': 1,  # Include pending transactions
                'account': account_id  # Filter to just this account
            }
            print(f"📅 Fetching transactions from {start_timestamp} to {end_timestamp}", flush=True)
            response = requests.get(f"{access_url}/accounts", params=params, timeout=60)
            if response.status_code != 200:
                print(f"❌ SimpleFin API error: {response.status_code} - {response.text}", flush=True)

                # If 403, mark token as invalid in database
                if response.status_code == 403:
                    print("🚫 SimpleFin token has been revoked or is invalid", flush=True)
                    c.execute("UPDATE simplefin_config SET is_valid = 0")
                    conn.commit()

                return

            data = response.json()

        print(f"✅ SimpleFin API response received, found {len(data.get('accounts', []))} accounts")

        # Find the matching account and get transactions
        target_account = None
        transactions = []
        for account in data.get("accounts", []):
            acc_id = account.get("id")
            print(f"  - Account: {acc_id} ({account.get('name', 'Unknown')})")
            if acc_id == account_id:
                target_account = account
                transactions = account.get("transactions", [])
                print(f"  ✅ MATCH! This is our tracked account")
                break

        if not target_account:
            print(f"❌ SimpleFin account {account_id} not found in response")
            all_account_ids = [acc.get("id") for acc in data.get("accounts", [])]
            print(f"   Available account IDs: {all_account_ids}")
            return

        print(f"✅ SimpleFin: Found {len(transactions)} total transactions for account {account_id}")

        # Get list of already seen transaction IDs with their pending status and amount
        c.execute("SELECT transaction_id, is_pending, amount FROM credit_card_transactions WHERE account_id = ?", (account_id,))
        existing_txs = {row[0]: {'is_pending': row[1], 'amount': row[2]} for row in c.fetchall()}
        print(f"  Already have {len(existing_txs)} transactions in database")

        new_transactions = []
        payment_transactions = []  # Track payments to move money back from pocket
        amount_adjustments = []  # Track amount changes that need pocket adjustment
        for tx in transactions:
            tx_id = tx.get("id")
            if not tx_id:
                print(f"  ⚠️ Skipping transaction with no ID: {tx}")
                continue

            # SimpleFin amounts may be strings, convert to float
            amount_str = tx.get("amount", "0")
            try:
                amount_float = float(amount_str)
                is_payment = amount_float > 0  # Positive = payment/credit, negative = purchase/debit
                amount = abs(amount_float)  # Store absolute value
            except (ValueError, TypeError):
                print(f"  ⚠️ Could not parse transaction amount '{amount_str}', using 0")
                amount = 0
                is_payment = False

            description = tx.get("description", "")
            posted = tx.get("posted")  # Unix timestamp
            transacted = tx.get("transacted")  # Unix timestamp when transaction occurred
            pending = not posted  # If no posted date, it's pending

            # Convert Unix timestamp to ISO date string if available
            date_str = None
            if posted:
                try:
                    from datetime import datetime
                    date_str = datetime.fromtimestamp(int(posted)).isoformat()
                except:
                    date_str = str(posted)
            elif transacted:
                try:
                    from datetime import datetime
                    date_str = datetime.fromtimestamp(int(transacted)).isoformat()
                except:
                    date_str = str(transacted)

            # Check if this transaction already exists
            if tx_id in existing_txs:
                existing_data = existing_txs[tx_id]
                was_pending = existing_data['is_pending']
                old_amount = existing_data['amount']

                # Check if amount has changed (e.g., tip added at restaurant)
                amount_changed = abs(amount - old_amount) > 0.01  # Use small epsilon for float comparison

                if was_pending and not pending:
                    # Transaction has posted! Update it with final date and clear pending flag
                    if amount_changed:
                        print(f"  📌 Transaction posted with amount change: ${old_amount:.2f} → ${amount:.2f} - {description} (ID: {tx_id})")
                        amount_diff = amount - old_amount
                        amount_adjustments.append({'amount': amount_diff, 'description': description})
                    else:
                        print(f"  📌 Transaction posted: ${amount} - {description} (ID: {tx_id})")

                    c.execute("""UPDATE credit_card_transactions
                                SET is_pending = 0, date = ?, amount = ?
                                WHERE transaction_id = ? AND account_id = ?""",
                            (date_str, amount, tx_id, account_id))
                    if c.rowcount > 0:
                        print(f"  ✅ Updated transaction {tx_id} to posted status")
                elif amount_changed:
                    # Amount changed but still pending (less common, but possible)
                    print(f"  💰 Pending transaction amount changed: ${old_amount:.2f} → ${amount:.2f} - {description} (ID: {tx_id})")
                    amount_diff = amount - old_amount
                    amount_adjustments.append({'amount': amount_diff, 'description': description})

                    c.execute("""UPDATE credit_card_transactions
                                SET amount = ?
                                WHERE transaction_id = ? AND account_id = ?""",
                            (amount, tx_id, account_id))
                    if c.rowcount > 0:
                        print(f"  ✅ Updated transaction {tx_id} amount")

                # Skip this transaction - it's already been processed
                continue

            # New transaction - insert it
            if is_payment:
                print(f"  💳 Payment received: ${amount} - {description} (ID: {tx_id}, pending={pending})")
                payment_transactions.append(tx)
            else:
                print(f"  💳 New transaction: ${amount} - {description} (ID: {tx_id}, pending={pending})")
                new_transactions.append(tx)

            c.execute("""INSERT INTO credit_card_transactions
                         (transaction_id, account_id, amount, date, merchant, description, is_pending)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (tx_id, account_id, amount, date_str, "", description, 1 if pending else 0))

            if c.rowcount > 0:
                print(f"  ✅ Inserted transaction {tx_id}")
            else:
                print(f"  ⚠️ Transaction {tx_id} was not inserted (error)")

        conn.commit()
        print(f"✅ Committed {len(new_transactions)} new transactions to database")

        # Move money from Checking to Credit Card pocket for each new transaction
        # Skip automatic money movement on initial sync to avoid huge transfers for historical transactions
        if new_transactions and pocket_id and not is_initial_sync:
            headers_crew = get_crew_headers()
            if headers_crew:
                # Get Checking subaccount ID
                all_subs = get_subaccounts_list()
                checking_subaccount_id = None
                if "error" not in all_subs:
                    for sub in all_subs.get("subaccounts", []):
                        if sub["name"] == "Checking":
                            checking_subaccount_id = sub["id"]
                            break

                if checking_subaccount_id:
                    # Check batch_mode setting for this account
                    c.execute("SELECT batch_mode FROM credit_card_config WHERE account_id = ? AND provider = 'simplefin'", (account_id,))
                    batch_row = c.fetchone()
                    batch_mode = batch_row[0] if batch_row and batch_row[0] is not None else 1  # Default to batch mode

                    if batch_mode == 0:
                        # Individual transfers mode: one transfer per transaction with merchant name as memo
                        print(f"💸 Creating {len(new_transactions)} individual transfer(s) from Checking to Credit Card pocket", flush=True)
                        for tx in new_transactions:
                            tx_amount = abs(float(tx.get("amount", 0)))
                            if tx_amount > 0.01:
                                # Use description as merchant name (SimpleFin stores merchant info in description)
                                merchant_name = tx.get("description", "").strip() or "Credit Card Transaction"
                                print(f"  💳 Moving ${tx_amount:.2f} - {merchant_name}", flush=True)
                                move_money(checking_subaccount_id, pocket_id, str(tx_amount), merchant_name)
                        cache.clear()
                    else:
                        # Batch mode: sum all transactions into one transfer
                        total_new_spending = sum(abs(float(tx.get("amount", 0))) for tx in new_transactions)
                        if total_new_spending > 0.01:
                            print(f"💸 Moving ${total_new_spending:.2f} from Checking to Credit Card pocket for {len(new_transactions)} new transaction(s)", flush=True)
                            move_money(checking_subaccount_id, pocket_id, str(total_new_spending), f"SimpleFin: {len(new_transactions)} new transaction(s)")
                            cache.clear()
        elif new_transactions and is_initial_sync:
            print(f"⏭️ Skipping automatic money movement for initial sync ({len(new_transactions)} historical transactions stored)", flush=True)

        # Move money from Credit Card pocket back to Checking for payment transactions
        # Skip automatic money movement on initial sync to avoid huge transfers for historical transactions
        if payment_transactions and pocket_id and not is_initial_sync:
            headers_crew = get_crew_headers()
            if headers_crew:
                # Get Checking subaccount ID
                all_subs = get_subaccounts_list()
                checking_subaccount_id = None
                if "error" not in all_subs:
                    for sub in all_subs.get("subaccounts", []):
                        if sub["name"] == "Checking":
                            checking_subaccount_id = sub["id"]
                            break

                if checking_subaccount_id:
                    # Check batch_mode setting for this account
                    c.execute("SELECT batch_mode FROM credit_card_config WHERE account_id = ? AND provider = 'simplefin'", (account_id,))
                    batch_row = c.fetchone()
                    batch_mode = batch_row[0] if batch_row and batch_row[0] is not None else 1  # Default to batch mode

                    if batch_mode == 0:
                        # Individual transfers mode: one transfer per payment with description as memo
                        print(f"💸 Creating {len(payment_transactions)} individual payment transfer(s) from Credit Card pocket to Checking", flush=True)
                        for tx in payment_transactions:
                            tx_amount = abs(float(tx.get("amount", 0)))
                            if tx_amount > 0.01:
                                # Use description as payment reference (SimpleFin stores payment info in description)
                                payment_ref = tx.get("description", "").strip() or "Credit Card Payment"
                                print(f"  💳 Moving ${tx_amount:.2f} - {payment_ref}", flush=True)
                                move_money(pocket_id, checking_subaccount_id, str(tx_amount), payment_ref)
                        cache.clear()
                    else:
                        # Batch mode: sum all payments into one transfer
                        total_payments = sum(abs(float(tx.get("amount", 0))) for tx in payment_transactions)
                        if total_payments > 0.01:
                            print(f"💸 Moving ${total_payments:.2f} from Credit Card pocket to Checking for {len(payment_transactions)} payment(s)", flush=True)
                            move_money(pocket_id, checking_subaccount_id, str(total_payments), f"SimpleFin: {len(payment_transactions)} payment(s)")
                            cache.clear()
        elif payment_transactions and is_initial_sync:
            print(f"⏭️ Skipping automatic payment transfers for initial sync ({len(payment_transactions)} historical payments stored)", flush=True)

        # Handle amount adjustments (e.g., tips added at restaurants)
        # Move additional money when transaction amounts increase
        if amount_adjustments and pocket_id and not is_initial_sync:
            headers_crew = get_crew_headers()
            if headers_crew:
                # Get Checking subaccount ID
                all_subs = get_subaccounts_list()
                checking_subaccount_id = None
                if "error" not in all_subs:
                    for sub in all_subs.get("subaccounts", []):
                        if sub["name"] == "Checking":
                            checking_subaccount_id = sub["id"]
                            break

                if checking_subaccount_id:
                    # Sum all amount adjustments (positive = need more money, negative = return money)
                    total_adjustment = sum(adj['amount'] for adj in amount_adjustments)

                    if abs(total_adjustment) > 0.01:
                        if total_adjustment > 0:
                            # Amount increased (e.g., tip added) - move more money to pocket
                            print(f"💰 Amount adjustment: Moving additional ${total_adjustment:.2f} from Checking to Credit Card pocket ({len(amount_adjustments)} transaction(s))", flush=True)
                            move_money(checking_subaccount_id, pocket_id, str(total_adjustment), f"Amount adjustment: {len(amount_adjustments)} transaction(s)")
                            cache.clear()
                        else:
                            # Amount decreased (rare, but possible) - return money to checking
                            print(f"💰 Amount adjustment: Returning ${abs(total_adjustment):.2f} from Credit Card pocket to Checking ({len(amount_adjustments)} transaction(s))", flush=True)
                            move_money(pocket_id, checking_subaccount_id, str(abs(total_adjustment)), f"Amount adjustment: {len(amount_adjustments)} transaction(s)")
                            cache.clear()

        # Update pocket balance to match SimpleFin balance
        # Always save the balance to database, even during initial sync
        if pocket_id:
            # SimpleFin returns balance as a string, convert to float
            balance_str = target_account.get("balance", "0")
            try:
                target_balance = abs(float(balance_str))
            except (ValueError, TypeError):
                print(f"Warning: Could not parse balance '{balance_str}', using 0")
                target_balance = 0

            # Save current balance to database (always, even for initial sync)
            c.execute("UPDATE credit_card_config SET current_balance = ? WHERE account_id = ? AND provider = 'simplefin'", (target_balance, account_id))
            conn.commit()

            # Skip automatic pocket syncing on initial sync to avoid huge transfers
            if is_initial_sync:
                print(f"📊 Saved balance ${target_balance} to database (skipping pocket sync for initial sync)", flush=True)
            else:
                # Only sync pocket balance during regular syncs (not initial sync)
                headers_crew = get_crew_headers()
                if headers_crew:
                    query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                    response_crew = requests.post(URL, headers=headers_crew, json={
                        "operationName": "GetSubaccount",
                        "variables": {"id": pocket_id},
                        "query": query_string
                    })

                    crew_data = response_crew.json()
                    current_balance = 0
                    try:
                        current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                    except:
                        pass

                    difference = target_balance - current_balance
                    all_subs = get_subaccounts_list()
                    if "error" not in all_subs:
                        checking_subaccount_id = None
                        for sub in all_subs.get("subaccounts", []):
                            if sub["name"] == "Checking":
                                checking_subaccount_id = sub["id"]
                                break

                        if checking_subaccount_id and abs(difference) > 0.01:
                            if difference > 0:
                                move_money(checking_subaccount_id, pocket_id, str(difference), f"SimpleFin credit card sync")
                            else:
                                move_money(pocket_id, checking_subaccount_id, str(abs(difference)), f"SimpleFin credit card sync")
                        cache.clear()

        if new_transactions:
            print(f"✅ Found {len(new_transactions)} new SimpleFin credit card transactions")
        else:
            print(f"🔄 SimpleFin credit card balance checked (no new transactions)")

    except Exception as e:
        print(f"❌ Error checking SimpleFin transactions: {e}")
        import traceback
        traceback.print_exc()

@app.route('/api/lunchflow/last-check-time')
@login_required
def api_last_check_time():
    """Get the last time credit card transactions were checked"""
    try:
        # Store last check time in a simple way - we'll use a file or just return current time minus some offset
        # For now, return a timestamp that represents "30 seconds ago" so countdown starts at 30
        import time as time_module
        return jsonify({
            "lastCheckTime": time_module.time(),
            "checkInterval": 30  # seconds
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def background_transaction_checker():
    """Background thread that checks for new transactions every 30 seconds"""
    while True:
        try:
            check_credit_card_transactions()
        except Exception as e:
            print(f"Error in background transaction checker: {e}")
        time.sleep(30)  # Check every 30 seconds

def start_background_thread_once():
    """Start the background thread exactly once (thread-safe)"""
    global _background_thread_started
    with _background_thread_lock:
        if not _background_thread_started:
            transaction_thread = threading.Thread(target=background_transaction_checker, daemon=True)
            transaction_thread.start()
            print("🔄 Credit card transaction checker started (checks every 30 seconds)", flush=True)
            _background_thread_started = True

@app.before_request
def ensure_background_thread():
    """Ensure background thread is started before handling requests"""
    start_background_thread_once()

@app.route('/api/lunchflow/transactions')
@login_required
def api_get_credit_card_transactions():
    """Get credit card transactions that have been synced (optionally filtered by accountId)"""
    try:
        account_id = request.args.get('accountId')  # Optional filter

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        if account_id:
            # Filter by specific account
            c.execute("""SELECT transaction_id, amount, date, merchant, description, is_pending, created_at
                         FROM credit_card_transactions
                         WHERE account_id = ?
                         ORDER BY date DESC, created_at DESC
                         LIMIT 100""", (account_id,))
        else:
            # Return all accounts
            c.execute("""SELECT transaction_id, amount, date, merchant, description, is_pending, created_at
                         FROM credit_card_transactions
                         ORDER BY date DESC, created_at DESC
                         LIMIT 100""")

        rows = c.fetchall()
        conn.close()

        transactions = []
        for row in rows:
            transactions.append({
                "id": row[0],
                "amount": row[1],
                "date": row[2],
                "merchant": row[3],
                "description": row[4],
                "isPending": bool(row[5]),
                "syncedAt": row[6],
                "isCreditCard": True  # Flag to identify credit card transactions
            })

        return jsonify({"transactions": transactions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- SIMPLEFIN API ENDPOINTS ---
import base64
from urllib.parse import urlparse

def store_simplefin_access_url(access_url):
    """Store or update the SimpleFin access URL in the global config table"""
    try:
        print(f"🔍 store_simplefin_access_url called with access_url: {access_url[:50] if access_url else 'None'}...", flush=True)

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Check if we already have an access URL
        c.execute("SELECT id FROM simplefin_config LIMIT 1")
        existing = c.fetchone()

        if existing:
            # Update existing access URL and mark as valid
            print("Updating existing SimpleFin access URL", flush=True)
            c.execute("UPDATE simplefin_config SET access_url = ?, is_valid = 1 WHERE id = ?", (access_url, existing[0]))
        else:
            # Insert new access URL (is_valid defaults to 1)
            print("Storing new SimpleFin access URL", flush=True)
            c.execute("INSERT INTO simplefin_config (access_url, is_valid) VALUES (?, 1)", (access_url,))

        conn.commit()
        rows_affected = c.rowcount
        conn.close()

        print(f"✅ SimpleFin access URL stored successfully ({rows_affected} rows affected)", flush=True)
        cache.clear()
        return True
    except Exception as e:
        print(f"❌ ERROR storing SimpleFin access URL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False

def simplefin_claim_token(token):
    """Claim a SimpleFin token and return the access URL"""
    try:
        # Decode the Base64 token to get the claim URL
        claim_url = base64.b64decode(token).decode('utf-8')

        # POST to the claim endpoint
        response = requests.post(claim_url, timeout=30)

        if response.status_code == 403:
            return {"error": "Token has been compromised or already claimed"}

        if response.status_code != 200:
            return {"error": f"SimpleFin claim error: {response.status_code} - {response.text}"}

        # The response body is the access URL with embedded credentials
        access_url = response.text.strip()

        return {"success": True, "accessUrl": access_url}
    except base64.binascii.Error:
        return {"error": "Invalid token format. Token must be Base64-encoded."}
    except Exception as e:
        return {"error": f"Failed to claim token: {str(e)}"}

def simplefin_get_accounts(access_url, account_id=None):
    """Fetch accounts from SimpleFin using the access URL"""
    try:
        # balances-only avoids pulling transaction data we don't need here
        # account filter limits the response to a single account when provided
        params = {'balances-only': 1}
        if account_id:
            params['account'] = account_id

        response = requests.get(f"{access_url}/accounts", params=params, timeout=30)

        if response.status_code != 200:
            # If 403, mark token as invalid
            if response.status_code == 403:
                print("🚫 SimpleFin token has been revoked or is invalid (get_accounts)", flush=True)
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                c.execute("UPDATE simplefin_config SET is_valid = 0")
                conn.commit()
                conn.close()

            return {"error": f"SimpleFin API error: {response.status_code} - {response.text}"}

        data = response.json()

        # Transform SimpleFin format to match our expected format
        accounts = []
        for account in data.get("accounts", []):
            # SimpleFin returns balance as a string, convert to float
            balance_str = account.get("balance", "0")
            try:
                balance_float = float(balance_str)
            except (ValueError, TypeError):
                balance_float = 0

            # Credit accounts have negative balance (amount owed)
            # Also check if balance_str starts with "-" to catch "-0" cases
            is_credit_account = balance_float < 0 or (balance_str and str(balance_str).strip().startswith("-"))

            accounts.append({
                "id": account.get("id"),
                "name": account.get("name", "Unknown Account"),
                "balance": balance_float,  # SimpleFin balance is in dollars
                "currency": account.get("currency", "USD"),
                "org": account.get("org", {}).get("name", "Unknown"),
                "type": account.get("type"),
                "subtype": account.get("subtype"),
                "is_credit_account": is_credit_account,  # Flag for credit accounts (negative balance)
            })

        return {"accounts": accounts}
    except Exception as e:
        return {"error": f"Failed to fetch accounts: {str(e)}"}

@app.route('/api/simplefin/get-access-url')
@login_required
def api_simplefin_get_access_url():
    """Get the stored SimpleFin access URL if it exists"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get SimpleFin access URL from global config
        c.execute("SELECT access_url FROM simplefin_config LIMIT 1")
        row = c.fetchone()
        conn.close()

        if row and row[0]:
            print(f"✅ SimpleFin access URL found (url length: {len(row[0])})", flush=True)
            return jsonify({"success": True, "accessUrl": row[0]})
        else:
            print(f"⚠️ No SimpleFin access URL found in database", flush=True)
            return jsonify({"success": False, "accessUrl": None})
    except Exception as e:
        print(f"❌ ERROR fetching SimpleFin access URL: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/claim-token', methods=['POST'])
@login_required
def api_simplefin_claim_token():
    """Claim a SimpleFin token and store the access URL immediately"""
    data = request.json
    token = data.get('token')

    print(f"🔍 api_simplefin_claim_token called with token: {token[:20] if token else 'None'}...", flush=True)

    if not token:
        return jsonify({"error": "token is required"}), 400

    # Claim the token
    result = simplefin_claim_token(token)
    print(f"🔍 simplefin_claim_token result: {result}", flush=True)

    if "error" in result:
        return jsonify(result), 400

    access_url = result.get("accessUrl")
    print(f"🔍 access_url: {access_url[:50] if access_url else 'None'}...", flush=True)

    # Store the access URL immediately using the dedicated function
    stored = store_simplefin_access_url(access_url)
    print(f"🔍 store_simplefin_access_url returned: {stored}", flush=True)

    if not stored:
        return jsonify({"error": "Failed to store access URL in database"}), 500

    return jsonify({"success": True, "accessUrl": access_url})

@app.route('/api/simplefin/accounts', methods=['POST'])
@login_required
def api_simplefin_accounts():
    """List all accounts from SimpleFin using the access URL"""
    data = request.json
    access_url = data.get('accessUrl')

    if not access_url:
        return jsonify({"error": "accessUrl is required"}), 400

    result = simplefin_get_accounts(access_url)

    if "error" in result:
        return jsonify(result), 400

    return jsonify(result)

@app.route('/api/simplefin/set-credit-card', methods=['POST'])
@login_required
def api_simplefin_set_credit_card():
    """Store the selected SimpleFin credit card account"""
    data = request.json
    account_id = data.get('accountId')
    account_name = data.get('accountName', '')

    if not account_id:
        return jsonify({"error": "accountId is required"}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Insert or ignore the account selection (allows multiple accounts, access_url is stored globally in simplefin_config)
        c.execute("""INSERT OR IGNORE INTO credit_card_config
                     (account_id, account_name, provider, created_at)
                     VALUES (?, ?, 'simplefin', CURRENT_TIMESTAMP)""",
                  (account_id, account_name))

        conn.commit()
        conn.close()

        cache.clear()
        return jsonify({"success": True, "message": "SimpleFin credit card account saved", "needsBalanceSync": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/get-balance', methods=['POST'])
@login_required
def api_simplefin_get_balance():
    """Get the balance for a specific SimpleFin account"""
    data = request.json
    account_id = data.get('accountId')
    access_url = data.get('accessUrl')

    if not account_id or not access_url:
        return jsonify({"error": "accountId and accessUrl are required"}), 400

    try:
        # Fetch only this account's balance
        result = simplefin_get_accounts(access_url, account_id=account_id)

        if "error" in result:
            return jsonify(result), 400

        for account in result.get("accounts", []):
            if account["id"] == account_id:
                return jsonify({"balance": {"amount": account["balance"]}})

        return jsonify({"error": "Account not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/create-pocket-with-balance', methods=['POST'])
@login_required
def api_simplefin_create_pocket_with_balance():
    """Create the credit card pocket for SimpleFin and optionally sync balance"""
    data = request.json
    account_id = data.get('accountId')
    sync_balance = data.get('syncBalance', False)

    if not account_id:
        return jsonify({"error": "accountId is required"}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get account info
        c.execute("SELECT account_name FROM credit_card_config WHERE account_id = ? AND provider = 'simplefin'", (account_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "SimpleFin account not found. Please select an account first."}), 400

        account_name = row[0]

        # Get SimpleFin access URL from global config
        c.execute("SELECT access_url FROM simplefin_config LIMIT 1")
        url_row = c.fetchone()
        access_url = url_row[0] if url_row else None

        # Fetch balance and transactions from SimpleFin in a single request
        # This data is reused below for both pocket creation (balance) and initial transaction sync
        simplefin_data = None
        initial_amount = "0"
        current_balance_value = 0
        if access_url:
            try:
                from datetime import datetime, timedelta
                tz = get_configured_timezone()
                now_local = datetime.now(tz) if tz else datetime.now()
                start_date = now_local - timedelta(days=30)
                start_timestamp = int(start_date.timestamp())
                end_timestamp = int(now_local.timestamp())

                params = {
                    'start-date': start_timestamp,
                    'end-date': end_timestamp,
                    'pending': 1,
                    'account': account_id
                }
                response = requests.get(f"{access_url}/accounts", params=params, timeout=60)
                if response.status_code == 200:
                    simplefin_data = response.json()
                    for account in simplefin_data.get("accounts", []):
                        if account.get("id") == account_id:
                            balance_str = account.get("balance", "0")
                            try:
                                current_balance_value = abs(float(balance_str))
                            except (ValueError, TypeError):
                                current_balance_value = 0
                            if sync_balance:
                                initial_amount = str(current_balance_value)
                            break
                else:
                    print(f"Warning: SimpleFin API error {response.status_code}: {response.text}", flush=True)
            except Exception as e:
                print(f"Warning: Could not fetch SimpleFin data: {e}", flush=True)

        # Create the pocket
        pocket_name = f"Credit Card - {account_name}"
        pocket_result = create_pocket(pocket_name, "0", initial_amount, f"SimpleFin credit card tracking pocket for {account_name}")

        if "error" in pocket_result:
            conn.close()
            return jsonify({"error": f"Failed to create pocket: {pocket_result['error']}"}), 500

        pocket_id = pocket_result.get("result", {}).get("id")
        if not pocket_id:
            conn.close()
            return jsonify({"error": "Pocket was created but no ID was returned"}), 500

        # Update the config with pocket_id and current_balance
        c.execute("UPDATE credit_card_config SET pocket_id = ?, current_balance = ? WHERE account_id = ? AND provider = 'simplefin'",
                 (pocket_id, current_balance_value, account_id))
        conn.commit()

        # Process initial transactions using the data already fetched above — no second API call
        if simplefin_data:
            print(f"🔄 Processing initial transactions for newly added SimpleFin account {account_id} (balance synced: {sync_balance})", flush=True)
            global _last_simplefin_sync

            try:
                check_simplefin_transactions(conn, c, account_id, pocket_id, access_url, is_initial_sync=True, prefetched_data=simplefin_data)
                _last_simplefin_sync[account_id] = time.time()
                print(f"✅ Initial transaction sync complete for account {account_id}, hourly timer reset", flush=True)
            except Exception as e:
                print(f"⚠️ Error processing initial transactions: {e}", flush=True)
                import traceback
                traceback.print_exc()

        conn.close()

        cache.clear()
        return jsonify({"success": True, "message": "SimpleFin credit card pocket created", "pocketId": pocket_id, "syncedBalance": sync_balance})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/sync-balance', methods=['POST'])
@login_required
def api_simplefin_sync_balance():
    """Sync the pocket balance to match the SimpleFin credit card balance"""
    data = request.json
    account_id = data.get('accountId')

    if not account_id:
        return jsonify({"error": "accountId is required"}), 400

    try:
        # Get pocket_id from database
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT pocket_id FROM credit_card_config WHERE account_id = ? AND provider = 'simplefin'", (account_id,))
        row = c.fetchone()

        if not row or not row[0]:
            conn.close()
            return jsonify({"error": "No SimpleFin pocket found for this account"}), 400

        pocket_id = row[0]

        # Get SimpleFin access URL from global config
        c.execute("SELECT access_url FROM simplefin_config LIMIT 1")
        url_row = c.fetchone()
        conn.close()

        if not url_row or not url_row[0]:
            return jsonify({"error": "SimpleFin access URL not found"}), 400

        access_url = url_row[0]

        # Get balance from SimpleFin (filtered to this account only)
        balance_result = simplefin_get_accounts(access_url, account_id=account_id)
        if "error" in balance_result:
            return jsonify(balance_result), 400

        target_balance = 0
        for account in balance_result.get("accounts", []):
            if account["id"] == account_id:
                target_balance = abs(account["balance"])
                break

        # Save current balance to database
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE credit_card_config SET current_balance = ? WHERE account_id = ? AND provider = 'simplefin'", (target_balance, account_id))
        conn.commit()
        conn.close()

        # Get current pocket balance
        headers_crew = get_crew_headers()
        if not headers_crew:
            return jsonify({"error": "Crew credentials not found"}), 400

        query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
        response_crew = requests.post(URL, headers=headers_crew, json={
            "operationName": "GetSubaccount",
            "variables": {"id": pocket_id},
            "query": query_string
        })

        crew_data = response_crew.json()
        current_balance = 0
        try:
            current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
        except:
            pass

        # Calculate difference
        difference = target_balance - current_balance

        # Get Checking subaccount ID
        all_subs = get_subaccounts_list()
        if "error" in all_subs:
            return jsonify({"error": "Could not get subaccounts list"}), 400

        checking_subaccount_id = None
        for sub in all_subs.get("subaccounts", []):
            if sub["name"] == "Checking":
                checking_subaccount_id = sub["id"]
                break

        if not checking_subaccount_id:
            return jsonify({"error": "Could not find Checking subaccount"}), 400

        # Transfer money to/from pocket
        if abs(difference) > 0:  # Only transfer if difference is significant
            if difference > 0:
                result = move_money(checking_subaccount_id, pocket_id, str(difference), f"SimpleFin sync credit card balance")
            else:
                result = move_money(pocket_id, checking_subaccount_id, str(abs(difference)), f"SimpleFin sync credit card balance")

            if "error" in result:
                return jsonify({"error": f"Failed to sync balance: {result['error']}"}), 500

        cache.clear()
        return jsonify({"success": True, "message": "Balance synced", "targetBalance": target_balance, "previousBalance": current_balance})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/get-batch-mode', methods=['POST'])
@login_required
def api_simplefin_get_batch_mode():
    """Get the batch mode setting for a SimpleFin account"""
    try:
        data = request.get_json()
        account_id = data.get("account_id")

        if not account_id:
            return jsonify({"error": "account_id is required"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        c.execute("SELECT batch_mode FROM credit_card_config WHERE account_id = ? AND provider = 'simplefin'", (account_id,))
        row = c.fetchone()
        conn.close()

        if not row:
            return jsonify({"error": "Account not found"}), 404

        # Default to 1 (batch mode) if NULL
        batch_mode = row[0] if row[0] is not None else 1
        return jsonify({"batch_mode": batch_mode})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/set-batch-mode', methods=['POST'])
@login_required
def api_simplefin_set_batch_mode():
    """Set the batch mode setting for a SimpleFin account"""
    try:
        data = request.get_json()
        account_id = data.get("account_id")
        batch_mode = data.get("batch_mode")

        if not account_id:
            return jsonify({"error": "account_id is required"}), 400

        if batch_mode is None:
            return jsonify({"error": "batch_mode is required (0 or 1)"}), 400

        # Validate batch_mode is 0 or 1
        batch_mode = int(batch_mode)
        if batch_mode not in (0, 1):
            return jsonify({"error": "batch_mode must be 0 or 1"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        c.execute("UPDATE credit_card_config SET batch_mode = ? WHERE account_id = ? AND provider = 'simplefin'", (batch_mode, account_id))
        conn.commit()

        if c.rowcount == 0:
            conn.close()
            return jsonify({"error": "Account not found"}), 404

        conn.close()

        mode_name = "Batch" if batch_mode == 1 else "Individual"
        print(f"🔧 Updated batch mode for account {account_id} to: {mode_name}", flush=True)
        return jsonify({"success": True, "batch_mode": batch_mode, "message": f"Transfer mode set to {mode_name}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/change-account', methods=['POST'])
@login_required
def api_simplefin_change_account():
    """Delete the SimpleFin credit card pocket and clear config"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get current config
        c.execute("SELECT account_id, pocket_id FROM credit_card_config WHERE pocket_id IS NOT NULL AND provider = 'simplefin' LIMIT 1")
        row = c.fetchone()

        if not row:
            # Check if there's any config at all
            c.execute("SELECT account_id, pocket_id FROM credit_card_config WHERE provider = 'simplefin' LIMIT 1")
            row = c.fetchone()
            if not row:
                conn.close()
                return jsonify({"error": "No SimpleFin credit card account configured"}), 400
            account_id = row[0]
            pocket_id = row[1] if len(row) > 1 else None
        else:
            account_id, pocket_id = row[0], row[1]

        # Get current pocket balance and return it to Checking
        headers_crew = get_crew_headers()
        if headers_crew and pocket_id:
            try:
                query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                response_crew = requests.post(URL, headers=headers_crew, json={
                    "operationName": "GetSubaccount",
                    "variables": {"id": pocket_id},
                    "query": query_string
                })

                crew_data = response_crew.json()
                current_balance = 0
                try:
                    current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                except:
                    pass

                # Return money to Checking if there's a balance
                all_subs = get_subaccounts_list()
                if "error" not in all_subs:
                    checking_subaccount_id = None
                    for sub in all_subs.get("subaccounts", []):
                        if sub["name"] == "Checking":
                            checking_subaccount_id = sub["id"]
                            break

                    if checking_subaccount_id and current_balance > 0.01:
                        move_money(pocket_id, checking_subaccount_id, str(current_balance), "Returning SimpleFin credit card pocket funds to Safe-to-Spend")

                # Delete the pocket
                delete_subaccount_action(pocket_id)
            except Exception as e:
                print(f"Warning: Error deleting pocket: {e}")

        # Delete config and transactions for this specific account
        # Note: We keep the access_url in simplefin_config as it works for all accounts
        c.execute("DELETE FROM credit_card_config WHERE account_id = ? AND provider = 'simplefin'", (account_id,))
        c.execute("DELETE FROM credit_card_transactions WHERE account_id = ?", (account_id,))

        conn.commit()
        conn.close()

        cache.clear()
        return jsonify({"success": True, "message": "SimpleFin account changed. Pocket deleted and funds returned to Safe-to-Spend."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/stop-tracking', methods=['POST'])
@login_required
def api_simplefin_stop_tracking():
    """Delete the SimpleFin credit card pocket and all config"""
    try:
        data = request.json
        account_id = data.get('accountId') if data else None

        if not account_id:
            return jsonify({"error": "accountId is required"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get current config for the specific account
        c.execute("SELECT account_id, pocket_id FROM credit_card_config WHERE account_id = ? AND pocket_id IS NOT NULL AND provider = 'simplefin'", (account_id,))
        row = c.fetchone()

        if not row:
            conn.close()
            return jsonify({"error": "No SimpleFin credit card account configured with that ID"}), 400

        account_id, pocket_id = row[0], row[1]

        # Get current pocket balance and return it to Checking
        headers_crew = get_crew_headers()
        if headers_crew and pocket_id:
            try:
                query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                response_crew = requests.post(URL, headers=headers_crew, json={
                    "operationName": "GetSubaccount",
                    "variables": {"id": pocket_id},
                    "query": query_string
                })

                crew_data = response_crew.json()
                current_balance = 0
                try:
                    current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                except:
                    pass

                # Return money to Checking
                all_subs = get_subaccounts_list()
                if "error" not in all_subs:
                    checking_subaccount_id = None
                    for sub in all_subs.get("subaccounts", []):
                        if sub["name"] == "Checking":
                            checking_subaccount_id = sub["id"]
                            break

                    if checking_subaccount_id and current_balance > 0.01:
                        move_money(pocket_id, checking_subaccount_id, str(current_balance), "Returning SimpleFin credit card pocket funds to Safe-to-Spend")

                # Delete the pocket
                delete_subaccount_action(pocket_id)
            except Exception as e:
                print(f"Warning: Error deleting pocket: {e}")

        # Delete all config and transactions
        c.execute("DELETE FROM credit_card_config WHERE account_id = ? AND provider = 'simplefin'", (account_id,))
        c.execute("DELETE FROM credit_card_transactions WHERE account_id = ?", (account_id,))
        conn.commit()
        conn.close()

        cache.clear()
        return jsonify({"success": True, "message": "SimpleFin tracking stopped. Pocket deleted and funds returned to Safe-to-Spend."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/disconnect', methods=['POST'])
@login_required
def api_simplefin_disconnect():
    """Completely disconnect SimpleFin - removes access URL and all account tracking"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get all SimpleFin accounts with pockets
        c.execute("SELECT account_id, pocket_id FROM credit_card_config WHERE provider = 'simplefin' AND pocket_id IS NOT NULL")
        accounts = c.fetchall()

        # Return funds and delete pockets for all accounts
        headers_crew = get_crew_headers()
        if headers_crew:
            # Get checking account
            all_subs = get_subaccounts_list()
            checking_subaccount_id = None
            if "error" not in all_subs:
                for sub in all_subs.get("subaccounts", []):
                    if sub["name"] == "Checking":
                        checking_subaccount_id = sub["id"]
                        break

            # Process each account
            for account_id, pocket_id in accounts:
                try:
                    # Get pocket balance
                    query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                    response_crew = requests.post(URL, headers=headers_crew, json={
                        "operationName": "GetSubaccount",
                        "variables": {"id": pocket_id},
                        "query": query_string
                    })

                    crew_data = response_crew.json()
                    current_balance = 0
                    try:
                        current_balance = crew_data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                    except:
                        pass

                    # Return money to Checking
                    if checking_subaccount_id and current_balance > 0.01:
                        move_money(pocket_id, checking_subaccount_id, str(current_balance), f"Disconnecting SimpleFin - returning funds")

                    # Delete the pocket
                    delete_subaccount_action(pocket_id)
                except Exception as e:
                    print(f"Warning: Error deleting pocket for account {account_id}: {e}")

        # Delete all SimpleFin configs and transactions
        c.execute("DELETE FROM credit_card_config WHERE provider = 'simplefin'")
        c.execute("DELETE FROM credit_card_transactions WHERE account_id IN (SELECT account_id FROM credit_card_config WHERE provider = 'simplefin')")

        # Delete the SimpleFin access URL (complete disconnect)
        c.execute("DELETE FROM simplefin_config")

        conn.commit()
        conn.close()

        cache.clear()
        return jsonify({"success": True, "message": "SimpleFin completely disconnected. All pockets deleted and funds returned."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/sync-schedule', methods=['GET'])
@login_required
def api_get_simplefin_sync_schedule():
    """Get the current SimpleFin sync schedule setting"""
    import json
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT sync_times, sync_timezone FROM simplefin_config LIMIT 1")
        row = c.fetchone()
        conn.close()

        if row and row[0]:
            sync_times = json.loads(row[0])
            sync_timezone = row[1] if row[1] else "UTC"
            return jsonify({
                "success": True,
                "syncTimes": sync_times,
                "syncTimezone": sync_timezone
            })
        else:
            # Default to empty (will use interval fallback)
            return jsonify({
                "success": True,
                "syncTimes": None,
                "syncTimezone": None
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/sync-schedule', methods=['POST'])
@login_required
def api_set_simplefin_sync_schedule():
    """Update the SimpleFin sync schedule setting"""
    import json
    data = request.json
    sync_times = data.get('syncTimes')  # Array of times in UTC like ["14:00", "02:00"]
    sync_timezone = data.get('syncTimezone', 'UTC')

    if not sync_times or not isinstance(sync_times, list):
        return jsonify({"error": "syncTimes array is required"}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        c.execute("SELECT id FROM simplefin_config LIMIT 1")
        existing = c.fetchone()

        if existing:
            c.execute("UPDATE simplefin_config SET sync_times = ?, sync_timezone = ? WHERE id = ?",
                     (json.dumps(sync_times), sync_timezone, existing[0]))
        else:
            return jsonify({"error": "SimpleFin not configured"}), 400

        conn.commit()
        conn.close()

        cache.clear()

        return jsonify({
            "success": True,
            "syncTimes": sync_times,
            "syncTimezone": sync_timezone
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/timezone', methods=['GET'])
@login_required
def api_get_simplefin_timezone():
    """Get the configured timezone"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT sync_timezone FROM simplefin_config LIMIT 1")
        row = c.fetchone()
        conn.close()

        timezone = row[0] if row and row[0] else "America/Denver"
        return jsonify({"success": True, "timezone": timezone})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/timezone', methods=['POST'])
@login_required
def api_set_simplefin_timezone():
    """Set the timezone for date calculations"""
    try:
        data = request.get_json()
        timezone = data.get("timezone")

        if not timezone:
            return jsonify({"error": "timezone is required"}), 400

        # Validate timezone
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(timezone)
        except:
            return jsonify({"error": f"Invalid timezone: {timezone}"}), 400

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        c.execute("SELECT id FROM simplefin_config LIMIT 1")
        existing = c.fetchone()

        if existing:
            c.execute("UPDATE simplefin_config SET sync_timezone = ? WHERE id = ?", (timezone, existing[0]))
        else:
            c.execute("INSERT INTO simplefin_config (sync_timezone) VALUES (?)", (timezone,))

        conn.commit()
        conn.close()

        print(f"🌍 Updated timezone to: {timezone}", flush=True)
        return jsonify({"success": True, "timezone": timezone})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/simplefin/sync-now', methods=['POST'])
@login_required
def api_simplefin_sync_now():
    """Manually trigger SimpleFin sync for all accounts"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get SimpleFin access URL
        c.execute("SELECT access_url FROM simplefin_config LIMIT 1")
        url_row = c.fetchone()
        access_url = url_row[0] if url_row and url_row[0] else None

        if not access_url:
            conn.close()
            return jsonify({"error": "SimpleFin not configured"}), 400

        # Get all SimpleFin accounts
        c.execute("SELECT account_id, pocket_id FROM credit_card_config WHERE provider = 'simplefin'")
        accounts = c.fetchall()

        if not accounts:
            conn.close()
            return jsonify({"error": "No SimpleFin accounts configured"}), 400

        # Batch fetch all accounts in one SimpleFin request
        global _last_simplefin_sync
        synced_count = 0

        from datetime import datetime, timedelta
        tz = get_configured_timezone()
        now_local = datetime.now(tz) if tz else datetime.now()
        start_date = now_local - timedelta(days=30)
        start_timestamp = int(start_date.timestamp())
        end_timestamp = int(now_local.timestamp())
        params = [
            ('start-date', start_timestamp),
            ('end-date', end_timestamp),
            ('pending', 1),
        ]
        for account_id, _ in accounts:
            params.append(('account', account_id))

        print(f"📡 Manual sync: batch fetching {len(accounts)} SimpleFin account(s) in one request", flush=True)
        print(f"📅 Date range: {start_timestamp} to {end_timestamp} (30 days)", flush=True)
        response = requests.get(f"{access_url}/accounts", params=params, timeout=60)
        if response.status_code != 200:
            print(f"❌ SimpleFin API error: {response.status_code} - {response.text}", flush=True)
            if response.status_code == 403:
                c.execute("UPDATE simplefin_config SET is_valid = 0")
                conn.commit()
            conn.close()
            return jsonify({"error": f"SimpleFin API error: {response.status_code}"}), 400

        simplefin_data = response.json()
        print(f"✅ SimpleFin batch fetch returned {len(simplefin_data.get('accounts', []))} accounts", flush=True)
        for acc in simplefin_data.get('accounts', []):
            print(f"  Account {acc.get('id')}: {len(acc.get('transactions', []))} transactions", flush=True)

        for account_id, pocket_id in accounts:
            try:
                check_simplefin_transactions(conn, c, account_id, pocket_id, access_url, prefetched_data=simplefin_data)
                _last_simplefin_sync[account_id] = time.time()
                synced_count += 1
            except Exception as e:
                print(f"Error syncing account {account_id}: {e}")

        # Persist last sync timestamp so the frontend can display it
        if synced_count > 0:
            from datetime import datetime
            c.execute("UPDATE simplefin_config SET last_sync = ?", (datetime.utcnow().isoformat() + 'Z',))
            conn.commit()

        conn.close()

        return jsonify({
            "success": True,
            "message": f"Synced {synced_count} account(s)",
            "accountsSynced": synced_count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- SPLITWISE API ENDPOINTS ---

@app.route('/api/splitwise/get-config')
@login_required
def api_splitwise_get_config():
    """Check if Splitwise is configured"""
    return jsonify({"configured": bool(get_splitwise_api_key())})

@app.route('/api/splitwise/save-key', methods=['POST'])
@login_required
def api_splitwise_save_key():
    """Validate and save Splitwise API key"""
    api_key = request.json.get('apiKey')
    if not api_key:
        return jsonify({"error": "API key required"}), 400

    # Validate by getting current user
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_current_user",
            headers=headers,
            timeout=30
        )
    except Exception as e:
        return jsonify({"error": f"Network error: {str(e)}"}), 500

    if response.status_code == 200:
        user_data = response.json().get("user", {})
        user_id = user_data.get("id")

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM splitwise_config")  # Clear old
        c.execute("INSERT INTO splitwise_config (api_key, user_id, is_valid) VALUES (?, ?, 1)",
                  (api_key, user_id))
        conn.commit()
        conn.close()

        return jsonify({"success": True, "userId": user_id})
    else:
        return jsonify({"error": "Invalid API key"}), 400

@app.route('/api/splitwise/friends')
@login_required
def api_splitwise_get_friends():
    """Get list of Splitwise friends for filtering"""
    api_key = get_splitwise_api_key()
    if not api_key:
        return jsonify({"error": "Splitwise not configured"}), 400

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_friends",
            headers=headers,
            timeout=30
        )
    except Exception as e:
        return jsonify({"error": f"Network error: {str(e)}"}), 500

    if response.status_code == 200:
        friends = response.json().get("friends", [])
        return jsonify({"friends": friends})
    return jsonify({"error": "Failed to fetch friends"}), 500

@app.route('/api/splitwise/set-tracked-friends', methods=['POST'])
@login_required
def api_splitwise_set_tracked_friends():
    """Set which friends to track (or NULL for all)"""
    friend_ids = request.json.get('friendIds')
    tracked_friends_json = json.dumps(friend_ids) if friend_ids else None

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Store in splitwise_config as temporary preference (will be copied to pocket_config on creation)
    c.execute("UPDATE splitwise_config SET tracked_friends = ? WHERE id = (SELECT MIN(id) FROM splitwise_config)",
              (tracked_friends_json,))

    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/splitwise/get-creditors')
@login_required
def api_splitwise_get_creditors():
    """Get list of friends user owes money to (from /get_friends endpoint)"""
    try:
        api_key = get_splitwise_api_key()

        if not api_key:
            return jsonify({"error": "Splitwise not configured"}), 400

        # Fetch friends list with balance information
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_friends",
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch Splitwise friends"}), 500

        # Get all friends (show all, regardless of balance)
        friends_list = []
        friends_data = response.json()

        for friend in friends_data.get("friends", []):
            # In Splitwise, balance is a list of balance objects for different currencies
            # We'll take the first balance (usually USD)
            balance_list = friend.get("balance", [])
            if isinstance(balance_list, list) and len(balance_list) > 0:
                balance = float(balance_list[0].get("amount", "0"))
            else:
                balance = float(balance_list) if balance_list else 0.0

            friend_id = friend.get("id")
            first_name = friend.get("first_name", "")
            last_name = friend.get("last_name", "")
            friend_name = f"{first_name} {last_name}".strip() or f"User {friend_id}"

            # Show amount owed (negative balance means user owes, positive means friend owes user)
            amount_owed = abs(balance) if balance < 0 else 0
            amount_owed_to_user = abs(balance) if balance > 0 else 0

            friends_list.append({
                "friendId": friend_id,
                "friendName": friend_name,
                "amountOwed": round(amount_owed, 2),
                "owesYou": round(amount_owed_to_user, 2)
            })

        # Sort by amount owed descending (those you owe first, then others)
        friends_list.sort(key=lambda x: x["amountOwed"], reverse=True)

        return jsonify({"creditors": friends_list})

    except Exception as e:
        print(f"❌ Error fetching creditors: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/splitwise/create-pockets', methods=['POST'])
@login_required
def api_splitwise_create_pockets():
    """Create per-friend Splitwise pockets for selected friends"""
    try:
        api_key = get_splitwise_api_key()

        if not api_key:
            return jsonify({"error": "Splitwise not configured"}), 400

        # Get list of selected friend IDs to create pockets for
        selected_friend_ids = request.json.get('friendIds', [])

        if not selected_friend_ids:
            return jsonify({"error": "No friends selected"}), 400

        # Fetch friends list to get names and balances
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_friends",
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch Splitwise friends"}), 500

        # Build map of selected friends with their names and balances
        friend_info = {}  # friend_id -> {name, balance}
        friends_data = response.json()

        for friend in friends_data.get("friends", []):
            friend_id = friend.get("id")

            # Only process selected friends
            if friend_id not in selected_friend_ids:
                continue

            # Get friend's balance (negative = user owes)
            # Balance is a list of balance objects for different currencies
            balance_list = friend.get("balance", [])
            if isinstance(balance_list, list) and len(balance_list) > 0:
                balance = float(balance_list[0].get("amount", "0"))
            else:
                balance = float(balance_list) if balance_list else 0.0

            first_name = friend.get("first_name", "")
            last_name = friend.get("last_name", "")
            friend_name = f"{first_name} {last_name}".strip() or f"User {friend_id}"

            # Create pocket for selected friend with current balance (negative = user owes, positive = friend owes user)
            initial_amount = abs(balance) if balance < 0 else 0  # Only move money when user owes (balance < 0)

            print(f"🔍 {friend_name}: raw_balance={balance}, is_positive={balance > 0}, initial_amount={initial_amount}", flush=True)

            friend_info[friend_id] = {
                "name": friend_name,
                "balance": initial_amount
            }

        if not friend_info:
            return jsonify({"error": "No friends selected"}), 400

        # Create a pocket for each selected friend
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        created_pockets = []

        for friend_id, info in friend_info.items():
            friend_name = info["name"]
            initial_amount = info["balance"]

            # Create pocket
            pocket_data = create_pocket(
                f"Owed to {friend_name}",
                target_amount=0,
                initial_amount=initial_amount,
                note=f"You owe {friend_name} this amount"
            )

            if pocket_data.get("error"):
                error_msg = pocket_data.get("error")
                print(f"❌ Failed to create pocket for {friend_name}: {error_msg}", flush=True)
                conn.close()
                return jsonify({"error": f"Failed to create pocket for {friend_name}: {error_msg}"}), 500

            result = pocket_data.get("result", {})
            pocket_id = result.get("id")

            if not pocket_id:
                conn.close()
                return jsonify({"error": f"Failed to get pocket ID for {friend_name}"}), 500

            # Save to database
            c.execute("""INSERT OR REPLACE INTO splitwise_pocket_config
                        (friend_id, friend_name, pocket_id)
                        VALUES (?, ?, ?)""",
                      (friend_id, friend_name, pocket_id))

            created_pockets.append({"friendId": friend_id, "name": friend_name, "pocketId": pocket_id})
            print(f"✨ Created pocket for {friend_name}: ${initial_amount:.2f}", flush=True)

        conn.commit()
        conn.close()
        cache.clear()

        return jsonify({
            "success": True,
            "pockets": created_pockets,
            "count": len(created_pockets)
        })

    except Exception as e:
        print(f"❌ Error creating pockets: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/splitwise/status')
@login_required
def api_splitwise_status():
    """Get Splitwise integration status"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Get all friend pockets
    c.execute("SELECT friend_id, friend_name, pocket_id FROM splitwise_pocket_config ORDER BY friend_name")
    pocket_rows = c.fetchall()

    c.execute("SELECT last_sync FROM splitwise_config LIMIT 1")
    config_row = c.fetchone()

    c.execute("SELECT COUNT(*) FROM splitwise_expenses")
    expense_row = c.fetchone()
    expense_count = expense_row[0] if expense_row else 0

    conn.close()

    pockets = [
        {"friendId": row[0], "friendName": row[1], "pocketId": row[2]}
        for row in pocket_rows
    ]

    return jsonify({
        "configured": bool(get_splitwise_api_key()),
        "pocketsCreated": len(pockets) > 0,
        "pockets": pockets,
        "lastSync": config_row[0] if config_row else None,
        "totalExpenses": expense_count
    })

@app.route('/api/splitwise/friend-balances')
@login_required
def api_splitwise_friend_balances():
    """Get tracked friend balances from Splitwise API"""
    try:
        api_key = get_splitwise_api_key()

        if not api_key:
            return jsonify({"error": "Splitwise not configured"}), 400

        # Fetch friends list with current balances
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_friends",
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch friends"}), 500

        # Get tracked friend list from database
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT friend_id, pocket_id FROM splitwise_pocket_config")
        tracked_friends = {row[0]: row[1] for row in c.fetchall()}
        conn.close()

        # Build response with tracked friends and their balances
        balances = []
        friends_data = response.json()

        for friend in friends_data.get("friends", []):
            friend_id = friend.get("id")

            # Only include tracked friends
            if friend_id not in tracked_friends:
                continue

            # Get friend's balance (positive = user owes in Splitwise API)
            balance_list = friend.get("balance", [])
            if isinstance(balance_list, list) and len(balance_list) > 0:
                balance = float(balance_list[0].get("amount", "0"))
            else:
                balance = float(balance_list) if balance_list else 0.0

            first_name = friend.get("first_name", "")
            last_name = friend.get("last_name", "")
            friend_name = f"{first_name} {last_name}".strip() or f"User {friend_id}"

            # Include all tracked friends with their absolute balance values
            balances.append({
                "friendId": friend_id,
                "friendName": friend_name,
                "balance": round(abs(balance), 2),  # Absolute value for display
                "pocketId": tracked_friends[friend_id]
            })

        # Sort by balance descending
        balances.sort(key=lambda x: x["balance"], reverse=True)

        return jsonify({"balances": balances})

    except Exception as e:
        print(f"❌ Error fetching friend balances: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/splitwise/sync-now', methods=['POST'])
@login_required
def api_splitwise_sync_now():
    """Sync Splitwise friend balances to pocket balances"""
    try:
        api_key = get_splitwise_api_key()
        if not api_key:
            return jsonify({"error": "Splitwise not configured"}), 400

        # Fetch friends list with current balances
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(
            "https://secure.splitwise.com/api/v3.0/get_friends",
            headers=headers,
            timeout=30
        )

        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch Splitwise friends"}), 500

        # Get tracked friends with their pocket IDs
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT friend_id, friend_name, pocket_id FROM splitwise_pocket_config")
        tracked_friends = {row[0]: {"name": row[1], "pocket_id": row[2]} for row in c.fetchall()}

        # Update sync timestamp (use subquery to get the actual row id)
        c.execute("UPDATE splitwise_config SET last_sync = ? WHERE id = (SELECT MIN(id) FROM splitwise_config)",
                  (datetime.now().isoformat(),))
        conn.commit()
        conn.close()

        if not tracked_friends:
            return jsonify({"success": True, "synced": 0, "message": "No tracked friends"})

        # Get Crew headers and checking account for transfers
        crew_headers = get_crew_headers()
        checking_id = get_primary_account_id()

        if not crew_headers or not checking_id:
            return jsonify({"error": "Crew credentials not configured"}), 400

        synced_count = 0
        friends_data = response.json()

        for friend in friends_data.get("friends", []):
            friend_id = friend.get("id")

            # Only sync tracked friends
            if friend_id not in tracked_friends:
                continue

            tracked = tracked_friends[friend_id]
            pocket_id = tracked["pocket_id"]
            friend_name = tracked["name"]

            # Get friend's balance from Splitwise
            # Negative balance = user owes money (we need to save for this)
            # Positive balance = friend owes user (they owe you)
            balance_list = friend.get("balance", [])
            if isinstance(balance_list, list) and len(balance_list) > 0:
                splitwise_balance = float(balance_list[0].get("amount", "0"))
            else:
                splitwise_balance = float(balance_list) if balance_list else 0.0

            # Amount user owes (negative in Splitwise = user owes)
            amount_owed = abs(splitwise_balance) if splitwise_balance < 0 else 0

            # Get current pocket balance from Crew
            query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
            pocket_response = requests.post(URL, headers=crew_headers, json={
                "operationName": "GetSubaccount",
                "variables": {"id": pocket_id},
                "query": query_string
            })
            pocket_data = pocket_response.json()
            current_balance_cents = pocket_data.get("data", {}).get("node", {}).get("overallBalance", 0)
            current_balance = current_balance_cents / 100.0

            # Calculate difference
            difference = amount_owed - current_balance

            if abs(difference) < 0.01:
                print(f"✅ {friend_name}: Already synced (${current_balance:.2f})", flush=True)
                continue

            if difference > 0:
                # Need to add money to pocket (user owes more than pocket has)
                result = move_money(checking_id, pocket_id, difference, f"Splitwise sync: {friend_name}")
                if result.get("error"):
                    print(f"❌ Failed to add ${difference:.2f} to {friend_name}'s pocket: {result['error']}", flush=True)
                else:
                    print(f"➕ Added ${difference:.2f} to {friend_name}'s pocket (now ${amount_owed:.2f})", flush=True)
                    synced_count += 1
            else:
                # Need to remove money from pocket (user owes less than pocket has)
                amount_to_remove = abs(difference)
                result = move_money(pocket_id, checking_id, amount_to_remove, f"Splitwise sync: {friend_name}")
                if result.get("error"):
                    print(f"❌ Failed to remove ${amount_to_remove:.2f} from {friend_name}'s pocket: {result['error']}", flush=True)
                else:
                    print(f"➖ Removed ${amount_to_remove:.2f} from {friend_name}'s pocket (now ${amount_owed:.2f})", flush=True)
                    synced_count += 1

        cache.clear()
        return jsonify({"success": True, "synced": synced_count})

    except Exception as e:
        print(f"❌ Error syncing Splitwise: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@app.route('/api/splitwise/disconnect', methods=['POST'])
@login_required
def api_splitwise_disconnect():
    """Disconnect Splitwise integration and delete all friend pockets"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        # Get all friend pockets
        c.execute("SELECT friend_name, pocket_id FROM splitwise_pocket_config")
        pocket_rows = c.fetchall()

        checking_id = get_primary_account_id()
        headers = get_crew_headers()

        # Try to return money from each pocket to Checking
        for friend_name, pocket_id in pocket_rows:
            if checking_id and pocket_id and headers:
                try:
                    query_string = """query GetSubaccount($id: ID!) { node(id: $id) { ... on Subaccount { id overallBalance } } }"""
                    response = requests.post(URL, headers=headers, json={
                        "operationName": "GetSubaccount",
                        "variables": {"id": pocket_id},
                        "query": query_string
                    })

                    if response.status_code == 200:
                        data = response.json()
                        balance = data.get("data", {}).get("node", {}).get("overallBalance", 0) / 100.0
                        if balance > 0.01:
                            move_money(pocket_id, checking_id, str(balance), f"Splitwise: {friend_name} disconnected")
                            print(f"✅ Returned ${balance:.2f} from {friend_name} pocket", flush=True)
                except Exception as e:
                    print(f"⚠️ Error returning {friend_name} pocket balance: {e}", flush=True)

        # Clear all Splitwise data
        c.execute("DELETE FROM splitwise_config")
        c.execute("DELETE FROM splitwise_pocket_config")
        c.execute("DELETE FROM splitwise_expenses")
        conn.commit()
        conn.close()

        cache.clear()
        print(f"✅ Splitwise disconnected - deleted {len(pocket_rows)} pockets", flush=True)
        return jsonify({"success": True, "message": f"Splitwise disconnected and {len(pocket_rows)} pocket(s) deleted"})
    except Exception as e:
        print(f"❌ Error disconnecting Splitwise: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    print("Server running on http://127.0.0.1:8080")
    # Background thread will start automatically on first request
    app.run(host='0.0.0.0', debug=True, port=8080)
