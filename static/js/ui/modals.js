/**
 * @file modals.js
 * @description Modal window management functions for move money, bills, pockets, groups, and detail views
 * @requires state.js - For global state variables
 * @requires utils.js - For formatting functions (fmt)
 * @requires api.js - For API calls
 * @requires ui/dialogs.js - For appConfirm and appAlert functions
 */

// --- MOVE MONEY MODAL ---

/**
 * Opens the move money modal with optional pre-filled destination
 * @param {string|null} preFillToId - Optional subaccount ID to pre-fill as destination
 */
function openMoveModal(preFillToId = null) {
    // Close other modals if open (like the pocket detail modal)
    document.getElementById('tx-modal').style.display = 'none';

    document.getElementById('move-modal').style.display = 'flex';
    document.getElementById('move-message').innerHTML = '';

    // ALWAYS REFRESH
    fetch('/api/subaccounts?refresh=true').then(res=>res.json()).then(data => {
        if(data.error) return;

        // Store account data globally for validation logic
        moveMoneyAccounts = data.subaccounts;

        const opts = data.subaccounts.map(acc => `<option value="${acc.id}">${acc.name} (${fmt(acc.balance)})</option>`).join('');
        const fromSelect = document.getElementById('move-from');
        const toSelect = document.getElementById('move-to');

        fromSelect.innerHTML = opts;
        toSelect.innerHTML = opts;

        // Logic to pre-fill destination if provided
        if(preFillToId) {
            toSelect.value = preFillToId;
            // Try to set 'From' to Checking (assuming first one or specific logic) if not same
            if(fromSelect.value === preFillToId && fromSelect.options.length > 1) {
                 fromSelect.selectedIndex = (fromSelect.selectedIndex + 1) % fromSelect.options.length;
            }
        }
    });
}

/**
 * Closes the move money modal
 */
function closeMoveModal() {
    document.getElementById('move-modal').style.display = 'none';
}

/**
 * Executes the money transfer from the move money modal
 */
function executeTransfer() {
    const fromId = document.getElementById('move-from').value;
    const toId = document.getElementById('move-to').value;
    const amount = document.getElementById('move-amount').value;
    const note = document.getElementById('move-note').value;
    const messageEl = document.getElementById('move-message');

    // Validation
    if (!fromId || !toId) {
        messageEl.innerHTML = '<div style="color:red; margin-bottom:10px;">Please select accounts</div>';
        return;
    }

    if (!amount || parseFloat(amount) <= 0) {
        messageEl.innerHTML = '<div style="color:red; margin-bottom:10px;">Please enter a valid amount</div>';
        return;
    }

    if (fromId === toId) {
        messageEl.innerHTML = '<div style="color:red; margin-bottom:10px;">Cannot transfer to the same account</div>';
        return;
    }

    // Check if source account has enough balance
    const fromAccount = moveMoneyAccounts.find(acc => acc.id === fromId);
    if (fromAccount && parseFloat(amount) > fromAccount.balance) {
        messageEl.innerHTML = '<div style="color:red; margin-bottom:10px;">Insufficient funds in source account</div>';
        return;
    }

    messageEl.innerHTML = '<div style="color:#0093E9;">Processing transfer...</div>';

    // Execute transfer
    fetch('/api/move-money', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fromId, toId, amount, note })
    })
    .then(res => res.json())
    .then(data => {
        if (data.error) {
            messageEl.innerHTML = `<div style="color:red; margin-bottom:10px;">Error: ${data.error}</div>`;
        } else {
            messageEl.innerHTML = '<div style="color:#63BB67; margin-bottom:10px;">✓ Transfer complete!</div>';
            setTimeout(() => {
                closeMoveModal();
                initBalances();
                reloadTx();
                loadSidebarPockets(true);

                // Refresh current view if needed
                if (document.getElementById('view-expenses').classList.contains('active')) loadExpenses(true);
                if (document.getElementById('view-goals').classList.contains('active')) loadGoals(true);
            }, 1000);
        }
    })
    .catch(error => {
        messageEl.innerHTML = `<div style="color:red; margin-bottom:10px;">Network error: ${error.message}</div>`;
    });
}

