/**
 * AirScout Service Worker v2
 * ==========================
 *
 * Offline support, push notifications, and background sync.
 */

const CACHE_NAME = 'airscout-v2';
const STATIC_ASSETS = [
    '/',
    '/index.html',
    '/manifest.json',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
    'https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css',
    'https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js',
    'https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(STATIC_ASSETS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(names => Promise.all(
                names.filter(n => n !== CACHE_NAME).map(n => caches.delete(n))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;

    const url = new URL(event.request.url);

    if (url.hostname.includes('supabase') ||
        url.hostname.includes('airnowapi') ||
        url.hostname.includes('openweathermap') ||
        url.hostname.includes('router.project-osrm') ||
        url.hostname.includes('nominatim')) {
        return;
    }

    event.respondWith(
        caches.match(event.request).then(cached => {
            if (cached) return cached;

            return fetch(event.request).then(response => {
                if (!response || response.status !== 200) return response;

                const clone = response.clone();
                caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                return response;
            });
        }).catch(() => caches.match('/'))
    );
});

self.addEventListener('push', event => {
    let data = {
        title: 'AirScout Alert',
        body: 'New hazard detected on your route',
        icon: '/icons/icon-192.png',
        badge: '/icons/icon-72.png'
    };

    if (event.data) {
        try {
            data = { ...data, ...event.data.json() };
        } catch (e) {
            data.body = event.data.text();
        }
    }

    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: data.icon,
            badge: data.badge,
            vibrate: [100, 50, 100],
            data: { url: data.url || '/', dateOfArrival: Date.now() },
            actions: [
                { action: 'view', title: 'View Map' },
                { action: 'dismiss', title: 'Dismiss' }
            ]
        })
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    if (event.action === 'dismiss') return;

    const urlToOpen = event.notification.data?.url || '/';
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(windowClients => {
                for (const client of windowClients) {
                    if (client.url === urlToOpen && 'focus' in client) {
                        return client.focus();
                    }
                }
                if (clients.openWindow) return clients.openWindow(urlToOpen);
            })
    );
});

self.addEventListener('sync', event => {
    if (event.tag === 'check-route-hazards') {
        event.waitUntil(checkRouteHazards());
    }
});

async function checkRouteHazards() {
    console.log('[SW] Background route hazard check');
}
