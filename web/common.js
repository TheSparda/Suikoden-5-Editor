/* Suikoden V web editor — shared UI helpers for both the Save editor (app.js) and
 * the ISO editor (iso.js). Loaded first; app.js and iso.js share this one global
 * script scope (helpers like $, esc, idbGet/idbSet, openPicker, confirmReview).
 *
 * Nothing here touches Pyodide or the file engines — it is pure UI plumbing:
 * DOM utils, toasts, an IndexedDB key/value store (remember-last-opened), a
 * searchable picker modal (usable on a phone with 500+ items), a review-changes
 * confirmation modal, theme + PWA install + mode-tab wiring. */

"use strict";

/* ---------- tiny DOM helpers ---------- */
const $ = (id) => document.getElementById(id);
const q = (sel, root) => (root || document).querySelector(sel);
const qa = (sel, root) => [...(root || document).querySelectorAll(sel)];
const esc = (x) => String(x == null ? "" : x)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const spin = (on) => $("spin") && $("spin").classList.toggle("on", !!on);

function toast(msg, kind) {
  const host = $("toast"); if (!host) return;
  const t = document.createElement("div");
  t.className = "tst" + (kind ? " " + kind : "");
  t.textContent = msg;
  host.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 3600);
  setTimeout(() => t.remove(), 4100);
}

function downloadBlob(bytes, filename, mime) {
  const blob = new Blob([bytes], { type: mime || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.download = filename; a.href = url;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* Build the ".edited" filename twin next to the original. */
function editedName(name, suffix) {
  suffix = suffix || ".edited";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) + suffix + name.slice(dot) : name + suffix;
}

/* ---------- IndexedDB kv (remember last opened) ---------- */
const IDB_NAME = "s5editor", IDB_STORE = "kv";
function _idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open(IDB_NAME, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(IDB_STORE);
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function idbSet(key, val) {
  try {
    const db = await _idb();
    await new Promise((res, rej) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).put(val, key);
      tx.oncomplete = res; tx.onerror = () => rej(tx.error);
    });
  } catch (_) { /* private mode / no IDB → last-opened just won't persist */ }
}
async function idbGet(key) {
  try {
    const db = await _idb();
    return await new Promise((res, rej) => {
      const tx = db.transaction(IDB_STORE, "readonly");
      const rq = tx.objectStore(IDB_STORE).get(key);
      rq.onsuccess = () => res(rq.result); rq.onerror = () => rej(rq.error);
    });
  } catch (_) { return undefined; }
}
async function idbDel(key) {
  try {
    const db = await _idb();
    await new Promise((res) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).delete(key); tx.oncomplete = res; tx.onerror = res;
    });
  } catch (_) {}
}

/* ---------- searchable picker modal ----------
 * openPicker(title, list, current, onPick, opts)
 *   list    : [{id, name, desc?, cat?}]
 *   current : currently-selected id (highlighted)
 *   onPick  : (id) => void      (id is the raw id from the list; String|Number preserved)
 *   opts    : { hideId?:bool, cap?:int }
 * Type-filters by id or name; caps the DOM to `cap` rows with a "keep typing" hint;
 * closes on pick / Escape / backdrop. The single biggest mobile win for big lists. */
function openPicker(title, list, current, onPick, opts) {
  opts = opts || {};
  const cap = opts.cap || 300;
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML =
    `<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
       <div class="modal-hd">${esc(title)}
         <button class="x" aria-label="Close">✕</button></div>
       <div class="modal-tools"><input class="pick-q" type="search"
            placeholder="filter by name or id…" autocomplete="off"></div>
       <div class="pick-list"></div>
       <div class="pick-hint note"></div>
     </div>`;
  const listEl = q(".pick-list", back), hintEl = q(".pick-hint", back);
  const input = q(".pick-q", back);
  function close() { back.remove(); document.removeEventListener("keydown", onKey); }
  function choose(id) { close(); onPick(id); }
  function render(f) {
    f = (f || "").trim().toLowerCase();
    const rows = list.filter((it) => !f
      || String(it.id).toLowerCase().includes(f)
      || String(it.name || "").toLowerCase().includes(f)
      || String(it.desc || "").toLowerCase().includes(f));
    const shown = rows.slice(0, cap);
    listEl.innerHTML = shown.map((it) => {
      const sel = String(it.id) === String(current) ? " sel" : "";
      const idtag = opts.hideId ? "" : `<span class="pick-id">${esc(it.id)}</span>`;
      const meta = [it.cat, it.desc].filter(Boolean).map(esc).join(" · ");
      return `<button class="pick-row${sel}" data-id="${esc(it.id)}">
        ${idtag}<span class="pick-name">${esc(it.name)}</span>
        ${meta ? `<span class="pick-desc note">${meta}</span>` : ""}</button>`;
    }).join("") || `<div class="note" style="padding:14px">No matches.</div>`;
    hintEl.textContent = rows.length > shown.length
      ? `Showing ${shown.length} of ${rows.length} — keep typing to narrow.` : "";
  }
  listEl.addEventListener("click", (e) => {
    const b = e.target.closest(".pick-row"); if (b) choose(b.dataset.id);
  });
  q(".x", back).onclick = close;
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  input.addEventListener("input", () => render(input.value));
  function onKey(e) { if (e.key === "Escape") close(); }
  document.addEventListener("keydown", onKey);
  document.body.appendChild(back);
  render("");
  input.focus();
}

