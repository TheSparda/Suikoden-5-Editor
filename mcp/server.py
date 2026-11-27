#!/usr/bin/env python3
"""Suikoden V Editor MCP server (stdio, Python standard library only).

Design: this server contains no editing logic of its own.
  - ISO side: every read and write goes through Editor/s5patch.py,
    Editor/s5fields.py and Editor/s5toggles.py, the exact modules the web
    editor runs under Pyodide, so offsets, validation and refusals cannot
    drift from the shipped editor. The view list is parsed out of
    web/iso.js (never re-typed), the Changes audit runs Editor/s5changes.py,
    and the .xdelta export runs web/vcdiff.js through mcp/xdelta_bridge.mjs.
  - Save side: every operation calls Editor/s5save.py directly (the host is
    Python, so no bridge process is needed), the same module the web save
    editor runs.

Working-copy rule: opening an ISO or save copies it into this server's
.work/ directory. Edits land on the working copy (the staging area) or on
staged save state; outputs are written only to caller-chosen paths. The
path a file was opened from is never written, and every export refuses
that path as its target.
"""
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "Editor"))

import s5fields as F      # noqa: E402
import s5patch as P       # noqa: E402
import s5save as SV       # noqa: E402
import s5changes as CH    # noqa: E402
import s5toggles as TG    # noqa: E402

SV.BACKUPS = False        # same setting the web editor's glue uses

WORK = os.path.join(HERE, ".work")
os.makedirs(WORK, exist_ok=True)


class ToolError(Exception):
    """A plain-language refusal or failure for the MCP client."""


def fail(msg):
    raise ToolError(msg)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def jsonable(x):
    if isinstance(x, bytes):
        return x.hex()
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    return x


# ---------------------------------------------------------------------------
# The editor's view list, parsed out of web/iso.js (never re-typed here).
# ---------------------------------------------------------------------------
def _load_views():
    src = open(os.path.join(REPO, "web", "iso.js"), encoding="utf-8").read()
    m = re.search(r"const ISO_VIEWS = \[(.*?)\];", src, re.S)
    if not m:
        raise RuntimeError("could not find ISO_VIEWS in web/iso.js")
    return [{"id": vid, "label": label}
            for vid, label in re.findall(r'\{ id: "(\w+)",\s*label: "([^"]+)" \}', m.group(1))]


VIEWS = _load_views()


def _elf_span(path):
    """(start, end) ISO file offsets of the boot ELF's PT_LOAD segments.

    Every named write the editor makes (tables, code patches, model
    pointers, character names) lands inside these segments; the overlay
    and DATA.PAK write paths go through container re-encoding, never raw
    bytes, so raw MCP writes are constrained to this span. Falls back to
    the span covering every mapped table if the header cannot be parsed.
    """
    with open(path, "rb") as f:
        head = f.read(0x200000)
    for m in re.finditer(rb"\x7fELF", head):
        base = m.start()
        try:
            phoff = struct.unpack_from("<I", head, base + 28)[0]
            phentsize, phnum = struct.unpack_from("<HH", head, base + 42)
            if phentsize != 32 or not (0 < phnum <= 32) or phoff > 0x10000:
                continue
            lo, hi = None, 0
            for i in range(phnum):
                p_type, p_off = struct.unpack_from(
                    "<II", head, base + phoff + i * phentsize)
                p_filesz = struct.unpack_from(
                    "<I", head, base + phoff + i * phentsize + 16)[0]
                if p_type == 1 and p_filesz:
                    lo = p_off if lo is None else min(lo, p_off)
                    hi = max(hi, p_off + p_filesz)
            if lo is not None:
                return (base + lo, base + hi)
        except Exception:
            continue
    return (0, 0x700000)


# ---------------------------------------------------------------------------
# Session state: one ISO and one save at a time, as in the web editor.
# ---------------------------------------------------------------------------
iso = None   # {name, sourcePath, sourceHash, size, region, work, pristine, span, toggleOriginals}
save = None  # {name, sourcePath, sourceHash, format, individual, folder, saves, index, staged}


def _iso():
    if not iso:
        fail("no ISO is open; call iso_open first")
    return iso


def _save():
    if not save:
        fail("no save is open; call save_open first")
    return save


def _guard_output(p):
    for st in (iso, save):
        if st and os.path.abspath(p) == os.path.abspath(st["sourcePath"]):
            fail("refusing to write over the file this session was opened from. "
                 "Choose a different output path.")
    if iso and os.path.abspath(p) in (os.path.abspath(iso["work"]),
                                      os.path.abspath(iso["pristine"])):
        fail("refusing to write over the server's own working copy. "
             "Choose a different output path.")


def _with_iso(writable, fn):
    st = _iso()
    with P.Iso(st["work"], writable=writable) as g:
        return fn(g)


# ---------------------------------------------------------------------------
# Record views: thin adapters over the engine's own read/write functions.
# Field lists are the engine's own field dicts ({label, off, width, kind,
# value}); nothing is re-typed here.
# ---------------------------------------------------------------------------
def _fields_map(field_dicts):
    return {f["label"]: f["value"] for f in field_dicts}


def _char_label_tables():
    """label -> [tables] from the s5fields TABLES registry itself."""
    out = {}
    for tname, (_base, _stride, fields) in F.TABLES.items():
        for (label, _off, _w, _kind) in fields:
            out.setdefault(label, []).append(tname)
    return out