// --- BILL MODAL ---

/**
 * Opens the add bill modal and populates day dropdown
 */
function openBillModal() {
    document.getElementById('bill-modal').style.display = 'flex';
    document.getElementById('bill-message').innerHTML = '';

    // Populate Days (1-31)
    const daySelect = document.getElementById('bill-day');
    daySelect.innerHTML = '';
    for(let i=1; i<=31; i++) {
        const opt = document.createElement('option');
        opt.value = i;
        opt.innerText = i + (i===1?'st':(i===2?'nd':(i===3?'rd':'th')));
        daySelect.appendChild(opt);
    }
}

/**
 * Closes the bill modal
 * @param {Event} e - The event object
 */
function closeBillModal(e) {
    document.getElementById('bill-modal').style.display = 'none';
}

/**
 * Saves a new bill expense
 * Validates inputs and sends to API
 */
function saveBill() {
    const btn = document.querySelector('#bill-modal .btn-goal-add');
    const msg = document.getElementById('bill-message');
    const modalBody = document.querySelector('#bill-modal .modal-body');

    // 1. Gather Inputs
    const name = document.getElementById('bill-name').value;
    const amount = document.getElementById('bill-amount').value;
    const frequency = document.getElementById('bill-freq').value;
    const dayOfMonth = document.getElementById('bill-day').value;

    // Optional / Advanced Inputs
    const matchString = document.getElementById('bill-id-search').value;
    const minAmount = document.getElementById('bill-min').value;
    const maxAmount = document.getElementById('bill-max').value;
    const isVariable = document.getElementById('bill-variable').checked;

    // 2. Validation
    if(!name || !amount) {
        msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">Please enter name and amount.</div>`;
        return;
    }

    if(!dayOfMonth) {
        msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">Please select a day of the month.</div>`;
        return;
    }

    btn.disabled = true;
    btn.innerText = "Creating Bill...";

    // 3. Construct Payload
    const payload = {
        name: name,
        amount: amount,
        frequency: frequency,
        dayOfMonth: dayOfMonth,
        matchString: matchString || null,
        minAmount: minAmount || null,
        maxAmount: maxAmount || null,
        variable: isVariable
    };

    // 4. API Call
    fetch('/api/create-bill', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
            btn.disabled = false;
            btn.innerText = "Create Bill";

            if(data.error) {
                msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">${data.error}</div>`;
            } else {
                // SUCCESS LOGIC
                const res = data.result;
                const reservedCents = res.reservedAmount || 0;
                const reservedAmt = reservedCents / 100.0;

                // Use the simplified name we injected from the Python backend
                const accountName = res.fundingDisplayName || "Checking";

                // Replace Modal Content with Success Message
                modalBody.innerHTML = `
                    <div style="text-align:center; padding: 20px 0;">
                        <div style="font-size: 40px; margin-bottom: 10px;">🎉</div>
                        <div style="font-size: 18px; font-weight: 700; color: var(--text-dark); margin-bottom: 15px;">Bill Created Successfully</div>

                        <div style="background: var(--bg-elevated); padding: 20px; border-radius: 8px; margin-bottom: 20px; text-align: left; border: 1px solid var(--border-color);">
                            <div style="font-size: 14px; line-height: 1.5; color: var(--text-light);">
                                To ensure <strong>${name}</strong> is fully caught up and aligned with your funding schedule,
                                <span style="color: var(--text-dark); font-weight: 700;">${fmt(reservedAmt)}</span>
                                has been automatically reserved from your
                                <span style="color: var(--text-dark); font-weight: 700;">${accountName}</span> account.
                            </div>
                        </div>

                        <button class="btn-goal-add" onclick="closeBillModalAndRefresh()">Done</button>
                    </div>
                `;
            }
        })
    .catch(err => {
        btn.disabled = false;
        btn.innerText = "Create Bill";
        msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">System Error</div>`;
        console.error(err);
    });
}

/**
 * Helper function to close bill modal and refresh data
 */
