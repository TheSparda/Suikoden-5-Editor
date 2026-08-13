# Suikoden V (SLUS-21291, USA) — ISO offsets

Reverse-engineered from Tony H's `Suikoden5EditorV10.exe` via `monodis`
(`Editor/Suikoden5Editor_monodis.il`). Namespace `Suikoden_V_PS2_ROM_Editor`, all
logic in `Form1`.

## Identity / serial
- Serial string `SLUS_212.91;1` at ISO offset **0x828BD** (exe validates the region at 0x828C4).
- Confirmed against `ISO/Suikoden V - OG.iso`.

## Character table
- **Base: `0x498C00`** (dec 4818624).
- **Record stride: `180` bytes (0xB4).** Offset for character *i* = `0x498C00 + i*180`.
- Selected via a name list (`list1RTB`); the handler computes `base + index*180` (`mul` in
  `list1RTB_Click`) and writes it into the ROM-address box, which `Patch1_Click` reads as hex.

### Character record layout (180 bytes) — from `Patch1_Click` write order
Sizes are authoritative (from `BinaryWriter.Write` overloads). Semantic names are best-effort
from the exe's Form1 labels (HP, ATK, MAG, PDF, MDF, TEC, ACC, EVA, SPD, LUK, Level, Skill
Points, *Growth variants, Head/Hand runes, equipment) + the cheat-table field order — a precise
per-NUD label pass (Form1 designer tab order) is still pending.

| Off | Size | Control | Group (tentative) |
|----:|:----:|---------|-------------------|
| 0x00 | 1 | list1_2NUD | Level / small stat |
| 0x01 | 1 | list1_3NUD | small stat |
| 0x02..0x18 | 2 each (12) | list1_4..15NUD | core stats: HP, SP, ATK, MAG, PDF, MDF, TEC, ACC, EVA, SPD, LUK (+1) |
| 0x1A..0x29 | 1 each (16, incl. 28a/29a) | list1_16..29aNUD | skill ranks / flags |
| 0x2A..0x34 | 2 each (6) | list1_31/35/39/43/47/51NUD | growth or magic-threshold values |
| 0x36..0x45 | 1 each (16) | list2_1..16NUD | skills block A |
| 0x46..0x57 | 1 each (18) | list3_1..18NUD | skills block B |
| 0x58..0x5B | 1 each (4) | list4_1..4NUD | equipment (helm/armor/gloves/boots?) |
| 0x5C..0x61 | 1/1/2/1/1 | list5_1..5NUD | runes (head/right/left) + flags |
| 0x62..0xB3 | 2 each (41) | item1_1..41NUD | starting items |

## Other ISO data tables (bases from `list2–5RTB_Click`, `Open_Click`)
Each is an index list selected the same way (`base + index*stride`); strides TBD per table.
- `0x4987C0` — list2 table
- `0x48A970` — list3 table
- `0x493112` — list4 table
- `0x4E87F0` — list5 table
- `0x498C00`/`0x4986C0` referenced in `Open_Click` (character table load region)

## Reference forms
- `Form2ItemList` — item hex-ID reference list.
- `Form3ListAllArmor` — armor reference list.
- Enemy target/rune-resist/drop docs embedded as message strings (see IL for enemy section).

## Save-side (RAM) struct
See `Editor/s5_char_struct.json` (from the Cheat Engine table) — separate RAM layout used for
the memory-card save editor; maps onto on-disk `.ps2` gamedata (RE step pending, needs real saves).