class CharView:
    id = "char"

    def describe(self):
        return {"tables": {t: [f[0] for f in fields]
                           for t, (_b, _s, fields) in F.TABLES.items()},
                "note": "Pass the table argument when a field label exists in "
                        "more than one table (for example Attack)."}

    def records(self, offset, limit, opts=None):
        chars = F.load_characters()
        return len(chars), [{"index": c["id"], "id": c["id"], "name": c["name"]}
                             for c in chars[offset:offset + limit]]

    def _table_for(self, field, table):
        tables = _char_label_tables().get(field)
        if not tables:
            fail(f"the Characters view has no field '{field}'")
        if table:
            if table not in tables:
                fail(f"field '{field}' is not in table '{table}' "
                     f"(it is in: {', '.join(tables)})")
            return table
        if len(tables) > 1:
            fail(f"field '{field}' exists in more than one table "
                 f"({', '.join(tables)}); pass the table argument")
        return tables[0]

    def get(self, index, field, opts):
        table = self._table_for(field, opts.get("table"))
        tbls = _with_iso(False, lambda g: P.read_character(g, index))
        if table not in tbls:
            fail(f"table '{table}' is not readable for character {index} "
                 "on this disc (some tables are NTSC-U only)")
        return {"table": table, "value": _fields_map(tbls[table])[field]}

    def set(self, index, field, value, opts):
        table = self._table_for(field, opts.get("table"))
        _with_iso(True, lambda g: P.write_field(g, table, index, field, int(value)))
        return {"table": table}


class SimpleView:
    """A view whose records are field-dict lists from one engine reader."""

    def __init__(self, vid, count, reader, writer, name_fn=None):
        self.id, self._count, self._reader, self._writer = vid, count, reader, writer
        self._name_fn = name_fn

    def describe(self):
        rows = _with_iso(False, lambda g: self._reader(g, 0))
        return {"fields": [f["label"] for f in rows]}

    def records(self, offset, limit, opts=None):
        out = []
        for i in range(offset, min(self._count, offset + limit)):
            rec = {"index": i, "fields": _fields_map(_with_iso(False, lambda g, i=i: self._reader(g, i)))}
            if self._name_fn:
                rec["name"] = self._name_fn(i)
            out.append(rec)
        return self._count, out

    def get(self, index, field, opts):
        fields = _fields_map(_with_iso(False, lambda g: self._reader(g, index)))
        if field not in fields:
            fail(f"view '{self.id}' has no field '{field}'. "
                 f"Fields: {', '.join(fields)}")
        return {"value": fields[field]}

    def set(self, index, field, value, opts):
        _with_iso(True, lambda g: self._writer(g, index, field, int(value)))
        return {}


class PricesView:
    def __init__(self, vid, reader, writer, count):
        self.id, self._reader, self._writer, self._count = vid, reader, writer, count

    def describe(self):
        return {"fields": ["buy", "sell"]}

    def records(self, offset, limit, opts=None):
        rows = _with_iso(False, self._reader)
        return len(rows), rows[offset:offset + limit]

    def get(self, index, field, opts):
        if field not in ("buy", "sell"):
            fail(f"view '{self.id}' fields are buy and sell, not '{field}'")
        rows = _with_iso(False, self._reader)
        if not (0 <= index < len(rows)):
            fail(f"index {index} out of range for '{self.id}'")
        return {"value": rows[index][field], "name": rows[index]["name"]}

    def set(self, index, field, value, opts):
        if field not in ("buy", "sell"):
            fail(f"view '{self.id}' fields are buy and sell, not '{field}'")
        _with_iso(True, lambda g: self._writer(g, index, field, int(value)))
        return {}


class EnemyView:
    id = "enemy"

    def _names(self):
        try:
            return F.res_json("s5_enemy_names.json")
        except Exception:
            return {}

    def describe(self):
        return {"fields": [f[0] for f in F.ENEMY_FIELDS]}

    def records(self, offset, limit, opts=None):
        rows = _with_iso(False, lambda g: P.read_enemies(g, self._names()))
        return len(rows), [{"index": r["id"], "id": r["id"], "name": r["name"],
                            "fields": _fields_map(_with_iso(False, lambda g, i=r["id"]: P.read_enemy(g, i)))}
                           for r in rows[offset:offset + limit]]

    def get(self, index, field, opts):
        fields = _fields_map(_with_iso(False, lambda g: P.read_enemy(g, index)))
        if field not in fields:
            fail(f"the Enemies view has no field '{field}'")
        return {"value": fields[field]}

    def set(self, index, field, value, opts):
        _with_iso(True, lambda g: P.write_enemy_field(g, index, field, int(value)))
        return {}


class GearView:
    id = "gear"
    SLOTS = ("head", "body", "arm", "foot", "accessory")

    def _slot(self, opts):
        slot = opts.get("slot") or "head"
        if slot not in self.SLOTS:
            fail(f"unknown gear slot '{slot}' (head, body, arm, foot, accessory)")
        return slot

    def describe(self):
        return {"slots": list(self.SLOTS),
                "fields": [f[0] for f in F.ARMOR_FIELDS],
                "note": "Pass the slot argument to choose the armor table."}

    def records(self, offset, limit, opts=None):
        slot = self._slot(opts or {})
        rows = _with_iso(False, lambda g: P.list_armor(g, slot))
        return len(rows), [{"index": r["id"], "name": r["name"], "slot": slot,
                            "fields": {"DEF": r["def"], "Buy price": r["buy"]}}
                           for r in rows[offset:offset + limit]]

    def get(self, index, field, opts):
        slot = self._slot(opts)
        item = _with_iso(False, lambda g: P.read_armor_item(g, slot, index))
        fields = _fields_map(item["fields"])
        if field not in fields:
            fail(f"the Gear view has no field '{field}'")
        return {"value": fields[field], "slot": slot, "name": item.get("name")}

    def set(self, index, field, value, opts):
        slot = self._slot(opts)
        _with_iso(True, lambda g: P.write_armor_field(g, slot, index, field, int(value)))
        return {"slot": slot}


