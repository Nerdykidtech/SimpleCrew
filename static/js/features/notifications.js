/**
 * @file notifications.js
 * @description Web Push notifications using VAPID authentication
 */

let notificationPermission = 'default';

/**
 * Initialize Web Push and check notification status
 */
async function initializeNotifications() {
    try {
        // Check if notifications are supported
        if (!('Notification' in window)) {
            console.log('This browser does not support notifications');
            return;
        }

        // Check if service worker is registered
        if (!('serviceWorker' in navigator)) {
            console.log('Service workers not supported');
            return;
        }

        // Check current permission status
        notificationPermission = Notification.permission;

        // If already granted, check if we have a valid subscription
        if (notificationPermission === 'granted') {
            const response = await fetch('/api/account/fcm/config');
            const data = await response.json();

            if (data.configured) {
                // Check if we already have a push subscription
                const registration = await navigator.serviceWorker.ready;
                const existingSubscription = await registration.pushManager.getSubscription();

                if (!existingSubscription) {
                    // No subscription yet, register for push
                    console.log('No existing subscription, registering...');
                    await registerForPush(data.vapid_public_key);
                } else {
                    console.log('Already subscribed to push notifications');
                }
            }
        }

        updateNotificationUI();

    } catch (error) {
        console.error('Failed to initialize notifications:', error);
    }
}

/**
 * Request notification permission from user
 */
async function requestNotificationPermission() {
    try {
        const permission = await Notification.requestPermission();
        notificationPermission = permission;

        if (permission === 'granted') {
            console.log('Notification permission granted');

            // Get VAPID key and register
            const response = await fetch('/api/account/fcm/config');
            const data = await response.json();

            if (!data.configured) {
                appAlert('Please configure VAPID keys in Account Settings first', 'VAPID Not Configured');
                updateNotificationUI();
                return permission;
            }

            await registerForPush(data.vapid_public_key);
            appAlert('✓ Push notifications enabled', 'Success');
        } else {
            console.log('Notification permission denied');
            appAlert('Notification permission denied. Enable in browser settings to receive push notifications.', 'Permission Denied');
        }

        updateNotificationUI();

        return permission;
    } catch (error) {
        console.error('Error requesting notification permission:', error);
        appAlert('Failed to enable notifications: ' + error.message, 'Error');
        updateNotificationUI();
        return permission;
    }
}

/**
 * Register for push notifications using native Push API
 */
async function registerForPush(vapidPublicKey) {
    try {
        console.log('Starting push registration with VAPID key:', vapidPublicKey.substring(0, 20) + '...');

        // Get service worker registration
        const registration = await navigator.serviceWorker.ready;
        console.log('Service worker ready');

        // Subscribe to push notifications
        console.log('Subscribing to push notifications...');
        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)
        });

        console.log('Push subscription successful:', subscription);

        // Send subscription to backend
        const token = JSON.stringify(subscription);
        const deviceName = getDeviceName();

        console.log('Registering token with backend...');
        const response = await fetch('/api/fcm/register-token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                token: token,
                device_name: deviceName
            })
        });

        const result = await response.json();
        console.log('Backend response:', result);

        if (result.success) {
            console.log('✓ Push subscription registered successfully');
        } else {
            console.error('✗ Failed to register push subscription:', result.error);
            throw new Error(result.error);
        }

    } catch (error) {
        console.error('✗ Failed to register for push:', error);
        throw error;
    }
}

/**
 * Convert VAPID key from base64 string to Uint8Array
 */
function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding)
        .replace(/\-/g, '+')
        .replace(/_/g, '/');

    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);

    for (let i = 0; i < rawData.length; ++i) {
        outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
}

/**
 * Get device name based on user agent
 */
function getDeviceName() {
    const ua = navigator.userAgent;
    let deviceName = 'Unknown Device';

    if (ua.includes('Chrome')) deviceName = 'Chrome';
    else if (ua.includes('Firefox')) deviceName = 'Firefox';
    else if (ua.includes('Safari')) deviceName = 'Safari';
    else if (ua.includes('Edge')) deviceName = 'Edge';

    if (ua.includes('Mac')) deviceName += ' on Mac';
    else if (ua.includes('Windows')) deviceName += ' on Windows';
    else if (ua.includes('Linux')) deviceName += ' on Linux';
    else if (ua.includes('iPhone')) deviceName += ' on iPhone';
    else if (ua.includes('iPad')) deviceName += ' on iPad';
    else if (ua.includes('Android')) deviceName += ' on Android';

    return deviceName;
}

/**
 * Update UI to reflect notification permission status
 */
function updateNotificationUI() {
    const statusEl = document.getElementById('notification-status');
    if (!statusEl) return;

    if (notificationPermission === 'granted') {
        statusEl.innerHTML = '<span style="background: #d4edda; color: #155724; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;">✓ Enabled</span>';
    } else if (notificationPermission === 'denied') {
        statusEl.innerHTML = '<span style="background: #f8d7da; color: #721c24; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;">✗ Blocked</span>';
    } else {
        statusEl.innerHTML = '<span style="background: #e9ecef; color: #6c757d; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600;">Not Enabled</span>';
    }
}

// Initialize on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeNotifications);
} else {
    initializeNotifications();
}
