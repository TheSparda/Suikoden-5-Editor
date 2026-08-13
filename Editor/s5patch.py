#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) ISO engine + CLI.

Reverse-engineered from Suikoden5EditorV10.exe (Tony H) via monodis, validated vs a
real ISO. See s5fields.py / Suikoden5_ISO_offsets.md. Never writes unverified fields.

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
    add("peek", _peek, [(("--off",), dict(required=True)), (("--len",), dict(type=int, default=16))])
    add("poke", _poke, [(("--off",), dict(required=True)), (("--u8",), dict(type=int)),
                        (("--u16",), dict(type=int)), (("--hex",), dict())])
    a = p.parse_args(argv); return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
