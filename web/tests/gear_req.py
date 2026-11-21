#!/usr/bin/env python3
"""Gear equip requirements (issue #49): the Corruption Shell and
Saint's Cloak thresholds are slti immediates in the can-equip
predicate, edited one by one through s5patch.read_gear_requirements /
write_gear_requirement (the Gear view fields) and through the verbatim
web glue adapters extracted from web/app.js. Runs against EVERY ISO in
ISO/ (the sites are region-keyed) and skips cleanly when there is none.

What's asserted: the stock display values (with the Cloak's off-by-one:
its maximum kills displays as the stored immediate minus 1), each write
lands in exactly its own gate word, the Cloak writes store value + 1,
out-of-range values and unknown fields are rejected without touching
the disc, a corrupted gate word refuses both reads and writes, and
restoring the stock display values leaves the slice byte-identical."""
import glob, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5patch as P

discs = []
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            r = P.region_of(g)
            if r: discs.append((r, cand))
    except Exception:
        continue
if not discs:
    print("SKIP gear_req: no Suikoden V ISO in ISO/ (constants are guarded by validate.mjs).")
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

OFFS = {"ntsc-u": {"shell_hp": 0x337ECC, "shell_kills": 0x337EE8,
                   "cloak_hp": 0x337F24, "cloak_kills": 0x337F44},
        "pal": {"shell_hp": 0x338C9C, "shell_kills": 0x338CB8,
                "cloak_hp": 0x338CF4, "cloak_kills": 0x338D14}}
STOCK_WORDS = [0x2842015E, 0x28420064, 0x2842012C, 0x2842001F]
ORDER = ["shell_hp", "shell_kills", "cloak_hp", "cloak_kills"]

# The verbatim web glue: the two adapters exactly as web/app.js ships
# them, exec'd against the slice (they contain no escaped sequences, so
# the raw source is the shipped source).
app_src = open(os.path.join(ROOT, "web", "app.js"), encoding="utf8").read()
def glue_def(name):
    start = app_src.index("def %s" % name)
    end = app_src.index("\ndef ", start)
    return app_src[start:end]
ns = {"json": json, "P": P}
exec(glue_def("iso_gearreqs"), ns)
exec(glue_def("iso_setgearreq"), ns)
chk("glue adapters extracted from web/app.js",
    callable(ns.get("iso_gearreqs")) and callable(ns.get("iso_setgearreq")))

