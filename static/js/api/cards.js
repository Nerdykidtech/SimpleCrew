/**
 * @file cards.js
 * @description API layer for physical and virtual card management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (goalsDataStore, cardColors)
 */

/**
 * Load physical and virtual cards from the API
 * @param {boolean} forceRefresh - If true, bypass cache and force refresh
 */
function loadCards(forceRefresh = false) {
    const url = forceRefresh ? '/api/cards?refresh=true' : '/api/cards';

    fetch(url).then(res => res.json()).then(data => {
        const container = document.getElementById('cards-content');
        if(data.error) {
            container.innerHTML = `<div style="text-align:center; padding:20px; color:red;">${data.error}</div>`;
            return;
        }

        let html = '';

        // Physical Cards Section
        if(data.cards && data.cards.length > 0) {
            html += '<div class="cards-section-title">Physical Cards</div>';
            data.cards.forEach(card => {
                const bg = cardColors[card.color] || '#333';

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
                                onchange="updateSpendPocket(this, '${card.userId}', '${card.id}')"> ${optionsHtml}
                        </select>
                        <span class="type-badge type-badge-physical">Physical</span>
                    </div>
                </div>`;
            });
        }

        // Virtual Cards Section (only active cards)
        const activeVirtual = (data.virtualCards || []).filter(c => c.status === 'ACTIVATED');
        if(activeVirtual.length > 0) {
            html += '<div class="cards-section-title" style="margin-top: 20px;">Virtual Cards</div>';
            activeVirtual.forEach(card => {
                html += renderVirtualCard(card);
            });
        }

        // Empty state
        if((!data.cards || data.cards.length === 0) && (!data.virtualCards || data.virtualCards.length === 0)) {
            html = '<div style="text-align:center; padding:40px; color:#999;">No cards found.</div>';
        }

        container.innerHTML = html;
    });
}

/**
 * Render a virtual card row
 * @param {Object} card - Virtual card data
 */
function renderVirtualCard(card) {
    const bg = cardColors[card.color] || '#333';
    const isFrozen = card.frozenStatus === 'FROZEN';
    const isSingleUse = card.type === 'Single-Use';
    const isAttachedToBill = card.isAttachedToBill;

    // Card meta info
    let metaInfo = `..${card.last4}`;
    if (card.linkedSubaccount) {
        metaInfo += ` • ${card.linkedSubaccount}`;
    }
    if (card.monthlyLimit) {
        metaInfo += ` • Limit: ${fmt(card.monthlyLimit)}`;
        if (card.remaining !== null) {
            metaInfo += ` (${fmt(card.remaining)} left)`;
        }
    }

    // Badge type
    const badgeClass = isSingleUse ? 'type-badge-single-use' : 'type-badge-virtual';
    const badgeText = isSingleUse ? 'Single-Use' : 'Virtual';

    // Build spend controls - either dropdown or bill attachment label
    let spendControlsHtml;
    if (isAttachedToBill && card.attachedBillName) {
        spendControlsHtml = `
            <span class="spend-label">Attached to:</span>
            <span class="attached-bill-name">${card.attachedBillName}</span>
        `;
    } else {
        // Build spend from dropdown options
        let optionsHtml = `<option value="Checking" ${card.current_spend_id === 'Checking' ? 'selected' : ''}>Safe-to-Spend</option>`;
        if (typeof goalsDataStore !== 'undefined') {
            goalsDataStore.forEach(goal => {
                const isSelected = card.current_spend_id === goal.id ? 'selected' : '';
                optionsHtml += `<option value="${goal.id}" ${isSelected}>${goal.name}</option>`;
            });
        }
        spendControlsHtml = `
            <span class="spend-label">Spend From:</span>
            <select class="modern-select"
                    onclick="event.stopPropagation()"
                    onchange="updateSpendPocket(this, '${card.userId}', '${card.id}')"> ${optionsHtml}
            </select>
        `;
    }

    return `
    <div class="card-row card-row-virtual">
        <div class="card-icon card-icon-virtual" style="background: linear-gradient(135deg, ${bg} 0%, ${adjustColor(bg, -30)} 100%);">
            <div class="virtual-chip"></div>
        </div>
        <div class="card-info">
            <div class="card-name">${card.name}${isFrozen ? '<span class="card-status-indicator status-frozen">Frozen</span>' : ''}</div>
            <div class="card-meta">${card.holder} • ${metaInfo}</div>
        </div>
        <div class="card-controls">
            ${spendControlsHtml}
            <span class="type-badge ${badgeClass}">${badgeText}</span>
        </div>
    </div>`;
}

/**
 * Adjust color brightness
 * @param {string} color - Hex color
 * @param {number} amount - Amount to adjust (-255 to 255)
 */
function adjustColor(color, amount) {
    const hex = color.replace('#', '');
    const r = Math.max(0, Math.min(255, parseInt(hex.substr(0, 2), 16) + amount));
    const g = Math.max(0, Math.min(255, parseInt(hex.substr(2, 2), 16) + amount));
    const b = Math.max(0, Math.min(255, parseInt(hex.substr(4, 2), 16) + amount));
    return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}

/**
 * Update the spend pocket assignment for a card
 * @param {HTMLElement} selectElement - The select dropdown element
 * @param {string} userId - The user ID for the card
 * @param {string} cardId - The card ID
 */
function updateSpendPocket(selectElement, userId, cardId) {
    const selectedPocketId = selectElement.value;

    // UI Feedback: Disable select temporarily
    selectElement.disabled = true;
    selectElement.style.opacity = "0.6";

    // We send just the IDs to Python; Python handles the complex GraphQL
    const payload = {
        userId: userId,
        pocketId: selectedPocketId,
        cardId: cardId
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
            // Reload cards to reset the dropdown to the server state
            loadCards(true);
        } else {
            console.log("Spend pocket updated successfully", data);

            // Visual confirmation (flash green border)
            selectElement.style.borderColor = "#63BB67";
            setTimeout(() => {
                selectElement.style.borderColor = "#DDD";
                // Reload cards to show updated pocket name
                loadCards(true);
            }, 500);
        }
    })
    .catch(err => {
        console.error("Error updating spend pocket", err);
        selectElement.disabled = false;
        selectElement.style.opacity = "1";
        appAlert("Network error occurred.", "Error");
    });
}
