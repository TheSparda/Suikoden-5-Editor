#!/usr/bin/env python3
"""Code-patch toggle framework: a registry of named on/off code patches,
each declaring its patch sites, with the same safety rules for all of
them (the patterns are the Suikoden III editor's Test tab audit and the
Suikoden IV editor's ISO toggles):

  - Sites are located by the toggle's own finder on the actual disc, and
    a signature-located site must be UNIQUE: the Dawn Rune finder
    refuses an ambiguous disc (anything but exactly one hit).
  - Before any write, every span of every site is classified: stock,
    patched, or FOREIGN (bytes the toggle does not recognise). A foreign
    span refuses the whole toggle before a single byte is written, so a
    disc patched by something else is never half-overwritten.
  - After writing, every span is re-read and verified against the
    intended bytes.
  - Restore is exact. Self-restoring toggles (the Dawn Rune: the stock
    word is a constant) restore from the registry alone. Gate toggles
    (the rune always-on gates) have per-site stock bytes (branch words
    differ per call site), so apply() returns the originals it displaced
    and restore requires them back; the web editor keeps them
    in its mod journal, the CLI prints them as JSON.

Registered toggles are honest about their in-game effect. The Dawn Rune
unlock is proven (its mechanism is documented in s5fields). Five rune
always-on toggles sit in the "Rune perks: always on" group. Three of
them (Fortune, Prosperity, Godspeed) are registered
with effect "not-working": the lab proved their gates feed only a
script-VM condition resolver that battle code never consumes
(ps2-re games/suikoden5/PASSIVES_CATHARI.md), and the reward-path trace
(ps2-re games/suikoden5/BATLE_REWARDS.md) found no statically provable
patch point that would make them work. They are in the registry so a
disc already patched with them can be verified and restored exactly,
not as a recommendation to apply them. The other two are the encounter
runes, Champion's and Great Firefly, with effect "code-verified":
their gate sites and mechanism were checked against the game code,
but the encounter effects themselves have not been tested in game.
Until v1.26.0 these five were also offered by a separate Passives
view writing the same gates through its own engine path; the registry
is now the only surface, so the two can never disagree.

The five CT-survey toggles (training SP x2, train-to-SS, forge x2) are
the community cheat table's code patches with their sites located by
signature and their semantics verified by disassembly on both regions;
they carry effect "code-verified" because no in-game A/B run has
exercised them yet (ps2-re games/suikoden5/CT_FEATURES_BUILD.md).

The gear-requirements-off toggle (issue #49) removes the equip gates
of the only two Type 6 body armors, the Corruption Shell and the
Saint's Cloak: four slti immediates in the shared can-equip predicate.
Its sites sit at the traced offsets for the disc's region and are
verified against their stock words (s5patch.gear_requirement_sites,
shared with the Gear view's threshold fields, which edit the same
immediates one by one). It carries effect "proven": the lab exercised
the stock refusals and passes in game at the exact boundaries, and a
disable ISO carrying exactly the toggle's patched immediates equipped
both pieces on a character the stock game refuses
(ps2-re games/suikoden5/GEAR_REQUIREMENTS.md).

The Godspeed dash toggle (issue #18) is separate from the not-working
godspeed-always entry above: it patches the field motor's dash-tier
gate, the mechanism the lab actually traced and measured
(ps2-re games/suikoden5/DASH_GATE_CODE.md), and carries effect
"code-verified" until an in-game A/B run exercises the exact patch.
Its two companion multiplier fields live in s5patch (dash_speed and
dash_anim) and render beside it in the Dash tuning group.
"""
import struct

import s5fields as F
import s5patch as P

_NOP = b"\x00\x00\x00\x00"


# ---------------------------------------------------------------------------
# Site finders. A site is {"label", "spans": [{"off", "stock", "patched"}]}
# where stock is the expected pristine bytes, or None when the stock shape
# is validated structurally instead of bytewise (gate branch words differ
# per call site); a span with stock None carries "stockOk": the finder has
# already validated the shape, and "cur": the bytes currently on disc.
# ---------------------------------------------------------------------------

_DAWN_ANCHOR_RE = None