for region, src in discs:
    print("\n-- %s (%s)" % (region, os.path.basename(src)))
    tmp = os.path.join(tempfile.gettempdir(), "s5_gearreq_test_%s.bin" % region)
    with open(src, "rb") as f:
        pristine = f.read(0x6A0000)
    with open(tmp, "wb") as o:
        o.write(pristine)
    chk("region detected", P.set_region_for(tmp) == region)
    offs = OFFS[region]

    with P.Iso(tmp) as g:
        r = P.read_gear_requirements(g)
    chk("read reports the region and the four fields in order",
        r["region"] == region
        and [f["field"] for f in r["fields"]] == ORDER, str(r["region"]))
    chk("stock display values are 350 / 100 / 300 / 30",
        [f["value"] for f in r["fields"]] == [350, 100, 300, 30],
        str([f["value"] for f in r["fields"]]))
    chk("stock metadata matches the display values",
        [f["stock"] for f in r["fields"]] == [350, 100, 300, 30])
    chk("field ranges are 0..32767, Cloak kills 0..32766",
        [(f["min"], f["max"]) for f in r["fields"]]
        == [(0, 32767), (0, 32767), (0, 32767), (0, 32766)])
    words = [int.from_bytes(pristine[offs[f]:offs[f] + 4], "little")
             for f in ORDER]
    chk("the pristine words at the traced offsets are the stock words",
        words == STOCK_WORDS, str([hex(w) for w in words]))

    with P.Iso(tmp, writable=True) as g:
        w = P.write_gear_requirement(g, "shell_hp", 200)
        chk("Shell HP write stores the immediate in its gate word",
            w["imm"] == 200 and g.ru(offs["shell_hp"], 4) == 0x284200C8)
        chk("Shell HP reads back as written",
            P.read_gear_requirements(g)["fields"][0]["value"] == 200)
        now = g.rd(0, len(pristine))
        diff = [i for i in range(len(pristine)) if now[i] != pristine[i]]
        chk("the Shell HP write touched only its own gate word",
            bool(diff) and all(offs["shell_hp"] <= i < offs["shell_hp"] + 4
                               for i in diff),
            str([hex(i) for i in diff][:6]))
        P.write_gear_requirement(g, "shell_hp", 0)
        chk("a minimum of 0 stores immediate 0 (check removed)",
            g.ru(offs["shell_hp"], 4) == 0x28420000)
        P.write_gear_requirement(g, "shell_hp", 32767)
        chk("the maximum display value stores immediate 0x7FFF",
            g.ru(offs["shell_hp"], 4) == 0x28427FFF)
        P.write_gear_requirement(g, "shell_kills", 55)
        chk("Shell kills write stores its immediate",
            g.ru(offs["shell_kills"], 4) == 0x28420037)
        # The Cloak off-by-one: the gate passes iff kills < imm, so the
        # field is a maximum and stores value + 1.
        w = P.write_gear_requirement(g, "cloak_kills", 30)
        chk("Cloak maximum kills 30 stores immediate 31",
            w["imm"] == 31 and g.ru(offs["cloak_kills"], 4) == 0x2842001F)
        w = P.write_gear_requirement(g, "cloak_kills", 0)
        chk("Cloak maximum kills 0 stores immediate 1 (nobody equips it)",
            w["imm"] == 1 and g.ru(offs["cloak_kills"], 4) == 0x28420001)
        w = P.write_gear_requirement(g, "cloak_kills", 32766)
        chk("Cloak maximum kills 32766 stores immediate 0x7FFF",
            w["imm"] == 32767 and g.ru(offs["cloak_kills"], 4) == 0x28427FFF)
        chk("Cloak maximum kills reads back as written",
            P.read_gear_requirements(g)["fields"][3]["value"] == 32766)

        for field, bogus in (("shell_hp", -1), ("shell_hp", 32768),
                             ("shell_kills", 40000), ("cloak_hp", -5),
                             ("cloak_kills", 32767), ("cloak_kills", -1),
                             ("bogus_field", 1)):
            try:
                P.write_gear_requirement(g, field, bogus)
                chk("%s = %s is rejected" % (field, bogus), False)
            except (ValueError, KeyError):
                chk("%s = %s is rejected" % (field, bogus), True)

        # Anchor refusal: a gate word that is not the traced slti is
        # never read as a threshold and never written over.
        g.wu(offs["shell_kills"], 4, 0xDEADBEEF)
        try:
            P.read_gear_requirements(g)
            chk("a corrupted gate word refuses the read", False)
        except ValueError:
            chk("a corrupted gate word refuses the read", True)
        try:
            P.write_gear_requirement(g, "shell_kills", 10)
            chk("a corrupted gate word refuses the write", False)
        except ValueError:
            chk("a corrupted gate word refuses the write", True)
        g.wu(offs["shell_kills"], 4, 0x28420064)   # back to stock

        for f, v in (("shell_hp", 350), ("shell_kills", 100),
                     ("cloak_hp", 300), ("cloak_kills", 30)):
            P.write_gear_requirement(g, f, v)
    with open(tmp, "rb") as f:
        chk("restoring the stock values leaves the slice byte-identical",
            f.read() == pristine)

    # Through the verbatim glue, the way the browser calls it.
    ns["ISO"] = tmp
    r = json.loads(ns["iso_gearreqs"]())
    chk("glue reads the stock thresholds",
        [f["value"] for f in r["fields"]] == [350, 100, 300, 30], str(r)[:120])
    r = json.loads(ns["iso_setgearreq"]("cloak_kills", 10))
    chk("glue writes Cloak maximum kills 10 as immediate 11",
        r.get("ok") is True and r.get("imm") == 11, str(r))
    r = json.loads(ns["iso_setgearreq"]("shell_hp", 99999))
    chk("glue reports an out-of-range write as an error, not a write",
        "error" in r, str(r)[:120])
    r = json.loads(ns["iso_gearreqs"]())
    chk("glue reads back the written values",
        [f["value"] for f in r["fields"]] == [350, 100, 300, 10])
    ns["iso_setgearreq"]("cloak_kills", 30)
    with open(tmp, "rb") as f:
        chk("glue restore leaves the slice byte-identical",
            f.read() == pristine)
    os.remove(tmp)

print("\n%d/%d passed" % (n - bad, n))
sys.exit(1 if bad else 0)
