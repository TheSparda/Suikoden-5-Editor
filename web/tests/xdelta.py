#!/usr/bin/env python3
"""xdelta (VCDIFF) checks for Editor/s5vcdiff.py and the s5patch CLI.

Two modes:
  (no args)  Build fixtures in a temp dir, run the Python-side checks
             (create + apply round-trips on synthetic pairs and on the real
             ISO's front slice treated as a standalone file, plus the CLI
             subcommands end to end), and print a JSON report for
             tests/xdelta.mjs, which cross-checks the JS implementation
             against these same fixtures.
  apply SRC PATCH OUT   Apply PATCH to SRC with s5vcdiff and print the
             output size (the mjs side uses this to verify JS-built
             patches decode correctly in Python).
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ED = os.path.abspath(os.path.join(HERE, "..", "..", "Editor"))
sys.path.insert(0, ED)
import s5vcdiff as V

if len(sys.argv) > 1 and sys.argv[1] == "apply":
    print(V.apply_patch(sys.argv[2], sys.argv[3], sys.argv[4]))
    sys.exit(0)

checks = []
def chk(name, cond, extra=""):
    checks.append({"name": name, "ok": bool(cond), "extra": str(extra)})

tmp = tempfile.mkdtemp(prefix="s5xdelta-")
def w(name, data):
    p = os.path.join(tmp, name)
    open(p, "wb").write(data)
    return p

import random
random.seed(1234)
src = bytearray(random.randbytes(1024 * 1024))
tgt = bytearray(src)
tgt[100:110] = b"ABCDEFGHIJ"
tgt[500000] ^= 0xFF
tgt[-50:] = bytes(50)
big = bytearray(random.randbytes(20 * 1024 * 1024))
bigt = bytearray(big)
bigt[8 * 1024 * 1024 - 10:8 * 1024 * 1024 + 10] = b"X" * 20
cases = []
for name, s, t in [("small", bytes(src), bytes(tgt)), ("big", bytes(big), bytes(bigt))]:
    sp, tp = w(name + ".src", s), w(name + ".tgt", t)
    pp = os.path.join(tmp, name + ".py.xdelta")
    V.create_patch(sp, tp, pp)
    op = os.path.join(tmp, name + ".out")
    V.apply_patch(sp, pp, op)
    chk(f"python round-trip ({name})", open(op, "rb").read() == t)
    cases.append({"name": name, "src": sp, "tgt": tp, "pyPatch": pp})

# CLI end to end on the small case
c = cases[0]
pp2 = os.path.join(tmp, "cli.xdelta")
r = subprocess.run([sys.executable, os.path.join(ED, "s5patch.py"), "xdelta-create",
                    c["tgt"], "--pristine", c["src"], "--out", pp2],
                   capture_output=True, text=True)
chk("CLI xdelta-create runs", r.returncode == 0, r.stderr.strip()[:200])
op2 = os.path.join(tmp, "cli.out")
r = subprocess.run([sys.executable, os.path.join(ED, "s5patch.py"), "xdelta-apply",
                    c["src"], "--patch", pp2, "--out", op2],
                   capture_output=True, text=True)
chk("CLI xdelta-apply reproduces the target",
    r.returncode == 0 and os.path.exists(op2) and open(op2, "rb").read() == open(c["tgt"], "rb").read(),
    r.stderr.strip()[:200])

# Real data: the pristine ISO's front slice as a standalone file pair.
iso = os.environ.get("S5_ISO") or os.path.abspath(
    os.path.join(HERE, "..", "..", "ISO", "suikoden5-us.iso"))
if os.path.exists(iso):
    with open(iso, "rb") as f:
        slice_bytes = f.read(0x6A0000)
    mod = bytearray(slice_bytes)
    mod[0x48A970 + 3] ^= 0x55            # a character stat byte
    mod[2500000:2500016] = b"OVERLAYEDIT12345"[:16]   # arbitrary bytes
    sp, tp = w("slice.src", slice_bytes), w("slice.tgt", bytes(mod))
    pp = os.path.join(tmp, "slice.py.xdelta")
    psz, changed = V.create_patch(sp, tp, pp)
    op = os.path.join(tmp, "slice.out")
    V.apply_patch(sp, pp, op)
    chk("real ISO slice round-trip", open(op, "rb").read() == bytes(mod),
        f"patch {psz} B for {changed} changed B")
    cases.append({"name": "slice", "src": sp, "tgt": tp, "pyPatch": pp})
else:
    chk("real ISO slice round-trip", True, "skipped: ISO absent")

# Negative control: an adler-carrying patch (the shape xdelta3 emits) must
# verify against the right source and refuse the wrong one. Hand-built:
# one window, COPY 0..9, ADD 4 new bytes, COPY 14..99 of a 100-byte source.
import zlib
def vint(n):
    b = bytearray([n & 0x7F]); n >>= 7
    while n: b.append((n & 0x7F) | 0x80); n >>= 7
    return bytes(reversed(b))
srcb = bytes(range(100))
tgtb = srcb[:10] + b"WXYZ" + srcb[14:]
data = b"WXYZ"
inst = bytearray(); addr = bytearray()
inst.append(19); inst += vint(10); addr += vint(0)     # COPY 10 from 0
inst.append(1);  inst += vint(4)                       # ADD 4
inst.append(19); inst += vint(86); addr += vint(14)    # COPY 86 from 14
block = bytearray(); block += vint(len(tgtb)); block.append(0)
block += vint(len(data)); block += vint(len(inst)); block += vint(len(addr))
block += (zlib.adler32(tgtb) & 0xFFFFFFFF).to_bytes(4, "big") + data + inst + addr
patch = bytearray([0xD6, 0xC3, 0xC4, 0x00, 0x00, 0x05])
patch += vint(100) + vint(0) + vint(len(block)) + block
ap = w("adler.xdelta", bytes(patch))
asp = w("adler.src", srcb)
aop = os.path.join(tmp, "adler.out")
V.apply_patch(asp, ap, aop)
chk("adler patch applies to the right source", open(aop, "rb").read() == tgtb)
wsp = w("adler.wrong", bytes([7]) * 100)
try:
    V.apply_patch(wsp, ap, os.path.join(tmp, "adler.wrong.out"))
    chk("adler patch refuses the wrong source", False, "silently produced output")
except Exception as e:
    chk("adler patch refuses the wrong source", True, str(e)[:80])

print(json.dumps({"checks": checks, "cases": cases, "tmp": tmp}))
