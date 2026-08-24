#!/usr/bin/env python3
"""Unit tests for the equipment-set engine — NO real disc needed.

Set bonuses live in MIPS code, so this builds a synthetic detector, jump table and
handlers *from the engine's own constants* (F.SET_DETECT_OFF / SET_JT_OFF /
SET_EXIT_VADDR / VADDR_DELTA), then exercises the parser and all three writers plus
the description writer. Because the fixture is generated from those constants it can't
drift out of sync with the engine, and it runs anywhere python3 does.

Real-disc behaviour (that the offsets point at the *actual* game code) is covered
separately by tests/sets_iso.py, which asserts against the Suikosource guide."""
import os, sys, struct, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5fields as F, s5patch as P

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

# ---------------- minimal MIPS assembler (just what the fixture needs) -------------
ZERO, V0, V1, A0, A1, S0 = 0, 2, 3, 4, 5, 16
def _i(op, rs, rt, imm): return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
def lbu(rt, off, rs):   return _i(0x24, rs, rt, off)
def lh(rt, off, rs):    return _i(0x21, rs, rt, off)
def sh(rt, off, rs):    return _i(0x29, rs, rt, off)
def sb(rt, off, rs):    return _i(0x28, rs, rt, off)
def li(rt, imm):        return _i(0x09, ZERO, rt, imm)
def beq(rs, rt, here, target): return _i(0x04, rs, rt, (target - (here + 4)) >> 2)
def bne(rs, rt, here, target): return _i(0x05, rs, rt, (target - (here + 4)) >> 2)
def jr(rs=31):          return (rs << 21) | 8
NOP = 0

DET_V   = F.SET_DETECT_OFF + F.VADDR_DELTA          # detector vaddr
EXIT_V  = F.SET_EXIT_VADDR                          # shared "return set index" exit
SIZE    = 0x6A0000