function closeBillModalAndRefresh() {
    closeBillModal();
    // Use timeout to allow modal animation to finish slightly
    setTimeout(() => {
        // Reset the modal body content for next time (optional, but good practice if not reloading page)
        // Since we are doing a simpler prototype, we can just reload lists.
        // Ideally you would reconstruct the form HTML here if you want to reuse it without refresh.
        // For now, we refresh data.
        loadExpenses(true);
        initBalances();
    }, 200);
}

// --- POCKET MODAL ---

/**
 * Opens the add pocket modal and populates group dropdown
 */
function openPocketModal() {
    document.getElementById('pocket-modal').style.display = 'flex';
    document.getElementById('pocket-message').innerHTML = '';
    // Reset fields
    document.getElementById('pocket-name').value = '';
    document.getElementById('pocket-amount').value = '';
    document.getElementById('pocket-initial').value = '';
    document.getElementById('pocket-desc').value = '';
    document.getElementById('pocket-group').value = '';

    // Populate group dropdown
    const groupSelect = document.getElementById('pocket-group');
    groupSelect.innerHTML = '<option value="">No Group</option>';
    allGroups.forEach(group => {
        const option = document.createElement('option');
        option.value = group.id;
        option.textContent = group.name;
        groupSelect.appendChild(option);
    });

    // Initial hint update
    const avail = currentBalances.checking;
    document.getElementById('pocket-funding-hint').innerText = `Safe-to-Spend: ${fmt(avail)}`;
}

/**
 * Closes the pocket modal
 * @param {Event} e - The event object
 */
function closePocketModal(e) {
    document.getElementById('pocket-modal').style.display = 'none';
}

/**
 * Saves a new pocket/goal
 * Validates inputs and sends to API
 */
function savePocket() {
    const btn = document.querySelector('#pocket-modal .btn-goal-add');
    const msg = document.getElementById('pocket-message');

    const name = document.getElementById('pocket-name').value;
    const amount = document.getElementById('pocket-amount').value;
    const initial = document.getElementById('pocket-initial').value || 0;
    const note = document.getElementById('pocket-desc').value || "";
    const groupId = document.getElementById('pocket-group').value || null;

    console.log('Selected groupId:', groupId); // Debug log

    // Validation
    if(!name || !amount) {
        msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">Please enter name and goal amount.</div>`;
        return;
    }

    const avail = currentBalances.checking;
    if (parseFloat(initial) > avail) {
        msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">Initial funding cannot exceed Safe-to-Spend balance (${fmt(avail)}).</div>`;
        return;
    }

    btn.disabled = true;
    btn.innerText = "Creating Pocket...";

    // Payload matches the arguments expected by the Python route
    const payload = {
        name: name,
        amount: amount,
        initial: initial,
        note: note,
        groupId: groupId
    };

    console.log('Sending payload:', payload); // Debug log

    fetch('/api/create-pocket', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        console.log('Backend response:', data); // Debug log
        btn.disabled = false;
        btn.innerText = "Create Pocket";

        if(data.error) {
            msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">${data.error}</div>`;
        } else {
            msg.innerHTML = `<div style="color:green; margin-bottom:10px;">Success! Pocket Created.</div>`;

            setTimeout(() => {
                closePocketModal();
                // Force refresh goals and sidebar to show the new pocket
                loadGoals(true);
                loadSidebarPockets(true);
                // Refresh balance math in case money was moved
                initBalances();
            }, 1000);
        }
    })
    .catch(err => {
        btn.disabled = false;
        btn.innerText = "Create Pocket";
        msg.innerHTML = `<div style="color:var(--alert-red); margin-bottom:10px;">System Error</div>`;
    });
}

// --- GROUP MANAGEMENT MODAL ---

/**
 * Opens the group management modal
 */
function openGroupMgmt() {
    document.getElementById('group-mgmt-modal').style.display = 'flex';
    renderMgmtList();
}

/**
 * Closes the group management modal
 */
function closeGroupMgmt() {
    document.getElementById('group-mgmt-modal').style.display = 'none';
}

// --- DETAIL MODALS ---

/**
 * Opens expense detail modal for a specific expense
 * @param {number} index - Index of expense in expensesDataStore
 */