class MpView:
    id = "mp"

    def describe(self):
        return {"fields": list(F.MP_FIELD_LABELS),
                "note": "index is the magic level group (0..3)."}

    def records(self, offset, limit, opts=None):
        rows = _with_iso(False, P.read_mp)
        return len(rows), [{"index": r["group"], "name": r["label"],
                            "fields": dict(zip(F.MP_FIELD_LABELS, r["values"]))}
                           for r in rows[offset:offset + limit]]

    def _idx(self, field):
        if field not in F.MP_FIELD_LABELS:
            fail(f"the MP growth view has no field '{field}'. "
                 f"Fields: {', '.join(F.MP_FIELD_LABELS)}")
        return F.MP_FIELD_LABELS.index(field)

    def get(self, index, field, opts):
        idx = self._idx(field)
        rows = _with_iso(False, P.read_mp)
        if not (0 <= index < len(rows)):
            fail(f"MP group {index} out of range (0..{len(rows) - 1})")
        return {"value": rows[index]["values"][idx]}

    def set(self, index, field, value, opts):
        idx = self._idx(field)
        _with_iso(True, lambda g: P.write_mp(g, index, idx, int(value)))
        return {}


class SkillfxView:
    id = "skillfx"

    def describe(self):
        return {"fields": list(F.SKILLFX_RANKS),
                "note": "Each field is the effect magnitude at that rank."}

    def records(self, offset, limit, opts=None):
        rows = _with_iso(False, P.read_skillfx)
        return len(rows), [{"index": r["id"], "name": r["name"],
                            "fields": dict(zip(F.SKILLFX_RANKS, r["values"]))}
                           for r in rows[offset:offset + limit]]

    def _rank(self, field):
        if field not in F.SKILLFX_RANKS:
            fail(f"the Skill effects view has no field '{field}'. "
                 f"Fields: {', '.join(F.SKILLFX_RANKS)}")
        return F.SKILLFX_RANKS.index(field)

    def get(self, index, field, opts):
        rank = self._rank(field)
        rows = _with_iso(False, P.read_skillfx)
        row = next((r for r in rows if r["id"] == index), None)
        if row is None:
            fail(f"no skill effect record {index}")
        return {"value": row["values"][rank], "name": row["name"]}

    def set(self, index, field, value, opts):
        rank = self._rank(field)
        _with_iso(True, lambda g: P.write_skillfx(g, index, rank, int(value)))
        return {}


class UniteView:
    id = "unite"

    def _names(self):
        try:
            chars = {c["id"]: c["name"] for c in F.load_characters()}
        except Exception:
            chars = {}
        chars.update(F.UNITE_EXTRA_CHARS)
        return chars

    def describe(self):
        return {"fields": ["Participant 1", "Participant 2", "Participant 3",
                           "Participant 4", "Participant 5", "Participant 6"],
                "note": "Only participants are editable; the participant count "
                        "byte is not, because the records are packed."}

    def records(self, offset, limit, opts=None):
        rows = _with_iso(False, P.read_unites)
        names = self._names()
        return len(rows), [{"index": r["id"], "name": r.get("name"),
                            "fields": {f"Participant {i + 1}": v
                                       for i, v in enumerate(r["ids"])},
                            "participants": [names.get(v, str(v)) for v in r["ids"]]}
                           for r in rows[offset:offset + limit]]

    def _slot(self, field):
        m = re.fullmatch(r"Participant ([1-6])", field or "")
        if not m:
            fail(f"the Unites view fields are 'Participant 1' to 'Participant 6', "
                 f"not '{field}'")
        return int(m.group(1)) - 1

    def get(self, index, field, opts):
        slot = self._slot(field)
        rows = _with_iso(False, P.read_unites)
        row = next((r for r in rows if r["id"] == index), None)
        if row is None or slot >= len(row["ids"]):
            fail(f"unite {index} has no participant slot {slot + 1}")
        return {"value": row["ids"][slot]}

    def set(self, index, field, value, opts):
        slot = self._slot(field)
        _with_iso(True, lambda g: P.write_unite_member(g, index, slot, int(value)))
        return {}


class NameView:
    id = "name"

    def describe(self):
        return {"fields": ["name"],
                "note": "Character name table entries, at most 7 characters, "
                        "as the engine enforces."}

    def records(self, offset, limit, opts=None):
        rows = _with_iso(False, lambda g: P.read_names(g))
        return len(rows), rows[offset:offset + limit]

    def get(self, index, field, opts):
        if field != "name":
            fail("the Char names view has one field: name")
        rows = _with_iso(False, lambda g: P.read_names(g))
        row = next((r for r in rows if r["index"] == index), None)
        if row is None:
            fail(f"no name record at index {index}")
        return {"value": row["name"]}

    def set(self, index, field, value, opts):
        if field != "name":
            fail("the Char names view has one field: name")
        _with_iso(True, lambda g: P.set_name(g, index, str(value)))
        return {}


class ReadOnlyView:
    """Discovery-only views: records can be listed, fields cannot be staged
    through the generic field tools (see the README's Known gaps)."""

    def __init__(self, vid, lister):
        self.id, self._lister = vid, lister

    def describe(self):
        fail(f"view '{self.id}' is discovery-only through MCP: its records can "
             "be listed with iso_list_records, but it has no generic field "
             "schema. See the Known gaps section of mcp/README.md.")

    def records(self, offset, limit, opts=None):
        rows = _with_iso(False, self._lister)
        return len(rows), rows[offset:offset + limit]

    def get(self, index, field, opts):
        fail(f"view '{self.id}' is discovery-only through MCP; use "
             "iso_list_records to see its records, or iso_read_word for raw bytes")

    def set(self, index, field, value, opts):
        fail(f"view '{self.id}' is discovery-only through MCP; its editing "
             "surface does not reduce to the generic field tools. See the "
             "Known gaps section of mcp/README.md.")


def _spell_name(i):
    try:
        names = F.res_json("s5_spell_names.json")
        return names[i] if i < len(names) else None
    except Exception:
        return None


