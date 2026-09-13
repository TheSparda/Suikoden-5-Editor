#!/usr/bin/env python3
"""Enemy-table base check. Needs a real disc: it skips cleanly (exit 0) when no ISO
is present, which is the normal CI case, as sets_iso.py does.

Why this exists: the PAL enemy base shipped as 0x4A4347 — one byte short of a record
boundary — and every PAL enemy read came back garbage. Nothing caught it, because the
enemy tests all run against a fabricated slice built from our own constants, which
agrees with itself no matter what the base is. Only a real disc can fail this.

The two anchors below are byte-identical across NTSC-U and PAL (ids 1..219 match
exactly on both discs), so one assertion guards both regions' bases.

It now also guards the other end. F.ENEMY_MAX was 584 where the table holds 219
records, so the editor listed ~430 "enemies" — the extra ~210 were unrelated ELF
bytes (text, pointer tables, and the skill-effect table) read through the enemy
record layout, which is why they showed impossible Levels and five-digit stats, and
why the scaler wrote scaled junk over them. Only a real disc can fail these too.
"""
import os, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))

import s5fields as F, s5patch as P
P.BACKUPS = False; P.RECORD_MODS = False

# id -> (Level, HP, Potch reward, Skill Pts reward). Verified on both discs.
ANCHORS = {1: (10, 80, 30, 10),        # Holly Boy
           4: (45, 1800, 2500, 135)}   # Nariqua
KEYS = ("Level", "HP", "Potch reward", "Skill Pts reward")

discs = []
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            r = P.region_of(g)
            if r: discs.append((r, cand))
    except Exception:
        continue
if not discs:
    print("SKIP enemy_base: no Suikoden V ISO in ISO/.")
    sys.exit(0)

n = bad = 0
def chk(label, ok, detail=""):
    global n, bad
    n += 1
    if not ok: bad += 1
    print(f"{'PASS' if ok else 'FAIL'} {label}{('  ' + detail) if detail else ''}")

seen = set()
for region, path in discs:
    if region in seen: continue        # one disc per region is enough
    seen.add(region)
    P.set_region_for(path)
    with P.Iso(path) as g:
        for eid, want in ANCHORS.items():
            got = {f["label"]: f["value"] for f in P.read_enemy(g, eid)}
            got = tuple(got[k] for k in KEYS)
            chk(f"{region}: enemy {eid} reads {want}", got == want,
                "" if got == want else f"got {got} (base {F.ENEMY_BASE:#x})")
        # A correct base lands every record on a stride boundary, so the roster is a
        # sane size. A base off by a byte shreds this (the bad PAL base gave 446).
        listed = P.read_enemies(g)
        chk(f"{region}: roster size is plausible", 210 <= len(listed) <= 219,
            f"{len(listed)} listed")
        # The table ENDS at Bahram (id 218). It used to be scanned 365 records too far,
        # and every extra row was unrelated ELF data offered as an editable enemy.
        top = max(e["id"] for e in listed)
        chk(f"{region}: nothing is listed past the last enemy", top <= 218, f"top id {top}")
        # The proof that fixes the end: the bytes the old scan offered as "Enemy 221"+
        # are ELF text. On the reporter's disc "Enemy 223" decoded to the memory-card
        # prompt ("...lease select MEMORY CARD sl..."). A stat table cannot hold a long
        # printable-ASCII run, so that run is the assertion (which string it is, and so
        # where the ELF puts it, is region-specific — the run is not).
        stray = g.rd(P.enemy_addr(219), 8 * F.ENEMY_STRIDE)
        run = best = 0
        for byte in stray:
            run = run + 1 if 32 <= byte < 127 else 0
            best = max(best, run)
        chk(f"{region}: past the table is text, not enemies", best >= 16,
            f"longest printable run {best} at {P.enemy_addr(219):#x}")
        # ...and the far end of the old scan sat on top of the verified skill-effect table.
        chk(f"{region}: the table clears the skill-effect table",
            F.ENEMY_BASE + F.ENEMY_MAX * F.ENEMY_STRIDE <= F.SKILLFX_BASE,
            f"enemies end {F.ENEMY_BASE + F.ENEMY_MAX * F.ENEMY_STRIDE:#x}, skillfx {F.SKILLFX_BASE:#x}")

print(f"\n{n - bad}/{n} passed")
sys.exit(1 if bad else 0)
