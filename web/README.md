# Suikoden V Editor — web edition

A browser-based twin of the desktop editor with **two modes**, running **entirely on your
device** (nothing is uploaded):

- **Save editor** — opens a Suikoden V (PS2) save, edits it, saves the edited copy back.
  Works everywhere, including Android.
- **ISO / Disc editor** — edits the game disc's data tables (stats, gear, spells, runes,
  prices, enemies, unites, MP, skill effects, names) and writes only the changed bytes
  back into your ~4 GB ISO in place. Desktop Chromium only (needs the File System Access
  API); the Save editor covers everything else.

**Live:** `https://<your-user>.github.io/Suikoden-5-Editor/web/`

## How it works (and why it needs almost no new code)

Neither editor reimplements the game logic in JavaScript. The page runs the desktop
editor's own pure-Python modules **unchanged** inside [Pyodide](https://pyodide.org)
(CPython → WebAssembly):

- **Saves:** [`../Editor/s5save.py`](../Editor/s5save.py) — the picked file is written to
  `/save.bin` in Pyodide's in-memory FS, the module's normal path-based functions run
  against it, and the edited bytes are read back for download. Checksums, ECC, and the
  CodeBreaker/SharkPort containers are all handled by the trusted desktop code.
- **ISO:** [`../Editor/s5patch.py`](../Editor/s5patch.py) + `s5fields.py`. Because the
  disc is ~4 GB, we do **not** load it into memory. We read only a **~6.6 MB front-slice**
  (`file.slice(0, 0x6A0000)`) — every editable table (the serial at `0x828BD` up through
  the `0x691600` name list) lives below that offset, so the engine's absolute-offset
  reads/writes work on the slice exactly as on the full disc. On **Save**, the edited
  slice is diffed against the pristine one and **only the changed byte-runs** are written
  back into the real file in place (`createWritable({keepExistingData:true})`) at their
  absolute offsets. The disc-wide extras (overlay/DATA.PAK/portrait tooling) are desktop-
  only and intentionally omitted.

This is the same "one source of truth for offsets" discipline the desktop uses — the web
UI never guesses a byte layout.

## Quality-of-life (both modes)

- **Searchable pickers** instead of native dropdowns for big id lists (items/runes/armor/
  spells) — usable on a phone.
- **Review-before-save** — an explicit old→new list is confirmed before anything is written
  to your file/disc.
- **Dirty highlight + unsaved badge** — changed fields are marked; a sticky toolbar shows
  the pending count and keeps Save reachable.
- **Remember last opened** — a one-tap **↻ Last opened** chip (IndexedDB; for the ISO it
  stores the file *handle*, never the 4 GB of bytes).
- **Save-in-place** via File System Access (overwrite the original after a permission
  prompt), with transparent **download** fallback; **Web Share** in and out on Android
  (share a save *into* the installed PWA, and share the edited copy *out*).
- **`.s5mod` recipe** (ISO) — export your edits as a tiny JSON of byte-runs to share a
  rebalance without passing around the disc; import replays it, serial-checked and warning
  on any byte that doesn't match the author's recorded original.
- **Balance** (ISO) — a one-click Hard Mode multiplier that scales starting stats from a
  remembered baseline (re-applying doesn't compound).
- **PWA / offline** — installable; a network-first service worker keeps returning users on
  the latest deploy yet still opens offline; the pinned Pyodide runtime is cached once.

## Install as an app (PWA)

Open the live URL in Chrome and tap **⬇ Install** (or **⋮ → Install app / Add to Home
Screen**). iOS Safari: **Share → Add to Home Screen**. After the first visit it works
offline.

## Deploying on GitHub Pages

1. **Settings → Pages → Deploy from a branch → your default branch → `/ (root)`.**
2. The editor lives at `/<repo>/web/` and fetches `../Editor/*.py` + `*.json` at runtime,
   so Pages **must serve from the repo root** (not `/web`), and the `Editor/` folder must
   stay in the deployed tree (it already is).
3. A root **`.nojekyll`** is committed so Pages serves `.py` and `_`-prefixed files
   verbatim. No build step; the Pyodide version is pinned in `index.html` and `sw.js`.

## Files

| File | Purpose |
|---|---|
| `index.html` | Dual-mode app shell (Save / ISO tabs), PWA meta, script load order |
| `common.js` | Shared UI helpers: IndexedDB kv, searchable picker + review modals, theme, PWA, tabs |
| `diff-core.js` | Pure byte-diff logic (changed-run computation) — DOM-free, unit-tested |
| `app.js` | Save editor + the shared Pyodide boot and the Python glue for **both** engines |
| `iso.js` | ISO editor: slice load, all table views, diff-runs save-in-place, `.s5mod` recipe |
| `style.css` | Falena Twilight / Sun Rune theme (dark + light), mobile-first, safe-area aware |
| `manifest.webmanifest`, `sw.js`, `icons/` | PWA install + offline + Web Share target |
| `tests/` | `validate.mjs` (static), `diff-logic.mjs` (pure), `iso_roundtrip.py` (+`.mjs` wrapper), `e2e.mjs` (headless) |

## Tests

The repo ships **no ROM/ISO/saves**; tests build synthetic fixtures from the engine's own
constants, so they can't drift.

```bash
cd web && npm test        # static + pure-logic + engine round-trip
npm run test:e2e          # headless Chromium shell + mobile-overflow (skips if not installed)
```

- **`validate.mjs`** — every client JS parses; the shell is wired (script tags, both mode
  tabs); the service worker precaches the shell + every engine data file; the ISO slice
  window covers the highest table offset; the manifest declares the share target.
- **`diff-logic.mjs`** — unit tests for the pure changed-run computation.
- **`iso_roundtrip.py`** — extracts the **exact** Python glue from `app.js`, points it at a
  fabricated 6.6 MB slice, and drives the same adapters the front-end calls (load / read /
  write / re-read / hardmode / recipe) — proving the engine reuse on the slice is correct.
  The `.mjs` wrapper skips cleanly if `python3` is absent.
- **`e2e.mjs`** — headless Chromium: shell renders, both modes switch, and **no horizontal
  overflow at 320/360 px**. Self-skips if playwright/Chromium isn't installed.

## Notes & limits

- Save-editable fields match the desktop's verified set (hero/castle name, New Game Plus,
  per-character level/armor/runes/skill slots/skill ranks, recruitment). Fields whose
  offsets aren't reverse-engineered yet are intentionally not exposed.
- ISO edits apply to a **new game** — do not use emulator save-states. Back up your ISO
  (or export a recipe) before saving.
- Keep your original file until you've confirmed the edited one loads in-game.
- First load downloads a few MB of Pyodide from a CDN; later loads are cached.
