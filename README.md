# Suikoden V ISO & Save Editor

A cross-platform editor for **Suikoden V** (PS2, USA `SLUS-21291`), modeled on the
[Suikoden III editor](https://github.com/TheSparda/Suikoden-3-Editor). It runs as a
local web app in your browser — nothing is uploaded, and the server only touches the
file you point it at. It ships with **no game ROM/ISO**; supply your own
legally-obtained ISO and/or save files.

Every editable field was reverse-engineered and then **verified** against public stat
guides; fields that could not be confirmed are shown read-only or omitted rather than
guessed. Writes are protected by an optional `.bak` backup (a toggle in the header,
on by default).

## Run

- **macOS:** double-click `Start Editor (Mac).command`
- **Windows:** double-click `Start Editor (Windows).bat`
- **Any:** `cd Editor && python3 s5editor.py`

Requires Python 3.8+ (standard library only — no `pip install`). The app opens your
browser at `http://127.0.0.1:8055/` (override with the `PORT` env var). Open your ISO
with **Browse… / Open / Verify**; the serial is checked before anything is read.

> **Edits apply to a NEW GAME.** ISO edits change the game's base data, so start a new
> game to see them. Use real in-game save files, not emulator save states.

## Features

### Characters
Per-character, indexed by the in-game roster. All verified byte-for-byte against the
game's data:
- **Stats & growths** — base HP/Attack/Technique/Magic/Evasion/PDF/MDF/Speed/Luck plus
  each stat's per-level growth.
- **Affinities** — elemental affinity grade (None / E / D / C / B / A / S) for all 14
  elements (Sun, Fire, Lightning, Wind, Water, Earth, Star, Sound, Holy, Dark, Slash,
  Thrust, Punch, Shoot).
- **Equipable skills** — the max rank (None … SS) this character can equip each of the
  48 skills at (the character's skill *cap*).
- **Weapon growth** — attack power at each of the 16 sharpen levels.
- **Starting equipment** — Head / Body / Arm / Feet armor for a new game (dropdowns of
  the real armor list).
- **Starting items** — up to four held items/accessories a unit begins with.

### Runes & Spells
- Which contiguous spell range each **rune** teaches (rune → spell grant table).
- **Spell** definition: element, power / heal amount, target shape, and status effect
  (None / Revive / Add status).
- A **custom spell-set builder** for composing a rune's spell list.

### Unites
Every unite attack (49, verified against the Unites guide) with its participant slots
editable via full-roster dropdowns. Member count is fixed (the table is packed) and the
damage/target are engine-driven, so those are shown for reference.

### Gear (Armor)
Five slots — Head / Body / Arm / Foot / Accessory — labeled with English item names.
Fully editable and verified against the Armor List guide:
- DEF, buy / sell price, weight **Type** (Light / Medium / Heavy) + SPD penalty.
- Stat bonuses: HP, Attack, Technique, Magic, Evasion, MDEF, Speed, Luck.
- Proc effects: auto-heal, HP-drain, status-resist, potch, counter (%).
- **Per-element ATK & DEF** for all 14 elements.
- The game's own description text is shown read-only for reference.

### MP Growth
The MP-cost thresholds for each magic level (Lv1–Lv4) as a caster gains more casts.
This table is **global** — shared by every unit (what varies per character is which
levels/casts they reach, via their Magic skill cap).

### Skill Effects
The magnitude of each of the **165 skills** at every rank (E → SS) — e.g. `Attack +`
5→40, `Stamina (% HP)` 105→130. Global, filterable.

### Save Editor
Reads and writes PS2 saves across formats and regions:
- Memory-card images (`.ps2`), plus `.psu`, and standalone `.xps` / `.sps` (SharkPort /
  X-Port) and `.cbs` (CodeBreaker, RC4 + zlib).
- **Region-aware** (NTSC-U / PAL / NTSC-J) — recognizes an S5 save by its payload, not
  the folder name, and tags each with a colored badge.
- **Open directly** (file picker or path) or **scan a folder** for both cards and
  standalone saves.
- Editable: hero name, castle name, New Game Plus. Level is shown read-only (it's the
  save-select display cache; real unit levels live elsewhere).

### Other ▾
- **Enemies** — Level, combat stats, Potch / Skill-Point rewards, per-enemy elemental
  affinities (E–S), and the five item-drop slots picked by item name (verified via the
  in-game drop table).
- **Prices** — buy / sell for items & equipment, rune (orb) prices, and healing-item
  prices.
- **Reference / Text** — clean English name lists (Characters, Spells, Skills, Runes,
  Enemies, Healing Items, all Gear slots) for lookup, plus the raw boot-ELF strings
  (all languages) which you can edit in place (byte-capped).
- **Hard Mode** — scale growth rates down by a factor (idempotent; restorable).
- **Tools**
  - **Share / Patch** — export your edits so others can apply them:
    - a **mod recipe** (`.s5mod`) that records every field edit (stats, gear, enemies,
      prices, spells, unites, MP, skill effects…) as a small, region-checked, reversible
      patch; anyone can apply it to their own *clean* ISO of the same region.
    - a one-click **xdelta3** patch (the universal PS2 standard) — a binary diff between a
      pristine ISO and your edited one that captures *everything*, including overlay edits
      (requires `xdelta3` installed + a clean ISO on hand).
  - raw hex peek at any offset; **overlay tools** that extract/decompress the disc's engine
    overlays (OVL/\*.ROM), re-insert edited ones (LZSS), and an **Overlay Text** editor for
    the story/dialogue text inside them (endings, letters, lore, newspaper, …).

Shared UX across tabs: filters, per-field restore (↺), dirty highlighting, Save / Revert,
grouped navigation, and a light/dark theme toggle.

## CLI

```bash
cd Editor
python3 s5patch.py verify     "/path/to/Suikoden V.iso"
python3 s5patch.py dump       "/path/to/Suikoden V.iso" --id 0
python3 s5patch.py set        "/path/to/Suikoden V.iso" --id 0 --table stats --field HP --value 200
```

## Layout

```
Editor/
  s5editor.py            local web app (all tabs + JSON API)
  s5patch.py             ISO engine + CLI (verify / dump / set / find-bytes / dump-region)
  s5fields.py            verified ISO tables + field schema (offsets, strides, decodes)
  s5save.py              PS2 memory-card + standalone-save engine (.ps2/.psu/.xps/.sps/.cbs)
  s5_*.json              verified name/reference data (characters, items, runes, skills,
                         armor, spells, enemies, English reference lists, …)
Start Editor (Mac).command / (Windows).bat   launchers
```

## Privacy & scope

The repository contains **no game ROM/ISO, save files, audio, or script/story assets**.
It does include small reverse-engineered **reference tables** — id→name maps (characters,
items, runes, spells, skills, armor, enemies, unites) and a boot-ELF string index — which
the editor needs to show meaningful labels; this is interoperability data, not the game.
Game images (`*.iso`), saves, stat guides, screenshots, and internal reverse-engineering
working notes are all git-ignored and never distributed. All offsets were independently
reverse-engineered and verified against public stat guides. Fan project; not affiliated
with or endorsed by Konami. Suikoden V is © Konami.

## License

See `LICENSE`. Reverse-engineered offset/name data is provided for interoperability with
your own legally-owned copy of the game.
