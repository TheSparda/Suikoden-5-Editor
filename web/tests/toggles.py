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
    chk("registry holds the fourteen toggles",
        set(sts) == {"dawn-unlock", "champions-always", "firefly-always",
                     "fortune-always", "prosperity-always",
                     "godspeed-always", "godspeed-dash", "training-sp-free",
                     "training-sp-gain", "train-ss-rank", "forge-once-max",
                     "smith-forge-max", "sig-skills-normal",
                     "gear-requirements-off"},
        ", ".join(sorted(sts)))
    chk("dawn is the proven toggle; the perks are labelled not-working",
        sts["dawn-unlock"]["effect"] == "proven"
        and all(sts[k]["effect"] == "not-working"
                for k in ("fortune-always", "prosperity-always", "godspeed-always")))
    chk("all five rune perks sit in the Rune perks group; the encounter "
        "runes are code-verified",
        all(sts[k]["group"] == "Rune perks: always on"
            for k in ("champions-always", "firefly-always", "fortune-always",
                      "prosperity-always", "godspeed-always"))
        and sts["champions-always"]["effect"] == "code-verified"
        and sts["firefly-always"]["effect"] == "code-verified")
    chk("gate sites are found for Champion's (79) and Great Firefly (80)",
        len(sts["champions-always"]["sites"]) >= 1
        and len(sts["firefly-always"]["sites"]) >= 1
        and sts["champions-always"]["state"] == "off"
        and sts["firefly-always"]["state"] == "off",
        "%d / %d sites" % (len(sts["champions-always"]["sites"]),
                           len(sts["firefly-always"]["sites"])))
    chk("everything is off on a pristine disc",
        all(s["state"] == "off" for s in sts.values()),
        ", ".join("%s=%s" % (k, v["state"]) for k, v in sts.items()))
    chk("dawn site is unique (exactly one signature hit)",
        len(sts["dawn-unlock"]["sites"]) == 1)
    # The framework's gate scan must agree with s5patch's own finder.
    agree = True
    for tid, rid in (("fortune-always", 77), ("prosperity-always", 78),
                     ("godspeed-always", 82), ("champions-always", 79),
                     ("firefly-always", 80)):
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

    # Champion's: the same gate contract for a new registry toggle.
    nspans = sum(len(s["spans"]) for s in sts["champions-always"]["sites"])
    r = T.set_toggle(g, "champions-always", True)
    chk("champions apply NOPs both branch words of every gate",
        r["changed"] == nspans, "changed %d of %d" % (r["changed"], nspans))
    chk("champions reads on after apply",
        T.toggle_status(g, "champions-always")["state"] == "on")
    try:
        T.set_toggle(g, "champions-always", False)
        chk("champions restore without originals refuses", False, "no error")
    except ValueError:
        chk("champions restore without originals refuses", True)
    T.set_toggle(g, "champions-always", False, r["originals"])
    chk("champions reads off after restore",
        T.toggle_status(g, "champions-always")["state"] == "off")

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

    # Signature-skill toggle (issue #16): the two range compares of
    # func_0043A430, located by the mode 2 constant-chain anchor at the
    # offsets the in-game proof established for NTSC-U. One toggle, two
    # sites, both words patched and restored together.
    SIG_US = (0x3E7DC0, 0x3E7DD8)
    st = sts["sig-skills-normal"]
    chk("sig-skills-normal is proven, off, two sites at 0x3E7DC0/0x3E7DD8",
        st["effect"] == "proven" and st["state"] == "off"
        and len(st["sites"]) == 2
        and [s["spans"][0]["off"] for s in st["sites"]] == list(SIG_US),
        "%s %s" % (st["effect"], st["state"]))
    r = T.set_toggle(g, "sig-skills-normal", True)
    chk("sig-skills-normal apply changes both compare words",
        r["changed"] == 2
        and [g.ru(o, 4) for o in SIG_US] == [0x28410031, 0x28610031],
        "changed %d" % r["changed"])
    chk("sig-skills-normal reads on after apply",
        T.toggle_status(g, "sig-skills-normal")["state"] == "on")
    others = {s["id"]: s["state"] for s in T.list_toggles(g)
              if s["id"] != "sig-skills-normal"}
    chk("the other toggles are unaffected while sig-skills-normal is on",
        all(others[k] == sts[k]["state"] for k in others),
        ", ".join("%s=%s" % (k, v) for k, v in sorted(others.items())))
    T.set_toggle(g, "sig-skills-normal", False)
    chk("sig-skills-normal reads off after restore",
        T.toggle_status(g, "sig-skills-normal")["state"] == "off")
    # Half-patched (one compare widened by hand): reads mixed, and apply
    # completes the pair rather than refusing or double-writing.
    g.wu(SIG_US[0], 4, 0x28410031)
    chk("a half-patched sig-skills-normal reads as mixed",
        T.toggle_status(g, "sig-skills-normal")["state"] == "mixed")
    r = T.set_toggle(g, "sig-skills-normal", True)
    chk("apply over the half-patched pair changes only the stock word",
        r["changed"] == 1
        and T.toggle_status(g, "sig-skills-normal")["state"] == "on")
    T.set_toggle(g, "sig-skills-normal", False)

    # Gear requirements toggle (issue #49): the four slti gate words of
    # the can-equip predicate at their traced NTSC-U offsets. Apply
    # writes the disable immediates (0 / 0 / 0 / 0x7FFF, the lab's
    # proven disable ISO: exactly 7 bytes differ from stock) and
    # restore returns the four stock words.
    GEAR_US = (0x337ECC, 0x337EE8, 0x337F24, 0x337F44)
    st = sts["gear-requirements-off"]
    chk("gear-requirements-off is proven, off, four sites at the traced offsets",
        st["effect"] == "proven" and st["state"] == "off"
        and len(st["sites"]) == 4
        and [s["spans"][0]["off"] for s in st["sites"]] == list(GEAR_US),
        "%s %s" % (st["effect"], st["state"]))
    before = g.rd(0, len(pristine))
    r = T.set_toggle(g, "gear-requirements-off", True)
    chk("gear-requirements-off apply changes the four gate words",
        r["changed"] == 4
        and [g.ru(o, 4) for o in GEAR_US]
            == [0x28420000, 0x28420000, 0x28420000, 0x28427FFF],
        "changed %d" % r["changed"])
    after = g.rd(0, len(pristine))
    diff = [i for i in range(len(pristine)) if before[i] != after[i]]
    chk("gear-requirements-off apply touches no byte outside the four gate words",
        len(diff) == 7
        and all(any(o <= i < o + 4 for o in GEAR_US) for i in diff),
        "%d bytes differ" % len(diff))
    chk("gear-requirements-off reads on after apply",
        T.toggle_status(g, "gear-requirements-off")["state"] == "on")
    T.set_toggle(g, "gear-requirements-off", False)
    chk("gear-requirements-off reads off after restore",
        T.toggle_status(g, "gear-requirements-off")["state"] == "off")
    chk("gear-requirements-off restore returns the four stock words",
        [g.ru(o, 4) for o in GEAR_US]
            == [0x2842015E, 0x28420064, 0x2842012C, 0x2842001F])

    # Godspeed dash: one site, the field motor's carry-byte skip branch.
    st = sts["godspeed-dash"]
    chk("godspeed-dash is the proven Dash tuning toggle",
        st["effect"] == "proven" and st["group"] == "Dash tuning"
        and st["state"] == "off" and len(st["sites"]) == 1
        and st["sites"][0]["spans"][0]["off"] == 0x1F5288,
        "%s %s %s" % (st["effect"], st.get("group"), st["state"]))
    before = g.rd(0, len(pristine))
    r = T.set_toggle(g, "godspeed-dash", True)
    chk("godspeed-dash apply NOPs the skip branch",
        r["changed"] == 1 and g.ru(0x1F5288, 4) == 0,
        "changed %d" % r["changed"])
    after = g.rd(0, len(pristine))
    diff = [i for i in range(len(pristine)) if before[i] != after[i]]
    chk("godspeed-dash apply touches only the branch word",
        len(diff) == 4 and all(0x1F5288 <= i < 0x1F528C for i in diff),
        "%d bytes differ" % len(diff))
    chk("godspeed-dash reads on after apply",
        T.toggle_status(g, "godspeed-dash")["state"] == "on")
    chk("godspeed-dash leaves the resolver-gate toggle off",
        T.toggle_status(g, "godspeed-always")["state"] == "off")
    T.set_toggle(g, "godspeed-dash", False)
    chk("godspeed-dash restore returns the stock branch word",
        g.ru(0x1F5288, 4) == 0x1040FF2F
        and T.toggle_status(g, "godspeed-dash")["state"] == "off")
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

    # Half-patched Champion's gate: NOP only one branch word.
    spans = T._gate_scan(g, 79)[0]["spans"]
    orig_word = g.ru(spans[0]["off"], 4)
    g.wu(spans[0]["off"], 4, 0)
    chk("a half-patched champions gate reads as foreign",
        T.toggle_status(g, "champions-always")["state"] == "foreign")
    before = g.rd(0, len(pristine))
    try:
        T.set_toggle(g, "champions-always", True)
        chk("apply over a half-patched champions gate refuses", False, "no error")
    except ValueError:
        chk("apply over a half-patched champions gate refuses", True)
    chk("the refused champions apply wrote nothing", g.rd(0, len(pristine)) == before)
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

    # One sig-skill compare corrupted: the anchor still locates the
    # pair, the toggle reads foreign, and apply refuses before writing
    # anything (the other compare must stay stock).
    g.wu(0x3E7DD8, 4, 0x28610099)
    chk("a corrupted sig-skill compare reads as foreign",
        T.toggle_status(g, "sig-skills-normal")["state"] == "foreign")
    before = g.rd(0, len(pristine))
    try:
        T.set_toggle(g, "sig-skills-normal", True)
        chk("apply over a foreign sig-skill compare refuses", False, "no error")
    except ValueError:
        chk("apply over a foreign sig-skill compare refuses", True)
    chk("the refused sig-skill apply wrote nothing",
        g.rd(0, len(pristine)) == before)
    g.wu(0x3E7DD8, 4, 0x28610017)   # back to stock

    # A gear gate word corrupted to a non-slti word: the toggle reads
    # foreign and refuses before writing anything.
    g.wu(0x337F44, 4, 0xDEADBEEF)
    chk("a corrupted gear gate word reads as foreign",
        T.toggle_status(g, "gear-requirements-off")["state"] == "foreign")
    before = g.rd(0, len(pristine))
    try:
        T.set_toggle(g, "gear-requirements-off", True)
        chk("apply over a foreign gear gate word refuses", False, "no error")
    except ValueError:
        chk("apply over a foreign gear gate word refuses", True)
    chk("the refused gear apply wrote nothing",
        g.rd(0, len(pristine)) == before)
    g.wu(0x337F44, 4, 0x2842001F)   # back to stock

    # A gate word holding a custom threshold (a Gear view field edit)
    # is neither the stock nor the toggle's patched form: foreign by
    # the same rule, so the toggle never overwrites a user's threshold.
    g.wu(0x337ECC, 4, 0x284200C8)   # Shell HP threshold 200
    chk("a custom gear threshold reads as foreign to the toggle",
        T.toggle_status(g, "gear-requirements-off")["state"] == "foreign")
    g.wu(0x337ECC, 4, 0x2842015E)   # back to stock

    # The dash gate word corrupted to neither stock nor NOP: foreign,
    # and apply refuses before writing anything.
    g.wu(0x1F5288, 4, 0xDEADBEEF)
    chk("a corrupted dash gate word reads as foreign",
        T.toggle_status(g, "godspeed-dash")["state"] == "foreign")
    before = g.rd(0, len(pristine))
    try:
        T.set_toggle(g, "godspeed-dash", True)
        chk("apply over a foreign dash gate word refuses", False, "no error")
    except ValueError:
        chk("apply over a foreign dash gate word refuses", True)
    chk("the refused dash apply wrote nothing", g.rd(0, len(pristine)) == before)
    g.wu(0x1F5288, 4, 0x1040FF2F)   # back to stock

    # The getter anchor above the branch corrupted: the finder locates
    # no site at all, so the toggle reads not-found rather than foreign.
    g.wu(0x1F527C, 4, 0xDEADBEEF)
    chk("a corrupted dash getter anchor reads as not-found",
        T.toggle_status(g, "godspeed-dash")["state"] == "not-found")
    g.wu(0x1F527C, 4, 0x0C0DB4F8)   # back to stock
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
            # Signature-skill toggle: the PAL predicate is byte-identical,
            # so the same anchor lands at the PAL offsets.
            SIG_PAL = (0x3E9580, 0x3E9598)
            st = T.toggle_status(g, "sig-skills-normal")
            chk("PAL sig-skills-normal is off, two sites at 0x3E9580/0x3E9598",
                st["state"] == "off" and len(st["sites"]) == 2
                and [s["spans"][0]["off"] for s in st["sites"]] == list(SIG_PAL),
                st["state"])
            r = T.set_toggle(g, "sig-skills-normal", True)
            chk("PAL sig-skills-normal apply changes both compare words",
                r["changed"] == 2
                and [g.ru(o, 4) for o in SIG_PAL] == [0x28410031, 0x28610031])
            chk("PAL sig-skills-normal reads on after apply",
                T.toggle_status(g, "sig-skills-normal")["state"] == "on")
            T.set_toggle(g, "sig-skills-normal", False)
            chk("PAL sig-skills-normal reads off after restore",
                T.toggle_status(g, "sig-skills-normal")["state"] == "off")
            # Gear requirements toggle: the PAL gate words sit at the
            # PAL offsets with identical stock words.
            GEAR_PAL = (0x338C9C, 0x338CB8, 0x338CF4, 0x338D14)
            st = T.toggle_status(g, "gear-requirements-off")
            chk("PAL gear-requirements-off is off, four sites at the PAL offsets",
                st["state"] == "off" and len(st["sites"]) == 4
                and [s["spans"][0]["off"] for s in st["sites"]] == list(GEAR_PAL),
                st["state"])
            r = T.set_toggle(g, "gear-requirements-off", True)
            chk("PAL gear-requirements-off apply changes the four gate words",
                r["changed"] == 4
                and [g.ru(o, 4) for o in GEAR_PAL]
                    == [0x28420000, 0x28420000, 0x28420000, 0x28427FFF])
            chk("PAL gear-requirements-off reads on after apply",
                T.toggle_status(g, "gear-requirements-off")["state"] == "on")
            T.set_toggle(g, "gear-requirements-off", False)
            chk("PAL gear-requirements-off reads off after restore",
                T.toggle_status(g, "gear-requirements-off")["state"] == "off")
            # Godspeed dash: the PAL skip branch sits at its PAL offset
            # with the identical stock word.
            st = T.toggle_status(g, "godspeed-dash")
            chk("PAL godspeed-dash is off, one site at 0x1F57E8",
                st["state"] == "off" and len(st["sites"]) == 1
                and st["sites"][0]["spans"][0]["off"] == 0x1F57E8,
                st["state"])
            r = T.set_toggle(g, "godspeed-dash", True)
            chk("PAL godspeed-dash apply NOPs its branch word",
                r["changed"] == 1 and g.ru(0x1F57E8, 4) == 0
                and T.toggle_status(g, "godspeed-dash")["state"] == "on")
            T.set_toggle(g, "godspeed-dash", False)
            chk("PAL godspeed-dash restore returns the stock word",
                g.ru(0x1F57E8, 4) == 0x1040FF2F
                and T.toggle_status(g, "godspeed-dash")["state"] == "off")
        with open(tmp2, "rb") as f:
            chk("PAL apply/restore cycle leaves the file byte-identical",
                f.read() == pristine2)
    os.remove(tmp2)

