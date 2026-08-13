#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) ISO engine + CLI.

Reverse-engineered from Suikoden5EditorV10.exe (Tony H) via monodis and validated
against a real ISO. See s5fields.py for what is VERIFIED vs research.

Commands:
  verify     <iso>                      check the serial
  names      <iso> [--limit N]          dump the 0x691600 character-name table
  find-name  <iso> --name Lyon          find ASCII occurrences of a name in the ISO
  set-name   <iso> --index I --name X   rename entry I in the name table (<=7 chars)
  ladder     <iso>                      dump the 0x4986C0 numeric ladder
  peek       <iso> --off 0x.. --len N   raw hex read at an absolute offset
  poke       <iso> --off 0x.. --u8/--u16/--hex ..   raw write (makes a .bak)
"""
import argparse, os, sys, shutil
import s5fields as F

HERE = os.path.dirname(os.path.abspath(__file__))


class Iso:
    def __init__(self, path, writable=False):
        self.path = path
        self.f = open(path, "r+b" if writable else "rb")
        self.writable = writable
    def close(self):
        try: self.f.close()
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def rd(self, off, n): self.f.seek(off); return self.f.read(n)
    def wr(self, off, b):
        if not self.writable: raise IOError("ISO opened read-only")
        self.f.seek(off); self.f.write(b)
    def ru(self, off, w): return int.from_bytes(self.rd(off, w), "little")
    def wu(self, off, w, v):
        if not (0 <= v < (1 << 8*w)): raise ValueError(f"{v} out of range for {w}B")
        self.wr(off, int(v).to_bytes(w, "little"))


def is_valid(iso):
    return iso.rd(F.SERIAL_OFF, len(F.SERIAL_STR)) == F.SERIAL_STR


def read_names(iso, limit=0):
    """Enumerate the 8-byte name table until a run of empty/non-ASCII slots."""
    out = []; empty = 0; i = 0
    while True:
        rec = iso.rd(F.NAME_TABLE_BASE + i * F.NAME_ENTRY_SIZE, F.NAME_ENTRY_SIZE)
        nm = rec.split(b"\x00")[0]
        ok = 1 <= len(nm) <= F.NAME_MAX_CHARS and all(32 <= c < 127 for c in nm)
        if ok:
            out.append({"index": i, "off": F.NAME_TABLE_BASE + i*F.NAME_ENTRY_SIZE,
                        "name": nm.decode("ascii")}); empty = 0
        else:
            empty += 1
            if empty >= 3 and out: break
        i += 1
        if limit and len(out) >= limit: break
        if i > 4000: break
    return out


def find_name(iso, name, maxhits=8):
    """Search the whole ISO for ASCII `name`; return byte offsets."""
    needle = name.encode("ascii")
    hits = []; CH = 8 << 20; ov = len(needle); pos = 0
    sz = os.path.getsize(iso.path)
    while pos < sz and len(hits) < maxhits:
        buf = iso.rd(pos, CH)
        if not buf: break
        j = buf.find(needle)
        while j >= 0 and len(hits) < maxhits:
            hits.append(pos + j); j = buf.find(needle, j + 1)
        pos += CH - ov
    return hits


def set_name(iso, index, name):
    if len(name) > F.NAME_MAX_CHARS:
        raise ValueError(f"name too long (max {F.NAME_MAX_CHARS} chars)")
    slot = name.encode("ascii").ljust(F.NAME_ENTRY_SIZE, b"\x00")
    iso.wr(F.NAME_TABLE_BASE + index * F.NAME_ENTRY_SIZE, slot)


def backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak): shutil.copy2(path, bak)
    return bak


# --------------------------------------------------------------------------- CLI
def _verify(a):
    with Iso(a.iso) as iso:
        ok = is_valid(iso); print("VALID SLUS-21291" if ok else "NOT a recognized S5 (USA) ISO")
        return 0 if ok else 2

def _names(a):
    with Iso(a.iso) as iso:
        for e in read_names(iso, a.limit):
            print(f"  #{e['index']:>3} 0x{e['off']:X}  {e['name']}")
    return 0

def _find(a):
    with Iso(a.iso) as iso:
        hits = find_name(iso, a.name)
        print(f"{a.name}: " + (", ".join(hex(h) for h in hits) if hits else "not found"))
    return 0

def _setname(a):
    backup(a.iso)
    with Iso(a.iso, writable=True) as iso:
        set_name(iso, a.index, a.name)
    print(f"renamed entry #{a.index} -> {a.name!r}")
    return 0

def _ladder(a):
    with Iso(a.iso) as iso:
        vals = [iso.ru(F.LADDER_OFF + i*2, 2) for i in range(F.LADDER_COUNT)]
        print("extra @0x%X:" % F.LADDER_EXTRA_OFF, iso.ru(F.LADDER_EXTRA_OFF, 2))
        for g in range(0, F.LADDER_COUNT, 10):
            print(" ", vals[g:g+10])
    return 0

def _peek(a):
    with Iso(a.iso) as iso:
        b = iso.rd(int(a.off, 0), max(1, min(256, a.len)))
    print(b.hex(" "))
    print("".join(chr(c) if 32 <= c < 127 else "." for c in b))
    return 0

def _poke(a):
    backup(a.iso)
    with Iso(a.iso, writable=True) as iso:
        off = int(a.off, 0)
        if a.hex is not None:
            iso.wr(off, bytes.fromhex(a.hex.replace(" ", "")))
        elif a.u16 is not None:
            iso.wu(off, 2, a.u16)
        elif a.u8 is not None:
            iso.wu(off, 1, a.u8)
        else:
            print("provide --u8/--u16/--hex", file=sys.stderr); return 2
    print(f"wrote to 0x{off:X}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Suikoden V ISO engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    def add(name, fn, args=()):
        sp = sub.add_parser(name); sp.add_argument("iso")
        for aa in args: sp.add_argument(*aa[0], **aa[1])
        sp.set_defaults(fn=fn); return sp
    add("verify", _verify)
    add("names", _names, [(("--limit",), dict(type=int, default=0))])
    add("find-name", _find, [(("--name",), dict(required=True))])
    add("set-name", _setname, [(("--index",), dict(type=int, required=True)),
                               (("--name",), dict(required=True))])
    add("ladder", _ladder)
    add("peek", _peek, [(("--off",), dict(required=True)), (("--len",), dict(type=int, default=16))])
    add("poke", _poke, [(("--off",), dict(required=True)), (("--u8",), dict(type=int)),
                        (("--u16",), dict(type=int)), (("--hex",), dict())])
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