def _dawn_locate(iso):
    """Locate the Dawn site by its invariant prefix alone (the clamp word
    + the jal to the setter), so a site word patched by something else
    still locates and can be classified foreign, instead of vanishing
    the way s5patch._dawn_site's full signature (which includes the
    variable word) makes it do. The prefix must hit exactly once."""
    global _DAWN_ANCHOR_RE
    if _DAWN_ANCHOR_RE is None:
        import re
        anchor = re.escape(struct.pack("<I", F.DAWN_ANCHOR_WORD))
        jal = b"..." + re.escape(b"\x0c")
        _DAWN_ANCHOR_RE = re.compile(anchor + jal, re.S)
    lo, hi = F.DAWN_SCAN_LO, F.DAWN_SCAN_HI
    blob = iso.rd(lo, hi - lo)
    hits = [m.start() for m in _DAWN_ANCHOR_RE.finditer(blob)]
    if len(hits) != 1:
        return None
    return lo + hits[0] + 8


def _dawn_sites(iso):
    off = _dawn_locate(iso)
    if off is None:
        return []
    s = {"off": off}
    stock = struct.pack("<I", F.DAWN_STOCK_WORD)
    patched = struct.pack("<I", F.DAWN_FORCE_WORD | F.DAWN_MAX)
    cur = iso.rd(s["off"], 4)
    w = struct.unpack("<I", cur)[0]
    # Any forced count (FORCE|1..4) is the on state, not just this
    # toggle's own count of 4: the Passives tab's finer control must not
    # read as foreign bytes.
    shape = ("off" if cur == stock else
             "on" if (w & 0xFFFF0000) == F.DAWN_FORCE_WORD else "foreign")
    return [{"label": "Dawn Rune spell-count word @0x%X" % s["off"],
             "shape": shape,
             "spans": [{"off": s["off"], "stock": stock, "patched": patched,
                        "cur": cur}]}]


def _gate_scan(iso, rune_id):
    """Canonical, forced and MALFORMED gates for one rune, mirroring
    s5patch._rune_gate_sites' scan of the resolver window exactly, except
    that gates whose shape is neither canonical nor forced are reported
    (as foreign) instead of skipped: a half-patched gate is precisely
    what the framework exists to catch."""
    lo, hi = F.RUNE_GATE_LO, F.RUNE_GATE_HI
    blob = iso.rd(lo - F.VADDR_DELTA, hi - lo)
    cnt = len(blob) // 4
    ws = struct.unpack("<%dI" % cnt, blob[:cnt * 4])
    target = (F.RUNE_HAS_FN & 0x0FFFFFFF) >> 2
    out = []
    for i, w in enumerate(ws):
        if (w >> 26) != 3 or (w & 0x3FFFFFF) != target:
            continue
        rid = None
        for k in range(max(0, i - 8), i + 2):
            u = ws[k]
            if (u >> 26) == 0x09 and ((u >> 21) & 31) == 0 and ((u >> 16) & 31) == 5:
                rid = u & 0xFFFF
        if rid != rune_id or i + 8 >= cnt:
            continue
        b1, andi, b2 = ws[i + 2], ws[i + 6], ws[i + 7]
        ok1 = (b1 >> 26) == 4 and ((b1 >> 21) & 31) == 2 and ((b1 >> 16) & 31) == 0
        ok2 = ((andi >> 26) == 0x0C and (andi & 0xFFFF) == 2
               and (b2 >> 26) == 4 and ((b2 >> 16) & 31) == 0)
        forced = b1 == 0 and b2 == 0 and (andi >> 26) == 0x0C and (andi & 0xFFFF) == 2
        shape = "foreign"
        if forced:
            shape = "on"
        elif ok1 and ok2:
            t1 = (lo + (i + 2) * 4) + 4 + P._s16(b1 & 0xFFFF) * 4
            t2 = (lo + (i + 7) * 4) + 4 + P._s16(b2 & 0xFFFF) * 4
            if t1 == t2:
                shape = "off"
        off1 = (lo + (i + 2) * 4) - F.VADDR_DELTA
        off2 = (lo + (i + 7) * 4) - F.VADDR_DELTA
        out.append({"label": "gate @0x%X" % (lo + i * 4), "shape": shape,
                    "spans": [
                        {"off": off1, "stock": None, "patched": _NOP,
                         "cur": struct.pack("<I", b1)},
                        {"off": off2, "stock": None, "patched": _NOP,
                         "cur": struct.pack("<I", b2)}]})
    return out


