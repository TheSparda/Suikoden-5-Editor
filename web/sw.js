/* Suikoden V editor — offline service worker.
 *
 * Strategy split by origin:
 *  - Same-origin app shell + reused engine/data files: NETWORK-FIRST (fall back to
 *    cache offline). So a new deploy is picked up on the next online launch — no
 *    reinstall — yet the editor still opens with no signal.
 *  - The Pyodide CDN (large, immutable, version-pinned URLs): CACHE-FIRST, so the
 *    ~10 MB runtime downloads once and is instant thereafter.
 * Bump CACHE to purge stale offline copies. */
const CACHE = "s5editor-v2";
const SHARE_CACHE = "s5share";   // holds a file shared into the PWA; never purged

const SHELL = [
  "./", "./index.html", "./style.css",
  "./diff-core.js", "./common.js", "./app.js", "./iso.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png", "./icons/icon-512.png", "./icons/icon-maskable-512.png",
  // Reused, unchanged from the desktop editor — single source of truth.
  "../Editor/s5save.py", "../Editor/s5patch.py", "../Editor/s5fields.py",
  "../Editor/s5_characters.json", "../Editor/s5_armor_names.json", "../Editor/s5_rune_ids.json",
  "../Editor/s5_rune_names.json", "../Editor/s5_skill_names.json", "../Editor/s5_runeprice_names.json",
  "../Editor/s5_healprice_names.json", "../Editor/s5_unite_names.json", "../Editor/s5_skilleffect_names.json",
  "../Editor/s5_drop_items.json", "../Editor/s5_armor_stat_names.json", "../Editor/s5_item_names.json",
  "../Editor/s5_held_items.json", "../Editor/s5_held_items_pal.json", "../Editor/s5_ref_english.json",
  "../Editor/s5_reference.json", "../Editor/s5_enemy_names.json", "../Editor/s5_spell_names.json"
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) =>
      Promise.all(SHELL.map((u) => c.add(u).catch(() => {})))  // never brick install on a 404
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE && k !== SHARE_CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // Web Share target: a save file shared *into* the installed PWA. Stash it and
  // redirect to the app, which picks it up on boot (?shared=1).
  if (req.method === "POST" && url.pathname.endsWith("/share-target")) {
    e.respondWith((async () => {
      try {
        const form = await req.formData();
        const file = form.get("file");
        if (file) {
          const c = await caches.open(SHARE_CACHE);
          await c.put("shared-file", new Response(file, { headers: { "X-Filename": file.name || "shared.ps2" } }));
        }
      } catch (_) {}
      return Response.redirect("./index.html?shared=1", 303);
    })());
    return;
  }
  if (req.method !== "GET") return;
  const isPyodide = /(^|\.)jsdelivr\.net$/.test(url.hostname) || url.pathname.includes("/pyodide/");
  const sameOrigin = url.origin === self.location.origin;

  if (isPyodide) {
    // cache-first for the pinned, immutable runtime
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res && (res.ok || res.type === "opaque")) {
        const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    })));
    return;
  }
  if (sameOrigin) {
    // network-first for the shell so deploys are picked up; cache fallback offline
    e.respondWith(fetch(req).then((res) => {
      if (res && res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {}); }
      return res;
    }).catch(() => caches.match(req)));
  }
});
