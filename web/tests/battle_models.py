#!/usr/bin/env python3
"""Battle-model checks: on slices of the real ISOs in ISO/ (front slice plus
the BATLE.ROM sector slot), the directory read matches the 82-entry table
from the lab report, a grouped set through the verbatim web glue moves the
Prince's three rows and no other bytes in the overlay image, a one-sided
set moves one row, the repack round-trips, the sector fit check refuses an
incompressible repack, and a grouped reset restores the factory image
byte-for-byte. The pairing section pins the tab's default coupling: a
paired pick through the glue writes the picked system and the counterpart
rows of the other system, an unpaired pick writes only its own system,
and an unpairable target (an ec file, or a look the other side does not
have) still lands on the picked side alone. Emits JSON for
tests/battle-models.mjs. Skips cleanly without ISOs, like
models_grouping.py.
"""
import glob, json, os, random, re, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
ROOT = os.path.dirname(WEB)
ED = os.path.join(ROOT, "Editor")
sys.path.insert(0, ED)
import s5patch as P
import s5fields as F

checks = []
def chk(name, cond, extra=""):
    checks.append({"name": name, "ok": bool(cond), "extra": str(extra)})

P.BACKUPS = False
P.RECORD_MODS = False

isos = {}
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            r = P.region_of(g)
        if r:
            isos[r] = cand
    except Exception:
        continue
if not isos:
    print(json.dumps({"skip": "no ISO in ISO/"}))
    sys.exit(0)

# the exact glue the browser runs, extracted from app.js like models_grouping.py
app = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
m = re.search(r"const GLUE = `([\s\S]*?)`;", app)
assert m, "could not find GLUE in app.js"

# the report's 82-entry table, in row order (BATTLE_MODELS.md)
STOCK = ["btl_pc%03d" % n for n in (
    1, 401, 201, 4, 6, 8, 9, 209, 11, 12, 13, 213, 14, 15, 17, 18, 19, 20,
    21, 221, 22, 110, 23, 25, 26, 28, 30, 33, 37, 38, 40, 42, 43, 44, 45, 46,
    48, 49, 51, 52, 58, 59, 60, 61, 62, 63, 67, 68, 69, 70, 71, 72, 73, 74,
    76, 77, 78, 79, 80, 81, 82, 85, 86, 87, 90, 91, 96, 97, 98, 100, 101,
    102, 103, 104, 107, 207, 109, 120, 121, 122, 123, 124)]
PRINCE = [0, 1, 2]

