#!/usr/bin/env python3
"""ISO-editor engine round-trip against a synthetic truncated slice.

Extracts the EXACT Python glue the browser runs (the GLUE template literal in
app.js), repoints its /editor and /iso.bin paths at local files, then drives the
same adapter functions the front-end calls — proving the web ISO editor reuses the
verified desktop engine correctly on a 6.6 MB front-slice. Uses a fabricated slice
built from the engine's own constants (no real disc shipped), so it can't drift.

Run directly (python3 tests/iso_roundtrip.py) or via tests/iso-roundtrip.mjs."""
import os, re, json, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
ED = os.path.abspath(os.path.join(WEB, "..", "Editor"))
sys.path.insert(0, ED)

ISO_END = 0x6A0000
slice_path = os.path.join(tempfile.gettempdir(), "s5_test_slice.bin")

def main():
    import s5fields as F
    # extract the glue verbatim from app.js and repoint browser paths at local files
    app = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
    m = re.search(r"const GLUE = `([\s\S]*?)`;", app)
    assert m, "could not find GLUE in app.js"
    glue = (m.group(1)
            .replace('sys.path.insert(0, "/editor")', "sys.path.insert(0, %r)" % ED)
            .replace('"/iso.bin"', repr(slice_path)))
    g = {}
    exec(compile(glue, "glue", "exec"), g)

    # fabricate a slice: serial + a known Dinn(11) stat record (verified values)
    buf = bytearray(ISO_END)
    buf[F.SERIAL_OFF:F.SERIAL_OFF + len(F.SERIAL_STR)] = F.SERIAL_STR
    sb = F.TABLES["stats"][0] + 11 * F.TABLES["stats"][1]
    buf[sb:sb + 9] = bytes([50, 30, 5, 5, 5, 1, 5, 10, 5])
    # ...and the field-model tables, laid out the way the disc does it: a 0x20-stride table
    # of resource paths, then one pointer per model id aiming at its own slot (id-1).
    for i in range(F.RESOURCE_NAME_COUNT):
        path = (b"VOL_COM:pc%03dc.rom" % i) if i < 128 else (b"VOL_USR:other%03d.rom" % i)
        o = F.RESOURCE_NAME_BASE + i * F.RESOURCE_NAME_STRIDE
        buf[o:o + F.RESOURCE_NAME_STRIDE] = path.ljust(F.RESOURCE_NAME_STRIDE, b"\x00")
    for i in range(F.MODEL_PTR_COUNT):
        target = F.RESOURCE_NAME_BASE + (i - 1) * F.RESOURCE_NAME_STRIDE if i else F.RESOURCE_NAME_BASE - 0x18
        o = F.MODEL_PTR_BASE + i * 4
        buf[o:o + 4] = (target + F.MODEL_VADDR_DELTA).to_bytes(4, "little")
    open(slice_path, "wb").write(buf)

    n = [0]; bad = [0]
    def chk(name, cond, extra=""):
        n[0] += 1
        print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
        if not cond: bad[0] += 1

    chk("iso_load region", json.loads(g["iso_load"]()).get("region") == "ntsc-u")
    mp = json.loads(g["iso_maps"]())
    chk("iso_maps ranks/elements/armor", "ranks" in mp and "elements" in mp and "head" in mp["armor"])
    chk("iso_char stats table", "stats" in json.loads(g["iso_char"](11))["tables"])
    chk("iso_setchar ok", json.loads(g["iso_setchar"](json.dumps({"id": 11}),
        json.dumps([{"table": "stats", "field": "HP", "value": 88}]))).get("ok") is True)
    hp = next(f["value"] for f in json.loads(g["iso_char"](11))["tables"]["stats"] if f["label"] == "HP")
    chk("HP write persisted", hp == 88, "HP=%d" % hp)
    chk("iso_chars hasStats", "hasStats" in json.loads(g["iso_chars"]())["chars"][0])
    chk("iso_spell fields", "fields" in json.loads(g["iso_spell"](0)))
    chk("iso_setspell ok", json.loads(g["iso_setspell"](json.dumps({"id": 0}),
        json.dumps([{"field": "Power / heal (u16)", "value": 123}]))).get("ok") is True)
    chk("iso_gear list", "items" in json.loads(g["iso_gear"]("body")))
    chk("iso_gearitem fields", "fields" in json.loads(g["iso_gearitem"]("body", 0)))
    chk("iso_setgear ok", json.loads(g["iso_setgear"](json.dumps({"slot": "body", "id": 0}),
        json.dumps([{"field": "DEF", "value": 10}]))).get("ok") is True)
    chk("iso_mp groups", "groups" in json.loads(g["iso_mp"]()))
    chk("iso_setmp ok", json.loads(g["iso_setmp"](0, 0, 42)).get("ok") is True)
    chk("iso_setprice ok", json.loads(g["iso_setprice"](0, "buy", 500)).get("ok") is True)
    chk("iso_reference dict", isinstance(json.loads(g["iso_reference"]()), dict))
    chk("iso_enemies", "enemies" in json.loads(g["iso_enemies"]()))
    chk("iso_runes", "runes" in json.loads(g["iso_runes"]()))
    chk("iso_prices", "prices" in json.loads(g["iso_prices"]()))
    chk("iso_skillfx", "skills" in json.loads(g["iso_skillfx"]()))
    chk("iso_setname ok", json.loads(g["iso_setname"](0, "Test")).get("ok") is True)
    chk("iso_hardmode ok", json.loads(g["iso_hardmode"](2.0)).get("ok") is True)
    chk("iso_hmrestore ok", json.loads(g["iso_hmrestore"]()).get("ok") is True)
    em = json.loads(g["iso_exportmod"](""))
    chk("iso_exportmod recipe", em.get("ok") is True and em["mod"]["patchCount"] > 0)
    # the recipe offset for the Dinn HP write must be the verified absolute ISO offset
    off_hp = F.TABLES["stats"][0] + 11 * F.TABLES["stats"][1]  # + 0 (HP)
    offs = {p["off"] for p in em["mod"]["patches"]}
    chk("recipe includes Dinn HP offset", off_hp in offs, hex(off_hp))

    # ---- field models: the swap is a pointer repoint, and Reset must restore it exactly
    mods = json.loads(g["iso_models"]())
    rows = mods["models"]
    chk("iso_models row count", len(rows) == F.MODEL_PTR_COUNT, str(len(rows)))
    chk("model id 2 is pc001c", rows[2]["file"] == "pc001c.rom", rows[2]["file"])
    chk("stock table reports no swaps", not any(r["changed"] for r in rows))
    chk("targets skip VOL_USR slots", all(t["file"].startswith("pc") for t in mods["targets"]),
        str(len(mods["targets"])))
    before = open(slice_path, "rb").read()[F.MODEL_PTR_BASE:F.MODEL_PTR_BASE + 4 * F.MODEL_PTR_COUNT]
    chk("iso_setmodel ok", json.loads(g["iso_setmodel"](2, 127)).get("ok") is True)
    swapped = json.loads(g["iso_models"]())["models"][2]
    chk("swap took", swapped["file"] == "pc127c.rom" and swapped["changed"], swapped["file"])
    chk("iso_setmodel reset ok", json.loads(g["iso_setmodel"](2, -1)).get("ok") is True)
    after = open(slice_path, "rb").read()[F.MODEL_PTR_BASE:F.MODEL_PTR_BASE + 4 * F.MODEL_PTR_COUNT]
    chk("reset restores the pointer table byte-for-byte", after == before)
    chk("out-of-range slot rejected", "error" in json.loads(g["iso_setmodel"](2, 9999)))
    chk("out-of-range model rejected", "error" in json.loads(g["iso_setmodel"](999, 1)))
    # a character's looks move together: in the fabricated slice every model is its own
    # character, so grouping is a no-op there — drive it through the engine directly.
    import s5patch as P
    with P.Iso(slice_path) as h: solo = P.model_group(h, 2)
    chk("group of a lone model is just itself", solo == [2], str(solo))
    grouped = json.loads(g["iso_setmodel"](2, 50, 1))
    chk("grouped write reports the ids it touched", grouped.get("ids") == [2], str(grouped.get("ids")))
    json.loads(g["iso_setmodel"](2, -1, 1))
    after2 = open(slice_path, "rb").read()[F.MODEL_PTR_BASE:F.MODEL_PTR_BASE + 4 * F.MODEL_PTR_COUNT]
    chk("grouped reset restores the pointer table too", after2 == before)

    print("\n%d/%d passed" % (n[0] - bad[0], n[0]))
    return 1 if bad[0] else 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try: os.remove(slice_path)
        except OSError: pass
        for side in (".s5mod.json", ".hardmode.json"):
            try: os.remove(slice_path + side)
            except OSError: pass
