# Suikoden V Editor

**🌐 Open the editor → https://thesparda.github.io/Suikoden-5-Editor/web/**

A **browser-based save and ISO editor for Suikoden V (PS2)**. No install, no Python, no
localhost server, nothing uploaded. The page runs this repository's own verified Python
engine inside [Pyodide](https://pyodide.org), reads your files straight off your device,
and writes the edited bytes back to you.

The repo ships **no game ROM, ISO, save or story assets**. Bring your own legally obtained
copy.

> 💬 **Feature requests and support:** the Toran Castle Discord,
> https://discord.gg/KesHMX5P2Z

> 🖥️ There is also a legacy desktop app (`Editor/s5editor.py`). It is **retired**: it still
> runs, but every new feature lands in the web editor. See
> [The desktop editor](#the-desktop-editor-retired).

Current release: **v1.17.0**.

## Two modes

| | **Save editor** | **ISO / disc editor** |
|---|---|---|
| What it edits | A real PS2 save: your current playthrough | The game disc's own data and code: applies to a **new game** |
| Where it runs | Everywhere, phones included | Full in-place writing on desktop Chromium (Chrome / Edge / Brave / Opera); elsewhere you can still open, edit and export a `.s5mod` recipe |
| Formats | `.ps2` `.mc2` `.mcd` `.bin` memory cards, `.psu`, `.xps` / `.sps`, `.cbs` | `.iso` (also `.bin` / `.img`) |
| Regions | NTSC-U, PAL, NTSC-J | NTSC-U (`SLUS-21291`) and PAL (`SLES-54087`), auto-detected from the serial |
| Output | Overwrite in place, download a copy, or Web Share it (Android) | Only the changed byte runs written into the 4 GB file in place, or a `.s5mod` recipe |

Every editable field was reverse-engineered and **verified**: against public stat guides,
the game's own data tables, and (for saves and for the code-level features) the game's
disassembly. Anything that could not be confirmed is shown read-only or left out rather
than guessed.

## Quick start

1. Open **https://thesparda.github.io/Suikoden-5-Editor/web/**. First load fetches a few MB
   of Pyodide; after that it is cached and works offline.
2. Pick a mode at the top: **Save Editor** or **ISO Editor**.
3. **Save mode:** drop a save file in, or **Choose file…**. Every save on a memory card is
   listed. Edit, press **Save**, confirm the review list.
4. **ISO mode:** **Choose ISO…**. The serial is checked and the region is shown. Edit across
   the tabs, press **Save**, confirm the review list. On desktop Chromium the ISO is
   rewritten in place; elsewhere you get a `.s5mod` recipe to apply later.
5. A **↻ Last opened** chip re-opens the file you used last (for the ISO it remembers the
   file handle, never the 4 GB of bytes).

> ISO edits change the game's **base data**, so they take effect on a **new game**. To
> change a playthrough you already started, use the Save editor. Use real in-game saves,
> not emulator save states. Keep your original file until the edited one has loaded in-game.

## Save editor

Opens a PS2 save, edits it, and writes the edited copy back. `.cbs` (CodeBreaker) is
transparently decrypted and re-encrypted; `.xps` / `.sps` (SharkPort / X-Port) are patched
in place; memory-card **ECC is recomputed** on every card write.

The save layout was reverse-engineered from the game itself. The save file is a verbatim
image of the game's save RAM, and disassembling the save routines proved there is **no body
checksum** (the game validates three fixed header values only), so field edits are safe.

**Per save slot:**

- **Names**: hero name, castle name, **army name** (up to 15 characters). The hero and
  castle names are each stored twice and the editor writes both copies, because updating
  only one makes the game disagree with itself about your own name.
- **Resources**: **Potch** (up to 99,999,999) and **Party SP** (up to 999,999), each with a
  one-tap **Max**.
- **New Game Plus**: the cleared-game flag that enables fast-forward.
- **Active party**: the 6 battle slots and 4 support slots, picked from that save's own
  recruited roster (unrecruited characters are listed last and labelled unavailable). The
  hero slot is locked because he always leads. Putting one character in two slots raises a
  warning rather than blocking you.
- **Level and playtime** are shown read-only, as the save-select screen reports them.

**Characters panel**, for any of the 120 characters:

- **Level** (1 to 99) and a **Recruited** toggle.
- **Equipped armor**: Helm, Armor, Gloves, Boots **and Accessory**, picked by name.
- **Equipped runes**: Head, Right hand, Left hand, using the full **92-rune name table**
  extracted from the ISO (verified against innate runes: Zerase = Star, the Prince = Dawn).
- **Equipped battle-skill slots**: the two active slots.
- **All 48 skill ranks** (None through SS). Support skills (Cook, Forge, Tutor and the rest)
  use the same scale as their fixed in-game grades.

**Recruitment panel**: the 108 Stars as a searchable checkbox roster, with a live recruited
counter, a name filter, check / uncheck all, a one-click **Recruit ALL**, and a write that
touches only the flags you changed. Story-only characters and antagonists stay locked so
saves remain consistent.

Verified by an automated suite: full write, read-back round-trips of every field on every
supported format (79 checks, NTSC-U and PAL).

## ISO / disc editor

Nineteen tabs, all driving the same verified engine. Every field write goes straight into
the working copy, is highlighted as dirty, is listed in the review-before-save dialog, and
can be reverted individually (↺) or undone as a whole (Ctrl+Z / Ctrl+Y, 300 steps).

### Characters

Per character, indexed by the in-game roster:

- **Stats and growths**: base HP, Attack, Technique, Magic, Evasion, PDF, MDF, Speed, Luck,
  plus each stat's per-level growth.
- **Affinities**: elemental affinity grade (None, E, D, C, B, A, S) for all 14 elements
  (Sun, Fire, Lightning, Wind, Water, Earth, Star, Sound, Holy, Dark, Slash, Thrust, Punch,
  Shoot).
- **Equipable skills**: the maximum rank (None through SS) this character can equip each of
  the 48 skills at, with one-click presets for **Max all (SS)**, **All S** and **Clear**.
- **Weapon growth**: attack power at each of the 16 sharpen levels.
- **Starting equipment**: Head, Body, Arm, Feet armor for a new game.
- **Starting items**: up to four held items or accessories a unit begins with.

### Gear

Five slots (Head, Body, Arm, Foot, Accessory) with English item names, verified against the
Armor List guide: DEF, buy and sell price, weight **Type** plus its SPD penalty, stat
bonuses, proc effects (auto-heal, drain, counter and so on), and **per-element ATK and DEF**
for all 14 elements.

You can also **rename any piece of gear and rewrite its description** (English only; the
other four languages share a packed pool that cannot be relocated safely). Both are capped
to what the disc record actually has room for, with a live character counter. The cap is
computed per record rather than assumed, because 15 of 219 armor records genuinely hold data
immediately after the description.

### Sets

The 9 armor sets (Fish, Prosperity, Pale Moon, Destiny, Guardian, Classic, Samurai,
Windspun, Sun) with their completion bonuses fully editable. Set bonuses live in **code**,
not in a data table, so this panel edits the game's own handlers:

- **Members**: swap any of the five slots (Head, Body, Arm, Foot, **Accessory**) for any
  other item, so a set can be built out of whatever gear you like. Each member's description
  is editable alongside it.
- **Bonus magnitudes**: change what a completed set grants and by how much, across **26
  targets**: HP, Attack, Magic Defense, Speed, Critical %, Double critical % and the other
  stats, plus all 14 elemental affinities as grades (E through S).
- **Effect used**: the dispatcher is a plain u32 jump table, so any set can be given any
  other set's bonus with a pure data edit.
- **Custom bonuses**: add effects a set never had, assembled into verified unreferenced code
  space. Numeric targets offer *add* (stack onto the wearer's value) and *force to* (a flat
  value); affinities are ranks, so those rows lock to *force to* and you pick the tier by
  name. Capacity is reported live (currently 6 add-style or 9 set-style effects, and only
  one set can hold a custom bonus at a time since they share the code space).
- **Bonus description text**: rewrite what the game says the set does.
- **Per-character restriction**: the Sun set's "Prince only" check can be **removed**
  entirely (a 4-byte NOP, so anyone wearing the full set benefits) or **retargeted** to
  another character.

Every set's members and documented bonus are cross-checked against the Suikosource Armor
Sets guide, which the test suite encodes as ground truth. Honestly reported limits:
Destiny's 20% revival chance is not in its handler (it runs on a separate code path), and
Prosperity's Potch doubling and Pale Moon's per-turn heal are jump-table no-ops. Those three
are shown but not editable rather than faked.

### Spells

Per spell: element, damage or heal power, target shape (single, all, row, column, cluster)
and status effect, with the readable description built from those fields.

### Runes

- Which contiguous spell range each **rune** teaches (the rune to spell grant table), plus a
  **custom spell-set builder**: pick the first spell and how many levels follow.
- Every spell the selected rune currently teaches is editable inline, so you can retune a
  rune without leaving the tab.
- **Dawn** and **Twilight** are in the list: they turned out to be real grant records, not
  the fixed spell sets they used to be shown as.
- **Dawn Rune, fourth spell**: unlock **Crimson Sky** from the start instead of near the
  endgame. It is the one rune whose spell count the game reads from a runtime counter that
  the story lowers, so the toggle rewrites a single instruction to hold that counter at 4,
  reaching the same end state as the community `.pnach` cheat without an emulator. Untick it
  and the disc is byte-identical again. The Prince still needs the magic level to cast it.
  The patch site is located by signature, so the toggle simply does not appear on a disc
  that does not carry it.

### Passives

Force a rune's overworld effect to be **permanently active**, so nobody has to equip the
rune and the slot stays free:

| Rune | Effect |
|---|---|
| Champion's Rune | fewer encounters, suppresses weak enemies |
| Great Firefly Rune | more encounters, increases enemy appearances |
| Fortune Rune | all party members receive 2x experience |
| Prosperity Rune | double Potch after every battle |
| Godspeed Rune | 2x field movement speed, and a 100% escape rate |

These runes gate their effect behind *is it equipped* **and** *is its flag set*. Both checks
branch to the same target, so replacing the pair with NOPs leaves only the active path. That
is 8 bytes per call site, and switching a toggle back off restores the disc byte for byte.
The effect wording is read from the disc's own description pool and cross-checked against
its separate rune-name pool.

**Verified:** the equip check really is bypassed and nothing else on the disc moves.
**Not verified in-game:** how strongly each effect then behaves. The resolver only selects an
effect id, so this is an on/off switch, not a rate slider (the encounter-rate *value* lives
in map script data, not in the executable). The Raven Rune's dungeon evasion would be a fine
addition but has no forceable gate, so it is deliberately excluded.

### Prices, Rune prices, Heal prices

Three filterable tables: buy and sell for items and equipment, rune (orb) prices, and
healing-item prices, each row labelled with its real English name.

### Enemies

Level, combat stats (HP, Attack, Technique, Accuracy, Magic, Evasion, PDF, MDF, Speed,
Luck), Potch and Skill Point rewards, per-enemy elemental affinities (E through S), and the
five item-drop slots picked by item name, verified against the in-game drop table.

Every stat is a **u16 (0 to 65535)**, which is the ceiling on a modded enemy; the editor caps
at it rather than refusing the write. For across-the-board changes use **Balance** or
**Excel / CSV**.

### Unites

All 49 unite attacks, verified against the Unites guide, with their participant slots
editable through full-roster pickers. Member count is fixed (the table is packed) and the
damage and target are engine-driven, so those are shown for reference.

### MP growth and Skill effects

- **MP growth**: the MP-cost thresholds for each magic level (Lv1 to Lv4), a global table.
- **Skill effects**: the magnitude of each of the 165 skills at every rank (E through SS),
  global and filterable.

### Balance

- **Characters**: scale every character's starting stats by a factor. A quick Hard Mode.
- **Enemies**: scale every enemy's combat stats, with a **separate multiplier for HP** so you
  can make enemies tankier without making them hit harder. Potch and skill-point rewards,
  elemental affinities and item drops are deliberately left alone, because those change the
  economy rather than the difficulty.

Both remember the original values on first apply, so re-applying never compounds, **Restore**
is exact, and anything past a field's ceiling is capped rather than failing.

### Excel / CSV

Export any of **eleven data tables** as CSV, bulk-edit it in Excel, Sheets or LibreOffice,
and import it back:

`Characters — stats & growths`, `— elemental affinities`, `— equipable-skill caps`,
`— weapon growth`, `— starting equipment`, `Enemies`, `Spells`, `Runes`, `Prices`,
`Skill effects`, `MP growth`.

`Spells` is all 106 spell records (element, power / heal, target, status). `Runes` is the
rune → spell grant table: a rune teaches the contiguous run
`Start spell … Start spell + Spell count - 1`, one spell per rune level, so those two columns
are both *which* spells a rune teaches and *how many levels* it has. Export both together,
since a rune's `Start spell` is an id from the `Spells` sheet.

Import writes only the cells that changed, with the same range validation, backup and recipe
recording as a tab edit. Blank cells and Excel quirks (BOM, `12.0` decimals) are handled.
Three things it will not let you get wrong:

- a value **too big for its field is capped** at that field's maximum and listed in the
  report, so doubling a column cannot leave the sheet half applied (enemy stats are u16, so
  tripling a 30,000 HP boss lands on 65535);
- a column that holds a **code rather than a quantity is never capped**. A spell's element,
  target and status, and a rune's start-spell id, are refused per cell if the value is not a
  real code, because capping one of those silently writes a *different* element. The legend
  for those columns is printed next to the Table picker, and anything the disc itself already
  uses stays accepted, so an edit can always be reverted;
- a sheet exported from a **different table is refused** before a byte is written. Stat names
  are shared between tables, so importing the enemy sheet with the Characters table selected
  used to write enemy numbers into character stats. The report names the table the columns
  actually belong to.

### Char names

The roster and menu short-name table (7-character ASCII entries), edited in place. This
changes the name shown in menus, party and status screens for a **new game**. It does not
rewrite dialogue, cutscene or battle text, which live elsewhere, so a rename can read
inconsistently in story scenes. The hero's name is chosen by the player at game start.

### Field models

Swap the 3D model a character walks around as. The disc keeps 129 field models in
`DATA.PAK` (`PCnnnC.ROM`, RenderWare clumps holding a skeleton, meshes and animations) and
the boot ELF picks one through a table of pointers into its resource-path list, so changing
a model is a **single 4-byte pointer**. Nothing inside `DATA.PAK` is touched and **Reset**
puts it back byte for byte.

Model ids are **character id + 2** (the Prince is #2, so `pc001c.rom`). Files numbered
2xx / 3xx / 4xx are **extra looks for the same person** (`pc401c` is 1+400, the Prince's
fourth model) that the game's own scripts switch to for particular scenes; that is how the
cast changes appearance as the story moves. Eight characters have one: the **Prince** and
**Georg** have two extra each, plus Lyon, Kyle, Zegai, Cathari, Gunde and Miakis. The hero's
three looks are grouped at the top of the tab instead of being scattered down a 128-row list
by model id.

Because the scripts reach for those alternates by themselves, **a character's looks move
together by default**: pick a file on the Prince's row and all three of his rows follow, so
he stays re-skinned through the scenes that ask for an alternate. Untick *keep a character's
looks together* to set a single row on its own. Reset is grouped the same way.

Who owns which alternate is the disc's own answer, not a guess. The ELF carries an identity
table (NTSC `0x211250` to `0x211330`) that folds a character's model ids onto one entry,
grouping `{2, 119, 128}` as the Prince, `{10, 120}` Lyon, `{14, 121}` Kyle and `{22, 122}`
Georg, and the four it omits match their base model's 256-colour palette almost exactly
(Cathari, Georg, Gunde, Miakis). Only `pc314c` (Zegai) rests on the numbering alone.

**i** reads a model's skeleton straight off the disc (bone count, mesh sizes, animation
count). The same bone count plus matching mesh sizes means the same character redressed.
Cutscenes ship their own baked copies of the cast, so a swap shows while you walk around
rather than in pre-rendered story scenes.

### Assets

A browser and extractor for **`DATA.PAK`**, the disc's 2.3 GB CRI ROFS volume holding about
7,700 files. It sits far past the slice the editor keeps in memory, so it is read on demand
straight out of your file; nothing here writes to the disc. Backed by decoders for the ROFS
volume, Konami's LZSS and `bpe` compression, and the `dxt` texture container, all
reverse-engineered for this editor.

- **Browse and filter** every internal file by path, and **Extract** any of them (stored,
  LZSS and `bpe` files are all decoded).
- **Portraits**: any `FACE` file rendered to PNG in the browser, both the battle face set
  (`BTL_FACE`, 92 faces) and the **high-res 256x256 per-character portraits**
  (`FACE_PC*` / `FACE_EC*`, one file per character with all of their expressions). Download
  a single face, the whole set as a **ZIP of PNGs**, or a **sprite sheet**.
- **Textures**: any other decodable file rendered to PNG at native size, covering field and
  map sprites (`SR_CHR*`), effect and particle art (`*_TEX*`: fire, explosions, lens flares)
  and UI window skins (`TLK_WIN` / `GMF*`), in both 8-bit palettized and 32-bit direct
  colour.

### Reference

Read-only English name lists (characters, spells, skills, runes, enemies, healing items and
every gear slot), filterable, for looking up an id.

## Sharing your edits: the `.s5mod` recipe

Instead of passing around a 4.5 GB disc image, export your edits as a small JSON recipe of
byte runs.

- **Export recipe** writes `edits.s5mod.json`: the disc serial plus, for each changed run,
  its offset, the original bytes and the new bytes. A few KB, human-readable, and reversible
  because the originals are recorded.
- **Import recipe** replays it into the ISO you have open, verifying the serial (a PAL recipe
  will not apply to an NTSC disc) and warning on any byte that does not match the author's
  recorded original, which is the signal that the target disc is not the same clean base.
- It works from a browser that cannot write the ISO in place, which is what makes the ISO
  editor useful on Android, Firefox and Safari: open, edit, export, then apply on desktop
  Chromium.

Because the recipe is built from the byte diff rather than from a list of field names, it
captures everything the web editor can do, including the code-level features (sets, passives,
the Dawn Rune toggle, model swaps).

`.xdelta` patch creation is **desktop and CLI only**; see below.

## Regions

Both regions are supported and auto-detected from the serial at `0x828BD`, with the region
shown when the disc opens. The game data is byte-identical across regions (same in-record
offsets, strides and counts), so region support means rebinding the 15 verified table bases,
plus the PAL rune-price stride.

**NTSC-U only**, because they patch code or text whose PAL offsets are not mapped:

- **Sets** (the set detector, dispatcher and handlers)
- **Passives** (the rune equip gates)
- **Char names** (the short-name table base)

Everything else, including Characters, Gear stats, Spells, Runes, all three price tables,
Enemies, Unites, MP growth, Skill effects, Balance, Excel / CSV and Field models, works on
both discs.

## How it works

Neither mode reimplements the game logic in JavaScript. The page runs this repository's own
pure-Python modules **unchanged** inside Pyodide (CPython compiled to WebAssembly):

- **Saves**: [`Editor/s5save.py`](Editor/s5save.py). The picked file is written to
  `/save.bin` in Pyodide's in-memory filesystem, the module's normal path-based functions run
  against it, and the edited bytes are read back for download. Checksums, ECC and the
  CodeBreaker / SharkPort containers are all handled by the trusted desktop code.
- **ISO**: [`Editor/s5patch.py`](Editor/s5patch.py) plus
  [`Editor/s5fields.py`](Editor/s5fields.py). Because the disc is around 4 GB we do **not**
  load it into memory. We read a **~6.6 MB front slice** (`file.slice(0, 0x6A0000)`), which
  covers every editable table from the serial at `0x828BD` up through the name list at
  `0x691600`, so the engine's absolute-offset reads and writes work on the slice exactly as
  they would on the full disc. On **Save** the edited slice is diffed against the pristine
  one and **only the changed byte runs** are written back into the real file in place
  (`createWritable({keepExistingData: true})`) at their absolute offsets.
- **Assets** sit about 2 GB into the disc, past the slice, so the Assets tab pulls byte
  ranges out of the `File` object on demand and hands them to the Python decoders. Building
  the `DATA.PAK` index costs roughly 600 KB of reads across 126 directory extents and is
  cached for the session.

This is the same "one source of truth for offsets" discipline the CLI uses: the web UI never
guesses a byte layout.

## Shared UX

- **Searchable pickers** instead of native dropdowns for the big id lists (items, runes,
  armor, spells, roster), usable on a phone.
- **Review before save**: an explicit grouped list of what is about to change, confirmed
  before anything is written.
- **Dirty highlighting, an unsaved badge and a sticky toolbar** that keeps the pending count
  and the Save button reachable, plus a browser warning if you try to leave with unsaved
  work.
- **Per-field revert (↺)** on every edited field, and **undo / redo** across the whole ISO
  session (Ctrl+Z, Ctrl+Y or Ctrl+Shift+Z).
- **↻ Last opened** via IndexedDB.
- **Save in place** through the File System Access API after a permission prompt, with a
  transparent **download** fallback, and **Web Share** in and out on Android (share a save
  into the installed app, share the edited copy back out).
- **Two themes**: Falena Twilight and Sun Rune, mobile-first and safe-area aware.
- **PWA and offline**: installable, with a network-first service worker that keeps returning
  users on the latest deploy yet still opens offline. The pinned Pyodide runtime is cached
  once, same-origin fetches revalidate, and every script and style URL carries a
  `?v=<release>` stamp so a deploy can never serve a new `index.html` beside a stale
  `iso.js`. A **↻ Force refresh** link in the footer clears the worker and caches if one ever
  gets stuck.

### Install as an app

Open the live URL in Chrome and tap **⬇ Install app**, or **⋮ → Install app / Add to Home
Screen**. On iOS Safari use **Share → Add to Home Screen**. After the first visit it works
offline.

## Privacy

The page has no server component and no upload path. Your ISO and saves are read on your
device, edited in memory, and written back by you. The only network traffic is the page
itself and the pinned Pyodide runtime from a CDN, both cached after the first load.

The repository contains **no game ROM, ISO, save files, audio or script / story assets**. It
includes small reverse-engineered **reference tables** (id to name maps and offset data) that
the editor needs in order to show meaningful labels. That is interoperability data, not the
game. Game images, saves, exported patches, extracted overlays and assets, stat guides and
internal working notes are all git-ignored and never distributed.

## The desktop editor (retired)

The local app (`Editor/s5editor.py`, started by `Start Editor (Mac).command` or
`Start Editor (Windows).bat`, Python 3.8+ and standard library only, serving
`http://127.0.0.1:8055/`) is in **maintenance only**. It still runs and still edits what it
always did, but it is no longer where the work happens.

- **Nothing is lost.** Both front-ends drive the same verified engine (`s5patch.py`,
  `s5fields.py`, `s5save.py`), which is **not** retired. The web app runs those exact modules
  in Pyodide and the CLI keeps working, so engine fixes reach both.
- **Web-only features:** Sets, Passives, the Dawn Rune toggle, gear name and description
  editing, the Excel / CSV round-trip with its capping and wrong-sheet guard, and the enemy
  stat scaler.
- **Still desktop and CLI only:** overlay extraction and re-insertion, the overlay text
  editor for story and dialogue text (endings, letters, lore, newspaper), `.xdelta` patch
  creation and application (needs `xdelta3` installed), raw hex peeking, and the boot-ELF
  string editor. ELF string editing was deliberately dropped from the web build as
  unreliable.
- **Your files are portable.** ISOs, saves and `.s5mod` recipes move between the two
  unchanged, so you can switch mid-project.

### xdelta, in one paragraph

An `.xdelta` patch captures *any* byte difference between two ISOs, including overlay edits
that a recipe cannot see, and it is the de-facto PS2 romhack standard. It needs a pristine
ISO to diff against. In the desktop app, **Tools → Share / Patch**, put the pristine ISO's
path in **Pristine ISO** and click **Create .xdelta**; a recipient supplies their pristine
ISO plus the patch and gets a new patched ISO alongside. Install `xdelta3` first
(`brew install xdelta`, or `sudo apt install xdelta3`).

## CLI

```bash
cd Editor
python3 s5patch.py verify "/path/to/Suikoden V.iso"        # prints the detected region
python3 s5patch.py dump   "/path/to/Suikoden V.iso" --id 0
python3 s5patch.py set    "/path/to/Suikoden V.iso" --id 0 --table stats --field HP --value 200
python3 s5patch.py models "/path/to/Suikoden V.iso"        # field-model table: id -> file, who
python3 s5patch.py set-model "/path/to/Suikoden V.iso" --id 2 --slot 127   # Prince -> pc401c
python3 s5patch.py set-model "/path/to/Suikoden V.iso" --id 2 --slot 127 --one   # that row only
python3 s5patch.py set-model "/path/to/Suikoden V.iso" --id 2 --reset
```

## Project layout

```
web/
  index.html             app shell: mode tabs, PWA meta, pinned Pyodide, script load order
  common.js              shared UI: IndexedDB kv, searchable picker, review modal, theme, tabs
  diff-core.js           pure byte-diff logic (changed-run computation), DOM-free, unit-tested
  app.js                 Save editor, the shared Pyodide boot, and the Python glue for both engines
  iso.js                 ISO editor: slice load, all 19 tabs, diff save-in-place, .s5mod recipe
  style.css              Falena Twilight / Sun Rune themes, mobile-first
  manifest.webmanifest, sw.js, icons/      PWA install, offline, Web Share target
  tests/                 static, pure-logic, engine round-trip and headless e2e suites
Editor/
  s5patch.py             ISO engine + CLI (verify / dump / set / recipe / xdelta / overlays / assets)
  s5fields.py            verified ISO tables and field schema (NTSC-U + PAL bases, region switch)
  s5save.py              PS2 memory-card and standalone-save engine (.ps2 / .psu / .xps / .sps / .cbs)
  s5_*.json              verified name and reference data (runes, items, armor, characters, ...)
  s5editor.py            the retired local web app
Start Editor (Mac).command / (Windows).bat   launchers for the retired desktop app
```

## Tests

The repo ships no ROM, ISO or saves, so the tests build synthetic fixtures from the engine's
own constants and cannot drift.

```bash
cd web && npm test        # static + pure logic + engine round-trip + feature suites
npm run test:e2e          # headless Chromium shell and mobile overflow (skips if not installed)
npm run test:all          # both
```

- **`validate.mjs`**: every client script parses, the shell is wired (script tags, both mode
  tabs), the service worker precaches the shell and every engine data file, the ISO slice
  window covers the highest table offset, and the manifest declares the share target.
- **`diff-logic.mjs`**: unit tests for the pure changed-run computation.
- **`iso-roundtrip.mjs`** / **`iso_roundtrip.py`**: extracts the **exact** Python glue out of
  `app.js`, points it at a fabricated 6.6 MB slice, and drives the same adapters the
  front-end calls (load, read, write, re-read, hard mode, recipe), proving the engine reuse
  on the slice is correct.
- **`save-fields.mjs`**: write and read-back round-trips of every save field on every
  supported format.
- **`sets-unit.mjs`**, **`sets-iso.mjs`**: the armor-set decode and rewrite, checked against
  the Suikosource guide as ground truth.
- **`runes-always.mjs`**, **`dawn-rune.mjs`**: the passive gates and the Dawn Rune counter,
  including that switching a toggle back off restores the original bytes exactly.
- **`enemy-base.mjs`**: guards the region-specific enemy table bases against a real disc
  layout.
- **`e2e.mjs`**: headless Chromium, checking that the shell renders, both modes switch, and
  there is no horizontal overflow at 320 and 360 px.

## Deploying your own copy

1. **Settings → Pages → Deploy from a branch → your default branch → `/ (root)`.**
2. The editor lives at `/<repo>/web/` and fetches `../Editor/*.py` and `*.json` at runtime,
   so Pages must serve from the **repo root** (not `/web`), and the `Editor/` folder must
   stay in the deployed tree.
3. A root `.nojekyll` is committed so Pages serves `.py` and `_`-prefixed files verbatim.
   There is no build step; the Pyodide version is pinned in `index.html` and `sw.js`.

## License

See [`LICENSE`](LICENSE) (MIT). Reverse-engineered offset and name data is provided for
interoperability with your own legally owned copy of the game.

Fan project, not affiliated with or endorsed by Konami. Suikoden V is © Konami.
