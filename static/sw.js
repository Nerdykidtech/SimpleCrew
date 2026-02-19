const CACHE_NAME = 'simple-finance-v10';

// Only pre-cache truly static assets (images, manifest) — NOT JS/CSS
// JS and CSS are always fetched fresh from the network
const urlsToCache = [
    '/manifest.json',
    '/static/images/192.png',
    '/static/images/512.png',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => Promise.allSettled(
                urlsToCache.map(url =>
                    cache.add(url).catch(err => console.warn('Failed to pre-cache:', url, err))
                )
            ))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        Promise.all([
            // Wipe all old caches
            caches.keys().then(names =>
                Promise.all(names.map(name => {
                    if (name !== CACHE_NAME) {
                        console.log('[SW] Deleting old cache:', name);
                        return caches.delete(name);
                    }
                }))
            ),
            // Take control of all open tabs immediately
            clients.claim()
        ])
    );
});

self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // ── API calls ──────────────────────────────────────────────
    // Network only. Never serve API responses from cache.
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(
            fetch(event.request).catch(() => {
                if (event.request.method === 'GET') {
                    return new Response(JSON.stringify({ error: 'Offline' }), {
                        status: 503,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
            })
        );
        return;
    }

    // ── JS, CSS, HTML navigation ───────────────────────────────
    // Always network first so deploys are picked up immediately.
    // Cache the response so the app still works offline.
    if (
        event.request.mode === 'navigate' ||
        url.pathname.startsWith('/static/js/') ||
        url.pathname.startsWith('/static/css/')
    ) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                    }
                    return response;
                })
                .catch(() => caches.match(event.request))
        );
        return;
    }

    // ── Everything else (images, fonts, manifest) ──────────────
    // Cache first — these never change between deploys.
    event.respondWith(
        caches.match(event.request)
            .then(cached => cached || fetch(event.request))
    );
});

// ── Web Push Notifications ─────────────────────────────────────

self.addEventListener('push', event => {
    let notificationData = {
        title: 'SimpleCrew',
        body: 'New notification',
        icon: '/static/images/192.png',
        badge: '/static/images/badge.png'
    };

    if (event.data) {
        try {
            const payload = event.data.json();
            notificationData.title = payload.notification?.title || notificationData.title;
            notificationData.body = payload.notification?.body || notificationData.body;
        } catch (e) {
            notificationData.body = event.data.text();
        }
    }

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

self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(clientList => {
                for (const client of clientList) {
                    if (client.url.includes(self.location.origin) && 'focus' in client) {
                        return client.focus();
                    }
                }
                if (clients.openWindow) return clients.openWindow('/');
            })
    );
});