def _gate_sites(rune_id):
    def find(iso):
        return _gate_scan(iso, rune_id)
    return find


# ---------------------------------------------------------------------------
# CT-survey toggles (2026-11-18): the five code patches the community cheat
# table applies at runtime, located statically in the ELF text window (the
# window the Dawn finder scans) and verified instruction by instruction
# against the pristine discs of BOTH regions (ps2-re CT_FEATURES_BUILD.md).
# Every stock word is a constant, so all five are self-restoring. Their
# semantics are proven by disassembly; none has had an in-game A/B run yet,
# so they carry effect "code-verified", below the Dawn toggle's "proven".
# ---------------------------------------------------------------------------

def _scan_blob(iso):
    return iso.rd(F.DAWN_SCAN_LO, F.DAWN_SCAN_HI - F.DAWN_SCAN_LO)


def _bytes_hits(blob, pat):
    out, i = [], blob.find(pat)
    while i != -1:
        out.append(i)
        i = blob.find(pat, i + 1)
    return out


def _one_site(iso, hits, rel, stock, patched, label):
    """Build the single site for a signature hit list; [] unless exactly
    one hit (the framework's uniqueness rule for signature-located sites)."""
    if len(hits) != 1:
        return []
    off = F.DAWN_SCAN_LO + hits[0] + rel
    return [{"label": label % off,
             "spans": [{"off": off, "stock": struct.pack("<I", stock),
                        "patched": struct.pack("<I", patched),
                        "cur": iso.rd(off, 4)}]}]


# spendSP (the party-SP payment function training uses) has an identical
# 10-word body in both regions; the two training-SP toggles patch two
# different words of it: the subu that subtracts the cost from the pool,
# and the sw that stores the pool back. The locator anchors on the
# invariant words only (the two patched words are left to the span
# classification), so a patched disc still locates, like the Dawn site.
_SPENDSP_PREFIX = struct.pack(
    "<6I", 0x8C820004, 0x0045082B, 0x10200003, 0x00000000, 0x10000004,
    0x0000102D)


def _spendsp_hits(iso):
    blob = _scan_blob(iso)
    hits = []
    for i in _bytes_hits(blob, _SPENDSP_PREFIX):
        if i % 4 or i + 40 > len(blob):
            continue
        w7 = struct.unpack_from("<I", blob, i + 0x1C)[0]
        w9 = struct.unpack_from("<I", blob, i + 0x24)[0]
        if w7 == 0x24020001 and w9 == 0x03E00008:
            hits.append(i)
    return hits


def _training_sp_free_sites(iso):
    return _one_site(iso, _spendsp_hits(iso), 0x20,
                     0xAC830004, 0x00000000,
                     "spendSP pool store @0x%X")


def _training_sp_gain_sites(iso):
    return _one_site(iso, _spendsp_hits(iso), 0x18,
                     0x00451823, 0x00451821,
                     "spendSP pool subtract @0x%X")


def _train_ss_sites(iso):
    """The training routine's rank increment: lbu $v0, 0x68($v1) (the rank
    byte, just below the rank array at record +0x69 for 1-based skill
    ids), addiu $v0, $v0, 1, a jal to the stat recompute, and the store
    sb $v0, 0x68($v1) in its delay slot. The jal target differs per
    region, so the pattern is structural: first and fourth words exact,
    third any jal. The increment word itself is not part of the anchor,
    so a patched or foreign word still locates and the span
    classification decides state."""
    blob = _scan_blob(iso)
    hits = []
    for i in _bytes_hits(blob, struct.pack("<I", 0x90620068)):
        if i % 4 or i + 16 > len(blob):
            continue
        w1, w2, w3 = struct.unpack_from("<3I", blob, i + 4)
        if (w2 >> 26) == 0x03 and w3 == 0xA0620068:
            hits.append(i)
    return _one_site(iso, hits, 4, 0x24420001, 0x24020007,
                     "training rank increment @0x%X")


