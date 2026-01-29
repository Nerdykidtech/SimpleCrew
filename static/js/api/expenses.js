/**
 * @file expenses.js
 * @description API layer for bills/expenses management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (expensesDataStore, currentFundingSource)
 */

/**
 * Load expenses/bills from the API
 * @param {boolean} forceRefresh - If true, bypass cache and force refresh
 */
function loadExpenses(forceRefresh = false) {
    const url = forceRefresh ? '/api/expenses?refresh=true' : '/api/expenses';
    fetch(url).then(res => res.json()).then(data => {
        if(data.error) return;
        updateAllMath(data.summary.totalReserved || 0);
        expensesDataStore = data.expenses;

        // Store the source for later use in Delete Logic
        currentFundingSource = data.summary.fundingSource || "Checking";

        const nextDate = new Date(data.summary.nextFundingDate).toLocaleDateString(undefined, {month:'short', day:'numeric', year:'numeric'});
        const summaryText = `Next Funding: ${nextDate} • Estimated ${fmt(data.summary.estimatedFunding)}`;

        document.getElementById('exp-summary-text').innerText = summaryText;

        const heroHtml = `
            <div class="exp-hero-card">
                <div class="exp-hero-col">
                    <span class="hero-lbl">Setting aside:</span>
                    <span class="hero-val">${fmt(data.summary.estimatedFunding)} on Payday 💰</span>
                </div>
                <div class="exp-hero-divider"></div>
                <div class="exp-hero-col right">
                    <span class="hero-lbl">Next Funding:</span>
                    <span class="hero-status">${nextDate}</span>
                </div>
            </div>
        `;
        document.getElementById('exp-hero-container').innerHTML = heroHtml;

        let html = `<div class="add-bill-row" onclick="openBillModal()">
            <span style="font-size:20px; line-height:1;">+</span> Add Bill
        </div>`;

        data.expenses.forEach((e, index) => {
            let pct = e.amount > 0 ? Math.min((e.reserved / e.amount) * 100, 100) : 0;
            const readyDate = e.reservedBy ? new Date(e.reservedBy).toLocaleDateString(undefined, {month:'short', day:'numeric'}) : 'Monthly';
            const estFunding = e.estimatedFunding > 0 ? `${fmt(e.estimatedFunding)} on Payday 💰` : 'Fully Funded';
            let statusBadge = e.paused ? `<span class="exp-funding-status">Paused</span>` : (e.reserved >= e.amount ? `<span class="exp-funding-status ready">Ready</span>` : '');
            html += `<div class="exp-item" onclick="openExpenseDetail(${index})"><div class="exp-header-line"><div class="exp-name">${e.name}</div>${statusBadge}</div><div class="exp-progress-container"><div class="exp-progress-bar" style="width: ${pct}%"></div></div><div class="exp-details"><span>${fmt(e.reserved)} of ${fmt(e.amount)} reserved • Ready by ${readyDate}</span><span style="color:#2C2C2C">${estFunding}</span></div></div>`;
        });
        document.getElementById('expenses-list').innerHTML = html;
    });
}

/**
 * Delete a bill/expense
 * @param {string} id - The bill ID
 * @param {string} name - The bill name
 * @param {number} reservedAmount - The currently reserved amount
 */
function deleteBill(id, name, reservedAmount) {
    let msg = `Are you sure you want to delete "${name}"?`;

    // Logic: Checking -> Safe-to-Spend, anything else -> displayName
    const destinationName = (currentFundingSource === "Checking") ? "Safe-to-Spend" : currentFundingSource;

    if (reservedAmount > 0) {
        msg += `\n\n${fmt(reservedAmount)} currently reserved will be returned to ${destinationName}.`;
    } else {
        msg += `\n\nThis will remove the bill and stop future funding.`;
    }

    appConfirm(msg, "Delete Expense", { confirmText: "Delete", danger: true }).then(confirmed => {
        if (!confirmed) return;

        const btn = document.querySelector('.btn-goal-delete');
        if(btn) {
            btn.innerText = "Deleting...";
            btn.disabled = true;
            btn.style.opacity = "0.7";
        }

        fetch('/api/delete-bill', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: id })
        })
        .then(res => res.json())
        .then(data => {
            if(data.error) {
                appAlert("Error: " + data.error, "Error");
                if(btn) {
                    btn.innerText = "Delete Expense";
                    btn.disabled = false;
                    btn.style.opacity = "1";
                }
            } else {
                closeModal();
                loadExpenses(true);
            }
        })
        .catch(err => {
            appAlert("System error occurred.", "Error");
            if(btn) btn.disabled = false;
        });
    });
}
