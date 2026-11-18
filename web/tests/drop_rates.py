#!/usr/bin/env python3
"""Global drop-rate editor (issue #40): the five slot rates and the dish rate
are immediates in the BATLE overlay's drop processor; the Cook chances are
data in the support-skill table. On slice copies of each real ISO (never the
pristine file): stock reads are 40/20/10/5/1, dish 100, Cook 20/30; a custom
write reads back; the byte diff stays inside the BATLE sector slot, its
directory record and the four Cook bytes; restore returns the stock values
with the overlay payload byte-identical to stock; out-of-range values and
foreign layouts are refused before anything is written."""
import glob, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5patch as P

P.BACKUPS = False
P.RECORD_MODS = False

fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ((" :: " + str(extra)) if extra and not cond else ""))
    if not cond: fails.append(name)

STOCK = {"slots": [40, 20, 10, 5, 1], "dish": 100, "cook": [20, 30]}
CUSTOM = {"slots": [99, 1, 50, 25, 75], "dish": 99, "cook": [5, 95]}

isos = [p for p in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso")))]
ran = 0
for src in isos:
    with P.Iso(src) as g: region = P.region_of(g)
    if not region: continue
    ov = next(o for o in P.list_overlays(src) if o["name"] == "BATLE.ROM")
    slot = ((ov["size"] + 2047) // 2048) * 2048
    end = ov["lba"] * 2048 + slot
    tb = P._SKILL_TABLE_ISO[region]
    assert end > tb + 0x1600, "slice must cover the skill table too"
    work = os.path.join(tempfile.mkdtemp(), "slice.iso")
    with open(src, "rb") as f: data = f.read(end)
    with open(work, "wb") as f: f.write(data)
    rec_off, rec_len = P._find_ovl_dirrec(src, "BATLE.ROM")
    cook6 = tb + 0x98 * 36 + 6 * 2 + 0x12
    ran += 1
    tag = region

    with P.Iso(work) as g: r = P.read_drop_rates(g)
    check(f"{tag}: stock drop rates read from the real ISO",
          r["slots"] == STOCK["slots"] and r["dish"] == STOCK["dish"]
          and r["cook"] == STOCK["cook"], r)

    with P.Iso(work, writable=True) as g:
        P.write_drop_rates(g, CUSTOM["slots"], CUSTOM["dish"], CUSTOM["cook"])
    with P.Iso(work) as g: r = P.read_drop_rates(g)
    check(f"{tag}: custom rates write and read back",
          r["slots"] == CUSTOM["slots"] and r["dish"] == CUSTOM["dish"]
          and r["cook"] == CUSTOM["cook"], r)

    now = open(work, "rb").read()
    diffs = [i for i in range(end) if now[i] != data[i]]
    base = ov["lba"] * 2048
    def allowed(i):
        return (base <= i < base + slot or rec_off <= i < rec_off + rec_len
                or cook6 <= i < cook6 + 4)
    check(f"{tag}: byte diff confined to the BATLE slot, its directory record "
          f"and the Cook bytes", bool(diffs) and all(allowed(i) for i in diffs),
          [hex(i) for i in diffs if not allowed(i)][:5])

    with P.Iso(work, writable=True) as g: P.restore_drop_rates(g)
    with P.Iso(work) as g:
        r = P.read_drop_rates(g); payload = P._batle_bin(g)
    with P.Iso(src) as g: stock_payload = P._batle_bin(g)
    check(f"{tag}: restore returns the stock rates",
          r["slots"] == STOCK["slots"] and r["dish"] == STOCK["dish"]
          and r["cook"] == STOCK["cook"], r)
    check(f"{tag}: restored overlay payload is byte-identical to stock",
          payload == stock_payload)

    def refused(slots, dish, cook):
        try:
            with P.Iso(work, writable=True) as g:
                P.write_drop_rates(g, slots, dish, cook)
            return False
        except ValueError:
            return True
    check(f"{tag}: slot rate 100 refused", refused([100, 20, 10, 5, 1], 100, [20, 30]))
    check(f"{tag}: Cook chance 100 refused", refused([40, 20, 10, 5, 1], 100, [20, 100]))
    check(f"{tag}: dish rate 101 refused", refused([40, 20, 10, 5, 1], 101, [20, 30]))
    check(f"{tag}: negative rate refused", refused([40, 20, 10, 5, -1], 100, [20, 30]))
    with P.Iso(work) as g: r = P.read_drop_rates(g)
    check(f"{tag}: refused writes changed nothing",
          r["slots"] == STOCK["slots"] and r["dish"] == STOCK["dish"]
          and r["cook"] == STOCK["cook"], r)

    # Foreign layouts are refused: corrupt the skill-table signature in one
    # copy, and a drop-processor pair instruction in another.
    bad1 = os.path.join(tempfile.mkdtemp(), "bad1.iso")
    b = bytearray(data); b[tb + 0x90 * 36 + 0x12 + 2] ^= 0xFF
    open(bad1, "wb").write(bytes(b))
    try:
        with P.Iso(bad1) as g: P.read_drop_rates(g)
        check(f"{tag}: foreign skill table refused", False)
    except ValueError:
        check(f"{tag}: foreign skill table refused", True)

    bad2 = os.path.join(tempfile.mkdtemp(), "bad2.iso")
    with open(bad2, "wb") as f: f.write(data)
    with P.Iso(bad2) as g: bin_data = bytearray(P._batle_bin(g))
    bin_data[0xF728] ^= 0xFF                      # slot 1 pair-address word
    fd, tmpbin = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as fh: fh.write(bytes(bin_data))
    with P.Iso(bad2, writable=True) as g: P.reinsert_overlay(g, "BATLE.ROM", tmpbin)
    os.remove(tmpbin)
    try:
        with P.Iso(bad2) as g: P.read_drop_rates(g)
        check(f"{tag}: foreign drop processor refused", False)
    except ValueError:
        check(f"{tag}: foreign drop processor refused", True)

check("at least one real ISO exercised", ran > 0, f"isos={isos}")
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