def _forge_once_sites(iso):
    """The forge helper reads the weapon level byte (record +0x3B), adds
    1 and stores it back. Anchor: the lbu, with the sb and the jr $ra
    exact at +8/+12; the increment word at +4 is the span, not part of
    the anchor, so patched and foreign forms still locate."""
    blob = _scan_blob(iso)
    hits = []
    for i in _bytes_hits(blob, struct.pack("<I", 0x9083003B)):
        if i % 4 or i + 16 > len(blob):
            continue
        w1, w2, w3 = struct.unpack_from("<3I", blob, i + 4)
        if w2 == 0xA083003B and w3 == 0x03E00008:
            hits.append(i)
    return _one_site(iso, hits, 4, 0x24630001, 0x2403000F,
                     "forge level increment @0x%X")


# The forge menu handler moves its cap argument into $s3 in its prologue
# (daddu $s3, $a1, $zero between the saved-register stores) and compares
# each weapon's level against $s3 (slt $at, $v0, $s3 after lw $v0, 0x10).
# The prologue shape occurs twice per disc; only the forge handler is
# followed by the forgeable check within 0x900 bytes. The cap word
# itself is the span, not part of the anchor, so patched and foreign
# forms still locate.
_SMITH_PROLOGUE_PREFIX = struct.pack("<3I", 0x7FB40040, 0x7FB30030,
                                     0x7FB20020)
_FORGEABLE_CHECK = struct.pack("<3I", 0x8C420010, 0x0053082A, 0x10200005)


def _smith_cap_sites(iso):
    blob = _scan_blob(iso)
    hits = []
    for start in _bytes_hits(blob, _SMITH_PROLOGUE_PREFIX):
        if start % 4 or start + 24 > len(blob):
            continue
        s1, s0 = struct.unpack_from("<2I", blob, start + 16)
        if (s1, s0) != (0x7FB10010, 0x7FB00000):
            continue
        daddu = start + 12
        if blob[daddu:daddu + 0x900].find(_FORGEABLE_CHECK) != -1:
            hits.append(daddu)
    return _one_site(iso, hits, 0, 0x00A0982D, 0x24130010,
                     "forge menu level cap @0x%X")


# ---------------------------------------------------------------------------
# Signature-skill toggle (issue #16): the skill menu's Equip, Remove and
# Move SP handlers all ask func_0043A430 whether a skill is "normal", and
# that predicate is a hardcoded range check: only 1-based skill ids below
# 0x17 count, so every skill from 0-based 22 (Thief) upward, including all
# the signature skills, is refused with "Can't remove unique skills." No
# data table gates it: raising caps and granting starting ranks were both
# patched in test ISOs and the refusal was unchanged. Widening the two
# range compares from 0x17 to 0x31 (the full 48-skill array plus the
# 1-based shift) makes every skill pass, and with exactly those two bytes
# changed the Prince equipped Analyze through the stock menu in game
# (ps2-re games/suikoden5/SIGNATURE_SKILLS.md). The mode 2 service-skill
# whitelist later in the same function is untouched.
#
# The locator anchors on the function's invariant mode 2 constant chain
# (the 0x2E / 0x2B / 0x30 / 0x20 / 0x1F loads and their branches, unique
# in the scan window in both regions), which excludes both patched words,
# so a patched or foreign disc still locates and the span classification
# decides state. The function body is byte-identical in PAL, so the same
# anchor lands there too. The two compares are two sites of the one
# toggle, the gate toggles' shape: set_toggle classifies every site
# before writing any byte, so a foreign word at either compare refuses
# the whole toggle untouched, and the pair always patches and restores
# together. Self-restoring: both stock words are constants.
# ---------------------------------------------------------------------------

_SIGSKILL_ANCHOR = struct.pack(
    "<27I",
    0x308300FF, 0x2402002E, 0x1062000F, 0x00000000, 0x2402002B, 0x1062000C,
    0x00000000, 0x24020030, 0x10620009, 0x00000000, 0x24020020, 0x10620006,
    0x00000000, 0x2402001F, 0x10620003, 0x00000000, 0x10000004, 0x0000102D,
    0x10000011, 0x24020001, 0x0000102D, 0x1000000E, 0x00000000, 0x308200FF,
    0x0002082A, 0x1020FFFA, 0x00000000)


