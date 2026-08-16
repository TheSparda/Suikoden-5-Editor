#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) ISO engine + CLI.

Reverse-engineered from the game's own data, validated vs a
real ISO. See s5fields.py. Never writes unverified fields.

Commands:
  verify   <iso>
  chars    <iso>                         list playable characters (id + name)
  dump     <iso> --id N [--table stats]  read a character's table(s)
  set      <iso> --id N --table T --field K --value V   write one field (.bak first)
  names    <iso> [--limit N]             dump 0x691600 name table
  set-name <iso> --index I --name X      rename in the name table
  ladder   <iso>                         dump the 0x4986C0 ladder
  peek     <iso> --off 0x.. --len N
  poke     <iso> --off 0x.. --u8/--u16/--hex ..
"""
import argparse, os, sys, shutil
import s5fields as F

HERE = os.path.dirname(os.path.abspath(__file__))


class Iso:
    def __init__(self, path, writable=False):
        self.path = path; self.f = open(path, "r+b" if writable else "rb"); self.writable = writable
    def close(self):
        try: self.f.close()
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def rd(self, off, n): self.f.seek(off); return self.f.read(n)
    def wr(self, off, b):
        if not self.writable: raise IOError("read-only")
        self.f.seek(off); self.f.write(b)
    def ru(self, off, w): return int.from_bytes(self.rd(off, w), "little")
    def wu(self, off, w, v):
        if not (0 <= v < (1 << 8*w)): raise ValueError(f"{v} out of range for {w}B")
        self.wr(off, int(v).to_bytes(w, "little"))


def is_valid(iso): return iso.rd(F.SERIAL_OFF, len(F.SERIAL_STR)) == F.SERIAL_STR


def table_addr(table, cid):
    base, stride, _ = F.TABLES[table]; return base + cid * stride


def read_table(iso, table, cid):
    base = table_addr(table, cid)
    return [{"label": l, "off": o, "width": w, "kind": k, "value": iso.ru(base + o, w)}
            for (l, o, w, k) in F.TABLES[table][2]]


def read_character(iso, cid):
    return {t: read_table(iso, t, cid) for t in F.TABLES}


def write_field(iso, table, cid, label, value):
    for (l, o, w, k) in F.TABLES[table][2]:
        if l == label:
            iso.wu(table_addr(table, cid) + o, w, value); return True
    raise KeyError(f"no field {label!r} in {table}")


def read_prices(iso, limit=0):
    out = []
    n = F.PRICE_COUNT if not limit else min(limit, F.PRICE_COUNT)
    for i in range(n):
        base = F.PRICE_BASE + i * F.PRICE_STRIDE
        rec = {"index": i, "off": base}
        for name, off, w in F.PRICE_FIELDS:
            rec[name] = iso.ru(base + off, w)
        out.append(rec)
    return out

def write_price(iso, index, field, value):
    off = next((o for (n, o, w) in F.PRICE_FIELDS if n == field), None)
    w = next((w for (n, o, w) in F.PRICE_FIELDS if n == field), None)
    if off is None: raise KeyError(field)
    iso.wu(F.PRICE_BASE + index * F.PRICE_STRIDE + off, w, value)

def spell_addr(sid): return F.SPELL_BASE + sid * F.SPELL_STRIDE

def read_spell(iso, sid):
    base = spell_addr(sid)
    return [{"label": l, "off": o, "width": w, "kind": k, "value": iso.ru(base + o, w)}
            for (l, o, w, k) in F.SPELL_FIELDS]

def write_spell_field(iso, sid, label, value):
    for (l, o, w, k) in F.SPELL_FIELDS:
        if l == label:
            iso.wu(spell_addr(sid) + o, w, value); return True
    raise KeyError(f"no spell field {label!r}")


def rune_addr(rid): return F.RUNE_GRANT_BASE + rid * F.RUNE_GRANT_STRIDE

def read_rune(iso, rid):
    base = rune_addr(rid)
    return [{"label": l, "off": o, "width": w, "kind": k, "value": iso.ru(base + o, w)}
            for (l, o, w, k) in F.RUNE_GRANT_FIELDS]

def read_runes(iso):
    """All 24 grant records: id, name, start, count (for the grouped UI)."""
    out = []
    for rid in range(F.RUNE_GRANT_COUNT):
        base = rune_addr(rid)
        out.append({"id": rid, "name": F.RUNE_GRANT_NAMES[rid] if rid < len(F.RUNE_GRANT_NAMES) else f"Rune {rid}",
                    "start": iso.ru(base, 1), "count": iso.ru(base + 2, 1), "synthetic": False})
    for i, sr in enumerate(F.SYNTH_RUNES):
        out.append({"id": F.SYNTH_RUNE_BASE + i, "name": sr["name"],
                    "start": sr["start"], "count": sr["count"], "synthetic": True})
    return out

def write_rune_field(iso, rid, label, value):
    for (l, o, w, k) in F.RUNE_GRANT_FIELDS:
        if l == label:
            iso.wu(rune_addr(rid) + o, w, value); return True
    raise KeyError(f"no rune field {label!r}")


# ---- Armor (gear) tables -----------------------------------------------------
def armor_addr(slot, i):
    base, _ = F.ARMOR_TABLES[slot]; return base + i * F.ARMOR_STRIDE

def _armor_summary(iso, base):
    b = iso.rd(base + F.ARMOR_SUMMARY_OFF, F.ARMOR_SUMMARY_LEN)
    e = b.find(b"\x00")
    try: return b[:e if e >= 0 else len(b)].decode("cp932")
    except Exception: return ""

def _armor_name_jp(iso, base):
    # The item's own (Japanese) name is embedded at record-0x1C (the prior
    # record's +0x78 field). Verified: aligns 1:1 with the guide across all slots.
    b = iso.rd(base - 0x1C, 0x18); e = b.find(b"\x00")
    try: return b[:e if e >= 0 else len(b)].decode("cp932")
    except Exception: return ""

try:
    import json as _json_armor
    _ARMOR_EN = _json_armor.load(open(os.path.join(HERE, "s5_armor_stat_names.json")))
except Exception:
    _ARMOR_EN = {}
def _armor_name(iso, slot, i, base):
    # English label only: matched EN name, else the EN-translated effect summary
    # (never the Japanese internal name). jp is returned for reference but not shown.
    en = (_ARMOR_EN.get(slot) or {}).get(str(i))
    jp = _armor_name_jp(iso, base)
    label = en or F.armor_summary_en(_armor_summary(iso, base))
    return label, en, jp

def _rd_signed(iso, off, w, signed):
    v = iso.ru(off, w)
    if signed and w == 1 and v >= 0x80: v -= 0x100
    return v

def read_armor_item(iso, slot, i):
    base = armor_addr(slot, i)
    summ = _armor_summary(iso, base)
    name, en, jp = _armor_name(iso, slot, i, base)
    fields = [{"label": l, "off": o, "width": w, "kind": "num", "signed": s,
               "value": _rd_signed(iso, base + o, w, s)}
              for (l, o, w, s) in F.ARMOR_FIELDS]
    return {"fields": fields, "name": name, "nameEn": en, "nameJp": jp,
            "summary": summ, "summaryEn": F.armor_summary_en(summ), "off": base}

def list_armor(iso, slot):
    _, n = F.ARMOR_TABLES[slot]
    out = []
    for i in range(n):
        base = armor_addr(slot, i)
        name, en, jp = _armor_name(iso, slot, i, base)
        out.append({"id": i, "def": iso.ru(base + 0x68, 1), "buy": iso.ru(base, 4),
                    "name": name or f"{F.ARMOR_SLOT_LABEL[slot]} #{i}",
                    "effect": F.armor_summary_en(_armor_summary(iso, base))})
    return out

def write_armor_field(iso, slot, i, label, value):
    for (l, o, w, s) in F.ARMOR_FIELDS:
        if l == label:
            if s and w == 1: value &= 0xFF          # two's-complement for signed byte
            iso.wu(armor_addr(slot, i) + o, w, value); return True
    raise KeyError(f"no armor field {label!r}")


def read_mp(iso):
    out = []
    for grp in range(F.MP_GROUPS):
        base = F.MP_BASE + grp * F.MP_STRIDE
        out.append({"group": grp, "label": F.MP_GROUP_LABELS[grp],
                    "values": [iso.ru(base + 2 * k, 2) for k in range(len(F.MP_FIELD_LABELS))]})
    return out

def write_mp(iso, group, idx, value):
    if not (0 <= group < F.MP_GROUPS) or not (0 <= idx < len(F.MP_FIELD_LABELS)):
        raise KeyError("mp index out of range")
    iso.wu(F.MP_BASE + group * F.MP_STRIDE + 2 * idx, 2, value)


def read_skillfx(iso):
    out = []
    for sid in range(F.SKILLFX_COUNT):
        base = F.SKILLFX_BASE + sid * F.SKILLFX_STRIDE
        out.append({"id": sid,
                    "name": F.SKILLFX_NAMES[sid] if sid < len(F.SKILLFX_NAMES) else f"Skill {sid}",
                    "values": [iso.ru(base + 2 * k, 2) for k in range(len(F.SKILLFX_RANKS))]})
    return out

def write_skillfx(iso, sid, rank_idx, value):
    if not (0 <= sid < F.SKILLFX_COUNT) or not (0 <= rank_idx < len(F.SKILLFX_RANKS)):
        raise KeyError("skillfx index out of range")
    iso.wu(F.SKILLFX_BASE + sid * F.SKILLFX_STRIDE + 2 * rank_idx, 2, value)


def enemy_addr(eid): return F.ENEMY_BASE + eid * F.ENEMY_STRIDE

def read_enemy(iso, eid):
    base = enemy_addr(eid)
    return [{"label": l, "off": o, "width": w, "kind": k, "value": iso.ru(base + o, w)}
            for (l, o, w, k) in F.ENEMY_FIELDS]

def read_enemies(iso, names=None):
    """List unit records that have HP (enemies + units), with best-effort names."""
    names = names or {}
    out = []
    for eid in range(F.ENEMY_MAX):
        hp = iso.ru(enemy_addr(eid) + 2, 2)
        if 0 < hp < 60000:
            out.append({"id": eid, "hp": hp, "name": names.get(str(eid)) or f"Enemy {eid}"})
    return out

def write_enemy_field(iso, eid, label, value):
    for (l, o, w, k) in F.ENEMY_FIELDS:
        if l == label:
            iso.wu(enemy_addr(eid) + o, w, value); return True
    raise KeyError(f"no enemy field {label!r}")


def read_cstring(iso, off, maxlen=64):
    b = iso.rd(off, maxlen); e = b.find(b"\x00")
    return b[:e if e >= 0 else maxlen]

def set_cstring(iso, off, text):
    """Overwrite a null-terminated string in place, capped to its original byte
    length (never overruns into the next string). Returns bytes written."""
    orig = read_cstring(iso, off, 128)
    cap = len(orig)
    s = text.encode("latin1", "replace")[:cap]
    iso.wr(off, s + b"\x00" * (cap - len(s)))   # keep same field length
    return cap

def read_names(iso, limit=0):
    out = []; empty = 0; i = 0
    while True:
        rec = iso.rd(F.NAME_TABLE_BASE + i*F.NAME_ENTRY_SIZE, F.NAME_ENTRY_SIZE)
        nm = rec.split(b"\x00")[0]
        if 1 <= len(nm) <= F.NAME_MAX_CHARS and all(32 <= c < 127 for c in nm):
            out.append({"index": i, "off": F.NAME_TABLE_BASE + i*F.NAME_ENTRY_SIZE,
                        "name": nm.decode("ascii")}); empty = 0
        else:
            empty += 1
            if empty >= 3 and out: break
        i += 1
        if limit and len(out) >= limit: break
        if i > 4000: break
    return out


def set_name(iso, index, name):
    if len(name) > F.NAME_MAX_CHARS: raise ValueError(f"max {F.NAME_MAX_CHARS} chars")
    iso.wr(F.NAME_TABLE_BASE + index*F.NAME_ENTRY_SIZE,
           name.encode("ascii").ljust(F.NAME_ENTRY_SIZE, b"\x00"))


def backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak): shutil.copy2(path, bak)
    return bak

# ---- Hard Mode: scale every character's VERIFIED starting stats party-wide.
# Idempotent via a sidecar baseline (originals stored once), so re-applying a new
# factor always scales the ORIGINAL values and Restore is exact.
import json as _json
_HM_STATS = [f for f in F.TABLES["stats"][2] if f[3] == "num" and f[1] < 9]  # 9 base stats @off 0-8 (u8); excludes the 9 growth fields at 9-17

def _hm_sidecar(iso_path): return iso_path + ".hardmode.json"

def hardmode_apply(iso_path, factor):
    """Multiply all characters' starting stats by `factor` (0.1..10). Stores a baseline
    on first apply. Returns count of characters touched."""
    chars = [c["id"] for c in F.load_characters()]
    side = _hm_sidecar(iso_path)
    base = {}
    if os.path.exists(side):
        base = _json.load(open(side))
    backup(iso_path)
    with Iso(iso_path, writable=True) as g:
        for cid in chars:
            a = table_addr("stats", cid); key = str(cid)
            if key not in base:
                base[key] = [g.ru(a + off, w) for (_, off, w, _k) in _HM_STATS]
            for i, (_, off, w, _k) in enumerate(_HM_STATS):
                v = int(base[key][i] * factor)
                g.wu(a + off, w, max(0, min((1 << 8*w) - 1, v)))
    _json.dump(base, open(side, "w"))
    return len(chars)

def hardmode_restore(iso_path):
    side = _hm_sidecar(iso_path)
    if not os.path.exists(side): return 0
    base = _json.load(open(side))
    with Iso(iso_path, writable=True) as g:
        for key, vals in base.items():
            a = table_addr("stats", int(key))
            for i, (_, off, w, _k) in enumerate(_HM_STATS):
                g.wu(a + off, w, vals[i])
    os.remove(side)
    return len(base)


# --------------------------------------------------------------------------- CLI
def _verify(a):
    with Iso(a.iso) as g: ok = is_valid(g)
    print("VALID SLUS-21291" if ok else "NOT recognized"); return 0 if ok else 2

def _chars(a):
    for c in F.load_characters(): print(f"  {c['id']:>3}  {c['name']}")
    return 0

def _dump(a):
    with Iso(a.iso) as g:
        tables = [a.table] if a.table else list(F.TABLES)
        nm = {c["id"]: c["name"] for c in F.load_characters()}.get(a.id, "?")
        print(f"Character #{a.id} ({nm})")
        for t in tables:
            print(f"  [{t}] @0x{table_addr(t,a.id):X}")
            for r in read_table(g, t, a.id):
                print(f"    +0x{r['off']:02X} {r['width']}B {r['label']:<16} = {r['value']}")
    return 0

def _set(a):
    backup(a.iso)
    with Iso(a.iso, writable=True) as g:
        write_field(g, a.table, a.id, a.field, a.value)
    print(f"#{a.id} {a.table}.{a.field!r} = {a.value}"); return 0

def _names(a):
    with Iso(a.iso) as g:
        for e in read_names(g, a.limit): print(f"  #{e['index']:>3} 0x{e['off']:X}  {e['name']}")
    return 0

def _setname(a):
    backup(a.iso)
    with Iso(a.iso, writable=True) as g: set_name(g, a.index, a.name)
    print(f"renamed #{a.index} -> {a.name!r}"); return 0

def _ladder(a):
    with Iso(a.iso) as g:
        print("extra @0x%X:" % F.LADDER_EXTRA_OFF, g.ru(F.LADDER_EXTRA_OFF, 2))
        vals = [g.ru(F.LADDER_OFF + i*2, 2) for i in range(F.LADDER_COUNT)]
        for gg in range(0, F.LADDER_COUNT, 10): print(" ", vals[gg:gg+10])
    return 0

def _peek(a):
    with Iso(a.iso) as g: b = g.rd(int(a.off, 0), max(1, min(256, a.len)))
    print(b.hex(" ")); print("".join(chr(c) if 32 <= c < 127 else "." for c in b)); return 0

def _findbytes(a):
    needle = bytes.fromhex(a.hex.replace(" ", ""))
    hits = []; CH = 8 << 20; ov = len(needle); pos = 0
    sz = os.path.getsize(a.iso)
    with Iso(a.iso) as g:
        while pos < sz and len(hits) < a.max:
            buf = g.rd(pos, CH)
            if not buf: break
            j = buf.find(needle)
            while j >= 0 and len(hits) < a.max:
                hits.append(pos + j); j = buf.find(needle, j + 1)
            pos += CH - ov
    print(f"{len(hits)} hit(s):", ", ".join(hex(h) for h in hits))
    return 0

def _dumpregion(a):
    off = int(a.off, 0); n = min(a.len, 4096)
    with Iso(a.iso) as g: b = g.rd(off, n)
    for r in range(0, len(b), 16):
        chunk = b[r:r+16]
        hexs = " ".join(f"{c:02x}" for c in chunk)
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        print(f"  0x{off+r:08X}  {hexs:<47}  {asc}")
    return 0

def _ids(a):
    import json
    path = os.path.join(HERE, "s5_reference.json")
    try: ref = json.load(open(path))
    except Exception: print("s5_reference.json not found"); return 2
    cats = [a.category] if a.category else list(ref)
    for c in cats:
        rows = ref.get(c, [])
        if a.filter: rows = [r for r in rows if a.filter.lower() in r["name"].lower()]
        print(f"\n[{c}] {len(rows)}")
        for r in rows[:a.limit]: print(f"  {r['off']}  {r['name']}")
    return 0

def _setstring(a):
    backup(a.iso)
    with Iso(a.iso, writable=True) as g:
        cap = set_cstring(g, int(a.off, 0), a.text)
    print(f"wrote string @0x{int(a.off,0):X} (cap {cap} bytes)"); return 0

def _poke(a):
    backup(a.iso)
    with Iso(a.iso, writable=True) as g:
        off = int(a.off, 0)
        if a.hex is not None: g.wr(off, bytes.fromhex(a.hex.replace(" ", "")))
        elif a.u16 is not None: g.wu(off, 2, a.u16)
        elif a.u8 is not None: g.wu(off, 1, a.u8)
        else: print("need --u8/--u16/--hex", file=sys.stderr); return 2
    print(f"wrote 0x{off:X}"); return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Suikoden V ISO engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    def add(name, fn, args=()):
        sp = sub.add_parser(name); sp.add_argument("iso")
        for aa in args: sp.add_argument(*aa[0], **aa[1])
        sp.set_defaults(fn=fn)
    add("verify", _verify)
    add("chars", _chars)
    add("dump", _dump, [(("--id",), dict(type=int, required=True)), (("--table",), dict(choices=list(F.TABLES)))])
    add("set", _set, [(("--id",), dict(type=int, required=True)), (("--table",), dict(required=True, choices=list(F.TABLES))),
                      (("--field",), dict(required=True)), (("--value",), dict(type=int, required=True))])
    add("names", _names, [(("--limit",), dict(type=int, default=0))])
    add("set-name", _setname, [(("--index",), dict(type=int, required=True)), (("--name",), dict(required=True))])
    add("ladder", _ladder)
    add("find-bytes", _findbytes, [(("--hex",), dict(required=True)), (("--max",), dict(type=int, default=16))])
    add("dump-region", _dumpregion, [(("--off",), dict(required=True)), (("--len",), dict(type=int, default=256))])
    add("ids", _ids, [(("--category",), dict()), (("--filter",), dict()), (("--limit",), dict(type=int, default=200))])
    add("set-string", _setstring, [(("--off",), dict(required=True)), (("--text",), dict(required=True))])
    def _hm(a):
        if a.restore: n = hardmode_restore(a.iso); print(f"restored {n} chars")
        else: n = hardmode_apply(a.iso, a.factor); print(f"scaled stats of {n} chars x{a.factor}")
        return 0
    add("hardmode", _hm, [(("--factor",), dict(type=float, default=0.5)), (("--restore",), dict(action="store_true"))])
    def _prices(a):
        with Iso(a.iso) as g:
            for r in read_prices(g, a.limit):
                if r["buy"] or r["sell"]: print(f"  #{r['index']:3d} buy={r['buy']:>7} sell={r['sell']:>7}")
        return 0
    add("prices", _prices, [(("--limit",), dict(type=int, default=0))])
    def _setprice(a):
        backup(a.iso)
        with Iso(a.iso, writable=True) as g: write_price(g, a.index, a.field, a.value)
        print(f"price #{a.index} {a.field}={a.value}"); return 0
    add("set-price", _setprice, [(("--index",), dict(type=int, required=True)),
        (("--field",), dict(required=True, choices=["buy", "sell"])), (("--value",), dict(type=int, required=True))])
    add("peek", _peek, [(("--off",), dict(required=True)), (("--len",), dict(type=int, default=16))])
    add("poke", _poke, [(("--off",), dict(required=True)), (("--u8",), dict(type=int)),
                        (("--u16",), dict(type=int)), (("--hex",), dict())])
    a = p.parse_args(argv); return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
