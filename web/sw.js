/* Suikoden V Save Editor — offline service worker.
   Caches the app shell + the reused editor Python module and name tables so the
   editor opens offline after the first visit. The Pyodide runtime itself is
   fetched from a CDN; we cache those responses opaquely on first use (best effort)
   so a warm install can boot offline too. Bump CACHE when any shell file changes. */
const CACHE = "s5save-v1";

// Same-origin app shell. Paths are relative to the SW scope (the web/ folder).
const SHELL = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-maskable-512.png",
  // Reused, unchanged from the desktop editor — single source of truth.
  "../Editor/s5save.py",
  "../Editor/s5_characters.json",
  "../Editor/s5_armor_names.json",
  "../Editor/s5_rune_ids.json"
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) =>
      // addAll fails the whole install if any file 404s; add individually so a
      // missing optional file never bricks the install.
      Promise.all(SHELL.map((u) => c.add(u).catch(() => {})))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const isPyodide = /(^|\.)jsdelivr\.net$/.test(url.hostname) ||
                    url.pathname.includes("/pyodide/");

  // Cache-first for the shell + already-cached Pyodide chunks; fall back to the
  // network and stash a copy (opaque for cross-origin Pyodide files).
  e.respondWith(
    caches.match(req).then((hit) => {
      if (hit) return hit;
      return fetch(req).then((res) => {
        const sameOrigin = url.origin === self.location.origin;
        if (res && (res.ok || res.type === "opaque") && (sameOrigin || isPyodide)) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }).catch(() => hit);
    })
  );
});
