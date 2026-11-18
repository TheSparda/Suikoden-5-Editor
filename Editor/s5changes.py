#!/usr/bin/env python3
"""Decoded diff between two Suikoden V disc images (or their front slices):
which of the editor's tables and fields differ, with old and new values.

This is the Suikoden V counterpart of the Suikoden III editor's Changes tab.
S5's architecture keeps all decoding in Python (the web editor runs this
module inside Pyodide like the rest of the engine), so the diff lives here
rather than in a JS core, and the region map is built from s5fields' own
table constants: a table that moves in s5fields moves here too.

The map's contract (same as S3's changes-core): regions are sorted by
offset and never overlap, so one changed byte belongs to exactly one field.
Any changed byte no region claims is reported as an "Unmapped" row rather
than dropped: a change the report silently omits is far worse than a row
of hex, because the point of the report is that it is the complete list.
Code patches (balance scalers, perk toggles, overlay rewrites) have no
table regions, so they honestly surface as Unmapped rows.

Only fields the editor itself can write are mapped. Gaps inside a record
(the undecoded bytes between known fields) are unmapped by design.
"""
import s5fields as F
import s5patch as P

# Record counts for the TABLES, from s5fields' own facts:
# stats / affinities / starting equipment / starting items are indexed by
# the 120-entry character table; equipable skills documents count 113;
# weapon growth is indexed by WEAPON RECORD id (F.WEAPON_RECORD_COUNT
# records 0..81, issue #22), not by character.
def _table_counts(n_chars):
    return {"stats": n_chars, "affinities": n_chars,
            "equipable skills": 113,
            "weapon growth": F.WEAPON_RECORD_COUNT,
            "starting equipment": n_chars, "starting items": n_chars}

_GROUP_ORDER = ["Characters", "Spells", "Runes", "Rune prices", "Heal prices",
                "Gear", "Enemies", "MP growth", "Skill effects", "Unites",
                "Char names", "Field models", "Unmapped"]


def _names():
    try:
        chars = F.res_json("s5_characters.json")
    except Exception:
        chars = []
    cn = {c["id"]: c["name"] for c in chars}
    try:
        armor = F.res_json("s5_armor_names.json")
    except Exception:
        armor = {}
    try:
        spells = F.res_json("s5_spell_names.json")
    except Exception:
        spells = []
    try:
        enemies = F.res_json("s5_enemy_names.json")
    except Exception:
        enemies = {}
    try:
        held = F.res_json("s5_held_items.json").get("map", {})
    except Exception:
        held = {}
    try:
        drops = F.res_json("s5_drop_items.json")
    except Exception:
        drops = {}
    return cn, armor, spells, enemies, held, drops


