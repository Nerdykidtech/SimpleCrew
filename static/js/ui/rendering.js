/**
 * @file rendering.js
 * @description Rendering functions for UI elements (goals, accounts, groups, math updates)
 * @requires state.js - For global state variables
 * @requires utils.js - For formatting functions (fmt)
 * @requires ui/modals.js - For modal opening functions
 */

/**
 * Updates the last sync display timestamp for credit card accounts
 * @param {string} lastSync - ISO timestamp of last sync
 */
function updateLastSyncDisplay(lastSync) {
    const container = document.getElementById('credit-last-updated');
    if (!container) return;

    if (lastSync) {
        const syncDate = new Date(lastSync);
        const now = new Date();
        const diffMs = now - syncDate;
        const diffMins = Math.floor(diffMs / 60000);

        let timeAgo;
        if (diffMins < 1) {
            timeAgo = 'just now';
        } else if (diffMins < 60) {
            timeAgo = `${diffMins} minute${diffMins !== 1 ? 's' : ''} ago`;
        } else if (diffMins < 1440) {
            const hours = Math.floor(diffMins / 60);
            timeAgo = `${hours} hour${hours !== 1 ? 's' : ''} ago`;
        } else {
            const days = Math.floor(diffMins / 1440);
            timeAgo = `${days} day${days !== 1 ? 's' : ''} ago`;
        }

        container.textContent = `Last updated ${timeAgo}`;
        container.style.display = 'block';
    } else {
        container.style.display = 'none';
    }
}

/**
 * Renders credit card account cards with balances and controls
 * @param {Array} accounts - Array of credit card account objects
 */
function renderAccountCards(accounts) {
    const container = document.getElementById('credit-accounts-list');
    if (!container) return;

    container.innerHTML = '';

    if (accounts.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 40px; color: #999;">No SimpleFin accounts configured yet.</div>';
        return;
    }

    // Fetch pockets data to get balances
    fetch('/api/goals?refresh=true')
        .then(res => res.json())
        .then(data => {
            accounts.forEach(account => {
                // Find the pocket for this account
                const pocket = data.goals ? data.goals.find(g => g.id === account.pocketId) : null;
                const balance = pocket ? fmt(pocket.balance) : 'N/A';
                const pocketName = pocket ? pocket.name : 'Credit Card';

                const cardHtml = `
                    <div class="credit-account-card sidebar-card" data-account-id="${account.accountId}">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px;">
                            <div style="flex: 1;">
                                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                                    <span style="font-size: 18px; color: var(--simple-blue);">💳</span>
                                    <div style="font-weight: 600; color: var(--text-dark); font-size: 16px;">${account.accountName}</div>
                                </div>
                                <div style="font-size: 12px; color: var(--text-muted); font-family: monospace; background: var(--bg-elevated); padding: 4px 8px; border-radius: 4px; display: inline-block;">
                                    ${account.accountId}
                                </div>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-size: 12px; color: var(--text-light); margin-bottom: 4px;">Pocket Balance</div>
                                <div style="font-size: 20px; font-weight: 700; color: var(--text-dark);">${balance}</div>
                                <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">${pocketName}</div>
                            </div>
                        </div>

                        <div style="display: flex; gap: 8px; margin-top: 16px;">
                            <button onclick="syncAccountBalance('${account.accountId}')" class="btn btn-outline" style="flex: 1; font-size: 13px; padding: 8px 12px;">
                                🔄 Sync
                            </button>
                            <button onclick="viewAccountTransactions('${account.accountId}')" class="btn btn-outline" style="flex: 1; font-size: 13px; padding: 8px 12px;">
                                📋 Transactions
                            </button>
                            <button onclick="removeAccount('${account.accountId}')" class="btn btn-outline" style="background: var(--alert-red); color: white; border-color: var(--alert-red); padding: 8px 12px;" title="Remove account">
                                🗑️
                            </button>
                        </div>
                    </div>
                `;
                container.insertAdjacentHTML('beforeend', cardHtml);
            });
        })
        .catch(err => console.error('Error loading pocket balances:', err));
}

/**
 * Updates all balance calculations and math displays
 * @param {number} billReserved - Amount reserved for bills
 */
function updateAllMath(billReserved) {
    const available = currentBalances.checking + currentBalances.savings + billReserved;
    document.getElementById('math-total').innerText = fmt(available);
    document.getElementById('math-sched').innerText = fmt(billReserved);
    document.getElementById('math-goals').innerText = fmt(currentBalances.savings);
    document.getElementById('sts-balance').innerText = fmt(currentBalances.checking);

    // Mobile Update - Always shows checking/safe-to-spend
    if (document.getElementById('mobile-header-balance')) {
        // Keep the arrow span when updating text
        const arrowHtml = '<span class="header-arrow">▼</span>';
        document.getElementById('mobile-header-balance').innerHTML = fmt(currentBalances.checking) + " " + arrowHtml;
    }
}

/**
 * Renders a single goal/pocket item HTML
 * @param {Object} g - Goal/pocket object
 * @param {number} index - Index in goalsDataStore
 * @returns {string} HTML string for the goal item
 */
