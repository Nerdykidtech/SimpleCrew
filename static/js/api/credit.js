/**
 * @file credit.js
 * @description API layer for credit card integration (SimpleFin and LunchFlow)
 * @requires utils/formatters.js (fmt function)
 * @requires state.js (selectedProvider, simpleFinAccessUrl, pendingAccountId, pendingAccountName, creditCardRefreshInterval)
 * @requires ui/dialogs.js (appAlert, appConfirm)
 * @requires ui/rendering.js (renderAccountCards, updateLastSyncDisplay)
 */

// Note: All state variables (selectedProvider, simpleFinAccessUrl, etc.) are defined in state.js

/**
 * Show provider selection screen
 */
function showProviderSelection() {
    document.getElementById('provider-selection').style.display = 'block';
    document.getElementById('lunchflow-setup').style.display = 'none';
    document.getElementById('simplefin-setup').style.display = 'none';
    document.getElementById('simplefin-account-selection').style.display = 'none';
}

/**
 * Select a credit card provider (lunchflow or simplefin)
 * @param {string} provider - The provider name ('lunchflow' or 'simplefin')
 */
function selectProvider(provider) {
    selectedProvider = provider;

    // Hide provider selection
    document.getElementById('provider-selection').style.display = 'none';

    if (provider === 'lunchflow') {
        // Show LunchFlow setup
        document.getElementById('lunchflow-setup').style.display = 'block';
        document.getElementById('simplefin-setup').style.display = 'none';
        loadLunchFlowAccounts();
    } else if (provider === 'simplefin') {
        // Show SimpleFin setup
        document.getElementById('simplefin-setup').style.display = 'block';
        document.getElementById('lunchflow-setup').style.display = 'none';

        // Fetch SimpleFin access URL and load accounts
        fetch('/api/simplefin/get-access-url')
            .then(res => res.json())
            .then(data => {
                if (data.success && data.accessUrl) {
                    simpleFinAccessUrl = data.accessUrl;
                    loadSimpleFinAccounts();
                } else {
                    appAlert('Error: SimpleFin access URL not found. Please reconnect SimpleFin.', 'Error');
                }
            })
            .catch(err => {
                console.error('Error fetching SimpleFin access URL:', err);
                appAlert('Error loading SimpleFin: ' + err.message, 'Error');
            });
    }
}

/**
 * Load credit card setup screen - determines what state to show
 */
function loadCreditSetup() {
    // Check status first
    fetch('/api/lunchflow/credit-card-status')
        .then(res => res.json())
        .then(status => {
            const providerSelection = document.getElementById('provider-selection');
            const lunchflowSetup = document.getElementById('lunchflow-setup');
            const simplefinSetup = document.getElementById('simplefin-setup');
            const setupScreen = document.getElementById('credit-setup-screen');
            const managementScreen = document.getElementById('credit-management-screen');
            const simplefinAccountSelection = document.getElementById('simplefin-account-selection');

            // Check if we have SimpleFin accounts (multi-account mode)
            const hasSimpleFinAccounts = status.accounts && status.accounts.length > 0;

            if (hasSimpleFinAccounts) {
                // Show multi-account management screen
                setupScreen.style.display = 'none';
                managementScreen.style.display = 'block';
                if (simplefinAccountSelection) {
                    simplefinAccountSelection.style.display = 'none';
                }
                loadCreditAccounts(status);
            } else if (status.configured && status.pocketCreated) {
                // Fully configured (legacy single account) - show management interface
                setupScreen.style.display = 'none';
                managementScreen.style.display = 'block';
                if (simplefinAccountSelection) {
                    simplefinAccountSelection.style.display = 'none';
                }
                loadCreditManagement(status);
            } else if (status.configured && !status.pocketCreated) {
                // Account selected but pocket not created - show balance sync modal
                setupScreen.style.display = 'block';
                managementScreen.style.display = 'none';
                providerSelection.style.display = 'none';
                lunchflowSetup.style.display = 'none';
                simplefinSetup.style.display = 'none';

                // Restore pending account info and show balance sync modal
                pendingAccountId = status.accountId;
                pendingAccountName = status.accountName;
                selectedProvider = status.provider || 'lunchflow';

                // Fetch balance based on provider
                if (status.provider === 'simplefin') {
                    // For SimpleFin, get access URL and fetch balance
                    if (status.hasSimplefinAccessUrl) {
                        fetch('/api/simplefin/get-access-url')
                            .then(res => res.json())
                            .then(data => {
                                if (data.success && data.accessUrl) {
                                    simpleFinAccessUrl = data.accessUrl;
                                    // Fetch balance
                                    return fetch('/api/simplefin/get-balance', {
                                        method: 'POST',
                                        headers: {'Content-Type': 'application/json'},
                                        body: JSON.stringify({
                                            accountId: status.accountId,
                                            accessUrl: simpleFinAccessUrl
                                        })
                                    });
                                } else {
                                    throw new Error('No access URL found');
                                }
                            })
                            .then(res => res.json())
                            .then(balanceData => {
                                if (balanceData.error) {
                                    showBalanceSyncModal(null);
                                } else {
                                    const balance = Math.abs(balanceData.balance?.amount || 0);
                                    showBalanceSyncModal(balance);
                                }
                            })
                            .catch(err => {
                                console.error('Error fetching SimpleFin balance:', err);
                                showBalanceSyncModal(null);
                            });
                    } else {
                        showBalanceSyncModal(null);
                    }
                } else {
                    // LunchFlow balance fetch
                    fetch(`/api/lunchflow/get-balance/${status.accountId}`)
                        .then(res => res.json())
                        .then(balanceData => {
                            if (balanceData.error) {
                                showBalanceSyncModal(null);
                            } else {
                                const balanceAmount = balanceData.balance?.amount || 0;
                                const balance = Math.abs(balanceAmount);
                                showBalanceSyncModal(balance);
                            }
                        })
                        .catch(err => {
                            console.error('Error fetching balance:', err);
                            showBalanceSyncModal(null);
                        });
                }
            } else {
                // Not configured - show provider selection
                setupScreen.style.display = 'block';
                managementScreen.style.display = 'none';
                providerSelection.style.display = 'block';
                lunchflowSetup.style.display = 'none';
                simplefinSetup.style.display = 'none';
            }
        })
        .catch(err => {
            console.error('Error loading credit card status:', err);
        });
}

