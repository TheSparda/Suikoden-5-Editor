"""
Suikoden V (USA, SLUS-21291) — verified ISO data tables + field schema.

Reverse-engineered from Tony H's Suikoden5EditorV10.exe (via monodis) and validated
against a real ISO for 6 known characters. See Suikoden5_ISO_offsets.md for how each
table was confirmed. Bases/strides are VERIFIED; field labels marked "(?)" are
tentative (offset holds real per-character data, but the exact stat name is unconfirmed).

Characters are keyed by the decimal id (NNN) from the exe's name list; the playable
list is in s5_characters.json. Every per-character table is `base + id*stride`.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

SERIAL_OFF = 0x828BD
SERIAL_STR = b"SLUS_212.91"

# ---- per-character tables: name -> (base, stride, [(label, off, width, kind), ...])
# kind: num | rank (0..7 skill grade) | item | rune
STAT_LABELS = [  # twelve u16 at +0x02.. ; HP confirmed, rest tentative (CT order)
    ("HP", "num"), ("SP (?)", "num"), ("Attack (?)", "num"), ("Magic (?)", "num"),
    ("PDF (?)", "num"), ("MDF (?)", "num"), ("Technique (?)", "num"),
    ("Accuracy (?)", "num"), ("Evasion (?)", "num"), ("Speed (?)", "num"),
    ("Stat11 (?)", "num"), ("Stat12 (?)", "num"),
]

def _stat_fields():
    f = [("Level", 0x00, 1, "num"), ("Level cap (?)", 0x01, 1, "num")]
    for i, (lbl, kind) in enumerate(STAT_LABELS):
        f.append((lbl, 0x02 + i*2, 2, kind))
    for i in range(16):
        f.append((f"Skill rank {i+1}", 0x1A + i, 1, "rank"))
    return f

TABLES = {
    "stats":      (0x49F0DC, 0x7C, _stat_fields()),
    "thresholds": (0x4987C0, 0x60, [(f"Threshold {i+1}", i, 1, "num") for i in range(16)]),
    "skills":     (0x48A970, 0x12, [(f"Skill {i+1}", i, 1, "num") for i in range(18)]),
    "equipment":  (0x493112, 0x18, [("Equip slot 1 (?)", 0, 1, "item"),
                                    ("Equip slot 2 (?)", 1, 1, "item"),
                                    ("Equip slot 3 (?)", 2, 1, "item"),
                                    ("Equip slot 4 (?)", 3, 1, "item")]),
    "runes":      (0x4E87F0, 0x54, [("Rune slot 1 (?)", 0, 1, "rune"),
                                    ("Rune slot 2 (?)", 1, 1, "rune"),
                                    ("Rune u16 (?)",    2, 2, "num"),
                                    ("Rune slot 3 (?)", 4, 1, "rune"),
                                    ("Rune flag (?)",   5, 1, "num")]),
}

# Character name table (separate index order from the NNN id list).
NAME_TABLE_BASE = 0x691600
NAME_ENTRY_SIZE = 8
NAME_MAX_CHARS  = 7

# Fixed cost/threshold ladder (purpose unconfirmed).
LADDER_OFF = 0x4986C0
LADDER_COUNT = 40
LADDER_EXTRA_OFF = 0x31A6BC

RANK_HELP = "skill rank byte: 0..7 (higher = better; 07 ~ SS)"

def load_characters():
    """[{id, name}] playable list; falls back to empty if json missing."""
    try:
        return json.load(open(os.path.join(HERE, "s5_characters.json")))
    except Exception:
        return []
