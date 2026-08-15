// Service worker minimal — rend l'app installable ("Sur l'écran d'accueil") et met en
// cache l'app shell (JS/CSS/icônes) pour un lancement plus rapide. Les appels à l'API
// backend (/api/...) ne sont volontairement PAS mis en cache : les données de scan
// doivent toujours être fraîches, jamais servies depuis un cache obsolète.
const CACHE_NAME = "mln-scan-shell-v1";
const APP_SHELL = ["/", "/manifest.json", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Jamais de cache pour les appels API : toujours du réseau, données live
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  // App shell : cache-first avec repli réseau, pour un chargement quasi instantané
  event.respondWith(
    caches.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).catch(() => cached);
    })
  );
});