/* ---------- review-changes confirmation modal ----------
 * confirmReview(title, groups, destLabel, onConfirm)
 *   groups   : [{title, rows:[{label, from, to}]}]  (only *effective* changes)
 *   destLabel: confirm-button text ("Apply & save to foo.ps2" / "…download" / "…share")
 * Resolves nothing until the user confirms; returns immediately after showing. */
function confirmReview(title, groups, destLabel, onConfirm) {
  const total = groups.reduce((s, g) => s + g.rows.length, 0);
  if (!total) { toast("No effective changes to apply.", "ok"); return; }
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML =
    `<div class="modal" role="dialog" aria-modal="true" aria-label="Review changes">
       <div class="modal-hd">Review ${total} change${total === 1 ? "" : "s"}${title ? " · " + esc(title) : ""}
         <button class="x" aria-label="Close">✕</button></div>
       <div class="review-list">${groups.map((g) => `
         <div class="review-grp">${g.title ? `<div class="review-gt">${esc(g.title)}</div>` : ""}
           ${g.rows.map((r) => `<div class="review-row">
             <span class="review-lbl">${esc(r.label)}</span>
             <span class="review-old">${esc(r.from)}</span>
             <span class="review-arr">→</span>
             <span class="review-new">${esc(r.to)}</span></div>`).join("")}
         </div>`).join("")}</div>
       <div class="modal-ft">
         <button class="ghost x2">Cancel</button>
         <button class="confirm">${esc(destLabel)}</button></div>
     </div>`;
  function close() { back.remove(); }
  q(".x", back).onclick = close; q(".x2", back).onclick = close;
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  q(".confirm", back).onclick = () => { close(); onConfirm(); };
  document.body.appendChild(back);
}

/* ---------- theme (shared by both modes) ---------- */
function initTheme() {
  const toggle = $("themeToggle"); if (!toggle) return;
  const apply = (light) => {
    document.body.classList.toggle("light", light);
    const m = q('meta[name="theme-color"]'); if (m) m.content = light ? "#dfe7f2" : "#0b1524";
  };
  toggle.addEventListener("change", (e) => {
    apply(e.target.checked);
    try { localStorage.setItem("s5theme", e.target.checked ? "light" : "dark"); } catch (_) {}
  });
  try { if (localStorage.getItem("s5theme") === "light") { toggle.checked = true; apply(true); } } catch (_) {}
}

/* ---------- PWA install button (Chromium/Android) ---------- */
function initPWA() {
  if ("serviceWorker" in navigator)
    navigator.serviceWorker.register("sw.js").catch(() => {});
  const btn = $("installBtn"); if (!btn) return;
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (standalone) return;
  let deferred = null;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault(); deferred = e; btn.classList.remove("hidden");
  });
  btn.onclick = async () => {
    if (!deferred) return;
    deferred.prompt(); await deferred.userChoice; deferred = null; btn.classList.add("hidden");
  };
  window.addEventListener("appinstalled", () => btn.classList.add("hidden"));
}

/* ---------- mode tabs (Save / ISO) ---------- */
function initModeTabs() {
  const tabs = qa(".mode-tab"); if (!tabs.length) return;
  const show = (mode) => {
    tabs.forEach((t) => t.classList.toggle("on", t.dataset.mode === mode));
    qa(".mode-pane").forEach((p) => { p.hidden = p.dataset.mode !== mode; });
    try { localStorage.setItem("s5mode", mode); } catch (_) {}
    if (mode === "iso" && typeof window.onIsoModeShown === "function") window.onIsoModeShown();
  };
  tabs.forEach((t) => t.addEventListener("click", () => show(t.dataset.mode)));
  let init = "save";
  try { init = localStorage.getItem("s5mode") || "save"; } catch (_) {}
  show(init);
}

/* feature detection shared by both editors */
const HAS_FS_ACCESS = typeof window !== "undefined" && "showOpenFilePicker" in window;
function canShareFiles(files) {
  try { return !!(navigator.canShare && navigator.canShare({ files })); } catch (_) { return false; }
}
