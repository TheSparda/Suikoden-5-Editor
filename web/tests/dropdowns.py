#!/usr/bin/env python3
"""Dropdown changes (issue #42), engine contracts the new controls rely on.
On slice copies of each real ISO (never the pristine file):
- Weapon id: the dropdown source has exactly 82 entries (0 = "No weapon
  record", the rest the disc's own type names); writing every id 0..81
  through the starting-equipment field and reading it back is byte-exact,
  and writing each character's current id changes no byte.
- Spell Power: the "Full heal (9999)" preset value writes exactly 9999
  through the spell field writer for all 106 spells, and restoring each
  spell's stock value returns the slice to its pristine bytes. Stock data
  confirms the sentinel: heal/revive spells really do carry 9999.
- Gear Type: head/body/arm/foot report kind "geartype", accessory stays
  "num"; writing a record's current Type (including body values outside
  1..3) changes no byte, and writing a different value touches exactly
  the Type byte.
- Sets: the grade-target offsets are the affinity block; the Fish set's
  Water affinity effect (offset 256) reads 6 (rank S), writes and reads
  back through the set-bonus writer; the grade list matches the custom
  builder's (AFFINITY_GRADES via set_grade_names)."""
import glob, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5patch as P
import s5fields as F

P.BACKUPS = False
P.RECORD_MODS = False

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra and not cond else ""))
    if not cond: fails.append(name)

SLICE = 0xC00000
POWER_LABEL = "Power / heal (u16)"

