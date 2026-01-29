/**
 * @file dragdrop.js
 * @description Drag and drop functionality for pocket reordering and grouping.
 * Supports both desktop (mouse) and mobile (touch) interactions with long-press activation.
 * @requires DOM elements with classes: .draggable-item, .group-drop-zone, #goals-list
 * @requires Global functions: savePocketOrder(), loadGoals(), appAlert()
 */

// Drag state variables
let draggedItem = null;
let touchStartY = 0;
let touchStartX = 0;
let isDragging = false;
let longPressTimer = null;

/**
 * Desktop drag start handler
 * @param {DragEvent} ev - The drag event
 */
function drag(ev) {
    draggedItem = ev.target;
    // Set data
    ev.dataTransfer.setData("pocketId", ev.target.getAttribute('data-pocket-id'));
    ev.dataTransfer.effectAllowed = 'move';

    // Visual timeout to allow the drag image to be generated before hiding the element
    setTimeout(() => ev.target.classList.add('is-dragging'), 0);
}

/**
 * Mobile touch start handler - initiates long-press detection
 * @param {TouchEvent} ev - The touch event
 */
function handleTouchStart(ev) {
    const touch = ev.touches[0];
    touchStartY = touch.clientY;
    touchStartX = touch.clientX;
    isDragging = false;

    const targetItem = ev.target.closest('.draggable-item');
    if (targetItem) {
        // Start a timer - only activate drag mode after 500ms hold
        longPressTimer = setTimeout(() => {
            isDragging = true;
            draggedItem = targetItem;
            draggedItem.classList.add('is-dragging');
            // Prevent body scrolling during drag
            document.body.classList.add('drag-mode-active');
            // Haptic feedback if available
            if (navigator.vibrate) {
                navigator.vibrate(50);
            }
        }, 500);
    }
}

/**
 * Mobile touch move handler - handles drag movement or detects scrolling
 * @param {TouchEvent} ev - The touch event
 */
function handleTouchMove(ev) {
    const touch = ev.touches[0];
    const deltaY = Math.abs(touch.clientY - touchStartY);
    const deltaX = Math.abs(touch.clientX - touchStartX);

    // If user moved significantly before long press activated, cancel drag mode (they're scrolling)
    if (!isDragging && (deltaY > 10 || deltaX > 10)) {
        if (longPressTimer) {
            clearTimeout(longPressTimer);
            longPressTimer = null;
        }
        return;
    }

    // If drag mode is active, prevent scrolling and handle drag
    if (!isDragging || !draggedItem) return;
    ev.preventDefault();
    ev.stopPropagation();

    const elementBelow = document.elementFromPoint(touch.clientX, touch.clientY);
    const zone = elementBelow?.closest('.group-drop-zone') || elementBelow?.closest('#goals-list');

    // Clear all active states
    document.querySelectorAll('.drag-active').forEach(el => el.classList.remove('drag-active'));

    if (zone) {
        zone.classList.add('drag-active');

        let listContainer;
        if (zone.id === 'goals-list') {
            listContainer = zone;
        } else if (zone.classList.contains('group-container')) {
            listContainer = zone.querySelector('.group-content');
            if (listContainer?.classList.contains('collapsed')) {
                listContainer.classList.remove('collapsed');
                zone.querySelector('.group-header')?.classList.remove('collapsed');
            }
        }

        if (listContainer) {
            const afterElement = getDragAfterElement(listContainer, touch.clientY);
            if (afterElement == null) {
                listContainer.appendChild(draggedItem);
            } else {
                listContainer.insertBefore(draggedItem, afterElement);
            }
        }
    }
}

/**
 * Mobile touch end handler - completes the drag operation
 * @param {TouchEvent} ev - The touch event
 */
function handleTouchEnd(ev) {
    // Clear long press timer if it's still pending
    if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
    }

    if (!isDragging || !draggedItem) {
        // Re-enable body scrolling just in case
        document.body.classList.remove('drag-mode-active');
        isDragging = false;
        draggedItem = null;
        return;
    }

    ev.preventDefault();
    ev.stopPropagation();

    const touch = ev.changedTouches[0];
    const elementBelow = document.elementFromPoint(touch.clientX, touch.clientY);
    const zone = elementBelow?.closest('.group-drop-zone') || elementBelow?.closest('#goals-list');

    document.querySelectorAll('.drag-active').forEach(el => el.classList.remove('drag-active'));
    draggedItem.classList.remove('is-dragging');
    // Re-enable body scrolling
    document.body.classList.remove('drag-mode-active');

    if (zone) {
        let groupId;
        let listContainer;

        if (zone.id === 'goals-list') {
            groupId = null;
            listContainer = zone;
        } else {
            const rawGroupId = zone.getAttribute('data-group-id');
            groupId = rawGroupId === "null" ? null : rawGroupId;
            listContainer = zone.classList.contains('group-container') ? zone.querySelector('.group-content') : zone;
        }

        if (listContainer) {
            const childPocketIds = [...listContainer.querySelectorAll('.draggable-item')]
                                    .map(el => el.getAttribute('data-pocket-id'));
            savePocketOrder(groupId, childPocketIds);
        }
    }

    draggedItem = null;
    isDragging = false;
}

/**
 * Mobile touch cancel handler - cleans up if touch is interrupted
 * @param {TouchEvent} ev - The touch event
 */
