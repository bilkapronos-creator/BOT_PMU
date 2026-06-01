const CACHE_NAME = 'velora-v11';
const ASSETS = [
  '/',
  '/index.html',
  '/config.js',
  '/manifest.json',
  '/icons/icon.svg'
];

function estNavigationPageStatique(url) {
  const path = url.pathname.replace(/\/+$/, '') || '/';
  return path === '/landing'
    || path === '/landing.html'
    || path === '/legales'
    || path === '/legales.html'
    || path === '/admin.html';
}

// Installation du Service Worker et mise en cache de l'interface
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

// Stratégie de Cache : Réseau d'abord, sinon Cache (hors pages statiques publiques)
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.mode === 'navigate' && estNavigationPageStatique(url)) {
    return;
  }

  e.respondWith(
    fetch(e.request).catch(() => {
      return caches.match(e.request);
    })
  );
});
