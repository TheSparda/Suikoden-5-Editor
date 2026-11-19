#!/usr/bin/env python3
"""Recruitment status-word suite (issue #39).

The editor used to read and write a "recruitment bitfield" at payload
+0xD7CA. That field is fake: the bytes are the tail of an unrelated
record array, the reads were wrong on genuine saves, and the writes
corrupted data (RECRUIT_FIELD_AUDIT.md). Recruitment is now read from
the game's own per-character status word, which persists at payload
0x2A8 + (idx+1)*0x160 + 0xA0 (CHEAT_TABLE_RECRUITMENT.md). Writing that
word was later proven on a roster screen in both directions
(ROSTER_VERIFY.md), so a per-character toggle writes it through
s5save.set_recruited; Recruit All and every bulk path stay gone.

This suite pins:
- the word read law and the display rule on the three genuine saves
  (Kevin US, Eachna early PAL, Eachna late PAL), including the exact
  case from the bug report (Nikea, Miakis, Viki, Faylen in the party
  reading not recruited);
- the state mapping on synthetic words, including the 0x13/0x17
  high-byte variants and party membership taking precedence;
- write confinement: no edit other than a 'rec' toggle may move any
  status word or the old fake-field region;
- the toggle write path: canonical values (0x0302 recruit, 0x0100
  unrecruit) at the reader's slot, a two-byte footprint, read-state
  round trips, no-ops that move nothing, and the guards (party member,
  no record, non-recruitable) refusing with ValueError;
- no bulk write paths in either frontend.
Real-save sections skip when a fixture is absent.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ED = os.path.abspath(os.path.join(HERE, "..", "..", "Editor"))
WEB = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ED)
import s5save as SV

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

HOME = os.path.expanduser("~")
def load_psu(rel):
    p = os.path.join(HOME, rel)
    if not os.path.exists(p):
        return None
    return SV.read_gamedata_payload(p)

us_path = os.path.join(HOME, "workspace/suikoden5-debug/saves/library/sparda-us-slot00.payload.bin")
us = open(us_path, "rb").read() if os.path.exists(us_path) else None
early = load_psu("workspace/user/files/SuikodenV_02NIV49_062_35.psu")
late = load_psu("workspace/user/files/SuikodenV_03NIV53_075_31.psu")

FAKE_LO, FAKE_HI = 0xD7CA, 0xD7CA + 15

# ---------- the read law, on every genuine save available ----------
for label, gd in (("US", us), ("PAL early", early), ("PAL late", late)):
    if gd is None:
        print(f"SKIP {label} word-law checks: fixture not found")
        continue
    ok = all(SV.status_word(gd, i) ==
             int.from_bytes(gd[SV.CHAR_BASE + (i + 1) * SV.CHAR_STRIDE + SV.STATUS_WORD_OFF:
                               SV.CHAR_BASE + (i + 1) * SV.CHAR_STRIDE + SV.STATUS_WORD_OFF + 2],
                            "little")
             for i in range(SV.NUM_CHARS))
    chk(f"{label}: status word reads at 0x2A8 + (idx+1)*0x160 + 0xA0 for all 120", ok)
    words = [SV.status_word(gd, i) for i in range(SV.NUM_CHARS)]
    chk(f"{label}: every high byte is a known band (00/01/03/13/17)",
        all((w >> 8) in (0x00, 0x01, 0x03, 0x13, 0x17) for w in words))

# ---------- display mapping on the genuine saves ----------
if us:
    def st(gd, i): return SV.recruit_state(gd, i)
    chk("US: Richard initialized, not recruited (he never joined)",
        st(us, 50)["state"] == "initialized" and not st(us, 50)["recruited"]
        and st(us, 50)["word"] == 0x0100)
    chk("US: Wabon and Dongo have no record",
        st(us, 51)["state"] == "none" and not st(us, 51)["recruited"]
        and st(us, 87)["state"] == "none" and not st(us, 87)["recruited"])
    chk("US: Luserina active (automatic recruit the fake field read as clear)",
        st(us, 52)["state"] == "active" and st(us, 52)["recruited"])
    chk("US: Sialeeds active during her story stint",
        st(us, 112)["state"] == "active" and st(us, 112)["word"] == 0x0313)
    chk("US: Bastan initialized, not recruited",
        st(us, 46)["state"] == "initialized" and st(us, 46)["word"] == 0x0100)
    chk("US: Eresh none, Euram initialized (neither rival recruited)",
        st(us, 10)["state"] == "none" and st(us, 108)["state"] == "initialized")
    chk("US: party members read as party",
        all(st(us, i)["state"] == "party" for i in (0, 8, 48, 19, 35, 81)))
if early:
    # the bug report that opened issue #39: these four were in Eachna's
    # party while the editor showed them not recruited
    for idx, nm in ((97, "Nikea"), (72, "Miakis"), (77, "Viki"), (100, "Faylen")):
        chk(f"PAL early: {nm} reads recruited (in party)",
            SV.recruit_state(early, idx)["recruited"]
            and SV.recruit_state(early, idx)["state"] == "party")
    chk("PAL early: Eresh recruited, Euram not (the organic pair state)",
        SV.recruit_state(early, 10)["recruited"]
        and not SV.recruit_state(early, 108)["recruited"])
if late:
    chk("PAL late: Bastan still not recruited (initialized only)",
        SV.recruit_state(late, 46)["state"] == "initialized"
        and not SV.recruit_state(late, 46)["recruited"])
    chk("PAL late: Wabon active (recruited by then)",
        SV.recruit_state(late, 51)["state"] == "active"
        and SV.recruit_state(late, 51)["recruited"])
    chk("PAL late: Eresh recruited, Euram locked out",
        SV.recruit_state(late, 10)["recruited"]
        and not SV.recruit_state(late, 108)["recruited"])

# ---------- state mapping on synthetic words ----------
gd = bytearray(SV.GAMEDATA_SIZE)
def set_word(idx, w):
    off = SV.CHAR_BASE + (idx + 1) * SV.CHAR_STRIDE + SV.STATUS_WORD_OFF
    gd[off:off + 2] = w.to_bytes(2, "little")
set_word(1, 0x0000); set_word(2, 0x0100); set_word(3, 0x0302)
set_word(4, 0x1301); set_word(5, 0x1712); set_word(6, 0x0200)
gd = bytes(gd)
chk("synthetic: 0x0000 is none", SV.recruit_state(gd, 1)["state"] == "none")
chk("synthetic: 0x0100 is initialized, not recruited",
    SV.recruit_state(gd, 2)["state"] == "initialized"
    and not SV.recruit_state(gd, 2)["recruited"])
chk("synthetic: 0x0302 is active, recruited",
    SV.recruit_state(gd, 3)["state"] == "active" and SV.recruit_state(gd, 3)["recruited"])
chk("synthetic: 0x1301 is active (high-byte variant)",
    SV.recruit_state(gd, 4)["state"] == "active" and SV.recruit_state(gd, 4)["recruited"])
chk("synthetic: 0x1712 is active (high-byte variant)",
    SV.recruit_state(gd, 5)["state"] == "active" and SV.recruit_state(gd, 5)["recruited"])
chk("synthetic: 0x0200 is not active (band needs both low bits)",
    SV.recruit_state(gd, 6)["state"] == "initialized"
    and not SV.recruit_state(gd, 6)["recruited"])
# party membership takes precedence over an untouched word
gd2 = bytearray(gd)
gd2[SV.PARTY_BATTLE_OFF + 2] = 2
chk("synthetic: party member with an initialized word reads party/recruited",
    SV.recruit_state(bytes(gd2), 2)["state"] == "party"
    and SV.recruit_state(bytes(gd2), 2)["recruited"])

# ---------- write confinement ----------
if us:
    words_before = [SV.status_word(us, i) for i in range(SV.NUM_CHARS)]
    g2, _ = SV.apply_gamedata_edits(us, {"party9": 58, "c58_lv": 40,
                                         "c58_sk0": 5, "potch": 999})
    chk("confinement: no status word moves under party/char/header edits",
        [SV.status_word(g2, i) for i in range(SV.NUM_CHARS)] == words_before)
    chk("confinement: the fake-field region is byte-identical after edits",
        g2[FAKE_LO:FAKE_HI] == us[FAKE_LO:FAKE_HI])

# ---------- the toggle write path ----------
def diff_bytes(a, b):
    return [i for i in range(len(a)) if a[i] != b[i]]

def expect_raise(name, gd, edits):
    try:
        SV.apply_gamedata_edits(gd, edits)
    except ValueError:
        chk(name, True)
    else:
        chk(name, False, "no ValueError raised")

if us:
    off50 = SV.status_word_off(50)
    g, ch = SV.apply_gamedata_edits(us, {"c50_rec": 1})
    chk("US: recruiting Richard writes 0x0302 at his status slot",
        ch == 1 and SV.status_word(g, 50) == SV.RECRUIT_WORD == 0x0302)
    chk("US: the recruit write moves exactly the word's two bytes",
        diff_bytes(us, g) == [off50, off50 + 1])
    chk("US: Richard reads active/recruited after the write",
        SV.recruit_state(g, 50)["state"] == "active"
        and SV.recruit_state(g, 50)["recruited"])
    chk("US: the recruit write leaves the fake-field region alone",
        g[FAKE_LO:FAKE_HI] == us[FAKE_LO:FAKE_HI])
    g2, ch2 = SV.apply_gamedata_edits(g, {"c50_rec": 0})
    chk("US: unrecruiting Richard writes 0x0100, round trip byte-identical",
        ch2 == 1 and SV.status_word(g2, 50) == SV.UNRECRUIT_WORD == 0x0100
        and g2 == us)
    g3, ch3 = SV.apply_gamedata_edits(us, {"c52_rec": 1})
    chk("US: recruiting an already-recruited character is a no-op",
        ch3 == 0 and g3 == us)
    g4, ch4 = SV.apply_gamedata_edits(us, {"c50_rec": 0})
    chk("US: unrecruiting a not-recruited character is a no-op",
        ch4 == 0 and g4 == us)
    off52 = SV.status_word_off(52)
    g5, ch5 = SV.apply_gamedata_edits(us, {"c52_rec": 0})
    chk("US: unrecruiting Luserina writes 0x0100, two-byte footprint",
        ch5 == 1 and SV.status_word(g5, 52) == 0x0100
        and diff_bytes(us, g5) == [off52, off52 + 1]
        and SV.recruit_state(g5, 52)["state"] == "initialized")
    expect_raise("US: unrecruiting a party member is refused (the hero)",
                 us, {"c0_rec": 0})
    expect_raise("US: recruiting a character with no record is refused (Wabon)",
                 us, {"c51_rec": 1})
    expect_raise("US: recruiting a non-recruitable story record is refused",
                 us, {"c115_rec": 1})
if early:
    g, ch = SV.apply_gamedata_edits(early, {"c46_rec": 1})
    chk("PAL early: recruiting Bastan writes 0x0302, two-byte footprint",
        ch == 1 and SV.status_word(g, 46) == 0x0302
        and diff_bytes(early, g) == [SV.status_word_off(46),
                                     SV.status_word_off(46) + 1])
    g, ch = SV.apply_gamedata_edits(early, {"c50_rec": 0})
    chk("PAL early: unrecruiting Richard writes 0x0100",
        ch == 1 and SV.status_word(g, 50) == 0x0100
        and not SV.recruit_state(g, 50)["recruited"])
if late:
    # the exact pair of flips ROSTER_VERIFY proved on the roster screen
    g, ch = SV.apply_gamedata_edits(late, {"c46_rec": 1})
    chk("PAL late: recruiting Bastan writes 0x0302 (the ROSTER_VERIFY flip)",
        ch == 1 and SV.status_word(g, 46) == 0x0302
        and SV.recruit_state(g, 46)["recruited"])
    g, ch = SV.apply_gamedata_edits(late, {"c50_rec": 0})
    chk("PAL late: unrecruiting Richard writes 0x0100 (the ROSTER_VERIFY flip)",
        ch == 1 and SV.status_word(g, 50) == 0x0100
        and not SV.recruit_state(g, 50)["recruited"])
    expect_raise("PAL late: unrecruiting a party member is refused (the hero)",
                 late, {"c0_rec": 0})
# the engine entry point itself, on a synthetic payload
b = bytearray(SV.GAMEDATA_SIZE)
off7 = SV.status_word_off(7)
b[off7:off7 + 2] = (0x0100).to_bytes(2, "little")
chk("engine: set_recruited writes at the reader's slot",
    SV.set_recruited(b, 7, True) and SV.status_word(bytes(b), 7) == 0x0302)
chk("engine: set_recruited unrecruit restores the initialized word",
    SV.set_recruited(b, 7, False) and SV.status_word(bytes(b), 7) == 0x0100)

# ---------- no bulk write paths; the per-character toggle is the only one ----------
src = open(os.path.join(ED, "s5save.py"), encoding="utf-8").read()
chk("engine: no RECRUIT_BASE remains", "RECRUIT_BASE" not in src)
chk("engine: set_recruited is the write path", "def set_recruited" in src)
app = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()
chk("web UI: no bulk recruitment write functions",
    all(s not in app for s in ("toggleRecruit", "writeRecruitOne", "recruitCheckAll", "recruitAll")))
chk("web UI: the per-character toggle is rendered",
    "function recruitToggleHTML" in app)
desk = open(os.path.join(ED, "s5editor.py"), encoding="utf-8").read()
chk("desktop UI: no bulk recruitment write functions",
    all(s not in desk for s in ("writeRecruit", "recruitCheckAll", "recruitAll")))
chk("desktop UI: the per-character toggle is rendered",
    "function recruitToggleHTML" in desk)

# ---------- the roster-row toggle (both frontends) ----------
# The recruitment list carries the same toggle as the character panel,
# so recruiting no longer requires leaving the list. These checks pin
# the wiring: rows render the row toggle, the web write routes through
# the panel's writeCharField staging with the row's own character id,
# the desktop write sends only the rec edit through /api/savewrite,
# and neither list still sends users to the character panel.
chk("web UI: roster rows render the toggle",
    "function recruitRowToggleHTML" in app and "recruitRowToggleHTML(i, c)" in app)
chk("web UI: roster toggle routes through writeCharField with the row id",
    "rosterRecToggle" in app
    and 'writeCharField(i, "rec", el.checked?1:0, el, idx)' in app
    and "idxOverride" in app)
chk("web UI: roster refresh updates the row in place",
    "function refreshRecruitRow" in app and "recstat" in app and "rectog" in app)
chk("web UI: roster no longer sends users to the Characters panel",
    "open them in the Characters panel and use the Recruited toggle" not in app)
chk("desktop UI: roster rows render the toggle",
    "function recruitRowToggleHTML" in desk and "recruitRowToggleHTML(i,c)" in desk)
chk("desktop UI: roster toggle writes only the rec edit via savewrite",
    "rosterRecToggle" in desk and "['c'+idx+'_rec']" in desk)
chk("desktop UI: roster refresh updates the row in place",
    "function refreshRecruitRow" in desk and "recstat" in desk and "rectog" in desk)
chk("desktop UI: roster no longer sends users to the character panel",
    "open them in the character panel and use the Recruited toggle" not in desk)
chk("engine: set_recruited is still the only recruitment write path",
    src.count("def set_recruited") == 1)

print(f"\nrecruitment: {n - bad}/{n} checks passed")
sys.exit(1 if bad else 0)
