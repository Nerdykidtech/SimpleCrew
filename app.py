import requests
import sqlite3
import time
import functools
import os
import threading
from datetime import datetime, date, timedelta
from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__)

# --- CONFIGURATION ---
URL = "https://api.trycrew.com/willow/graphql"
# In app.py
DB_FILE = os.environ.get("DB_FILE", "savings_data.db")

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
        query_string = """ query RecentActivity($accountId: ID!, $cursor: String, $pageSize: Int = 100, $searchFilters: CashTransactionFilter) { account: node(id: $accountId) { ... on Account { id cashTransactions(first: $pageSize, after: $cursor, searchFilters: $searchFilters) { edges { node { id amount description occurredAt title type subaccount { id } } } } } } } """
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
                sub_id = node.get('subaccount', {}).get('id') if node.get('subaccount') else None
                if min_date or max_date:
                    tx_date = date_str[:10]
                    if min_date and tx_date < min_date: continue
                    if max_date and tx_date > max_date: continue
                if min_amount or max_amount:
                    abs_amt = abs(amt)
                    if min_amount and abs_amt < float(min_amount): continue
                    if max_amount and abs_amt > float(max_amount): continue
                txs.append({"id": node['id'], "title": node['title'], "description": node['description'], "amount": amt, "date": date_str, "type": node['type'], "subaccountId": sub_id})
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
def get_subaccounts_list():
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ query CurrentUser { currentUser { accounts { subaccounts { id name overallBalance } } } } """
        response = requests.post(URL, headers=headers, json={"operationName": "CurrentUser", "query": query_string})
        data = response.json()
        subs = []
        for account in data.get("data", {}).get("currentUser", {}).get("accounts", []):
            for sub in account.get("subaccounts", []):
                balance = sub.get("overallBalance", 0) / 100.0
                subs.append({"id": sub.get("id"), "name": sub.get("name"), "balance": balance})
        return {"subaccounts": subs}
    except Exception as e:
        return {"error": str(e)}

def move_money(from_id, to_id, amount, note=""):
    try:
        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}
        query_string = """ mutation InitiateTransferScottie($input: InitiateTransferInput!) { initiateTransfer(input: $input) { result { id __typename } __typename } } """
        amount_cents = int(round(float(amount) * 100))
        variables = {"input": {"amount": amount_cents, "accountFromId": from_id, "accountToId": to_id, "note": note or "Transfer"}}
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

        return {"cards": all_cards}
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


def set_spend_pocket_action(user_id, pocket_id):
    try:
        # --- FIX START: Resolve "Checking" to a real ID ---
        if pocket_id == "Checking":
            # Fetch the list of subaccounts to find the ID for "Checking"
            all_subs = get_subaccounts_list()
            
            if "error" in all_subs:
                return {"error": "Could not resolve Checking ID"}
                
            found_id = None
            for sub in all_subs.get("subaccounts", []):
                if sub["name"] == "Checking":
                    found_id = sub["id"]
                    break
            
            if found_id:
                pocket_id = found_id
            else:
                return {"error": "Checking subaccount not found"}
        # --- FIX END ---

        headers = get_crew_headers()
        if not headers: return {"error": "Credentials not found"}

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
                "selectedSpendSubaccountId": pocket_id
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
@app.route('/')
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

# --- API ROUTES ---
@app.route('/api/family')
def api_family(): return jsonify(get_family_data())
@app.route('/api/cards')
def api_cards():
    # Allow forcing a refresh if ?refresh=true is passed
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_cards_data(force_refresh=refresh))

# 3. CREATE THE MISSING MOVE/REORDER ENDPOINT
@app.route('/api/groups/move-pocket', methods=['POST'])
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
def api_set_card_spend():
    data = request.json
    return jsonify(set_spend_pocket_action(
        data.get('userId'),
        data.get('pocketId')
    ))

@app.route('/api/savings')
def api_savings():
    # Check if the frontend is asking for a forced refresh
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_financial_data(force_refresh=refresh))

@app.route('/api/history')
def api_history(): return jsonify(get_history())
@app.route('/api/transactions')
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
            if q and q.lower() not in (row[3] or "").lower() and q.lower() not in (row[4] or "").lower():
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

        # Merge and sort by date
        if result["transactions"]:
            all_txs = result["transactions"] + credit_card_txs
            # Handle None dates by treating them as empty strings for sorting
            all_txs.sort(key=lambda x: x.get("date") or "", reverse=True)
            result["transactions"] = all_txs
        elif credit_card_txs:
            result["transactions"] = credit_card_txs

    except Exception as e:
        print(f"Error loading credit card transactions: {e}")

    return jsonify(result)
@app.route('/api/transaction/<path:tx_id>')
def api_transaction_detail(tx_id): return jsonify(get_transaction_detail(tx_id))

@app.route('/api/expenses')
def api_expenses():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_expenses_data(force_refresh=refresh))

@app.route('/api/goals')
def api_goals():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_goals_data(force_refresh=refresh))

@app.route('/api/trends')
def api_trends(): return jsonify(get_monthly_trends())

@app.route('/api/subaccounts')
def api_subaccounts():
    refresh = request.args.get('refresh') == 'true'
    return jsonify(get_subaccounts_list(force_refresh=refresh))

@app.route('/api/move-money', methods=['POST'])
def api_move_money():
    data = request.json
    return jsonify(move_money(data.get('fromId'), data.get('toId'), data.get('amount'), data.get('note')))

@app.route('/api/delete-pocket', methods=['POST'])
def api_delete_pocket():
    data = request.json
    return jsonify(delete_subaccount_action(data.get('id')))


@app.route('/api/create-pocket', methods=['POST'])
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
def api_delete_bill():
    data = request.json
    return jsonify(delete_bill_action(data.get('id')))


@app.route('/api/create-bill', methods=['POST'])
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
def api_user():
    return jsonify(get_user_profile_info())

@app.route('/api/intercom')
def api_intercom():
    return jsonify(get_intercom_data())

# --- LUNCHFLOW API ENDPOINTS ---
@app.route('/api/lunchflow/get-config')
def api_get_lunchflow_config():
    """Get LunchFlow configuration status"""
    api_key = get_lunchflow_api_key()
    return jsonify({
        "hasApiKey": api_key is not None,
        "isConfigured": api_key is not None
    })

@app.route('/api/lunchflow/save-key', methods=['POST'])
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
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            start_timestamp = int(datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc).timestamp())
            end_year, end_month = (now_utc.year + 1, 1) if now_utc.month == 12 else (now_utc.year, now_utc.month + 1)
            end_timestamp = int(datetime(end_year, end_month, 1, tzinfo=timezone.utc).timestamp())
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

            # Calculate date range: current calendar month
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            start_timestamp = int(datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc).timestamp())
            end_year, end_month = (now_utc.year + 1, 1) if now_utc.month == 12 else (now_utc.year, now_utc.month + 1)
            end_timestamp = int(datetime(end_year, end_month, 1, tzinfo=timezone.utc).timestamp())

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

        # Get list of already seen transaction IDs
        c.execute("SELECT transaction_id FROM credit_card_transactions WHERE account_id = ?", (account_id,))
        seen_ids = {row[0] for row in c.fetchall()}
        print(f"  Already have {len(seen_ids)} transactions in database")

        new_transactions = []
        for tx in transactions:
            tx_id = tx.get("id")
            if not tx_id:
                print(f"  ⚠️ Skipping transaction with no ID: {tx}")
                continue
            if tx_id in seen_ids:
                continue

            # SimpleFin amounts may be strings, convert to float
            amount_str = tx.get("amount", "0")
            try:
                amount = abs(float(amount_str))  # SimpleFin amounts are in dollars, negative for debits
            except (ValueError, TypeError):
                print(f"  ⚠️ Could not parse transaction amount '{amount_str}', using 0")
                amount = 0

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

            print(f"  💳 New transaction: ${amount} - {description} (ID: {tx_id})")

            c.execute("""INSERT OR IGNORE INTO credit_card_transactions
                         (transaction_id, account_id, amount, date, merchant, description, is_pending)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (tx_id, account_id, amount, date_str, "", description, 1 if pending else 0))

            if c.rowcount > 0:
                new_transactions.append(tx)
                print(f"  ✅ Inserted transaction {tx_id}")
            else:
                print(f"  ⚠️ Transaction {tx_id} was not inserted (already exists or error)")

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
                    total_new_spending = sum(abs(float(tx.get("amount", 0))) for tx in new_transactions)
                    if total_new_spending > 0.01:
                        print(f"💸 Moving ${total_new_spending:.2f} from Checking to Credit Card pocket for {len(new_transactions)} new transaction(s)", flush=True)
                        move_money(checking_subaccount_id, pocket_id, str(total_new_spending), f"SimpleFin: {len(new_transactions)} new transaction(s)")
                        cache.clear()
        elif new_transactions and is_initial_sync:
            print(f"⏭️ Skipping automatic money movement for initial sync ({len(new_transactions)} historical transactions stored)", flush=True)

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
                balance = float(balance_str)
            except (ValueError, TypeError):
                balance = 0

            accounts.append({
                "id": account.get("id"),
                "name": account.get("name", "Unknown Account"),
                "balance": balance,  # SimpleFin balance is in dollars
                "currency": account.get("currency", "USD"),
                "org": account.get("org", {}).get("name", "Unknown")
            })

        return {"accounts": accounts}
    except Exception as e:
        return {"error": f"Failed to fetch accounts: {str(e)}"}

