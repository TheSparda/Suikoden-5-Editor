#!/usr/bin/env python3
"""Test harness for the Suikoden V Editor MCP server.

Speaks to the server as a real MCP client (JSON-RPC 2.0 over stdio) and
drives it against fixtures: the repo's truncated US test disc and a COPY of
a real save from the workspace save library. The originals are never opened
by the server; the copies are, and their hashes are asserted unchanged
afterwards (the working-copy guarantee).

Run: python3 mcp/test.py   (exit 0 = all checks passed)
Optional real-data smoke tests (read path on the real ISO, plus a save
round trip on a copy of the real card):
    S5_ISO=/path/to/suikoden5-us.iso S5_CARD=/path/to/card.ps2 python3 mcp/test.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.dirname(os.path.dirname(REPO))          # ~/workspace
TEST_DISC = os.path.join(WS, "suikoden5-debug", "test-disc-us-truncated.iso")
LIB_SAVE = os.path.join(WS, "suikoden5-debug", "saves", "library",
                        "sparda-us-slot00.psu")

# Temp space lives on the home volume, not /tmp: this machine's /tmp is a
# small tmpfs and the fixtures are disc images.
_TMPROOT = os.path.join(WS, "suikoden5-debug", "tmp-tests")
os.makedirs(_TMPROOT, exist_ok=True)
TMP = tempfile.mkdtemp(prefix="s5mcp-", dir=_TMPROOT)
fails = 0
total = 0


def check(name, cond, extra=""):
    global fails, total
    total += 1
    print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" -- {extra}" if extra else ""))
    if not cond:
        fails += 1


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- minimal MCP client ------------------------------------------------------
srv = subprocess.Popen(["python3", os.path.join(HERE, "server.py")],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
_next = 0


def rpc(method, params=None):
    global _next
    _next += 1
    srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": _next,
                                "method": method, "params": params or {}}) + "\n")
    srv.stdin.flush()
    return json.loads(srv.stdout.readline())


def notify(method, params=None):
    srv.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                "params": params or {}}) + "\n")
    srv.stdin.flush()


def call(name, args=None):
    r = rpc("tools/call", {"name": name, "arguments": args or {}})
    res = r.get("result") or {}
    text = (res.get("content") or [{}])[0].get("text", "")
    try:
        data = json.loads(text)
    except Exception:
        data = None
    return bool(res.get("isError")), text, data


# ---- fixtures ------------------------------------------------------------------
iso_fixture = os.path.join(TMP, "test.iso")
shutil.copyfile(TEST_DISC, iso_fixture)
save_fixture = os.path.join(TMP, "save.psu")
shutil.copyfile(LIB_SAVE, save_fixture)
iso_hash0, save_hash0 = sha(iso_fixture), sha(save_fixture)

# ---- handshake and discovery ---------------------------------------------------
print("Handshake and tool listing:")
init = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                          "clientInfo": {"name": "s5-mcp-test", "version": "0"}})
check("initialize returns the server identity",
      init["result"]["serverInfo"]["name"] == "suikoden-5-editor")
notify("notifications/initialized", {})
lst = rpc("tools/list")
names = [t["name"] for t in lst["result"]["tools"]]
EXPECTED = ["iso_open", "iso_list_views", "iso_describe_view",
            "iso_list_records", "iso_get_field", "iso_set_field",
            "iso_read_word", "iso_stage_word", "iso_list_toggles",
            "iso_set_toggle", "iso_list_changes", "iso_discard_changes",
            "iso_export_iso", "iso_export_recipe", "iso_export_xdelta",
            "save_open", "save_get", "save_stage_character",
            "save_stage_misc", "save_list_changes", "save_discard_changes",
            "save_export"]
check("tools/list returns the full surface (22 tools)",
      names == EXPECTED, ",".join(names))

# ---- ISO round trip --------------------------------------------------------------
print("ISO discovery and field edit round trip (truncated test disc):")
err, txt, d = call("iso_open", {"path": iso_fixture})
check("iso_open detects NTSC-U", not err and d["region"] == "ntsc-u", txt[:120])
check("the editable region is the parsed ELF span",
      d["editableRegion"] == {"start": "0xad900", "end": "0xa91980"},
      str(d["editableRegion"]))
err, txt, d = call("iso_list_views")
check("iso_list_views returns all 20 views", len(d["views"]) == 20)
err, txt, d = call("iso_describe_view", {"view": "char"})
check("iso_describe_view char exposes the stats table",
      "HP" in d["tables"]["stats"])
err, txt, d = call("iso_list_records", {"view": "spell", "limit": 3})
check("iso_list_records returns spell records",
      len(d["records"]) == 3 and d["total"] == 106)
err, txt, d = call("iso_get_field", {"view": "char", "index": 0,
                                     "field": "HP", "table": "stats"})
check("iso_get_field reads the Prince's starting HP", not err and d["value"] == 20,
      f"HP={d and d.get('value')}")
err, txt, d = call("iso_set_field", {"view": "char", "index": 0, "field": "HP",
                                     "table": "stats", "value": 99})
check("iso_set_field stages the edit", not err and d["staged"] is True)
err, txt, d = call("iso_get_field", {"view": "char", "index": 0,
                                     "field": "HP", "table": "stats"})
check("the staged value reads back", d["value"] == 99)
err, txt, d = call("iso_list_changes")
check("iso_list_changes decodes the edit as a Characters row",
      any(r["group"] == "Characters" for r in d["rows"]),
      json.dumps(d["summary"]))
err, txt, d = call("iso_set_field", {"view": "char", "index": 0,
                                     "field": "Nope", "table": "stats",
                                     "value": 1})
check("an unknown field is refused in plain words", err and "Nope" in txt)

# ---- toggles ---------------------------------------------------------------------
print("Code patches toggles:")
err, txt, d = call("iso_list_toggles")
check("iso_list_toggles lists the registry (12 toggles, all off)",
      len(d["toggles"]) == 12 and all(t["state"] == "off" for t in d["toggles"]),
      ",".join(t["id"] for t in d["toggles"]))
err, txt, d = call("iso_set_toggle", {"toggle": "sig-skills-normal", "on": True})
check("iso_set_toggle stages a toggle on", not err and d["on"] is True)
err, txt, d = call("iso_list_toggles")
check("the toggle now reads as on",
      next(t for t in d["toggles"] if t["id"] == "sig-skills-normal")["state"] == "on")
err, txt, d = call("iso_set_toggle", {"toggle": "sig-skills-normal", "on": False})
check("the toggle restores back off", not err)
err, txt, d = call("iso_list_toggles")
check("the toggle reads off again",
      next(t for t in d["toggles"] if t["id"] == "sig-skills-normal")["state"] == "off")

# ---- refusal cases -----------------------------------------------------------------
print("Refusals:")
err, txt, d = call("iso_stage_word", {"offset": 0x100, "width": 4, "value": 0})
check("a raw write outside the editable region is refused",
      err and "outside the editable region" in txt, txt[:110])
err, txt, d = call("iso_list_toggles")
dawn = next(t for t in d["toggles"] if t["id"] == "dawn-unlock")
site_off = dawn["sites"][0]["spans"][0]["off"]
err, txt, d = call("iso_stage_word", {"offset": site_off, "width": 4,
                                      "value": 0xDEADBEEF})
check("a raw write at a toggle site stages", not err)
err, txt, d = call("iso_set_toggle", {"toggle": "dawn-unlock", "on": True})
check("the registry's foreign-byte refusal is honoured",
      err and "does not recognise" in txt, txt[:130])
err, txt, d = call("iso_discard_changes")
err, txt, d = call("iso_list_changes")
check("iso_discard_changes empties the change set",
      not err and d["summary"]["totalBytes"] == 0)
err, txt, d = call("iso_list_toggles")
check("after discard the corrupted toggle reads off again",
      next(t for t in d["toggles"] if t["id"] == "dawn-unlock")["state"] == "off")

# ---- exports -----------------------------------------------------------------------
print("ISO exports:")
err, txt, d = call("iso_set_field", {"view": "char", "index": 0, "field": "HP",
                                     "table": "stats", "value": 42})
out_iso = os.path.join(TMP, "out.iso")
err, txt, d = call("iso_export_iso", {"outPath": out_iso})
check("iso_export_iso writes a patched copy", not err and os.path.isfile(out_iso))
recipe_path = os.path.join(TMP, "recipe.json")
err, txt, d = call("iso_export_recipe", {"outPath": recipe_path})
recipe = json.load(open(recipe_path)) if os.path.isfile(recipe_path) else {}
check("iso_export_recipe writes an s5mod recipe",
      not err and recipe.get("format") == "s5mod" and recipe.get("patchCount", 0) > 0)
xd_path = os.path.join(TMP, "out.xdelta")
err, txt, d = call("iso_export_xdelta", {"outPath": xd_path})
magic = open(xd_path, "rb").read(4) if os.path.isfile(xd_path) else b""
check("iso_export_xdelta writes a VCDIFF patch (via web/vcdiff.js)",
      not err and magic == bytes([0xD6, 0xC3, 0xC4, 0x00]), txt[:120])
err, txt, d = call("iso_export_iso", {"outPath": iso_fixture})
check("exporting over the opened source is refused",
      err and "refusing to write over" in txt)
check("the source ISO hash is unchanged after edits and exports",
      sha(iso_fixture) == iso_hash0)
err, txt, d = call("iso_open", {"path": out_iso})
err, txt, d = call("iso_get_field", {"view": "char", "index": 0,
                                     "field": "HP", "table": "stats"})
check("reopening the exported ISO reads the edited value back",
      not err and d["value"] == 42)

# ---- save round trip -----------------------------------------------------------------
print("Save edit round trip (copy of a real .psu through s5save):")
err, txt, d = call("save_open", {"path": save_fixture})
check("save_open decodes the save", not err and d["chosen"]["folder"] == "BASLUS-2129100",
      txt[:140])
err, txt, d = call("save_get", {"section": "summary"})
check("save_get summary shows the real hero and potch",
      d["fields"]["heroName"] == "Sparda" and d["fields"]["potch"] == 52152,
      json.dumps(d["fields"]))
err, txt, d = call("save_get", {"section": "characters"})
hero = next(c for c in d["characters"] if c["idx"] == 0)
check("save_get characters shows the hero at level 39", hero["level"] == 39)
err, txt, d = call("save_get", {"section": "health"})
check("save_get health returns the audit snapshot",
      not err and d["health"]["potch"] == 52152)
err, txt, d = call("save_stage_misc", {"edits": {"newGamePlus": 1}})
check("staging NG+ without the confirm flag is refused, with the conversion warning",
      err and "CONVERTS" in txt, txt[:130])
err, txt, d = call("save_stage_misc", {"edits": {"newGamePlus": 1},
                                       "confirmNgPlusConversion": True})
check("staging NG+ with the confirm flag works", not err)
err, txt, d = call("save_discard_changes")
check("save_discard_changes clears the staged NG+ flag", not err)
err, txt, d = call("save_stage_character", {"index": 0, "edits": {"level": 50}})
check("save_stage_character stages", not err and d["staged"] is True)
err, txt, d = call("save_stage_misc", {"edits": {"potch": 9999}})
check("save_stage_misc stages potch", not err)
err, txt, d = call("save_list_changes")
check("save_list_changes shows the staged edits",
      d["staged"].get("c0_level") == 50 and d["staged"].get("potch") == 9999)
out_save = os.path.join(TMP, "out.psu")
err, txt, d = call("save_export", {"outPath": out_save})
check("save_export writes through s5save", not err and os.path.isfile(out_save),
      txt[:160])
check("the source save hash is unchanged", sha(save_fixture) == save_hash0)
err, txt, d = call("save_open", {"path": out_save})
err, txt, d = call("save_get", {"section": "summary"})
check("the exported potch reads back", d["fields"]["potch"] == 9999)
err, txt, d = call("save_get", {"section": "characters"})
hero = next(c for c in d["characters"] if c["idx"] == 0)
check("the exported hero level reads back", hero["level"] == 50)
# NG+ confirmed staging lands the carry byte on export (the conversion itself
# is the game's, on load; the file just carries the byte).
err, txt, d = call("save_stage_misc", {"edits": {"newGamePlus": 1},
                                       "confirmNgPlusConversion": True})
out_save2 = os.path.join(TMP, "out2.psu")
err, txt, d = call("save_export", {"outPath": out_save2})
err, txt, d = call("save_open", {"path": out_save2})
err, txt, d = call("save_get", {"section": "summary"})
check("a confirmed NG+ staging exports the carry byte",
      not err and d["fields"]["newGamePlus"] == 1)

# ---- real ISO smoke (optional) ---------------------------------------------------------
print("Real ISO smoke test:")
real_iso = os.environ.get("S5_ISO") or os.path.join(
    WS, "suikoden5-debug", "editor", "ISO", "suikoden5-us.iso")
if os.path.isfile(real_iso):
    h0 = sha(real_iso) if os.environ.get("S5_ISO") else None
    err, txt, d = call("iso_open", {"path": real_iso})
    err2, txt2, d2 = call("iso_list_records", {"view": "char", "limit": 2})
    check("the real disc opens and its characters read",
          not err and not err2 and len(d2["records"]) == 2
          and d2["records"][0]["name"] == "Hero/Prince",
          (d2 and str(d2["records"][0]))[:110])
    if h0:
        check("the real ISO hash is unchanged (S5_ISO set)", sha(real_iso) == h0)
else:
    print("  - skipped: no real ISO found")

# ---- real memory card smoke (optional, S5_CARD) -----------------------------------------
print("Real memory card smoke test:")
if os.environ.get("S5_CARD") and os.path.isfile(os.environ["S5_CARD"]):
    card = os.environ["S5_CARD"]
    card_hash = sha(card)
    err, txt, d = call("save_open", {"path": card})
    check("the real card opens and decodes its saves",
          not err and d["saveCount"] >= 1, txt[:120])
    call("save_stage_misc", {"edits": {"potch": 31337}})
    cout = os.path.join(TMP, "card-out.ps2")
    err, txt, d = call("save_export", {"outPath": cout})
    call("save_open", {"path": cout})
    err, txt, d = call("save_get", {"section": "summary"})
    check("a potch edit round-trips through a real card export",
          not err and d["fields"]["potch"] == 31337)
    check("the source card hash is unchanged", sha(card) == card_hash)
else:
    print("  - skipped: set S5_CARD to a memory card image to run it")

srv.kill()
print(f"\n{total - fails}/{total} checks passed"
      + (f", {fails} FAILED" if fails else "") + ".")
sys.exit(1 if fails else 0)