/**
 * Show balance sync modal with optional balance
 * @param {number|null} balance - The balance to display, or null if unavailable
 */
function showBalanceSyncModal(balance) {
    const modal = document.getElementById('balance-sync-modal');
    const balanceAmountEl = document.getElementById('balance-amount');

    if (!modal || !balanceAmountEl) {
        console.error('Balance sync modal elements not found');
        return;
    }

    // Display balance or "Unavailable" message
    if (balance !== null && balance !== undefined) {
        balanceAmountEl.textContent = fmt(balance);
    } else {
        balanceAmountEl.textContent = 'Unavailable';
    }

    // Show the modal
    modal.style.display = 'flex';
}

/**
 * Load credit management screen (legacy single account)
 * @param {Object} status - Status object from API
 */
function loadCreditManagement(status) {
    // Show/hide invalid token warning for SimpleFin
    if (status.provider === 'simplefin' && status.simplefinTokenInvalid) {
        document.getElementById('credit-management-invalid-warning').style.display = 'block';
    } else {
        document.getElementById('credit-management-invalid-warning').style.display = 'none';
    }

    // Update account info
    document.getElementById('mgmt-account-name').textContent = status.accountName || 'Unknown';
    document.getElementById('mgmt-account-id').textContent = status.accountId || 'N/A';

    if (status.createdAt) {
        const date = new Date(status.createdAt);
        document.getElementById('mgmt-connected-date').textContent = date.toLocaleDateString();
    } else {
        document.getElementById('mgmt-connected-date').textContent = 'Unknown';
    }

    // Clear any existing intervals
    if (creditCardRefreshInterval) {
        clearInterval(creditCardRefreshInterval);
    }

    // Load pocket information immediately
    refreshCreditCardBalance(status);

    // Set up periodic refresh every hour (silent background check)
    creditCardRefreshInterval = setInterval(() => {
        refreshCreditCardBalance(status);
    }, 3600000); // 1 hour
}

/**
 * Refresh credit card balance display
 * @param {Object} status - Status object from API
 */
function refreshCreditCardBalance(status) {
    if (status.pocketId) {
        // Fetch goals to get pocket data (force refresh to get latest balance)
        fetch('/api/goals?refresh=true')
            .then(res => res.json())
            .then(data => {
                if (data.goals) {
                    const creditCardPocket = data.goals.find(g => g.id === status.pocketId);
                    if (creditCardPocket) {
                        document.getElementById('credit-pocket-name').textContent = creditCardPocket.name;
                        document.getElementById('credit-pocket-balance').textContent = fmt(creditCardPocket.balance);
                        document.getElementById('credit-pocket-account').textContent = `Tracking: ${status.accountName}`;
                    }
                }
            })
            .catch(err => console.error('Error loading pocket data:', err));
    }
}

/**
 * Clean up intervals when leaving credit page
 */
function cleanupCreditCardIntervals() {
    if (creditCardRefreshInterval) {
        clearInterval(creditCardRefreshInterval);
        creditCardRefreshInterval = null;
    }
    stopTransactionAutoRefresh();
}

// --- MULTI-ACCOUNT MANAGEMENT FUNCTIONS ---

/**
 * Load credit accounts (multi-account management screen)
 * @param {Object} status - Status object from API
 */
function loadCreditAccounts(status) {
    // Show/hide invalid token warning for SimpleFin
    if (status && status.simplefinTokenInvalid) {
        document.getElementById('credit-management-invalid-warning').style.display = 'block';
    } else {
        document.getElementById('credit-management-invalid-warning').style.display = 'none';
    }

    // Fetch latest status to get accounts array and last sync time
    fetch('/api/lunchflow/credit-card-status')
        .then(res => res.json())
        .then(data => {
            const accounts = data.accounts || [];
            renderAccountCards(accounts);

            // Update last sync timestamp
            updateLastSyncDisplay(data.lastSync);
        });

    // Load sync schedule settings
    loadSyncScheduleSettings();
}

/**
 * Load LunchFlow accounts for selection
 */
