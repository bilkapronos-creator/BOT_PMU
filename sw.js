const CACHE_NAME = 'mtech-v1';
const ASSETS = [
  './index.html',
  './manifest.json'
];

// Installation du Service Worker et mise en cache de l'interface
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

// Stratégie de Cache : Réseau d'abord, sinon Cache
self.addEventListener('fetch', (e) => {
  e.respondWith(
    fetch(e.request).catch(() => {
      return caches.match(e.request);
    })
  );
});