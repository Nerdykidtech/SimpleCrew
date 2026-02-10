/**
 * @file account.js
 * @description API layer for account settings and credential management
 * @requires ui/dialogs.js (appAlert)
 */

/**
 * Load and display account credentials status
 */
async function loadAccountSettings() {
    try {
        const response = await fetch('/api/account/credentials/status');
        const data = await response.json();

        if (data.success) {
            updateCredentialStatus('crew', data.credentials.crew);
            updateCredentialStatus('simplefin', data.credentials.simplefin);
            updateCredentialStatus('lunchflow', data.credentials.lunchflow);
            updateCredentialStatus('splitwise', data.credentials.splitwise);
        } else {
            console.error('Failed to load credentials status:', data.error);
        }
    } catch (error) {
        console.error('Error loading account settings:', error);
    }
}

/**
 * Update credential status badge and visibility
 * @param {string} provider - The provider name ('crew', 'simplefin', 'lunchflow')
 * @param {object} status - The status object {configured, valid}
 */
function updateCredentialStatus(provider, status) {
    const badgeEl = document.getElementById(`${provider}-status-badge`);
    const formEl = document.getElementById(`${provider}-config-form`);
    const displayEl = document.getElementById(`${provider}-config-display`);

    if (status.configured && status.valid) {
        // Show green configured badge
        badgeEl.innerHTML = '<span style="background: #d4edda; color: #155724; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;">✓ Configured</span>';
        formEl.style.display = 'none';
        displayEl.style.display = 'block';
    } else if (status.configured && !status.valid) {
        // Show yellow warning badge
        badgeEl.innerHTML = '<span style="background: #fff3cd; color: #856404; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;">⚠ Invalid</span>';
        formEl.style.display = 'block';
        displayEl.style.display = 'none';
    } else {
        // Show gray not configured badge
        badgeEl.innerHTML = '<span style="background: #e9ecef; color: #6c757d; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;">Not Configured</span>';
        formEl.style.display = 'block';
        displayEl.style.display = 'none';
    }
}

// --- CREW TOKEN MANAGEMENT ---

/**
 * Show Crew token edit form
 */
function editCrewToken() {
    document.getElementById('crew-config-form').style.display = 'block';
    document.getElementById('crew-config-display').style.display = 'none';
    document.getElementById('crew-token-input').value = '';
    document.getElementById('crew-error').style.display = 'none';
}

/**
 * Cancel Crew token edit
 */
function cancelCrewEdit() {
    document.getElementById('crew-config-form').style.display = 'none';
    document.getElementById('crew-config-display').style.display = 'block';
    document.getElementById('crew-token-input').value = '';
    document.getElementById('crew-error').style.display = 'none';
}

/**
 * Save Crew bearer token
 */
async function saveCrewToken() {
    const token = document.getElementById('crew-token-input').value.trim();
    const errorEl = document.getElementById('crew-error');

    if (!token) {
        errorEl.textContent = 'Please enter a bearer token';
        errorEl.style.display = 'block';
        return;
    }

    errorEl.style.display = 'none';

    try {
        const response = await fetch('/api/account/crew/update-token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token })
        });

        const data = await response.json();

        if (data.success) {
            appAlert('✓ Crew token updated successfully');
            loadAccountSettings(); // Reload status
        } else {
            errorEl.textContent = data.error || 'Failed to save token';
            errorEl.style.display = 'block';
        }
    } catch (error) {
        errorEl.textContent = 'Network error. Please try again.';
        errorEl.style.display = 'block';
    }
}

/**
 * Test Crew connection
 */
async function testCrewConnection() {
    try {
        const response = await fetch('/api/account/crew/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (data.success) {
            appAlert(`✓ ${data.message}`);
        } else {
            appAlert(`✗ ${data.error}`);
        }
    } catch (error) {
        appAlert('✗ Connection test failed');
    }
}

// --- SIMPLEFIN TOKEN MANAGEMENT ---

/**
 * Show SimpleFin token edit form
 */
function editSimpleFinToken() {
    document.getElementById('simplefin-config-form').style.display = 'block';
    document.getElementById('simplefin-config-display').style.display = 'none';
    document.getElementById('simplefin-token-input').value = '';
    document.getElementById('simplefin-error').style.display = 'none';
}

/**
 * Cancel SimpleFin token edit
 */
function cancelSimpleFinEdit() {
    document.getElementById('simplefin-config-form').style.display = 'none';
    document.getElementById('simplefin-config-display').style.display = 'block';
    document.getElementById('simplefin-token-input').value = '';
    document.getElementById('simplefin-error').style.display = 'none';
}

/**
 * Save SimpleFin setup token
 */
