# Suikoden V (SLUS-21291, USA) — ISO offsets

Reverse-engineered from Tony H's `Suikoden5EditorV10.exe` (via `monodis`) and validated
against a real ISO. Methodology: decompile the community editor for offsets + label
data, then confirm every field empirically against the ISO. Never write an unverified
field. Namespace `Suikoden_V_PS2_ROM_Editor`; logic in `Form1`.

## Identity / serial  (VERIFIED)
- `SLUS_212.91;1` at ISO offset **0x828BD** (exe validates region 0x828C4).

## Character indexing  (VERIFIED)
- Characters are keyed by a **decimal id (NNN)** shown in the exe's name list
  (`043 - Lyon`, `000 - Kyle`, `034 - Georg`, `036 - Hero/Prince`, `012 - Miakis`).
  Full playable list in `s5_characters.json` (73 entries, ids 0..80 with gaps).
- Every per-character table below is addressed as `base + id*stride`. Confirmed: Lyon(43),
  Kyle(0), Georg(34), Hero(36), Miakis(12), Belcoot(15) all read sane values.

## Per-character tables  (VERIFIED bases/strides; field labels noted)
| Table | Base | Stride | Count | Fields |
|-------|------|--------|-------|--------|
| Stats     | `0x49F0DC` | `0x7C` (124) | — | +0x00 u8 Level; +0x01 u8 (level-cap?); +0x02..0x14 ten u16 stats **HP, Spell Count, Attack, Magic, PDF, MDF, Technique, Accuracy, Evasion, Speed** (order VERIFIED vs L60 Character Database guide); +0x16 u16 (~2500 const, unverified); +0x18 u16 **Luck**; +0x1A..0x29 sixteen u8 growth/skill bytes |

## Item / equipment PRICE table  (VERIFIED vs rune guide)
- Base `0x49433C`, stride `148` (0x94), 148 records in item-id order.
- +0x00 u32 **buy price**, +0x04 u32 **sell price** (sell == buy/2).
- Verified: record 17 = 6000/3000 (Fire Rune), record 21 = 35000/17500 (Shield Rune).
- Editable via s5patch prices/set-price + editor Prices panel.
| Skill thresholds | `0x4987C0` | `0x60` (96) | 16 u8 | ascending magic/skill level thresholds (per class; Kyle==Miakis, Georg==Belcoot) |
| Skills    | `0x48A970` | `0x12` (18) | 18 u8 | skill values/ranks (0..25 seen; late joiners all 0) |
| Equipment | `0x493112` | `0x18` (24) | 4 u8  | 4 equip slot item-ids (helm/armor/gloves/boots — order tentative) |
| Runes     | `0x4E87F0` | `0x54` (84) | u8,u8,u16,u8,u8 | rune slot ids + flags (layout tentative) |
| Name      | `0x691600` | `0x08` (8)  | ASCII | 7 chars + null; **editable rename** (this table is index-ordered differently — see s5_char_names.json) |

Note: stat sub-fields `list1_31/35/39/43/47/51` (growth values) are read from 6 extra
seeks in `list1RTB_Click` at bases not yet resolved — currently READ-ONLY/omitted.

## Fixed tables
- `0x4986C0`: 4×10 ascending u16 ladder capped 999 (cost/threshold; exe control `item1`).
  `0x31A6BC`: single u16 = 1000. Purpose unconfirmed.

## How verified
- Bases/strides extracted from the exe's `listNRTB_Click` loaders (`set_Position` +
  `Read*` into named NUD controls) and `Patch1_Click` writers.
- Values cross-checked in the ISO for 6 known characters; HP magnitudes and skill-rank
  ranges (0..7) match expectations.

## Still open (research — per the S3 feature target)
- Exact identity of the 12 stat u16s (need a stat guide to confirm order beyond HP).
- Growth-rate table (list1_31..51), rune field layout, equipment slot order.
- Spells / rune-spell effects, unite attacks, gear DEF/price/effects, weapons, foods,
  shops, enemy list, boot-ELF text table — not yet located. Approach: find boot ELF
  PT_LOAD extent, anchor on name/description string pools, score fields vs ground truth.

