// Versioned PWA shell: prefer the network for executable CSS/JS assets.
// Never cache dashboard HTML/API, and update installed PWA tabs immediately.
const CACHE = 'autotrade-pwa-shell-v10';
const VERSION = '20261030-excel-reconciled-v2';
const ASSETS = [
  '/static/manifest.webmanifest', '/static/icon.svg',
  '/static/icon-192.png', '/static/icon-512.png',
  '/static/app.css?v=' + VERSION, '/static/app.js?v=' + VERSION
];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key !== CACHE).map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const u = new URL(event.request.url);
  if (event.request.method !== 'GET' || !u.pathname.startsWith('/static/')) return;
  if (u.pathname.endsWith('/app.js') || u.pathname.endsWith('/app.css')) {
    // Network first prevents yesterday's app.js from drawing today's HTML.
    event.respondWith(fetch(event.request, {cache:'no-store'}).then(response => {
      if (response.ok) {
        const snapshot = response.clone();
        event.waitUntil(caches.open(CACHE).then(cache => cache.put(event.request, snapshot)));
      }
      return response;
    }).catch(() => caches.match(event.request)));
  } else {
    event.respondWith(caches.match(event.request).then(hit => hit || fetch(event.request)));
  }
});
