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
