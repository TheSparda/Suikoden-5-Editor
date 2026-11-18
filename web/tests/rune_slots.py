#!/usr/bin/env python3
"""Rune slot state display tests (issue #6, display only).

Covers the pure state rule (filled slots display open at any level; empty
slots gate on the community-derived threshold), the three in-game-proven
anchors (Goesch left Lv 20, Lyon head Lv 45, Cathari left Lv 30), the
never and story-gated cases, characters with no community data, and the
fixed-rune flag passthrough from a real save's game-written records
(the lab templates file: its per-record rune_flags are the save bytes at
+0xEF/+0xF0/+0xF1, and its raw records rebuild the payload here).

Run directly (python3 tests/rune_slots.py) or via tests/rune-slots.mjs."""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "Editor")))
import s5save as SV

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

S = SV.rune_slot_state
# pure rule: locked below, open at and above the threshold
chk("locked below threshold", S(18, 20, False) == {"state": "locked", "unlock": 20})
chk("open at threshold", S(20, 20, False)["state"] == "open")
chk("open above threshold", S(40, 30, False)["state"] == "open")
# a filled slot displays regardless of level (Prince head probe: filled at
# Lv 34 against a threshold of 35 still shows the rune and its spells)
chk("filled slot displays below threshold", S(34, 35, True)["state"] == "open")
chk("never stays never at level 99", S(99, "N", False)["state"] == "never")
chk("fixed slot is open while empty", S(1, "F", False)["state"] == "open")
chk("story-gated slot is never while empty", S(45, "F", False, True)["state"] == "never")
chk("no community data is unknown", S(50, None, False)["state"] == "unknown")

# the community table itself: 41 characters, anchors exact
chk("community table covers 41 characters", len(SV.RUNE_SLOT_THRESHOLDS) == 41,
    str(len(SV.RUNE_SLOT_THRESHOLDS)))
chk("anchor Goesch left 20", SV.RUNE_SLOT_THRESHOLDS[48][2] == 20)
chk("anchor Lyon head 45", SV.RUNE_SLOT_THRESHOLDS[8][0] == 45)
chk("anchor Cathari left 30", SV.RUNE_SLOT_THRESHOLDS[19][2] == 30)
chk("Lyon left is the story-gated slot", (8, 2) in SV.RUNE_SLOT_STORY_GATED)

# integration over a synthetic payload: states + flags from real record bytes
gd = bytearray(SV.GAMEDATA_SIZE)
def seed(idx, level, runes, flags):
    r = SV._char_off(idx)
    gd[r + SV.CHAR_LEVEL] = level
    for s in range(3):
        gd[r + SV.CHAR_RUNE + s] = runes[s]
        gd[r + SV.CHAR_RUNE_FLAGS + s] = flags[s]
seed(48, 18, [0, 0, 0], [0, 0, 0])          # Goesch at his save level
seed(8, 40, [0, 5, 0], [0, 0, 0])           # Lyon: head empty, left story slot empty
seed(19, 40, [84, 0, 0], [1, 0, 0])         # Cathari: Godspeed fixed in head
seed(0, 34, [70, 1, 0], [0, 1, 0])          # Prince: head filled below threshold
g = bytes(gd)
st = {s["slot"]: s for s in SV.rune_slot_states(g, 48)}
chk("Goesch left locked until 20 at level 18",
    st["rleft"]["state"] == "locked" and st["rleft"]["unlock"] == 20, str(st["rleft"]))
st = {s["slot"]: s for s in SV.rune_slot_states(g, 8)}
chk("Lyon head locked until 45 at level 40",
    st["rhead"]["state"] == "locked" and st["rhead"]["unlock"] == 45)
chk("Lyon left never while empty (story-gated)", st["rleft"]["state"] == "never")
chk("Lyon right open", st["rright"]["state"] == "open")
st = {s["slot"]: s for s in SV.rune_slot_states(g, 19)}
chk("Cathari head filled + fixed flag shown",
    st["rhead"]["state"] == "open" and st["rhead"]["fixed"] is True and st["rhead"]["filled"] is True)
chk("Cathari left open at level 40 (threshold 30)", st["rleft"]["state"] == "open")
st = {s["slot"]: s for s in SV.rune_slot_states(g, 0)}
chk("Prince head filled displays open at level 34", st["rhead"]["state"] == "open")
chk("Prince right fixed flag passthrough", st["rright"]["fixed"] is True)
rc = SV.read_character(g, 19)
chk("read_character carries runeFlags + slotStates",
    rc["runeFlags"] == [1, 0, 0] and len(rc["slotStates"]) == 3)
st = {s["slot"]: s for s in SV.rune_slot_states(g, 1)}
chk("character outside the sample is unknown, not guessed",
    all(s["state"] == "unknown" and s["community"] is False for s in st.values()))

# real save records: the flags in the payload are the template flags, and
# Goesch's real record (level 18, empty left) computes the proven state
lab = os.environ.get("S5_LAB_DIR") or os.path.abspath(os.path.join(
    HERE, "..", "..", "..", "..", "ps2-re", "games", "suikoden5", "loadout_work"))
import glob as _glob
_tpls = _glob.glob(os.path.join(lab, "templates_*.json"))
if _tpls:
    tpl = json.load(open(_tpls[0]))
    gd2 = bytearray(SV.GAMEDATA_SIZE)
    for k, v in tpl.items():
        idx = int(k)
        gd2[SV._char_off(idx):SV._char_off(idx) + SV.CHAR_STRIDE] = bytes.fromhex(v["raw"])
    # (The template dump's per-record "recruited" labels came from the
    # editor's old fake bitfield and are ignored here, issue #39; rune
    # flags and slot states derive from the records themselves.)
    gd2 = bytes(gd2)
    okflags = all(SV.read_character(gd2, int(k))["runeFlags"] == v["rune_flags"]
                  for k, v in tpl.items())
    chk("fixed-rune flags match the real save for all 120 records", okflags)
    st = {s["slot"]: s for s in SV.rune_slot_states(gd2, 48)}
    chk("real Goesch record: left locked until Lv 20",
        st["rleft"]["state"] == "locked" and st["rleft"]["unlock"] == 20, str(st["rleft"]))
    st = {s["slot"]: s for s in SV.rune_slot_states(gd2, 19)}
    chk("real Cathari record: head fixed, left matches her level",
        st["rhead"]["fixed"] is True
        and st["rleft"]["state"] == ("open" if tpl["19"]["level"] >= 30 else "locked"))
else:
    print("SKIP real-record flag checks: no lab templates file")

print(f"\n{n-bad}/{n} passed")
sys.exit(1 if bad else 0)
