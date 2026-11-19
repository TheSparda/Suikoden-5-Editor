#!/usr/bin/env python3
"""CT-survey save features (held items, EXP/SP, weapon sharpen level,
rune spell uses) on a synthetic payload plus the real fixture saves.

The offsets were proven by static correlation against live-record dumps
(US + PAL, 120/120 characters each; ps2-re CT_FEATURES_BUILD.md). This
suite pins the read/write paths: readbacks against known real-save
values, byte-exact footprints, clamps, held-item validation (the same
rules as inventory edits), the per-instance value semantics, and the
held-slot-1 / accessory byte sharing. Real-save sections skip when a
fixture is absent."""
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

# ---------- synthetic payload: write paths, footprints, clamps ----------
gd = bytes(SV.GAMEDATA_SIZE)
g2, ch = SV.apply_gamedata_edits(gd, {"c3_exp": 500, "c3_sp": 1234,
                                      "c3_wlv": 16, "c3_ruse2": 4})
c = SV.read_character(g2, 3)
chk("synthetic EXP/SP readback", (c["exp"], c["sp"]) == (500, 1234))
chk("synthetic weapon level reads in displayed units", c["weaponLevel"] == 16)
chk("weapon level stores level-1", g2[rec_off(3) + SV.CHAR_WEAPON_LEVEL] == 15)
chk("synthetic rune uses readback", c["runeUses"][2] == 4)
chk("EXP/SP/wlv/ruse footprint is the six value bytes",
    diffs(gd, g2) == sorted([rec_off(3) + 0xCE, rec_off(3) + 0xCF,
                             rec_off(3) + 0xD0, rec_off(3) + 0xD1,
                             rec_off(3) + 0xEB, rec_off(3) + 0xC8]),
    str(diffs(gd, g2)))
g3, _ = SV.apply_gamedata_edits(gd, {"c3_exp": 70000, "c3_sp": -5,
                                     "c3_wlv": 99, "c3_ruse0": 42})
c = SV.read_character(g3, 3)
chk("EXP clamps to u16, SP clamps to 0",
    c["exp"] == 0xFFFF and c["sp"] == 0)
chk("weapon level clamps to 16, rune uses clamp to 9",
    c["weaponLevel"] == 16 and c["runeUses"][0] == 9)
g4, _ = SV.apply_gamedata_edits(gd, {"c3_wlv": 0})
chk("weapon level clamps to 1 (stored 0)",
    SV.read_character(g4, 3)["weaponLevel"] == 1
    and g4[rec_off(3) + SV.CHAR_WEAPON_LEVEL] == 0)

# held items on the synthetic payload
g5, ch = SV.apply_gamedata_edits(gd, {"c3_held0": {"cat": 9, "id": 1, "qty": 3},
                                      "c3_held2": {"cat": 5, "id": 20, "qty": 0}})
c = SV.read_character(g5, 3)
chk("held set readback (stackable + single)",
    (c["held"][0]["cat"], c["held"][0]["id"], c["held"][0]["qty"],
     c["held"][0]["max"]) == (9, 1, 3, 5)
    and (c["held"][2]["cat"], c["held"][2]["id"]) == (5, 20))
chk("held footprint is the two entries' head bytes",
    diffs(gd, g5) == sorted([rec_off(3) + 0xF8 + i for i in (0, 1, 2, 3)]
                            + [rec_off(3) + 0xF8 + 16 + i for i in (0, 1)]),
    str(diffs(gd, g5)))
# per-instance value: a quantity-only edit keeps it, an item change zeroes it
b = bytearray(g5)
b[rec_off(3) + 0xF8 + 4:rec_off(3) + 0xF8 + 8] = (777).to_bytes(4, "little")
g6, _ = SV.apply_gamedata_edits(bytes(b), {"c3_held0": {"cat": 9, "id": 1, "qty": 2}})
chk("quantity-only held edit keeps the instance value",
    SV.read_character(g6, 3)["held"][0]["value"] == 777)
g7, _ = SV.apply_gamedata_edits(bytes(b), {"c3_held0": {"cat": 9, "id": 2, "qty": 1}})
chk("item-change held edit zeroes the instance value",
    SV.read_character(g7, 3)["held"][0]["value"] == 0)
g8, _ = SV.apply_gamedata_edits(g5, {"c3_held0": {"cat": 0}})
chk("held clear zeroes the whole entry",
    SV.read_character(g8, 3)["held"][0]
    == {"cat": 0, "id": 0, "qty": 0, "max": 0, "value": 0})
# held slot 1 and the accessory byte are the same byte
chk("accessory field reads held slot 1's id byte (shared byte)",
    SV.read_character(g5, 3)["armor"]["accessory"]
    == SV.read_character(g5, 3)["held"][0]["id"] == 1)  # entry 0 is (9,1) here