RECORD_VIEWS = {
    "char": CharView(),
    "spell": SimpleView("spell", F.SPELL_COUNT, P.read_spell, P.write_spell_field,
                        name_fn=_spell_name),
    "rune": SimpleView("rune", F.RUNE_GRANT_COUNT, P.read_rune, P.write_rune_field,
                       name_fn=lambda i: F.RUNE_GRANT_NAMES[i] if i < len(F.RUNE_GRANT_NAMES) else None),
    "gear": GearView(),
    "runeprice": PricesView("runeprice", P.read_rune_prices, P.write_rune_price,
                            F.RUNEPRICE_COUNT),
    "healprice": PricesView("healprice", P.read_heal_prices, P.write_heal_price,
                            F.HEALPRICE_COUNT),
    "enemy": EnemyView(),
    "mp": MpView(),
    "skillfx": SkillfxView(),
    "unite": UniteView(),
    "name": NameView(),
    "sets": ReadOnlyView("sets", lambda g: P.read_sets(g)["sets"]),
    "model": ReadOnlyView("model", P.read_models),
}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
tools = []


def tool(name, description, properties, required, fn):
    tools.append({"name": name, "description": description,
                  "inputSchema": {"type": "object", "properties": properties,
                                  "required": required or []},
                  "fn": fn})


def S(type_, desc, extra=None):
    d = {"type": type_, "description": desc}
    if extra:
        d.update(extra)
    return d


# ---- ISO tools -------------------------------------------------------------
def t_iso_open(a):
    p = a["path"]
    if not os.path.isfile(p):
        fail(f"no file at {p}")
    with P.Iso(p) as g:
        region = P.region_of(g)
    if not region:
        fail("not a recognized Suikoden V ISO (expected NTSC-U SLUS-212.91 "
             "or PAL SLES-54087)")
    global iso
    work = os.path.join(WORK, "iso.work")
    pristine = os.path.join(WORK, "iso.pristine")
    shutil.copyfile(p, pristine)
    shutil.copyfile(p, work)
    P.clear_mod(work)
    F.set_region(region)
    iso = {"name": os.path.basename(p), "sourcePath": p,
           "sourceHash": sha256_file(p), "size": os.path.getsize(p),
           "region": region, "work": work, "pristine": pristine,
           "span": _elf_span(work), "toggleOriginals": {}}
    return {"opened": iso["name"], "size": iso["size"], "region": region,
            "regionName": F.REGION_NAMES.get(region, region),
            "sourceSha256": iso["sourceHash"],
            "editableRegion": {"start": hex(iso["span"][0]),
                               "end": hex(iso["span"][1])},
            "note": "Edits land on a working copy in mcp/.work/. The source "
                    "file will not be written."}


tool("iso_open",
     "Open a Suikoden V ISO (NTSC-U or PAL, as the engine detects). The file "
     "is copied to a working location; the source file is never written. "
     "Returns disc identity, region and the source hash.",
     {"path": S("string", "Path to the ISO on this machine")}, ["path"],
     t_iso_open)


def t_iso_list_views(a):
    return {"views": [{"id": v["id"], "label": v["label"],
                       "recordEditing": v["id"] in RECORD_VIEWS and
                       not isinstance(RECORD_VIEWS[v["id"]], ReadOnlyView),
                       "recordDiscovery": v["id"] in RECORD_VIEWS}
                      for v in VIEWS],
            "recordViews": [v for v in RECORD_VIEWS
                            if not isinstance(RECORD_VIEWS[v], ReadOnlyView)],
            "discoveryOnlyViews": [v for v in RECORD_VIEWS
                                   if isinstance(RECORD_VIEWS[v], ReadOnlyView)]}


tool("iso_list_views",
     "List the editor's ISO views (parsed from web/iso.js) and mark which "
     "ones this server exposes for record-level editing.",
     {}, [], t_iso_list_views)


def _view(view):
    v = RECORD_VIEWS.get(view)
    if not v:
        fail(f"view '{view}' has no record table exposed through MCP. Record "
             f"views: {', '.join(RECORD_VIEWS)}. Any offset in the editable "
             "region can still be read with iso_read_word and staged with "
             "iso_stage_word.")
    return v


def t_iso_describe_view(a):
    v = _view(a["view"])
    out = {"view": v.id}
    out.update(v.describe())
    return out


tool("iso_describe_view",
     "Describe a record view: its editable fields, as defined by the "
     "editor's own registries (Editor/s5fields.py). The Characters view "
     "takes an optional table argument on field access; the Gear view an "
     "optional slot argument.",
     {"view": S("string", "View id from iso_list_views")}, ["view"],
     t_iso_describe_view)


def t_iso_list_records(a):
    _iso()
    v = _view(a["view"])
    total, rows = v.records(int(a.get("offset") or 0),
                            int(a.get("limit") or 50), a)
    return {"view": v.id, "total": total, "records": jsonable(rows)}


tool("iso_list_records",
     "List records of a record view (index, name where the disc stores one, "
     "and field values).",
     {"view": S("string", "Record view id"),
      "offset": S("integer", "First index (default 0)"),
      "limit": S("integer", "Max records (default 50)"),
      "slot": S("string", "Armor slot (only for the gear view)")}, ["view"],
     t_iso_list_records)


def t_iso_get_field(a):
    _iso()
    v = _view(a["view"])
    out = {"view": v.id, "index": a["index"], "field": a["field"]}
    out.update(v.get(int(a["index"]), a["field"], a))
    return jsonable(out)


tool("iso_get_field",
     "Read one field of one record in a record view (the working-copy value, "
     "including any edits already made on it). Extra arguments: table (for "
     "the Characters view), slot (for the Gear view).",
     {"view": S("string", "Record view id"),
      "index": S("integer", "Record index"),
      "field": S("string", "Field name from iso_describe_view"),
      "table": S("string", "Characters table name (only for the char view)"),
      "slot": S("string", "Armor slot (only for the gear view)")},
     ["view", "index", "field"], t_iso_get_field)