function handleTouchCancel(ev) {
    // Clean up if touch is interrupted
    if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
    }
    if (draggedItem) {
        draggedItem.classList.remove('is-dragging');
    }
    document.querySelectorAll('.drag-active').forEach(el => el.classList.remove('drag-active'));
    // Re-enable body scrolling
    document.body.classList.remove('drag-mode-active');
    draggedItem = null;
    isDragging = false;
}

/**
 * Desktop allow drop handler - handles dragover event
 * @param {DragEvent} ev - The drag event
 */
function allowDrop(ev) {
    ev.preventDefault();
    const zone = ev.target.closest('.group-drop-zone') || ev.target.closest('#goals-list');
    if (!zone) return;

    // Clear all active states first
    document.querySelectorAll('.drag-active').forEach(el => el.classList.remove('drag-active'));
    zone.classList.add('drag-active');

    let listContainer;
    if (zone.id === 'goals-list') {
        // Dropped outside any group - ungroup the item
        listContainer = zone;
    } else if (zone.classList.contains('group-container')) {
        // Inside a group card, the items are in .group-content
        listContainer = zone.querySelector('.group-content');
        // If the group is collapsed, we force expand it to allow drop
        if (listContainer?.classList.contains('collapsed')) {
            listContainer.classList.remove('collapsed');
            zone.querySelector('.group-header')?.classList.remove('collapsed');
        }
    }

    if (listContainer && draggedItem) {
        const afterElement = getDragAfterElement(listContainer, ev.clientY);
        if (afterElement == null) {
            listContainer.appendChild(draggedItem);
        } else {
            listContainer.insertBefore(draggedItem, afterElement);
        }
    }
}

/**
 * Helper to calculate insertion point based on mouse Y position
 * @param {HTMLElement} container - The container element
 * @param {number} y - The Y coordinate
 * @returns {HTMLElement|null} The element to insert before, or null to append
 */
function getDragAfterElement(container, y) {
    // Get all draggable items in this container EXCEPT the one currently dragging
    const draggableElements = [...container.querySelectorAll('.draggable-item:not(.is-dragging)')];

    return draggableElements.reduce((closest, child) => {
        const box = child.getBoundingClientRect();
        // offset: distance from the center of the child to the mouse cursor
        const offset = y - box.top - box.height / 2;

        // We want the element where the mouse is strictly ABOVE the center (negative offset)
        // and closest to 0
        if (offset < 0 && offset > closest.offset) {
            return { offset: offset, element: child };
        } else {
            return closest;
        }
    }, { offset: Number.NEGATIVE_INFINITY }).element;
}

/**
 * Desktop drag leave handler - removes active state when leaving zone
 * @param {DragEvent} ev - The drag event
 */
function dragLeave(ev) {
    // We strictly remove the 'active' style from the wrapper
    const zone = ev.target.closest('.group-drop-zone');
    if(zone) {
        // Check if we are actually leaving the zone, not just entering a child
        const rect = zone.getBoundingClientRect();
        if (ev.clientX < rect.left || ev.clientX >= rect.right ||
            ev.clientY < rect.top || ev.clientY >= rect.bottom) {
            zone.classList.remove('drag-active');
        }
    }
}

/**
 * Desktop drop handler - completes the drop operation
 * @param {DragEvent} ev - The drag event
 */
function drop(ev) {
    ev.preventDefault();
    const zone = ev.target.closest('.group-drop-zone') || ev.target.closest('#goals-list');
    if (!zone) return;

    // Clear all active states
    document.querySelectorAll('.drag-active').forEach(el => el.classList.remove('drag-active'));

    if(draggedItem) {
        draggedItem.classList.remove('is-dragging');
        draggedItem = null;
    }

    // 1. Identify the new Group ID
    let groupId;
    let listContainer;

    if (zone.id === 'goals-list') {
        // Dropped outside any group - ungroup the item
        groupId = null;
        listContainer = zone;
    } else {
        const rawGroupId = zone.getAttribute('data-group-id');
        groupId = rawGroupId === "null" ? null : rawGroupId;
        listContainer = zone.classList.contains('group-container') ? zone.querySelector('.group-content') : zone;
    }

    if (listContainer) {
        const childPocketIds = [...listContainer.querySelectorAll('.draggable-item')]
                                .map(el => el.getAttribute('data-pocket-id'));
        savePocketOrder(groupId, childPocketIds);
    }
}

/**
 * Saves pocket order after drag and drop
 * @param {string|null} groupId - The target group ID, or null for ungrouped
 * @param {string[]} orderedIds - Array of pocket IDs in new order
 */
function savePocketOrder(groupId, orderedIds) {
    // Optimistic UI is already done (DOM is moved). Now persist.
    fetch('/api/groups/move-pocket', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            targetGroupId: groupId,
            orderedPocketIds: orderedIds
        })
    })
    .then(res => res.json())
    .then(data => {
        if(!data.success) {
            console.error("Failed to save order", data.error);
            appAlert("Could not save new order. Reloading...", "Error");
            loadGoals(true); // Revert on failure
        } else {
            // Success - refresh to update group numbers
            loadGoals(true);
        }
    })
    .catch(err => {
        console.error("Network error", err);
    });
}

// Use dragend to cleanup styles if drop didn't happen
document.addEventListener("dragend", function(event) {
     document.querySelectorAll('.is-dragging').forEach(el => el.classList.remove('is-dragging'));
     document.querySelectorAll('.drag-active').forEach(el => el.classList.remove('drag-active'));
});
