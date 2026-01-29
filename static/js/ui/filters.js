/**
 * @file filters.js
 * @description Search and filter functions for transactions
 * @requires state.js - For filterState global variable
 * @requires api/transactions.js - For reloadTx function
 */

/**
 * Triggers transaction search based on search input value
 * Updates filterState and reloads transactions
 */
function triggerSearch() {
    filterState.q = document.getElementById('search-input-box').value;
    reloadTx();
}

/**
 * Applies transaction filters (date range and amount range)
 * Updates filterState from form inputs and reloads transactions
 */
function applyFilters() {
    filterState.minDate = document.getElementById('filter-min-date').value;
    filterState.maxDate = document.getElementById('filter-max-date').value;
    filterState.minAmt = document.getElementById('filter-min-amt').value;
    filterState.maxAmt = document.getElementById('filter-max-amt').value;
    updatePills();
    reloadTx();
    toggleFilterMenu();
}

/**
 * Updates the filter pills display in the UI
 * Shows active filters or default "Last 6 months" pill
 */
function updatePills() {
    const container = document.getElementById('active-filters');
    container.innerHTML = '';

    if (!filterState.minDate && !filterState.maxDate && !filterState.minAmt && !filterState.maxAmt) {
        container.innerHTML = `<div class="pill" onclick="removeFilter('date')">Last 6 months <span class="pill-remove">×</span></div>`;
        return;
    }

    if(filterState.minDate || filterState.maxDate) {
        container.innerHTML += `<div class="pill" onclick="removeFilter('date')">${filterState.minDate || '...'} to ${filterState.maxDate || '...'} <span class="pill-remove">×</span></div>`;
    }

    if(filterState.minAmt || filterState.maxAmt) {
        container.innerHTML += `<div class="pill" onclick="removeFilter('amt')">$${filterState.minAmt || '0'} - $${filterState.maxAmt || '∞'} <span class="pill-remove">×</span></div>`;
    }
}

/**
 * Removes a specific filter type and updates the UI
 * @param {string} type - The filter type to remove ('date' or 'amt')
 */
function removeFilter(type) {
    if(type === 'date') {
        filterState.minDate = '';
        filterState.maxDate = '';
        document.getElementById('filter-min-date').value = '';
        document.getElementById('filter-max-date').value = '';
    }

    if(type === 'amt') {
        filterState.minAmt = '';
        filterState.maxAmt = '';
        document.getElementById('filter-min-amt').value = '';
        document.getElementById('filter-max-amt').value = '';
    }

    updatePills();
    reloadTx();
}