def build_fixture(path):
    """Two sets: A (5 pieces incl. accessory -> index 1) and B (4 pieces -> index 4).
    A's handler `set`s a byte via a delay-slot store; B's handler `add`s to a halfword."""
    buf = bytearray(SIZE)
    buf[F.SERIAL_OFF:F.SERIAL_OFF + len(F.SERIAL_STR)] = F.SERIAL_STR

    def put(vaddr, words):
        off = vaddr - F.VADDR_DELTA
        buf[off:off + 4 * len(words)] = struct.pack("<%dI" % len(words), *words)

    HA, HB = DET_V + 0x80, DET_V + 0x140            # per-set blocks
    FAILA, FAILB = DET_V + 0x100, DET_V + 0x1C0

    # dispatch on the head id
    d = DET_V
    put(d, [
        lbu(V1, 68, A0),
        li(V0, 27), beq(V1, V0, d + 8, HA), NOP,     # set A head = 27
        li(V0, 11), beq(V1, V0, d + 20, HB), NOP,    # set B head = 11
        beq(ZERO, ZERO, d + 28, EXIT_V), li(V0, 0),  # no match -> index 0
    ])
    # set A: body 60, arm 30, foot 24, accessory 8 -> index 1
    a = HA
    put(a, [
        lbu(A1, 69, A0), li(V0, 60), bne(A1, V0, a + 8, FAILA), NOP,
        lbu(A1, 70, A0), li(V0, 30), bne(A1, V0, a + 24, FAILA), NOP,
        lbu(A1, 71, A0), li(V0, 24), bne(A1, V0, a + 40, FAILA), NOP,
        lbu(A1, F.SET_ACC_ID_OFF, A0), li(V0, 8), bne(A1, V0, a + 56, FAILA), NOP,
        beq(ZERO, ZERO, a + 64, EXIT_V), li(V0, 1),  # index 1 in the delay slot
    ])
    put(FAILA, [beq(ZERO, ZERO, FAILA, EXIT_V), li(V0, 0)])
    # set B: body 10, arm 8, foot 5 -> index 4 (no accessory requirement)
    b = HB
    put(b, [
        lbu(A1, 69, A0), li(V0, 10), bne(A1, V0, b + 8, FAILB), NOP,
        lbu(A1, 70, A0), li(V0, 8),  bne(A1, V0, b + 24, FAILB), NOP,
        lbu(A1, 71, A0), li(V0, 5),  bne(A1, V0, b + 40, FAILB), NOP,
        beq(ZERO, ZERO, b + 48, EXIT_V), li(V0, 4),
    ])
    put(FAILB, [beq(ZERO, ZERO, FAILB, EXIT_V), li(V0, 0)])
    put(EXIT_V, [jr(31), NOP])

    # handlers (outside the detector window) + the jump table
    H_SET = DET_V + 0x4E0       # `li`+delay-slot store  -> "set char+256 = 6"
    H_ADD = DET_V + 0x500       # read-modify-write       -> "add +50 to char+20"
    H_GATE = DET_V + 0x540      # per-character restriction, then a bonus
    put(H_SET, [li(V1, 6), beq(ZERO, ZERO, H_SET + 4, H_SET + 0x20), sb(V1, 256, S0)])
    put(H_ADD, [lh(V1, 20, S0), _i(0x09, V1, V1, 50), sh(V1, 20, S0),
                beq(ZERO, ZERO, H_ADD + 12, H_ADD + 0x20)])
    # gated handler: `lbu $v1,16($s0); bne $v1,$zero,skip` then +7 to char+40
    put(H_GATE, [lbu(V1, 16, S0), bne(V1, ZERO, H_GATE + 4, H_GATE + 0x20), NOP,
                 lh(V1, 40, S0), _i(0x09, V1, V1, 7), sh(V1, 40, S0),
                 beq(ZERO, ZERO, H_GATE + 24, H_GATE + 0x20)])
    jt = [F.SET_NOOP_VADDR] * F.SET_COUNT
    jt[1] = H_SET; jt[4] = H_ADD
    buf[F.SET_JT_OFF:F.SET_JT_OFF + 4 * F.SET_COUNT] = struct.pack("<%dI" % F.SET_COUNT, *jt)

    # one armor record with a name (at record-0x1C) and a description, for the text writers
    rec = P.armor_addr("head", 3)
    desc = "頭　直防＋１０".encode("cp932")
    buf[rec + F.ARMOR_SUMMARY_OFF:rec + F.ARMOR_SUMMARY_OFF + len(desc)] = desc
    nm = "レザーキャップ".encode("cp932")
    buf[rec - 0x1C:rec - 0x1C + len(nm)] = nm
    open(path, "wb").write(buf)
    return {"H_SET": H_SET, "H_ADD": H_ADD, "H_GATE": H_GATE}

tmp = os.path.join(tempfile.gettempdir(), "s5_sets_unit.bin")
P.BACKUPS = False; P.RECORD_MODS = False
F.set_region("ntsc-u")
H = build_fixture(tmp)

# ---------------- parse ----------------
with P.Iso(tmp) as g:
    d = P.read_sets(g)
by = {s["index"]: s for s in d["sets"]}
chk("parses both synthetic sets", sorted(by) == [1, 4], str(sorted(by)))
chk("set A members (incl. accessory)",
    [(m["slot"], m["id"]) for m in by[1]["members"]] ==
    [("head", 27), ("body", 60), ("arm", 30), ("foot", 24), ("accessory", 8)],
    str([(m["slot"], m["id"]) for m in by[1]["members"]]))
chk("set B members (no accessory)",
    [(m["slot"], m["id"]) for m in by[4]["members"]] ==
    [("head", 11), ("body", 10), ("arm", 8), ("foot", 5)],
    str([(m["slot"], m["id"]) for m in by[4]["members"]]))
chk("jump table read", by[1]["handler"] == H["H_SET"] and by[4]["handler"] == H["H_ADD"])
chk("delay-slot store decoded as a `set` effect",
    [(e["kind"], e["charOff"], e["value"]) for e in by[1]["effects"]] == [("set", 256, 6)],
    str(by[1]["effects"]))
chk("read-modify-write decoded as an `add` effect",
    [(e["kind"], e["charOff"], e["value"], e["width"]) for e in by[4]["effects"]] == [("add", 20, 50, "h")],
    str(by[4]["effects"]))