@app.route('/api/simplefin/get-access-url')
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
                from datetime import datetime, timezone
                now_utc = datetime.now(timezone.utc)
                start_timestamp = int(datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc).timestamp())
                end_year, end_month = (now_utc.year + 1, 1) if now_utc.month == 12 else (now_utc.year, now_utc.month + 1)
                end_timestamp = int(datetime(end_year, end_month, 1, tzinfo=timezone.utc).timestamp())

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

@app.route('/api/simplefin/change-account', methods=['POST'])
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

@app.route('/api/simplefin/sync-now', methods=['POST'])
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

        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        start_timestamp = int(datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc).timestamp())
        end_year, end_month = (now_utc.year + 1, 1) if now_utc.month == 12 else (now_utc.year, now_utc.month + 1)
        end_timestamp = int(datetime(end_year, end_month, 1, tzinfo=timezone.utc).timestamp())
        params = [
            ('start-date', start_timestamp),
            ('end-date', end_timestamp),
            ('pending', 1),
        ]
        for account_id, _ in accounts:
            params.append(('account', account_id))

        print(f"📡 Manual sync: batch fetching {len(accounts)} SimpleFin account(s) in one request", flush=True)
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

if __name__ == '__main__':
    init_db()
    print("Server running on http://127.0.0.1:8080")
    # Background thread will start automatically on first request
    app.run(host='0.0.0.0', debug=True, port=8080)
