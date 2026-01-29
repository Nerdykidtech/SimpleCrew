/**
 * @file app.js
 * @description Main application initialization and event setup
 * @requires All other modules (state.js, ui/, api/, features/, utils/)
 */

// --- PWA SERVICE WORKER REGISTRATION ---
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js')
        .then(registration => {
            console.log('SW Registered: ', registration.scope);
        })
        .catch(err => {
            console.log('SW Registration Failed: ', err);
        });
    });
}

// --- INITIAL DATA LOAD ---
// Accepts a 'force' parameter (true/false)
function initBalances(force = false) {
    // If force is true, add ?refresh=true to the URL
    const url = force ? '/api/savings?refresh=true' : '/api/savings';

    fetch(url).then(res=>res.json()).then(data => {
        if(!data.error) {
            const allPocketsTotal = data.total_goals || 0;
            const checkBal = data.checking ? parseFloat(data.checking.raw_balance) : 0;

            currentBalances.checking = checkBal;
            currentBalances.savings = allPocketsTotal;

            if(data.checking) {
                document.getElementById('checking-val').innerText = fmt(checkBal);
            }
            const mathGoalsEl = document.getElementById('math-goals');
            if(mathGoalsEl) {
                mathGoalsEl.innerText = fmt(allPocketsTotal);
            }
        }
        loadExpenses();
    });
}

// --- EVENT LISTENER SETUP ---
// Click off listener for mobile stats dropdown
document.addEventListener('click', function(e) {
    const dropdown = document.getElementById('mobile-sts-dropdown');
    const header = document.querySelector('.mobile-center-header');
    if(dropdown && dropdown.classList.contains('show')) {
        dropdown.classList.remove('show');
        header.classList.remove('active');
    }
});

// --- INITIALIZE ON PAGE LOAD ---
document.addEventListener('DOMContentLoaded', () => {
    console.log('SimpleCrew initializing...');

    // Set up search input event listener
    const searchInput = document.getElementById('search-input-box');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            filterState.q = e.target.value;
            reloadTx();
        });
    }

    // Load initial data (matching original initialization sequence)
    initBalances();
    reloadTx();
    loadSidebarPockets(true);
    loadUserProfile();
    loadIntercom();
    loadCards(true);
    updateCreditCardToggleButton();

    // Set default tab
    switchTab('activity');

    console.log('SimpleCrew ready!');
});
