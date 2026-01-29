/**
 * @file navigation.js
 * @description Navigation and UI toggle functions
 * @requires api/transactions.js (reloadTx)
 * @requires api/expenses.js (loadExpenses)
 * @requires api/goals.js (loadGoals)
 * @requires api/family.js (loadFamily)
 * @requires api/cards.js (loadCards)
 * @requires api/credit.js (loadCreditSetup, cleanupCreditCardIntervals)
 * @requires features/autorefresh.js (startTransactionAutoRefresh, stopTransactionAutoRefresh)
 */

// --- TAB SWITCHING ---
function switchTab(tab) {
    // Clear Active Desktop
    document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
    // Clear Active Mobile
    document.querySelectorAll('.mobile-nav-link').forEach(el => el.classList.remove('active'));
    // Clear View
    document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));

    // Set Desktop Active
    const desktopNav = document.getElementById(`nav-${tab}`);
    if(desktopNav) desktopNav.classList.add('active');

    // Set Mobile Active
    const mobileNav = document.getElementById(`mb-nav-${tab}`);
    if(mobileNav) mobileNav.classList.add('active');

    const searchContainer = document.getElementById('search-container');
    const filterBar = document.getElementById('filter-bar');
    const controlsBar = document.getElementById('controls-bar');

    document.getElementById(`view-${tab}`).classList.add('active');

    // Handle UI Visibility based on Tab
    if(tab === 'activity') {
        searchContainer.style.opacity = '1'; searchContainer.style.visibility = 'visible'; filterBar.style.display = 'flex';
        controlsBar.style.display = 'flex';
        // Load transactions and start auto-refresh when activity tab is active
        reloadTx();
        startTransactionAutoRefresh();
    } else if (tab === 'goals') {
        stopTransactionAutoRefresh();
        searchContainer.style.opacity = '0'; searchContainer.style.visibility = 'hidden'; filterBar.style.display = 'none';
        controlsBar.style.display = 'none'; // FIX: Hide Controls bar on mobile Pockets to avoid whitespace
    } else {
        // Expenses, Family, Cards - Hide controls bar completely
        searchContainer.style.opacity = '0'; searchContainer.style.visibility = 'hidden'; filterBar.style.display = 'none';
        controlsBar.style.display = 'none';
        stopTransactionAutoRefresh();
    }
    // This forces a refresh of the top header numbers every time you change tabs
    initBalances(true);
    // =============================
    // === UPDATED LOGIC: Force refresh on Expenses and Goals ===
    // This ensures data is fresh when navigating
    if(tab === 'expenses') loadExpenses(true);
    if(tab === 'goals') loadGoals(true);

    if(tab === 'family') loadFamily();
    if(tab === 'cards') loadCards(true);
    if(tab === 'credit') {
        loadCreditSetup();
    } else {
        // Clean up credit card intervals when leaving credit page
        cleanupCreditCardIntervals();
    }
}

// Helper to toggle accordion
function toggleGroup(id, headerEl) {
    const content = document.getElementById(id);
    if(content.innerHTML.trim() === "") return; // Don't toggle empty groups
    content.classList.toggle('collapsed');
    headerEl.classList.toggle('collapsed');
}

function toggleMobileStats(e) {
    e.stopPropagation(); // Prevent document click from closing immediately
    const header = document.querySelector('.mobile-center-header');
    const dropdown = document.getElementById('mobile-sts-dropdown');

    // Toggle class
    const isShowing = dropdown.classList.contains('show');

    if (isShowing) {
        dropdown.classList.remove('show');
        header.classList.remove('active');
    } else {
        // Update numbers from the desktop hidden header
        document.getElementById('mb-math-total').innerText = document.getElementById('math-total').innerText;
        document.getElementById('mb-math-bills').innerText = "-" + document.getElementById('math-sched').innerText;
        document.getElementById('mb-math-goals').innerText = "-" + document.getElementById('math-goals').innerText;
        document.getElementById('mb-math-sts').innerText = document.getElementById('sts-balance').innerText;

        dropdown.classList.add('show');
        header.classList.add('active');
    }
}

function toggleFilterMenu() {
    const menu = document.getElementById('filter-menu');
    menu.style.display = menu.style.display === 'block' ? 'none' : 'block';
}

function toggleCreditCardPocketsVisibility() {
    showCreditCardPockets = !showCreditCardPockets;
    localStorage.setItem('showCreditCardPockets', showCreditCardPockets);
    updateCreditCardToggleButton();
    loadGoals(); // Reload pockets view
    loadSidebarPockets(); // Reload sidebar
}

function updateCreditCardToggleButton() {
    const btn = document.getElementById('cc-pockets-toggle-text');
    if (btn) {
        btn.textContent = showCreditCardPockets ? '💳 Hide CC' : '💳 Show CC';
    }
}
