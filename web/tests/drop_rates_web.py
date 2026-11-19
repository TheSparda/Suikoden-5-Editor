#!/usr/bin/env python3
"""Global drop rates through the WEB loading model (issue #46): the web ISO
editor loads only the first 0x6A0000 bytes of the disc into /iso.bin, and
BATLE.ROM lives far past that (US: LBA 5797, byte 0xB54000), so the drop-rate
panel's engine reads hit EOF and the panel refused on every disc, stock or
rebuilt. The desktop editor reads the full file and always worked.

The fix stages the overlay's sector slot from the retained full-disc File
into the web image at its real offset (zero-filled gap), extending the
pristine baseline identically, so the save/recipe diff sees the overlay
edits as changed runs at absolute offsets. This suite replicates that model
exactly, through the same engine entry points the panel's glue calls
(read_drop_rates / write_drop_rates / restore_drop_rates):

  * the bare front slice refuses, the failure the panel showed;
  * the staged image reads the stock rates, and its bytes at the overlay
    offset are byte-identical to the disc's (staging is faithful);
  * a custom write's changed bytes, diffed against the identically
    extended pristine baseline (the web save/recipe diff), stay inside the
    overlay's real offset range, its directory record and the Cook bytes,
    with NOT ONE changed byte in the zero gap, so a save can never write
    gap zeros into the real disc;
  * restore returns the stock rates with the overlay payload byte-identical
    to stock and the container form preserved (the container stream itself
    is re-encoded on write, as on desktop, so identity is payload-level).
"""
import glob, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5patch as P

P.BACKUPS = False
P.RECORD_MODS = False

ISO_END = 0x6A0000          # web/iso.js: the front-slice length

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
    off = ov["lba"] * 2048
    end = off + slot
    tb = P._SKILL_TABLE_ISO[region]
    cook6 = tb + 0x98 * 36 + 6 * 2 + 0x12
    tmp = tempfile.mkdtemp()
    ran += 1
    tag = "web/" + region

    check(f"{tag}: BATLE.ROM sits past the web front slice",
          off >= ISO_END, hex(off))

    # The bare front slice: exactly what /iso.bin holds before staging.
    front_path = os.path.join(tmp, "iso.bin")
    with open(src, "rb") as f: front = f.read(ISO_END)
    with open(front_path, "wb") as f: f.write(front)
    try:
        with P.Iso(front_path) as g: P.read_drop_rates(g)
        check(f"{tag}: bare front slice refuses (the panel's failure)", False)
    except ValueError as e:
        check(f"{tag}: bare front slice refuses (the panel's failure)",
              "BATLE.ROM" in str(e), str(e))

    # Stage exactly as web/iso.js stageBatleInto does: front slice, zero
    # gap, the overlay's sector slot read from the full disc at its real
    # offset. `pristine` is the web ORIG baseline, extended identically.
    staged = bytearray(end)
    staged[:ISO_END] = front
    with open(src, "rb") as f:
        f.seek(off); staged[off:end] = f.read(slot)
    pristine = bytes(staged)
    staged_path = os.path.join(tmp, "iso-staged.bin")
    with open(staged_path, "wb") as f: f.write(staged)
    rec_off, rec_len = P._find_ovl_dirrec(staged_path, "BATLE.ROM")

    with open(src, "rb") as f:
        f.seek(off); disc_container = f.read(ov["size"])
    check(f"{tag}: staged bytes at the overlay offset are byte-identical "
          f"to the disc's",
          pristine[off:off + ov["size"]] == disc_container)

    with P.Iso(staged_path) as g: r = P.read_drop_rates(g)
    check(f"{tag}: stock drop rates read through the staged image",
          r["slots"] == STOCK["slots"] and r["dish"] == STOCK["dish"]
          and r["cook"] == STOCK["cook"], r)

    with P.Iso(staged_path, writable=True) as g:
        P.write_drop_rates(g, CUSTOM["slots"], CUSTOM["dish"], CUSTOM["cook"])
    with P.Iso(staged_path) as g: r = P.read_drop_rates(g)
    check(f"{tag}: custom rates write and read back through the staged image",
          r["slots"] == CUSTOM["slots"] and r["dish"] == CUSTOM["dish"]
          and r["cook"] == CUSTOM["cook"], r)

    # The web save/recipe diff: edited image vs the extended pristine
    # baseline. Every changed byte must be a real edit at an absolute
    # offset: the overlay slot, its directory record, or the Cook bytes.
    now = open(staged_path, "rb").read()
    check(f"{tag}: write did not change the image length", len(now) == end,
          (len(now), end))
    diffs = [i for i in range(end) if now[i] != pristine[i]]
    def allowed(i):
        return (off <= i < end or rec_off <= i < rec_off + rec_len
                or cook6 <= i < cook6 + 4)
    check(f"{tag}: changed runs confined to the overlay's real offset "
          f"range, its directory record and the Cook bytes",
          bool(diffs) and all(allowed(i) for i in diffs),
          [hex(i) for i in diffs if not allowed(i)][:5])
    check(f"{tag}: the write really changed bytes inside the overlay range",
          any(off <= i < end for i in diffs))
    check(f"{tag}: NOT ONE changed byte in the zero gap (a save can never "
          f"write gap zeros into the real disc)",
          not any(ISO_END <= i < off for i in diffs),
          [hex(i) for i in diffs if ISO_END <= i < off][:5])

    with P.Iso(staged_path, writable=True) as g: P.restore_drop_rates(g)
    with P.Iso(staged_path) as g:
        r = P.read_drop_rates(g); payload = P._batle_bin(g)
    with P.Iso(src) as g: stock_payload = P._batle_bin(g)
    check(f"{tag}: restore returns the stock rates",
          r["slots"] == STOCK["slots"] and r["dish"] == STOCK["dish"]
          and r["cook"] == STOCK["cook"], r)
    check(f"{tag}: restored overlay payload is byte-identical to stock",
          payload == stock_payload)
    with open(staged_path, "rb") as f:
        f.seek(off + 8); type_word = f.read(4)
    check(f"{tag}: restore keeps the disc's container form",
          type_word == pristine[off + 8:off + 12])

check("at least one real ISO exercised", ran > 0, f"isos={isos}")
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
