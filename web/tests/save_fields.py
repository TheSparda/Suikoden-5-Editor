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
# 16-char names (issue #38): the game allows the full 16-byte field; the editor
# used to truncate to 15 and silently cut a character off the castle name on save.
gd5, _ = SV.apply_gamedata_edits(gd, {"castleName": "Chateau Lalaland"})
f5 = SV.decode_gamedata(gd5)
chk("16-char castle round-trip", f5["castleName"] == "Chateau Lalaland", f5["castleName"])
chk("16-char castle mirror", gd5[SV.S5_FIELD_MIRRORS["castleName"]:][:17] == b"Chateau Lalaland\x00")
gd6, _ = SV.apply_gamedata_edits(gd, {"castleName": "1234567890ABCDEFG"})
chk("17-char castle truncates at 16", SV.decode_gamedata(gd6)["castleName"] == "1234567890ABCDEF")
# hero can sit in any party slot (issue #38): decode keeps him wherever he is
gd7 = bytearray(gd)
gd7[SV.PARTY_OFF:SV.PARTY_OFF+2] = (20).to_bytes(2, "little")   # Georg in slot 1
gd7[SV.PARTY_OFF+6:SV.PARTY_OFF+8] = (0).to_bytes(2, "little")   # hero in slot 4
chk("hero in slot 4 decode", SV.decode_gamedata(bytes(gd7))["party"][:4] == [20, 8, SV.PARTY_EMPTY, 0])
# invalid party value rejected (char id must be <120 or the empty sentinel)
gd4, ch4 = SV.apply_gamedata_edits(gd3, {"party3": 500})
chk("bad party id rejected", ch4 == 0)
# regression guards on the verified constants
chk("POTCH off", SV.S5_FIELDS["potch"][0] == 0xBC74)
chk("PARTY off/slots", (SV.PARTY_OFF, SV.PARTY_SLOTS, SV.PARTY_EMPTY) == (0x36, 10, 0x0100))
chk("mirror offs", (SV.S5_FIELD_MIRRORS["heroName"], SV.S5_FIELD_MIRRORS["castleName"]) == (0xBC7C, 0xBC9E))

print(f"\n{n-bad}/{n} passed")
sys.exit(1 if bad else 0)
