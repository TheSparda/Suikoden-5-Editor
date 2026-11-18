#!/usr/bin/env python3
"""Code-patch toggle framework checks (Editor/s5toggles.py) on a temp copy
of the real NTSC-U front slice, plus the CT-survey toggles' PAL sites on a
PAL slice when one is present. Emits JSON on stdout for tests/toggles.mjs.
Skips cleanly when no NTSC-U ISO is present, as runes_always.py does.

Note: byte comparisons go through the Iso handle (or a fresh read after
the handle closes). The Iso object's writes are buffered, so a separate
file handle opened mid-block can see stale bytes at a just-written site;
that is a harness artifact, not a framework bug, and an earlier version
of this test failed on exactly that."""
import glob, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "Editor"))
import s5patch as P
import s5toggles as T

checks = []
def chk(name, cond, extra=""):
    checks.append({"name": name, "ok": bool(cond), "extra": str(extra)})

iso = None
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            if P.region_of(g) == "ntsc-u":
                iso = cand
                break
    except Exception:
        continue
if not iso:
    print(json.dumps({"skip": "no NTSC-U ISO in ISO/"}))
    sys.exit(0)

tmp = os.path.join(tempfile.gettempdir(), "s5_toggles_test.bin")
with open(iso, "rb") as f:
    pristine = f.read(0x6A0000)
with open(tmp, "wb") as o:
    o.write(pristine)
P.BACKUPS = False
P.RECORD_MODS = False
if P.set_region_for(tmp) != "ntsc-u":
    print(json.dumps({"skip": "slice is not NTSC-U"}))
    sys.exit(0)


def file_bytes():
    with open(tmp, "rb") as f:
        return f.read()


with P.Iso(tmp, writable=True) as g:
    sts = {s["id"]: s for s in T.list_toggles(g)}
    chk("registry holds the nine toggles",
        set(sts) == {"dawn-unlock", "fortune-always", "prosperity-always",
                     "godspeed-always", "training-sp-free", "training-sp-gain",
                     "train-ss-rank", "forge-once-max", "smith-forge-max"},
        ", ".join(sorted(sts)))
    chk("dawn is the proven toggle; the perks are labelled not-working",
        sts["dawn-unlock"]["effect"] == "proven"
        and all(sts[k]["effect"] == "not-working"
                for k in ("fortune-always", "prosperity-always", "godspeed-always")))
    chk("everything is off on a pristine disc",
        all(s["state"] == "off" for s in sts.values()),
        ", ".join("%s=%s" % (k, v["state"]) for k, v in sts.items()))
    chk("dawn site is unique (exactly one signature hit)",
        len(sts["dawn-unlock"]["sites"]) == 1)
    # The framework's gate scan must agree with s5patch's own finder.
    agree = True
    for tid, rid in (("fortune-always", 77), ("prosperity-always", 78),
                     ("godspeed-always", 82)):
        theirs = [x for x in P._rune_gate_sites(g) if x["runeId"] == rid]
        if len(theirs) != len(sts[tid]["sites"]):
            agree = False
    chk("framework gate scan agrees with s5patch's finder", agree)

    # Dawn: apply, verify, restore exactly (self-restoring).
    r = T.set_toggle(g, "dawn-unlock", True)
    chk("dawn apply changes one word", r["changed"] == 1)
    chk("dawn reads on after apply",
        T.toggle_status(g, "dawn-unlock")["state"] == "on")
    T.set_toggle(g, "dawn-unlock", False)
    chk("dawn reads off after restore",
        T.toggle_status(g, "dawn-unlock")["state"] == "off")

    # Perks: apply returns the originals; restore needs them and is exact.
    r = T.set_toggle(g, "fortune-always", True)
    chk("fortune apply NOPs both branch words of its gate",
        r["changed"] == 2, "changed %d" % r["changed"])
    chk("fortune reads on after apply",
        T.toggle_status(g, "fortune-always")["state"] == "on")
    try:
        T.set_toggle(g, "fortune-always", False)
        chk("fortune restore without originals refuses", False, "no error")
    except ValueError:
        chk("fortune restore without originals refuses", True)
    T.set_toggle(g, "fortune-always", False, r["originals"])
    chk("fortune reads off after restore",
        T.toggle_status(g, "fortune-always")["state"] == "off")

    # CT-survey toggles: signature-located, one word each, at the offsets
    # the disassembly proof established for NTSC-U.
    CT_US = {"training-sp-free": 0x31AAB0, "training-sp-gain": 0x31AAA8,
             "train-ss-rank": 0x27CEB8, "forge-once-max": 0x332DC8,
             "smith-forge-max": 0x345BA4}
    for tid, off in CT_US.items():
        st = sts[tid]
        chk("%s is code-verified, off, one site at 0x%X" % (tid, off),
            st["effect"] == "code-verified" and st["state"] == "off"
            and len(st["sites"]) == 1
            and st["sites"][0]["spans"][0]["off"] == off,
            "%s %s" % (st["effect"], st["state"]))
        r = T.set_toggle(g, tid, True)
        chk("%s apply changes one word and reads on" % tid,
            r["changed"] == 1
            and T.toggle_status(g, tid)["state"] == "on")
        T.set_toggle(g, tid, False)
        chk("%s reads off after restore" % tid,
            T.toggle_status(g, tid)["state"] == "off")
    # The two spendSP toggles share one function but patch different
    # words: both can be on at once and both stay located.
    T.set_toggle(g, "training-sp-free", True)
    T.set_toggle(g, "training-sp-gain", True)
    chk("both spendSP toggles on together stay located and on",
        T.toggle_status(g, "training-sp-free")["state"] == "on"
        and T.toggle_status(g, "training-sp-gain")["state"] == "on")
    T.set_toggle(g, "training-sp-free", False)
    T.set_toggle(g, "training-sp-gain", False)