function loadLunchFlowAccounts() {
    const accountsList = document.getElementById('accounts-list');
    accountsList.innerHTML = '<div style="text-align: center; padding: 40px; color: #999;"><div class="spinner" style="border: 3px solid #f3f3f3; border-top: 3px solid var(--simple-blue); border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 16px;"></div>Loading accounts...</div>';

    fetch('/api/lunchflow/accounts')
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                accountsList.innerHTML = `<div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 20px; color: #721c24;">
                    <strong>Error loading accounts:</strong> ${data.error}
                </div>`;
                return;
            }

            // LunchFlow API returns { accounts: [...], total: number }
            const accounts = data.accounts || [];

            if (accounts.length === 0) {
                accountsList.innerHTML = '<div style="text-align: center; padding: 40px; color: #999;">No accounts found. Please connect an account in your LunchFlow dashboard first.</div>';
                return;
            }

            let html = '<div style="display: flex; flex-direction: column; gap: 12px;">';
            accounts.forEach(account => {
                // Schema: id (number), name, institution_name, provider, status, currency, institution_logo
                const accountId = account.id;
                const accountName = account.name || 'Unknown Account';
                const institutionName = account.institution_name || '';
                const provider = account.provider || '';
                const status = account.status || 'ACTIVE';
                const currency = account.currency || '';
                const logo = account.institution_logo || null;

                // Filter to only show ACTIVE accounts, or show all with status badge
                const statusColor = status === 'ACTIVE' ? '#28a745' : status === 'ERROR' ? '#dc3545' : '#6c757d';

                html += `
                    <div style="background: white; border: 2px solid ${status === 'ACTIVE' ? '#e0e0e0' : '#f0f0f0'}; border-radius: 8px; padding: 20px; cursor: ${status === 'ACTIVE' ? 'pointer' : 'not-allowed'}; transition: all 0.2s; opacity: ${status === 'ACTIVE' ? '1' : '0.7'};"
                         ${status === 'ACTIVE' ? `onclick="selectCreditCardAccount(${accountId}, '${accountName.replace(/'/g, "\\'")}')"` : ''}
                         ${status === 'ACTIVE' ? 'onmouseover="this.style.borderColor=\'var(--simple-blue)\'; this.style.boxShadow=\'0 2px 8px rgba(0,0,0,0.1)\'"' : ''}
                         ${status === 'ACTIVE' ? 'onmouseout="this.style.borderColor=\'#e0e0e0\'; this.style.boxShadow=\'none\'"' : ''}>
                        <div style="display: flex; justify-content: space-between; align-items: center; gap: 16px;">
                            ${logo ? `<img src="${logo}" alt="${institutionName}" style="width: 40px; height: 40px; object-fit: contain; border-radius: 4px; background: #f8f9fa; padding: 4px;">` : '<div style="width: 40px; height: 40px; background: #e9ecef; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 20px;">🏦</div>'}
                            <div style="flex: 1;">
                                <div style="font-weight: 600; color: var(--text-dark); margin-bottom: 4px;">${accountName}</div>
                                <div style="font-size: 13px; color: var(--text-light); display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                                    ${institutionName ? `<span>${institutionName}</span>` : ''}
                                    ${provider ? `<span>• ${provider}</span>` : ''}
                                    ${currency ? `<span>• ${currency}</span>` : ''}
                                    <span style="background: ${statusColor}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; text-transform: uppercase;">${status}</span>
                                </div>
                            </div>
                            ${status === 'ACTIVE' ? '<div style="font-size: 24px; color: var(--simple-blue);">→</div>' : '<div style="font-size: 14px; color: #999;">Not available</div>'}
                        </div>
                    </div>
                `;
            });
            html += '</div>';

            accountsList.innerHTML = html;
        })
        .catch(err => {
            accountsList.innerHTML = `<div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 20px; color: #721c24;">
                <strong>Error:</strong> ${err.message}
            </div>`;
        });
}

/**
 * Select a credit card account (LunchFlow)
 * @param {string|number} accountId - The account ID
 * @param {string} accountName - The account name
 */
function selectCreditCardAccount(accountId, accountName) {
    pendingAccountId = String(accountId);
    pendingAccountName = accountName;

    // First, save the account selection
    fetch('/api/lunchflow/set-credit-card', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            accountId: pendingAccountId,
            accountName: pendingAccountName
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.error) {
            appAlert('Error saving account: ' + data.error, 'Error');
            return;
        }

        // Fetch balance and show sync confirmation modal
        fetch(`/api/lunchflow/get-balance/${pendingAccountId}`)
            .then(res => res.json())
            .then(balanceData => {
                if (balanceData.error) {
                    // If balance fetch fails, still proceed but without balance display
                    showBalanceSyncModal(null);
                } else {
                    const balanceAmount = balanceData.balance?.amount || 0;
                    // Balance is already in dollars, not cents
                    const balance = Math.abs(balanceAmount);
                    showBalanceSyncModal(balance);
                }
            })
            .catch(err => {
                console.error('Error fetching balance:', err);
                showBalanceSyncModal(null);
            });
    })
    .catch(err => {
        appAlert('Error: ' + err.message, 'Error');
    });
}

// --- SIMPLEFIN FUNCTIONS ---

/**
 * Claim SimpleFin setup token
 */
function claimSimpleFinToken() {
    const token = document.getElementById('simplefin-token-field').value.trim();
    if (!token) {
        appAlert('Please enter a SimpleFin token', 'Error');
        return;
    }

    console.log('Claiming SimpleFin setup token...');
    fetch('/api/simplefin/claim-token', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token})
    })
    .then(res => res.json())
    .then(data => {
        console.log('SimpleFin claim token result:', data);
        if (data.error) {
            appAlert('Error claiming token: ' + data.error, 'Error');
            return;
        }
        console.log('✅ SimpleFin token claimed successfully, access URL stored');
        simpleFinAccessUrl = data.accessUrl;
        // Now fetch accounts
        loadSimpleFinAccounts();
    })
    .catch(err => {
        console.error('❌ Error claiming SimpleFin token:', err);
        appAlert('Error: ' + err.message, 'Error');
    });
}

/**
 * Load SimpleFin accounts for selection
 */
function loadSimpleFinAccounts() {
    console.log('loadSimpleFinAccounts called');
    const tokenInput = document.getElementById('simplefin-token-input');
    const accountSelection = document.getElementById('simplefin-account-selection');
    const accountsList = document.getElementById('simplefin-accounts-list');

    console.log('Elements found:', {
        tokenInput: !!tokenInput,
        accountSelection: !!accountSelection,
        accountsList: !!accountsList
    });

    if (tokenInput) tokenInput.style.display = 'none';
    if (accountSelection) accountSelection.style.display = 'block';

    if (!accountsList) {
        console.error('simplefin-accounts-list element not found!');
        return;
    }

    accountsList.innerHTML = '<div style="text-align: center; padding: 40px; color: #999;"><div class="spinner" style="border: 3px solid #f3f3f3; border-top: 3px solid var(--simple-blue); border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 16px;"></div>Loading accounts...</div>';

    // Ensure we have the access URL
    if (!simpleFinAccessUrl) {
        console.error('simpleFinAccessUrl is not set!');
        accountsList.innerHTML = '<div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 20px; color: #721c24;"><strong>Error:</strong> SimpleFin access URL not found. Please reconnect SimpleFin.</div>';
        return;
    }

    console.log('Fetching accounts with access URL (length):', simpleFinAccessUrl.length);
    fetch('/api/simplefin/accounts', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({accessUrl: simpleFinAccessUrl})
    })
    .then(res => {
        console.log('Accounts API response status:', res.status);
        return res.json();
    })
    .then(data => {
        console.log('Accounts API response data:', data);
        if (data.error) {
            accountsList.innerHTML = `<div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 20px; color: #721c24;">
                <strong>Error loading accounts:</strong> ${data.error}
            </div>`;
            return;
        }

        const accounts = data.accounts || [];
        if (accounts.length === 0) {
            accountsList.innerHTML = '<div style="text-align: center; padding: 40px; color: #999;">No accounts found.</div>';
            return;
        }

        console.log('Building account list HTML for', accounts.length, 'accounts');
        let html = '<div style="display: flex; flex-direction: column; gap: 12px;">';
        accounts.forEach(account => {
            const accountId = account.id;
            const accountName = account.name || 'Unknown Account';
            const org = account.org || '';
            const currency = account.currency || '';
            const balance = account.balance || 0;

            html += `
                <div style="background: white; border: 2px solid #e0e0e0; border-radius: 8px; padding: 20px; cursor: pointer; transition: all 0.2s;"
                     onclick="selectSimpleFinAccount('${accountId}', '${accountName.replace(/'/g, "\\'")}')"
                     onmouseover="this.style.borderColor='var(--simple-blue)'; this.style.boxShadow='0 2px 8px rgba(0,0,0,0.1)'"
                     onmouseout="this.style.borderColor='#e0e0e0'; this.style.boxShadow='none'">
                    <div style="display: flex; justify-content: space-between; align-items: center; gap: 16px;">
                        <div style="width: 40px; height: 40px; background: #e9ecef; border-radius: 4px; display: flex; align-items: center; justify-content: center; font-size: 20px;">🏦</div>
                        <div style="flex: 1;">
                            <div style="font-weight: 600; color: var(--text-dark); margin-bottom: 4px;">${accountName}</div>
                            <div style="font-size: 13px; color: var(--text-light); display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                                ${org ? `<span>${org}</span>` : ''}
                                ${currency ? `<span>• ${currency}</span>` : ''}
                                ${balance ? `<span>• Balance: ${fmt(Math.abs(balance))}</span>` : ''}
                            </div>
                        </div>
                        <div style="font-size: 24px; color: var(--simple-blue);">→</div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        console.log('Setting innerHTML for accountsList element');
        console.log('accountsList element:', accountsList);
        console.log('HTML length:', html.length);
        accountsList.innerHTML = html;
        console.log('innerHTML set successfully');
    })
    .catch(err => {
        accountsList.innerHTML = `<div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 20px; color: #721c24;">
            <strong>Error:</strong> ${err.message}
        </div>`;
    });
}

