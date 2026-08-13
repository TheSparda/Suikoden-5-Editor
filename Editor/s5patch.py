#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) ISO patcher / research tool.

Reverse-engineered from Suikoden5EditorV10.exe (by Tony H) via monodis, and validated
against a real "Suikoden V" ISO. Offsets are RAW byte positions into the ISO (the game
data sits in a flat region; no LBA/sector math needed for the character table).

Character table: base 0x498C00, stride 180 bytes. Record i at 0x498C00 + i*180.
The 180-byte record layout lives in s5fields.CHAR_FIELDS (offsets authoritative).

Usage:
  python3 s5patch.py verify    "ISO/Suikoden V - OG.iso"
  python3 s5patch.py dump-char "ISO/..." --index 0
  python3 s5patch.py set-field "ISO/..." --index 0 --off 0x02 --u16 999
"""
import argparse, os, struct, sys, shutil, datetime
import s5fields as F

HERE = os.path.dirname(os.path.abspath(__file__))


class Iso:
    """Thin random-access wrapper over the ISO file."""
    def __init__(self, path, writable=False):
        self.path = path
        self.f = open(path, "r+b" if writable else "rb")
        self.writable = writable

    def close(self):
        try: self.f.close()
        except Exception: pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def rd(self, off, n):
        self.f.seek(off); return self.f.read(n)

    def wr(self, off, data):
        if not self.writable: raise IOError("ISO opened read-only")
        self.f.seek(off); self.f.write(data)

    def ru(self, off, w):
        return int.from_bytes(self.rd(off, w), "little")

    def wu(self, off, w, val):
        maxv = (1 << (8 * w)) - 1
        if not (0 <= val <= maxv):
            raise ValueError(f"value {val} out of range for {w}-byte field")
        self.wr(off, int(val).to_bytes(w, "little"))


def is_valid(iso):
    """True if the ISO's serial region matches SLUS-21291."""
    return iso.rd(F.SERIAL_OFF, len(F.SERIAL_STR)) == F.SERIAL_STR


def char_offset(index):
    return F.CHAR_TABLE_BASE + index * F.CHAR_STRIDE


def read_char(iso, index):
    """Return [{label, off, width, kind, value}] for character `index`."""
    base = char_offset(index)
    out = []
    for label, off, w, kind in F.CHAR_FIELDS:
        out.append({"label": label, "off": off, "width": w, "kind": kind,
                    "value": iso.ru(base + off, w)})
    return out


def write_field(iso, index, off, width, value):
    iso.wu(char_offset(index) + off, width, value)


def backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


# --------------------------------------------------------------------------- CLI
def _cmd_verify(a):
    with Iso(a.iso) as iso:
        ok = is_valid(iso)
        print("VALID SLUS-21291" if ok else "NOT a recognized Suikoden V (USA) ISO")
        return 0 if ok else 2


def _cmd_dump_char(a):
    with Iso(a.iso) as iso:
        if not is_valid(iso):
            print("warning: serial check failed; dumping anyway", file=sys.stderr)
        print(f"Character #{a.index} @ 0x{char_offset(a.index):X}")
        for row in read_char(iso, a.index):
            print(f"  +0x{row['off']:02X} {row['width']}B  {row['label']:<24} = {row['value']}")
    return 0


def _cmd_set_field(a):
    if a.u8 is None and a.u16 is None:
        print("provide --u8 or --u16", file=sys.stderr); return 2
    width, val = (1, a.u8) if a.u8 is not None else (2, a.u16)
    backup(a.iso)
    with Iso(a.iso, writable=True) as iso:
        write_field(iso, a.index, int(a.off, 0), width, val)
    print(f"wrote {val} ({width}B) to char #{a.index} +0x{int(a.off,0):02X}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Suikoden V ISO patcher")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify"); v.add_argument("iso"); v.set_defaults(fn=_cmd_verify)
    d = sub.add_parser("dump-char"); d.add_argument("iso")
    d.add_argument("--index", type=int, default=0); d.set_defaults(fn=_cmd_dump_char)
    s = sub.add_parser("set-field"); s.add_argument("iso")
    s.add_argument("--index", type=int, required=True)
    s.add_argument("--off", required=True, help="offset within record, e.g. 0x02")
    s.add_argument("--u8", type=int); s.add_argument("--u16", type=int)
    s.set_defaults(fn=_cmd_set_field)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
