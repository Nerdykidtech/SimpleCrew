/**
 * @file dialogs.js
 * @description Custom dialog system for alerts and confirmations
 * @requires state.js
 */

// Dialog promise resolver
let appDialogResolve = null;

function showAppDialog(title, message, buttons) {
    const overlay = document.getElementById('app-dialog-overlay');
    const titleEl = document.getElementById('app-dialog-title');
    const bodyEl = document.getElementById('app-dialog-body');
    const footerEl = document.getElementById('app-dialog-footer');

    titleEl.textContent = title || 'Alert';
    bodyEl.textContent = message || '';
    footerEl.innerHTML = '';

    if (buttons && buttons.length > 0) {
        buttons.forEach((btn, index) => {
            const button = document.createElement('button');
            button.className = `app-dialog-button ${btn.class || 'app-dialog-button-secondary'}`;
            button.textContent = btn.text || 'OK';
            button.onclick = (e) => {
                e.stopPropagation();
                const result = btn.value !== false;
                if (appDialogResolve) {
                    appDialogResolve(result);
                    appDialogResolve = null;
                }
                if (btn.onClick) btn.onClick();
                hideAppDialog();
            };
            footerEl.appendChild(button);
        });
    } else {
        const button = document.createElement('button');
        button.className = 'app-dialog-button app-dialog-button-primary';
        button.textContent = 'OK';
        button.onclick = (e) => {
            e.stopPropagation();
            if (appDialogResolve) {
                appDialogResolve(true);
                appDialogResolve = null;
            }
            hideAppDialog();
        };
        footerEl.appendChild(button);
    }

    overlay.onclick = function(e) {
        if (e.target === overlay) {
            hideAppDialog();
        }
    };

    const dialog = overlay.querySelector('.app-dialog');
    dialog.onclick = function(e) {
        e.stopPropagation();
    };

    overlay.classList.add('show');
    return new Promise(resolve => {
        appDialogResolve = resolve;
    });
}

function hideAppDialog() {
    const overlay = document.getElementById('app-dialog-overlay');
    if (!overlay) return;
    overlay.classList.remove('show');
    if (appDialogResolve) {
        appDialogResolve(false);
        appDialogResolve = null;
    }
}

function appAlert(message, title = 'Alert') {
    return showAppDialog(title, message, [
        { text: 'OK', class: 'app-dialog-button-primary', value: true }
    ]);
}

function appConfirm(message, title = 'Confirm', options = {}) {
    const confirmText = options.confirmText || 'OK';
    const cancelText = options.cancelText || 'Cancel';
    const confirmClass = options.danger ? 'app-dialog-button-danger' : 'app-dialog-button-primary';

    return showAppDialog(title, message, [
        { text: cancelText, class: 'app-dialog-button-secondary', value: false },
        { text: confirmText, class: confirmClass, value: true }
    ]);
}
