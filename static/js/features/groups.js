/**
 * @file groups.js
 * @description Group management functionality for creating, editing, and deleting pocket groups.
 * @requires Global functions: appAlert(), appConfirm(), loadGoals(), renderMgmtList()
 * @requires Global variables: goalsDataStore, allGroups
 */

/**
 * Saves a new or edited group
 * @param {number|null} id - The group ID to edit, or null/0 for new group
 */
function saveGroup(id) {
    const name = document.getElementById('mgmt-name-input').value;
    if(!name) { appAlert("Please enter a group name", "Validation Error"); return; }

    // Get selected pockets
    const checkboxes = document.querySelectorAll('.ps-checkbox:checked');
    const pocketIds = Array.from(checkboxes).map(cb => cb.value);

    const btn = document.querySelector('.btn-primary');
    btn.innerText = "Saving...";
    btn.disabled = true;

    fetch('/api/groups/manage', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: id, name: name, pockets: pocketIds })
    })
    .then(res => res.json())
    .then(data => {
        if(data.success) {
            // Refresh data and return to list
            loadGoals(true); // Background refresh main list
            // We need to fetch groups again locally to update 'allGroups' for the UI
            fetch('/api/goals').then(r=>r.json()).then(d => {
                 goalsDataStore = d.goals;
                 allGroups = d.all_groups;
                 renderMgmtList();
            });
        } else {
            appAlert("Error: " + data.error, "Error");
            btn.disabled = false;
            btn.innerText = "Save Group";
        }
    });
}

/**
 * Deletes a group after user confirmation
 * @param {number} id - The group ID to delete
 */
function deleteGroup(id) {
    appConfirm("Delete this group? Pockets inside will become ungrouped.", "Delete Group", { confirmText: "Delete", danger: true }).then(confirmed => {
        if (!confirmed) return;

        fetch('/api/groups/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: id })
        })
        .then(res => res.json())
        .then(data => {
            if(data.success) {
                loadGoals(true);
                // Manually remove from local array to speed up UI
                allGroups = allGroups.filter(g => g.id !== id);
                renderMgmtList();
            } else {
                appAlert("Error: " + data.error, "Error");
            }
        });
    });
}