g9, _ = SV.apply_gamedata_edits(gd, {"c3_held0": {"cat": 5, "id": 7, "qty": 0}})
chk("setting held slot 1 to an accessory equips it",
    SV.read_character(g9, 3)["armor"]["accessory"] == 7)
# validation: unverified items and quantities raise, like inventory edits
for key, val, why in (
        ("c3_held0", {"cat": 99, "id": 1, "qty": 0}, "bad category"),
        ("c3_held0", {"cat": 9, "id": 99, "qty": 1}, "bad id"),
        ("c3_held0", {"cat": 9, "id": 1, "qty": 6}, "qty above stack limit"),
        ("c3_held0", {"cat": 5, "id": 1, "qty": 2}, "qty on a one-per-slot item")):
    try:
        SV.apply_gamedata_edits(gd, {key: val})
        chk("held edit raises on " + why, False, "no error")
    except ValueError:
        chk("held edit raises on " + why, True)
g10, ch = SV.apply_gamedata_edits(gd, {"c3_held4": {"cat": 9, "id": 1, "qty": 1},
                                       "c3_ruse4": 3})
chk("out-of-range held/ruse slots are ignored", ch == 0 and g10 == gd)

# ---------- real fixtures ----------
HOME = os.path.expanduser("~")
US_PAYLOAD = os.path.join(HOME, "workspace/suikoden5-debug/saves/library/sparda-us-slot00.payload.bin")
PAL_FILES = [os.path.join(HOME, "workspace/suikoden5-debug/fixtures/pal.psu"),
             os.path.join(HOME, "workspace/suikoden5-debug/fixtures/pal-eachna-early.psu")]
fixtures = []
if os.path.exists(US_PAYLOAD):
    fixtures.append(("US", open(US_PAYLOAD, "rb").read()))
for p in PAL_FILES:
    if os.path.exists(p):
        pay = SV.read_gamedata_payload(p)
        if pay: fixtures.append((os.path.basename(p), pay))
if not fixtures:
    print("SKIP ct_save_fields real-save sections: no fixtures present")
else:
    for name, pay in fixtures:
        ok = True
        for idx in range(SV.NUM_CHARS):
            cc = SV.read_character(pay, idx)
            for e in cc["held"]:
                if not e["cat"]:
                    if e["id"] or e["qty"] or e["max"] or e["value"]: ok = False
                    continue
                if e["cat"] not in SV.ITEM_CAT_MAXID: ok = False; continue
                if not 1 <= e["id"] <= SV.ITEM_CAT_MAXID[e["cat"]]: ok = False
                if e["max"] != SV.stack_limit(e["cat"], e["id"]): ok = False
                if e["max"] and not 1 <= e["qty"] <= e["max"]: ok = False
                if not e["max"] and e["qty"]: ok = False
            if not 1 <= cc["weaponLevel"] <= 16: ok = False
            if any(u > 9 or m > 9 for u, m in zip(cc["runeUses"],
                                                 cc["runeUsesMax"])): ok = False
        chk(f"{name}: every held entry satisfies the inventory invariants", ok)
        # round trip: change one of each field and put it back, byte-identical
        c0 = SV.read_character(pay, 0)
        edits = {"c0_exp": c0["exp"] + 1, "c0_sp": c0["sp"] + 1,
                 "c0_wlv": 16 if c0["weaponLevel"] != 16 else 15,
                 "c0_ruse0": (c0["runeUses"][0] + 1) % 10}
        g2, _ = SV.apply_gamedata_edits(pay, edits)
        back = {"c0_exp": c0["exp"], "c0_sp": c0["sp"],
                "c0_wlv": c0["weaponLevel"], "c0_ruse0": c0["runeUses"][0]}
        g3, _ = SV.apply_gamedata_edits(g2, back)
        chk(f"{name}: EXP/SP/wlv/ruse round trip is byte-identical", g3 == pay)

    us = dict(fixtures)["US"] if "US" in dict(fixtures) else None
    if us:
        prince = SV.read_character(us, 0)
        chk("US Prince known values (Lv39 save)",
            (prince["exp"], prince["sp"], prince["weaponLevel"],
             prince["runeUses"]) == (717, 5533, 10, [8, 6, 4, 1]),
            str((prince["exp"], prince["sp"], prince["weaponLevel"],
                 prince["runeUses"])))
        chk("US Prince held slot 1 is his Sun Badge accessory",
            (prince["held"][0]["cat"], prince["held"][0]["id"]) == (5, 5)
            and prince["armor"]["accessory"] == 5)
    pal = dict(fixtures).get("pal.psu")
    if pal:
        c17 = SV.read_character(pal, 17)
        chk("PAL late: spent rune uses persist (char 17 Lv4 uses 1 of 2)",
            c17["runeUses"] == [9, 6, 4, 1]
            and c17["runeUsesMax"] == [9, 6, 4, 2],
            str((c17["runeUses"], c17["runeUsesMax"])))

print(f"\nct_save_fields: {n - bad}/{n} checks passed")
sys.exit(1 if bad else 0)
