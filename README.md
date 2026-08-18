# Suikoden V ISO & Save Editor

A cross-platform editor for **Suikoden V** (PS2), modeled on the
[Suikoden III editor](https://github.com/TheSparda/Suikoden-3-Editor). It runs as a local
web app in your browser — nothing is uploaded, and the server only touches the files you
point it at. It ships with **no game ROM/ISO**; supply your own legally-obtained ISO and
save files.

> 💬 **Feature requests / Support** available on the **Toran Castle Discord**:
> https://discord.gg/KesHMX5P2Z

**At a glance:**

- **ISO editing** — characters, runes & spells, gear, enemies, prices, unites, MP growth,
  skill effects, text, and a one-click Hard Mode. Edits apply to a *new game*.
- **Save editing** — full per-character editing (level, armor, runes, battle-skill slots,
  all 48 skill ranks) plus **recruitment** of the 108 Stars, hero/castle names, and New
  Game Plus. Works on real saves across every common format.
- **Asset explorer** — every character portrait rendered to PNG in the browser (battle
  faces + high-res 256×256 portrait sets), a general texture viewer (sprites, effects,
  UI art), and a browser/extractor for all ~7,700 files in the game's DATA.PAK.
- **Mod sharing** — export your ISO edits as a tiny, reversible `.s5mod` recipe or a
  standard `.xdelta` patch instead of shipping a 4.5 GB disc image.
- **Both regions** — NTSC-U (`SLUS-21291`) and PAL (`SLES-54087`), auto-detected from the
  ISO serial (badge in the header). Saves additionally support NTSC-J detection.

Every editable field was **reverse-engineered and verified** — against public stat guides,
the game's own data tables, and (for saves) the game's disassembled save code. Fields that
couldn't be confirmed are shown read-only or omitted rather than guessed. Writes are
protected by an optional `.bak` backup (header toggle, on by default).

## Quick start

- **macOS:** double-click `Start Editor (Mac).command`
- **Windows:** double-click `Start Editor (Windows).bat`
- **Any:** `cd Editor && python3 s5editor.py`

Requires Python 3.8+ (standard library only — no `pip install`). The app opens your
browser at `http://127.0.0.1:8055/` (override with the `PORT` env var). Open your ISO with
**Browse… / Open / Verify**; the serial is checked and the region badge appears. The Save
Editor works independently of the ISO — open a save file directly or scan a folder.

> **ISO edits apply to a NEW GAME** (they change the game's base data). To change an
> existing playthrough, use the **Save Editor**. Use real in-game saves, not emulator
> save states.

## ISO editors

### Characters
Per-character, indexed by the in-game roster, verified byte-for-byte:
- **Stats & growths** — base HP / Attack / Technique / Magic / Evasion / PDF / MDF /
  Speed / Luck plus each stat's per-level growth.
- **Affinities** — elemental affinity grade (None / E / D / C / B / A / S) for all 14
  elements (Sun, Fire, Lightning, Wind, Water, Earth, Star, Sound, Holy, Dark, Slash,
  Thrust, Punch, Shoot).
- **Equipable skills** — the max rank (None … SS) this character can equip each of the
  48 skills at.
- **Weapon growth** — attack power at each of the 16 sharpen levels.
- **Starting equipment** — Head / Body / Arm / Feet armor for a new game.
- **Starting items** — up to four held items/accessories a unit begins with.

### Runes & Spells
- Which contiguous spell range each **rune** teaches (rune → spell grant table), plus a
  **custom spell-set builder**.
- **Spell** definition: element, power / heal amount, target shape, and status effect.

### Gear (Armor)
Five slots — Head / Body / Arm / Foot / Accessory — with English item names, verified vs
the Armor List guide: DEF, buy/sell, weight **Type** + SPD penalty, stat bonuses, proc
effects (auto-heal / drain / counter / …), and **per-element ATK & DEF** for all 14
elements. The game's own description text is shown read-only.

### Enemies
Level, combat stats, Potch / Skill-Point rewards, per-enemy elemental affinities (E–S),
and the five item-drop slots picked by item name (verified via the in-game drop table).

### Unites
Every unite attack (49, verified against the Unites guide) with its participant slots
editable via full-roster dropdowns. Member count is fixed (the table is packed) and the
damage/target are engine-driven, so those are shown for reference.

### MP Growth & Skill Effects
- **MP Growth** — the MP-cost thresholds for each magic level (Lv1–Lv4); global table.
- **Skill Effects** — the magnitude of each of the **165 skills** at every rank (E → SS);
  global, filterable.

### Prices
Buy / sell for items & equipment, rune (orb) prices, and healing-item prices.

### Reference / Text
Clean English name lists (Characters, Spells, Skills, Runes, Enemies, Healing Items, all
Gear slots) for lookup, plus — on NTSC-U — the raw boot-ELF strings (all languages),
editable in place (byte-capped). PAL shows the read-only lists (its 5-language string
layout can't be relocated safely).

### Hard Mode
Scale every character's growth rates down by a factor — idempotent and fully restorable.

## Save Editor

Reads and writes PS2 saves across formats and regions — `.ps2` memory-card images,
`.psu`, `.xps`/`.sps` (SharkPort/X-Port), and `.cbs` (CodeBreaker, transparently
decrypted and re-encrypted) — for NTSC-U / PAL / NTSC-J saves, opened by file picker or
folder scan.

The save layout was reverse-engineered from the game itself: the save file is a verbatim
image of the game's save RAM, and disassembling the save routines proved there is **no
body checksum** (the game validates only three fixed header values), so field edits are
safe. Every field below is cross-verified against the game's own data tables.

- **Header** — hero name, castle name, **New Game Plus** (fast-forward).
- **Characters panel** — for any of the 120 characters:
  - **Level** (1–99)
  - **Equipped armor** — Helm / Armor / Gloves / Boots, picked by name
  - **Equipped runes** — Head / Right / Left hand, with the full **92-rune name table**
    extracted from the ISO (verified against innate runes: Zerase = Star, Prince = Dawn)
  - **Equipped battle-skill slots** — the two active skill slots
  - **All 48 skill ranks** (None → SS) — support skills (Cook, Forge, Tutor, …) use the
    same scale as their fixed in-game grades
- **Recruitment panel** — the 108-Stars recruitment flags as a searchable checkbox
  roster: live recruited counter, name filter, check/uncheck-all, per-character
  **Recruit** toggle, a one-click **Recruit ALL**, and a write that applies only the
  flags you changed. Story-only characters and antagonists are locked so saves stay
  consistent.

Memory-card **ECC is recomputed automatically** on every card write, and a `.bak` backup
is made first when backups are on. Verified by an automated suite: full write→read
round-trips of every field on every supported format (79 checks, NTSC-U + PAL).

## Assets & Portraits

Its own tab (under **Other → Assets / Portraits**), backed by decoders for the game's
CRI ROFS volume, Konami's LZSS and `bpe` compression, and its `dxt` texture container —
all reverse-engineered for this editor.

- **Portraits gallery** — pick a portrait set from a dropdown and view every
  expression/pose rendered to PNG in the browser: the battle face set (`BTL_FACE`,
  92 faces) and the **high-res 256×256 per-character portraits** (`FACE_PC*`/`FACE_EC*`,
  one file per character with all their expressions). Download any face, a set as a
  **ZIP** of PNGs or a **sprite sheet**, or **ALL portraits** at once (one ZIP, a folder
  per set).
- **All DATA.PAK files** — browse, search and extract any of the ~7,700 internal files in
  the 2.3 GB asset volume (stored, LZSS and `bpe`-compressed files are all decoded).
- **Texture viewer** — decodable files get a **Textures** button that renders any packed
  image to PNG at native size: field/map sprites (`SR_CHR*`), effect & particle art
  (`*_TEX*`: fire, explosions, lens flares…), and UI window skins (`TLK_WIN`/`GMF*`) —
  both 8-bit palettized and 32-bit direct-colour.

## Sharing your edits — patching walkthrough

The editor writes changes straight into your ISO. To share a "mod" (a hard mode, a
rebalance, a translation tweak) **without** distributing the 4.5 GB game disc, use
**Tools → Share / Patch**. Two formats are offered:

| | **Mod recipe (`.s5mod`)** | **xdelta patch (`.xdelta`)** |
|---|---|---|
| What it captures | The structured field edits the editor made (stats, gear, enemies, prices, spells, unites, MP, skill effects, …) | *Any* byte difference between two ISOs, including overlay edits |
| Size | Tiny (a few KB) | Small (KB for small edits) |
| Human-readable | Yes (JSON of offset/old/new) | No (binary diff) |
| Needs a clean ISO to create | No | Yes (a pristine copy to diff against) |
| Region-checked on apply | Yes (serial-verified; a PAL recipe won't apply to an NTSC ISO) | The source ISO must match exactly |
| Reversible | Yes (stores original bytes) | Via re-patching |
| Standard | This editor's own format | The de-facto PS2 romhack/undub standard |

### A) Share a mod recipe (recommended for editor-made changes)

**To create one:**
1. Open your ISO and make your edits in the tabs as usual (every field write is recorded
   automatically).
2. Go to **Tools → Share / Patch**. The "Mod recipe" line shows how many edits are
   recorded (click **Refresh** to update).
3. Click **Export recipe (.s5mod)**. It writes `<your-iso-name>.s5mod` next to the ISO and
   shows the path. Send that small file to anyone.

**To apply someone's recipe:**
1. Open *your own clean ISO of the same region* (NTSC-U recipe → NTSC-U ISO, etc.).
2. In **Tools → Share / Patch**, put the `.s5mod` path in the **Apply recipe** box and
   click **Apply to current ISO**.
3. It verifies the serial, writes the changes in place (making a `.bak` if backups are
   on), and reports how many patches applied. A mismatch warning means the target ISO
   differs from the author's clean base.

Use **Clear recipe** to reset the recording (it does not undo edits already written to the
ISO — a `.bak` or Revert does that).

### B) Share an xdelta patch (captures everything, incl. overlay edits)

Requires `xdelta3` installed: macOS `brew install xdelta`, Debian/Ubuntu
`sudo apt install xdelta3`.

**To create one:** keep a **pristine** copy of your ISO before editing. After editing,
in **Tools → Share / Patch** put the pristine ISO's path in **Pristine ISO** and click
**Create .xdelta (pristine → current)**. It writes `<iso>.xdelta`.

**To apply one:** the recipient puts their **pristine ISO** path in **Pristine ISO**, the
`.xdelta` path in **Apply patch**, and clicks **pristine + patch → new ISO**. It writes a
new patched ISO alongside.

### Which should I use?
- **Recipe** for sharing editor-made rebalances/presets — smaller, readable, region-safe,
  and needs no clean ISO to create.
- **xdelta** when your mod includes **overlay text/data edits** (recipes don't capture
  those), or when you want a single universal patch the wider PS2 community can apply.

## Tools

- **Share / Patch** — the recipe + xdelta workflow above.
- **Excel / CSV round-trip** — export any of nine data tables (character stats /
  affinities / skill caps / weapon growth / starting equipment, enemies, prices, skill
  effects, MP growth) as CSV, bulk-edit in Excel / Sheets / LibreOffice, and import back.
  Import writes only the cells that changed, with the same range validation, `.bak`
  backup, and recipe recording as tab edits; blank cells and Excel quirks (BOM, `12.0`
  decimals) are handled.
- **Overlays** — extract/decompress the disc's 17 engine overlays (OVL/\*.ROM),
  re-insert edited ones (LZSS, sector-slot guarded), and an **Overlay Text** editor for
  the story/dialogue text inside them (endings, letters, lore, newspaper, …).
- **Raw hex** — peek at any absolute ISO offset.

Shared UX across tabs: filters, per-field restore (↺), dirty highlighting, Save / Revert,
grouped navigation, and a light/dark theme toggle.

## CLI

```bash
cd Editor
python3 s5patch.py verify "/path/to/Suikoden V.iso"        # prints detected region
python3 s5patch.py dump   "/path/to/Suikoden V.iso" --id 0
python3 s5patch.py set    "/path/to/Suikoden V.iso" --id 0 --table stats --field HP --value 200
```

## Project layout

```
Editor/
  s5editor.py            local web app (all tabs + JSON API)
  s5patch.py             ISO engine + CLI (verify / dump / set / recipe / xdelta / overlays / assets)
  s5fields.py            verified ISO tables + field schema (NTSC-U + PAL bases, region switch)
  s5save.py              PS2 memory-card + standalone-save engine (.ps2/.psu/.xps/.sps/.cbs)
  s5_*.json              verified name/reference data (runes, items, armor, characters, …)
Start Editor (Mac).command / (Windows).bat   launchers
```

## Privacy & scope

The repository contains **no game ROM/ISO, save files, audio, or script/story assets**. It
includes small reverse-engineered **reference tables** — id→name maps and offset data — that
the editor needs to show meaningful labels; this is interoperability data, not the game.
Game images (`*.iso`), saves, exported patches (`*.s5mod`, `*.xdelta`), extracted overlays
and assets, stat guides, and internal working notes are all git-ignored and never
distributed. Fan project; not affiliated with or endorsed by Konami. Suikoden V is
© Konami.

## License

See `LICENSE` (MIT). Reverse-engineered offset/name data is provided for interoperability
with your own legally-owned copy of the game.