def static_regions():
    """Every mapped field of every declarative table in s5fields.
    A region is {off, len, group, label, kind, signed?}."""
    cn, armor, spells, enemies, _held, _drops = _names()
    R = []

    def add(off, ln, group, label, kind, signed=False):
        R.append({"off": off, "len": ln, "group": group, "label": label,
                  "kind": kind, "signed": signed})

    counts = _table_counts(len(cn) or 120)
    table_groups = {"stats": "Characters", "affinities": "Characters",
                    "equipable skills": "Characters",
                    "weapon growth": "Characters",
                    "starting equipment": "Characters",
                    "starting items": "Characters"}
    for tname, (base, stride, fields) in F.TABLES.items():
        for rec in range(counts[tname]):
            # Weapon growth rows are weapon records, not characters (#22).
            who = ("Weapon record %d" % rec) if tname == "weapon growth" \
                else cn.get(rec, "#%d" % rec)
            for (label, foff, width, kind) in fields:
                add(base + rec * stride + foff, width, table_groups[tname],
                    "%s · %s" % (who, label), kind)

    for rec in range(F.SPELL_COUNT):
        nm = spells[rec] if rec < len(spells) else "#%d" % rec
        for (label, foff, width, kind) in F.SPELL_FIELDS:
            add(F.SPELL_BASE + rec * F.SPELL_STRIDE + foff, width, "Spells",
                "%s · %s" % (nm, label), kind)

    for rec in range(F.RUNE_GRANT_COUNT):
        nm = F.RUNE_GRANT_NAMES[rec] if rec < len(F.RUNE_GRANT_NAMES) else "#%d" % rec
        for (label, foff, width, kind) in F.RUNE_GRANT_FIELDS:
            add(F.RUNE_GRANT_BASE + rec * F.RUNE_GRANT_STRIDE + foff, width,
                "Runes", "%s · %s" % (nm, label), kind)

    for base, count, names, group in (
            (F.RUNEPRICE_BASE, F.RUNEPRICE_COUNT, F.RUNEPRICE_NAMES, "Rune prices"),
            (F.HEALPRICE_BASE, F.HEALPRICE_COUNT, F.HEALPRICE_NAMES, "Heal prices")):
        stride = F.RUNEPRICE_STRIDE if group == "Rune prices" else F.HEALPRICE_STRIDE
        for rec in range(count):
            nm = names[rec] if rec < len(names) else "#%d" % rec
            add(base + rec * stride + 0, 3, group, "%s · Buy price" % nm, "num")
            add(base + rec * stride + 4, 3, group, "%s · Sell price" % nm, "num")

    for slot, (base, count) in F.ARMOR_TABLES.items():
        slot_names = armor.get("glove" if slot == "arm" else slot, {})
        for rec in range(count):
            nm = slot_names.get(str(rec), "")
            who = "#%d %s" % (rec, nm) if nm else "#%d" % rec
            for (label, foff, width, signed) in F.ARMOR_FIELDS:
                add(base + rec * F.ARMOR_STRIDE + foff, width,
                    "Gear", "%s (%s) · %s" % (who, F.ARMOR_SLOT_LABEL[slot], label),
                    "geartype" if (label == "Type" and slot != "accessory") else "num",
                    signed)

    for rec in range(F.ENEMY_MAX):
        nm = enemies.get(str(rec), "#%d" % rec)
        for (label, foff, width, kind) in F.ENEMY_FIELDS:
            add(F.ENEMY_BASE + rec * F.ENEMY_STRIDE + foff, width, "Enemies",
                "%s · %s" % (nm, label), kind)

    for g in range(F.MP_GROUPS):
        for k, label in enumerate(F.MP_FIELD_LABELS):
            add(F.MP_BASE + g * F.MP_STRIDE + k * 2, 2, "MP growth",
                "%s · %s" % (F.MP_GROUP_LABELS[g], label), "num")

    for rec in range(F.SKILLFX_COUNT):
        nm = F.SKILLFX_NAMES[rec] if rec < len(F.SKILLFX_NAMES) else "#%d" % rec
        for k, rank in enumerate(F.SKILLFX_RANKS):
            add(F.SKILLFX_BASE + rec * F.SKILLFX_STRIDE + k * 2, 2,
                "Skill effects", "%s · Rank %s" % (nm, rank), "num")

    for k in range(F.RESOURCE_NAME_COUNT):
        add(F.RESOURCE_NAME_BASE + k * F.RESOURCE_NAME_STRIDE,
            F.RESOURCE_NAME_STRIDE, "Field models", "Resource name %d" % k, "text")
    for k in range(F.MODEL_PTR_COUNT):
        add(F.MODEL_PTR_BASE + k * 4, 4, "Field models",
            "Model slot %d" % k, "modelptr")

    R.sort(key=lambda r: (r["off"], r["len"]))
    for a, b in zip(R, R[1:]):
        if b["off"] < a["off"] + a["len"]:
            raise ValueError("region map overlap at 0x%X: %r vs %r"
                             % (b["off"], a, b))
    return R


class _BytesISO:
    """The rd/ru surface s5patch.parse_unites needs, over a bytes object."""
    def __init__(self, data):
        self.d = data

    def rd(self, off, n):
        return self.d[off:off + n]

    def ru(self, off, n):
        return int.from_bytes(self.d[off:off + n], "little")


