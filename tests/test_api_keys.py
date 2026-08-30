"""Tests for scoped, read-only API-key authentication."""
import sqlite3

import app as app_module

BEARER = 'Authorization'

# The two GET routes that must never be reachable with a read-scoped key.
# Both return live credentials, which is why "read-only" cannot be defined as
# "the GET verb" in this application.
PAN_AND_CVV_ROUTE = '/api/cards/card_123/sensitive'
SIMPLEFIN_ACCESS_URL_ROUTE = '/api/simplefin/get-access-url'


def auth(key):
    return {BEARER: 'Bearer ' + key}


# --- minting -----------------------------------------------------------------

def test_mint_returns_plaintext_once_and_stores_only_a_hash(logged_in):
    response = logged_in.post('/api/account/api-keys',
                              json={'label': 'homelab', 'scope': 'read'})
    assert response.status_code == 200
    body = response.get_json()
    plaintext = body['key']
    assert plaintext.startswith('sc_')

    listed = logged_in.get('/api/account/api-keys').get_json()['keys']
    assert len(listed) == 1
    assert 'key' not in listed[0]
    assert plaintext not in str(listed)

    conn = sqlite3.connect(app_module.DB_FILE)
    rows = conn.execute("SELECT key_hash, key_prefix, scope FROM api_keys").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == app_module.hash_api_key(plaintext)
    assert plaintext not in rows[0][0]
    assert rows[0][2] == 'read'


def test_mint_rejects_an_unsupported_scope(logged_in):
    response = logged_in.post('/api/account/api-keys',
                              json={'label': 'too much', 'scope': 'write'})
    assert response.status_code == 400


def test_mint_requires_a_label(logged_in):
    assert logged_in.post('/api/account/api-keys', json={'label': ' '}).status_code == 400


# --- the read scope grants what it should ------------------------------------

def test_read_key_is_accepted_on_an_allowlisted_route(simplecrew, read_key):
    response = simplecrew.get('/api/history', headers=auth(read_key))
    assert response.status_code == 200
    assert 'labels' in response.get_json()


def test_read_key_is_accepted_on_onboarding_status(simplecrew, read_key):
    assert simplecrew.get('/api/onboarding/status',
                          headers=auth(read_key)).status_code == 200


def test_using_a_key_records_last_used_at(simplecrew, read_key):
    conn = sqlite3.connect(app_module.DB_FILE)
    assert conn.execute("SELECT last_used_at FROM api_keys").fetchone()[0] is None
    conn.close()

    simplecrew.get('/api/history', headers=auth(read_key))

    conn = sqlite3.connect(app_module.DB_FILE)
    assert conn.execute("SELECT last_used_at FROM api_keys").fetchone()[0] is not None
    conn.close()


# --- the read scope refuses what it must -------------------------------------

def test_read_key_is_refused_on_the_pan_and_cvv_route(simplecrew, read_key):
    """A read-scoped key must not be able to read a full card number."""
    response = simplecrew.get(PAN_AND_CVV_ROUTE, headers=auth(read_key))
    assert response.status_code == 403
    assert 'pan' not in response.get_data(as_text=True).lower()


def test_read_key_is_refused_on_the_simplefin_access_url_route(simplecrew, read_key):
    """A read-scoped key must not be able to read the SimpleFin credential."""
    response = simplecrew.get(SIMPLEFIN_ACCESS_URL_ROUTE, headers=auth(read_key))
    assert response.status_code == 403
    assert 'accessUrl' not in response.get_data(as_text=True)


def test_the_two_dangerous_routes_are_named_refusals_not_just_omissions(simplecrew):
    """Both routes are listed by name, so widening the allowlist cannot expose them."""
    assert 'api_card_sensitive' in app_module.READ_SCOPE_NEVER_ENDPOINTS
    assert 'api_simplefin_get_access_url' in app_module.READ_SCOPE_NEVER_ENDPOINTS
    assert not (app_module.READ_SCOPE_ENDPOINTS & app_module.READ_SCOPE_NEVER_ENDPOINTS)


def test_read_key_is_refused_on_writes(simplecrew, read_key):
    response = simplecrew.post('/api/create-pocket',
                               json={'name': 'nope', 'target': 1},
                               headers=auth(read_key))
    assert response.status_code == 403


def test_read_key_cannot_manage_api_keys(simplecrew, read_key):
    assert simplecrew.get('/api/account/api-keys',
                          headers=auth(read_key)).status_code == 403
    assert simplecrew.post('/api/account/api-keys',
                           json={'label': 'escalate'},
                           headers=auth(read_key)).status_code == 403


def test_read_key_is_refused_on_routes_added_later(simplecrew, read_key):
    """The gate is a positive allowlist: an unlisted endpoint is denied."""
    unlisted = {rule.endpoint for rule in app_module.app.url_map.iter_rules()}
    unlisted -= app_module.READ_SCOPE_ENDPOINTS
    assert 'api_change_password' in unlisted
    assert simplecrew.post('/api/auth/change-password',
                           json={'currentPassword': 'x', 'newPassword': 'yyyyyyyy'},
                           headers=auth(read_key)).status_code == 403


# --- revocation and bad keys --------------------------------------------------

def test_revoked_key_gets_401(logged_in, simplecrew):
    minted = logged_in.post('/api/account/api-keys',
                            json={'label': 'short lived'}).get_json()
    key = minted['key']

    assert simplecrew.get('/api/history', headers=auth(key)).status_code == 200

    revoke = logged_in.post('/api/account/api-keys/%d/revoke' % minted['id'])
    assert revoke.status_code == 200
    logged_in.post('/api/auth/logout')

    response = simplecrew.get('/api/history', headers=auth(key))
    assert response.status_code == 401
    assert response.get_json()['error'] == 'Invalid or revoked API key'


def test_unknown_key_gets_401(simplecrew):
    response = simplecrew.get('/api/history', headers=auth('sc_not_a_real_key'))
    assert response.status_code == 401


def test_revoking_someone_elses_key_is_refused(logged_in):
    minted = logged_in.post('/api/account/api-keys',
                            json={'label': 'mine'}).get_json()
    conn = sqlite3.connect(app_module.DB_FILE)
    conn.execute("UPDATE api_keys SET user_id = 999 WHERE id = ?", (minted['id'],))
    conn.commit()
    conn.close()
    assert logged_in.post(
        '/api/account/api-keys/%d/revoke' % minted['id']).status_code == 403


def test_revoking_a_missing_key_is_404(logged_in):
    assert logged_in.post('/api/account/api-keys/4242/revoke').status_code == 404


# --- existing behaviour is untouched -----------------------------------------

def test_session_auth_still_reaches_everything(logged_in):
    """A browser session is unaffected, including the two gated routes."""
    assert logged_in.get('/api/history').status_code == 200
    response = logged_in.get(SIMPLEFIN_ACCESS_URL_ROUTE)
    assert response.status_code == 200
    assert response.get_json()['success'] is False  # nothing configured in tests


def test_unauthenticated_requests_still_redirect_to_login(simplecrew):
    response = simplecrew.get('/api/history')
    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_a_non_sc_bearer_header_is_ignored(simplecrew):
    """Unrelated Authorization headers keep the pre-existing behaviour."""
    response = simplecrew.get('/api/history',
                              headers={BEARER: 'Bearer some-other-token'})
    assert response.status_code == 302