def t_iso_set_field(a):
    _iso()
    v = _view(a["view"])
    out = {"staged": True, "view": v.id, "index": a["index"], "field": a["field"]}
    out.update(v.set(int(a["index"]), a["field"], a["value"], a))
    out["changes"] = _changes_summary()
    return jsonable(out)


tool("iso_set_field",
     "Stage one field edit on a record: the edit lands on the working copy "
     "and is listed by iso_list_changes. Nothing reaches a caller-visible "
     "file until iso_export_iso, iso_export_recipe or iso_export_xdelta is "
     "called. Extra arguments: table (char view), slot (gear view). The "
     "Char names view takes a string value; every other field takes a number.",
     {"view": S("string", "Record view id"),
      "index": S("integer", "Record index"),
      "field": S("string", "Field name"),
      "value": S("integer", "New value (a string for the Char names view)",
                 {"type": ["integer", "string"]}),
      "table": S("string", "Characters table name (only for the char view)"),
      "slot": S("string", "Armor slot (only for the gear view)")},
     ["view", "index", "field", "value"], t_iso_set_field)


def t_iso_read_word(a):
    st = _iso()
    off, w = int(a["offset"]), int(a.get("width") or 4)
    if w not in (1, 2, 4):
        fail("width must be 1, 2 or 4")
    if off < 0 or off + w > st["size"]:
        fail(f"offset 0x{off:X} is outside the disc image")
    cur = _with_iso(False, lambda g: g.ru(off, w))
    with P.Iso(st["pristine"]) as g:
        orig = g.ru(off, w)
    return {"offset": hex(off), "width": w, "value": cur, "original": orig,
            "staged": cur != orig}


tool("iso_read_word",
     "Read a raw 1, 2 or 4 byte value at an absolute ISO offset (the "
     "working-copy value and the opened disc's original value).",
     {"offset": S("integer", "Absolute ISO offset"),
      "width": S("integer", "1, 2 or 4 (default 4)")}, ["offset"],
     t_iso_read_word)


def t_iso_stage_word(a):
    st = _iso()
    off, w, value = int(a["offset"]), int(a["width"]), int(a["value"])
    if w not in (1, 2, 4):
        fail("width must be 1, 2 or 4")
    lo, hi = st["span"]
    if off < lo or off + w > hi:
        fail(f"offset 0x{off:X} is outside the editable region "
             f"(0x{lo:X}..0x{hi:X}, the boot ELF's loaded segments). Overlay "
             "and DATA.PAK writes go through the editor's container "
             "re-encoding and are not offered as raw writes.")
    if not (0 <= value < (1 << (8 * w))):
        fail(f"value {value} does not fit in {w} byte(s)")
    _with_iso(True, lambda g: g.wu(off, w, value))
    return {"staged": True, "offset": hex(off), "changes": _changes_summary()}


tool("iso_stage_word",
     "Stage a raw word edit at an absolute ISO offset inside the editable "
     "region (the boot ELF's loaded segments). The editor's container write "
     "paths (overlays, DATA.PAK) are not reachable as raw writes.",
     {"offset": S("integer", "Absolute ISO offset"),
      "width": S("integer", "1, 2 or 4"),
      "value": S("integer", "New value")}, ["offset", "width", "value"],
     t_iso_stage_word)


def t_iso_list_toggles(a):
    _iso()
    toggles = _with_iso(False, TG.list_toggles)
    return {"toggles": jsonable(toggles)}


tool("iso_list_toggles",
     "List the Code patches registry toggles (Editor/s5toggles.py, read at "
     "runtime) with label, group, effect honesty label and current state on "
     "the working copy: off, on, mixed, foreign or not-found.",
     {}, [], t_iso_list_toggles)


def t_iso_set_toggle(a):
    st = _iso()
    tid, on = a["toggle"], bool(a["on"])
    try:
        if on:
            def apply_on(g):
                r = TG.set_toggle(g, tid, True)
                if r.get("originals"):
                    st["toggleOriginals"].setdefault(tid, {}).update(r["originals"])
                return r
            r = _with_iso(True, apply_on)
        else:
            originals = st["toggleOriginals"].get(tid)
            r = _with_iso(True, lambda g: TG.set_toggle(g, tid, False,
                                                        originals=originals))
    except KeyError as e:
        fail(str(e).strip("'\"") or f"unknown toggle '{tid}'")
    except (ValueError, AssertionError) as e:
        fail(str(e))
    return {"staged": True, "toggle": tid, "on": on,
            "changedSpans": r.get("changed"), "changes": _changes_summary()}


tool("iso_set_toggle",
     "Stage a Code patches registry toggle on or off. The registry refuses "
     "a toggle whose patch site holds bytes it does not recognise, and that "
     "refusal applies here too. Restoring a gate toggle uses the original "
     "bytes this session displaced when it was applied, exactly as the web "
     "editor does.",
     {"toggle": S("string", "Toggle id from iso_list_toggles"),
      "on": S("boolean", "true to apply, false to restore stock")},
     ["toggle", "on"], t_iso_set_toggle)


def _changes_summary():
    rep = _diff_report()
    return rep["summary"]


def _diff_report():
    st = _iso()
    n = min(st["size"], st["span"][1])
    with open(st["work"], "rb") as f:
        cur = f.read(n)
    with open(st["pristine"], "rb") as f:
        base = f.read(n)
    try:
        return CH.diff_report(cur, base)
    except ValueError as e:
        fail(str(e))


def t_iso_list_changes(a):
    return jsonable(_diff_report())


tool("iso_list_changes",
     "List every change on the working copy against the opened disc, decoded "
     "into labelled rows by the editor's own Changes engine "
     "(Editor/s5changes.py): group, label, old and new values, plus a summary.",
     {}, [], t_iso_list_changes)


def t_iso_discard_changes(a):
    st = _iso()
    shutil.copyfile(st["pristine"], st["work"])
    P.clear_mod(st["work"])
    st["toggleOriginals"] = {}
    return {"discarded": True}