chk("member immediates carry a file offset", all(isinstance(m["off"], int) for m in by[1]["members"]))
chk("effects carry a file offset", all(e["immOff"] for e in by[4]["effects"]))
chk("noop detection", by[1]["noop"] is False)

# ---------------- write: membership ----------------
with P.Iso(tmp, writable=True) as g:
    r = P.write_set_member(g, 1, "body", 99)
chk("write_set_member returns the patched offset", r["ok"] and r["id"] == 99)
with P.Iso(tmp) as g: d2 = P.read_sets(g)
b2 = {s["index"]: s for s in d2["sets"]}
chk("membership write round-trips",
    next(m["id"] for m in b2[1]["members"] if m["slot"] == "body") == 99)
chk("sibling members untouched",
    [(m["slot"], m["id"]) for m in b2[1]["members"] if m["slot"] != "body"] ==
    [("head", 27), ("arm", 30), ("foot", 24), ("accessory", 8)])
chk("other set untouched", [(m["slot"], m["id"]) for m in b2[4]["members"]] ==
    [("head", 11), ("body", 10), ("arm", 8), ("foot", 5)])

# ---------------- write: bonus magnitude ----------------
with P.Iso(tmp, writable=True) as g: P.write_set_bonus(g, 4, 0, 1234)
with P.Iso(tmp) as g: d3 = P.read_sets(g)
chk("magnitude write round-trips",
    next(s for s in d3["sets"] if s["index"] == 4)["effects"][0]["value"] == 1234)
with P.Iso(tmp, writable=True) as g: P.write_set_bonus(g, 4, 0, 50)
with P.Iso(tmp) as g: d3b = P.read_sets(g)
chk("magnitude revert", next(s for s in d3b["sets"] if s["index"] == 4)["effects"][0]["value"] == 50)

# ---------------- write: effect reassignment (custom sets) ----------------
with P.Iso(tmp, writable=True) as g: P.write_set_handler(g, 1, H["H_ADD"])
with P.Iso(tmp) as g: d4 = P.read_sets(g)
s1 = next(s for s in d4["sets"] if s["index"] == 1)
chk("handler reassignment gives set A set B's bonus",
    s1["handler"] == H["H_ADD"] and [(e["kind"], e["charOff"], e["value"]) for e in s1["effects"]] == [("add", 20, 50)],
    str(s1["effects"]))
with P.Iso(tmp, writable=True) as g: P.write_set_handler(g, 1, F.SET_NOOP_VADDR)
with P.Iso(tmp) as g: d5 = P.read_sets(g)
chk("reassigning to the no-op clears the bonus",
    next(s for s in d5["sets"] if s["index"] == 1)["noop"] is True)

# ---------------- write: description (length-capped) ----------------
with P.Iso(tmp) as g: cap0 = P.armor_summary_cap(g, "head", 3)
with P.Iso(tmp, writable=True) as g: rd = P.write_armor_summary(g, "head", 3, "DEF+10")
with P.Iso(tmp) as g: after = P.read_armor_item(g, "head", 3)
chk("English description write took", after["summary"] == "DEF+10", repr(after["summary"]))
chk("cap covers string + padding, inside the stat block", 0 < cap0 <= (0x41 - F.ARMOR_SUMMARY_OFF), str(cap0))
# the cap must be STABLE after shortening, so a short text can be lengthened again
with P.Iso(tmp) as g: cap1 = P.armor_summary_cap(g, "head", 3)
chk("cap stays stable after shortening (can lengthen again)", cap1 == cap0, f"{cap1} vs {cap0}")
with P.Iso(tmp, writable=True) as g: P.write_armor_summary(g, "head", 3, "Water DEF +1 set")
with P.Iso(tmp) as g: relong = P.read_armor_item(g, "head", 3)["summary"]
chk("can write a LONGER description after a short one", relong == "Water DEF +1 set", repr(relong))
with P.Iso(tmp, writable=True) as g: P.write_armor_summary(g, "head", 3, "X" * 500)
with P.Iso(tmp) as g: longw = P.read_armor_item(g, "head", 3)["summary"]
chk("over-long description is truncated to the cap", len(longw.encode("cp932")) == cap0, str(len(longw)))
# the stat block that sits after the description must be intact
with P.Iso(tmp) as g:
    stat_ok = all(f["value"] == 0 for f in P.read_armor_item(g, "head", 3)["fields"] if f["label"] == "DEF")
