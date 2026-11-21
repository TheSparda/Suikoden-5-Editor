#!/usr/bin/env python3
"""Dash tuning checks (issue #18): the field motor's two multiplier fields
(s5patch.read_dash_multipliers / write_dash_multiplier) on front slices of
every ISO in ISO/, their composition with the godspeed-dash toggle, the
verbatim web glue (iso_dashtuning / iso_setdashmul from app.js), and the
save editor's NG+ carry-byte round trip on a real save when one is at hand.
Emits JSON for tests/dash-tuning.mjs. Skips cleanly without ISOs, like
gear_req.py.

The multiplier sites were verified against the pristine discs of both
regions (ps2-re DASH_GATE_CODE.md): speed US 0x1F4F34 stock 2.0, PAL
0x1F5494 stock 1.75; animation US 0x1F5080, PAL 0x1F55E0, stock 2.0 in
both. The animation lui is a dedicated inline constant on the dash-state
path only, not a shared pool entry, and each pattern occurs exactly once
per disc.
"""
import glob, json, os, re, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5patch as P
import s5toggles as T
import s5save as SV

checks = []
def chk(name, cond, extra=""):
    checks.append({"name": name, "ok": bool(cond), "extra": str(extra)})

P.BACKUPS = False
P.RECORD_MODS = False

SITES = {  # region: (speed off, speed stock word, anim off, gate off)
    "ntsc-u": (0x1F4F34, 0x3C024000, 0x1F5080, 0x1F5288),
    "pal":    (0x1F5494, 0x3C023FE0, 0x1F55E0, 0x1F57E8),
}
LUI = {1.0: 0x3C023F80, 1.5: 0x3C023FC0, 1.75: 0x3C023FE0,
       2.0: 0x3C024000, 2.5: 0x3C024020, 3.0: 0x3C024040, 4.0: 0x3C024080}

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

