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
STAT_LABELS = [  # twelve u16 at +0x02..; order VERIFIED vs the L60 Character Database
    # guide (Hp, Spell Count, Atk, Mag, Pdf, Mdf, Tec, Acc, Eva, Spd, Luk).
    ("HP", "num"), ("Spell Count", "num"), ("Attack", "num"), ("Magic", "num"),
    ("PDF", "num"), ("MDF", "num"), ("Technique", "num"), ("Accuracy", "num"),
    ("Evasion", "num"), ("Speed", "num"),
    ("Field11 (~const, unverified)", "num"),   # ~2500 across chars; not a listed stat
    ("Luck", "num"),
]

def _skill_names():
    try: return json.load(open(os.path.join(HERE, "s5_skill_names.json")))
    except Exception: return []
SKILL_NAMES = _skill_names()
def _skill_label(i):
    return SKILL_NAMES[i] if i < len(SKILL_NAMES) else f"Skill {i+1}"

# Skill-rank byte value -> grade (verified 01=E .. 07=SS).
RANK_NAMES = ["—", "E", "D", "C", "B", "A", "S", "SS"]

def _stat_fields():
    f = [("Level", 0x00, 1, "num"), ("Level cap (?)", 0x01, 1, "num")]
    for i, (lbl, kind) in enumerate(STAT_LABELS):
        f.append((lbl, 0x02 + i*2, 2, kind))
    # +0x1A..0x29: per-skill affinity ranks, labeled positionally (Stamina, Attack, ...)
    for i in range(16):
        f.append((_skill_label(i) + " affinity", 0x1A + i, 1, "rank"))
    return f

TABLES = {
    "stats":      (0x49F0DC, 0x7C, _stat_fields()),
    "thresholds": (0x4987C0, 0x60, [(f"Magic threshold {i+1}", i, 1, "num") for i in range(16)]),
    "skills":     (0x48A970, 0x12, [(_skill_label(i), i, 1, "rank") for i in range(18)]),
    "items":      (0x493112, 0x18, [(f"Starting item {i+1}", i, 1, "item") for i in range(4)]),
    "runes":      (0x4E87F0, 0x54, [("Rune slot 1 (id)", 0, 1, "rune"),
                                    ("Rune slot 2 (id)", 1, 1, "rune"),
                                    ("Rune value (u16)", 2, 2, "num"),
                                    ("Rune slot 3 (id)", 4, 1, "rune"),
                                    ("Rune flag",        5, 1, "num")]),
}

# Character name table (separate index order from the NNN id list).
NAME_TABLE_BASE = 0x691600
NAME_ENTRY_SIZE = 8
NAME_MAX_CHARS  = 7

# Item/equipment PRICE table (VERIFIED vs the community rune guide: buy+sell u32,
# sell == buy/2; e.g. record 17 = 6000/3000 (Fire Rune), record 21 = 35000/17500
# (Shield Rune)). Records are in item-id order; names live in a parallel pool.
PRICE_BASE  = 0x49433C
PRICE_STRIDE = 148          # 0x94
PRICE_COUNT = 148
PRICE_FIELDS = [("buy", 0x00, 4), ("sell", 0x04, 4)]

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