/**
 * Select a SimpleFin account
 * @param {string} accountId - The account ID
 * @param {string} accountName - The account name
 */
function selectSimpleFinAccount(accountId, accountName) {
    // Check if account already tracked
    fetch('/api/lunchflow/credit-card-status')
        .then(res => res.json())
        .then(statusData => {
            const existingAccount = statusData.accounts?.find(a => a.accountId === accountId);
            if (existingAccount) {
                appAlert('This account is already being tracked!', 'Info');
                return Promise.reject(new Error('Account already tracked'));
            }

            pendingAccountId = accountId;
            pendingAccountName = accountName;

            // Save the account selection
            return fetch('/api/simplefin/set-credit-card', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    accountId: pendingAccountId,
                    accountName: pendingAccountName,
                    accessUrl: simpleFinAccessUrl
                })
            });
        })
        .then(res => res ? res.json() : null)
        .then(data => {
            if (!data) return;

            if (data.error) {
                appAlert('Error saving account: ' + data.error, 'Error');
                return;
            }

            // Fetch balance and show sync confirmation modal
            return fetch('/api/simplefin/get-balance', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    accountId: pendingAccountId,
                    accessUrl: simpleFinAccessUrl
                })
            });
        })
        .then(res => res ? res.json() : null)
        .then(balanceData => {
            if (!balanceData) return;

            if (balanceData.error) {
                showBalanceSyncModal(null);
            } else {
                const balance = Math.abs(balanceData.balance?.amount || 0);
                showBalanceSyncModal(balance);
            }
        })
        .catch(err => {
            if (err.message !== 'Account already tracked') {
                console.error('Error:', err);
                appAlert('Error: ' + err.message, 'Error');
            }
        });
}

