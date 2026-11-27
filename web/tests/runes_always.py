#!/usr/bin/env python3
"""Rune gate scan invariants. The gates live in CODE, so this needs a real disc;
it skips cleanly (exit 0) when no NTSC-U ISO is present, as sets_iso.py does.

Until v1.26.0 a Passives view forced these gates through
s5patch.read_rune_always_on / write_rune_always_on. The merge into Code
patches removed those two functions: the s5toggles registry is the only
writer now, and its own _gate_scan mirrors the reference scan kept here.
What this suite still guards is the scan itself (window, gate census,
shapes, the five perk runes present, Raven absent) and the agreement
between the two scans, which is what the registry's correctness rests on.
"""
import os, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5fields as F, s5patch as P, s5toggles as T

iso = None
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            if P.region_of(g) == "ntsc-u": iso = cand; break
    except Exception:
        continue
if not iso:
    print("SKIP runes_always: no NTSC-U ISO in ISO/ (constants are guarded by validate.mjs).")
    sys.exit(0)

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

with P.Iso(iso) as g:
    gates = P._rune_gate_sites(g)
    names = F.res_json("s5_rune_ids.json")

by_rune = {}
for gt in gates:
    by_rune.setdefault(gt["runeId"], []).append(gt)

chk("scan window is the rune resolver only", F.RUNE_GATE_HI - F.RUNE_GATE_LO <= 0x2100,
    "0x%x bytes" % (F.RUNE_GATE_HI - F.RUNE_GATE_LO))
chk("85 canonical gates found", len(gates) == 85, "got %d" % len(gates))
chk("84 distinct runes", len(by_rune) == 84, "got %d" % len(by_rune))
chk("hasRune fn matches the mapped address", F.RUNE_HAS_FN == 0x34DC20, hex(F.RUNE_HAS_FN))
chk("nothing is forced on a pristine disc", not any(gt["forced"] for gt in gates))

# Every rune the registry offers as a perk must have a gate, by name.
for rid, want in ((79, "Champion"), (80, "Great Firefly"), (77, "Fortune"),
                  (78, "Prosperity"), (82, "Godspeed")):
    chk("rune %d has a gate and is named %r" % (rid, want),
        rid in by_rune and want in names[rid], names[rid] if rid < len(names) else "MISSING")
# Raven (83) reads well on paper but has no canonical gate. Assert that, so
# nobody adds it to the registry expecting it to work.
chk("Raven (83) has no gate", 83 not in by_rune)

# Every gate must be a real beq $?,$zero,<off> pair on a clean disc.
ok = all((gt["word1"] >> 26) == 4 and (gt["word2"] >> 26) == 4 for gt in gates)
chk("every gate word is a beq", ok)

# The registry's own scan must agree with this reference scan on the five
# perk runes: same site count, same span offsets.
with P.Iso(iso) as g:
    for rid in (77, 78, 79, 80, 82):
        theirs = sorted(sp["off"] for s in T._gate_scan(g, rid) for sp in s["spans"])
        mine = sorted(o for gt in by_rune[rid] for o in (gt["off1"], gt["off2"]))
        chk("registry gate scan agrees with the reference scan for rune %d" % rid,
            theirs == mine, "%d spans" % len(mine))

print("\n%d/%d passed" % (n - bad, n))
sys.exit(1 if bad else 0)
