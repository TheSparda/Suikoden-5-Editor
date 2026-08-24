"""
Suikoden V (USA, SLUS-21291) — verified ISO data tables + field schema.

Reverse-engineered from the game's own data and validated
against a real ISO for 6 known characters. Bases/strides confirmed empirically. Bases/strides are VERIFIED; field labels marked "(?)" are
tentative (offset holds real per-character data, but the exact stat name is unconfirmed).

Characters are keyed by the decimal id (NNN) from the game's name list; the playable
list is in s5_characters.json. Every per-character table is `base + id*stride`.
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))

def res_json(name):
    """Load a bundled s5_*.json resource. Reads from disk beside the sources normally;
    inside the single-file .pyz build (where open() can't reach archive members) it
    falls back to pkgutil, which reads straight out of the zip."""
    p = os.path.join(HERE, name)
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    import pkgutil
    data = pkgutil.get_data(__name__, name)
    if data is None: raise FileNotFoundError(name)
    return json.loads(data.decode("utf-8"))

SERIAL_OFF = 0x828BD
SERIAL_STR = b"SLUS_212.91"

# ---- per-character tables: name -> (base, stride, [(label, off, width, kind), ...])
# kind: num | rank (0..7 skill grade) | item | rune
def _skill_names():
    try: return res_json("s5_skill_names.json")
    except Exception: return []
SKILL_NAMES = _skill_names()
def _skill_label(i):
    return SKILL_NAMES[i] if i < len(SKILL_NAMES) else f"Skill {i+1}"

# Skill-rank byte value -> grade (verified 01=E .. 07=SS).
RANK_NAMES = ["—", "E", "D", "C", "B", "A", "S", "SS"]
# Elemental affinity grade (value IS the grade): 0=None,1=E,2=D,3=C,4=B,5=A,6=S.
# VERIFIED vs a community-documented affinities1.txt + the ISO affinity table. (Distinct scale
# from RANK_NAMES, which is 1-based with 0="—".)
AFFINITY_GRADES = ["None", "E", "D", "C", "B", "A", "S"]

# ---- Character stats + growths (VERIFIED vs the game's stat data)
# Base 0x48A970, stride 0x12 (18 u8), indexed by the character id (list order, Hero=0;
# addr = 0x48A970 + id*0x12). Confirmed byte-for-byte against the game's stat data:
#   Dinn(11)@0x48AA36 = HP50/Atk30/Tec5/Mag5/Eva5/PDF1/MDF5/Spd10/Luk5,
#   Lance(27)@0x48AB56 = HP50/Atk80/Tec5/Mag5/Eva5/PDF50/MDF10/Spd5/Luk5 — exact match.
# Field order (9 base stats then 9 growths, all u8): HP, Attack, Technique, Magic,
# Evasion, PDF, MDF, Speed, Luck. Left column = starting stats (edit before new game);
# right column = per-level growth. (This table was previously mislabeled "skills"; the
# old 0x49F0DC "stats" table is actually the ENEMIES table — hence its inflated numbers.)
_CHAR_STATS = ["HP", "Attack", "Technique", "Magic", "Evasion", "PDF", "MDF", "Speed", "Luck"]
def _char_stat_fields():
    f = [(lbl, i, 1, "num") for i, lbl in enumerate(_CHAR_STATS)]
    f += [(lbl + " Growth", 9 + i, 1, "num") for i, lbl in enumerate(_CHAR_STATS)]
    return f

TABLES = {
    "stats":      (0x48A970, 0x12, _char_stat_fields()),
    # ---- Elemental affinities (VERIFIED @0x48B530, stride 14, char-indexed like stats).
    # Recovered by byte-matching a community-documented "Affinity" module slice into the ISO, then
    # verified by content: Prince Sun=A(5), Zerase Fire/Star/Dark=A, Lance Punch=A. Each of
    # the 14 elements is one grade byte 0=None..6=S (AFFINITY_GRADES). Column order is the
    # Affinity module's (Wind@3 / Water@4, matching our spell ELEMENT_NAMES, not the
    # community elements.txt which had them swapped). Placed right after stats in the UI.
    "affinities": (0x48B530, 14, [(e, i, 1, "grade") for i, e in enumerate(
        ["Sun", "Fire", "Lightning", "Wind", "Water", "Earth", "Star", "Sound",
         "Holy", "Dark", "Slash", "Thrust", "Punch", "Shoot"])]),
    # ---- Equipable-skill CAPS per character (VERIFIED @0x4B2731, stride 49, count 113,
    # char-indexed). Recovered from a community-documented "Equipable Skill" module + byte-matched
    # (block is byte-identical to the extract). Each of the 48 skills is one byte = the
    # max rank this character can equip it at: 0=None..7=SS (RANK_NAMES). Verified by
    # content: Prince Attack/Technique/MDef=S; Zerase Magic/Incantation=S + SS support
    # caps; Lance Attack S / Dragon Special SS. The 49th byte is padding (unused).
    "equipable skills": (0x4B2731, 49, [(n, i, 1, "rank") for i, n in enumerate(
        ["Stamina", "Attack", "Defense", "Technique", "Vitality", "Agility", "Magic",
         "Magic Defense", "Incantation", "Sword of Magic", "Raging Lion", "Fate Control",
         "Karmic Effect", "Armor of Gods", "Swift Foot", "Triple Harmony", "All-out Strike",
         "Untold Clarity", "Divine Right", "Zen Sword", "Sacred Oath", "Royal Paradise",
         "Thief", "Mow Down", "Pierce", "Freeze", "??? (unused)", "Barrage", "Long Throw",
         "Dragon Special", "Forge", "Combat Teacher", "Chain Magic", "Analyze",
         "Potch Finder", "Treasure Hunt", "Escape Route", "Healing", "Treatment", "Haggle",
         "Trade In", "Cook", "Rune Sage", "Bard", "Perfect Pitch", "Appraisal", "Bath",
         "Tutor"])]),
    # 0x4987C0/0x60 is per-character WEAPON GROWTH (attack power at sharpen levels
    # 1-16), edited by the game's weapon-growth data (
    # "Found your weapon stats"). Verified: 376 varied ascending records, base+stride
    # match the ISO. (Previously mislabeled here as "Magic thresholds".)
    "weapon growth": (0x4987C0, 0x60, [(f"Sharpen Lv{i+1} attack", i, 1, "num") for i in range(16)]),
    # (Removed the old "skills" table — 0x48A970/0x12 is the character STATS table above.)
    # 4 armor slots equipped at new-game start (the "Starting Equipment" data:
    # Head/Body/Arm/Feet). Values are armor ids; clean armor names aren't id-aligned
    # so shown numerically.
    # Four VERIFIED armor slots. The stored byte is the GAME ARMOR ID (0 = Nothing),
    # matching the verified armor id list (s5_armor_names.json):
    #   +0 Head (33 ids), +1 Body (72), +2 Arm/gloves (38), +3 Foot (31).
    # Ground truth: Richard = [0x11,0x24,0x11,0x0E] = Knight Headpiece / Knight Full Armor
    # / Knight Gloves / Knight Boots (a full knight set). (slot2 was briefly "unknown" — it
    # is the Arm/glove slot; the ELF pool lacked an "Arm -" desc prefix, but the armor id list includes it.)
    "starting equipment": (0x493112, 0x18, [("Head", 0, 1, "armorhead"), ("Body", 1, 1, "armorbody"),
                                            ("Arm (gloves)", 2, 1, "armorarm"), ("Feet", 3, 1, "armorfoot")]),
    # Same 0x493112/0x18 record also holds FOUR starting held-item slots as u32 NAME
    # POINTERS (into the internal item-name pool): +0x06/+0x0a/+0x0e/+0x12. 0x6DFDE8 = empty.
    # Editable only within the closed set of pointers the game already uses for held items
    # (s5_held_items.json) so every written value is a known-valid item reference.
    # Ground truth: Prince = Lightning Amulet, Richard = Sun Badge + Jewel Necklace.
    "starting items": (0x493112, 0x18, [("Item 1", 0x06, 4, "helditem"), ("Item 2", 0x0a, 4, "helditem"),
                                        ("Item 3", 0x0e, 4, "helditem"), ("Item 4", 0x12, 4, "helditem")]),
    # NO "potch" table. The +0x54 u16 was previously shown as "Starting Potch" — that
    # was WRONG. The game data reads 0x54 as list1_51NUD, the last of six generic
    # unlabeled "growth" NUDs (list1_31..51 -> record offsets 0x2c,0x34,0x3c,0x44,0x4c,
    # 0x54, step 8). Its values are byte-identical across whole bands of unrelated units
    # (chars AND enemies share them by id range) and rise with id — a shared growth/exp
    # tier value, not money. Meaning unverified -> omitted from the editor.
    # NOTE: 0x4E87F0/0x54 is the SPELL DEFINITION table (element/power/target),
    # indexed by SPELL id — NOT per-character runes. It was previously (wrongly)
    # rendered here indexed by character id. Removed; see SPELLS below + the Spells tab.
}

# ---- Spell definition table (VERIFIED against ~25 known spells) --------------
# Base 0x4E87F0, stride 0x54, indexed by spell id (0..105, order = spell-name pool).
#   +0 u8  Element   (clusters perfectly by element)
#   +1 u8  (unverified; ascending within element — hidden)
#   +2 u16 Power     (ascending damage; 9999 = full heal)
#   +4 u8  Target    (matches the documented legend: 02/04/0A/0C/14/24/44)
#   +5 u8  (unverified; mostly 0 — hidden)
SPELL_BASE, SPELL_STRIDE, SPELL_COUNT = 0x4E87F0, 0x54, 106
# Element value -> name. NB: 3/4 are Wind/Water per the ISO data (the community
# exe's own legend had them swapped); all others matched.
ELEMENT_NAMES = {0: "Sun / Special", 1: "Fire", 2: "Lightning", 3: "Wind",
                 4: "Water", 5: "Earth", 6: "Star", 7: "Sound", 8: "Holy",
                 9: "Dark", 0xA: "Slash", 0xB: "Thrust", 0xC: "Punch", 0xD: "Shoot"}
# Target value -> name (from the documented legend, data-verified).
TARGET_NAMES = {1: "Transform", 2: "Single (ally)", 3: "Self", 4: "Single (enemy)",
                0xA: "All (ally)", 0xC: "All (enemy)", 0x14: "Column", 0x24: "Row",
                0x44: "Cluster"}
SPELL_FIELDS = [("Element", 0, 1, "element"),
                ("Power / heal (u16)", 2, 2, "num"),
                ("Target", 4, 1, "target"),
                # +5 Status byte VERIFIED across all 106 spells: 0x02 = exactly the three
                # revive spells (Light of Day / Mother Ocean / Yell); 0x20 = the 31
                # status/buff appliers (Wind of Sleep, Silent Lake, Clay Guardian, ...).
                # 0x01 appears on 4 spells (First Ray, Thunder Runner, Furious Blow,
                # Comet) and is undocumented.
                ("Status", 5, 1, "spellstatus")]
SPELL_STATUS_NAMES = {0: "None", 1: "Unknown (0x01)", 2: "Revive", 0x20: "Add status"}

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

# ---- Rune (orb) price table (VERIFIED @0x4E24FC vs the rune guide: Fire Orb
# 6000/3000, Shield Orb 35000/17500; sell = buy/2; event-only orbs have buy=0).
# From a community-documented Rune Price module (NTSC-U), byte-matched into the ISO. 70 records,
# stride 76; buy @+0 and sell @+4 are 3-byte LE (byte 3/7 always 0). Names = runes.txt
# order (s5_runeprice_names.json). NOTE: PAL uses stride 80 (separate layout).
RUNEPRICE_BASE, RUNEPRICE_STRIDE, RUNEPRICE_COUNT = 0x4E24FC, 76, 70
def _runeprice_names():
    try: return res_json("s5_runeprice_names.json")
    except Exception: return []
RUNEPRICE_NAMES = _runeprice_names()

# ---- Healing-item price table (@0x4CCFD0, from a community-documented Healing Item Price
# module, byte-matched into the ISO). 41 records, stride 88; buy @+0 / sell @+4,
# 3-byte LE. Names = healing items.txt order (s5_healprice_names.json).
HEALPRICE_BASE, HEALPRICE_STRIDE, HEALPRICE_COUNT = 0x4CCFD0, 88, 41
def _healprice_names():
    try: return res_json("s5_healprice_names.json")
    except Exception: return []
HEALPRICE_NAMES = _healprice_names()

# Item/equipment PRICE table (VERIFIED vs the rune guide: buy+sell u32,
# sell == buy/2; e.g. record 17 = 6000/3000 (Fire Rune), record 21 = 35000/17500
# (Shield Rune)). Records are in item-id order; names live in a parallel pool.
PRICE_BASE  = 0x49433C
PRICE_STRIDE = 148          # 0x94
PRICE_COUNT = 148
PRICE_FIELDS = [("buy", 0x00, 4), ("sell", 0x04, 4)]

# ---- Armor stat tables (VERIFIED vs the Suikosource Armor List guide) --------
# Four per-slot tables, stride 0x94, indexed by an internal armor-stat id (NOT the
# Form3/equip id order). Each record: +0x00 u32 buy, +0x04 u32 sell (=buy/2),
# +0x08 Shift-JIS stat-summary (the in-game "what it does" line), then a signed
# stat block: +0x63 HP, +0x64 ATK, +0x66 MAG, +0x67 Evade, +0x68 DEF (u8),
# +0x69 MDEF. Match vs guide: Head 39/39, Body 93/93, Foot 47/47, Arm 47/52
# (arm's 5 misses are range-specific ATK, a separate field). Element resists +
# procs (counter/status/potch/auto-heal/element ±N) are NOT stored numerically in
# the record (confirmed by full correlation + record diffs) -> shown read-only
# from the summary, never written. See reference_s5_armor_tables memory.
ARMOR_STRIDE = 0x94
ARMOR_SUMMARY_OFF, ARMOR_SUMMARY_LEN = 0x08, 0x58
ARMOR_TABLES = {                       # slot -> (base, count)
    "head":      (0x495D88, 32),
    "body":      (0x48C9A8, 71),
    "arm":       (0x4942A8, 37),
    "foot":      (0x4974C8, 30),
    # Accessories (summary prefix 補助 "support"): same 0x94 record struct.
    "accessory": (0x4AC6D8, 49),
}
ARMOR_SLOT_LABEL = {"head": "Head", "body": "Body", "arm": "Arm", "foot": "Foot",
                    "accessory": "Accessory"}
# (label, off, width, signed) — VERIFIED-editable fields. The stat block 0x63..0x6B
# was pinned byte-for-byte via the single-stat accessory rings (Physical=HP@0x63,
# Attack@0x64, Technique@0x65, Magic@0x66, Guard=DEF@0x68, Psycho=MDEF@0x69,
# Speed@0x6A, Luck@0x6B; Evade@0x67 from armor). DEF is unsigned u8; stats are
# signed s8 (armor can carry Speed penalties). PROC effects each have a dedicated
# byte too (found via badge diffs): Auto-heal@0x6C, HP-drain@0x6D, Status-resist@0x72,
# Potch@0x74, Counter@0x75 — all % (unsigned). Writing one adds that effect to the
# item (the game applies it; the +0x08 summary TEXT is separate and won't update).
# Element resist/attribute (Fire/Water ±N) is the only effect NOT a clean byte
# (element records differ only by name/flavor) -> stays read-only from the summary.
# Element order for the per-element ATK/DEF blocks (matches ELEMENT_NAMES / affinities).
ARMOR_ELEMENTS = ["Sun", "Fire", "Lightning", "Wind", "Water", "Earth", "Star",
                  "Sound", "Holy", "Dark", "Slash", "Thrust", "Punch", "Shoot"]
# Element Atk (our 0x41..0x4E) + Def (0x4F..0x5C) are 14 signed bytes each, VERIFIED:
# each nonzero byte matches the piece's own summary text exactly (Sun Helm "Sun ATK+1"
# -> Sun ATK=1; Flame Helmet "Fire DEF+1" -> Fire DEF=1; "+1" with no ATK/DEF word sets
# both). Recovered via a community-documented Head Gear module (module offset - 8 = our offset).
# Type (base-3): armor weight class 1=Light/2=Medium/3=Heavy (accessories use a different
# set 1=Cape..6=Ring). SPD penalty (base-2): unsigned speed cost, scales with weight class.
ARMOR_FIELDS = [
    ("Buy price",  0x00, 4, False),
    ("Sell price", 0x04, 4, False),
    ("DEF",        0x68, 1, False),
    ("Type",       -3,   1, False),
    ("SPD penalty", -2,  1, False),
    ("HP",         0x63, 1, True),
    ("Attack",     0x64, 1, True),
    ("Technique",  0x65, 1, True),
    ("Magic",      0x66, 1, True),
    ("Evasion",    0x67, 1, True),
    ("MDEF",       0x69, 1, True),
    ("Speed",      0x6A, 1, True),
    ("Luck",       0x6B, 1, True),
    ("Auto-heal %",     0x6C, 1, False),
    ("HP drain %",      0x6D, 1, False),
    # Range ATK/ACC @0x5D..0x62 + Critical% @0x70 VERIFIED vs the pieces' own summaries
    # (bangle L-ATK ladder 2/4/6/8, Power Gloves S&M-ATK 3/3, bracer L-ACC 5/8/14/20,
    # Engraved Gauntlets Critical +5%). These were the 5 "arm misses" in the original
    # guide match — they were range-specific ATK all along.
    ("Short-range ATK", 0x5D, 1, False),
    ("Mid-range ATK",   0x5E, 1, False),
    ("Long-range ATK",  0x5F, 1, False),
    ("Short-range ACC", 0x60, 1, False),
    ("Mid-range ACC",   0x61, 1, False),
    ("Long-range ACC",  0x62, 1, False),
    ("Critical %",      0x70, 1, False),
    ("Status resist %", 0x72, 1, False),
    ("Potch %",         0x74, 1, False),
    ("Counter %",       0x75, 1, False),
] + [(f"{e} ATK", 0x41 + i, 1, True) for i, e in enumerate(ARMOR_ELEMENTS)] \
  + [(f"{e} DEF", 0x4F + i, 1, True) for i, e in enumerate(ARMOR_ELEMENTS)]

# JP stat-summary -> readable EN (display only). Longest tokens first so e.g.
# "の防御" is consumed before "防", and compound effect words before their parts.
_ARMOR_TR = [
    ("バッドステータス阻害", "Status resist "), ("戦闘後のポッチ", "Potch after battle "),
    ("カウンター発生率", "Counter rate "), ("クリティカル発生率", "Critical rate "),
    ("ＨＰ自動回復", "Auto-heal"), ("ＨＰドレイン", "HP drain"), ("ＨＰ減少", "HP loss"),
    ("全能力値", "All stats "),
    ("突きの防御", "Thrust DEF"), ("の防御", " DEF"), ("の攻撃", " ATK"),
    ("レンジ", "-range "), ("命中", "Accuracy "), ("技術", "TECH "),
    ("突き", "Thrust "), ("運", "Luck "), ("発生率", " rate "),
    ("直防", "DEF"), ("魔防", "MDEF"), ("魔力", "MAG"), ("攻撃力", "ATK"),
    ("回避", "Evade "), ("ＨＰ", "HP"), ("速", "SPD "),
    ("太陽", "Sun"), ("火", "Fire"), ("水", "Water"), ("雷", "Lightning"),
    ("土", "Earth"), ("風", "Wind"), ("闇", "Dark"), ("聖", "Holy"),
    ("阻害", "resist "), ("と", "&"), ("　", " "), ("胴", ""), ("頭", ""),
    ("腕", ""), ("脚", ""), ("補助", ""),
]
def armor_summary_en(jp):
    """Translate the ISO stat-summary to a short readable effect label."""
    import unicodedata, re
    s = unicodedata.normalize("NFKC", jp or "")
    for a, b in _ARMOR_TR:
        s = s.replace(unicodedata.normalize("NFKC", a), b)
    return re.sub(r"\s+", " ", s).strip()

# ---- Equipment SET completion bonuses (VERIFIED 2026-08-24 by ELF reverse-engineering).
# Set bonuses are NOT table data — they are inline code — but every value that matters is a
# byte/halfword immediate, so they are fully editable. Three layers:
#   1) detector @file 0x281AD0 (vaddr 0x2D41D0): reads the live char struct's equipped ids
#      (+68 head, +69 body, +70 arm, +71 foot; accessory list at +72 = 4 entries x 8 bytes,
#      entry+0 = slot type (5=accessory), entry+1 = item id) and compares each against an
#      inline `addiu rX,$zero,<EQUIP ID>` immediate -> returns a set index in $v0.
#   2) dispatcher @vaddr 0x2D4660: bounds-checks the index < SET_COUNT then jumps through
#      SET_JT_OFF (a plain u32 jump table -> repointing a set's effect is a pure data edit).
#   3) per-set handlers: read-modify-write on the live char struct with an immediate
#      magnitude (e.g. Destiny +50, Sun +5 x8 stats), or a `li`+store (Fish).
# PROOF the model is right: Fish = set 1, whose handler stores 6 into char+256; the affinity
# scale is AFFINITY_GRADES = [None,E,D,C,B,A,S] so 6 == S — exactly the documented
# "Fish full set raises Water affinity to S". NTSC-U only (PAL offsets unmapped).
VADDR_DELTA     = 0x52700          # vaddr = file offset + this
SET_DETECT_OFF  = 0x281AD0         # file offset of the set detector
SET_DETECT_LEN  = 0x490
SET_EXIT_VADDR  = 0x2D4650         # shared "return the set index" exit
SET_JT_OFF      = 0x687B00         # file offset of the 10-entry u32 jump table
SET_COUNT       = 10
SET_NOOP_VADDR  = 0x2D47C4         # handler that applies nothing
SET_STRUCT_SLOT = {68: "head", 69: "body", 70: "arm", 71: "foot"}
SET_ACC_ID_OFF  = 73               # accessory list entry+1 = item id
SET_SLOT_ORDER  = ("head", "body", "arm", "foot", "accessory")
# Live-struct offsets the handlers touch — LABELS VERIFIED against the Suikosource
# "Armor Sets" guide (every set's decoded effect matched the guide exactly):
#   Classic  HP+10 / ATK+5        -> +20 = HP, +40 = ATK
#   Destiny  HP+50                -> +20 = HP (confirmed twice)
#   Guardian MDEF+10              -> +46 = MDEF
#   Samurai  Crit+10 / DblCrit+10 -> +304 = Critical %, +305 = Double-critical %
#   Fish     "Water affinity is S"-> +256 = Water affinity (value 6 == S)
#   Sun      "Prince only: all stats +5" -> the +40..+56 halfword block is the stat array
#   Windspun "in other words, SPD+34"    -> the float handler's 34.0f, +54 = SPD
SET_FIELD_HINT  = {20: "HP", 40: "ATK", 46: "MDEF", 54: "SPD",
                   256: "Water affinity", 304: "Critical %", 305: "Double critical %",
                   42: "stat", 44: "stat", 48: "stat", 50: "stat", 52: "stat", 56: "stat"}
# ---- CUSTOM set-bonus handlers -------------------------------------------------
# The stock handlers are packed back-to-back with no slack, so a custom bonus is
# assembled into a free 80-byte gap and the jump table is pointed at it.
# The gap at vaddr 0x4484B0 is VERIFIED unreferenced: it follows a function's tail
# `j`+delay-slot epilogue, nothing branches or jumps into it, and its address is never
# materialised anywhere in the ELF. 80 bytes = 20 instructions.
SET_CUSTOM_VADDR = 0x4484B0
SET_CUSTOM_LEN   = 80
SET_RETURN_VADDR = 0x2D47C4      # the handlers' shared epilogue (restores $ra, returns)
SET_STRUCT_REG   = 16            # $s0 holds the live character struct in every handler
# Effect targets in the LIVE character struct. verified=True means the offset was pinned
# by matching a documented set bonus from the Suikosource guide; the rest are inferred
# from the stat/affinity blocks those verified hits sit inside.
# (label, char-struct offset, width, verified, kind)
#   kind "num"   -> a quantity; can be added to or forced
#   kind "grade" -> an AFFINITY_GRADES tier (0..6, 6 == rank S); only ever forced,
#                   since "adding" to a tier is meaningless and would overflow the scale
SET_EFFECT_TARGETS = [
    ("HP",                 20,  "h", True,  "num"),
    ("Attack",             40,  "h", True,  "num"),
    ("Stat @+42",          42,  "h", False, "num"),
    ("Stat @+44",          44,  "h", False, "num"),
    ("Magic Defense",      46,  "h", True,  "num"),
    ("Stat @+48",          48,  "h", False, "num"),
    ("Stat @+50",          50,  "h", False, "num"),
    ("Stat @+52",          52,  "h", False, "num"),
    ("Speed",              54,  "h", True,  "num"),
    ("Stat @+56",          56,  "h", False, "num"),
    ("Critical %",         304, "b", True,  "num"),
    ("Double critical %",  305, "b", True,  "num"),
]
# Element affinity block: only Water is proven (Fish writes 6 == rank S to +256); the
# others are inferred by the documented element order around it.
SET_AFFINITY_BASE = 252
SET_AFFINITY_ELEMENTS = ARMOR_ELEMENTS      # Sun, Fire, Lightning, Wind, Water, ...
for _i, _e in enumerate(SET_AFFINITY_ELEMENTS):
    SET_EFFECT_TARGETS.append(("%s affinity" % _e, SET_AFFINITY_BASE + _i, "b",
                               _e == "Water", "grade"))

# Documented set bonuses (Suikosource) — shown alongside the decoded values so users can
# see intent vs. what the bytes actually do. Keyed by set index.
SET_DOC_BONUS = {
    1: "Water affinity is S",
    2: "Potch from battle is doubled (applied on the battle-reward path, not here)",
    3: "Recover 10% HP each turn (applied elsewhere, not a stat write)",
    4: "HP +50, plus a 20% chance of revival",
    5: "MDEF +10",
    6: "HP +10, ATK +5",
    7: "Critical Hit +10%, Double Critical +10%",
    8: "Equipment SPD penalty canceled, SPD +20 (i.e. SPD +34)",
    9: "Prince only: all stats +5",
}

# ---- MP growth thresholds (VERIFIED @0x4986C0; this is the old "LADDER" table, now
# identified via a community-documented MP Growth module + byte-match). 4 groups = magic Levels
# 1-4; each group is 9 u16 MP-cost thresholds (a 10th u16 = 999 terminator/pad). Content
# confirmed: Lv1 [0,20,40,75,100,130,165,225,290], Lv4 [180,270,450,520,...]. Editing
# tunes MP requirements but can't raise the 9/9/7/5 casts-per-level cap.
MP_BASE, MP_STRIDE, MP_GROUPS = 0x4986C0, 20, 4
MP_GROUP_LABELS = ["Magic Lv1", "Magic Lv2", "Magic Lv3", "Magic Lv4"]
MP_FIELD_LABELS = ["1st MP", "2nd MP", "3rd MP", "4th MP", "5th MP (Lv4 cap)",
                   "6th MP", "7th MP (Lv3 cap)", "8th MP", "9th MP"]

# ---- Unite attacks (VERIFIED @0x4D3420; 49 records, 49/49 match the Unites guide).
# Located by disassembling the ELF name-lookup dispatcher (lui/addiu forming 0x525B21 ->
# file 0x4D3421). PACKED VARIABLE-LENGTH records, parsed at runtime:
#   [Shift-JIS unite name \0] [SJIS effect desc \0 (1-2 lines)] [count u8 (2..6)]
#   [count x participant char-id u8]  ... next record.
# Participant ids use the same character-id space as the stats tables (DoReMi quints are
# 129-133, beyond the roster json). The count byte is NOT editable (records are packed —
# changing it would shift every later record); participants ARE editable in place.
# English names/effects (guide-sourced) in s5_unite_names.json, index = record order.
UNITE_BASE, UNITE_COUNT, UNITE_SCAN_END = 0x4D3420, 49, 0x4D6000
def _unite_names():
    try: return res_json("s5_unite_names.json")
    except Exception: return []
UNITE_NAMES = _unite_names()
# DoReMi quintuplet ids (not in s5_characters.json roster).
UNITE_EXTRA_CHARS = {129: "ReMiFa", 130: "MiFaSo", 131: "FaSoLa", 132: "SoLaTi", 133: "LaTiDo"}

# ---- Skill effect magnitudes (VERIFIED @0x4AEB1C, stride 36, count 165; byte-identical
# to a community-documented extract). Per skill: 7 u16 values = the skill's magnitude at rank
# E/D/C/B/A/S/SS, at offsets 0,2,4,6,8,10,12. Verified content: "Attack +" 5..40,
# "Stamina (% HP)" 105..130, "Karmic Effect" starts at C. GLOBAL table (indexed by skill
# id, shared by all units). Names from skills.txt (s5_skilleffect_names.json, 165).
SKILLFX_BASE, SKILLFX_STRIDE, SKILLFX_COUNT = 0x4AEB1C, 36, 165
SKILLFX_RANKS = ["E", "D", "C", "B", "A", "S", "SS"]
def _skillfx_names():
    try: return res_json("s5_skilleffect_names.json")
    except Exception: return []
SKILLFX_NAMES = _skillfx_names()

# Fixed cost/threshold ladder (purpose unconfirmed).
LADDER_OFF = 0x4986C0
LADDER_COUNT = 40
LADDER_EXTRA_OFF = 0x31A6BC

RANK_HELP = "skill rank byte: 0..7 (higher = better; 07 ~ SS)"

# Per-section help, drawn from verified in-game labels.
SECTION_HELP = {
    "stats":      "Character starting stats (HP, Attack, Technique, Magic, Evasion, PDF, MDF, Speed, Luck) plus each stat's per-level Growth. Base stats apply to a NEW GAME; growth affects level-ups. Verified byte-for-byte in-game (Dinn/Lance match exactly).",
    "weapon growth": "Attack power of this character's weapon at each sharpen level (1-16), ascending. Verified against the game's weapon-growth data.",
    "starting equipment": "Armor equipped at new-game start — Head / Body / Arm (gloves) / Feet. All four are dropdowns of the game's armor ids (0 = Nothing). Verified: Richard starts in a full knight set.",
    "starting items": "Up to four items/accessories a unit starts holding (stored as name pointers). Choices are the closed set of items the game actually assigns; 'Nothing' clears a slot. Verified: Prince = Lightning Amulet, Richard = Sun Badge + Jewel Necklace.",
    "runes":      "Runes equipped at the start of a new game (head / right / left slots). Rune id space is unconfirmed — labels are best-effort.",
    "gear":       "Armor pieces (Head / Body / Arm / Foot). Fully editable, verified vs the Armor List guide: DEF, buy/sell price, weight Type (1=Light/2=Medium/3=Heavy) + SPD penalty, stat bonuses (HP/Attack/Technique/Magic/Evasion/MDEF/Speed/Luck), proc effects (auto-heal/HP-drain/status-resist/potch/counter %), and per-element ATK & DEF for all 14 elements. The read-only summary below is just the game's own description text.",
    "affinities": "Elemental affinity grades for this character — one per element (None / E / D / C / B / A / S). Higher = better at casting/resisting that element. Verified in-game (Prince = Sun A; Zerase = Fire/Star/Dark A). Applies to a NEW GAME.",
    "equipable skills": "The MAX rank this character can equip each skill at (None / E / D / C / B / A / S / SS). This is the character's skill cap, not their current level. Verified vs the game data (Prince caps Attack/Technique/MDef at S; Zerase caps Magic/Incantation at S). Applies to a NEW GAME.",
}
GLOBAL_HELP = "Edits apply to a NEW GAME. Do NOT use emulator save states — use in-game save files."

# ---- Enemy / unit editor -----------------------------------------------------
# Enemies live at 0x49F0DC / stride 0x7C, indexed by a unit id. FULL record layout
# recovered from a community-documented Enemy module (its base 0x49F157 = ours + 0x7B, so
# module offset - 1 = our offset, module enemy i = our id i+1) and VERIFIED on real
# records: Nariqua (our id 4) Lv45 HP1800 Potch2500 SP135, 20%-drop cat7/item0x10 =
# Rune Pieces / Drain Piece (our original ground truth); Holly Boy (id 1) Lv10 HP80
# Potch30 SP10. This CORRECTS the old stat labels (order is HP, ATK, TECH, ACC, MAG,
# EVA, PDF, MDF, SPD, LUK — the previous Attack/Magic/... order was wrong) and
# identifies the old hidden +0x16 as the POTCH reward (+0x18 = skill-point reward).
# Each drop slot is 8 bytes: category u8 + item u8 (+6 unknown); read as one u16 LE
# the value is category | item<<8 — decoded via s5_drop_items.json (Item Drop Table).
# +0x28 = 100%-drop flag (0x0F = drops whole loot table every time).
# Enemy AFFINITIES @+0x1A..: 14 grade bytes, scale 0=E..5=S (ENEMY_AFFINITY_GRADES —
# distinct from the character scale which starts at 0=None).
ENEMY_BASE, ENEMY_STRIDE, ENEMY_MAX = 0x49F0DC, 0x7C, 584
ENEMY_AFFINITY_GRADES = ["E", "D", "C", "B", "A", "S"]
def _enemy_fields():
    f = [("Level", 0x01, 1, "num")]
    stats = ["HP", "Attack", "Technique", "Accuracy", "Magic",
             "Evasion", "PDF", "MDF", "Speed", "Luck"]
    f += [(lbl, 0x02 + i*2, 2, "num") for i, lbl in enumerate(stats)]
    f += [("Potch reward", 0x16, 2, "num"), ("Skill Pts reward", 0x18, 2, "num")]
    f += [(f"{e} affinity", 0x1A + i, 1, "egrade") for i, e in enumerate(ARMOR_ELEMENTS)]
    f.append(("100% drop flag", 0x28, 1, "num"))
    for pct, off in [("40%", 0x2c), ("20%", 0x34), ("10%", 0x3c), ("5%", 0x44), ("1%", 0x4c)]:
        f.append((f"Drop {pct}", off, 2, "drop"))
    return f
ENEMY_FIELDS = _enemy_fields()

def _drop_items():
    try: return res_json("s5_drop_items.json")
    except Exception: return {"categories": {}, "items": {}}
DROP_TABLE = _drop_items()

def load_characters():
    """[{id, name}] playable list; falls back to empty if json missing."""
    try:
        return res_json("s5_characters.json")
    except Exception:
        return []


# ---- Region awareness (NTSC-U default; PAL bases VERIFIED via extract byte-match) ----
# The game DATA is byte-identical across regions (same in-record offsets/strides/counts);
# only the absolute table BASES differ, plus PAL rune-price stride 76->80. So region
# support = rebind the 15 verified table bases. detect via the serial @0x828BD.
REGION = "ntsc-u"
SERIALS = {"ntsc-u": b"SLUS_212.91", "pal": b"SLES_540.87"}
REGION_NAMES = {"ntsc-u": "NTSC-U (SLUS-21291)", "pal": "PAL (SLES-54087)"}

# TABLES keys skipped in PAL read_character (their PAL offsets aren't mapped). Empty
# after Phase 3 — held-item pointers are mapped via s5_held_items_pal.json.
GATED_IN_PAL = []

# PAL bases (SLES_540.87) — see reference_s5_pal_map. NTSC bases are captured live below.
_PAL = {
    "stats": 0x48FB60, "affinities": 0x490720, "equipable skills": 0x4B80D1,
    "weapon growth": 0x49D9B0, "spell": 0x4FC950, "runeprice": 0x4F0B20, "runeprice_stride": 80,
    "healprice": 0x4D2980, "mp": 0x49D8B0, "skillfx": 0x4B44BC, "enemy": 0x4A4347,
    "armor_head": 0x49AF70, "armor_body": 0x491B90, "armor_arm": 0x499490,
    "armor_foot": 0x49C6B0, "armor_accessory": 0x4B2070,
    # Phase 2 (byte-match verified): rune->spell grant, shop item prices, starting-equipment armor.
    "runegrant": 0x4FAF02, "price": 0x49952C, "starting equipment": 0x498302,
    # Phase 3: starting held-items share the starting-equipment record base.
    "starting items": 0x498302,
    # Phase 3: unite table (packed; byte-identical layout, Δ+0x59B0). scan-end keeps the
    # same window span as NTSC (0x4D6000-0x4D3420 = 0x2BE0).
    "unite": 0x4D8DD0, "unite_end": 0x4D8DD0 + 0x2BE0,
}

def _snapshot_bases():
    return {
        "stats": TABLES["stats"][0], "affinities": TABLES["affinities"][0],
        "equipable skills": TABLES["equipable skills"][0], "weapon growth": TABLES["weapon growth"][0],
        "starting equipment": TABLES["starting equipment"][0], "starting items": TABLES["starting items"][0],
        "spell": SPELL_BASE, "runeprice": RUNEPRICE_BASE, "runeprice_stride": RUNEPRICE_STRIDE,
        "healprice": HEALPRICE_BASE, "mp": MP_BASE, "skillfx": SKILLFX_BASE, "enemy": ENEMY_BASE,
        "runegrant": RUNE_GRANT_BASE, "price": PRICE_BASE,
        "unite": UNITE_BASE, "unite_end": UNITE_SCAN_END,
        "armor_head": ARMOR_TABLES["head"][0], "armor_body": ARMOR_TABLES["body"][0],
        "armor_arm": ARMOR_TABLES["arm"][0], "armor_foot": ARMOR_TABLES["foot"][0],
        "armor_accessory": ARMOR_TABLES["accessory"][0],
    }

_NTSC = _snapshot_bases()

def set_region(region):
    """Rebind all region-variable table bases in place. No-op if unknown."""
    global REGION, SPELL_BASE, RUNEPRICE_BASE, RUNEPRICE_STRIDE, HEALPRICE_BASE
    global MP_BASE, SKILLFX_BASE, ENEMY_BASE, RUNE_GRANT_BASE, PRICE_BASE
    global UNITE_BASE, UNITE_SCAN_END
    b = {"ntsc-u": _NTSC, "pal": _PAL}.get(region)
    if not b:
        return REGION
    REGION = region
    for key in ("stats", "affinities", "equipable skills", "weapon growth",
                "starting equipment", "starting items"):
        base, stride, fields = TABLES[key]
        TABLES[key] = (b[key], stride, fields)
    for slot in ("head", "body", "arm", "foot", "accessory"):
        base, count = ARMOR_TABLES[slot]
        ARMOR_TABLES[slot] = (b["armor_" + slot], count)
    SPELL_BASE = b["spell"]
    RUNEPRICE_BASE = b["runeprice"]; RUNEPRICE_STRIDE = b["runeprice_stride"]
    HEALPRICE_BASE = b["healprice"]
    MP_BASE = b["mp"]; SKILLFX_BASE = b["skillfx"]; ENEMY_BASE = b["enemy"]
    RUNE_GRANT_BASE = b["runegrant"]; PRICE_BASE = b["price"]
    UNITE_BASE = b["unite"]; UNITE_SCAN_END = b["unite_end"]
    return REGION
