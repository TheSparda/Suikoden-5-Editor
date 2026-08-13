"""
Suikoden V (USA, SLUS-21291) — verified ISO structures + schema notes.

Reverse-engineered from Tony H's Suikoden5EditorV10.exe (via monodis) and validated
against a real ISO. IMPORTANT: unlike the S3 editor, S5's original tool does NOT use a
clean `base + index*stride` character table. It LOCATES a character by searching the ISO
for the character's name bytes, then edits stats at manual offsets from the match. So
there is no simple stat-record formula; stat offsets remain unverified (research).

VERIFIED
--------
- Serial `SLUS_212.91` at ISO offset 0x828BD.
- Character NAME table: 8-byte fixed-width, ASCII, null-padded, at 0x691600.
  Enumerable/editable (rename). Full dump in s5_char_names.json (~130 playable names
  first, then rune/other names). Renaming is safe within each 8-byte slot (<=7 chars).
- Numeric ladder at 0x4986C0: 4 groups of 10 ascending u16 values ending in 999
  (0,20,40,75,100,130,165,225,290,999, ...). Purpose likely a skill/forge cost or
  threshold table (NOT starting items, despite the exe's "item1" control names).
  Single u16 at 0x31A6BC = 1000. Purpose unverified.

RESEARCH / UNVERIFIED
---------------------
- Character stat records: located by name search in the exe; no confirmed base/stride.
  The RAM save struct in s5_char_struct.json (from the cheat table) is the reliable
  layout for the memory-card save editor and is unrelated to these ISO offsets.
"""

SERIAL_OFF   = 0x828BD
SERIAL_STR   = b"SLUS_212.91"

# Character name table (VERIFIED).
NAME_TABLE_BASE   = 0x691600
NAME_ENTRY_SIZE   = 8
NAME_MAX_CHARS    = 7          # 7 chars + 1 null terminator per slot

# Cost/threshold ladder (VERIFIED location; purpose unconfirmed).
LADDER_OFF   = 0x4986C0
LADDER_COUNT = 40             # 4 groups of 10 u16
LADDER_EXTRA_OFF = 0x31A6BC   # single u16

RANK_HELP = "01=E 02=D 03=C 04=B 05=A 06=S 07=SS"
