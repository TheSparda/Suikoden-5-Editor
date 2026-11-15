#!/usr/bin/env python3
"""Save-field round-trip on a synthetic gamedata payload built from the engine's own
constants (no real save shipped). Covers the fields verified 2026-08-23 by ELF RE:
potch @0xBC74, party SP @0xBC78, army name @0xBC8D, the hero/castle struct mirrors
(0xBC7C/0xBC9E — the copies the game loads back live), the 10-slot party @0x36,
and the playtime display decode. Run via tests/save-fields.mjs (skips without python3)."""
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

gd = bytearray(SV.GAMEDATA_SIZE)
# seed party: hero + Lyon(8), rest empty
for k in range(SV.PARTY_SLOTS):
    v = 0 if k == 0 else (8 if k == 1 else SV.PARTY_EMPTY)
    gd[SV.PARTY_OFF+2*k:SV.PARTY_OFF+2*k+2] = v.to_bytes(2, "little")
# seed playtime 19:16:05
for i, v in enumerate((19, 16, 5)):
    gd[SV.PLAYTIME_OFF+2*i:SV.PLAYTIME_OFF+2*i+2] = v.to_bytes(2, "little")
gd = bytes(gd)

f = SV.decode_gamedata(gd)
chk("decode exposes new fields", all(k in f for k in ("potch", "partySP", "armyName", "party", "playtime")))
chk("playtime decode", f["playtime"] == "19:16:05", f["playtime"])
chk("party decode", f["party"][:3] == [0, 8, SV.PARTY_EMPTY], str(f["party"]))

edits = {"potch": 123456, "partySP": 4321, "armyName": "Royalist",
         "heroName": "Freyja", "castleName": "Ceras", "party2": 16, "party1": SV.PARTY_EMPTY}
gd2, changed = SV.apply_gamedata_edits(gd, edits)
chk("apply count", changed == len(edits), str(changed))
f2 = SV.decode_gamedata(gd2)
chk("potch round-trip", f2["potch"] == 123456)
chk("partySP round-trip", f2["partySP"] == 4321)
chk("armyName round-trip", f2["armyName"] == "Royalist")
chk("party slot edits", f2["party"][1] == SV.PARTY_EMPTY and f2["party"][2] == 16, str(f2["party"]))
# struct mirrors: renames must land in the game-state struct copies too
chk("hero struct mirror", gd2[SV.S5_FIELD_MIRRORS["heroName"]:][:17].split(b"\x00")[0] == b"Freyja")
chk("castle struct mirror", gd2[SV.S5_FIELD_MIRRORS["castleName"]:][:17].split(b"\x00")[0] == b"Ceras")
# caps
gd3, _ = SV.apply_gamedata_edits(gd2, {"potch": 500_000_000, "partySP": 5_000_000})
f3 = SV.decode_gamedata(gd3)
chk("potch clamped", f3["potch"] == 99_999_999, f"{f3['potch']:,}")
chk("partySP clamped", f3["partySP"] == 999_999, f"{f3['partySP']:,}")
# invalid party value rejected (char id must be <120 or the empty sentinel)
gd4, ch4 = SV.apply_gamedata_edits(gd3, {"party3": 500})
chk("bad party id rejected", ch4 == 0)
# ---- issue #32: a party assignment also sets the member's recruitment bit, in the
# same edit, and touches nothing else (the game keeps party => recruited in sync;
# a bit-clear party member is a corrupted state the old write path produced).
gd5, ch5 = SV.apply_gamedata_edits(gd, {"party4": 57})
chk("party assign counts once", ch5 == 1, str(ch5))
chk("party assign sets the recruit bit", SV.read_character(gd5, 57)["recruited"] == 1)
diff = [i for i in range(len(gd)) if gd5[i] != gd[i]]
chk("party assign touches only its slot + one bitfield byte",
    diff == [SV.PARTY_OFF + 8, SV.PARTY_OFF + 9, SV.RECRUIT_BASE + 7], str(diff))
chk("only the one bit flipped in that byte",
    gd5[SV.RECRUIT_BASE + 7] == (gd[SV.RECRUIT_BASE + 7] | (1 << (57 & 7))))
# emptying a slot never touches the bitfield
gd6, _ = SV.apply_gamedata_edits(gd5, {"party4": SV.PARTY_EMPTY})
chk("emptying a slot leaves the bitfield alone",
    gd6[SV.RECRUIT_BASE:SV.RECRUIT_BASE + 15] == gd5[SV.RECRUIT_BASE:SV.RECRUIT_BASE + 15])
# a same-batch staged un-recruit cannot undo a party assignment (party pass runs last)
gd7, _ = SV.apply_gamedata_edits(gd, {"c57_rec": 0, "party4": 57})
chk("party assignment wins over a same-batch un-recruit",
    SV.read_character(gd7, 57)["recruited"] == 1)
# ---- issue #24: Eresh (10) and Euram (108) are mutually exclusive in normal play,
# but deliberately recruiting both is a legitimate edit. The engine must set both
# bits when asked; the impossibility warning is a UI concern, not a write block.
gd8, ch8 = SV.apply_gamedata_edits(gd, {"c10_rec": 1, "c108_rec": 1})
chk("Eresh+Euram both settable", ch8 == 2
    and SV.read_character(gd8, 10)["recruited"] == 1
    and SV.read_character(gd8, 108)["recruited"] == 1)
chk("pair bits land in their own bytes",
    gd8[SV.RECRUIT_BASE + 1] == (gd[SV.RECRUIT_BASE + 1] | (1 << 2))
    and gd8[SV.RECRUIT_BASE + 13] == (gd[SV.RECRUIT_BASE + 13] | (1 << 4)))
# regression guards on the verified constants
chk("POTCH off", SV.S5_FIELDS["potch"][0] == 0xBC74)
chk("PARTY off/slots", (SV.PARTY_OFF, SV.PARTY_SLOTS, SV.PARTY_EMPTY) == (0x36, 10, 0x0100))
chk("mirror offs", (SV.S5_FIELD_MIRRORS["heroName"], SV.S5_FIELD_MIRRORS["castleName"]) == (0xBC7C, 0xBC9E))

print(f"\n{n-bad}/{n} passed")
sys.exit(1 if bad else 0)