for region, path in sorted(isos.items()):
    sp_off, sp_word, an_off, gate_off = SITES[region]
    tmp = os.path.join(tempfile.gettempdir(), "s5_dash_test_%s.bin" % region)
    with open(path, "rb") as f:
        pristine = f.read(0x6A0000)
    with open(tmp, "wb") as o:
        o.write(pristine)
    if P.set_region_for(tmp) != region:
        chk("%s slice region" % region, False, "set_region_for mismatch")
        os.remove(tmp)
        continue

    def file_bytes():
        with open(tmp, "rb") as f:
            return f.read()

    with P.Iso(tmp, writable=True) as g:
        r = P.read_dash_multipliers(g)
        flds = {f["field"]: f for f in r["fields"]}
        chk("%s read reports the region and both fields in order" % region,
            r["region"] == region
            and [f["field"] for f in r["fields"]] == ["dash_speed", "dash_anim"])
        chk("%s stocks read back (speed %s, anim 2.0)" % (region, flds["dash_speed"]["stock"]),
            flds["dash_speed"]["value"] == flds["dash_speed"]["stock"]
            and flds["dash_anim"]["value"] == 2.0
            and flds["dash_speed"]["presets"] == [1.5, 2.0, 2.5, 3.0, 4.0]
            and flds["dash_anim"]["presets"] == [1.0, 1.5, 2.0, 2.5, 3.0, 4.0])
        chk("%s pristine words at the traced offsets" % region,
            g.ru(sp_off, 4) == sp_word and g.ru(an_off, 4) == 0x3C024000
            and g.ru(gate_off, 4) == 0x1040FF2F)

        # every preset lands in exactly its own word
        ok = True
        for v in (1.5, 2.5, 3.0, 4.0):
            before = g.rd(0, len(pristine))
            P.write_dash_multiplier(g, "dash_speed", v)
            after = g.rd(0, len(pristine))
            diff = [i for i in range(len(pristine)) if before[i] != after[i]]
            ok = ok and g.ru(sp_off, 4) == LUI[v] \
                and all(sp_off <= i < sp_off + 4 for i in diff) and diff
        chk("%s speed presets land in the speed word only" % region, ok)
        ok = True
        for v in (1.0, 1.5, 3.0, 4.0):
            before = g.rd(0, len(pristine))
            P.write_dash_multiplier(g, "dash_anim", v)
            after = g.rd(0, len(pristine))
            diff = [i for i in range(len(pristine)) if before[i] != after[i]]
            ok = ok and g.ru(an_off, 4) == LUI[v] \
                and all(an_off <= i < an_off + 4 for i in diff) and diff
        chk("%s anim presets land in the anim word only" % region, ok)
        chk("%s the two multipliers are independent" % region,
            P.read_dash_multipliers(g)["fields"][0]["value"] == 4.0
            and P.read_dash_multipliers(g)["fields"][1]["value"] == 4.0)
        chk("%s the 4.0 tier writes and reads back on both fields" % region,
            g.ru(sp_off, 4) == LUI[4.0] and g.ru(an_off, 4) == LUI[4.0])

        # validation: non-presets and unknown fields refuse, writing nothing
        before = g.rd(0, len(pristine))
        refused = 0
        for field, v in (("dash_speed", 1.25), ("dash_speed", 5.0),
                         ("dash_anim", 0.5), ("dash_nope", 2.0)):
            try:
                P.write_dash_multiplier(g, field, v)
            except ValueError:
                refused += 1
        chk("%s invalid writes refuse and write nothing" % region,
            refused == 4 and g.rd(0, len(pristine)) == before)

        # the region stock is always writable (restore path), even the
        # PAL speed stock 1.75, which is not a preset
        stock = flds["dash_speed"]["stock"]
        P.write_dash_multiplier(g, "dash_speed", stock)
        P.write_dash_multiplier(g, "dash_anim", 2.0)
        chk("%s restore to stock is byte-identical" % region,
            g.rd(0, len(pristine)) == pristine)
        if region == "ntsc-u":
            try:
                P.write_dash_multiplier(g, "dash_speed", 1.75)
                chk("US speed refuses the PAL stock 1.75", False, "no error")
            except ValueError:
                chk("US speed refuses the PAL stock 1.75", True)

        # composition: the gate toggle plus both multipliers, in any order
        T.set_toggle(g, "godspeed-dash", True)
        P.write_dash_multiplier(g, "dash_speed", 2.5)
        P.write_dash_multiplier(g, "dash_anim", 1.5)
        chk("%s toggle and multipliers compose" % region,
            g.ru(gate_off, 4) == 0 and g.ru(sp_off, 4) == LUI[2.5]
            and g.ru(an_off, 4) == LUI[1.5]
            and T.toggle_status(g, "godspeed-dash")["state"] == "on")
        P.write_dash_multiplier(g, "dash_speed", stock)
        P.write_dash_multiplier(g, "dash_anim", 2.0)
        T.set_toggle(g, "godspeed-dash", False)
        chk("%s full restore after composition is byte-identical" % region,
            g.rd(0, len(pristine)) == pristine)

        # anchor refusal: a corrupted anchor word blocks reads and writes
        g.wu(sp_off + 4, 4, 0xDEADBEEF)
        try:
            P.read_dash_multipliers(g)
            chk("%s corrupted speed anchor refuses the read" % region, False, "no error")
        except ValueError:
            chk("%s corrupted speed anchor refuses the read" % region, True)
        g.wu(sp_off + 4, 4, 0x24160001)   # back to stock
        g.wu(an_off - 8, 4, 0xDEADBEEF)
        try:
            P.write_dash_multiplier(g, "dash_anim", 2.5)
            chk("%s corrupted anim anchor refuses the write" % region, False, "no error")
        except ValueError:
            chk("%s corrupted anim anchor refuses the write" % region, True)
        g.wu(an_off - 8, 4, 0x12C00003)   # back to stock
    chk("%s dash experiments leave the file byte-identical" % region,
        file_bytes() == pristine)

    # ---- the verbatim web glue from app.js, exercised end to end ----
    appjs = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    ns = {"P": P, "json": json, "ISO": tmp}
    for fname in ("iso_dashtuning", "iso_setdashmul"):
        m = re.search(r"^def %s\(.*?(?=^def |\Z)" % fname, appjs, re.S | re.M)
        chk("%s glue %s found in app.js" % (region, fname), bool(m))
        if m:
            exec(m.group(0), ns)
    if "iso_dashtuning" in ns and "iso_setdashmul" in ns:
        out = json.loads(ns["iso_dashtuning"]())
        chk("%s glue read reports both fields" % region,
            "error" not in out and len(out.get("fields", [])) == 2, str(out)[:120])
        chk("%s glue presets offer the 4.0 tier the web select renders" % region,
            "error" not in out and len(out.get("fields", [])) == 2
            and all(4.0 in f["presets"] for f in out["fields"]), str(out)[:120])
        res = json.loads(ns["iso_setdashmul"]("dash_anim", 2.5))
        chk("%s glue write lands" % region,
            res.get("value") == 2.5 and "error" not in res, str(res)[:120])
        with P.Iso(tmp) as g:
            chk("%s glue write is in the anim word" % region,
                g.ru(an_off, 4) == LUI[2.5])
        json.loads(ns["iso_setdashmul"]("dash_anim", 2.0))
    with open(tmp, "rb") as f:
        chk("%s glue round trip leaves the file byte-identical" % region,
            f.read() == pristine)
    os.remove(tmp)

# ---- save editor: the NG+ write on a real save, through the engine ----
save_cands = [os.path.join(ROOT, "..", "saves", "library", "sparda-us-slot00.psu")]
save_src = next((c for c in save_cands if os.path.exists(c)), None)
if save_src:
    tmp = os.path.join(tempfile.gettempdir(), "s5_dash_ngplus_test.psu")
    shutil.copy(save_src, tmp)

    def payload_of(path):
        with open(path, "rb") as f:
            psu = SV.PsuSave(f.read())
        return psu.read_file("gamedata") or psu.read_file(psu.folder)

    before = payload_of(tmp)
    entries = SV.read_all_saves(tmp)
    chk("real save reads with the NG+ byte off",
        len(entries) == 1 and entries[0]["fields"]["newGamePlus"] == 0
        and before[SV.NG_PLUS_OFF] == 0)
    SV.write_save_fields(tmp, entries[0]["folder"], {"newGamePlus": 1})
    after = payload_of(tmp)
    diff = [i for i in range(len(before)) if before[i] != after[i]]
    chk("real save NG+ write touches only payload byte 0x120E8",
        diff == [0x120E8] and after[0x120E8] == 1 and after[0x12] == before[0x12],
        "%d bytes differ" % len(diff))
    chk("real save reads back on after the write",
        SV.read_all_saves(tmp)[0]["fields"]["newGamePlus"] == 1)
    SV.write_save_fields(tmp, entries[0]["folder"], {"newGamePlus": 0})
    chk("real save NG+ write off restores the payload exactly",
        payload_of(tmp) == before)
    os.remove(tmp)
else:
    chk("real save NG+ round trip (skipped: no library save)", True, "skipped")

print(json.dumps({"checks": checks}))
