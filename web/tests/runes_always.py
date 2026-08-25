#!/usr/bin/env python3
"""Always-on rune gate engine test. The gates live in CODE, so this needs a real disc;
it skips cleanly (exit 0) when no NTSC-U ISO is present, as sets_iso.py does."""
import os, sys, glob, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5fields as F, s5patch as P

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

tmp = os.path.join(tempfile.gettempdir(), "s5_runealways_test.bin")
with open(iso, "rb") as f: pristine = f.read(0x6A0000)
with open(tmp, "wb") as o: o.write(pristine)
P.BACKUPS = False; P.RECORD_MODS = False
region = P.set_region_for(tmp)

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

if region != "ntsc-u":
    print("SKIP runes_always: gate offsets are NTSC-U only (found %s)." % region); os.remove(tmp); sys.exit(0)

with P.Iso(tmp) as g:
    d = P.read_rune_always_on(g)
runes = {r["runeId"]: r for r in d["runes"]}
sites = sum(r["siteCount"] for r in d["runes"])

chk("scan window is the rune resolver only", F.RUNE_GATE_HI - F.RUNE_GATE_LO <= 0x2100,
    "0x%x bytes" % (F.RUNE_GATE_HI - F.RUNE_GATE_LO))
chk("85 canonical gates found", sites == 85, "got %d" % sites)
chk("84 distinct runes", len(runes) == 84, "got %d" % len(runes))
chk("hasRune fn matches the mapped address", d["hasRuneFn"] == 0x34DC20, hex(d["hasRuneFn"]))
chk("nothing is forced on a pristine disc", not any(r["allForced"] for r in d["runes"]))

# Every rune the Passives tab offers must be forceable, by name from s5_rune_ids.json.
for rid, want in ((79, "Champion"), (80, "Great Firefly"), (77, "Fortune"),
                  (78, "Prosperity"), (82, "Godspeed")):
    chk("rune %d is forceable and named %r" % (rid, want),
        rid in runes and want in runes[rid]["name"], runes.get(rid, {}).get("name", "MISSING"))
# Raven (83) reads well on paper — "100%% evasion of direct attacks in dungeons" — but has
# no canonical gate. Assert that, so nobody adds it to the UI expecting it to work.
chk("Raven (83) has no forceable gate", 83 not in runes)

# Every gate must be a real beq $?,$zero,<off> pair, never already-zero on a clean disc.
ok = all((s["word1"] >> 26) == 4 and (s["word2"] >> 26) == 4
         for r in d["runes"] for s in r["sites"])
chk("every gate word is a beq", ok)

# --- round trip on Champion's Rune -------------------------------------------------
orig = {s["vaddr"]: [s["word1"], s["word2"]] for s in runes[79]["sites"]}
with P.Iso(tmp, True) as g:
    res = P.write_rune_always_on(g, 79, True)
    chk("enable reports the right site count", res["sites"] == runes[79]["siteCount"] and res["forced"])
    after = P.read_rune_always_on(g)
    a = {r["runeId"]: r for r in after["runes"]}
    chk("Champion's Rune now reads as forced", a[79]["allForced"])
    chk("no other rune was touched", sum(1 for r in after["runes"] if r["runeId"] != 79 and r["allForced"]) == 0)
    chk("forced runes stay listed so they can be switched back", len(after["runes"]) == 84)
    g.f.flush()
    with open(tmp, "rb") as f: patched = f.read()
    diff = [i for i in range(len(pristine)) if patched[i] != pristine[i]]
    # The two branch words become NOPs. Only 6 bytes actually differ, because one byte of
    # each original beq encoding was already 0x00 — so assert on the touched WORDS, not a
    # byte count, and prove nothing outside those two words moved.
    touched = set()
    for s_ in runes[79]["sites"]:
        touched |= set(range(s_["off1"], s_["off1"] + 4)) | set(range(s_["off2"], s_["off2"] + 4))
    chk("only the two gate words changed", set(diff) <= touched, "stray %s" % sorted(set(diff) - touched)[:4])
    chk("both gate words are now NOPs", all(patched[i] == 0 for i in touched))

    P.write_rune_always_on(g, 79, False, orig)
    b = {r["runeId"]: r for r in P.read_rune_always_on(g)["runes"]}
    chk("revert clears the forced flag", not b[79]["allForced"])
    chk("revert keeps the rune forceable", b[79]["siteCount"] == runes[79]["siteCount"])

with open(tmp, "rb") as f: back = f.read()
chk("disc is byte-identical to pristine after revert", back == pristine)

# --- error paths -------------------------------------------------------------------
with P.Iso(tmp, True) as g:
    try:
        P.write_rune_always_on(g, 9999, True); chk("unknown rune is rejected", False)
    except KeyError: chk("unknown rune is rejected", True)
    try:
        P.write_rune_always_on(g, 79, False, None); chk("restore without originals is rejected", False)
    except ValueError: chk("restore without originals is rejected", True)
    try:
        P.write_rune_always_on(g, 79, False, {0xDEAD: [1, 2]}); chk("restore with wrong originals is rejected", False)
    except ValueError: chk("restore with wrong originals is rejected", True)
with open(tmp, "rb") as f: chk("failed writes left the disc untouched", f.read() == pristine)

os.remove(tmp)
print("\n%d/%d passed" % (n - bad, n))
sys.exit(1 if bad else 0)