chk("apply/restore cycles leave the file byte-identical", file_bytes() == pristine)

with P.Iso(tmp, writable=True) as g:
    # Foreign bytes: corrupt the dawn word into neither stock nor forced.
    site = T._dawn_sites(g)[0]["spans"][0]
    g.wu(site["off"], 4, 0xDEADBEEF)
    chk("a corrupted dawn word reads as foreign",
        T.toggle_status(g, "dawn-unlock")["state"] == "foreign")
    before = g.rd(0, len(pristine))
    try:
        T.set_toggle(g, "dawn-unlock", True)
        chk("apply over foreign bytes refuses", False, "no error")
    except ValueError:
        chk("apply over foreign bytes refuses", True)
    chk("the refused apply wrote nothing", g.rd(0, len(pristine)) == before)
    g.wu(site["off"], 4, 0x306400FF)   # back to stock

    # Half-patched gate: NOP only one branch word of Fortune's gate.
    spans = T._gate_scan(g, 77)[0]["spans"]
    orig_word = g.ru(spans[0]["off"], 4)
    g.wu(spans[0]["off"], 4, 0)
    chk("a half-patched gate reads as foreign",
        T.toggle_status(g, "fortune-always")["state"] == "foreign")
    before = g.rd(0, len(pristine))
    try:
        T.set_toggle(g, "fortune-always", True)
        chk("apply over a half-patched gate refuses", False, "no error")
    except ValueError:
        chk("apply over a half-patched gate refuses", True)
    chk("the refused gate apply wrote nothing", g.rd(0, len(pristine)) == before)
    g.wu(spans[0]["off"], 4, orig_word)   # repair

    # A CT toggle's span word corrupted to neither stock nor patched:
    # the invariant anchor still locates it and it reads foreign.
    site = T._forge_once_sites(g)[0]["spans"][0]
    g.wu(site["off"], 4, 0xDEADBEEF)
    chk("a corrupted forge word reads as foreign",
        T.toggle_status(g, "forge-once-max")["state"] == "foreign")
    try:
        T.set_toggle(g, "forge-once-max", True)
        chk("apply over a foreign forge word refuses", False, "no error")
    except ValueError:
        chk("apply over a foreign forge word refuses", True)
    g.wu(site["off"], 4, 0x24630001)   # back to stock
chk("foreign-byte experiments leave the file byte-identical",
    file_bytes() == pristine)

os.remove(tmp)

# ---------- PAL: the five CT toggles locate at their PAL offsets ----------
pal_iso = None
for cand in sorted(glob.glob(os.path.join(ROOT, "ISO", "*.iso"))):
    try:
        with P.Iso(cand) as g:
            if P.region_of(g) == "pal":
                pal_iso = cand
                break
    except Exception:
        continue
if pal_iso:
    tmp2 = os.path.join(tempfile.gettempdir(), "s5_toggles_test_pal.bin")
    with open(pal_iso, "rb") as f:
        pristine2 = f.read(0x6A0000)
    with open(tmp2, "wb") as o:
        o.write(pristine2)
    if P.set_region_for(tmp2) == "pal":
        CT_PAL = {"training-sp-free": 0x31B710, "training-sp-gain": 0x31B708,
                  "train-ss-rank": 0x27D6B8, "forge-once-max": 0x333BA8,
                  "smith-forge-max": 0x346954}
        with P.Iso(tmp2, writable=True) as g:
            for tid, off in CT_PAL.items():
                st = T.toggle_status(g, tid)
                chk("PAL %s is off, one site at 0x%X" % (tid, off),
                    st["state"] == "off" and len(st["sites"]) == 1
                    and st["sites"][0]["spans"][0]["off"] == off,
                    st["state"])
            T.set_toggle(g, "smith-forge-max", True)
            chk("PAL smith-forge-max reads on after apply",
                T.toggle_status(g, "smith-forge-max")["state"] == "on")
            T.set_toggle(g, "smith-forge-max", False)
        with open(tmp2, "rb") as f:
            chk("PAL apply/restore cycle leaves the file byte-identical",
                f.read() == pristine2)
    os.remove(tmp2)

print(json.dumps({"checks": checks}))
