"""Test fixtures for SimpleCrew.

app.py opens (and creates) its SQLite database at import time, so DB_FILE has
to point at a throwaway file before the module is imported.
"""
import os
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

_DB_DIR = tempfile.mkdtemp(prefix='simplecrew-tests-')
os.environ['DB_FILE'] = os.path.join(_DB_DIR, 'test.db')

import app as app_module  # noqa: E402  (must follow the DB_FILE assignment)

# The background transaction checker talks to external APIs on a timer; keep it
# out of the test process.
app_module._background_thread_started = True

TEST_USERNAME = 'tester'
TEST_PASSWORD = 'correct horse battery'


@pytest.fixture
def simplecrew(tmp_path, monkeypatch):
    """A freshly initialised app with one registered user."""
    db_file = str(tmp_path / 'simplecrew.db')
    monkeypatch.setattr(app_module, 'DB_FILE', db_file)
    app_module.init_db()

    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False

    with app_module.app.test_client() as client:
        response = client.post('/api/auth/register', json={
            'username': TEST_USERNAME,
            'email': 'tester@example.com',
            'password': TEST_PASSWORD,
        })
        assert response.status_code == 200, response.get_data(as_text=True)
        client.post('/api/auth/logout')
        yield client


@pytest.fixture
def logged_in(simplecrew):
    """The same client, with an authenticated browser session."""
    response = simplecrew.post('/api/auth/login', json={
        'username': TEST_USERNAME,
        'password': TEST_PASSWORD,
    })
    assert response.status_code == 200, response.get_data(as_text=True)
    return simplecrew


@pytest.fixture
def read_key(logged_in):
    """A freshly minted scope='read' key, as plaintext."""
    response = logged_in.post('/api/account/api-keys',
                              json={'label': 'test key', 'scope': 'read'})
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    logged_in.post('/api/auth/logout')
    return body['key']
