#!/usr/bin/env python3
"""Dump audit snapshots for the two real fixture saves as JSON on stdout,
for tests/health-core.mjs. Values are null when a fixture is absent (the
mjs side then skips the real-save acceptance checks)."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "Editor")))
import s5save as SV

HOME = os.path.expanduser("~")
out = {"us": None, "pal": None}
us = os.path.join(HOME, "workspace/suikoden5-debug/saves/library/sparda-us-slot00.payload.bin")
if os.path.exists(us):
    out["us"] = SV.audit_snapshot(open(us, "rb").read())
for cand in (os.path.join(HOME, "workspace/user/files/SuikodenV_03NIV53_075_31.psu"),
             os.path.join(HOME, "workspace/suikoden5-debug/fixtures/pal.psu")):
    if os.path.exists(cand):
        gd = SV.read_gamedata_payload(cand)
        if gd:
            out["pal"] = SV.audit_snapshot(gd)
        break
print(json.dumps(out))
