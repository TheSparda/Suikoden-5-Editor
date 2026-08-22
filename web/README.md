# Suikoden V Save Editor — web edition

A browser-based twin of the desktop save editor. It opens a Suikoden V (PS2) save,
lets you edit it, and downloads the edited save back — **entirely on your device**.
The save file is never uploaded anywhere.

**Live:** `https://<your-user>.github.io/Suikoden-5-Editor/web/`

## What it does

- Opens PS2 memory-card images (`.ps2 .mc2 .mcd .bin`), single-save exports (`.psu`),
  and standalone saves (`.cbs .xps .sps`).
- Edits the same **verified** fields as the desktop editor:
  - Hero name, Castle name, New Game Plus flag
  - Per-character: level, equipped armor (helm/body/gloves/boots), equipped runes,
    equipped skill slots, all skill ranks
  - Recruitment (per character, "Recruit ALL", and a full roster grid)
- Downloads the edited copy as `<name>.edited.<ext>`. Your original file is never touched.

## How it works (and why it needs no rewrite)

The save logic is **not** reimplemented in JavaScript. The page runs the desktop
editor's own pure-Python module — [`../Editor/s5save.py`](../Editor/s5save.py) —
**unchanged** inside [Pyodide](https://pyodide.org) (CPython compiled to WebAssembly):

1. `loadPyodide()` boots CPython in WebAssembly.
2. `s5save.py` is fetched and written into Pyodide's in-memory filesystem.
3. Your picked file is written to `/save.bin` in that same in-memory FS.
4. The module's normal **path-based** functions run against `/save.bin`
   (`read_all_saves`, `read_individual_save`, `read_gamedata_payload`,
   `read_all_characters`, `write_save_fields`, `write_individual_save`).
5. The edited bytes are read back out of `/save.bin` and handed to the browser as a
   download.

Checksums, ECC, and the CodeBreaker/SharkPort container formats are all handled by
the trusted desktop code. Suikoden V saves have **no body checksum** (confirmed by
reverse-engineering the ELF), so verified-field edits load in-game; the memory card's
hardware ECC is refreshed on every card write.

Dropdown names come from the same tables the desktop app uses
(`s5_characters.json`, `s5_armor_names.json`, `s5_rune_ids.json`).

## Install on Android (or desktop) as an app

It's a PWA. Open the live URL in Chrome, then **⋮ → Add to Home screen / Install app**.
After the first visit it works offline (an app-shell service worker caches the page,
the Python module, and — best effort — the Pyodide runtime).

## Deploying on GitHub Pages

1. Repo **Settings → Pages → Build and deployment → Source: "Deploy from a branch"**,
   pick your default branch and `/ (root)`.
2. The editor lives at `/<repo>/web/`. It fetches `../Editor/*.py` and `../Editor/*.json`
   at runtime, so keep the `Editor/` folder in the deployed tree (it already is).
3. That's it — no build step. It's three static files plus icons.

## Files

| File | Purpose |
|---|---|
| `index.html` | Page shell + Pyodide script tag |
| `app.js` | Pyodide bootstrap, the Python glue, and all UI logic |
| `style.css` | Falena Twilight / Sun Rune theme (dark + light) |
| `manifest.webmanifest`, `sw.js`, `icon-*.png` | PWA install + offline support |

## Notes & limits

- This is the **save** editor only. The desktop app's ISO patcher needs the game disc
  and can't run in the browser.
- Keep your original save until you've confirmed the edited one loads in-game.
- First load downloads a few MB of Pyodide from a CDN; later loads are cached.