function renderGoalItem(g, index) {
    // Check if this is a credit card pocket
    const isCreditCard = g.isCreditCard === true;
    const amountLabel = isCreditCard ? 'set aside' : 'saved';

    // Calculate progress
    let pct = 0;
    let hasGoal = false;
    let progressHtml = '';
    let detailsText = '';

    if (isCreditCard && g.creditCardBalance && g.creditCardBalance > 0) {
        // Credit card: show pocket balance vs actual card balance
        pct = Math.min((g.balance / g.creditCardBalance) * 100, 100);
        hasGoal = true;
        progressHtml = `<div class="exp-progress-container"><div class="exp-progress-bar" style="width: ${pct}%"></div></div>`;
        detailsText = `<span>${fmt(g.balance)} of ${fmt(g.creditCardBalance)} ${amountLabel}</span>`;
    } else if (g.target > 0) {
        // Regular pocket with goal
        pct = Math.min((g.balance / g.target) * 100, 100);
        hasGoal = true;
        progressHtml = `<div class="exp-progress-container"><div class="exp-progress-bar" style="width: ${pct}%"></div></div>`;
        detailsText = `<span>${fmt(g.balance)} of ${fmt(g.target)} ${amountLabel}</span>`;
    } else {
        // No goal
        detailsText = `<span>${fmt(g.balance)} ${amountLabel}</span>`;
    }

    const creditCardIcon = isCreditCard ? '<span style="font-size: 16px; margin-right: 8px; vertical-align: middle;">💳</span>' : '';

    // ADDED: draggable="true", ondragstart, data-pocket-id, touch events
    return `<div class="exp-item draggable-item ${isCreditCard ? 'credit-card-pocket' : ''}"
                draggable="true"
                ondragstart="drag(event)"
                ontouchstart="handleTouchStart(event)"
                ontouchmove="handleTouchMove(event)"
                ontouchend="handleTouchEnd(event)"
                ontouchcancel="handleTouchCancel(event)"
                data-pocket-id="${g.id}"
                onclick="openGoalDetailList(${index})">
                <div class="exp-header-line">
                    <div class="exp-name">${creditCardIcon}${g.name}</div>
                    <span class="exp-funding-status ${isCreditCard ? 'credit-card-status' : 'ready'}">${isCreditCard ? '💳 Credit' : 'Active'}</span>
                </div>
                ${progressHtml}
                <div class="exp-details">${detailsText}</div>
            </div>`;
}

/**
 * Renders the group management list view
 * Shows all existing groups with edit/delete options
 */
function renderMgmtList() {
    const body = document.getElementById('mgmt-body');
    document.getElementById('mgmt-title').innerText = "Manage Groups";

    let html = '<div style="margin-bottom:20px;">';

    if (allGroups.length === 0) {
        html += '<div style="color:#999; font-style:italic; padding:10px 0;">No groups created yet.</div>';
    } else {
        allGroups.forEach(g => {
            html += `
            <div class="mgmt-list-item">
                <div class="mgmt-name">${g.name}</div>
                <div class="mgmt-actions">
                    <span class="action-icon" onclick="renderMgmtEdit(${g.id}, '${g.name.replace(/'/g, "\\'")}')">✎ Edit</span>
                    <span class="action-icon delete" onclick="deleteGroup(${g.id})">🗑 Delete</span>
                </div>
            </div>`;
        });
    }
    html += '</div>';

    html += `<button class="btn-primary" onclick="renderMgmtEdit(null, '')">+ Create New Group</button>`;

    body.innerHTML = html;
}

/**
 * Renders the group edit/create form
 * @param {number|null} id - Group ID for editing, null for creating new
 * @param {string} name - Group name to pre-fill
 */
function renderMgmtEdit(id, name) {
    const body = document.getElementById('mgmt-body');
    const title = id ? "Edit Group" : "Create New Group";
    document.getElementById('mgmt-title').innerText = title;

    let html = `
        <label class="form-label">Group Name</label>
        <input type="text" id="mgmt-name-input" class="form-input" value="${name}" placeholder="e.g. Vacation Funds">

        <label class="form-label">Assign Pockets</label>
        <div class="pocket-select-list">
    `;

    // Render Pockets Checkboxes
    goalsDataStore.forEach(p => {
        const isChecked = (id && p.groupId === id) ? 'checked' : '';
        // Logic for label: if in another group, show which one
        let subLabel = '';
        if (p.groupId && p.groupId !== id) {
            const otherGroupName = p.groupName || 'Another Group';
            subLabel = `<span class="ps-current-group">In: ${otherGroupName}</span>`;
        }

        html += `
        <label class="pocket-select-row">
            <input type="checkbox" class="ps-checkbox" value="${p.id}" ${isChecked}>
            <span class="ps-name">${p.name}</span>
            ${subLabel}
        </label>`;
    });

    html += `</div>
        <div style="margin-top:20px;">
            <button class="btn-primary" onclick="saveGroup(${id})">Save Group</button>
            <button class="btn-secondary" onclick="renderMgmtList()">Back</button>
        </div>
    `;

    body.innerHTML = html;
}