def _sigskill_sites(iso):
    hits = _bytes_hits(_scan_blob(iso), _SIGSKILL_ANCHOR)
    if len(hits) != 1:
        return []
    base = F.DAWN_SCAN_LO + hits[0]
    out = []
    for rel, mode, stock, patched in ((0x6C, 0, 0x28410017, 0x28410031),
                                      (0x84, 1, 0x28610017, 0x28610031)):
        off = base + rel
        out.append({"label": "skill-id range check, mode %d compare @0x%X"
                             % (mode, off),
                    "spans": [{"off": off,
                               "stock": struct.pack("<I", stock),
                               "patched": struct.pack("<I", patched),
                               "cur": iso.rd(off, 4)}]})
    return out


# ---------------------------------------------------------------------------
# Gear equip requirements toggle (issue #49): the Corruption Shell and
# the Saint's Cloak are the only Type 6 body armors, and Type 6 takes a
# special path in the shared can-equip predicate func_0038A4B0, gated on
# four slti immediates (Shell: max HP >= 350 and kills >= 100; Cloak:
# max HP >= 300 and kills < 31). The sites are defined once in
# s5patch (GEAR_REQ_FIELDS / gear_requirement_sites) and shared with
# the Gear view's threshold fields: the traced offsets for the disc's
# region, with every word verified against its stock or patched form
# before any write, so a custom threshold set in the Gear view reads
# foreign here and the toggle refuses rather than overwrite it.
# Self-restoring: all four stock words are constants. The patched
# immediates (0 / 0 / 0 / 0x7FFF) are exactly the disable ISO the lab
# proved in game.
# ---------------------------------------------------------------------------

def _gear_req_sites(iso):
    return P.gear_requirement_sites(iso)


# ---------------------------------------------------------------------------
# Godspeed dash toggle (issue #18): the field motor func_00247360 admits
# its fast dash tier when Cathari is in the live party OR the NG+ carry
# byte is nonzero; on the not-found path a byte of 0 branches at the site
# below to skip the tier. NOPing that branch admits the tier for any
# party in any run: the fall-through joins the same tier block the
# admitted paths use, tier flag included, so the speed doubling and the
# run animation rate agree (ps2-re DASH_GATE_CODE.md). Sites are defined
# in s5patch (dash_gate_sites), anchor-checked on the getter call.
# This is a NEW toggle id, not a rebind of "godspeed-always": that entry
# patches the event-script resolver gates and stays registered so discs
# carrying it still verify and restore exactly. Rebinding its id to the
# motor site would read such discs as off (the new sites are stock
# there) and a journal restore carrying the old originals would write
# nothing at the resolver offsets, silently leaving the old NOPs in
# place. Self-restoring: the stock branch word is a constant in both
# regions. The tier it admits is the one measured in game with Cathari
# or NG+ (about double dash distance on the world map); this exact patch
# has not had an in-game A/B run yet, so it carries "code-verified".
# ---------------------------------------------------------------------------

def _godspeed_dash_sites(iso):
    return P.dash_gate_sites(iso)


