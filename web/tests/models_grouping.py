#!/usr/bin/env python3
"""Field-model grouping checks: on front slices of the real ISOs in ISO/,
a character's looks form a real group, not one row each. A grouped set on
the Prince (ids 2, 119, 128) through the verbatim web glue moves all three
rows and no others, and a grouped reset restores the pointer table
byte-for-byte. Also pins the set_model guards the web picker implies:
model id 0 and slots outside model_targets are refused. Emits JSON for
tests/models-grouping.mjs. Skips cleanly without ISOs, like dash_tuning.py.
"""
import glob, json, os, re, sys, tempfile

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

# the exact glue the browser runs, extracted from app.js like iso_roundtrip.py
app = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
m = re.search(r"const GLUE = `([\s\S]*?)`;", app)
assert m, "could not find GLUE in app.js"

PRINCE = [2, 119, 128]

for region, path in sorted(isos.items()):
    tmp = os.path.join(tempfile.gettempdir(), "s5_models_test_%s.bin" % region)
    with open(path, "rb") as f:
        pristine = f.read(0x6A0000)
    with open(tmp, "wb") as o:
        o.write(pristine)
    try:
        glue = (m.group(1)
                .replace('sys.path.insert(0, "/editor")', "sys.path.insert(0, %r)" % ED)
                .replace('"/iso.bin"', repr(tmp)))
        g = {}
        exec(compile(glue, "glue", "exec"), g)
        chk("%s glue loads the slice" % region,
            json.loads(g["iso_load"]()).get("region") == region)

        def ptr_bytes():
            with open(tmp, "rb") as f:
                f.seek(F.MODEL_PTR_BASE)
                return f.read(4 * F.MODEL_PTR_COUNT)

        with P.Iso(tmp) as h:
            chk("%s the Prince's looks are one group" % region,
                P.model_group(h, 2) == PRINCE, str(P.model_group(h, 2)))
            chk("%s Zegai's group reaches his alternate" % region,
                P.model_group(h, 15) == [15, 123], str(P.model_group(h, 15)))
            usr = next(n["slot"] for n in P.read_resource_names(h)
                       if n["vol"] == "VOL_USR")

        before = ptr_bytes()
        # slot 20 is pc020c (Cathari's base model, the review's cross swap);
        # no group member points there by default, so all three entries move.
        r = json.loads(g["iso_setmodel"](2, 20, 1))
        chk("%s grouped set reports the group" % region,
            r.get("ok") is True and r.get("ids") == PRINCE, str(r.get("ids")))
        rows = json.loads(g["iso_models"]())["models"]
        chk("%s grouped set moves all three looks to pc020c" % region,
            all(rows[i]["file"] == "pc020c.rom" and rows[i]["changed"] for i in PRINCE))
        chk("%s grouped set moves no other row" % region,
            not any(x["changed"] for x in rows if x["id"] not in PRINCE))
        after = ptr_bytes()
        diff = [i for i in range(F.MODEL_PTR_COUNT)
                if after[i * 4:i * 4 + 4] != before[i * 4:i * 4 + 4]]
        chk("%s only the group's pointer entries changed" % region,
            diff == PRINCE, str(diff))
        chk("%s each moved entry now holds the slot 20 pointer" % region,
            all(after[i * 4:i * 4 + 4] == before[21 * 4:21 * 4 + 4] for i in PRINCE))

        r = json.loads(g["iso_setmodel"](2, -1, 1))
        chk("%s grouped reset ok" % region, r.get("ok") is True)
        chk("%s grouped reset restores the pointer table byte-for-byte" % region,
            ptr_bytes() == before)
        rows = json.loads(g["iso_models"]())["models"]
        chk("%s no row reports a change after the reset" % region,
            not any(x["changed"] for x in rows))

        chk("%s model id 0 refused" % region,
            "error" in json.loads(g["iso_setmodel"](0, 127, 0)))
        chk("%s a VOL_USR slot refused" % region,
            "error" in json.loads(g["iso_setmodel"](2, usr, 0)), "slot %d" % usr)
        chk("%s refused writes changed nothing" % region, ptr_bytes() == before)
    finally:
        try: os.remove(tmp)
        except OSError: pass
        for side in (".s5mod.json", ".hardmode.json", ".enemyscale.json",
                     ".growthscale.json"):
            try: os.remove(tmp + side)
            except OSError: pass

print(json.dumps({"checks": checks}))
