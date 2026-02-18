/**
 * @file splitwise.js
 * @description Splitwise integration - creates per-friend pockets
 * @requires utils/formatters.js (fmt function)
 * @requires ui/dialogs.js (appAlert, appConfirm)
 */

/**
 * Load Splitwise status and show appropriate screen
 */
function loadSplitwiseSetup() {
    console.log('Loading Splitwise status...');
    fetch('/api/splitwise/status')
        .then(res => res.json())
        .then(status => {
            console.log('Splitwise status:', status);
            if (status.configured && status.pocketsCreated && status.pockets.length > 0) {
                loadSplitwiseManagement(status);
            } else {
                showConnectScreen();
            }
        })
        .catch(err => {
            console.error('Error loading Splitwise:', err);
            showConnectScreen();
        });
}

/**
 * Show connect screen
 */
function showConnectScreen() {
    console.log('Showing connect screen');
    document.getElementById('splitwise-connect-screen').style.display = 'block';
    document.getElementById('splitwise-management-screen').style.display = 'none';
    document.getElementById('splitwise-api-error').style.display = 'none';
}

/**
 * Save API key and show creditor selection
 */
function saveAndCreateSplitwisePockets() {
    const apiKey = document.getElementById('splitwise-api-key-input').value.trim();
    const errorEl = document.getElementById('splitwise-api-error');

    if (!apiKey) {
        errorEl.textContent = 'Please enter an API key';
        errorEl.style.display = 'block';
        return;
    }

    errorEl.style.display = 'none';

    // Step 1: Save API key
    fetch('/api/splitwise/save-key', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({apiKey})
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            console.log('API key saved, loading creditors...');
            // Step 2: Load creditors for selection
            showCreditorSelection();
        } else {
            throw new Error(data.error || 'Invalid API key');
        }
    })
    .catch(err => {
        console.error('Error:', err);
        errorEl.textContent = err.message || 'Connection failed';
        errorEl.style.display = 'block';
    });
}

/**
 * Show creditor selection screen
 */
function showCreditorSelection() {
    const errorEl = document.getElementById('splitwise-api-error');
    errorEl.style.display = 'none';

    console.log('Fetching creditors...');
    fetch('/api/splitwise/get-creditors')
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                throw new Error(data.error);
            }

            const creditors = data.creditors || [];
            console.log('Got creditors:', creditors);

            if (creditors.length === 0) {
                appAlert('No debts found. You don\'t owe anyone!');
                loadSplitwiseSetup();
                return;
            }

            // Show creditor selection UI
            const creditorSelect = document.getElementById('splitwise-creditor-select');
            creditorSelect.innerHTML = '';

            creditors.forEach(friend => {
                const checkbox = document.createElement('label');
                checkbox.style.display = 'flex';
                checkbox.style.alignItems = 'center';
                checkbox.style.padding = '12px';
                checkbox.style.margin = '8px 0';
                checkbox.style.borderRadius = '6px';
                checkbox.style.cursor = 'pointer';
                checkbox.style.border = '1px solid var(--border-color)';

                // Highlight friends you owe money to
                if (friend.amountOwed > 0) {
                    checkbox.style.backgroundColor = '#e8f5e9';
                    checkbox.style.borderColor = '#4caf50';
                } else {
                    checkbox.style.backgroundColor = '#f9f9f9';
                }

                const input = document.createElement('input');
                input.type = 'checkbox';
                input.value = friend.friendId;
                input.style.marginRight = '12px';
                input.style.cursor = 'pointer';

                const text = document.createElement('span');
                let statusText = '';
                if (friend.amountOwed > 0) {
                    statusText = ` — You owe $${friend.amountOwed.toFixed(2)}`;
                } else if (friend.owesYou > 0) {
                    statusText = ` — They owe you $${friend.owesYou.toFixed(2)}`;
                } else {
                    statusText = ' — Settled';
                }
                text.innerHTML = `<strong>${friend.friendName}</strong>${statusText}`;
                text.style.flex = '1';

                checkbox.appendChild(input);
                checkbox.appendChild(text);
                creditorSelect.appendChild(checkbox);
            });

            // Show selection screen
            document.getElementById('splitwise-connect-screen').style.display = 'none';
            document.getElementById('splitwise-creditor-selection-screen').style.display = 'block';
        })
        .catch(err => {
            console.error('Error loading creditors:', err);
            errorEl.textContent = `Error: ${err.message}`;
            errorEl.style.display = 'block';
        });
}

/**
 * Create pockets for selected creditors
 */
function createSelectedCreditorPockets() {
    const checkboxes = document.querySelectorAll('#splitwise-creditor-select input[type="checkbox"]:checked');
    const selectedFriendIds = Array.from(checkboxes).map(cb => parseInt(cb.value));

    if (selectedFriendIds.length === 0) {
        appAlert('Please select at least one person');
        return;
    }

    console.log('Creating pockets for friends:', selectedFriendIds);

    fetch('/api/splitwise/create-pockets', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({friendIds: selectedFriendIds})
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            console.log('Created pockets:', data);
            appAlert(`Created ${data.count} pocket(s).`);
            loadGoals(true);  // Refresh pockets list
            loadSplitwiseSetup();  // Reload Splitwise tab
        } else {
            throw new Error(data.error || 'Failed to create pockets');
        }
    })
    .catch(err => {
        console.error('Error:', err);
        appAlert(`Error: ${err.message}`);
    });
}

/**
 * Load and display management screen with friend pockets
 */