function openExpenseDetail(index) {
    const e = expensesDataStore[index];
    const modal = document.getElementById('tx-modal');
    modal.style.display = 'flex';
    document.getElementById('modal-title-text').innerText = "Expense Details";
    const pct = e.amount > 0 ? ((e.reserved / e.amount) * 100).toFixed(0) : 0;

    const safeName = e.name.replace(/'/g, "\\'");

    document.getElementById('modal-body-content').innerHTML = `
        <div style="text-align:center;">
            <div style="font-size:18px; font-weight:600; margin-bottom:10px;">${e.name}</div>
            <div class="detail-amount">${fmt(e.reserved)}</div>
            <div style="color:#999; margin-bottom:20px;">Reserved of ${fmt(e.amount)}</div>
            <div style="text-align:left;">
                <div class="detail-row"><span>Status</span><span>${e.paused?'Paused':'Active'}</span></div>
                <div class="detail-row"><span>Progress</span><span>${pct}%</span></div>
                <div class="detail-row"><span>Next Funding</span><span>${fmt(e.estimatedFunding)}</span></div>
            </div>

            <button class="btn-goal-delete" onclick="deleteBill('${e.id}', '${safeName}', ${e.reserved})">Delete Expense</button>
        </div>`;
}

/**
 * Opens goal/pocket detail modal with activity list
 * @param {number} index - Index of goal in goalsDataStore
 */
function openGoalDetailList(index) {
    const g = goalsDataStore[index];
    const modal = document.getElementById('tx-modal');
    modal.style.display = 'flex';
    document.getElementById('modal-title-text').innerText = g.name;
    const body = document.getElementById('modal-body-content');

    // Set initial layout while loading
    const isCreditCard = g.isCreditCard === true;
    const amountLabel = isCreditCard ? 'Set Aside' : 'Saved';
    const targetText = g.target > 0 ? `${amountLabel} of ${fmt(g.target)}` : `Total ${amountLabel}`;

    body.innerHTML = `
        <div class="pocket-header">
            <div class="pocket-balance">${fmt(g.balance)}</div>
            <div class="pocket-sub">${targetText}</div>
        </div>

        <div class="pocket-tx-scroll" id="pocket-tx-list">
            <div style="text-align:center; padding:20px; color:#999;">Loading transactions...</div>
        </div>

        <div style="margin-top: 20px; display: flex; flex-direction: row; gap: 15px;">
            <button class="btn-goal-add" style="flex:1; margin:0;" onclick="openMoveModal('${g.id}')">Add Funds</button>
            <button class="btn-goal-delete" style="flex:1; margin:0;" onclick="deletePocket('${g.id}', '${g.name.replace(/'/g, "\\'")}')">Delete Pocket</button>
        </div>
    `;

    // Fetch transactions directly for this pocket
    fetch(`/api/pocket-transactions/${encodeURIComponent(g.id)}?pageSize=50`)
        .then(res => res.json())
        .then(data => {
            const listContainer = document.getElementById('pocket-tx-list');

            if (!listContainer) {
                console.error('pocket-tx-list element not found');
                return;
            }

            if (data.error) {
                console.error('API error:', data.error);
                listContainer.innerHTML = '<div style="text-align:center; padding:20px; color:red;">Error loading transactions</div>';
                return;
            }

            if (!data.transactions || data.transactions.length === 0) {
                listContainer.innerHTML = '<div style="text-align:center; padding:20px; color:#999;">No recent activity</div>';
                return;
            }

            let html = '';
            data.transactions.forEach(tx => {
                const date = tx.occurredAt
                    ? new Date(tx.occurredAt).toLocaleDateString(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'})
                    : '';
                const amtClass = tx.amount > 0 ? 'tx-pos' : 'tx-neg';
                const sign = tx.amount > 0 ? '+' : '';

                // Determine the display title
                let displayTitle = tx.title || tx.matchingName || 'Transaction';

                // Add transfer type indicator if it's a transfer
                let typeIndicator = '';
                if (tx.transferType === 'SUBACCOUNT') {
                    typeIndicator = '<span class="pocket-tx-type">Transfer</span>';
                } else if (tx.type === 'CARD') {
                    typeIndicator = '<span class="pocket-tx-type card">Card</span>';
                }

                html += `
                <div class="pocket-tx-row">
                    <div class="pocket-tx-left">
                        <div class="pocket-tx-title">${displayTitle}${typeIndicator}</div>
                        <div class="pocket-tx-date">${date}</div>
                    </div>
                    <div class="pocket-tx-amt ${amtClass}">${sign}${fmt(Math.abs(tx.amount))}</div>
                </div>
                `;
            });

            // Show load more if there are more pages
            if (data.hasNextPage) {
                html += `<div style="text-align:center; padding:15px; color:#999; font-size:12px;">Showing first 50 transactions</div>`;
            }

            listContainer.innerHTML = html;
        })
        .catch(error => {
            console.error('Error fetching pocket transactions:', error);
            const listContainer = document.getElementById('pocket-tx-list');
            if (listContainer) {
                listContainer.innerHTML = '<div style="text-align:center; padding:20px; color:red;">Error loading transactions</div>';
            }
        });
}

/**
 * Opens family member detail modal
 * @param {string} type - The type of family member (e.g., 'child')
 * @param {number} index - Index in the family data array
 */
function openFamilyDetail(type, index) {
    if (type !== 'child') return;
    fetch('/api/family').then(res=>res.json()).then(data => {
        const person = data.children[index];
        const modal = document.getElementById('tx-modal'); modal.style.display = 'flex'; document.getElementById('modal-title-text').innerText = "Account Details";
        const age = Math.abs(new Date(Date.now() - new Date(person.dob).getTime()).getUTCFullYear() - 1970);
        document.getElementById('modal-body-content').innerHTML = `<div style="text-align:center;"><img src="${person.image}" style="width:80px; height:80px; border-radius:50%; margin-bottom:15px; border:2px solid #EEE;"><div style="font-size:20px; font-weight:700; color:#333; margin-bottom:5px;">${person.name}</div><div class="detail-amount">${fmt(person.balance)}</div><div style="color:#999; margin-bottom:25px;">Checking Balance</div><div style="text-align:left;"><div class="detail-row"><span>Role</span><span>Child (${age} yrs)</span></div><div class="detail-row"><span>Allowance</span><span>${person.allowance}</span></div><div class="detail-row"><span>Card Color</span><span style="color:${cardColors[person.color]}">● ${person.color}</span></div></div><button class="btn-goal-add">Manage Settings</button></div>`;
    });
}

/**
 * Opens goal detail from sidebar click
 * @param {number} index - Index of goal in sidebar
 */
function openSidebarGoalDetail(index) {
    // Reuse the main detail view for sidebar clicks too for consistency
    // Note: Since sidebar ordering might differ if grouping is used, we need to find the correct index in the master store
    // However, loadSidebarPockets updates goalsDataStore to flat list, which might conflict if both are used.
    // Better to rely on the ID. But for now, since loadGoals refreshes the store with grouping, let's just re-open the main goals tab.
    switchTab('goals');
}

/**
 * Opens transaction detail modal
 * @param {string} id - Transaction ID
 */
function openTxDetail(id) {
    const modal = document.getElementById('tx-modal');
    modal.style.display = 'flex';
    document.getElementById('modal-title-text').innerText = "Transaction Details";
    document.getElementById('modal-body-content').innerHTML = 'Loading...';
    fetch(`/api/transaction/${encodeURIComponent(id)}`).then(res=>res.json()).then(data => {
        document.getElementById('modal-body-content').innerHTML = `<div style="text-align:center; margin-bottom: 20px;"><div class="detail-amount">${fmt(data.amount)}</div><div style="font-size:16px;">${data.title}</div></div><div style="text-align:left;"><div class="detail-row"><span>Date</span><span>${new Date(data.date).toLocaleDateString()}</span></div><div class="detail-row"><span>Status</span><span>${data.status}</span></div></div>`;
    });
}

/**
 * Closes the generic modal (tx-modal)
 * @param {Event} e - The event object
 */
function closeModal(e) {
    document.getElementById('tx-modal').style.display = 'none';
}