chk("adjacent stat block not corrupted by the long write", stat_ok)

def raises_early(fn):
    try: fn(); return False
    except Exception: return True

# ---------------- per-character restriction (Sun's "Prince only") ----------------
with P.Iso(tmp, writable=True) as g: P.write_set_handler(g, 4, H["H_GATE"])
with P.Iso(tmp) as g: dg = P.read_sets(g)
sg = next(s for s in dg["sets"] if s["index"] == 4)
chk("restriction detected on a gated handler",
    sg["gate"] is not None and sg["gate"]["restricted"] is True and sg["gate"]["charOff"] == 16,
    str(sg["gate"]))
chk("the gated bonus still decodes",
    [(e["kind"], e["charOff"], e["value"]) for e in sg["effects"]] == [("add", 40, 7)], str(sg["effects"]))
orig_word = sg["gate"]["word"]
with P.Iso(tmp, writable=True) as g: P.write_set_gate(g, 4, False)
with P.Iso(tmp) as g: dg2 = P.read_sets(g)
sg2 = next(s for s in dg2["sets"] if s["index"] == 4)
chk("removing the restriction NOPs the branch",
    sg2["gate"]["restricted"] is False and sg2["gate"]["word"] == 0, str(sg2["gate"]))
chk("the bonus survives removing the restriction",
    [(e["kind"], e["charOff"], e["value"]) for e in sg2["effects"]] == [("add", 40, 7)], str(sg2["effects"]))
with P.Iso(tmp, writable=True) as g: P.write_set_gate(g, 4, True, orig_word)
with P.Iso(tmp) as g: dg3 = P.read_sets(g)
chk("restoring the restriction round-trips",
    next(s for s in dg3["sets"] if s["index"] == 4)["gate"]["word"] == orig_word)
with P.Iso(tmp) as g:
    chk("ungated handler reports no restriction",
        next(s for s in dg3["sets"] if s["index"] == 1)["gate"] is None or
        next(s for s in dg3["sets"] if s["index"] == 1)["gate"].get("restricted") is not True)
with P.Iso(tmp, writable=True) as g:
    P.write_set_gate(g, 4, False)          # NOP it out again
    def _no_word():
        P.write_set_gate(g, 4, True)       # enabling without the original word must fail
    chk("restoring without the original word is rejected", raises_early(_no_word))
# --- re-point the restriction at another character ---
with P.Iso(tmp, writable=True) as g: P.write_set_gate(g, 4, True, orig_word)   # restore first
with P.Iso(tmp) as g: g0 = next(s for s in P.read_sets(g)["sets"] if s["index"] == 4)["gate"]
chk("stock gate reports character 0", g0["charId"] == 0, str(g0))
with P.Iso(tmp, writable=True) as g:
    rr = P.write_set_gate_char(g, 4, 8, orig_word)              # restrict to character 8
chk("retarget synthesizes the two-instruction form", rr["mode"] == "synthesized", str(rr))
with P.Iso(tmp) as g:
    dr = P.read_sets(g); sr = next(s for s in dr["sets"] if s["index"] == 4)
chk("gate now reports character 8", sr["gate"]["charId"] == 8 and sr["gate"]["kind"] == "retargeted", str(sr["gate"]))
chk("retargeted gate is still 'restricted'", sr["gate"]["restricted"] is True)
chk("the bonus survives retargeting",
    [(e["kind"], e["charOff"], e["value"]) for e in sr["effects"]] == [("add", 40, 7)], str(sr["effects"]))
# the skip target must be preserved across the rewrite
with P.Iso(tmp) as g: chk("skip target preserved", sr["gate"]["target"] == g0["target"],
                          f"{hex(sr['gate']['target'])} vs {hex(g0['target'])}")
# changing it again patches in place (no re-synthesis)
with P.Iso(tmp, writable=True) as g: rr2 = P.write_set_gate_char(g, 4, 21)
chk("changing the character again patches in place", rr2["mode"] == "patched", str(rr2))
with P.Iso(tmp) as g:
    chk("gate reports character 21",
        next(s for s in P.read_sets(g)["sets"] if s["index"] == 4)["gate"]["charId"] == 21)