TOGGLES = [
    {"id": "dawn-unlock", "label": "Dawn Rune: unlock all four spells",
     "group": "Code patches", "effect": "proven",
     "blurb": "Gives the Dawn Rune all four spells from the start of "
              "the game.",
     "find": _dawn_sites, "self_restoring": True},
    {"id": "champions-always", "label": "Champion's Rune always-on",
     "group": "Rune perks: always on", "effect": "code-verified",
     "blurb": "Removes the rune's equip check so its encounter effect "
              "applies with no rune equipped: fewer random encounters, "
              "and weak enemies are suppressed. The gate sites and the "
              "mechanism were checked against the game code; the "
              "encounter effect itself has not been tested in game.",
     "find": _gate_sites(79), "self_restoring": False},
    {"id": "firefly-always", "label": "Great Firefly Rune always-on",
     "group": "Rune perks: always on", "effect": "code-verified",
     "blurb": "Removes the rune's equip check so its encounter effect "
              "applies with no rune equipped: more random encounters, "
              "which is what you want for grinding. The gate sites and "
              "the mechanism were checked against the game code; the "
              "encounter effect itself has not been tested in game.",
     "find": _gate_sites(80), "self_restoring": False},
    {"id": "fortune-always", "label": "Fortune always-on (not working in game)",
     "group": "Rune perks: always on", "effect": "not-working",
     "blurb": "Does not work: the gates this removes feed a script "
              "check that battle rewards never use. Listed so a disc "
              "already patched with it can be checked and restored "
              "exactly.",
     "find": _gate_sites(77), "self_restoring": False},
    {"id": "prosperity-always", "label": "Prosperity always-on (not working in game)",
     "group": "Rune perks: always on", "effect": "not-working",
     "blurb": "Does not work: the gates this removes feed a script "
              "check that battle rewards never use. Listed so a disc "
              "already patched with it can be checked and restored "
              "exactly.",
     "find": _gate_sites(78), "self_restoring": False},
    {"id": "godspeed-always", "label": "Godspeed always-on (not working in game)",
     "group": "Rune perks: always on", "effect": "not-working",
     "blurb": "Does not work: the gates this removes feed a script "
              "check that movement and escape code never use. Listed "
              "so a disc already patched with it can be checked and "
              "restored exactly.",
     "find": _gate_sites(82), "self_restoring": False},
    {"id": "godspeed-dash", "label": "Godspeed dash always on",
     "group": "Dash tuning", "effect": "proven",
     "blurb": "Normally the fast dash tier needs Cathari in the "
              "party or a New Game Plus run. This removes that check, "
              "so holding L2 or Triangle dashes at the fast tier for "
              "any party in any run. About double the dash distance on "
              "the world map, in towns and indoors; walking is never "
              "affected, and in dungeons the tier adds almost nothing. "
              "Proven in game: on a first run with no Cathari, the "
              "patched disc dashed 108.14 units over the world map "
              "course where the stock disc dashed 54.47. Pair with "
              "the dash multipliers below to change the tier strength.",
     "find": _godspeed_dash_sites, "self_restoring": True},
    {"id": "training-sp-free", "label": "Training does not use SP",
     "group": "Code patches", "effect": "code-verified",
     "blurb": "Training a skill costs no party SP. Checked against "
              "the game code in both regions; not yet tested in game.",
     "find": _training_sp_free_sites, "self_restoring": True},
    {"id": "training-sp-gain", "label": "Training increases SP",
     "group": "Code patches", "effect": "code-verified",
     "blurb": "Training a skill adds party SP instead of spending "
              "it. Checked against the game code in both regions; "
              "not yet tested in game.",
     "find": _training_sp_gain_sites, "self_restoring": True},
    {"id": "train-ss-rank", "label": "Train skill once for SS rank",
     "group": "Code patches", "effect": "code-verified",
     "blurb": "One paid training session sets a skill's rank "
              "straight to SS. Checked against the game code in both "
              "regions; not yet tested in game.",
     "find": _train_ss_sites, "self_restoring": True},
    {"id": "forge-once-max", "label": "Forge once sets weapon level to max",
     "group": "Code patches", "effect": "code-verified",
     "blurb": "A single forge sets a weapon straight to level 16, "
              "the max, instead of raising it by 1. Checked against "
              "the game code in both regions; not yet tested in game.",
     "find": _forge_once_sites, "self_restoring": True},
    {"id": "smith-forge-max", "label": "Blacksmiths can forge to max level",
     "group": "Code patches", "effect": "code-verified",
     "blurb": "Every blacksmith can forge weapons up to level 16, "
              "the max, instead of stopping at their own limit. "
              "Checked against the game code in both regions; not "
              "yet tested in game.",
     "find": _smith_cap_sites, "self_restoring": True},
    {"id": "sig-skills-normal", "label": "Signature skills are normal skills",
     "group": "Code patches", "effect": "proven",
     "blurb": "The skill menu refuses every skill from Thief upward "
              "(Analyze, Freeze, Barrage and the other signature "
              "skills) with the message \"Can't remove unique skills.\" "
              "This widens the check behind that refusal, so every "
              "skill can be equipped, removed and moved like a normal "
              "skill, including a character removing their own "
              "signature skill. It grants no ranks: a character still "
              "needs a rank in the skill before it can be equipped. "
              "HQ service skills are unaffected.",
     "find": _sigskill_sites, "self_restoring": True},
    {"id": "gear-requirements-off", "label": "Remove armor equip requirements",
     "group": "Code patches", "effect": "proven",
     "blurb": "Corruption Shell normally needs 350+ max HP and 100+ "
              "kills. Saint's Cloak needs 300+ max HP and no more than "
              "30 kills. This removes both gates, so any character can "
              "equip them.",
     "find": _gear_req_sites, "self_restoring": True},
]
_BY_ID = {t["id"]: t for t in TOGGLES}