/**
 * Disconnect SimpleFin integration
 */
function disconnectSimpleFin() {
    if (!confirm('Are you sure you want to disconnect SimpleFin? This will remove all SimpleFin accounts and return any pocket funds to your Checking account.')) {
        return;
    }

    fetch('/api/simplefin/disconnect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            appAlert(data.message || 'SimpleFin disconnected successfully', 'Success');
            // Refresh the page to show clean state
            setTimeout(() => window.location.reload(), 1500);
        } else {
            appAlert(data.error || 'Failed to disconnect SimpleFin', 'Error');
        }
    })
    .catch(err => {
        console.error('Error disconnecting SimpleFin:', err);
        appAlert('Error: ' + err.message, 'Error');
    });
}

/**
 * Add another SimpleFin account
 */
function addAnotherSimpleFinAccount() {
    // Set provider to simplefin
    selectedProvider = 'simplefin';

    // Return to account selection screen
    document.getElementById('credit-management-screen').style.display = 'none';
    document.getElementById('credit-setup-screen').style.display = 'block';
    document.getElementById('provider-selection').style.display = 'none';
    document.getElementById('simplefin-setup').style.display = 'block';

    // Fetch SimpleFin access URL first, then load accounts
    console.log('Fetching SimpleFin access URL...');
    fetch('/api/simplefin/get-access-url')
        .then(res => res.json())
        .then(data => {
            console.log('Access URL response:', data);
            if (data.success && data.accessUrl) {
                simpleFinAccessUrl = data.accessUrl;
                console.log('Access URL set, loading accounts...');
                loadSimpleFinAccounts();
            } else {
                console.error('No access URL in response');
                appAlert('Error: SimpleFin access URL not found. Please reconnect SimpleFin.', 'Error');
                // Go back to setup
                document.getElementById('credit-setup-screen').style.display = 'block';
                document.getElementById('provider-selection').style.display = 'block';
                document.getElementById('simplefin-setup').style.display = 'none';
            }
        })
        .catch(err => {
            console.error('Error fetching access URL:', err);
            appAlert('Error loading SimpleFin accounts: ' + err.message, 'Error');
        });
}

/**
 * Handle balance sync decision
 * @param {boolean} syncBalance - Whether to sync balance or start at zero
 */
function handleBalanceSync(syncBalance) {
    const modal = document.getElementById('balance-sync-modal');

    if (!pendingAccountId) {
        appAlert('Error: No account selected', 'Error');
        return;
    }

    // Show loading state in modal
    const modalContent = modal.querySelector('div[style*="background: white"]');
    const originalContent = modalContent.innerHTML;
    modalContent.innerHTML = `
        <div style="text-align: center; padding: 60px 40px;">
            <div class="spinner" style="border: 4px solid #f3f3f3; border-top: 4px solid var(--simple-blue); border-radius: 50%; width: 60px; height: 60px; animation: spin 1s linear infinite; margin: 0 auto 24px;"></div>
            <h3 style="font-size: 20px; font-weight: 600; margin-bottom: 12px; color: var(--text-dark);">Setting up your account...</h3>
            <p style="color: var(--text-light); line-height: 1.6;">
                ${syncBalance ? 'Syncing balance and importing transactions. This may take a moment.' : 'Creating pocket and importing transactions. This may take a moment.'}
            </p>
        </div>
    `;

    // Determine which API to use based on selected provider
    const endpoint = selectedProvider === 'simplefin'
        ? '/api/simplefin/create-pocket-with-balance'
        : '/api/lunchflow/create-pocket-with-balance';

    // Create pocket with or without balance sync
    fetch(endpoint, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            accountId: pendingAccountId,
            syncBalance: syncBalance
        })
    })
    .then(res => res.json())
    .then(data => {
        modal.style.display = 'none';
        modalContent.innerHTML = originalContent;

        if (data.error) {
            appAlert('Error creating pocket: ' + data.error, 'Error');
            return;
        }

        // Show success message with details
        if (syncBalance && data.syncedBalance) {
            appAlert(`✅ Account added successfully!\n\nThe pocket balance has been synced to match your current credit card balance. Transactions have been imported and will be tracked going forward.`, 'Success');
        } else {
            appAlert(`✅ Account added successfully!\n\nThe pocket starts at $0. Transactions will be tracked going forward, and new spending will automatically adjust the pocket balance.`, 'Success');
        }

        // Refresh Safe to Spend balance and goals/pockets list after pocket creation
        if (typeof initBalances === 'function') {
            initBalances(true);
        }
        if (typeof loadGoals === 'function') {
            loadGoals(true);
        }

        // Reload to show management interface
        loadCreditSetup();

        // Clear pending values
        pendingAccountId = null;
        pendingAccountName = null;
    })
    .catch(err => {
        modal.style.display = 'none';
        modalContent.innerHTML = originalContent;
        appAlert('Error: ' + err.message, 'Error');
    });
}

