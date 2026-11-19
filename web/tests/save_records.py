#!/usr/bin/env python3
"""Per-character record edits (runes, skill ranks, skill slots, party
arrays) on the two real fixture saves plus a synthetic payload.

save_fields.py covers the header fields and the #32/#39 semantics on a
synthetic payload; this suite covers the character-record write paths
themselves (c<idx>_rhead / _sk<n> / _ss0 / _ss1), which no other suite
exercises, and re-proves the party invariants on the real US and PAL
fixtures with byte-exact footprints and byte-identical round trips.
Recruitment itself is read-only (issue #39): it is read from the
status word, and no edit in this suite may move a status word or the
old fake-field region.
Real-save sections skip when a fixture is absent."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "Editor")))
import s5save as SV

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

def diffs(a, b):
    return [i for i in range(len(a)) if a[i] != b[i]]

def rec_off(idx):
    return SV._char_off(idx)

# ---------- synthetic payload: record write paths + validation ----------
gd = bytes(SV.GAMEDATA_SIZE)
# rune write (save-space byte 84 = Godspeed, see the rune id shift fix)
g2, ch = SV.apply_gamedata_edits(gd, {"c8_rhead": 84})
chk("synthetic rune write counts", ch == 1, str(ch))
chk("synthetic rune readback", SV.read_character(g2, 8)["runes"]["rhead"] == 84)
chk("synthetic rune footprint is one byte",
    diffs(gd, g2) == [rec_off(8) + SV.CHAR_RUNE], str(diffs(gd, g2)))
# Epic skill grant: rank + equip slot (issue #16: Epics are ordinary array rows)
g3, ch = SV.apply_gamedata_edits(gd, {"c48_sk19": 3, "c48_ss1": 20})
c48 = SV.read_character(g3, 48)
chk("synthetic epic rank readback", c48["skills"][19] == 3)
chk("synthetic epic slot readback", c48["slots"][1] == 20)
chk("synthetic epic footprint is two bytes",
    diffs(gd, g3) == sorted([rec_off(48) + SV.CHAR_SKILLS + 19,
                             rec_off(48) + SV.CHAR_SLOTS + 1]),
    str(diffs(gd, g3)))
# negative controls: out-of-range targets are ignored, never written
g4, ch = SV.apply_gamedata_edits(gd, {"c120_rhead": 84, "c48_sk48": 3,
                                      "c999_rec": 1, "c48_helm2": 1})
chk("out-of-range char/skill/slot edits are ignored", ch == 0 and g4 == gd,
    str(ch))
# clamps are pinned behavior: rank caps at 7, slot id at SKILL_COUNT,
# rune bytes mask to u8 (the web UI validates before it calls the engine)
g5, _ = SV.apply_gamedata_edits(gd, {"c48_sk19": 9, "c48_ss1": 99,
                                     "c8_rhead": 300})
c48 = SV.read_character(g5, 48)
chk("rank clamps to 7", c48["skills"][19] == 7)
chk("slot id clamps to SKILL_COUNT", c48["slots"][1] == SV.SKILL_COUNT)
chk("rune byte masks to u8", SV.read_character(g5, 8)["runes"]["rhead"] == 44)

# ---------- real fixtures ----------
HOME = os.path.expanduser("~")
US_PAYLOAD = os.path.join(HOME, "workspace/suikoden5-debug/saves/library/sparda-us-slot00.payload.bin")
PAL_CANDIDATES = [os.path.join(HOME, "workspace/user/files/SuikodenV_03NIV53_075_31.psu"),
                  os.path.join(HOME, "workspace/suikoden5-debug/fixtures/pal.psu")]
fixtures = []
if os.path.exists(US_PAYLOAD):
    fixtures.append(("US", open(US_PAYLOAD, "rb").read()))
pal_path = next((p for p in PAL_CANDIDATES if os.path.exists(p)), None)
if pal_path:
    pal = SV.read_gamedata_payload(pal_path)
    if pal:
        fixtures.append(("PAL", pal))
if not fixtures:
    print("SKIP real-save record checks: no fixture saves found")

for label, gd in fixtures:
    # rune round trip on Lyon (8): set the Godspeed byte, then restore hers
    orig = SV.read_character(gd, 8)["runes"]["rhead"]
    g2, _ = SV.apply_gamedata_edits(gd, {"c8_rhead": 84})
    chk(f"{label}: rune write readback",
        SV.read_character(g2, 8)["runes"]["rhead"] == 84)
    chk(f"{label}: rune write footprint is one byte",
        diffs(gd, g2) == [rec_off(8) + SV.CHAR_RUNE], str(diffs(gd, g2)))
    g3, _ = SV.apply_gamedata_edits(g2, {"c8_rhead": orig})
    chk(f"{label}: rune round trip is byte-identical", g3 == gd)
    # Epic grant round trip on char 48: rank + slot, then restore originals
    c0 = SV.read_character(gd, 48)
    g4, _ = SV.apply_gamedata_edits(gd, {"c48_sk19": 3, "c48_ss1": 20})
    c1 = SV.read_character(g4, 48)
    chk(f"{label}: epic grant readback",
        c1["skills"][19] == 3 and c1["slots"][1] == 20)
    g5, _ = SV.apply_gamedata_edits(g4, {"c48_sk19": c0["skills"][19],
                                         "c48_ss1": c0["slots"][1]})
    chk(f"{label}: epic round trip is byte-identical", g5 == gd)

# ---------- #32 / #24 on the real US fixture ----------
us = dict(fixtures).get("US")
if us:
    party = SV.decode_gamedata(us)["party"]
    slot = next(i for i, v in enumerate(party) if v == SV.PARTY_EMPTY)
    chk("US: Hazuki starts unrecruited",
        SV.read_character(us, 58)["recruited"] == 0)
    g2, _ = SV.apply_gamedata_edits(us, {"party%d" % slot: 58})
    chk("US: party assign readback",
        SV.decode_gamedata(g2)["party"][slot] == 58
        and SV.read_character(g2, 58)["recruited"] == 1)
    # the write lands in the real battle array, and the browser summary is
    # regenerated flattened (58 inserted at her battle position, the
    # entourage entries shift one summary slot later)
    chk("US: party assign lands in the real battle array",
        g2[SV.PARTY_BATTLE_OFF:SV.PARTY_BATTLE_OFF + SV.PARTY_BATTLE_SLOTS]
        == bytes([0, 8, 48, 58, SV.PARTY_REAL_EMPTY, SV.PARTY_REAL_EMPTY])
        and [int.from_bytes(g2[SV.PARTY_OFF + 2 * i:SV.PARTY_OFF + 2 * i + 2],
                            "little") for i in range(SV.PARTY_SLOTS)]
        == [0, 8, 48, 58, 81, 19, 35] + [SV.PARTY_EMPTY] * 3)
    chk("US: party assign footprint is summary + battle byte only",
        diffs(us, g2) == [SV.PARTY_OFF + 6, SV.PARTY_OFF + 8, SV.PARTY_OFF + 10,
                          SV.PARTY_OFF + 12, SV.PARTY_OFF + 13,
                          SV.PARTY_BATTLE_OFF + 3],
        str(diffs(us, g2)))
    chk("US: party assign leaves Hazuki's status word at zero",
        SV.status_word(g2, 58) == 0)
    # negative control: the summary is not the source of truth. A payload
    # whose summary was rewritten on its own (what the pre-fix editor
    # produced) still decodes to the party in the real arrays.
    tampered = bytearray(us)
    for i in range(SV.PARTY_SLOTS):
        tampered[SV.PARTY_OFF + 2 * i:SV.PARTY_OFF + 2 * i + 2] = \
            SV.PARTY_EMPTY.to_bytes(2, "little")
    chk("US: decode ignores a stale summary",
        SV.decode_gamedata(bytes(tampered))["party"]
        == SV.decode_gamedata(us)["party"])
    # emptying the slot restores the original bytes exactly: the party
    # write never touched her status word, so her read returns to plain
    # unrecruited with no second edit (issue #39)
    g3, _ = SV.apply_gamedata_edits(g2, {"party%d" % slot: SV.PARTY_EMPTY})
    chk("US: emptying the slot restores the unrecruited read",
        SV.read_character(g3, 58)["recruited"] == 0)
    chk("US: party round trip is byte-identical", g3 == us)
    # char 58 has no record in Kevin's save (word 0), so the toggle's
    # recruit write is refused and the payload is untouched (issue #39)
    try:
        SV.apply_gamedata_edits(us, {"c58_rec": 1})
        chk("US: recruiting a no-record character is refused", False)
    except ValueError:
        chk("US: recruiting a no-record character is refused", True)
    # #24 on real data: in Kevin's save neither rival reads recruited
    # (Eresh has no record, Euram's is initialized only). The old suite
    # expected Eresh set: that was the fake bitfield talking (issue #39).
    chk("US: neither rival reads recruited",
        SV.read_character(us, 10)["recruited"] == 0
        and SV.read_character(us, 10)["recruitState"] == "none"
        and SV.read_character(us, 108)["recruited"] == 0
        and SV.read_character(us, 108)["recruitState"] == "initialized")

# ---------- #32 / #24 fixture state on the real PAL fixture ----------
pal = dict(fixtures).get("PAL")
if pal:
    # Eachna recruited Eresh organically: her status word is active, and
    # Euram's is initialized only, so the mutually exclusive pair reads
    # exactly as the story leaves it. (The old suite expected both set:
    # her save also carries the bulk-set fake bitfield from the #38 era,
    # which the editor no longer reads, issue #39.)
    chk("PAL: Eresh recruited, Euram not",
        SV.read_character(pal, 10)["recruited"] == 1
        and SV.read_character(pal, 108)["recruited"] == 0)
    party = SV.decode_gamedata(pal)["party"]
    slot = next(i for i, v in enumerate(party) if v == SV.PARTY_EMPTY)
    member = next(i for i in range(SV.NUM_CHARS)
                  if i not in party and SV.read_character(pal, i)["recruited"] == 1)
    g2, _ = SV.apply_gamedata_edits(pal, {"party%d" % slot: member})
    chk("PAL: party assign lands in the real entourage array",
        g2[SV.PARTY_ENTOUR_OFF:SV.PARTY_ENTOUR_OFF + SV.PARTY_ENTOUR_SLOTS]
        == bytes([56, 63, member, SV.PARTY_REAL_EMPTY]))
    chk("PAL: party assign footprint is summary slot + entourage byte only (recruitment data untouched)",
        diffs(pal, g2) == [SV.PARTY_OFF + 2 * slot, SV.PARTY_OFF + 2 * slot + 1,
                           SV.PARTY_ENTOUR_OFF + 2],
        str(diffs(pal, g2)))
    g3, _ = SV.apply_gamedata_edits(g2, {"party%d" % slot: SV.PARTY_EMPTY})
    chk("PAL: party round trip is byte-identical", g3 == pal)

print(f"\nsave_records: {n - bad}/{n} checks passed")
sys.exit(1 if bad else 0)
