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
    # +0x1A..0x29: per-skill growth bytes (values 0..~50, NOT the 0..7 E-SS grade —
    # the actual skill grade lives in the Skills table). Kept numeric + unverified.
    for i in range(16):
        f.append((_skill_label(i) + " growth (?)", 0x1A + i, 1, "num"))
    return f

TABLES = {
    "stats":      (0x49F0DC, 0x7C, _stat_fields()),
    # 0x4987C0/0x60 is per-character WEAPON GROWTH (attack power at sharpen levels
    # 1-16), edited by the community exe's list2 ("Weapon level N Attack Power" /
    # "Found your weapon stats"). Verified: 376 varied ascending records, base+stride
    # match the exe handler. (Previously mislabeled here as "Magic thresholds".)
    "weapon growth": (0x4987C0, 0x60, [(f"Sharpen Lv{i+1} attack", i, 1, "num") for i in range(16)]),
    "skills":     (0x48A970, 0x12, [(_skill_label(i), i, 1, "rank") for i in range(18)]),
    "items":      (0x493112, 0x18, [(f"Starting item {i+1}", i, 1, "item") for i in range(4)]),
    # NOTE: 0x4E87F0/0x54 is the SPELL DEFINITION table (element/power/target),
    # indexed by SPELL id — NOT per-character runes. It was previously (wrongly)
    # rendered here indexed by character id. Removed; see SPELLS below + the Spells tab.
}

# ---- Spell definition table (VERIFIED against ~25 known spells) --------------
# Base 0x4E87F0, stride 0x54, indexed by spell id (0..105, order = spell-name pool).
#   +0 u8  Element   (clusters perfectly by element)
#   +1 u8  (unverified; ascending within element — hidden)
#   +2 u16 Power     (ascending damage; 9999 = full heal)
#   +4 u8  Target    (matches the exe legend: 02/04/0A/0C/14/24/44)
#   +5 u8  (unverified; mostly 0 — hidden)
SPELL_BASE, SPELL_STRIDE, SPELL_COUNT = 0x4E87F0, 0x54, 106
# Element value -> name. NB: 3/4 are Wind/Water per the ISO data (the community
# exe's own legend had them swapped); all others matched.
ELEMENT_NAMES = {0: "Sun / Special", 1: "Fire", 2: "Lightning", 3: "Wind",
                 4: "Water", 5: "Earth", 6: "Star", 7: "Sound", 8: "Holy",
                 9: "Dark", 0xA: "Slash", 0xB: "Thrust", 0xC: "Punch", 0xD: "Shoot"}
# Target value -> name (from the community exe's documented legend, data-verified).
TARGET_NAMES = {1: "Transform", 2: "Single (ally)", 3: "Self", 4: "Single (enemy)",
                0xA: "All (ally)", 0xC: "All (enemy)", 0x14: "Column", 0x24: "Row",
                0x44: "Cluster"}
SPELL_FIELDS = [("Element", 0, 1, "element"),
                ("Power / heal (u16)", 2, 2, "num"),
                ("Target", 4, 1, "target")]

# ---- Rune -> spell GRANT table (VERIFIED vs the rune guide, 24/24) -----------
# Base 0x4E6DA2, stride 0x46, 24 records. A rune teaches the CONTIGUOUS spell
# range [start .. start+count-1] (spell ids index the spell table above).
#   +0 u8 start spell id, +2 u8 count, +3 u8 flag(=1), +4.. Shift-JIS name.
RUNE_GRANT_BASE, RUNE_GRANT_STRIDE, RUNE_GRANT_COUNT = 0x4E6DA2, 0x46, 24
RUNE_GRANT_NAMES = [
    "Fire Rune", "Rage Rune", "Lightning Rune", "Thunder Rune", "Water Rune",
    "Flowing Rune", "Wind Rune", "Cyclone Rune", "Earth Rune", "Mother Earth Rune",
    "Star Rune", "Blinking Rune", "Sound Rune (DoReMi)", "Beast Rune", "Shield Rune",
    "Pale Gate Rune", "Resurrection Rune", "Rune 17 (spells 59-62)",
    "Rage Sword Rune", "Thunder Sword Rune", "Flowing Sword Rune",
    "Cyclone Sword Rune", "Mother Earth Sword Rune", "Rune of Condemnation",
]
RUNE_GRANT_FIELDS = [("Start spell", 0, 1, "spellid"), ("Spell count", 2, 1, "num")]

# Spells not owned by any grant record, surfaced as read-only "runes" so every
# spell is still reachable + editable from the Runes tab (no separate Spells tab).
SYNTH_RUNE_BASE = 100
SYNTH_RUNES = [
    {"name": "Dawn Rune", "start": 0, "count": 4},       # Time of Wakening..Crimson Sky
    {"name": "Twilight Rune", "start": 4, "count": 4},    # Evening Dusk..Vermilion Sky
    {"name": "Other · Level placeholders", "start": 82, "count": 24},
]

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

# Per-section help, drawn from the original community editor's own labels/messages.
SECTION_HELP = {
    "stats":      "Edit character stats here. These are starting values — ISO edits apply to a NEW GAME.",
    "weapon growth": "Attack power of this character's weapon at each sharpen level (1-16), ascending. Verified against the original editor's Weapon Growth tab.",
    "skills":     "Per-skill affinity / aptitude. Rank 01=E, 02=D, 03=C, 04=B, 05=A, 06=S, 07=SS (higher learns faster / caps higher).",
    "items":      "Items the character is carrying when you start a new game.",
    "runes":      "Runes equipped at the start of a new game (head / right / left slots). Rune id space is unconfirmed — labels are best-effort.",
}
GLOBAL_HELP = "Edits apply to a NEW GAME. Do NOT use emulator save states — use in-game save files."

# ---- Enemy / unit editor -----------------------------------------------------
# Enemies live in the SAME record table as characters (0x49F0DC/0x7C), indexed by
# a unit id. Combat stats are the shared u16 stat block (+0x02..); enemy-specific
# fields VERIFIED via enemy 004 Nariqua (drop 0x1007 "Drain Piece" in the 20% slot):
#   drops at +0x2c/0x34/0x3c/0x44/0x4c (40/20/10/5/1%), Starting Potch +0x54 (all u16).
ENEMY_BASE, ENEMY_STRIDE, ENEMY_MAX = 0x49F0DC, 0x7C, 584
def _enemy_fields():
    f = [(lbl, 0x02 + i*2, 2, "num") for i, (lbl, _k) in enumerate(STAT_LABELS)]
    for pct, off in [("40%", 0x2c), ("20%", 0x34), ("10%", 0x3c), ("5%", 0x44), ("1%", 0x4c)]:
        f.append((f"Drop {pct} (item hex id)", off, 2, "num"))
    f.append(("Starting Potch", 0x54, 2, "num"))
    return f
ENEMY_FIELDS = _enemy_fields()

def load_characters():
    """[{id, name}] playable list; falls back to empty if json missing."""
    try:
        return json.load(open(os.path.join(HERE, "s5_characters.json")))
    except Exception:
        return []
