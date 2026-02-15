const CACHE_NAME = 'simple-finance-v7';
const urlsToCache = [
    '/',
    '/manifest.json',
    // CSS files
    '/static/css/main.css',
    '/static/css/navigation.css',
    '/static/css/components.css',
    '/static/css/modals.css',
    '/static/css/mobile.css',
    // JS - Utilities
    '/static/js/utils/formatters.js',
    '/static/js/utils/helpers.js',
    '/static/js/state.js',
    // JS - API layer
    '/static/js/api/cards.js',
    '/static/js/api/goals.js',
    '/static/js/api/expenses.js',
    '/static/js/api/transactions.js',
    '/static/js/api/family.js',
    '/static/js/api/credit.js',
    // JS - UI layer
    '/static/js/ui/navigation.js',
    '/static/js/ui/dialogs.js',
    '/static/js/ui/modals.js',
    '/static/js/ui/filters.js',
    '/static/js/ui/rendering.js',
    // JS - Features
    '/static/js/features/dragdrop.js',
    '/static/js/features/groups.js',
    '/static/js/features/autorefresh.js',
    // JS - Main
    '/static/js/app.js',
    // External resources
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap'
    // Note: Removed CloudFront logo due to CORS - it will be fetched from network instead
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('Opened cache');
                // Add URLs one by one, ignoring failures (e.g., CORS errors)
                return Promise.allSettled(
                    urlsToCache.map(url =>
                        cache.add(url).catch(err => {
                            console.warn('Failed to cache:', url, err);
                            return null;
                        })
                    )
                );
            })
            .then(() => {
                console.log('Service worker installed successfully');
                self.skipWaiting(); // Activate immediately
            })
    );
});

self.addEventListener('fetch', event => {
    // API strategy: Network first, fall back to cache for data consistency
    if (event.request.url.includes('/api/')) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    // Only cache GET requests (Cache API doesn't support POST/PUT/etc)
                    if(event.request.method === 'GET' && response && response.status === 200 && response.type === 'basic') {
                        const responseToCache = response.clone();
                        caches.open(CACHE_NAME)
                            .then(cache => {
                                cache.put(event.request, responseToCache);
                            });
                    }
                    return response;
                })
                .catch(() => {
                    // Fallback to cache if network fails (only GET requests are cached)
                    if (event.request.method === 'GET') {
                        return caches.match(event.request);
                    }
                    return new Response(JSON.stringify({error: 'Network error'}), {
                        status: 503,
                        headers: {'Content-Type': 'application/json'}
                    });
                })
        );
    } else {
        // Static assets: Cache first
        event.respondWith(
            caches.match(event.request)
                .then(response => {
                    if (response) {
                        return response;
                    }
                    return fetch(event.request);
                })
        );
    }
});

self.addEventListener('activate', event => {
    const cacheWhitelist = [CACHE_NAME];
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheWhitelist.indexOf(cacheName) === -1) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
});

// --- WEB PUSH NOTIFICATIONS ---

// Listen for push notifications
self.addEventListener('push', event => {
    console.log('[Service Worker] Push received:', event);

    let notificationData = {
        title: 'SimpleCrew',
        body: 'New notification',
        icon: '/static/images/192.png',
        badge: '/static/images/badge.png'
    };

    // Parse notification data
    if (event.data) {
        try {
            const payload = event.data.json();
            notificationData.title = payload.notification?.title || notificationData.title;
            notificationData.body = payload.notification?.body || notificationData.body;
        } catch (e) {
            console.error('[Service Worker] Error parsing push data:', e);
            notificationData.body = event.data.text();
        }
    }

    // Show notification
    event.waitUntil(
        self.registration.showNotification(notificationData.title, {
            body: notificationData.body,
            icon: notificationData.icon,
            badge: notificationData.badge,
            tag: 'simplecrew-sync',
            requireInteraction: false,
            vibrate: [200, 100, 200]
        })
    );
});

// Handle notification click
self.addEventListener('notificationclick', event => {
    console.log('[Service Worker] Notification clicked:', event);

    event.notification.close();

    // Open or focus the app
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(clientList => {
                // Try to focus existing window
                for (const client of clientList) {
                    if (client.url.includes(self.location.origin) && 'focus' in client) {
                        return client.focus();
                    }
                }
                // Open new window if none exists
                if (clients.openWindow) {
                    return clients.openWindow('/');
                }
            })
    );
});