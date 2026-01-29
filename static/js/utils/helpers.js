/**
 * @file helpers.js
 * @description Utility helper functions (debounce, DOM utilities)
 */

// Debounce utility to prevent spamming while typing
function debounce(func, wait) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), wait);
    };
}

// Helper to calculate insertion point based on mouse Y position during drag & drop
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