def dynamic_regions(base):
    """Regions whose offsets depend on the disc's own content: unite
    participant bytes (packed variable-length records, parsed with
    s5patch's own parser) and the character name table entries (found with
    the same validity walk as s5patch.read_names). Parsed from the BASE
    image, since that is the layout being diffed against."""
    cn, _a, _s, _e, _h, _d = _names()
    R = []
    try:
        for u in P.parse_unites(_BytesISO(base)):
            meta = F.UNITE_NAMES[u["id"]] if u["id"] < len(F.UNITE_NAMES) else {}
            nm = meta.get("name", u["jpName"] or "Unite %d" % u["id"])
            for slot in range(u["count"]):
                R.append({"off": u["idsOff"] + slot, "len": 1, "group": "Unites",
                          "label": "%s · member %d" % (nm, slot + 1),
                          "kind": "charid", "signed": False})
    except Exception:
        pass
    i, empty, found = 0, 0, 0
    while True:
        off = F.NAME_TABLE_BASE + i * F.NAME_ENTRY_SIZE
        if off + F.NAME_ENTRY_SIZE > len(base):
            break
        rec = base[off:off + F.NAME_ENTRY_SIZE]
        nm = rec.split(b"\x00")[0]
        if 1 <= len(nm) <= F.NAME_MAX_CHARS and all(32 <= c < 127 for c in nm):
            R.append({"off": off, "len": F.NAME_ENTRY_SIZE, "group": "Char names",
                      "label": "Name entry %d" % i, "kind": "text", "signed": False})
            empty, found = 0, found + 1
        else:
            empty += 1
            if empty >= 3 and found:
                break
        i += 1
    return R


def _fmt(region, img, names):
    cn, _armor, spells, _enemies, held, drops = names
    raw = img[region["off"]:region["off"] + region["len"]]
    kind = region["kind"]
    if kind == "text":
        return raw.split(b"\x00")[0].decode("latin-1", "replace")
    if kind == "modelptr":
        va = int.from_bytes(raw, "little")
        off = va - F.MODEL_VADDR_DELTA
        if 0 <= off < len(img):
            target = img[off:off + 32].split(b"\x00")[0].decode("latin-1", "replace")
            return "0x%X (%s)" % (va, target)
        return "0x%X" % va
    if kind == "helditem":
        va = int.from_bytes(raw, "little")
        return held.get(str(va), "0x%X" % va)
    if kind == "charid":
        cid = raw[0] if raw else 0
        return F.UNITE_EXTRA_CHARS.get(cid, cn.get(cid, "#%d" % cid))
    v = int.from_bytes(raw, "little", signed=bool(region.get("signed")))
    if kind == "grade":
        return "%d (%s)" % (v, F.AFFINITY_GRADES[v]) if 0 <= v < len(F.AFFINITY_GRADES) else str(v)
    if kind == "egrade":
        return "%d (%s)" % (v, F.ENEMY_AFFINITY_GRADES[v]) if 0 <= v < len(F.ENEMY_AFFINITY_GRADES) else str(v)
    if kind == "rank":
        return "%d (%s)" % (v, F.RANK_NAMES[v]) if 0 <= v < len(F.RANK_NAMES) else str(v)
    if kind == "element":
        return "%d (%s)" % (v, F.ELEMENT_NAMES[v]) if v in F.ELEMENT_NAMES else str(v)
    if kind == "target":
        return "%d (%s)" % (v, F.TARGET_NAMES[v]) if v in F.TARGET_NAMES else str(v)
    if kind == "spellstatus":
        return "%d (%s)" % (v, F.SPELL_STATUS_NAMES[v]) if v in F.SPELL_STATUS_NAMES else str(v)
    if kind == "spellid":
        return "%d (%s)" % (v, spells[v]) if 0 <= v < len(spells) else str(v)
    if kind == "geartype":
        return "%d (%s)" % (v, F.GEAR_TYPE_NAMES[v]) if v in F.GEAR_TYPE_NAMES else str(v)
    if kind == "drop":
        cat, item = v & 0xFF, (v >> 8) & 0xFF
        nm = (drops.get("items") or {}).get("%d:%d" % (cat, item), "")
        cnm = (drops.get("categories") or {}).get(str(cat), "")
        return "%s %d%s" % (cnm or "category %d" % cat, item,
                             " (%s)" % nm if nm else "")
    if kind in ("armorhead", "armorbody", "armorarm", "armorfoot"):
        slot = {"armorhead": "head", "armorbody": "body",
                "armorarm": "glove", "armorfoot": "foot"}[kind]
        _cn, armor, _sp, _en, _h, _d = names
        nm = armor.get(slot, {}).get(str(v), "")
        return "%d (%s)" % (v, nm) if nm else str(v)
    return str(v)


