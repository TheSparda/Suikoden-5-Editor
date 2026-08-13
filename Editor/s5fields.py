"""
Named field schema for the Suikoden V character record, reconstructed from the
decompiled Suikoden5EditorV10.exe (by Tony H) via `Patch1_Click`'s write order.

Each field: (label, offset_within_record, width_bytes, kind)
  kind: "num"  = plain number
        "item" = 2-byte item/equipment hex id
        "rune" = 1-byte rune slot id
        "skill"= 1-byte skill id / rank
        "rank" = skill rank byte (01=E 02=D 03=C 04=B 05=A 06=S 07=SS)

STATUS: PROVISIONAL / UNVERIFIED. The field *sizes* and write *order* are decoded
correctly from the exe, but the character record's base address and stride are NOT
yet pinned down: `Patch1_Click` seeks per sub-block and the record address is derived
from the RTF name list embedded in Form1.resources (not a plain base+index*stride).
Two base/stride guesses (0x498C00/180, 0x4967DC/124) both landed in Shift-JIS text
regions, so DO NOT rely on these offsets for writing yet. Next RE step: extract the
list1RTB RTF from Form1.resources to recover each character's real ROM address.

The Cheat-Engine-derived RAM save struct in s5_char_struct.json IS reliable and is
the basis for the memory-card save editor.
"""

CHAR_TABLE_BASE   = 0x498C00
CHAR_STRIDE       = 180
SERIAL_OFF        = 0x828BD
SERIAL_STR        = b"SLUS_212.91"

RANK_HELP = "01=E 02=D 03=C 04=B 05=A 06=S 07=SS"

# Core starting stats (12 x u16 at 0x02..0x18). Named from the exe's stat labels /
# cheat-table order (HP, SP, ATK, MAG, PDF, MDF, TEC, ACC, EVA, SPD, LUK, +1).
_CORE_STATS = [
    ("HP",         0x02), ("Skill Points (SP)", 0x04), ("Attack",   0x06),
    ("Magic",      0x08), ("PDF",               0x0A), ("MDF",      0x0C),
    ("Technique",  0x0E), ("Accuracy",          0x10), ("Evasion",  0x12),
    ("Speed",      0x14), ("Luck",              0x16), ("Stat 12 (verify)", 0x18),
]

CHAR_FIELDS = []

def _add(label, off, w, kind):
    CHAR_FIELDS.append((label, off, w, kind))

_add("Level",             0x00, 1, "num")
_add("Skill Points byte", 0x01, 1, "num")
for _lbl, _o in _CORE_STATS:
    _add(_lbl, _o, 2, "num")

# Skill-rank / flag bytes (0x1A..0x29, 16 bytes incl. 28a/29a in the exe).
for _i in range(16):
    _add(f"Skill/flag byte {_i+1} (verify)", 0x1A + _i, 1, "num")

# Growth / magic-threshold values (6 x u16 at 0x2A..0x34).
for _i in range(6):
    _add(f"Growth/threshold {_i+1} (verify)", 0x2A + _i*2, 2, "num")

# Skill block A (list2, 16 x u8 at 0x36..0x45) — skill ranks.
for _i in range(16):
    _add(f"Skill A{_i+1} rank", 0x36 + _i, 1, "rank")

# Skill block B (list3, 18 x u8 at 0x46..0x57) — skill ranks.
for _i in range(18):
    _add(f"Skill B{_i+1} rank", 0x46 + _i, 1, "rank")

# Equipment (list4, 4 x u8 at 0x58..0x5B): helm/armor/gloves/boots.
for _lbl, _o in (("Equipped Helm", 0x58), ("Equipped Armor", 0x59),
                 ("Equipped Gloves", 0x5A), ("Equipped Boots", 0x5B)):
    _add(_lbl, _o, 1, "num")

# Runes + flags (list5, at 0x5C..0x61: u8,u8,u16,u8,u8).
_add("Rune slot 1 (verify)", 0x5C, 1, "rune")
_add("Rune slot 2 (verify)", 0x5D, 1, "rune")
_add("Rune/flag u16 (verify)", 0x5E, 2, "num")
_add("Rune slot 3 (verify)", 0x60, 1, "rune")
_add("Flag (verify)", 0x61, 1, "num")

# Starting items (item1, 41 x u16 at 0x62..0xB3): item hex ids.
for _i in range(41):
    _add(f"Starting item {_i+1} (id)", 0x62 + _i*2, 2, "item")

RECORD_SIZE = CHAR_STRIDE  # 180; last item ends at 0xB2..0xB3

assert CHAR_FIELDS[-1][1] + CHAR_FIELDS[-1][2] <= CHAR_STRIDE, "field overruns record"