for region, path in sorted(isos.items()):
    ov = next(o for o in P.list_overlays(path) if o["name"] == "BATLE.ROM")
    slot = ((ov["size"] + 2047) // 2048) * 2048
    slice_len = ov["lba"] * 2048 + slot
    tmp = os.path.join(tempfile.gettempdir(), "s5_battle_test_%s.bin" % region)
    with open(path, "rb") as f:
        head = f.read(slice_len)
    with open(tmp, "wb") as o:
        o.write(head)
    try:
        glue = (m.group(1)
                .replace('sys.path.insert(0, "/editor")', "sys.path.insert(0, %r)" % ED)
                .replace('"/iso.bin"', repr(tmp)))
        g = {}
        exec(compile(glue, "glue", "exec"), g)
        chk("%s glue loads the slice" % region,
            json.loads(g["iso_load"]()).get("region") == region)

        # the full disc and the slice read the same directory
        with P.Iso(path) as h:
            full = [r["default_name"] for r in P.read_battle_models(h)]
        chk("%s full-disc directory matches the report table" % region,
            full == STOCK)
        d = json.loads(g["iso_battlemodels"]())
        rows, targets = d["models"], d["targets"]
        chk("%s 82 rows and 82 targets" % region,
            len(rows) == 82 and len(targets) == 82)
        chk("%s slice directory matches the report table" % region,
            [r["name"] for r in rows] == STOCK
            and [r["default_name"] for r in rows] == STOCK
            and not any(r["changed"] for r in rows))
        chk("%s targets are the stock models" % region,
            [t["name"] for t in targets] == STOCK)
        spot = {0: ("Hero/Prince", 0), 18: ("Georg", 18), 21: ("Arshtat", 21),
                74: ("Roy", 74), 75: ("Roy", 75), 77: ("Dilber", 98),
                81: ("", 102)}
        chk("%s row owners and directory positions" % region,
            all(rows[i]["char"] == c and rows[i]["dir"] == dd
                for i, (c, dd) in spot.items()),
            str([(i, rows[i]["char"], rows[i]["dir"]) for i in spot]))

        def image():
            with P.Iso(tmp) as h:
                return P._batle_bin(h)

        def spans(*row_ids):
            out = set()
            for i in row_ids:
                off = P.BATTLE_MODEL_DIR_OFF + rows[i]["dir"] * 16
                out.update(range(off, off + 16))
            return out

        pristine = image()
        r = json.loads(g["iso_setbattlemodel"](0, "btl_pc107", 1))
        chk("%s grouped set reports the group" % region,
            r.get("ok") is True and r.get("ids") == PRINCE, str(r.get("ids")))
        rows2 = json.loads(g["iso_battlemodels"]())["models"]
        chk("%s grouped set moves all three looks to btl_pc107" % region,
            all(rows2[i]["name"] == "btl_pc107" and rows2[i]["changed"]
                for i in PRINCE))
        chk("%s grouped set moves no other row" % region,
            not any(x["changed"] for x in rows2 if x["id"] not in PRINCE))
        img = image()
        diff = {i for i in range(len(img)) if img[i] != pristine[i]}
        chk("%s grouped set changes only the three directory entries" % region,
            diff <= spans(*PRINCE), "%d bytes differ" % len(diff))

        # the repacked container on the slice decodes to the same image
        ov2 = next(o for o in P.list_overlays(tmp) if o["name"] == "BATLE.ROM")
        with open(tmp, "rb") as f:
            f.seek(ov2["lba"] * 2048)
            raw = f.read(ov2["size"])
        codec, dec, ok = P.rom_decode(raw)
        chk("%s repack round-trips" % region, ok and dec == img, codec)

        # the container must also parse the way the game's loader reads
        # it: the header word at +0x10 is the total container size
        # (header + stream), equal to the directory size, and the
        # stream is the rest of the file, decoding to the image with
        # every stream byte consumed. (rom_decode above slices by the
        # header word itself, so it cannot catch a wrong word; a
        # stream-length word there leaves the game 0x40 bytes short
        # and hangs the overlay load.)
        total = int.from_bytes(raw[0x10:0x14], "little")
        chk("%s repack header carries the total container size" % region,
            total == ov2["size"] == len(raw), str(total))
        if codec == "szl":
            import s5rom
            try:
                gout, gconsumed = s5rom.lzss_decompress(
                    raw[0x40:total], len(img))
                gok = gout == img and gconsumed == total - 0x40
            except Exception:
                gok = False
            chk("%s repack decodes the game way" % region, gok)

        r = json.loads(g["iso_setbattlemodel"](0, "", 1))
        chk("%s grouped reset ok" % region, r.get("ok") is True)
        chk("%s grouped reset restores the image byte-for-byte" % region,
            image() == pristine)
        rows3 = json.loads(g["iso_battlemodels"]())["models"]
        chk("%s no row reports a change after the reset" % region,
            not any(x["changed"] for x in rows3))

        # one-sided: a single row, no group
        r = json.loads(g["iso_setbattlemodel"](74, "btl_pc001", 0))
        chk("%s one-sided set reports just the row" % region,
            r.get("ok") is True and r.get("ids") == [74], str(r.get("ids")))
        img = image()
        diff = {i for i in range(len(img)) if img[i] != pristine[i]}
        chk("%s one-sided set changes only Roy's entry" % region,
            diff and diff <= spans(74), "%d bytes differ" % len(diff))
        rows4 = json.loads(g["iso_battlemodels"]())["models"]
        chk("%s one-sided set reads back" % region,
            rows4[74]["name"] == "btl_pc001" and rows4[74]["changed"]
            and rows4[74]["target_char"] == "Hero/Prince"
            and not any(x["changed"] for x in rows4 if x["id"] != 74))
        json.loads(g["iso_setbattlemodel"](74, "", 0))
        chk("%s one-sided reset restores the image byte-for-byte" % region,
            image() == pristine)

        # ---- pairing (the tab's default): a pick in one system also sets
        # the other system's rows for the same character to the picked
        # target's counterpart there (same character, same look number).
        def ptr_bytes():
            with open(tmp, "rb") as f:
                f.seek(F.MODEL_PTR_BASE)
                return f.read(4 * F.MODEL_PTR_COUNT)

        def field_rows():
            return json.loads(g["iso_models"]())["models"]

        def brows_now():
            return json.loads(g["iso_battlemodels"]())["models"]

        before_ptr = ptr_bytes()

        # grouped field pick on the Prince to Cathari pairs his battle rows
        r = json.loads(g["iso_setmodel"](2, 20, 1, 1))
        chk("%s paired field pick reports the battle group" % region,
            r.get("ok") is True and r.get("battle_ids") == PRINCE
            and not r.get("pair_error"), str(r))
        pfr, pbr = field_rows(), brows_now()
        chk("%s paired field pick moves the field group" % region,
            all(pfr[i]["file"] == "pc020c.rom" and pfr[i]["changed"]
                for i in (2, 119, 128)))
        chk("%s paired field pick moves the battle group to Cathari" % region,
            all(pbr[i]["name"] == "btl_pc020" and pbr[i]["changed"]
                for i in PRINCE))
        chk("%s paired field pick moves no other battle row" % region,
            not any(x["changed"] for x in pbr if x["id"] not in PRINCE))
        img = image()
        diff = {i for i in range(len(img)) if img[i] != pristine[i]}
        chk("%s paired field pick changes only the three battle entries" % region,
            diff <= spans(*PRINCE), "%d bytes differ" % len(diff))
        r = json.loads(g["iso_setmodel"](2, -1, 1, 1))
        chk("%s paired reset restores both systems" % region,
            r.get("ok") is True and ptr_bytes() == before_ptr
            and image() == pristine)

        # grouped battle pick on the Prince to Roy pairs his field rows
        r = json.loads(g["iso_setbattlemodel"](0, "btl_pc107", 1, 1))
        chk("%s paired battle pick reports the field group" % region,
            r.get("ok") is True and r.get("field_ids") == [2, 119, 128]
            and not r.get("pair_error"), str(r))
        pfr, pbr = field_rows(), brows_now()
        chk("%s paired battle pick moves the battle group" % region,
            all(pbr[i]["name"] == "btl_pc107" and pbr[i]["changed"]
                for i in PRINCE))
        chk("%s paired battle pick moves the field group to Roy" % region,
            all(pfr[i]["file"] == "pc107c.rom" and pfr[i]["changed"]
                for i in (2, 119, 128)))
        chk("%s paired battle pick moves no other field row" % region,
            not any(x["changed"] for x in pfr if x["id"] not in (2, 119, 128)))
        r = json.loads(g["iso_setbattlemodel"](0, "", 1, 1))
        chk("%s paired battle reset restores both systems" % region,
            r.get("ok") is True and ptr_bytes() == before_ptr
            and image() == pristine)

        # pairing off: only the picked system moves
        r = json.loads(g["iso_setmodel"](2, 20, 1, 0))
        chk("%s unpaired field pick leaves the battle image alone" % region,
            r.get("ok") is True and not r.get("battle_ids")
            and image() == pristine)
        json.loads(g["iso_setmodel"](2, -1, 1, 0))
        r = json.loads(g["iso_setbattlemodel"](0, "btl_pc107", 1, 0))
        chk("%s unpaired battle pick leaves the field table alone" % region,
            r.get("ok") is True and not r.get("field_ids")
            and ptr_bytes() == before_ptr)
        json.loads(g["iso_setbattlemodel"](0, "", 1, 0))
        chk("%s unpaired resets restore both systems" % region,
            ptr_bytes() == before_ptr and image() == pristine)

        # a single paired pick pairs just the touched look
        r = json.loads(g["iso_setmodel"](2, 20, 0, 1))
        pbr = brows_now()
        chk("%s single paired field pick pairs battle look 1 only" % region,
            r.get("battle_ids") == [0] and pbr[0]["name"] == "btl_pc020"
            and not any(x["changed"] for x in pbr if x["id"] != 0),
            str(r.get("battle_ids")))
        json.loads(g["iso_setmodel"](2, -1, 0, 1))
        chk("%s single paired reset restores both systems" % region,
            ptr_bytes() == before_ptr and image() == pristine)

        # unpairable picks still land on the picked side alone: an ec
        # target is not a character model, and Georg's third field look
        # has no third battle look opposite it
        ec = next(t["slot"] for t in json.loads(g["iso_models"]())["targets"]
                  if t["group"] == "Other characters")
        r = json.loads(g["iso_setmodel"](2, ec, 0, 1))
        pfr = field_rows()
        chk("%s paired pick of an ec target changes only the field row" % region,
            r.get("ok") is True and not r.get("battle_ids")
            and pfr[2]["changed"] and image() == pristine, str(r))
        json.loads(g["iso_setmodel"](2, -1, 0, 1))
        r = json.loads(g["iso_setmodel"](125, 20, 0, 1))
        pfr = field_rows()
        chk("%s paired pick past the battle look count changes only the field row"
            % region,
            r.get("ok") is True and not r.get("battle_ids")
            and pfr[125]["changed"] and image() == pristine, str(r))
        json.loads(g["iso_setmodel"](125, -1, 0, 1))
        # and the other way: Roy's second battle look has no field look 2
        r = json.loads(g["iso_setbattlemodel"](0, "btl_pc207", 0, 1))
        pbr = brows_now()
        chk("%s paired pick of Roy's second battle look changes only the battle row"
            % region,
            r.get("ok") is True and not r.get("field_ids")
            and pbr[0]["name"] == "btl_pc207" and ptr_bytes() == before_ptr,
            str(r))
        json.loads(g["iso_setbattlemodel"](0, "", 0, 1))
        chk("%s pairing checks leave both systems pristine" % region,
            ptr_bytes() == before_ptr and image() == pristine)

        # guards: unknown names and rows are refused and change nothing
        chk("%s an unknown battle model refused" % region,
            "error" in json.loads(g["iso_setbattlemodel"](0, "btl_pc999", 0)))
        chk("%s a field model name refused" % region,
            "error" in json.loads(g["iso_setbattlemodel"](0, "pc107c", 0)))
        chk("%s row 82 refused" % region,
            "error" in json.loads(g["iso_setbattlemodel"](82, "btl_pc001", 0)))
        chk("%s refused writes changed nothing" % region, image() == pristine)

        # the fit check: an incompressible same-length image cannot be
        # reinserted; the refusal leaves the disc bytes untouched
        with open(tmp, "rb") as f:
            f.seek(ov["lba"] * 2048)
            raw_before = f.read(slot)
        bad = bytearray(pristine)
        rnd = random.Random(1234)
        bad[0x1000:0x1000 + 400000] = rnd.randbytes(400000)
        fd, badpath = tempfile.mkstemp(prefix="s5_batle_bad_", suffix=".bin")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(bytes(bad))
            try:
                with P.Iso(tmp, writable=True) as h:
                    P.reinsert_overlay(h, "BATLE.ROM", badpath)
                refused = ""
            except ValueError as e:
                refused = str(e)
        finally:
            os.remove(badpath)
        chk("%s oversized repack refused by the fit check" % region,
            "slot" in refused, refused[:80])
        with open(tmp, "rb") as f:
            f.seek(ov["lba"] * 2048)
            raw_after = f.read(slot)
        chk("%s refused repack changed no disc bytes" % region,
            raw_after == raw_before)
    finally:
        try: os.remove(tmp)
        except OSError: pass
        for side in (".s5mod.json", ".hardmode.json", ".enemyscale.json",
                     ".growthscale.json", ".bak"):
            try: os.remove(tmp + side)
            except OSError: pass

print(json.dumps({"checks": checks}))
