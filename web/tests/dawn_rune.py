#!/usr/bin/env python3
"""Dawn Rune fourth-spell unlock. The patch site is in CODE and the rune records it sits
next to are region-variable, so this needs a real disc; it runs against EVERY ISO in ISO/
and skips cleanly (exit 0) when there isn't one, as runes_always.py does.

What's asserted: the signature finds exactly one site per disc, the site really is the
`jal` delay slot of the counter's setter, the write is one word, nothing else on the disc
moves, and revert is byte-identical. Also that Dawn + Twilight now read as real, editable
grant records rather than synthetic placeholders."""
import os, sys, glob, struct, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5fields as F, s5patch as P

discs = []
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            r = P.region_of(g)
            if r: discs.append((r, cand))
    except Exception:
        continue
if not discs:
    print("SKIP dawn_rune: no Suikoden V ISO in ISO/ (constants are guarded by validate.mjs).")
    sys.exit(0)

P.BACKUPS = False
P.RECORD_MODS = False
n = bad = 0

def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond:
        bad += 1

for region, src in discs:
    print("\n-- %s (%s)" % (region, os.path.basename(src)))
    tmp = os.path.join(tempfile.gettempdir(), "s5_dawn_test_%s.bin" % region)
    with open(src, "rb") as f:
        pristine = f.read(0x6A0000)
    with open(tmp, "wb") as o:
        o.write(pristine)
    chk("region detected", P.set_region_for(tmp) == region)

    # --- the records Dawn/Twilight used to be faked as -----------------------------
    with P.Iso(tmp) as g:
        runes = P.read_runes(g)
        d = P.read_dawn_unlock(g)
    real = [r for r in runes if not r["synthetic"]]
    chk("26 real grant records", len(real) == 26, "got %d" % len(real))
    chk("record 0 is the Dawn Rune, spells 0-3",
        real[0]["name"] == "Dawn Rune" and real[0]["start"] == 0 and real[0]["count"] == 4,
        str(real[0]))
    chk("record 1 is the Twilight Rune, spells 4-7",
        real[1]["name"] == "Twilight Rune" and real[1]["start"] == 4 and real[1]["count"] == 4,
        str(real[1]))
    chk("Fire Rune kept its spells after the base moved",
        real[2]["name"] == "Fire Rune" and real[2]["start"] == 8, str(real[2]))
    chk("last record is still Condemnation",
        real[-1]["name"] == "Rune of Condemnation" and real[-1]["start"] == 78, str(real[-1]))
    chk("Dawn and Twilight are no longer synthesized",
        not any(r["name"] in ("Dawn Rune", "Twilight Rune")
                for r in runes if r["synthetic"]))
    chk("the Dawn Rune is where the UI looks for it", F.DAWN_RUNE_INDEX == 0)

    # --- the patch site -------------------------------------------------------------
    chk("signature found exactly one site", d["found"], str(d))
    if not d["found"]:
        os.remove(tmp)
        continue
    chk("pristine disc is vanilla", d["count"] == 0 and d["stock"])
    off = d["off"]
    # The site must be the delay slot of a `jal`, and the word before that the clamp's
    # own ceiling — otherwise the signature matched something that only looks like it.
    jal = struct.unpack_from("<I", pristine, off - 4)[0]
    anchor = struct.unpack_from("<I", pristine, off - 8)[0]
    chk("site follows a jal", (jal >> 26) == 3, "0x%08X" % jal)
    chk("site follows the clamp ceiling", anchor == F.DAWN_ANCHOR_WORD, "0x%08X" % anchor)
    # ...and that jal must land on a 2-instruction leaf that stores a byte via $gp.
    setter = ((off - 4) + F.VADDR_DELTA & 0xF0000000) | ((jal & 0x3FFFFFF) << 2)
    sw = struct.unpack_from("<I", pristine, setter - F.VADDR_DELTA + 4)[0]
    ret = struct.unpack_from("<I", pristine, setter - F.VADDR_DELTA)[0]
    chk("setter is `jr ra`", ret == 0x03E00008, "0x%08X" % ret)
    chk("setter stores a byte through $gp",
        (sw >> 26) == 0x28 and ((sw >> 21) & 31) == 28, "0x%08X" % sw)

    # --- round trip -----------------------------------------------------------------
    with P.Iso(tmp, True) as g:
        res = P.write_dawn_unlock(g, F.DAWN_MAX)
        chk("write reports the site and count", res["off"] == off and res["count"] == F.DAWN_MAX)
        after = P.read_dawn_unlock(g)
        chk("reads back as unlocked", after["count"] == F.DAWN_MAX and not after["stock"])
        chk("the site is still findable once patched", after["found"] and after["off"] == off)
    with open(tmp, "rb") as f:
        patched = f.read()
    diff = [i for i in range(len(pristine)) if patched[i] != pristine[i]]
    # Only 3 bytes differ: byte 1 of `andi a0,v1,255` is already 0x00 in both encodings.
    chk("only the one instruction word changed", set(diff) <= set(range(off, off + 4)),
        "stray %s" % ["%06X" % x for x in sorted(set(diff) - set(range(off, off + 4)))][:4])
    chk("the word is `addiu a0,zero,4`",
        struct.unpack_from("<I", patched, off)[0] == (F.DAWN_FORCE_WORD | F.DAWN_MAX))

    # partial unlocks work too, even though the UI only offers the toggle
    with P.Iso(tmp, True) as g:
        P.write_dawn_unlock(g, 2)
        chk("a partial count round-trips", P.read_dawn_unlock(g)["count"] == 2)
        P.write_dawn_unlock(g, 0)
        back = P.read_dawn_unlock(g)
        chk("revert reads as vanilla", back["count"] == 0 and back["stock"])
    with open(tmp, "rb") as f:
        chk("disc is byte-identical to pristine after revert", f.read() == pristine)

    # --- error paths ------------------------------------------------------------------
    with P.Iso(tmp, True) as g:
        for bogus in (-1, F.DAWN_MAX + 1, 99):
            try:
                P.write_dawn_unlock(g, bogus)
                chk("count %s is rejected" % bogus, False)
            except ValueError:
                chk("count %s is rejected" % bogus, True)
    with open(tmp, "rb") as f:
        chk("failed writes left the disc untouched", f.read() == pristine)
    os.remove(tmp)

print("\n%d/%d passed" % (n - bad, n))
sys.exit(1 if bad else 0)