function loadSplitwiseManagement(status) {
    console.log('Loading Splitwise management screen');
    document.getElementById('splitwise-connect-screen').style.display = 'none';
    document.getElementById('splitwise-management-screen').style.display = 'block';

    // Display last sync time
    if (status.lastSync) {
        const lastSyncDate = new Date(status.lastSync);
        document.getElementById('splitwise-last-sync').textContent = lastSyncDate.toLocaleString();
    } else {
        document.getElementById('splitwise-last-sync').textContent = 'Never';
    }

    // Display friend pockets with their balances
    renderFriendPockets(status.pockets);

    // Load and display tracked friend balances
    loadFriendBalances();
}

/**
 * Render list of friend pockets with balances
 */
function renderFriendPockets(pockets) {
    const container = document.getElementById('splitwise-pockets-list');

    if (!pockets || pockets.length === 0) {
        container.innerHTML = '<p style="color: var(--text-light); text-align: center;">No debts found</p>';
        return;
    }

    // Fetch pocket balances from Crew API
    fetch('/api/goals?refresh=true')
        .then(res => res.json())
        .then(data => {
            const goals = data.goals || [];
            let html = '<div style="display: flex; flex-direction: column; gap: 10px;">';

            pockets.forEach(pocket => {
                const goal = goals.find(g => g.id === pocket.pocketId);
                const balance = goal ? goal.balance : 0;

                html += `
                    <div style="padding: 15px; background: var(--bg-elevated); border-radius: 8px; border-left: 3px solid var(--simple-blue);">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div style="font-weight: 600; color: var(--text-dark);">${pocket.friendName}</div>
                            <div style="font-size: 16px; font-weight: bold; color: var(--simple-blue);">${fmt(balance)}</div>
                        </div>
                    </div>
                `;
            });

            html += '</div>';
            container.innerHTML = html;
        })
        .catch(err => {
            console.error('Error fetching pocket balances:', err);
            let html = '<div style="display: flex; flex-direction: column; gap: 10px;">';
            pockets.forEach(pocket => {
                html += `
                    <div style="padding: 15px; background: var(--bg-elevated); border-radius: 8px; border-left: 3px solid var(--simple-blue);">
                        <div style="font-weight: 600; color: var(--text-dark);">${pocket.friendName}</div>
                    </div>
                `;
            });
            html += '</div>';
            container.innerHTML = html;
        });
}

/**
 * Load and display friend balances
 */
function loadFriendBalances() {
    fetch('/api/splitwise/friend-balances')
        .then(res => res.json())
        .then(data => {
            if (data.balances && data.balances.length > 0) {
                renderFriendBalances(data.balances);
            } else {
                document.getElementById('splitwise-all-expenses').innerHTML =
                    '<p style="color: var(--text-light); text-align: center;">No tracked friends</p>';
            }
        })
        .catch(err => {
            console.error('Error loading balances:', err);
            document.getElementById('splitwise-all-expenses').innerHTML =
                '<p style="color: var(--alert-red); text-align: center;">Error loading balances</p>';
        });
}

/**
 * Render tracked friend balances
 */
function renderFriendBalances(balances) {
    const container = document.getElementById('splitwise-all-expenses');

    if (!balances || balances.length === 0) {
        container.innerHTML = '<p style="color: var(--text-light); text-align: center;">No tracked friends</p>';
        return;
    }

    let html = '<div style="display: flex; flex-direction: column; gap: 8px;">';

    balances.forEach(balance => {
        const theyOweYou = balance.balance > 0;
        const youOwe = balance.balance < 0;
        const settled = balance.balance === 0;

        const bgColor = theyOweYou ? 'rgba(76,175,80,0.12)' : youOwe ? 'rgba(239,83,80,0.12)' : 'var(--bg-elevated)';
        const borderColor = theyOweYou ? '#4caf50' : youOwe ? '#ef5350' : 'var(--border-color)';
        const amountColor = theyOweYou ? '#4caf50' : youOwe ? '#ef5350' : 'var(--text-light)';
        const label = theyOweYou ? 'They owe you' : youOwe ? 'You owe them' : 'Settled';

        html += `
            <div style="padding: 12px; background: ${bgColor}; border-radius: 6px; border-left: 3px solid ${borderColor};">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="font-weight: 600; color: var(--text-dark);">${balance.friendName}</div>
                    <div style="text-align: right;">
                        <div style="font-size: 16px; font-weight: bold; color: ${amountColor};">${fmt(Math.abs(balance.balance))}</div>
                        <div style="font-size: 11px; color: ${amountColor};">${label}</div>
                    </div>
                </div>
            </div>
        `;
    });

    html += '</div>';
    container.innerHTML = html;
}

/**
 * Sync Splitwise and update pocket balances
 */
function syncSplitwiseNow() {
    const button = event.target;
    button.disabled = true;
    button.textContent = '⏳ Syncing...';

    fetch('/api/splitwise/sync-now', {method: 'POST'})
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                appAlert('Sync complete!');
                loadSplitwiseSetup();  // Reload
                loadGoals(true);  // Refresh pockets
            } else {
                appAlert(data.error || 'Sync failed');
            }
        })
        .catch(err => {
            console.error('Error syncing:', err);
            appAlert('Sync error');
        })
        .finally(() => {
            button.disabled = false;
            button.textContent = '🔄 Sync Now';
        });
}

/**
 * Disconnect Splitwise and delete all pockets
 */
function disconnectSplitwise() {
    if (!confirm('Disconnect Splitwise? This will delete all friend pockets and return money to Checking.')) {
        return;
    }

    fetch('/api/splitwise/disconnect', {method: 'POST'})
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                appAlert('Splitwise disconnected');
                loadSplitwiseSetup();  // Reload
                loadGoals(true);  // Refresh pockets
            } else {
                appAlert(data.error || 'Error disconnecting');
            }
        })
        .catch(err => {
            console.error('Error disconnecting:', err);
            appAlert('Error disconnecting Splitwise');
        });
}