tool("iso_discard_changes",
     "Discard all edits on the working copy and restore it to the opened "
     "disc state.", {}, [], t_iso_discard_changes)


def t_iso_export_iso(a):
    st = _iso()
    out = a["outPath"]
    _guard_output(out)
    shutil.copyfile(st["work"], out)
    return {"written": out, "sha256": sha256_file(out),
            "changes": _changes_summary()}


tool("iso_export_iso",
     "Write a full patched ISO copy (the working copy) to a caller-chosen "
     "path. The opened source is never the target.",
     {"outPath": S("string", "Where to write the patched ISO")}, ["outPath"],
     t_iso_export_iso)


def t_iso_export_recipe(a):
    st = _iso()
    out = a["outPath"]
    _guard_output(out)
    try:
        recipe = P.export_mod(st["work"])
    except (ValueError, FileNotFoundError):
        fail("no edits have been recorded on the working copy yet, so there "
             "is no recipe to export")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(recipe, f, indent=2)
    return {"written": out, "format": recipe.get("format"),
            "patchCount": recipe.get("patchCount")}


tool("iso_export_recipe",
     "Export the edits made on the working copy as an .s5mod recipe JSON "
     "(the editor's own recipe format, from Editor/s5patch.py): byte runs "
     "with old and new bytes, serial-checked on apply.",
     {"outPath": S("string", "Where to write the recipe JSON")}, ["outPath"],
     t_iso_export_recipe)


def _journal_runs():
    """Coalesced (offset, new-bytes) runs from the working copy's own
    .s5mod journal; falls back to a chunk diff if the journal is missing."""
    st = _iso()
    side = st["work"] + ".s5mod.json"
    runs = []
    if os.path.exists(side):
        with open(side) as f:
            bm = json.load(f).get("bytes", {})
        items = sorted((int(k), v[1]) for k, v in bm.items())
        for off, new in items:
            if runs and off == runs[-1][0] + len(runs[-1][1]):
                runs[-1][1] += bytes([new])
            else:
                runs.append([off, bytes([new])])
        return [(off, data) for off, data in runs]
    n = min(st["size"], st["span"][1])
    with open(st["work"], "rb") as f:
        cur = f.read(n)
    with open(st["pristine"], "rb") as f:
        base = f.read(n)
    i = 0
    while i < n:
        if cur[i] != base[i]:
            j = i
            while j < n and cur[j] != base[j]:
                j += 1
            runs.append((i, cur[i:j]))
            i = j
        else:
            i += 1
    return runs


