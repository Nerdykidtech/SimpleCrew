/**
 * @file cards.js
 * @description API layer for physical card management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (goalsDataStore, cardColors)
 */

/**
 * Load physical cards from the API
 * @param {boolean} forceRefresh - If true, bypass cache and force refresh
 */
function loadCards(forceRefresh = false) {
    // If forceRefresh is true, append the query param to bypass cache
    const url = forceRefresh ? '/api/cards?refresh=true' : '/api/cards';

    fetch(url).then(res => res.json()).then(data => {
        const container = document.getElementById('cards-content');
        if(data.error) {
            container.innerHTML = `<div style="text-align:center; padding:20px; color:red;">${data.error}</div>`;
            return;
        }

        let html = '';
        if(data.cards.length === 0) {
            html = '<div style="text-align:center; padding:40px; color:#999;">No physical cards found.</div>';
        } else {
            data.cards.forEach(card => {
                const bg = cardColors[card.color] || '#333';

                // let optionsHtml = `<option value="Checking" ${card.current_spend_id === 'Checking' ? 'selected' : ''}>Checking</option>`;
                let optionsHtml = `<option value="Checking" ${card.current_spend_id === 'Checking' ? 'selected' : ''}>Safe-to-Spend</option>`;
                if (typeof goalsDataStore !== 'undefined') {
                    goalsDataStore.forEach(goal => {
                        const isSelected = card.current_spend_id === goal.id ? 'selected' : '';
                        optionsHtml += `<option value="${goal.id}" ${isSelected}>${goal.name}</option>`;
                    });
                }


                html += `
                <div class="card-row">
                    <div class="card-icon" style="background-color: ${bg};"></div>
                    <div class="card-info">
                        <div class="card-name">${card.holder}</div>
                        <div class="card-meta">Simple Visa® Card • ..${card.last4}</div>
                    </div>
                    <div class="card-controls">
                        <span class="spend-label">Spend From:</span>

                        <select class="modern-select"
                                onclick="event.stopPropagation()"
                                onchange="updateSpendPocket(this, '${card.userId}')"> ${optionsHtml}
                        </select>

                        <span class="type-badge">Physical</span>
                    </div>
                </div>`;
            });
        }
        container.innerHTML = html;
    });
}

/**
 * Update the spend pocket assignment for a card
 * @param {HTMLElement} selectElement - The select dropdown element
 * @param {string} userId - The user ID for the card
 */
function updateSpendPocket(selectElement, userId) {
    const selectedPocketId = selectElement.value;

    // UI Feedback: Disable select temporarily
    selectElement.disabled = true;
    selectElement.style.opacity = "0.6";

    // We send just the IDs to Python; Python handles the complex GraphQL
    const payload = {
        userId: userId,
        pocketId: selectedPocketId
    };

    // UPDATED: Point to the specific Python route, not /api/graphql
    fetch('/api/set-card-spend', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        // Re-enable control
        selectElement.disabled = false;
        selectElement.style.opacity = "1";

        if (data.error) {
            appAlert("Failed to update: " + data.error, "Error");
            // Optional: Reload cards to reset the dropdown to the server state
            loadCards(true);
        } else {
            console.log("Spend pocket updated successfully", data);

            // Visual confirmation (flash green border)
            selectElement.style.borderColor = "#63BB67";
            setTimeout(() => selectElement.style.borderColor = "#DDD", 1000);
        }
    })
    .catch(err => {
        console.error("Error updating spend pocket", err);
        selectElement.disabled = false;
        selectElement.style.opacity = "1";
        appAlert("Network error occurred.", "Error");
    });
}