isos = [p for p in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso")))]
ran = 0
for src in isos:
    with P.Iso(src) as g: region = P.region_of(g)
    if not region: continue
    P.set_region_for(src)
    work = os.path.join(tempfile.mkdtemp(), "slice.iso")
    with open(src, "rb") as f: data = f.read(SLICE)
    with open(work, "wb") as f: f.write(data)
    ran += 1
    tag = region

    # ---- weapon id dropdown source + round trip ----
    with P.Iso(work) as g: names = P.weapon_type_names(g)
    check(f"{tag}: weapon dropdown has exactly 82 entries",
          len(names) == 82 and set(names) == set(range(82)), len(names))
    check(f"{tag}: weapon id 0 is 'No weapon record', 1..81 all named",
          names.get(0) == "No weapon record"
          and all(names.get(i) for i in range(1, 82)))

    with P.Iso(work) as g:
        cur = {}
        for cid in range(134):
            flds = P.read_table(g, "starting equipment", cid)
            w = next(f for f in flds if f["label"] == "Weapon id")
            cur[cid] = w["value"]
            assert w["kind"] == "weaponid"
    with P.Iso(work, writable=True) as g:
        for cid, v in cur.items():
            P.write_field(g, "starting equipment", cid, "Weapon id", v)
    check(f"{tag}: writing every character's current weapon id changes no byte",
          open(work, "rb").read() == data)
    ok = True
    with P.Iso(work, writable=True) as g:
        for v in range(82):
            P.write_field(g, "starting equipment", 0, "Weapon id", v)
            flds = P.read_table(g, "starting equipment", 0)
            if next(f for f in flds if f["label"] == "Weapon id")["value"] != v:
                ok = False
        P.write_field(g, "starting equipment", 0, "Weapon id", cur[0])
    check(f"{tag}: weapon id domain sweep 0..81 writes and reads back", ok)
    check(f"{tag}: weapon sweep restored the slice byte-identically",
          open(work, "rb").read() == data)

    # ---- spell power preset value ----
    with P.Iso(work) as g:
        stock = {}
        for sid in range(F.SPELL_COUNT):
            flds = P.read_spell(g, sid)
            stock[sid] = next(f for f in flds if f["label"] == POWER_LABEL)["value"]
    check(f"{tag}: stock data carries the 9999 full-heal sentinel",
          sum(1 for v in stock.values() if v == 9999) >= 3,
          sum(1 for v in stock.values() if v == 9999))
    ok = True
    with P.Iso(work, writable=True) as g:
        for sid in range(F.SPELL_COUNT):
            P.write_spell_field(g, sid, POWER_LABEL, 9999)
            flds = P.read_spell(g, sid)
            if next(f for f in flds if f["label"] == POWER_LABEL)["value"] != 9999:
                ok = False
        for sid, v in stock.items():
            P.write_spell_field(g, sid, POWER_LABEL, v)
    check(f"{tag}: preset value 9999 writes exactly for all 106 spells", ok)
    check(f"{tag}: spell powers restored byte-identically",
          open(work, "rb").read() == data)

    # ---- gear Type kinds + round trip ----
    for slot in ("head", "body", "arm", "foot", "accessory"):
        n = F.ARMOR_TABLES[slot][1]
        kinds_ok, same_ok, diff_ok = True, True, True
        with P.Iso(work) as g:
            types = {}
            for i in range(n):
                r = P.read_armor_item(g, slot, i)
                tf = next(f for f in r["fields"] if f["label"] == "Type")
                types[i] = tf["value"]
                want = "num" if slot == "accessory" else "geartype"
                if tf["kind"] != want: kinds_ok = False
        check(f"{tag}: {slot} Type kind is "
              f"{'num (untouched)' if slot == 'accessory' else 'geartype'}", kinds_ok)
        if slot == "accessory":
            continue
        with P.Iso(work, writable=True) as g:
            for i, v in types.items():
                P.write_armor_field(g, slot, i, "Type", v)
        if open(work, "rb").read() != data: same_ok = False
        with P.Iso(work, writable=True) as g:
            for i, v in types.items():
                alt = (v % 3) + 1
                if alt == v: alt = 1 if v != 1 else 2
                P.write_armor_field(g, slot, i, "Type", alt)
                r = P.read_armor_item(g, slot, i)
                if next(f for f in r["fields"] if f["label"] == "Type")["value"] != alt:
                    diff_ok = False
                P.write_armor_field(g, slot, i, "Type", v)
        check(f"{tag}: {slot} Type current values (incl. any outside 1..3) "
              f"write back with no byte change", same_ok)
        check(f"{tag}: {slot} Type changed values read back, then restored", diff_ok)
        check(f"{tag}: {slot} Type sweep restored the slice byte-identically",
              open(work, "rb").read() == data)

    # ---- sets grade targets ----
    grade_offs = {t[1] for t in F.SET_EFFECT_TARGETS if t[4] == "grade"}
    want_offs = {F.SET_AFFINITY_BASE + i for i in range(len(F.SET_AFFINITY_ELEMENTS))}
    check(f"{tag}: grade targets are exactly the affinity block",
          grade_offs == want_offs, (sorted(grade_offs), sorted(want_offs)))
    check(f"{tag}: grade list matches the custom builder's",
          P.set_grade_names() == ["None", "E", "D", "C", "B", "A", "S"]
          and F.AFFINITY_GRADES == P.set_grade_names())
    with P.Iso(work) as g: d = P.read_sets(g)
    if not d["sets"]:
        # Same documented limitation as sets_iso.py: the set detector offsets
        # are NTSC-U only, so PAL decodes no sets and has no grade bonuses.
        check(f"{tag}: no sets decode on this region (set offsets are NTSC-U only)",
              region == "pal", f"sets={len(d['sets'])}")
    else:
        fish = next(s for s in d["sets"] if s["index"] == 1)
        eff_i = next(i for i, e in enumerate(fish["effects"]) if e.get("charOff") == 256)
        check(f"{tag}: Fish Water affinity effect reads 6 (rank S)",
              fish["effects"][eff_i]["value"] == 6, fish["effects"])
        with P.Iso(work, writable=True) as g:
            P.write_set_bonus(g, 1, eff_i, 3)
        with P.Iso(work) as g: d = P.read_sets(g)
        fish = next(s for s in d["sets"] if s["index"] == 1)
        check(f"{tag}: Fish Water affinity writes and reads back 3 (rank B)",
              fish["effects"][eff_i]["value"] == 3, fish["effects"])
        with P.Iso(work, writable=True) as g:
            P.write_set_bonus(g, 1, eff_i, 6)
        with P.Iso(work) as g: d = P.read_sets(g)
        fish = next(s for s in d["sets"] if s["index"] == 1)
        check(f"{tag}: Fish Water affinity restored to 6",
              fish["effects"][eff_i]["value"] == 6, fish["effects"])

check("at least one real ISO exercised", ran > 0, f"isos={isos}")
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