## Boot ELF + string pools  (VERIFIED — basis for spells/gear/text RE)
- Main boot ELF at ISO **0xAD800** (MIPS ET_EXEC, entry 0x100008).
- PT_LOAD1: **vaddr 0x100000 ↔ ISO file 0xAD900**, size 0x951600 (ISO 0xAD900..0x9FEF00).
  Conversion: `file = vaddr - 0x100000 + 0xAD900`; `vaddr = file - 0xAD900 + 0x100000`.
- PT_LOAD2: vaddr 0xA51600 ↔ ISO 0x9FEF00, size 0x92A80.
- All character tables above sit inside PT_LOAD1 (e.g. stats 0x49F0DC = vaddr 0x5497DC).
- String pools (found by known names; multi-language: EN/FR/DE/IT/ES packed):
  - Rune names: ISO ~0x663E00 (va ~0x6B6500). 47 EN rune names → `s5_rune_names.json`.
  - Item names: ISO ~0x651000 (va ~0x6A3B00) → `s5_item_pool.json` (multi-lang).
  - Spell names: ISO ~0x66BF30 (va ~0x6BE630); spell names also embedded in a
    description pool ~0x655xxx.
- Rune name POINTER array at ISO 0x666070 (u32 vaddr pointers into the rune pool).

## Name pointer arrays (VERIFIED) vs stat arrays (unlinked)
- Spell NAME pointer array at ISO 0x66D640+ (u32 → name strings in 0x6BExxx pool),
  multi-language. Names are searchable + editable in place (Reference tab / set-string).
- CONFIRMED: spells (and by extension gear/weapon/food/shop) store names via pointer
  arrays but keep numeric STATS in a SEPARATE array the community exe never references.
  So stat-field offsets can't be verified from the exe. Per methodology they stay
  READ-ONLY until scored ≥95% against an external stat guide (a future ground-truth pass).

## Still to RE (editable stat tables — need ground-truth validation)
Record tables that reference the pools via (name_ptr, desc_ptr) — spells (power/cast/
element/target/AOE/status), rune→spell grants, unite attacks, gear (DEF/price/effects),
weapons (sharpen curve), foods, shops. Per methodology, score each field vs description
text / a stat guide to ≥95% before writing. NOT YET verified — do not write blind.

## Save-side (memory card) — VERIFIED against a real save
PS2 card engine (PS2MFS + ECC + .psu) ported verbatim from S3 → `s5save.py`; ECC
validated on real cards. S5 save specifics:
- Save folder + payload filename: **BASLUS-2129100** (payload file is named like the
  folder, NOT "gamedata"). Payload size **74024 bytes** (0x12128).
- PS2-browser title (icon.sys) e.g. "SuikodenV 01LV.39/019:16" → slot / level / playtime.
- gamedata fields (VERIFIED): **heroName @0x00 (16B)**, **castleName @0x14 (16B)**,
  **New Game Plus / cleared flag @0x12 (u8, 1=on)** — isolated by diffing a same-
  playthrough pre-end vs post-end pair, confirmed 6/6 (all cleared saves=1, in-progress=0).
  (0x28 is NOT level — varies 39/58/867/2873/3130 across saves.) `field25` @0x4C, event .rom @0x8C.
- Individual save formats decoded (py3, from mymc): CodeBreaker .cbs (RC4+zlib),
  SharkPort/X-Port .sps/.xps. Payload file is named like the folder (BASLUS-21291NN).
- Checksum: S3-style sum-zero is FALSE here; S5's checksum (if any) is uncracked —
  writes carry a warning + .bak; test on a card copy. Write path round-trips with ECC
  intact on a copied card.
- NEEDS MORE SAVES: per-character stat/party/gold/recruit offsets, checksum algorithm,
  and the **New Game Plus flag** — require multiple saves (esp. a pre/post-NG+ pair) to
  diff. Only one distinct save state is currently available.

The Cheat Engine RAM struct (`s5_char_struct.json`) is a guide for the per-character
save layout once located.