def _runs(cur, base):
    """Strict changed-byte runs (no gap merging: a run must never claim a
    byte that did not change, or a field would be blamed for a neighbour's
    edit). Runs are {off, len} in ascending order."""
    n = min(len(cur), len(base))
    out, i = [], 0
    while i < n:
        if cur[i] != base[i]:
            s = i
            while i < n and cur[i] != base[i]:
                i += 1
            out.append({"off": s, "len": i - s})
        else:
            i += 1
    return out


_STATIC_CACHE = None


def _static_cached():
    global _STATIC_CACHE
    if _STATIC_CACHE is None:
        _STATIC_CACHE = static_regions()
    return _STATIC_CACHE


def diff_report(cur, base):
    """The decoded diff of `cur` against the pristine `base`.
    Returns {rows, summary}; each row is {off, group, label, old, new} for a
    mapped field, or {off, group: "Unmapped", label, old, new} with hex for
    a changed span no field claims."""
    names = _names()
    regions = _static_cached() + dynamic_regions(base)
    regions.sort(key=lambda r: (r["off"], r["len"]))
    for a, b in zip(regions, regions[1:]):
        if b["off"] < a["off"] + a["len"]:
            raise ValueError("region map overlap at 0x%X" % b["off"])
    rs = _runs(cur, base)
    rows, seen = [], set()
    ri = 0
    for run in rs:
        end = run["off"] + run["len"]
        while ri < len(regions) and regions[ri]["off"] + regions[ri]["len"] <= run["off"]:
            ri += 1
        cur_pos, j = run["off"], ri
        while j < len(regions) and regions[j]["off"] < end:
            g = regions[j]
            if g["off"] > cur_pos:
                rows.append(_unmapped(cur, base, cur_pos, min(g["off"], end)))
            if j not in seen:
                seen.add(j)
                rows.append({"off": g["off"], "group": g["group"],
                             "label": g["label"],
                             "old": _fmt(g, base, names),
                             "new": _fmt(g, cur, names)})
            cur_pos = max(cur_pos, g["off"] + g["len"])
            j += 1
        if cur_pos < end:
            rows.append(_unmapped(cur, base, cur_pos, end))
    rows.sort(key=lambda r: r["off"])
    unmapped = [r for r in rows if r["group"] == "Unmapped"]
    summary = {
        "fields": len(rows) - len(unmapped),
        "unmappedRows": len(unmapped),
        "unmappedBytes": sum(r["len"] for r in unmapped),
        "totalBytes": sum(r["len"] for r in rs),
        "runs": len(rs),
        "sizeMismatch": len(cur) != len(base),
    }
    return {"rows": rows, "summary": summary}


def _unmapped(cur, base, off, end):
    def hx(img):
        b = img[off:end]
        s = b[:32].hex(" ")
        return s + (" ..." if len(b) > 32 else "")
    return {"off": off, "len": end - off, "group": "Unmapped",
            "label": "0x%X (%d bytes)" % (off, end - off),
            "old": hx(base), "new": hx(cur)}
