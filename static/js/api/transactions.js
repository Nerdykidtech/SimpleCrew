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
        const grouped = {};

        // Also populate Search suggestions for Bills from initial load if needed, but dynamic is better

        data.transactions.forEach(tx => {
            const dateStr = new Date(tx.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
            if(!grouped[dateStr]) grouped[dateStr] = [];
            grouped[dateStr].push(tx);
        });

        let html = '';
        for (const [date, txs] of Object.entries(grouped)) {
            html += `<div class="tx-group-date">${date}</div>`;
            txs.forEach(tx => {
                const amtClass = tx.amount > 0 ? 'tx-pos' : 'tx-neg';
                const isCreditCard = tx.isCreditCard === true;
                const creditCardBadge = isCreditCard ? '<span style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 2px 8px; border-radius: 12px; font-size: 10px; font-weight: 600; margin-right: 8px; text-transform: uppercase;">💳 Credit</span>' : '';
                const pendingBadge = (isCreditCard && tx.isPending) ? '<span style="background: #ffc107; color: #333; padding: 2px 8px; border-radius: 12px; font-size: 10px; font-weight: 600; margin-right: 8px;">Pending</span>' : '';
                const rowClass = isCreditCard ? 'tx-row credit-card-tx' : 'tx-row';
                const merchantInfo = isCreditCard && tx.merchant ? `<span class="tx-desc" style="color: #667eea; font-weight: 500;">${tx.merchant}</span>` : '';
                html += `<div class="${rowClass}" onclick="openTxDetail('${tx.id}')"><div class="tx-left">${creditCardBadge}${pendingBadge}<span class="tx-title">${tx.title || 'Unknown'}</span>${merchantInfo}${tx.description && !isCreditCard ? `<span class="tx-desc">${tx.description}</span>` : ''}</div><div class="tx-amount ${amtClass}">${fmt(tx.amount)}</div></div>`;
            });
        }
        content.innerHTML = html;
    }).catch(err => {
        loader.style.display = 'none';
        content.innerHTML = '<div style="text-align:center; padding:20px; color:#999;">Offline / Cached Mode</div>';
    });
}