/**
 * Sync account balance for a specific account
 * @param {string} accountId - The account ID to sync
 */
function syncAccountBalance(accountId) {
    appConfirm('⚠️ WARNING: This will sync your credit card pocket balance to match your current credit card balance. This will move money from Safe-to-Spend to the pocket (or vice versa).\n\nDo you want to continue?', 'Sync Balance', { confirmText: 'Continue' }).then(confirmed => {
        if (!confirmed) return;

        // Show loading indicator
        const loadingModal = document.createElement('div');
        loadingModal.style = 'position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 10000;';
        loadingModal.innerHTML = `
            <div style="background: white; border-radius: 16px; padding: 40px; text-align: center; max-width: 400px;">
                <div class="spinner" style="border: 4px solid #f3f3f3; border-top: 4px solid var(--simple-blue); border-radius: 50%; width: 60px; height: 60px; animation: spin 1s linear infinite; margin: 0 auto 24px;"></div>
                <h3 style="font-size: 20px; font-weight: 600; margin-bottom: 12px; color: var(--text-dark);">Syncing Balance...</h3>
                <p style="color: var(--text-light);">Fetching current balance from SimpleFin</p>
            </div>
        `;
        document.body.appendChild(loadingModal);

        fetch('/api/simplefin/sync-balance', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({accountId: accountId})
        })
        .then(res => res.json())
        .then(data => {
            loadingModal.remove();
            if (data.error) {
                appAlert('Error syncing balance: ' + data.error, 'Error');
            } else {
                appAlert(`✅ Balance synced successfully!\n\nPrevious: ${fmt(data.previousBalance)}\nNew: ${fmt(data.targetBalance)}`, 'Success');
                loadCreditSetup();
                if (typeof loadGoals === 'function') loadGoals(true);
                if (typeof initBalances === 'function') initBalances(true);
            }
        })
        .catch(err => {
            loadingModal.remove();
            appAlert('Error syncing balance: ' + err.message, 'Error');
        });
    });
}

/**
 * Sync credit balance (legacy single account)
 */
function syncCreditBalance() {
    fetch('/api/lunchflow/credit-card-status')
        .then(res => res.json())
        .then(status => {
            if (!status.accountId) {
                appAlert('No credit card account configured', 'Error');
                return;
            }

            const provider = status.provider || 'lunchflow';
            const syncEndpoint = provider === 'simplefin' ? '/api/simplefin/sync-balance' : '/api/lunchflow/sync-balance';

            appConfirm('⚠️ WARNING: This will sync your credit card pocket balance to match your current credit card balance. This will move money from Safe-to-Spend to the pocket (or vice versa).\n\nDo you want to continue?', 'Sync Balance', { confirmText: 'Continue' }).then(confirmed => {
                if (!confirmed) return;
                fetch(syncEndpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ accountId: status.accountId })
                })
                .then(res => res.json())
                .then(data => {
                    if (data.error) {
                        appAlert('Error syncing balance: ' + data.error, 'Error');
                    } else {
                        appAlert(`✅ Balance synced successfully!\n\nPrevious: ${fmt(data.previousBalance)}\nNew: ${fmt(data.targetBalance)}`, 'Success');
                        loadCreditSetup();
                        if (typeof loadGoals === 'function') loadGoals(true);
                        if (typeof initBalances === 'function') initBalances(true);
                    }
                })
                .catch(err => appAlert('Error: ' + err.message, 'Error'));
            });
        });
}

/**
 * Change credit account (legacy single account)
 */
function changeCreditAccount() {
    const warningMessage = `⚠️ WARNING: Changing the credit card account will:\n\n` +
                          `1. DELETE the current credit card pocket\n` +
                          `2. Return all money from the pocket back to Safe-to-Spend\n` +
                          `3. Remove all transaction history for this account\n` +
                          `4. Require you to select a new account and create a new pocket\n\n` +
                          `Are you sure you want to change accounts?`;

    appConfirm(warningMessage, 'Change Account', { confirmText: 'Change Account', danger: true }).then(confirmed => {
        if (!confirmed) return;

        // Check provider to use correct endpoint
        fetch('/api/lunchflow/credit-card-status')
            .then(res => res.json())
            .then(status => {
                const provider = status.provider || 'lunchflow';
                const endpoint = provider === 'simplefin' ? '/api/simplefin/change-account' : '/api/lunchflow/change-account';

                fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                })
                .then(res => res.json())
                .then(data => {
                    if (data.error) {
                        appAlert('Error changing account: ' + data.error, 'Error');
                    } else {
                        pendingAccountId = null;
                        pendingAccountName = null;
                        selectedProvider = null;
                        simpleFinAccessUrl = null;

                        const balanceSyncModal = document.getElementById('balance-sync-modal');
                        if (balanceSyncModal) {
                            balanceSyncModal.style.display = 'none';
                        }

                        appAlert('✅ Account changed successfully!\n\nThe pocket has been deleted and funds returned to Safe-to-Spend. Please select a new account.', 'Success');
                        setTimeout(() => {
                            loadCreditSetup();
                            if (typeof loadGoals === 'function') loadGoals(true);
                            if (typeof initBalances === 'function') initBalances(true);
                        }, 100);
                    }
                })
                .catch(err => appAlert('Error: ' + err.message, 'Error'));
            });
    });
}

