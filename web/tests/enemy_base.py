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

    # ---- (#40) the drop block: +0x28 attribute flags and the +0x54 food dish --------
    # The old "100% drop flag" label was false (the byte gates battle actions, never
    # drops) and the sixth drop slot at +0x54 was not exposed at all. Writes go to a
    # throwaway front slice of the disc (the first 0x6A0000 bytes hold the whole enemy
    # table, as in dawn_rune.py), never to the real ISO.
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), f"s5_enemy_test_{region}.bin")
    with open(path, "rb") as fh:
        front = fh.read(0x6A0000)
    with open(tmp, "wb") as fh:
        fh.write(front)
    try:
        chk(f"{region}: slice keeps its region", P.set_region_for(tmp) == region)
        with P.Iso(tmp, True) as g:
            labels = [f["label"] for f in P.read_enemy(g, 1)]
            chk(f"{region}: +0x28 is exposed as Attribute flags",
                "Attribute flags" in labels and "100% drop flag" not in labels)
            chk(f"{region}: +0x54 is exposed as Food dish (Cook)",
                "Food dish (Cook)" in labels)
            def field(eid, label):
                return {f["label"]: f["value"] for f in P.read_enemy(g, eid)}[label]
            chk(f"{region}: Holly Boy's food dish is Tomato Salad (category 9, item 15)",
                field(1, "Food dish (Cook)") == (9 | 15 << 8),
                f"got {field(1, 'Food dish (Cook)'):#x}")
            chk(f"{region}: Diamond Sun A's food dish is empty",
                field(117, "Food dish (Cook)") == 0,
                f"got {field(117, 'Food dish (Cook)'):#x}")
            empties = sum(1 for eid in range(F.ENEMY_MAX)
                          if field(eid, "Food dish (Cook)") == 0)
            chk(f"{region}: 62 enemies carry no food dish", empties == 62,
                f"got {empties}")
            # Round trip: writing every record's own +0x28 and +0x54 values back must
            # leave the whole table byte-identical.
            before = g.rd(P.enemy_addr(0), F.ENEMY_MAX * F.ENEMY_STRIDE)
            for eid in range(F.ENEMY_MAX):
                P.write_enemy_field(g, eid, "Attribute flags", field(eid, "Attribute flags"))
                P.write_enemy_field(g, eid, "Food dish (Cook)", field(eid, "Food dish (Cook)"))
            chk(f"{region}: +0x28/+0x54 write-back is byte-identical (all 219 records)",
                g.rd(P.enemy_addr(0), F.ENEMY_MAX * F.ENEMY_STRIDE) == before)
            # Changed values must land on exactly the two fields' bytes, nowhere else.
            P.write_enemy_field(g, 1, "Attribute flags", 0xA5)
            P.write_enemy_field(g, 1, "Food dish (Cook)", 9 | 20 << 8)
            chk(f"{region}: edited Attribute flags reads back",
                field(1, "Attribute flags") == 0xA5,
                f"got {field(1, 'Attribute flags'):#x}")
            chk(f"{region}: edited Food dish reads back",
                field(1, "Food dish (Cook)") == (9 | 20 << 8),
                f"got {field(1, 'Food dish (Cook)'):#x}")
            after = g.rd(P.enemy_addr(0), F.ENEMY_MAX * F.ENEMY_STRIDE)
            diff = [i for i in range(len(before)) if after[i] != before[i]]
            # The dish edit kept category 9, so only its item byte (+0x55) differs.
            want = [1 * F.ENEMY_STRIDE + 0x28, 1 * F.ENEMY_STRIDE + 0x55]
            chk(f"{region}: the edits touched only the two fields' bytes", diff == want,
                f"diff at {[hex(i) for i in diff]}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

print(f"\n{n - bad}/{n} passed")
sys.exit(1 if bad else 0)