async function saveSimpleFinToken() {
    const token = document.getElementById('simplefin-token-input').value.trim();
    const errorEl = document.getElementById('simplefin-error');

    if (!token) {
        errorEl.textContent = 'Please enter a setup token';
        errorEl.style.display = 'block';
        return;
    }

    errorEl.style.display = 'none';

    try {
        const response = await fetch('/api/account/simplefin/update-token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token })
        });

        const data = await response.json();

        if (data.success) {
            appAlert('✓ SimpleFin token updated successfully');
            loadAccountSettings(); // Reload status
        } else {
            errorEl.textContent = data.error || 'Failed to save token';
            errorEl.style.display = 'block';
        }
    } catch (error) {
        errorEl.textContent = 'Network error. Please try again.';
        errorEl.style.display = 'block';
    }
}

/**
 * Test SimpleFin connection
 */
async function testSimpleFinConnection() {
    try {
        const response = await fetch('/api/account/simplefin/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (data.success) {
            appAlert(`✓ ${data.message}`);
        } else {
            appAlert(`✗ ${data.error}`);
        }
    } catch (error) {
        appAlert('✗ Connection test failed');
    }
}

// --- LUNCHFLOW API KEY MANAGEMENT ---

/**
 * Show LunchFlow API key edit form
 */
function editLunchFlowKey() {
    document.getElementById('lunchflow-config-form').style.display = 'block';
    document.getElementById('lunchflow-config-display').style.display = 'none';
    document.getElementById('lunchflow-apikey-input').value = '';
    document.getElementById('lunchflow-account-error').style.display = 'none';
}

/**
 * Cancel LunchFlow API key edit
 */
function cancelLunchFlowEdit() {
    document.getElementById('lunchflow-config-form').style.display = 'none';
    document.getElementById('lunchflow-config-display').style.display = 'block';
    document.getElementById('lunchflow-apikey-input').value = '';
    document.getElementById('lunchflow-account-error').style.display = 'none';
}

/**
 * Save LunchFlow API key
 */
async function saveLunchFlowKey() {
    const apiKey = document.getElementById('lunchflow-apikey-input').value.trim();
    const errorEl = document.getElementById('lunchflow-account-error');

    if (!apiKey) {
        errorEl.textContent = 'Please enter an API key';
        errorEl.style.display = 'block';
        return;
    }

    errorEl.style.display = 'none';

    try {
        const response = await fetch('/api/account/lunchflow/update-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ apiKey })
        });

        const data = await response.json();

        if (data.success) {
            appAlert('✓ LunchFlow API key updated successfully');
            loadAccountSettings(); // Reload status
        } else {
            errorEl.textContent = data.error || 'Failed to save API key';
            errorEl.style.display = 'block';
        }
    } catch (error) {
        errorEl.textContent = 'Network error. Please try again.';
        errorEl.style.display = 'block';
    }
}

/**
 * Test LunchFlow connection
 */
async function testLunchFlowConnection() {
    try {
        const response = await fetch('/api/account/lunchflow/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (data.success) {
            appAlert(`✓ ${data.message}`);
        } else {
            appAlert(`✗ ${data.error}`);
        }
    } catch (error) {
        appAlert('✗ Connection test failed');
    }
}

// --- SPLITWISE API KEY MANAGEMENT ---

/**
 * Show Splitwise API key edit form
 */
function editSplitwiseKey() {
    document.getElementById('splitwise-config-form').style.display = 'block';
    document.getElementById('splitwise-config-display').style.display = 'none';
    document.getElementById('splitwise-apikey-input').value = '';
    document.getElementById('splitwise-account-error').style.display = 'none';
}

/**
 * Cancel Splitwise API key edit
 */
function cancelSplitwiseEdit() {
    document.getElementById('splitwise-config-form').style.display = 'none';
    document.getElementById('splitwise-config-display').style.display = 'block';
    document.getElementById('splitwise-apikey-input').value = '';
    document.getElementById('splitwise-account-error').style.display = 'none';
}

/**
 * Save Splitwise API key
 */
async function saveSplitwiseKey() {
    const apiKey = document.getElementById('splitwise-apikey-input').value.trim();
    const errorEl = document.getElementById('splitwise-account-error');

    if (!apiKey) {
        errorEl.textContent = 'Please enter an API key';
        errorEl.style.display = 'block';
        return;
    }

    errorEl.style.display = 'none';

    try {
        const response = await fetch('/api/account/splitwise/update-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ apiKey })
        });

        const data = await response.json();

        if (data.success) {
            appAlert('✓ Splitwise API key updated successfully');
            loadAccountSettings(); // Reload status
        } else {
            errorEl.textContent = data.error || 'Failed to save API key';
            errorEl.style.display = 'block';
        }
    } catch (error) {
        errorEl.textContent = 'Network error. Please try again.';
        errorEl.style.display = 'block';
    }
}

/**
 * Test Splitwise connection
 */
async function testSplitwiseConnection() {
    try {
        const response = await fetch('/api/account/splitwise/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();

        if (data.success) {
            appAlert(`✓ ${data.message}`);
        } else {
            appAlert(`✗ ${data.error}`);
        }
    } catch (error) {
        appAlert('✗ Connection test failed');
    }
}
