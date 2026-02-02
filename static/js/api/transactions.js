/**
 * @file transactions.js
 * @description API layer for transaction management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (filterState)
 */

/**
 * Reload transactions list with current filters
 */
function reloadTx() {
    const loader = document.getElementById('tx-loading');
    const content = document.getElementById('tx-content');
    loader.style.display = 'block';
    content.innerHTML = '';

    const cleanState = {};
    for (const key in filterState) {
        if (filterState[key]) cleanState[key] = filterState[key];
    }

    const params = new URLSearchParams(cleanState).toString();
    fetch(`/api/transactions?${params}`).then(res => res.json()).then(data => {
        loader.style.display = 'none';
        if(data.error || !data.transactions || data.transactions.length === 0) {
            content.innerHTML = `<div style="text-align:center; padding:40px; color:#999;">No transactions found.</div>`;
            return;
        }
        // Separate pending and regular transactions
        const pendingTxs = [];
        const regularTxs = [];

        data.transactions.forEach(tx => {
            if (tx.isPending) {
                pendingTxs.push(tx);
            } else {
                regularTxs.push(tx);
            }
        });

        let html = '';

        // Helper function to render a transaction row
        const renderTxRow = (tx) => {
            const amtClass = tx.amount > 0 ? 'tx-pos' : 'tx-neg';
            const isCreditCard = tx.isCreditCard === true;
            const rowClass = isCreditCard ? 'tx-row credit-card-tx' : 'tx-row';
            const merchantInfo = isCreditCard && tx.merchant ? `<span class="tx-desc">${tx.merchant}</span>` : '';
            const accountInfo = isCreditCard && tx.accountName ? `<span class="tx-desc" style="color: var(--simple-blue); font-weight: 500;">${tx.accountName}</span>` : '';

            // Pocket tag for pocket activities
            const pocketTag = tx.pocketName ? `<span class="tx-pocket-tag">${tx.pocketName}</span>` : '';

            // Transfer indicator
            const transferTag = tx.transferType === 'SUBACCOUNT' ? `<span class="tx-transfer-tag">Transfer</span>` : '';

            // Description (for non-credit card transactions)
            const descHtml = tx.description && !isCreditCard ? `<span class="tx-desc">${tx.description}</span>` : '';

            // Memo display - only show if different from title and description
            const memoText = tx.memo || '';
            const isDuplicateMemo = memoText && (
                memoText.toLowerCase() === (tx.title || '').toLowerCase() ||
                memoText.toLowerCase() === (tx.description || '').toLowerCase()
            );
            const memoHtml = memoText && !isDuplicateMemo ? `<span class="tx-memo">${memoText}</span>` : '';

            return `<div class="${rowClass}" onclick="openTxDetail('${tx.id}')"><div class="tx-left"><span class="tx-title">${tx.title || 'Unknown'}${pocketTag}${transferTag}</span>${accountInfo}${merchantInfo}${descHtml}${memoHtml}</div><div class="tx-amount ${amtClass}">${fmt(tx.amount)}</div></div>`;
        };

        // Render pending transactions section if any exist
        if (pendingTxs.length > 0) {
            html += `<div class="tx-group-date">PENDING</div>`;
            pendingTxs.forEach(tx => {
                html += renderTxRow(tx);
            });
        }

        // Group regular transactions by date
        const grouped = {};
        regularTxs.forEach(tx => {
            const dateStr = new Date(tx.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
            if(!grouped[dateStr]) grouped[dateStr] = [];
            grouped[dateStr].push(tx);
        });

        // Render date-grouped transactions
        for (const [date, txs] of Object.entries(grouped)) {
            html += `<div class="tx-group-date">${date}</div>`;
            txs.forEach(tx => {
                html += renderTxRow(tx);
            });
        }
        content.innerHTML = html;
    }).catch(err => {
        loader.style.display = 'none';
        content.innerHTML = '<div style="text-align:center; padding:20px; color:#999;">Offline / Cached Mode</div>';
    });
}
