#!/usr/bin/env python3
"""Verify s5save.STACK_LIMITS against the stack-limit bytes in the disc's ELF.

The game's inventory append functions copy a per-item stack limit into each
inventory entry's +7 byte, for item categories 7, 9, 10 and 11 only (every
other category stores 0). The bytes live in the ELF (SLUS-212.91, vaddr =
ISO file offset + 0x52700) at:

  cat 7  (Rune Pieces):  record+20 of the 76-stride table at vaddr 0x51EE40
  cat 9  (Healing):      the byte at vaddr 0x51F674 + 88*id
  cat 10 (Scrolls):      record+19 of the 76-stride table at vaddr 0x520580
  cat 11 (Party Items):  record+20 of the 76-stride table at vaddr 0x5209B0

These formulas were pinned by cross-checking all 283 inventory entries in two
real saves (US and PAL): every entry's +7 byte equals the byte above. This
script re-derives the tables from the disc and compares them with the copy
embedded in s5save.py, so the embedded copy can be regenerated or audited.

Run:  python3 Editor/build_stack_limits.py [path-to-iso]
      (default: <repo>/ISO/suikoden5-us.iso, or $S5_ISO)
Exit code 0 = embedded table matches the disc; 1 = mismatch or no ISO.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import s5save as SV

K = 0x52700  # vaddr = ISO file offset + K

SOURCES = {
    7:  (0x51EE40, 76, 20, 25),
    9:  (0x51F674, 88, 0, 41),
    10: (0x520580, 76, 19, 12),
    11: (0x5209B0, 76, 20, 2),
}


def derive(iso_path):
    out = {}
    with open(iso_path, "rb") as f:
        for cat, (anchor, stride, off, n) in SOURCES.items():
            f.seek(anchor - K)
            tab = f.read(stride * (n + 1))
            out[cat] = [tab[stride * i + off] for i in range(1, n + 1)]
    return out


def main():
    iso = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("S5_ISO")
           or os.path.join(os.path.dirname(HERE), "ISO", "suikoden5-us.iso"))
    if not os.path.exists(iso):
        print(f"no ISO at {iso}; cannot verify STACK_LIMITS")
        return 1
    got = derive(iso)
    bad = 0
    for cat in sorted(SOURCES):
        want = SV.STACK_LIMITS.get(cat)
        if got[cat] == want:
            print(f"cat {cat}: OK ({len(got[cat])} limits)")
        else:
            bad += 1
            print(f"cat {cat}: MISMATCH\n  disc:     {got[cat]}\n  embedded: {want}")
    print("STACK_LIMITS matches the disc" if not bad else "STACK_LIMITS STALE")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
