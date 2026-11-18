#!/usr/bin/env python3
"""Starting-loadout engine tests (issue #28).

Two layers:
1. A synthetic front-slice driven through the EXACT browser glue (the GLUE
   template literal in app.js): decode of the rune words (rune / unlock /
   empty, permanent flags), the caps merge for effective skill ranks, the
   derived equipped slots, and every write validation (rune id space,
   unlock range, perm values, rank range, rank vs cap, PAL gating).
2. The real disc plus the lab enumeration: the implementation must
   reproduce ps2-re loadout_work/loadouts_elf.json for all 120 characters,
   and must predict the game-written starting records in a real save
   (templates file). The ground-truth set is the characters who are not
   recruited in that save under the status-word rule (issue #39): 84
   records, predicted at slots 84/84, ranks 82/84 (exceptions Eresh 10
   and Levi 60), runes 83/84 (exception Miakis 72), level 79/84,
   armor 82/84. (The original lab selection used the editor's old fake
   recruitment bitfield; the word-based reselection is larger and
   cleaner. LOADOUTS.md section 4 carries the correction note.)

Run directly (python3 tests/loadout.py) or via tests/loadout.mjs."""
import os, re, json, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
ED = os.path.abspath(os.path.join(WEB, "..", "Editor"))
sys.path.insert(0, ED)

ISO_END = 0x6A0000
slice_path = os.path.join(tempfile.gettempdir(), "s5_test_loadout_slice.bin")

n = [0]; bad = [0]
def chk(name, cond, extra=""):
    n[0] += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad[0] += 1