/**
 * Stop credit card tracking entirely
 */
function stopCreditTracking() {
    const warningMessage = `⚠️ WARNING: Stopping credit card tracking will:\n\n` +
                          `1. DELETE the credit card pocket permanently\n` +
                          `2. Return all money from the pocket back to Safe-to-Spend\n` +
                          `3. Remove all credit card transaction history\n` +
                          `4. Remove all credit card configuration\n\n` +
                          `You will need to set up credit card tracking again from scratch if you want to use it later.\n\n` +
                          `Are you absolutely sure you want to stop tracking?`;

    appConfirm(warningMessage, 'Stop Tracking', { confirmText: 'Stop Tracking', danger: true }).then(confirmed => {
        if (!confirmed) return;

        // Check provider to use correct endpoint
        fetch('/api/lunchflow/credit-card-status')
            .then(res => res.json())
            .then(status => {
                const provider = status.provider || 'lunchflow';
                const endpoint = provider === 'simplefin' ? '/api/simplefin/stop-tracking' : '/api/lunchflow/stop-tracking';

                fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                })
                .then(res => res.json())
                .then(data => {
                    if (data.error) {
                        appAlert('Error stopping tracking: ' + data.error, 'Error');
                    } else {
                        appAlert('✅ Credit card tracking stopped.\n\nThe pocket has been deleted and funds returned to Safe-to-Spend.', 'Success');
                        cleanupCreditCardIntervals();
                        selectedProvider = null;
                        simpleFinAccessUrl = null;
                        pendingAccountId = null;
                        pendingAccountName = null;
                        loadCreditSetup();
                        if (typeof loadGoals === 'function') loadGoals(true);
                        if (typeof initBalances === 'function') initBalances(true);
                    }
                })
                .catch(err => appAlert('Error: ' + err.message, 'Error'));
            });
    });
}

/**
 * Remove a specific credit account (multi-account mode)
 * @param {string} accountId - The account ID to remove
 */
function removeAccount(accountId) {
    const warningMessage = `⚠️ WARNING: Removing this credit card account will:\n\n` +
                          `1. DELETE the credit card pocket permanently\n` +
                          `2. Return all money from the pocket back to Safe-to-Spend\n` +
                          `3. Remove all transaction history for this account\n\n` +
                          `Are you sure you want to remove this account?`;

    appConfirm(warningMessage, 'Remove Account', { confirmText: 'Remove Account', danger: true }).then(confirmed => {
        if (!confirmed) return;

        // Show loading indicator
        const loadingModal = document.createElement('div');
        loadingModal.style = 'position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 10000;';
        loadingModal.innerHTML = `
            <div style="background: white; border-radius: 16px; padding: 40px; text-align: center; max-width: 400px;">
                <div class="spinner" style="border: 4px solid #f3f3f3; border-top: 4px solid var(--simple-blue); border-radius: 50%; width: 60px; height: 60px; animation: spin 1s linear infinite; margin: 0 auto 24px;"></div>
                <h3 style="font-size: 20px; font-weight: 600; margin-bottom: 12px; color: var(--text-dark);">Removing Account...</h3>
                <p style="color: var(--text-light);">Deleting pocket and returning funds</p>
            </div>
        `;
        document.body.appendChild(loadingModal);

        fetch('/api/simplefin/stop-tracking', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({accountId: accountId})
        })
        .then(res => res.json())
        .then(data => {
            loadingModal.remove();
            if (data.error) {
                appAlert('Error removing account: ' + data.error, 'Error');
            } else {
                appAlert('✅ Account removed successfully!\n\nThe pocket has been deleted and funds returned to Safe-to-Spend.', 'Success');
                loadCreditSetup();
                if (typeof loadGoals === 'function') loadGoals(true);
                if (typeof initBalances === 'function') initBalances(true);
            }
        })
        .catch(err => {
            loadingModal.remove();
            appAlert('Error removing account: ' + err.message, 'Error');
        });
    });
}

/**
 * View transactions for a specific credit card account
 * @param {string} accountId - The account ID to view transactions for
 */
function viewAccountTransactions(accountId) {
    // Switch to Activity tab
    if (typeof switchTab === 'function') {
        switchTab('activity');
    }

    // Scroll to transactions view
    const activityView = document.getElementById('view-activity');
    if (activityView) {
        activityView.scrollIntoView({ behavior: 'smooth' });
    }

    // Set search filter to account ID
    const searchInput = document.getElementById('search-input-box');
    if (searchInput) {
        searchInput.value = accountId;
        if (filterState) {
            filterState.q = accountId;
        }

        // Trigger transaction reload with filter
        if (typeof reloadTx === 'function') {
            reloadTx();
        }
    }
}

/**
 * Get schedule presets - maps preset keys to local times
 */
function getSchedulePresets() {
    return {
        'morning': ['06:00'],
        'afternoon': ['12:00'],
        'evening': ['18:00'],
        'morning-evening': ['06:00', '18:00'],
        'three-times': ['06:00', '12:00', '18:00'],
        'business-hours': ['09:00', '12:00', '15:00', '17:00']
    };
}

/**
 * Convert local times to UTC times
 */
