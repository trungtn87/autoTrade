// Cache only public app assets. NEVER cache API responses, login, or private dashboard HTML.
const CACHE = 'autotrade-pwa-shell-v1';
const ASSETS = ['/static/manifest.webmanifest','/static/icon.svg','/static/icon-192.png','/static/icon-512.png','/static/app.css','/static/app.js'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS))));
self.addEventListener('activate', e => e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))));
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || !u.pathname.startsWith('/static/')) return;
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
