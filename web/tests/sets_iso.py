#!/usr/bin/env python3
"""Equipment-set engine test. Set bonuses live in CODE, so this needs a real disc:
it skips cleanly (exit 0) when no ISO is present, which is the normal CI case — the
constants themselves are guarded by validate.mjs instead (playbook B19).

Ground truth is the Suikosource "Armor Sets" guide (Guides/), which independently
documents every set's members and bonus; the assertions below encode it."""
import os, sys, glob, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))

import s5fields as F, s5patch as P

# Set offsets are NTSC-U only, so pick a disc by its serial rather than by filename.
iso = None
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            if P.region_of(g) == "ntsc-u": iso = cand; break
    except Exception:
        continue
if not iso:
    print("SKIP sets_iso: no NTSC-U ISO in ISO/ (constants are guarded by validate.mjs).")
    sys.exit(0)

# Work on a truncated copy so the real disc is never touched.
tmp = os.path.join(tempfile.gettempdir(), "s5_sets_test.bin")
with open(iso, "rb") as f, open(tmp, "wb") as o:
    o.write(f.read(0x6A0000))
P.BACKUPS = False; P.RECORD_MODS = False
region = P.set_region_for(tmp)

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

if region != "ntsc-u":
    print("SKIP sets_iso: set offsets are NTSC-U only (found %s)." % region)
    os.remove(tmp); sys.exit(0)

with P.Iso(tmp) as g:
    d = P.read_sets(g)
sets = {s["name"]: s for s in d["sets"]}
chk("all 9 sets parsed", len(d["sets"]) == 9, str(sorted(sets)))

# --- members, straight from the Suikosource guide (equip ids) ---
EXPECT_MEMBERS = {
    "Fish":       [("head",27),("body",60),("arm",30),("foot",24),("accessory",8)],
    "Prosperity": [("head",11),("body",28),("arm",5),("foot",12),("accessory",47)],
    "Destiny":    [("head",12),("body",10),("arm",8),("foot",5),("accessory",3)],
    "Guardian":   [("head",21),("body",55),("arm",22),("foot",17),("accessory",19)],
    "Classic":    [("body",24),("arm",1),("foot",16),("accessory",23)],
    "Sun":        [("head",25),("body",46),("arm",24),("foot",22),("accessory",49)],
}
for nm, want in EXPECT_MEMBERS.items():
    got = [(m["slot"], m["id"]) for m in sets[nm]["members"]]
    chk("%s members match the guide" % nm, got == want, str(got))

# --- bonuses, straight from the guide ---
EXPECT_BONUS = {
    "Classic":  [(20, 10), (40, 5)],      # HP +10, ATK +5
    "Destiny":  [(20, 50)],               # HP +50
    "Guardian": [(46, 10)],               # MDEF +10
    "Samurai":  [(304, 10), (305, 10)],   # Crit +10%, Double crit +10%
}
for nm, want in EXPECT_BONUS.items():
    got = [(e["charOff"], e["value"]) for e in sets[nm]["effects"] if e["kind"] == "add"]
    chk("%s bonus matches the guide" % nm, got[:len(want)] == want, str(got))
chk("Fish sets Water affinity to 6 (== rank S)",
    [(e["kind"], e["charOff"], e["value"]) for e in sets["Fish"]["effects"]] == [("set", 256, 6)])
chk("Sun grants 8 x +5 (all stats)",
    len(sets["Sun"]["effects"]) == 8 and all(e["value"] == 5 for e in sets["Sun"]["effects"]))
chk("Windspun uses float math (read-only)",
    all(e["kind"] == "float" for e in sets["Windspun"]["effects"]))
chk("Prosperity/Pale Moon apply elsewhere (jump-table no-ops)",
    sets["Prosperity"]["noop"] and sets["Pale Moon"]["noop"])
chk("field labels resolved", F.SET_FIELD_HINT.get(20) == "HP" and F.SET_FIELD_HINT.get(46) == "MDEF")

# --- writes: membership, magnitude, and effect reassignment (custom sets) ---
with P.Iso(tmp, writable=True) as g:
    P.write_set_member(g, sets["Prosperity"]["index"], "head", 25)          # require Sun Helm
    P.write_set_bonus(g, sets["Destiny"]["index"], 0, 999)                  # HP +50 -> +999
    P.write_set_handler(g, sets["Prosperity"]["index"], sets["Sun"]["handler"])  # custom bonus
    P.write_armor_summary(g, "head", 20, "SET TEST")                        # in-game text
with P.Iso(tmp) as g:
    d2 = P.read_sets(g)
    item = P.read_armor_item(g, "head", 20)
s2 = {s["index"]: s for s in d2["sets"]}
pr = s2[sets["Prosperity"]["index"]]
chk("member write took", next(m["id"] for m in pr["members"] if m["slot"] == "head") == 25)
chk("magnitude write took", s2[sets["Destiny"]["index"]]["effects"][0]["value"] == 999)
chk("effect reassignment took (custom set)",
    pr["handler"] == sets["Sun"]["handler"] and len(pr["effects"]) == 8 and not pr["noop"])
chk("description write took", item["summary"].startswith("SET TEST"), item["summary"][:24])
chk("neighbouring set untouched",
    [(e["charOff"], e["value"]) for e in s2[sets["Classic"]["index"]]["effects"]] == [(20, 10), (40, 5)])
# revert the magnitude to prove edits are reversible
with P.Iso(tmp, writable=True) as g:
    P.write_set_bonus(g, sets["Destiny"]["index"], 0, 50)
with P.Iso(tmp) as g:
    d3 = P.read_sets(g)
chk("magnitude revert", next(s for s in d3["sets"]
    if s["index"] == sets["Destiny"]["index"])["effects"][0]["value"] == 50)

os.remove(tmp)
print("\n%d/%d passed" % (n - bad, n))
sys.exit(1 if bad else 0)