with P.Iso(tmp, writable=True) as g:
    chk("out-of-range character rejected", raises_early(lambda: P.write_set_gate_char(g, 4, 999)))
    chk("gating a no-op set rejected", raises_early(lambda: P.write_set_gate_char(g, 2, 5)))
# removing the restriction still works after retargeting
with P.Iso(tmp, writable=True) as g: P.write_set_gate(g, 4, False)
with P.Iso(tmp) as g:
    gg = next(s for s in P.read_sets(g)["sets"] if s["index"] == 4)["gate"]
chk("can still remove a retargeted restriction", gg["restricted"] is False, str(gg))
with P.Iso(tmp, writable=True) as g: P.write_set_handler(g, 4, H["H_ADD"])

# ---------------- gear name (in-record string at record-0x1C) ----------------
with P.Iso(tmp) as g:
    ncap = P.armor_name_cap(g, "head", 3)
    n0 = P.read_armor_item(g, "head", 3)["nameJp"]
chk("in-record name reads back", n0 == "レザーキャップ", repr(n0))
chk("name cap is inside the 0x18 window", 0 < ncap <= 0x18, str(ncap))
with P.Iso(tmp, writable=True) as g: rn = P.write_armor_name(g, "head", 3, "Leather Cap")
with P.Iso(tmp) as g: n1 = P.read_armor_item(g, "head", 3)["nameJp"]
chk("name write took", n1 == "Leather Cap", repr(n1))
with P.Iso(tmp) as g: chk("name cap stable after shortening", P.armor_name_cap(g, "head", 3) == ncap)
with P.Iso(tmp, writable=True) as g: P.write_armor_name(g, "head", 3, "Z" * 200)
with P.Iso(tmp) as g: n2 = P.read_armor_item(g, "head", 3)["nameJp"]
chk("over-long name truncated to the cap", len(n2.encode("cp932")) == ncap, str(len(n2)))
# the record's own fields (buy price at +0) must be untouched by the name write
with P.Iso(tmp) as g:
    buy = next(f["value"] for f in P.read_armor_item(g, "head", 3)["fields"] if f["label"] == "Buy price")
chk("record fields untouched by the name write", buy == 0, str(buy))
with P.Iso(tmp, writable=True) as g:
    chk("naming a nonexistent slot rejected", raises_early(lambda: P.write_armor_name(g, "nope", 3, "x")))