def _span_state(span):
    cur = span["cur"]
    if cur == span["patched"]:
        return "on"
    if span["stock"] is not None:
        return "off" if cur == span["stock"] else "foreign"
    return None     # gate span: the site's shape decides, not the bytes


def toggle_status(iso, toggle_id):
    """{id, label, group, blurb, effect, state, sites} where state is one
    of off / on / mixed / foreign / not-found, decided span by span. The
    group and blurb ride along because the web Code patches view groups
    and annotates by them; without them every toggle rendered under the
    default group with no blurb."""
    t = _BY_ID[toggle_id]
    sites = t["find"](iso)
    if not sites:
        return {"id": toggle_id, "label": t["label"], "group": t["group"],
                "blurb": t["blurb"], "effect": t["effect"],
                "state": "not-found", "sites": []}
    out_sites, states = [], []
    for s in sites:
        if "shape" in s:                       # gate site
            st = {"off": "off", "on": "on", "foreign": "foreign"}[s["shape"]]
        else:
            ss = [_span_state(sp) for sp in s["spans"]]
            st = ss[0] if all(x == ss[0] for x in ss) else "mixed"
        states.append(st)
        out_sites.append({"label": s["label"], "state": st,
                          "spans": [{"off": sp["off"],
                                     "cur": sp["cur"].hex()} for sp in s["spans"]]})
    if all(x == states[0] for x in states):
        state = states[0]
    elif "foreign" in states:
        state = "foreign"
    else:
        state = "mixed"
    return {"id": toggle_id, "label": t["label"], "group": t["group"],
            "blurb": t["blurb"], "effect": t["effect"],
            "state": state, "sites": out_sites}


def list_toggles(iso):
    return [toggle_status(iso, t["id"]) for t in TOGGLES]


def set_toggle(iso, toggle_id, on, originals=None):
    """Apply (on=True) or restore (on=False) a registered toggle.

    Apply: every span must be stock-shaped or already patched; a foreign
    span refuses the whole toggle before anything is written. Returns
    {"ok", "changed", "originals"} where originals maps each changed
    span's offset (as a string) to its displaced bytes (hex): the exact
    restore token for non-self-restoring toggles.

    Restore: self-restoring toggles write their constant stock bytes.
    Gate toggles require `originals` (the map apply returned); without
    it, or with a span missing from it, the restore refuses rather than
    guess a branch word."""
    t = _BY_ID[toggle_id]
    st = toggle_status(iso, toggle_id)
    if st["state"] == "not-found":
        raise KeyError("toggle %s: no patch sites found on this disc" % toggle_id)
    if st["state"] == "foreign":
        raise ValueError("toggle %s: a patch site holds bytes this toggle "
                         "does not recognise; refusing to touch it" % toggle_id)
    sites = t["find"](iso)
    plan = []          # (off, bytes_to_write, displaced_hex)
    if on:
        for s in sites:
            for sp in s["spans"]:
                if sp["cur"] != sp["patched"]:
                    plan.append((sp["off"], sp["patched"], sp["cur"].hex()))
    else:
        orig = {int(k): bytes.fromhex(v) for k, v in (originals or {}).items()}
        for s in sites:
            for sp in s["spans"]:
                if sp["cur"] == sp["patched"] or (sp["stock"] is not None
                                                  and sp["cur"] != sp["stock"]):
                    if sp["off"] in orig:
                        plan.append((sp["off"], orig[sp["off"]], sp["cur"].hex()))
                    elif t["self_restoring"]:
                        plan.append((sp["off"], sp["stock"], sp["cur"].hex()))
                    else:
                        raise ValueError(
                            "toggle %s: restoring needs the original bytes "
                            "for the span at 0x%X (apply returns them); "
                            "refusing to guess" % (toggle_id, sp["off"]))
    displaced = {}
    for off, data, cur_hex in plan:
        iso.wr(off, data)
        displaced[str(off)] = cur_hex
    for off, data, _ in plan:                       # post-write verify
        if iso.rd(off, len(data)) != data:
            raise AssertionError("toggle %s: write at 0x%X did not verify"
                                 % (toggle_id, off))
    return {"ok": True, "changed": len(plan), "originals": displaced}
