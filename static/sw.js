const CACHE_NAME = 'simple-finance-v2';
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
    'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap',
    'https://d31s10tn3clc14.cloudfront.net/imgs/deposits/Review+Logos/simple-logo.png'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('Opened cache');
                return cache.addAll(urlsToCache);
            })
    );
});

self.addEventListener('fetch', event => {
    // API strategy: Network first, fall back to cache for data consistency
    if (event.request.url.includes('/api/')) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    // Clone the response to cache it
                    if(!response || response.status !== 200 || response.type !== 'basic') {
                        return response;
                    }
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME)
                        .then(cache => {
                            cache.put(event.request, responseToCache);
                        });
                    return response;
                })
                .catch(() => {
                    // Fallback to cache if network fails
                    return caches.match(event.request);
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