/**
 * @file cards.js
 * @description API layer for physical and virtual card management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (goalsDataStore, cardColors)
 */

// Store family subaccounts for card spend dropdowns
let familySubaccountsData = null;

/**
 * Build options HTML for spend dropdown
 * Only shows pockets belonging to the same owner as the currently selected pocket
 * @param {string} selectedId - Currently selected pocket ID
 * @returns {string} HTML string of option elements
 */
function buildSpendOptionsHtml(selectedId) {
    let html = '';

    if (familySubaccountsData && familySubaccountsData.groups) {
        // Find which group the selected pocket belongs to
        let ownerGroup = null;
        for (const group of familySubaccountsData.groups) {
            for (const pocket of group.pockets) {
                if (pocket.id === selectedId) {
                    ownerGroup = group;
                    break;
                }
            }
            if (ownerGroup) break;
        }

        // If no owner found (e.g., "Checking" string), default to main account
        if (!ownerGroup) {
            ownerGroup = familySubaccountsData.groups.find(g => g.ownerType === 'main');
        }

        // Only show pockets from the same owner
        if (ownerGroup) {
            ownerGroup.pockets.forEach(pocket => {
                // Only rename "Checking" to "Safe-to-Spend" for main account, not for kids
                const displayName = (pocket.name === 'Checking' && ownerGroup.ownerType === 'main')
                    ? 'Safe-to-Spend'
                    : pocket.name;
                const isSelected = pocket.id === selectedId ? 'selected' : '';
                html += `<option value="${pocket.id}" ${isSelected}>${displayName}</option>`;
            });
        }
    } else {
        // Fallback to goalsDataStore if family data not loaded (main account only)
        html = `<option value="Checking" ${selectedId === 'Checking' ? 'selected' : ''}>Safe-to-Spend</option>`;
        if (typeof goalsDataStore !== 'undefined') {
            goalsDataStore.forEach(goal => {
                const isSelected = goal.id === selectedId ? 'selected' : '';
                html += `<option value="${goal.id}" ${isSelected}>${goal.name}</option>`;
            });
        }
    }

    return html;
}

/**
 * Load physical and virtual cards from the API
 * @param {boolean} forceRefresh - If true, bypass cache and force refresh
 */
function loadCards(forceRefresh = false) {
    const cardsUrl = forceRefresh ? '/api/cards?refresh=true' : '/api/cards';

    // Fetch both cards and family subaccounts in parallel
    Promise.all([
        fetch(cardsUrl).then(res => res.json()),
        fetch('/api/family-subaccounts').then(res => res.json())
    ]).then(([cardsData, familyData]) => {
        const container = document.getElementById('cards-content');

        if(cardsData.error) {
            container.innerHTML = `<div style="text-align:center; padding:20px; color:red;">${cardsData.error}</div>`;
            return;
        }

        // Store family subaccounts for dropdown building
        if (!familyData.error) {
            familySubaccountsData = familyData;
        }

        let html = '';

        // Physical Cards Section
        if(cardsData.cards && cardsData.cards.length > 0) {
            html += '<div class="cards-section-title">Physical Cards</div>';
            cardsData.cards.forEach(card => {
                const bg = cardColors[card.color] || '#333';
                const optionsHtml = buildSpendOptionsHtml(card.current_spend_id);

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
        const activeVirtual = (cardsData.virtualCards || []).filter(c => c.status === 'ACTIVATED');
        if(activeVirtual.length > 0) {
            html += '<div class="cards-section-title" style="margin-top: 20px;">Virtual Cards</div>';
            activeVirtual.forEach(card => {
                html += renderVirtualCard(card);
            });
        }

        // Empty state
        if((!cardsData.cards || cardsData.cards.length === 0) && (!cardsData.virtualCards || cardsData.virtualCards.length === 0)) {
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
        // Build spend from dropdown options with grouped family pockets
        const optionsHtml = buildSpendOptionsHtml(card.current_spend_id);
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
