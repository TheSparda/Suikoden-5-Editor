# Suikoden V Editor, web edition

**Live:** https://thesparda.github.io/Suikoden-5-Editor/web/

This is **the** editor. The full feature list lives in the [root README](../README.md); this
file is the developer-facing companion: architecture, files, tests and deployment.

Two modes, both running entirely on the user's device:

- **Save editor**: opens a Suikoden V (PS2) save, edits it, writes the edited copy back.
  Works everywhere, Android included.
- **ISO / disc editor**: 19 tabs over the disc's data tables and, for a handful of features,
  its code. On desktop Chromium (File System Access) it writes only the changed bytes back
  into the ~4 GB ISO in place; everywhere else you can still open, edit and export a
  `.s5mod` recipe.

## How it works, and why it needs almost no new code

Neither mode reimplements the game logic in JavaScript. The page runs the repository's own
pure-Python modules **unchanged** inside [Pyodide](https://pyodide.org) (CPython compiled to
WebAssembly):

- **Saves:** [`../Editor/s5save.py`](../Editor/s5save.py). The picked file is written to
  `/save.bin` in Pyodide's in-memory FS, the module's normal path-based functions run against
  it, and the edited bytes are read back for download. Checksums, ECC and the CodeBreaker /
  SharkPort containers are all handled by the trusted engine code.
- **ISO:** [`../Editor/s5patch.py`](../Editor/s5patch.py) plus `s5fields.py`. The disc is
  ~4 GB, so we do **not** load it. We read a **~6.6 MB front slice**
  (`file.slice(0, 0x6A0000)`); every editable table, from the serial at `0x828BD` up through
  the `0x691600` name list, lives below that offset, so the engine's absolute-offset reads
  and writes work on the slice exactly as on the full disc. On **Save** the edited slice is
  diffed against the pristine one and **only the changed byte runs** go back into the real
  file in place (`createWritable({keepExistingData: true})`) at their absolute offsets.
- **Assets:** `DATA.PAK` sits ~2 GB in, past the slice, so the Assets and Field models tabs
  pull byte ranges out of the `File` on demand and hand them to the Python decoders (ISO9660
  records, Konami LZSS / `bpe`, the `dxt` texture container). Indexing costs ~600 KB of reads
  across 126 directory extents and is cached for the session.

Same "one source of truth for offsets" discipline as the CLI: the web UI never guesses a byte
layout.

## Code-level features

Most tabs are ordinary table edits. Five features patch the game's **code** instead, so they
were built against the disassembly and each carries its own test suite:

| Feature | What it rewrites | Reversible |
|---|---|---|
| **Sets** | the set detector's member immediates, handler magnitudes, the u32 jump table, the per-character gate, and custom bonuses assembled into unreferenced code space | yes |
| **Passives** | the rune equip-check pair, 8 bytes of NOPs per call site | byte for byte |
| **Dawn Rune fourth spell** | one instruction holding the spell counter at 4 | byte for byte |
| **Sun set restriction** | a 4-byte NOP, or a re-pointed `li`+`bne` to another character id | yes |
| **Field models** | a single 4-byte pointer into the resource-path table | yes, per row |

Sets, Passives and Char names are **NTSC-U only** (their PAL offsets are not mapped). The
Dawn Rune site is located by signature, so it simply does not surface on a disc without it.
Everything else works on both discs.

## Files

| File | Purpose |
|---|---|
| `index.html` | Dual-mode app shell (Save / ISO tabs), PWA meta, pinned Pyodide, script load order |
| `common.js` | Shared UI helpers: IndexedDB kv, searchable picker + review modals, theme, PWA, tabs |
| `diff-core.js` | Pure byte-diff logic (changed-run computation), DOM-free, unit-tested |
| `app.js` | Save editor + the shared Pyodide boot and the Python glue for **both** engines |
| `iso.js` | ISO editor: slice load, all 19 tab views, diff save-in-place, `.s5mod` recipe, DATA.PAK reads |
| `style.css` | Falena Twilight / Sun Rune theme (dark + light), mobile-first, safe-area aware |
| `manifest.webmanifest`, `sw.js`, `icons/` | PWA install + offline + Web Share target |
| `serve.py` | Tiny static server for local development |
| `tests/` | Static, pure-logic, engine round-trip, feature and headless-e2e suites |

The Python glue for both engines lives as a string constant in `app.js` (`GLUE`) and is
extracted verbatim by `tests/iso_roundtrip.py`, so the tested code is the shipped code.

## Tests

The repo ships **no ROM, ISO or saves**; tests build synthetic fixtures from the engine's own
constants, so they cannot drift.

```bash
cd web && npm test        # static + pure logic + engine round-trip + feature suites
npm run test:e2e          # headless Chromium shell + mobile overflow (skips if not installed)
npm run test:all          # both
```

- **`validate.mjs`**: every client script parses; the shell is wired (script tags, both mode
  tabs); the service worker precaches the shell and every engine data file; the ISO slice
  window covers the highest table offset; the manifest declares the share target.
- **`diff-logic.mjs`**: unit tests for the pure changed-run computation.
- **`iso-roundtrip.mjs`** (wrapping `iso_roundtrip.py`): drives the extracted glue against a
  fabricated 6.6 MB slice through load, read, write, re-read, hard mode and recipe. Skips
  cleanly if `python3` is absent.
- **`save-fields.mjs`**: write and read-back round-trips of every save field on every
  supported format.
- **`sets-unit.mjs`** / **`sets-iso.mjs`**: set decode and rewrite, with the Suikosource
  Armor Sets guide encoded as ground truth.
- **`runes-always.mjs`** / **`dawn-rune.mjs`**: the passive gates and the Dawn Rune counter,
  including exact restoration when a toggle goes back off.
- **`enemy-base.mjs`**: guards the region-specific enemy table bases.
- **`e2e.mjs`**: headless Chromium; the shell renders, both modes switch, and there is no
  horizontal overflow at 320 / 360 px.

## Deploying on GitHub Pages

1. **Settings → Pages → Deploy from a branch → your default branch → `/ (root)`.**
2. The editor lives at `/<repo>/web/` and fetches `../Editor/*.py` and `*.json` at runtime,
   so Pages **must serve from the repo root** (not `/web`), and the `Editor/` folder must
   stay in the deployed tree (it already is).
3. A root **`.nojekyll`** is committed so Pages serves `.py` and `_`-prefixed files verbatim.
   No build step; the Pyodide version is pinned in `index.html` and `sw.js`.
4. Every script and style URL carries a `?v=<release>` stamp, so a deploy can never serve a
   new `index.html` beside a stale `iso.js`. GitHub Pages' `max-age=600` made exactly that
   happen once. Bump the stamp with the version.

## Notes and limits

- Save-editable fields are the verified set (hero / castle / army name, Potch, Party SP, New
  Game Plus, active party, and per character level, armor, accessory, runes, skill slots,
  skill ranks, recruitment). Fields whose offsets are not reverse-engineered yet are
  intentionally not exposed.
- ISO edits apply to a **new game**. Do not use emulator save states. Back up the ISO, or
  export a recipe, before saving.
- Keep the original file until the edited one has loaded in-game.
- Overlay extraction, the overlay text editor, `.xdelta` patches, raw hex and boot-ELF string
  editing stay in the CLI / retired desktop app.
- First load downloads a few MB of Pyodide from a CDN; later loads are cached and offline.