function localTimesToUTC(localTimes) {
    const now = new Date();
    const utcTimes = [];

    for (const localTime of localTimes) {
        const [hours, minutes] = localTime.split(':').map(Number);

        // Create date with local time
        const localDate = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hours, minutes, 0);

        // Get UTC hours and minutes
        const utcHours = localDate.getUTCHours().toString().padStart(2, '0');
        const utcMinutes = localDate.getUTCMinutes().toString().padStart(2, '0');

        utcTimes.push(`${utcHours}:${utcMinutes}`);
    }

    return utcTimes;
}

/**
 * Convert UTC times to local times
 */
function utcTimesToLocal(utcTimes) {
    const now = new Date();
    const localTimes = [];

    for (const utcTime of utcTimes) {
        const [hours, minutes] = utcTime.split(':').map(Number);

        // Create UTC date
        const utcDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hours, minutes, 0));

        // Get local hours and minutes
        const localHours = utcDate.getHours().toString().padStart(2, '0');
        const localMinutes = utcDate.getMinutes().toString().padStart(2, '0');

        localTimes.push(`${localHours}:${localMinutes}`);
    }

    return localTimes;
}

/**
 * Detect which preset matches the given local times
 */
function detectPreset(localTimes) {
    const presets = getSchedulePresets();

    for (const [presetKey, presetTimes] of Object.entries(presets)) {
        if (JSON.stringify(presetTimes.sort()) === JSON.stringify(localTimes.sort())) {
            return presetKey;
        }
    }

    return 'morning-evening'; // Default fallback
}

/**
 * Load sync schedule settings and populate UI
 */
function loadSyncScheduleSettings() {
    fetch('/api/simplefin/sync-schedule')
        .then(res => res.json())
        .then(data => {
            if (data.success && data.syncTimes) {
                // Convert UTC times back to local times
                const localTimes = utcTimesToLocal(data.syncTimes);

                // Detect which preset this matches
                const preset = detectPreset(localTimes);

                const selectEl = document.getElementById('sync-schedule-select');
                if (selectEl) {
                    selectEl.value = preset;
                }

                updateScheduleInfo(localTimes);
            } else {
                // Default to morning-evening
                const selectEl = document.getElementById('sync-schedule-select');
                if (selectEl) {
                    selectEl.value = 'morning-evening';
                }
                updateScheduleInfo(['06:00', '18:00']);
            }
        })
        .catch(err => {
            console.error('Error loading sync schedule:', err);
            updateScheduleInfo(['06:00', '18:00']);
        });
}

/**
 * Update sync schedule setting
 */
function updateSyncSchedule() {
    const selectEl = document.getElementById('sync-schedule-select');
    const preset = selectEl.value;

    const presets = getSchedulePresets();
    const localTimes = presets[preset];

    // Convert to UTC
    const utcTimes = localTimesToUTC(localTimes);

    // Get user's timezone
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

    fetch('/api/simplefin/sync-schedule', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            syncTimes: utcTimes,
            syncTimezone: timezone
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            updateScheduleInfo(localTimes);
            appAlert('✅ Sync schedule updated successfully!', 'Success');
        } else {
            appAlert('Error updating sync schedule: ' + data.error, 'Error');
        }
    })
    .catch(err => {
        appAlert('Error: ' + err.message, 'Error');
    });
}

/**
 * Update the schedule info display
 */
function updateScheduleInfo(localTimes) {
    const infoEl = document.getElementById('sync-schedule-info');
    if (!infoEl) return;

    const now = new Date();
    const currentMinutes = now.getHours() * 60 + now.getMinutes();

    let nextSyncTime = null;
    let minDiff = Infinity;

    for (const timeStr of localTimes) {
        const [hours, minutes] = timeStr.split(':').map(Number);
        const scheduledMinutes = hours * 60 + minutes;

        let diff = scheduledMinutes - currentMinutes;
        if (diff < 0) diff += 1440; // Add 24 hours if time has passed today

        if (diff < minDiff) {
            minDiff = diff;
            nextSyncTime = timeStr;
        }
    }

    if (nextSyncTime) {
        const hours = Math.floor(minDiff / 60);
        const minutes = minDiff % 60;

        let timeUntil = '';
        if (hours > 0) {
            timeUntil = `${hours} hour${hours !== 1 ? 's' : ''}`;
            if (minutes > 0) {
                timeUntil += ` ${minutes} min`;
            }
        } else {
            timeUntil = `${minutes} minute${minutes !== 1 ? 's' : ''}`;
        }

        // Convert to 12-hour format
        const [h, m] = nextSyncTime.split(':').map(Number);
        const period = h >= 12 ? 'PM' : 'AM';
        const hour12 = h % 12 || 12;

        infoEl.textContent = `Next sync: ${hour12}:${m.toString().padStart(2, '0')} ${period} (in ${timeUntil})`;
    }
}

/**
 * Manually trigger sync now
 */
function syncNow() {
    const btn = document.getElementById('sync-now-btn');
    if (!btn) return;

    btn.disabled = true;
    btn.textContent = '⏳ Syncing...';

    fetch('/api/simplefin/sync-now', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            appAlert(`✅ ${data.message}`, 'Success');
            // Reload accounts after sync
            setTimeout(() => {
                if (typeof loadCreditAccountsData === 'function') {
                    loadCreditAccountsData();
                }
            }, 1000);
        } else {
            appAlert('Error syncing: ' + data.error, 'Error');
        }
    })
    .catch(err => {
        appAlert('Error: ' + err.message, 'Error');
    })
    .finally(() => {
        btn.disabled = false;
        btn.textContent = '🔄 Sync Now';
    });
}