def main():
    import s5fields as F
    import s5patch as P

    app = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
    m = re.search(r"const GLUE = `([\s\S]*?)`;", app)
    assert m, "could not find GLUE in app.js"
    glue = (m.group(1)
            .replace('sys.path.insert(0, "/editor")', "sys.path.insert(0, %r)" % ED)
            .replace('"/iso.bin"', repr(slice_path)))
    g = {}
    exec(compile(glue, "glue", "exec"), g)

    # fabricate a slice and seed character 5 (Galleon) across the four tables
    buf = bytearray(ISO_END)
    buf[F.SERIAL_OFF:F.SERIAL_OFF + len(F.SERIAL_STR)] = F.SERIAL_STR
    eqb, eqs = F.TABLES["starting equipment"][0], F.TABLES["starting equipment"][1]
    buf[eqb + 5 * eqs + 0] = 46                                  # starting level
    buf[eqb + 5 * eqs + 2:eqb + 5 * eqs + 6] = bytes([1, 2, 3, 4])
    rb = F.LOADOUT_RUNE_BASE + 5 * F.LOADOUT_RUNE_STRIDE
    # head word 0 (empty), right word -1 (unlock 1), left word 77 (rune, perm)
    for i, w in enumerate([0, -1, 77, 0, 0, 1]):
        buf[rb + i * 4:rb + i * 4 + 4] = w.to_bytes(4, "little", signed=True)
    sb = F.LOADOUT_SKILL_BASE + 5 * F.LOADOUT_SKILL_STRIDE
    buf[sb + 1] = 2                                              # Stamina start 2
    buf[sb + 1 + 23] = 1                                         # Mow Down start 1
    cb, cs = F.TABLES["equipable skills"][0], F.TABLES["equipable skills"][1]
    buf[cb + 5 * cs + 0] = 6                                     # Stamina cap
    buf[cb + 5 * cs + 1] = 4                                     # Attack cap
    buf[cb + 5 * cs + 23] = 5                                    # Mow Down cap
    open(slice_path, "wb").write(buf)

    chk("iso_load region", json.loads(g["iso_load"]()).get("region") == "ntsc-u")
    lo = json.loads(g["iso_loadout"](5))["loadout"]
    chk("loadout level + armor from the equipment record",
        lo["level"] == 46 and lo["levelByte"] == 46 and lo["armor"] == [1, 2, 3, 4], str(lo)[:120])
    rn = {r["slot"]: r for r in lo["runes"]}
    names = F.res_json("s5_rune_save_ids.json")
    chk("rune word 0 decodes as empty", rn["head"]["kind"] == "empty" and rn["head"]["word"] == 0)
    chk("rune word -1 decodes as unlock at level 1",
        rn["right"]["kind"] == "unlock" and rn["right"]["unlockLevel"] == 1)
    chk("rune word 77 decodes as a save-space rune with its perm flag",
        rn["left"]["kind"] == "rune" and rn["left"]["runeId"] == 77
        and rn["left"]["runeName"] == names[77] and rn["left"]["perm"] == 1,
        str(rn["left"]))
    sk = {s["name"]: s for s in lo["skills"]}
    chk("starting rank kept when nonzero", sk["Stamina"]["start"] == 2 and sk["Stamina"]["effective"] == 2)
    chk("caps merge fills a zero start at rank 1", sk["Attack"]["start"] == 0 and sk["Attack"]["effective"] == 1)
    chk("no cap and no start stays rank 0", sk["Defense"]["cap"] == 0 and sk["Defense"]["effective"] == 0)
    chk("equipped slots are the two lowest starting skills",
        lo["equippedSlots"] == [1, 24], str(lo["equippedSlots"]))

    def setlo(kind, index, value):
        return json.loads(g["iso_setloadout"](5, kind, index, value))
    chk("rune write ok", setlo("rune", 0, 13).get("ok") is True)
    lo = json.loads(g["iso_loadout"](5))["loadout"]
    chk("rune write persisted in save id space",
        lo["runes"][0]["kind"] == "rune" and lo["runes"][0]["runeName"] == names[13])
    chk("unlock write ok", setlo("rune", 0, -20).get("ok") is True)
    lo = json.loads(g["iso_loadout"](5))["loadout"]
    chk("unlock write persisted", lo["runes"][0]["kind"] == "unlock" and lo["runes"][0]["unlockLevel"] == 20)
    chk("rune id past save id space refused", "error" in setlo("rune", 0, 99))
    chk("unlock level past 99 refused", "error" in setlo("rune", 0, -100))
    chk("perm write ok", setlo("perm", 0, 1).get("ok") is True)
    chk("perm value 2 refused", "error" in setlo("perm", 0, 2))
    chk("skill write at the cap ok", setlo("skill", 1, 4).get("ok") is True)
    chk("skill write past the cap refused", "error" in setlo("skill", 1, 5))
    chk("skill rank past 7 refused", "error" in setlo("skill", 0, 8))
    lo = json.loads(g["iso_loadout"](5))["loadout"]
    chk("skill write persisted", {s["name"]: s for s in lo["skills"]}["Attack"]["start"] == 4)

    # PAL: the loadout tables are unmapped there, so the surface gates off
    F.set_region("pal")
    try:
        with P.Iso(slice_path) as h:
            chk("loadout gated in PAL", P.read_loadout(h, 5).get("gated") is True)
    finally:
        F.set_region("ntsc-u")

    # ---- real disc vs the lab enumeration + the real save's templates
    real_iso = os.environ.get("S5_ISO") or os.path.join(WEB, "..", "ISO", "suikoden5-us.iso")
    lab = os.environ.get("S5_LAB_DIR") or os.path.abspath(os.path.join(
        HERE, "..", "..", "..", "..", "ps2-re", "games", "suikoden5", "loadout_work"))
    enum_path = os.path.join(lab, "loadouts_elf.json")
    import glob as _glob
    _tpls = _glob.glob(os.path.join(lab, "templates_*.json"))
    tpl_path = _tpls[0] if _tpls else os.path.join(lab, "templates_missing.json")
    if not (os.path.exists(real_iso) and os.path.exists(enum_path)):
        print("SKIP real-disc loadout checks: no real ISO or lab enumeration")
    else:
        enum = json.load(open(enum_path))
        with P.Iso(real_iso) as h:
            mismatch = []
            for cid in range(120):
                got = P.build_loadout_enumeration(h, cid)
                want = enum[str(cid)]
                for key in ("level", "level_byte", "slots", "slot_names", "ranks"):
                    if got[key] != want[key]:
                        mismatch.append((cid, key, got[key], want[key]))
                for gr, wr in zip(got["runes"], want["runes"]):
                    for key in ("slot", "word", "perm_flag", "rune_id", "unlock_level", "rune"):
                        if gr.get(key) != wr.get(key):
                            mismatch.append((cid, "rune." + key, gr.get(key), wr.get(key)))
            chk("enumeration reproduced for all 120 characters (#28)",
                not mismatch, str(mismatch[:4]))

            if os.path.exists(tpl_path):
                tpl = json.load(open(tpl_path))
                # Ground-truth selection by the status-word rule on the
                # source save itself (issue #39). The templates file's own
                # "recruited" labels predate the correction (they were
                # read off the fake bitfield) and are not used.
                import s5save as SV
                src_path = os.path.join(os.path.expanduser("~"),
                    "workspace/suikoden5-debug/saves/library/sparda-us-slot00.payload.bin")
                src = open(src_path, "rb").read() if os.path.exists(src_path) else None
                if src is None:
                    print("SKIP template tally checks: source payload not found")
                    unrec = []
                else:
                    unrec = [int(k) for k in tpl
                             if not SV.recruit_state(src, int(k))["recruited"]]
                    chk("template set is the 84 word-unrecruited records (#39)",
                        len(unrec) == 84, str(len(unrec)))
                labels = [f[0] for f in F.TABLES["equipable skills"][2]]
                tally = {"slots": 0, "ranks": 0, "runes": 0, "level": 0, "armor": 0}
                miss = {"ranks": [], "runes": []}
                for cid in unrec:
                    t = tpl[str(cid)]
                    got = P.build_loadout_enumeration(h, cid)
                    lo = P.read_loadout(h, cid)
                    if (got["slots"] + [0, 0])[:2] == t["slots"]: tally["slots"] += 1
                    tranks = {labels[i]: v for i, v in enumerate(t["skills"]) if v}
                    if got["ranks"] == tranks: tally["ranks"] += 1
                    else: miss["ranks"].append(cid)
                    trunes = [r.get("rune_id", 0) for r in got["runes"]]
                    tflags = [r["perm_flag"] for r in got["runes"]]
                    if trunes == t["runes"] and tflags == t["rune_flags"]: tally["runes"] += 1
                    else: miss["runes"].append(cid)
                    if got["level"] == t["level"]: tally["level"] += 1
                    if lo["armor"] == t["armor"][:4]: tally["armor"] += 1
                chk("predicted equipped slots match the save 84/84 (#28)",
                    tally["slots"] == 84, str(tally))
                chk("predicted ranks match 82/84 with the measured exceptions (#28)",
                    tally["ranks"] == 82 and miss["ranks"] == [10, 60], str(miss["ranks"]))
                chk("predicted runes match 83/84 with the measured exception (#28)",
                    tally["runes"] == 83 and miss["runes"] == [72], str(miss["runes"]))
                chk("predicted levels match the measured 79/84 (#28)",
                    tally["level"] == 79, str(tally))
                chk("predicted armor matches the measured 82/84 (#28)",
                    tally["armor"] == 82, str(tally))
            else:
                print("SKIP template tally checks: no templates file")

    print("\n%d/%d passed" % (n[0] - bad[0], n[0]))
    return 1 if bad[0] else 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try: os.remove(slice_path)
        except OSError: pass
        try: os.remove(slice_path + ".s5mod.json")
        except OSError: pass