def t_iso_export_xdelta(a):
    st = _iso()
    out = a["outPath"]
    _guard_output(out)
    import base64
    runs = _journal_runs()
    req = {"sourceSize": st["size"],
           "edits": [{"off": off, "dataB64": base64.b64encode(data).decode()}
                     for off, data in runs],
           "outPath": os.path.abspath(out)}
    bridge = os.path.join(HERE, "xdelta_bridge.mjs")
    try:
        proc = subprocess.run(["node", bridge], input=json.dumps(req),
                              capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        fail("iso_export_xdelta needs Node.js on PATH (it runs the editor's "
             "own web/vcdiff.js through a small bridge); Node was not found")
    try:
        res = json.loads(proc.stdout.strip().split("\n")[-1])
    except Exception:
        fail(f"the xdelta bridge returned unreadable output: "
             f"{(proc.stderr or proc.stdout)[:300]}")
    if not res.get("ok"):
        fail(f"xdelta export failed: {res.get('error', 'unknown error')}")
    return {"written": out, "patchBytes": res["patchBytes"], "edits": len(runs)}


tool("iso_export_xdelta",
     "Export the working copy's edits as a standard VCDIFF (.xdelta) patch "
     "against the opened disc, built by the repo's own web/vcdiff.js through "
     "a small Node bridge. Requires Node.js on PATH.",
     {"outPath": S("string", "Where to write the .xdelta patch")}, ["outPath"],
     t_iso_export_xdelta)


# ---- Save tools --------------------------------------------------------------
def _individual(path):
    with open(path, "rb") as f:
        head = f.read(20)
    return head[:4] == b"CFU\x00" or head[:17] == b"\x0d\x00\x00\x00SharkPortSave"


def _save_payload():
    st = _save()
    if st["individual"]:
        s = SV.read_individual_save(st["work"])
        return None, s
    gd = SV.read_gamedata_payload(st["work"], st["folder"] or None)
    if not gd:
        fail("could not read the gamedata payload of the opened save")
    return gd, None


def t_save_open(a):
    p = a["path"]
    index = int(a.get("index") or 0)
    if not os.path.isfile(p):
        fail(f"no file at {p}")
    work = os.path.join(WORK, "save.work")
    shutil.copyfile(p, work)
    global save
    if _individual(work):
        s = SV.read_individual_save(work)
        if not s:
            fail("no Suikoden V save found in that file (supported formats: "
                 "memory-card images .ps2/.mc2/.mcd/.bin, .psu, .cbs, "
                 ".xps/.sps)")
        save = {"name": os.path.basename(p), "sourcePath": p,
                "sourceHash": sha256_file(p), "format": "individual",
                "individual": True, "folder": None, "saves": [s],
                "index": 0, "staged": {}, "work": work}
        chosen = s
    else:
        saves = SV.read_all_saves(work)
        if not saves:
            fail("no Suikoden V save found in that file (supported formats: "
                 "memory-card images .ps2/.mc2/.mcd/.bin, .psu, .cbs, "
                 ".xps/.sps)")
        if not (0 <= index < len(saves)):
            fail(f"save index {index} out of range; the file holds "
                 f"{len(saves)} save(s)")
        chosen = saves[index]
        save = {"name": os.path.basename(p), "sourcePath": p,
                "sourceHash": sha256_file(p),
                "format": SV._sniff_format(work), "individual": False,
                "folder": chosen.get("folder"), "saves": saves,
                "index": index, "staged": {}, "work": work}
    flds = chosen.get("fields") or {}
    return {"opened": save["name"], "format": save["format"],
            "saveCount": len(save["saves"]),
            "chosen": {"index": save["index"], "folder": save["folder"],
                       "region": chosen.get("region"),
                       "heroName": flds.get("heroName"),
                       "castleName": flds.get("castleName"),
                       "level": flds.get("level"),
                       "potch": flds.get("potch")},
            "note": "The source was copied to a working location and will "
                    "not be written."}


tool("save_open",
     "Open a memory-card image (.ps2/.mc2/.mcd/.bin), a .psu export, or an "
     "individual save file (.cbs, .xps/.sps). Decodes every Suikoden V save "
     "it contains through s5save. The source is copied to a working "
     "location and never written.",
     {"path": S("string", "Path to the card or save file"),
      "index": S("integer", "Which save in the file to edit (default 0)")},
     ["path"], t_save_open)


def t_save_get(a):
    st = _save()
    section = a.get("section") or "summary"
    gd, indiv = _save_payload()
    if indiv is not None:
        fields = indiv.get("fields") or {}
        chars = []
    else:
        fields = SV.decode_gamedata(gd)
        chars = SV.read_all_characters(gd)
    if section == "summary":
        return {"fields": {k: fields.get(k) for k in
                           ("heroName", "castleName", "armyName", "level",
                            "potch", "partySP", "newGamePlus", "playtime")},
                "folder": st["folder"], "format": st["format"],
                "characterCount": len(chars),
                "stagedEdits": st["staged"]}
    if section == "characters":
        if indiv is not None:
            fail("character detail is not decoded for individual save files; "
                 "open the memory card or .psu form for per-character data")
        return {"characters": jsonable(chars)}
    if section == "party":
        return {"party": fields.get("party")}
    if section == "inventory":
        return {"inventory": jsonable(fields.get("inventory"))}
    if section == "recruitment":
        if indiv is not None:
            fail("recruitment detail is not decoded for individual save files")
        return {"recruitment": [{"idx": c["idx"], "recruited": c["recruited"],
                                 "state": c["recruitState"],
                                 "recruitable": c["recruitable"],
                                 "inParty": c["inParty"]} for c in chars]}
    if section == "names":
        return {"names": {k: fields.get(k) for k in
                          ("heroName", "castleName", "armyName")}}
    if section == "resources":
        return {"resources": {k: fields.get(k) for k in
                              ("potch", "partySP", "level")}}
    if section == "health":
        if indiv is not None:
            fail("the health audit snapshot is not available for individual "
                 "save files")
        return {"health": jsonable(SV.audit_snapshot(gd)),
                "note": "The raw audit snapshot the web editor's health "
                        "check consumes (web/health-core.js); the rules "
                        "themselves live in that JS core and are not "
                        "re-run here."}
    fail(f"unknown section '{section}' (summary, characters, party, "
         "inventory, recruitment, names, resources, health)")


tool("save_get",
     "Read the decoded state of the opened save: summary, characters, party, "
     "inventory, recruitment, names, resources, or the health audit "
     "snapshot. Shows the state on the working copy; staged edits are "
     "listed separately by save_list_changes.",
     {"section": S("string", "One of: summary, characters, party, "
                             "inventory, recruitment, names, resources, "
                             "health (default summary)")}, [], t_save_get)


_CHAR_SLOTS = ({"level", "exp", "sp", "wlv", "accessory", "helm", "body",
                "glove", "foot", "rhead", "rright", "rleft", "rec",
                "ss0", "ss1"}
               | {f"sk{i}" for i in range(48)}
               | {f"ruse{i}" for i in range(4)}
               | {f"held{i}" for i in range(4)})


def t_save_stage_character(a):
    st = _save()
    idx = int(a["index"])
    edits = a["edits"] or {}
    if not (0 <= idx < SV.NUM_CHARS):
        fail(f"character index {idx} out of range (0..{SV.NUM_CHARS - 1})")
    for slot in edits:
        if slot not in _CHAR_SLOTS:
            fail(f"unknown character edit '{slot}'. Slots: level, exp, sp, "
                 "wlv, accessory, helm, body, glove, foot, rhead, rright, "
                 "rleft, rec, ss0, ss1, sk0..sk47, ruse0..ruse3, "
                 "held0..held3 (held values are {cat, id, qty} objects)")
    for slot, val in edits.items():
        st["staged"][f"c{idx}_{slot}"] = val
    return {"staged": True, "index": idx,
            "stagedKeys": sorted(k for k in st["staged"]
                                 if k.startswith(f"c{idx}_"))}


tool("save_stage_character",
     "Stage character edits for one roster index, in s5save's own edit "
     "slots: level, exp, sp, wlv (weapon level 1..16), armor (helm, body, "
     "glove, foot, accessory), runes (rhead, rright, rleft), rune spell "
     "uses (ruse0..ruse3), skills (sk0..sk47 ranks, ss0/ss1 equipped "
     "slots), held items (held0..held3 as {cat, id, qty}), and rec "
     "(recruitment toggle, judged by s5save's own guards at export). "
     "Applied by s5save with its own clamping when save_export is called.",
     {"index": S("integer", "Roster index of the character"),
      "edits": S("object", "Map of slot name to value")},
     ["index", "edits"], t_save_stage_character)


def t_save_stage_misc(a):
    st = _save()
    edits = a["edits"] or {}
    confirm = bool(a.get("confirmNgPlusConversion"))
    for k, v in edits.items():
        if k == "newGamePlus":
            if not confirm:
                fail("refusing to stage newGamePlus without "
                     "confirmNgPlusConversion. Setting the NG+ carry byte "
                     "CONVERTS this save: when the game loads it, the "
                     "carry-over build resets the party and every "
                     "character record to the New Game Plus opening state "
                     "(potch and world position carry over). Pass "
                     "confirmNgPlusConversion: true if that conversion is "
                     "really what you want.")
            if int(v) not in (0, 1):
                fail("newGamePlus must be 0 or 1")
            st["staged"]["newGamePlus"] = int(v)
        elif k in ("heroName", "castleName", "armyName"):
            st["staged"][k] = str(v)
        elif k in ("potch", "partySP", "level"):
            st["staged"][k] = int(v)
        elif k == "party":
            if isinstance(v, dict):
                items = [(int(s), c) for s, c in v.items()]
            else:
                items = list(enumerate(v))
            for slot, cid in items:
                if not (0 <= slot < 10):
                    fail(f"party slot {slot} out of range (0..9: six battle "
                         "slots then four entourage slots)")
                st["staged"][f"party{slot}"] = int(cid)
        elif k == "inventory":
            if not isinstance(v, dict):
                fail("inventory edits are an object keyed "
                     "'<list>_<slot>' (list: bag, warehouse, keyItems) "
                     "with {cat, id, qty} values")
            for key, val in v.items():
                if not re.fullmatch(r"(bag|warehouse|keyItems)_\d+", key):
                    fail(f"bad inventory key '{key}' (expected "
                         "<bag|warehouse|keyItems>_<slot>)")
                st["staged"][f"inv_{key}"] = val
        elif k.startswith("inv_"):
            st["staged"][k] = v
        else:
            fail(f"unknown misc edit '{k}'. Accepted: heroName, castleName, "
                 "armyName, potch, partySP, level, party, inventory, "
                 "newGamePlus (gated, see the tool description)")
    return {"staged": True, "stagedKeys": sorted(st["staged"])}


tool("save_stage_misc",
     "Stage non-character save edits: names (heroName, castleName, "
     "armyName), resources (potch, partySP, level), party composition "
     "(party: a list of up to 10 character ids, battle slots then "
     "entourage, or a slot map), and inventory (an object keyed "
     "'<bag|warehouse|keyItems>_<slot>' with {cat, id, qty} values; "
     "{cat: 0} removes the entry). newGamePlus is special: setting it "
     "CONVERTS the save on load (the game resets party and character "
     "records to the NG+ opening state), so staging it requires the "
     "confirmNgPlusConversion argument.",
     {"edits": S("object", "Map of edit name to value"),
      "confirmNgPlusConversion": S(
          "boolean",
          "Required to stage newGamePlus: confirms you understand the "
          "save will be converted by the game's NG+ carry-over build on "
          "load")},
     ["edits"], t_save_stage_misc)


def t_save_list_changes(a):
    st = _save()
    return {"staged": jsonable(st["staged"])}


tool("save_list_changes",
     "Show everything staged for the opened save, in s5save's flat edit "
     "shape. Nothing has been written to any file yet.",
     {}, [], t_save_list_changes)


def t_save_discard_changes(a):
    st = _save()
    st["staged"] = {}
    return {"discarded": True}


tool("save_discard_changes", "Discard all staged save edits.", {}, [],
     t_save_discard_changes)


def t_save_export(a):
    st = _save()
    out = a["outPath"]
    _guard_output(out)
    shutil.copyfile(st["sourcePath"], out)
    try:
        if st["individual"]:
            r = SV.write_individual_save(out, st["staged"],
                                         make_backup=False)
        else:
            r = SV.write_save_fields(out, st["folder"], st["staged"],
                                     make_backup=False)
    except Exception as e:
        try:
            os.remove(out)
        except OSError:
            pass
        fail(f"s5save refused the write: {e}")
    if isinstance(r, dict) and r.get("error"):
        try:
            os.remove(out)
        except OSError:
            pass
        fail(f"s5save refused the write: {r['error']}")
    return {"written": out, "result": jsonable(r), "sha256": sha256_file(out)}


tool("save_export",
     "Apply the staged edits through s5save (card ECC handled by the core) "
     "and write the result to a caller-chosen output path. The opened "
     "source is never the target.",
     {"outPath": S("string", "Where to write the modified card or save")},
     ["outPath"], t_save_export)


# ---------------------------------------------------------------------------
# MCP stdio transport (JSON-RPC 2.0, newline-delimited)
# ---------------------------------------------------------------------------
PROTOCOL = "2024-11-05"


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def handle(msg):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return  # notification
    try:
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid,
                  "result": {"protocolVersion": PROTOCOL,
                             "capabilities": {"tools": {}},
                             "serverInfo": {"name": "suikoden-5-editor",
                                            "version": "1.0.0"}}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid,
                  "result": {"tools": [{"name": t["name"],
                                        "description": t["description"],
                                        "inputSchema": t["inputSchema"]}
                                       for t in tools]}})
        elif method == "tools/call":
            t = next((x for x in tools if x["name"] == params.get("name")), None)
            if t is None:
                send({"jsonrpc": "2.0", "id": mid,
                      "result": {"isError": True,
                                 "content": [{"type": "text",
                                              "text": f"unknown tool '{params.get('name')}'"}]}})
                return
            try:
                r = t["fn"](params.get("arguments") or {})
                send({"jsonrpc": "2.0", "id": mid,
                      "result": {"content": [{"type": "text",
                                              "text": json.dumps(r, indent=2)}]}})
            except ToolError as e:
                send({"jsonrpc": "2.0", "id": mid,
                      "result": {"isError": True,
                                 "content": [{"type": "text",
                                              "text": f"Error: {e}"}]}})
            except Exception as e:  # engine refusals surface in plain words
                send({"jsonrpc": "2.0", "id": mid,
                      "result": {"isError": True,
                                 "content": [{"type": "text",
                                              "text": f"Error: {type(e).__name__}: {e}"}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601,
                            "message": f"method not found: {method}"}})
    except Exception as e:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32603, "message": f"internal error: {e}"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception:
            continue  # ignore malformed lines, as the S3 server does


if __name__ == "__main__":
    main()
