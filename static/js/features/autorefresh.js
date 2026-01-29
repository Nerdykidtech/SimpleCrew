/**
 * @file autorefresh.js
 * @description Auto-refresh functionality for transaction lists.
 * Automatically refreshes transactions every 30 seconds when the activity tab is active.
 * @requires state.js - For transactionRefreshInterval global variable
 * @requires api/transactions.js - For reloadTx()
 * @requires DOM element: #view-activity
 */

// Note: transactionRefreshInterval is defined in state.js

/**
 * Starts auto-refresh interval for transactions
 * Refreshes every 30 seconds when activity tab is active
 */
function startTransactionAutoRefresh() {
    // Clear any existing interval
    if (transactionRefreshInterval) {
        clearInterval(transactionRefreshInterval);
    }
    // Refresh transactions every 30 seconds when activity tab is active
    transactionRefreshInterval = setInterval(() => {
        const activityTab = document.getElementById('view-activity');
        if (activityTab && activityTab.classList.contains('active')) {
            reloadTx();
        }
    }, 30000); // 30 seconds
}

/**
 * Stops auto-refresh interval for transactions
 */
function stopTransactionAutoRefresh() {
    if (transactionRefreshInterval) {
        clearInterval(transactionRefreshInterval);
        transactionRefreshInterval = null;
    }
}
