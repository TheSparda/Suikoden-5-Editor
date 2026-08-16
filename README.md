# Suikoden V ISO & Save Editor

A cross-platform editor for **Suikoden V** (PS2), modeled on the
[Suikoden III editor](https://github.com/TheSparda/Suikoden-3-Editor). It runs as a
local web app in your browser — nothing is uploaded, and the server only touches the
file you point it at. It ships with **no game ROM/ISO**; supply your own
legally-obtained ISO and/or save files.

Every editable field was reverse-engineered and then **verified** against public stat
guides and the game's own data; fields that couldn't be confirmed are shown read-only or
omitted rather than guessed. Writes are protected by an optional `.bak` backup (a toggle
in the header, on by default).

**Regions:** NTSC-U (`SLUS-21291`) and PAL (`SLES-54087`) are both fully supported and
**auto-detected** from the ISO serial — a **NTSC-U** / **PAL** badge in the header shows
which. Every editor works on both regions. The only PAL limitation is the raw
in-place *ELF-text editor* (the Reference / Text tab shows read-only English name lists
in PAL, but not the string editor — the region's 5-language layout can't be relocated
safely).

## Run

- **macOS:** double-click `Start Editor (Mac).command`
- **Windows:** double-click `Start Editor (Windows).bat`
- **Any:** `cd Editor && python3 s5editor.py`

Requires Python 3.8+ (standard library only — no `pip install`). The app opens your
browser at `http://127.0.0.1:8055/` (override with the `PORT` env var). Open your ISO
with **Browse… / Open / Verify**; the serial is checked and the region badge appears.

> **ISO edits apply to a NEW GAME.** They change the game's base data, so start a new
> game to see them. Use real in-game save files, not emulator save states.

## Features

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

### Unites
Every unite attack (49, verified against the Unites guide) with its participant slots
editable via full-roster dropdowns. Member count is fixed (the table is packed) and the
damage/target are engine-driven, so those are shown for reference.

### Gear (Armor)
Five slots — Head / Body / Arm / Foot / Accessory — with English item names, verified vs
the Armor List guide: DEF, buy/sell, weight **Type** + SPD penalty, stat bonuses, proc
effects (auto-heal / drain / counter / …), and **per-element ATK & DEF** for all 14
elements. The game's own description text is shown read-only.

### MP Growth
The MP-cost thresholds for each magic level (Lv1–Lv4). This table is **global** — shared
by every unit.

### Skill Effects
The magnitude of each of the **165 skills** at every rank (E → SS). Global, filterable.

### Enemies
Level, combat stats, Potch / Skill-Point rewards, per-enemy elemental affinities (E–S),
and the five item-drop slots picked by item name (verified via the in-game drop table).

### Prices
Buy / sell for items & equipment, rune (orb) prices, and healing-item prices.

### Reference / Text
Clean English name lists (Characters, Spells, Skills, Runes, Enemies, Healing Items, all
Gear slots) for lookup, plus — on NTSC-U — the raw boot-ELF strings (all languages) which
you can edit in place (byte-capped).

### Hard Mode
Scale every character's growth rates down by a factor (idempotent; fully restorable).

### Save Editor
Reads and writes PS2 saves across formats and regions (`.ps2`, `.psu`, `.xps`/`.sps`,
`.cbs`), region-aware (NTSC-U / PAL / NTSC-J), by file picker or folder scan. Editable:
hero name, castle name, New Game Plus. (The save editor detects each save's own region and
is independent of which ISO you have open.)

### Tools
- **Share / Patch** — export your edits so others can apply them (see the walkthrough
  below).
- **Raw hex** — peek at any absolute ISO offset.
- **Overlays** — extract/decompress the disc's engine overlays (OVL/\*.ROM), re-insert
  edited ones (LZSS), and an **Overlay Text** editor for the story/dialogue text inside
  them (endings, letters, lore, newspaper, …).
- **Assets (DATA.PAK)** — browse the game's 2.3 GB CRI ROFS asset volume (~7,700 internal
  files: backgrounds, UI, effects, character portraits as `FACE_*.ROM`) and extract any of
  them. Files stored uncompressed or LZSS are decoded; `bpe`-compressed textures are dumped
  raw for now (a `bpe` decoder + PS2 texture→PNG step for portrait *rendering* is in
  progress).

Shared UX across tabs: filters, per-field restore (↺), dirty highlighting, Save / Revert,
grouped navigation, and a light/dark theme toggle.

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

## CLI

```bash
cd Editor
python3 s5patch.py verify "/path/to/Suikoden V.iso"        # prints detected region
python3 s5patch.py dump   "/path/to/Suikoden V.iso" --id 0
python3 s5patch.py set    "/path/to/Suikoden V.iso" --id 0 --table stats --field HP --value 200
```

## Layout

```
Editor/
  s5editor.py            local web app (all tabs + JSON API)
  s5patch.py             ISO engine + CLI (verify / dump / set / recipe / xdelta / overlays)
  s5fields.py            verified ISO tables + field schema (NTSC-U + PAL bases, region switch)
  s5save.py              PS2 memory-card + standalone-save engine (.ps2/.psu/.xps/.sps/.cbs)
  s5_*.json              verified name/reference data (incl. s5_held_items_pal.json for PAL)
Start Editor (Mac).command / (Windows).bat   launchers
```

## Privacy & scope

The repository contains **no game ROM/ISO, save files, audio, or script/story assets**. It
includes small reverse-engineered **reference tables** — id→name maps and offset data — that
the editor needs to show meaningful labels; this is interoperability data, not the game.
Game images (`*.iso`), saves, exported patches (`*.s5mod`, `*.xdelta`), extracted overlays,
stat guides, and internal working notes are all git-ignored and never distributed. Fan
project; not affiliated with or endorsed by Konami. Suikoden V is © Konami.

## License

See `LICENSE` (MIT). Reverse-engineered offset/name data is provided for interoperability
with your own legally-owned copy of the game.
