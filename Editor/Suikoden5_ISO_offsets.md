# Suikoden V (SLUS-21291, USA) — ISO offsets

Reverse-engineered from Tony H's `Suikoden5EditorV10.exe` via `monodis`, and validated
against a real ISO. Namespace `Suikoden_V_PS2_ROM_Editor`, logic in `Form1`.

## Identity / serial  (VERIFIED)
- `SLUS_212.91;1` at ISO offset **0x828BD** (exe validates region 0x828C4).

## Addressing model  (VERIFIED — important)
S5's original editor does **not** use a clean `base + index*stride` character table like
Suikoden III. When you click a name in `list1RTB`, `list1RTB_Click`:
1. reads the clicked line text, converts it to bytes (`Encoding.ASCII` + an identity
   `byte→uint32` map), and fills the `list1Search*NUD` search boxes;
2. **searches the ISO** for that signature to locate the record;
3. edits stats at manual offsets from the match (the address boxes `addrList1TxtBox` etc.).

So there is no single character-stat formula. Earlier `base+index*stride` guesses
(0x498C00/180, 0x4967DC/124) were **wrong** — 0x4967DC is a Shift-JIS name/text region.

## Character NAME table  (VERIFIED — editable)
- **8-byte fixed-width, ASCII, null-padded, base `0x691600`.**
- Entry i at `0x691600 + i*8`. Names <= 7 chars + null. Renameable in place.
- Order (first entries): Raja, Zerase, Craig, Galleon, Boz, Nakula, Lyon, Talgeyl,
  Eresh, Dinn, Kyle, Zegai, Isato, Haswar, Belcoot, Norma, Ax, Cathari, Georg, ...
- Full dump: `s5_char_names.json` (~130 playable, then rune/other names to ~0x692590).
- A second copy of the name strings exists around `0x6933E8` (e.g. Georg found there too).

## Numeric ladder  (VERIFIED location; purpose unconfirmed)
- `0x4986C0`: 4 groups of 10 u16, ascending, capped 999
  (`0,20,40,75,100,130,165,225,290,999` / `60,90,...,999` / ...). Read on ISO open into
  the exe's `item1_*` controls, but the values are a cost/threshold ladder, NOT items.
- `0x31A6BC`: single u16 = 1000. Purpose unconfirmed.

## Still open (research)
- Character **stat records**: located by name search in the exe; base/stride/field
  offsets not yet confirmed. The `Patch1_Click` write sizes/order are known
  (`s5_iso_seeks.json`) but are relative to a search-match address, not a fixed base.
- Data tables referenced by the other list handlers: `0x4987C0`, `0x48A970`,
  `0x493112`, `0x4E87F0` (strides/contents TBD).

## Save-side (RAM) struct
`s5_char_struct.json` (from the Cheat Engine table) is the reliable RAM layout for the
memory-card save editor; unrelated to the ISO offsets above.