# ---------------- custom bonus (assembled into free code space) ----------------
capn = P.read_custom_set_capacity()
chk("capacity derived from the gap size", capn["words"] == F.SET_CUSTOM_LEN // 4 and capn["maxAdd"] >= 4, str(capn))
tg = P.set_effect_targets()
chk("targets carry a kind", all("kind" in t for t in tg) and
    {t["kind"] for t in tg} == {"num", "grade"}, str({t["kind"] for t in tg}))
chk("affinities are graded, stats are numeric",
    all(t["kind"] == "grade" for t in tg if "affinity" in t["label"]) and
    all(t["kind"] == "num" for t in tg if "affinity" not in t["label"]))
chk("grade names exposed", P.set_grade_names()[6] == "S" and P.set_grade_names()[0] == "None")
with P.Iso(tmp, writable=True) as g:
    chk("a rank cannot be ADDED to", raises_early(lambda: P.write_custom_set_bonus(
        g, 2, [{"charOff": 256, "width": "b", "op": "add", "value": 1}])))
    chk("a rank above the scale is rejected", raises_early(lambda: P.write_custom_set_bonus(
        g, 2, [{"charOff": 256, "width": "b", "op": "set", "value": 9}])))
    r_ok = P.write_custom_set_bonus(g, 2, [{"charOff": 256, "width": "b", "op": "set", "value": 6}])
chk("a valid rank is accepted", r_ok["ok"])
chk("effect catalog exposes verified + inferred targets",
    any(t["verified"] for t in P.set_effect_targets()) and any(not t["verified"] for t in P.set_effect_targets()))
custom = [{"charOff": 20,  "width": "h", "op": "add", "value": 300},
          {"charOff": 304, "width": "b", "op": "add", "value": 30},
          {"charOff": 256, "width": "b", "op": "set", "value": 6}]
with P.Iso(tmp, writable=True) as g: rc = P.write_custom_set_bonus(g, 2, custom)
chk("custom bonus assembled", rc["ok"] and rc["handler"] == F.SET_CUSTOM_VADDR, str(rc))
with P.Iso(tmp) as g: dc = P.read_sets(g)
sc = next((x for x in dc["sets"] if x["index"] == 2), None)
if sc is None:   # index 2 isn't in the synthetic fixture -> verify via the table + decode
    with P.Iso(tmp) as g:
        jt = struct.unpack("<%dI" % F.SET_COUNT, g.rd(F.SET_JT_OFF, F.SET_COUNT * 4))
        decoded = P._set_effects(g, F.SET_CUSTOM_VADDR)
    chk("jump table entry points at the custom handler", jt[2] == F.SET_CUSTOM_VADDR, hex(jt[2]))
else:
    decoded = sc["effects"]
    chk("jump table entry points at the custom handler", sc["handler"] == F.SET_CUSTOM_VADDR)
chk("all custom effects decode back",
    [(e["kind"], e["charOff"], e["value"]) for e in decoded] ==
    [("add", 20, 300), ("add", 304, 30), ("set", 256, 6)], str(decoded))
with P.Iso(tmp) as g:
    ws2 = struct.unpack("<%dI" % (F.SET_CUSTOM_LEN // 4), g.rd(F.SET_CUSTOM_VADDR - F.VADDR_DELTA, F.SET_CUSTOM_LEN))
jidx = [i for i, w in enumerate(ws2) if (w >> 26) == 2]
chk("tail is a j to the shared epilogue",
    bool(jidx) and ((ws2[jidx[0]] & 0x3FFFFFF) << 2) == F.SET_RETURN_VADDR)
chk("delay slot after the j is a nop", ws2[jidx[0] + 1] == 0)
chk("unused gap bytes are zeroed", all(w == 0 for w in ws2[jidx[0] + 2:]))
with P.Iso(tmp, writable=True) as g:
    chk("over-capacity rejected", raises_early(lambda: P.write_custom_set_bonus(
        g, 2, [{"charOff": 20, "width": "h", "op": "add", "value": 1}] * 12)))
    chk("unknown target rejected", raises_early(lambda: P.write_custom_set_bonus(
        g, 2, [{"charOff": 4242, "width": "h", "op": "add", "value": 1}])))
    chk("bad op rejected", raises_early(lambda: P.write_custom_set_bonus(
        g, 2, [{"charOff": 20, "width": "h", "op": "multiply", "value": 1}])))
    chk("out-of-range value rejected", raises_early(lambda: P.write_custom_set_bonus(
        g, 2, [{"charOff": 304, "width": "b", "op": "set", "value": 5000}])))
# handing the set back to the stock no-op clears it
with P.Iso(tmp, writable=True) as g: P.write_set_handler(g, 2, F.SET_NOOP_VADDR)
with P.Iso(tmp) as g:
    jt2 = struct.unpack("<%dI" % F.SET_COUNT, g.rd(F.SET_JT_OFF, F.SET_COUNT * 4))
chk("revertible to no bonus", jt2[2] == F.SET_NOOP_VADDR)

# ---------------- error handling ----------------
def raises(fn):
    try: fn(); return False
    except Exception: return True
with P.Iso(tmp, writable=True) as g:
    chk("unknown set index rejected", raises(lambda: P.write_set_member(g, 99, "head", 1)))
    chk("unknown slot rejected", raises(lambda: P.write_set_member(g, 4, "accessory", 1)))
    chk("out-of-range equip id rejected", raises(lambda: P.write_set_member(g, 1, "head", 999)))
    chk("bad effect index rejected", raises(lambda: P.write_set_bonus(g, 1, 7, 1)))
    chk("bad set index for handler rejected", raises(lambda: P.write_set_handler(g, 42, 0)))

os.remove(tmp)
print("\n%d/%d passed" % (n - bad, n))
sys.exit(1 if bad else 0)
