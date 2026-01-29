/**
 * @file goals.js
 * @description API layer for goals/pockets management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (goalsDataStore, allGroups, showCreditCardPockets)
 */

/**
 * Load goals/pockets from the API
 * @param {boolean} forceRefresh - If true, bypass cache and force refresh
 */
function loadGoals(forceRefresh = false) {
    const url = forceRefresh ? '/api/goals?refresh=true' : '/api/goals';
    fetch(url).then(res => res.json()).then(data => {
        // Handle error or missing data
        if (data.error || !data.goals) {
            console.error('Error loading goals:', data.error);
            document.getElementById('goals-list').innerHTML = '<div style="text-align:center; padding:20px; color:#999;">Error loading pockets</div>';
            return;
        }

        goalsDataStore = data.goals;
        allGroups = data.all_groups || [];

        let html = '';

        // Add Pocket Button
        html += `<div class="add-bill-row" onclick="openPocketModal()">
            <span style="font-size:20px; line-height:1;">+</span> Add Pocket
        </div>`;

        // 1. Group Data
        const groups = {};
        const ungrouped = [];

        // Initialize groups structure from defined groups
        allGroups.forEach(g => {
            groups[g.id] = { id: g.id, name: g.name, pockets: [], totalBalance: 0, totalTarget: 0 };
        });

        // Filter out credit card pockets unless toggle is on
        const regularGoals = showCreditCardPockets ? data.goals : data.goals.filter(g => !g.isCreditCard);

        regularGoals.forEach((g, index) => {
            // Attach original index for onClick handlers (using original goals array for indexing)
            const originalIndex = data.goals.findIndex(goal => goal.id === g.id);
            g.originalIndex = originalIndex >= 0 ? originalIndex : index;

            if (g.groupId && groups[g.groupId]) {
                groups[g.groupId].pockets.push(g);
                groups[g.groupId].totalBalance += g.balance;
                groups[g.groupId].totalTarget += g.target;
            } else {
                ungrouped.push(g);
            }
        });

        // 2. Render Groups (Modern Card Style)
        Object.values(groups).forEach(group => {
            const groupId = 'grp-' + group.id;

            // Consistent color based on name
            const colorIndex = (group.name.charCodeAt(0) + group.name.length) % 5;
            const colors = ['#0093E9', '#80D0C7', '#F5A623', '#7B1FA2', '#43A047'];
            const bg = colors[colorIndex];

            const chevron = group.pockets.length > 0 ? '▼' : ' ';

            // Add Drag Attributes to Group Container
            html += `
            <div class="group-container group-drop-zone"
                 data-group-id="${group.id}"
                 ondragover="allowDrop(event)"
                 ondrop="drop(event)"
                 ondragleave="dragLeave(event)">

                <div class="group-header" onclick="toggleGroup('${groupId}', this)">
                    <div class="group-icon-square" style="background:${bg}">${group.name[0].toUpperCase()}</div>
                    <div class="group-info">
                        <div class="group-title-text">${group.name}</div>
                        <div class="group-subtitle">${group.pockets.length} pockets</div>
                    </div>
                    <div class="group-stats">
                        <div class="group-balance">${fmt(group.totalBalance)}</div>
                        <div class="group-target">of ${fmt(group.totalTarget)}</div>
                    </div>
                    <div class="group-chevron">${chevron}</div>
                </div>
                <div id="${groupId}" class="group-content">
            `;

            // Render Pockets inside Group
            group.pockets.forEach(g => {
                html += renderGoalItem(g, g.originalIndex);
            });

            html += `</div></div>`; // Close content and container
        });

        // 3. Render Ungrouped Items
        if (ungrouped.length > 0) {
            html += `<div style="padding:15px 25px; font-size:11px; color:#999; font-weight:700; letter-spacing:1px; text-transform:uppercase; margin-top:20px;">Ungrouped</div>`;
            ungrouped.forEach(g => {
                html += renderGoalItem(g, g.originalIndex);
            });
        }

        document.getElementById('goals-list').innerHTML = html;

        // Make the entire goals-list container a drop zone for ungrouping
        const goalsList = document.getElementById('goals-list');
        goalsList.setAttribute('data-group-id', 'null');
        goalsList.addEventListener('dragover', allowDrop);
        goalsList.addEventListener('drop', drop);
        goalsList.addEventListener('dragleave', dragLeave);
    });
}

/**
 * Load sidebar pockets display
 * @param {boolean} forceRefresh - If true, bypass cache and force refresh
 */
function loadSidebarPockets(forceRefresh = false) {
    const url = forceRefresh ? '/api/goals?refresh=true' : '/api/goals';
    fetch(url).then(res => res.json()).then(data => {
        const container = document.getElementById('sidebar-pockets-list');
        if(data.error || !data.goals || data.goals.length === 0) {
            container.innerHTML = '<div style="color:#999; font-size:13px; text-align:center;">No active pockets</div>';
            return;
        }

        let html = '';
        // Filter out credit card pockets from sidebar unless toggle is on
        const regularGoals = showCreditCardPockets ? data.goals : data.goals.filter(g => !g.isCreditCard);

        regularGoals.forEach((g, index) => {
            let pct = g.target > 0 ? Math.min((g.balance / g.target) * 100, 100) : 0;
            const hasGoal = g.target > 0;

            // Sidebar specific logic for no-goal pockets
            const isCreditCard = g.isCreditCard === true;
            const amountLabel = isCreditCard ? 'Set Aside' : 'Saved';

            const detailsText = hasGoal
                ? `<span>${fmt(g.balance)}</span><span>of ${fmt(g.target)}</span>`
                : `<span>${fmt(g.balance)}</span><span>${amountLabel}</span>`;

            const barHtml = hasGoal
                ? `<div class="goal-bar-bg"><div class="goal-bar-fill" style="width:${pct}%"></div></div>`
                : '';

            html += `
            <div class="goal-card-clickable" onclick="openSidebarGoalDetail(${index})" style="margin-bottom: 15px; cursor:pointer;">
                <div style="margin-bottom: 5px; font-weight:600; font-size:14px;">${g.name}</div>
                <div style="font-size:12px; color:#767676; display:flex; justify-content:space-between; margin-bottom:5px;">
                    ${detailsText}
                </div>
                ${barHtml}
            </div>
            `;
        });
        container.innerHTML = html;
        // Update shared store
        goalsDataStore = data.goals;
    });
}

/**
 * Delete a pocket/goal
 * @param {string} id - The pocket ID
 * @param {string} name - The pocket name
 */
function deletePocket(id, name) {
    // 1. Confirmation
    appConfirm(`Are you sure you want to delete the "${name}" pocket?\n\nThis will permanently remove the pocket history.`, "Delete Pocket", { confirmText: "Delete", danger: true }).then(confirmed => {
        if (!confirmed) return;

        const btn = document.querySelector('.btn-goal-delete');
        if(btn) {
            btn.innerText = "Deleting...";
            btn.disabled = true;
            btn.style.opacity = "0.7";
        }

        // 2. Call Backend
        fetch('/api/delete-pocket', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: id })
        })
        .then(res => res.json())
        .then(data => {
            if(data.error) {
                appAlert("Error deleting pocket: " + data.error, "Error");
                if(btn) {
                    btn.innerText = "Delete Pocket";
                    btn.disabled = false;
                }
            } else {
                // 3. Success
                closeModal();
                // Force refresh lists
                loadGoals(true);
                loadSidebarPockets(true);
                initBalances(); // Refresh total balances
            }
        })
        .catch(err => {
            appAlert("System error occurred.", "Error");
        });
    });
}