# --- Recipe back-compat -------------------------------------------------------
# Recipes (.s5mod) store raw byte runs ({off, old, new}), never edit keys,
# so a recipe exported by v1.26.0 or earlier, when the Passives view wrote
# Champion's gate NOPs through its own engine path, must still land after
# the merge: the same bytes at the same offsets, and the registry toggle
# (which reads its state from the disc bytes) must read on afterwards.
tmp3 = os.path.join(tempfile.gettempdir(), "s5_toggles_recipe_test.bin")
with open(tmp3, "wb") as o:
    o.write(pristine)
if P.set_region_for(tmp3) == "ntsc-u":
    import s5fields as F
    with P.Iso(tmp3) as g:
        serial = g.rd(F.SERIAL_OFF, len(F.SERIAL_STR)).decode("latin1", "replace")
        scan = T._gate_scan(g, 79)
        gate_offs = sorted(sp["off"] for s in P._rune_gate_sites(g)
                           if s["runeId"] == 79
                           for sp in ({"off": s["off1"]}, {"off": s["off2"]}))
    patches = [{"off": sp["off"], "old": sp["cur"].hex(), "new": "00000000"}
               for s in scan for sp in s["spans"]]
    chk("the synthetic old-format recipe covers exactly the gate spans",
        sorted(p["off"] for p in patches) == gate_offs,
        "%d runs" % len(patches))
    r = P.apply_mod(tmp3, {"format": "s5mod", "version": 1, "serial": serial,
                           "note": "web editor", "patchCount": len(patches),
                           "patches": patches}, make_backup=False)
    chk("the old-format recipe applies with no mismatched runs",
        r["mismatchedRuns"] == 0 and r["patchCount"] == len(patches)
        and r["appliedBytes"] == 4 * len(patches), str(r))
    with P.Iso(tmp3) as g:
        chk("after the recipe, champions-always reads on in the registry",
            T.toggle_status(g, "champions-always")["state"] == "on")
        chk("after the recipe, the other rune perks stay off",
            all(T.toggle_status(g, k)["state"] == "off"
                for k in ("firefly-always", "fortune-always",
                          "prosperity-always", "godspeed-always")))
    os.remove(tmp3)

print(json.dumps({"checks": checks}))
