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


# ---- Shareable "mod recipe": every field write is auto-journaled at the Iso layer
# into <iso>.s5mod.json (a byte-level diff with old+new, so it's reversible + region-
# checkable). RECORD_MODS toggles it; _SUPPRESS_MOD is set during bulk/overlay writes
# and recipe-apply so those don't pollute the recipe.
RECORD_MODS = True
_SUPPRESS_MOD = False

class Iso:
    def __init__(self, path, writable=False):
        self.path = path; self.f = open(path, "r+b" if writable else "rb")
        self.writable = writable; self._writes = []
    def close(self):
        if self.writable and RECORD_MODS and not _SUPPRESS_MOD and self._writes:
            try: _flush_mods(self.path, self._writes)
            except Exception: pass
        self._writes = []
        try: self.f.close()
        except Exception: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()
    def rd(self, off, n): self.f.seek(off); return self.f.read(n)
    def wr(self, off, b):
        if not self.writable: raise IOError("read-only")
        if RECORD_MODS and not _SUPPRESS_MOD:
            old = self.rd(off, len(b))
            self._writes.append((off, old, bytes(b)))
        self.f.seek(off); self.f.write(b)
    def ru(self, off, w): return int.from_bytes(self.rd(off, w), "little")
    def wu(self, off, w, v):
        if not (0 <= v < (1 << 8*w)): raise ValueError(f"{v} out of range for {w}B")
        self.wr(off, int(v).to_bytes(w, "little"))


def region_of(iso):
    """Return 'ntsc-u' / 'pal' from the serial @0x828BD, or None if unrecognized."""
    s = iso.rd(F.SERIAL_OFF, 11)
    for r, ser in F.SERIALS.items():
        if s == ser: return r
    return None

def is_valid(iso): return region_of(iso) is not None

def set_region_for(path):
    """Detect the ISO's region and rebind the field bases; returns the region (or None)."""
    with Iso(path) as g: r = region_of(g)
    if r: F.set_region(r)
    return r


# ---- Mod recipe (.s5mod) + xdelta patch export/apply --------------------------
def _mod_sidecar(path): return path + ".s5mod.json"

def _flush_mods(path, writes):
    import json
    side = _mod_sidecar(path)
    try:
        with open(side) as fp: data = json.load(fp)
    except Exception:
        data = {"format": "s5mod", "version": 1, "bytes": {}}
    bm = data.setdefault("bytes", {})
    for off, old, new in writes:
        for i in range(len(new)):
            k = str(off + i)
            if k not in bm: bm[k] = [old[i], new[i]]
            else: bm[k][1] = new[i]
    tmp = side + ".tmp"
    with open(tmp, "w") as fp: json.dump(data, fp)
    os.replace(tmp, side)

def mod_status(path):
    """Byte + coalesced-run count of the recipe accumulated for this ISO."""
    import json
    try:
        with open(_mod_sidecar(path)) as fp: bm = json.load(fp).get("bytes", {})
    except Exception:
        return {"bytes": 0, "runs": 0}
    offs = sorted(int(k) for k in bm)
    runs = 0; prev = None
    for o in offs:
        if prev is None or o != prev + 1: runs += 1
        prev = o
    return {"bytes": len(offs), "runs": runs}

def export_mod(path, note=""):
    """Coalesce the accumulated byte journal into a portable .s5mod dict."""
    import json
    with open(_mod_sidecar(path)) as fp: bm = json.load(fp).get("bytes", {})
    if not bm: raise ValueError("no edits recorded for this ISO yet")
    items = sorted((int(k), v) for k, v in bm.items())
    runs = []
    for off, (old, new) in items:
        if runs and off == runs[-1]["_end"]:
            r = runs[-1]; r["old"] += "%02x" % old; r["new"] += "%02x" % new; r["_end"] += 1
        else:
            runs.append({"off": off, "old": "%02x" % old, "new": "%02x" % new, "_end": off + 1})
    for r in runs: r.pop("_end")
    with Iso(path) as g:
        serial = g.rd(F.SERIAL_OFF, len(F.SERIAL_STR)).decode("latin1", "replace")
    return {"format": "s5mod", "version": 1, "serial": serial, "note": note,
            "patchCount": len(runs), "patches": runs}

def apply_mod(path, mod, make_backup=True):
    """Replay a .s5mod recipe onto a target ISO (serial-checked, old-byte-warned)."""
    global _SUPPRESS_MOD
    if mod.get("format") != "s5mod": raise ValueError("not an s5mod recipe")
    with Iso(path) as g:
        cur_serial = g.rd(F.SERIAL_OFF, len(F.SERIAL_STR)).decode("latin1", "replace")
    want = (mod.get("serial") or "").replace("\x00", "").strip()
    if want and want != cur_serial.replace("\x00", "").strip():
        raise ValueError(f"ISO serial {cur_serial.strip()!r} != recipe serial {want!r}")
    if make_backup: backup(path)
    applied = mism = 0
    _SUPPRESS_MOD = True
    try:
        with Iso(path, writable=True) as g:
            for p in mod.get("patches", []):
                new = bytes.fromhex(p["new"]); off = int(p["off"])
                if p.get("old") and g.rd(off, len(new)) != bytes.fromhex(p["old"]): mism += 1
                g.wr(off, new); applied += len(new)
    finally:
        _SUPPRESS_MOD = False
    return {"appliedBytes": applied, "mismatchedRuns": mism, "patchCount": len(mod.get("patches", []))}

def clear_mod(path):
    try: os.remove(_mod_sidecar(path)); return True
    except FileNotFoundError: return False

def xdelta_available():
    import shutil
    return shutil.which("xdelta3") is not None

def make_xdelta(pristine, edited, out):
    import subprocess
    r = subprocess.run(["xdelta3", "-e", "-f", "-s", pristine, edited, out],
                       capture_output=True, text=True)
    if r.returncode: raise RuntimeError(r.stderr.strip() or "xdelta3 encode failed")
    return os.path.getsize(out)

def apply_xdelta(pristine, patch, out):
    import subprocess
    r = subprocess.run(["xdelta3", "-d", "-f", "-s", pristine, patch, out],
                       capture_output=True, text=True)
    if r.returncode: raise RuntimeError(r.stderr.strip() or "xdelta3 decode failed")
    return os.path.getsize(out)


# ---- ISO9660 directory + compressed-overlay (.ROM) tools ---------------------
# The disc's OVL/ folder holds LZSS-compressed engine overlays (BATLE.ROM, WAR.ROM,
# FISHING.ROM, ...). Container: byte-reversed ASCII tags. "\x00mor"=rom hdr
# (u32 tag, u32 hdrSize), then "\x00szl"=lzs chunk (u32 decSize, u32 compSize),
# payload at fileStart+0x40. Compression is classic LZSS (4096-byte window, zero-init,
# r=N-18, flag bit 1=literal else [lo | (hi&0xF0)<<4 offset, (hi&0x0F)+3 length]).
_SECT = 2048
def _iso_listdir(f, lba, size):
    f.seek(lba * _SECT); d = f.read(size); out = []; i = 0
    while i < len(d):
        ln = d[i]
        if ln == 0:
            i = (i // _SECT + 1) * _SECT
            if i >= len(d): break
            continue
        rec = d[i:i + ln]
        flba = int.from_bytes(rec[2:6], "little"); fsz = int.from_bytes(rec[10:14], "little")
        nl = rec[32]; name = rec[33:33 + nl].decode("latin1"); flags = rec[25]
        out.append({"name": name.split(";")[0], "lba": flba, "size": fsz, "dir": bool(flags & 2)})
        i += ln
    return out

def iso_root(iso_path):
    f = open(iso_path, "rb")
    f.seek(16 * _SECT); pvd = f.read(_SECT)
    root = pvd[156:156 + 34]
    return f, int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little")

def list_overlays(iso_path):
    """List OVL/*.ROM engine overlays with compressed + decompressed sizes."""
    f, rlba, rsz = iso_root(iso_path)
    for e in _iso_listdir(f, rlba, rsz):
        if e["name"] == "OVL" and e["dir"]:
            out = []
            for o in _iso_listdir(f, e["lba"], e["size"]):
                if not o["name"].endswith(".ROM"): continue
                f.seek(o["lba"] * _SECT); head = f.read(0x14)
                dec = None
                if head[0:4] == b"\x00mor" and head[8:12] == b"\x00szl":
                    dec = int.from_bytes(head[0x0C:0x10], "little")
                o.update({"decSize": dec})
                out.append(o)
            f.close(); return out
    f.close(); return []

def _lzss_decompress(data, decsize, N=4096, F_=18):
    out = bytearray(); ring = bytearray(N); r = N - F_
    i = 0; flags = 0; fcnt = 0; n = len(data)
    while len(out) < decsize and i < n:
        if fcnt == 0: flags = data[i]; i += 1; fcnt = 8
        bit = flags & 1; flags >>= 1; fcnt -= 1
        if bit:
            b = data[i]; i += 1; out.append(b); ring[r] = b; r = (r + 1) % N
        else:
            if i + 1 >= n: break
            lo = data[i]; hi = data[i + 1]; i += 2
            off = lo | ((hi & 0xF0) << 4); ln = (hi & 0x0F) + 3
            for k in range(ln):
                b = ring[(off + k) % N]; out.append(b); ring[r] = b; r = (r + 1) % N
    return bytes(out)

def _lzss_compress(data):
    """LZSS encoder matching _lzss_decompress (ring N=4096, r0=N-18, len 3..18).
    The stored offset is an ABSOLUTE ring position: output byte j lives at
    ring[(N-18+j) % N], so a match on source index j encodes off=(N-18+j)%N.
    Greedy with a hash chain; dictionary limited to N-F so offsets stay valid."""
    N, F_, MINM = 4096, 18, 3
    n = len(data); out = bytearray()
    heads = {}; chain = [-1] * n
    def key(p): return (data[p] << 16) | (data[p + 1] << 8) | data[p + 2]
    def insert(p):
        if p + MINM <= n:
            k = key(p); chain[p] = heads.get(k, -1); heads[k] = p
    flag = 0; nbits = 0; tokens = bytearray(); i = 0
    while i < n:
        best_len = 0; best_j = 0
        if i + MINM <= n:
            lo = i - (N - F_)
            if lo < 0: lo = 0
            j = heads.get(key(i), -1); tries = 0; maxlen = min(F_, n - i)
            while j >= lo and tries < 256:
                dist = i - j; l = 0
                while l < maxlen and data[i + l] == data[i - dist + l]:
                    l += 1
                if l > best_len:
                    best_len = l; best_j = j
                    if l == maxlen: break
                j = chain[j]; tries += 1
        if best_len >= MINM:
            off = (N - F_ + best_j) % N
            tokens.append(off & 0xFF)
            tokens.append(((off >> 4) & 0xF0) | ((best_len - MINM) & 0x0F))
            nbits += 1
            end = i + best_len
            while i < end: insert(i); i += 1
        else:
            tokens.append(data[i]); flag |= (1 << nbits); nbits += 1
            insert(i); i += 1
        if nbits == 8:
            out.append(flag); out += tokens; flag = 0; nbits = 0; tokens = bytearray()
    if nbits: out.append(flag); out += tokens
    return bytes(out)


def extract_overlay(iso_path, name, out_dir):
    """Extract + decompress one OVL/<name> to out_dir/<name>.bin. Returns the path + sizes."""
    ov = next((o for o in list_overlays(iso_path) if o["name"] == name), None)
    if not ov: raise KeyError(f"no overlay {name!r}")
    f = open(iso_path, "rb"); f.seek(ov["lba"] * _SECT); raw = f.read(ov["size"]); f.close()
    if raw[0:4] == b"\x00mor" and raw[8:12] == b"\x00szl":
        dec = int.from_bytes(raw[0x0C:0x10], "little")
        data = _lzss_decompress(raw[0x40:], dec)
        kind = "lzss"
    else:
        data = raw; kind = "raw"
    os.makedirs(out_dir, exist_ok=True)
    outp = os.path.join(out_dir, name.replace(".ROM", "") + ".bin")
    open(outp, "wb").write(data)
    return {"name": name, "path": os.path.abspath(outp), "kind": kind,
            "compSize": ov["size"], "decSize": len(data)}

def _str_slot(d, off):
    """Return the writable byte slot at off = printable run + following NULs."""
    n = len(d); j = off
    while j < n and 0x20 <= d[j] < 0x7F: j += 1
    k = j
    while k < n and d[k] == 0: k += 1
    return j, k - off   # run_end, cap (run + trailing nulls)

def overlay_strings(bin_path, minlen=4, limit=5000):
    """List editable ASCII strings in a decompressed overlay .bin (in-place slots)."""
    d = open(bin_path, "rb").read(); out = []; i = 0; n = len(d)
    while i < n and len(out) < limit:
        if 0x20 <= d[i] < 0x7F:
            run_end, cap = _str_slot(d, i)
            if run_end - i >= minlen and run_end < n and d[run_end] == 0:
                out.append({"off": i, "text": d[i:run_end].decode("latin1"), "cap": cap})
                i += cap; continue
            i = run_end
        else:
            i += 1
    return out

def write_overlay_string(bin_path, off, text):
    """Byte-capped, in-place edit of one string in the extracted .bin (keeps length)."""
    d = bytearray(open(bin_path, "rb").read())
    _, cap = _str_slot(d, off)
    enc = text.encode("latin1", "replace")
    if len(enc) + 1 > cap:
        raise ValueError(f"too long: {len(enc)+1} bytes but slot is only {cap}")
    d[off:off + cap] = enc + b"\x00" * (cap - len(enc))
    open(bin_path, "wb").write(d)
    return {"ok": True, "cap": cap, "wrote": len(enc)}


def _find_ovl_dirrec(iso_path, name):
    """Return (abs_offset_of_record, record_len) for OVL/<name> in the ISO directory."""
    f, rlba, rsz = iso_root(iso_path)
    ovl = next((e for e in _iso_listdir(f, rlba, rsz) if e["name"] == "OVL" and e["dir"]), None)
    if not ovl: f.close(); raise KeyError("OVL dir not found")
    f.seek(ovl["lba"] * _SECT); d = f.read(ovl["size"]); f.close()
    i = 0
    while i < len(d):
        ln = d[i]
        if ln == 0:
            i = (i // _SECT + 1) * _SECT
            if i >= len(d): break
            continue
        nl = d[i + 32]; nm = d[i + 33:i + 33 + nl].decode("latin1").split(";")[0]
        if nm == name:
            return ovl["lba"] * _SECT + i, ln
        i += ln
    raise KeyError(f"{name} not in OVL dir")

# ---- DATA.PAK (CRI CVM / ROFS) asset filesystem -----------------------------
# DATA.PAK is a CRI ROFS volume: a CVMH header then an embedded ISO9660 filesystem
# holding ~7000 internal files (mostly the same "\x00mor" LZSS/`.ROM` containers as
# OVL/). Portraits live here as FACE_*.ROM. Chunk codecs seen: non (stored), szl
# (LZSS, decodable), epb ("bpe", not yet decoded), ffh. LBAs inside are relative to the
# embedded volume start (embedded-PVD offset - 16 sectors).
def _rofs_volume(iso_path):
    """Return (open file, vol_start_byte_offset, root_lba, root_size) for DATA.PAK's
    embedded ISO9660 volume."""
    f, rlba, rsz = iso_root(iso_path)
    pak = next((e for e in _iso_listdir(f, rlba, rsz) if e["name"] == "DATA.PAK"), None)
    if not pak: f.close(); raise KeyError("DATA.PAK not found")
    pak_base = pak["lba"] * _SECT
    # find the embedded ISO9660 PVD within the first 64 MB of DATA.PAK
    f.seek(pak_base); win = f.read(64 * 1024 * 1024)
    p = win.find(b"\x01CD001\x01")
    if p < 0: f.close(); raise ValueError("no embedded ISO9660 volume in DATA.PAK")
    pvd_off = pak_base + p
    vol_start = pvd_off - 16 * _SECT
    f.seek(pvd_off); pvd = f.read(_SECT); root = pvd[156:156 + 34]
    return f, vol_start, int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little")

def _rofs_listdir(f, vol_start, lba, size):
    f.seek(vol_start + lba * _SECT); d = f.read(size); out = []; i = 0
    while i < len(d):
        ln = d[i]
        if ln == 0:
            i = (i // _SECT + 1) * _SECT
            if i >= len(d): break
            continue
        rec = d[i:i + ln]
        flba = int.from_bytes(rec[2:6], "little"); fsz = int.from_bytes(rec[10:14], "little")
        nl = rec[32]; nm = rec[33:33 + nl].decode("latin1"); flags = rec[25]
        base = nm.split(";")[0]
        if base not in ("\x00", "\x01"):
            out.append({"name": base, "lba": flba, "size": fsz, "dir": bool(flags & 2)})
        i += ln
    return out

def _rofs_codec(f, vol_start, lba):
    f.seek(vol_start + lba * _SECT); h = f.read(12)
    tag = h[8:12]
    return {b"\x00non": "non", b"\x00szl": "szl", b"\x00epb": "bpe",
            b"\x00ffh": "ffh", b"\x00mor": "mor"}.get(tag, tag.hex())

def datapak_list(iso_path, filt="", limit=9000):
    """Walk DATA.PAK's ROFS and return internal files [{path,name,size,codec,lba}].
    Optional case-insensitive substring filter on the path."""
    f, vol_start, rlba, rsz = _rofs_volume(iso_path)
    out = []; fl = (filt or "").lower()
    def walk(lba, size, prefix):
        for e in _rofs_listdir(f, vol_start, lba, size):
            path = prefix + "/" + e["name"]
            if e["dir"]:
                if len(out) < limit: walk(e["lba"], e["size"], path)
            else:
                if len(out) >= limit: return
                if fl and fl not in path.lower(): continue
                out.append({"path": path, "name": e["name"], "size": e["size"],
                            "lba": e["lba"], "codec": _rofs_codec(f, vol_start, e["lba"])})
    walk(rlba, rsz, ""); f.close()
    out.sort(key=lambda x: x["path"])
    return out

def _bpe_decompress(data, dec_size):
    """Konami 'epb' codec — a Byte-Pair-Encoding (Gage-family) variant. Reverse-engineered
    from the PS2 EE decompressor at ELF vaddr 0x220690 and validated byte-exact against a
    full instruction emulation of it. Per block: identity-init left[256]; read a count-byte
    pair table (count>127 => skip count-127 identity codes then 1 pair; else count+1 pairs;
    a code with left[c]==c has no right byte); read a big-endian 16-bit symbol count; expand
    each symbol via a stack (terminal when left[s]==s, else push right[s] then left[s])."""
    out = bytearray(); pos = 0; n = len(data)
    def g():
        nonlocal pos
        b = data[pos] if pos < n else 0; pos += 1; return b
    while len(out) < dec_size and pos < n:
        left = list(range(256)); right = [0] * 256; c = 0
        while c < 256:
            cnt = g()
            if cnt > 127:
                c += cnt - 127; cnt = 0
            if c >= 256: break
            i = 0
            while i <= cnt and c < 256:
                left[c] = g()
                if left[c] != c: right[c] = g()
                i += 1; c += 1
        size = (g() << 8) | g()
        for _ in range(size):
            st = [g()]
            while st:
                s = st.pop()
                if left[s] == s: out.append(s)
                else: st.append(right[s]); st.append(left[s])
    return bytes(out[:dec_size])


def _datapak_read(iso_path, name):
    """Find an internal DATA.PAK file by exact then loose name; return (name, decoded_bytes,
    codec). non/szl/bpe are decoded; other codecs return the raw container."""
    f, vol_start, rlba, rsz = _rofs_volume(iso_path)
    hit = None
    def find_exact(lba, size):
        nonlocal hit
        for e in _rofs_listdir(f, vol_start, lba, size):
            if hit: return
            if e["dir"]: find_exact(e["lba"], e["size"])
            elif e["name"] == name: hit = e
    def find_loose(lba, size):
        nonlocal hit
        for e in _rofs_listdir(f, vol_start, lba, size):
            if hit: return
            if e["dir"]: find_loose(e["lba"], e["size"])
            elif name in e["name"]: hit = e
    find_exact(rlba, rsz)
    if not hit: find_loose(rlba, rsz)
    if not hit: f.close(); raise KeyError(f"{name} not found in DATA.PAK")
    f.seek(vol_start + hit["lba"] * _SECT); blob = f.read(hit["size"]); f.close()
    codec = {b"\x00non": "non", b"\x00szl": "szl", b"\x00epb": "bpe",
             b"\x00ffh": "ffh"}.get(blob[8:12], blob[8:12].hex())
    decSize = int.from_bytes(blob[0x0C:0x10], "little"); compSize = int.from_bytes(blob[0x10:0x14], "little")
    if codec == "non": data = blob[0x40:0x40 + decSize] if decSize else blob[0x40:]
    elif codec == "szl": data = _lzss_decompress(blob[0x40:0x40 + compSize], decSize)
    elif codec == "bpe": data = _bpe_decompress(blob[0x40:0x40 + compSize], decSize)
    else: data = blob
    return hit["name"], data, codec


# ---- PS2 texture (dxt/txd container) -> PNG portrait decode -------------------
def _png(width, height, rgba):
    """Minimal RGBA PNG encoder (stdlib zlib)."""
    import zlib, struct
    raw = bytearray()
    for y in range(height):
        raw.append(0); raw += rgba[y * width * 4:(y + 1) * width * 4]
    def chunk(tag, body):
        c = tag + body
        return struct.pack(">I", len(body)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))

def _unswizzle_clut256(c):
    """PS2 256-entry CLUT (CSM1) de-swizzle: within each 32-color block, swap the two
    middle 8-color groups. c is 1024 bytes (256 RGBA)."""
    out = bytearray(1024)
    for i in range(0, 256, 32):
        for j in range(8):
            for k in range(4):
                out[(i + j) * 4 + k] = c[(i + j) * 4 + k]
                out[(i + 8 + j) * 4 + k] = c[(i + 16 + j) * 4 + k]
                out[(i + 16 + j) * 4 + k] = c[(i + 8 + j) * 4 + k]
                out[(i + 24 + j) * 4 + k] = c[(i + 24 + j) * 4 + k]
    return out

# Portrait texture geometry (verified vs BTL_FACE): each face = 128x32 8-bit indices
# (4096 B) + a 256-color RGBA CLUT (1024 B), rendered at 2x vertical -> 128x64.
_FACE_W, _FACE_H, _FACE_IDX, _FACE_CLUT = 128, 32, 4096, 1024

def render_portraits(iso_path, name):
    """Decode a FACE/BTL_FACE dxt texture file to a list of PNG portraits (RGBA, 128x64).
    Raises ValueError if the file isn't a portrait/dxt texture container."""
    _, data, codec = _datapak_read(iso_path, name)
    if data[:4] != b"\x00dxt":
        raise ValueError(f"{name} is not a portrait/dxt texture (tag {data[:4].hex()}, codec {codec})")
    marks = []; i = 0x40
    while True:
        j = data.find(b"\xff\xff\x03\x18", i)
        if j < 0: break
        marks.append(j); i = j + 4
    out = []
    for k in range(len(marks) - 1):
        if not (5100 <= marks[k + 1] - marks[k] <= 5200):
            continue
        s = data[marks[k] + 20:marks[k + 1]]
        if len(s) < _FACE_IDX + _FACE_CLUT:
            continue
        idx = s[:_FACE_IDX]; clut = _unswizzle_clut256(s[_FACE_IDX:_FACE_IDX + _FACE_CLUT])
        rgba = bytearray(_FACE_W * (_FACE_H * 2) * 4)
        for y in range(_FACE_H):
            for x in range(_FACE_W):
                ci = idx[y * _FACE_W + x]
                r, g, b, a = clut[ci*4], clut[ci*4+1], clut[ci*4+2], clut[ci*4+3]
                a = min(255, a * 2)   # PS2 alpha: 0x80 = opaque
                for dy in range(2):   # 2x vertical to correct native squish
                    o = ((y * 2 + dy) * _FACE_W + x) * 4
                    rgba[o], rgba[o+1], rgba[o+2], rgba[o+3] = r, g, b, a
        out.append(_png(_FACE_W, _FACE_H * 2, rgba))
    return out


def datapak_extract(iso_path, name, out_dir):
    """Extract one internal DATA.PAK file by name (or path). `non`/stored and `szl`/LZSS
    payloads are decoded; other codecs (bpe/ffh) are written as the raw container blob.
    Returns {name, codec, size, decoded, out}."""
    f, vol_start, rlba, rsz = _rofs_volume(iso_path)
    hit = None
    def walk(lba, size):
        nonlocal hit
        for e in _rofs_listdir(f, vol_start, lba, size):
            if hit: return
            if e["dir"]: walk(e["lba"], e["size"])
            elif e["name"] == name or e["name"].split(".")[0] == name.split(".")[0] and name in e["name"]:
                hit = e
    # exact-name pass first, then loose
    def find_exact(lba, size):
        nonlocal hit
        for e in _rofs_listdir(f, vol_start, lba, size):
            if hit: return
            if e["dir"]: find_exact(e["lba"], e["size"])
            elif e["name"] == name: hit = e
    find_exact(rlba, rsz)
    if not hit: walk(rlba, rsz)
    if not hit: f.close(); raise KeyError(f"{name} not found in DATA.PAK")
    f.seek(vol_start + hit["lba"] * _SECT); blob = f.read(hit["size"]); f.close()
    codec = {b"\x00non": "non", b"\x00szl": "szl", b"\x00epb": "bpe",
             b"\x00ffh": "ffh"}.get(blob[8:12], blob[8:12].hex())
    decSize = int.from_bytes(blob[0x0C:0x10], "little")
    compSize = int.from_bytes(blob[0x10:0x14], "little")
    decoded = True
    if codec == "non":
        data = blob[0x40:0x40 + decSize] if decSize else blob[0x40:]
    elif codec == "szl":
        data = _lzss_decompress(blob[0x40:0x40 + compSize], decSize)
    elif codec == "bpe":
        data = _bpe_decompress(blob[0x40:0x40 + compSize], decSize)
    else:
        data = blob; decoded = False   # ffh: dump the raw container (codec not decoded yet)
    os.makedirs(out_dir, exist_ok=True)
    ext = ".bin" if decoded else "." + codec + ".rom"
    out = os.path.join(out_dir, hit["name"].split(".")[0] + ext)
    with open(out, "wb") as w: w.write(data)
    return {"name": hit["name"], "codec": codec, "size": len(data), "decoded": decoded, "out": os.path.abspath(out)}


def reinsert_overlay(iso, name, bin_path):
    """Re-compress an edited overlay .bin and write it back into the ISO in place.
    The edited bin MUST be the same length as the original decompressed size (edit
    values, not layout). Fails if the recompressed container exceeds the file's
    sector slot. Updates the ISO9660 directory size (LE @+10 + BE @+14)."""
    ov = next((o for o in list_overlays(iso.path) if o["name"] == name), None)
    if not ov: raise KeyError(f"no overlay {name!r}")
    if ov["decSize"] is None: raise ValueError(f"{name} is not a compressed overlay")
    edited = open(bin_path, "rb").read()
    if len(edited) != ov["decSize"]:
        raise ValueError(f"edited bin is {len(edited)} bytes; must equal original "
                         f"decompressed size {ov['decSize']} (edit values, not size)")
    header = bytearray(iso.rd(ov["lba"] * _SECT, 0x40))   # preserve original header bytes
    comp = _lzss_compress(edited)
    header[0x0C:0x10] = len(edited).to_bytes(4, "little")   # decSize (unchanged)
    header[0x10:0x14] = len(comp).to_bytes(4, "little")     # compSize
    container = bytes(header) + comp
    budget = ((ov["size"] + _SECT - 1) // _SECT) * _SECT     # sector-aligned slot
    if len(container) > budget:
        raise ValueError(f"recompressed {name} is {len(container)} bytes; slot is only "
                         f"{budget}. Edit rejected (would overwrite the next file).")
    base = ov["lba"] * _SECT
    padded = container + b"\x00" * (((len(container) + _SECT - 1) // _SECT) * _SECT - len(container))
    global _SUPPRESS_MOD
    prev = _SUPPRESS_MOD; _SUPPRESS_MOD = True   # overlay is a whole-sector write; keep it out of the .s5mod recipe
    try:
        iso.wr(base, padded)
        # update directory size (both-endian u32) so the loader reads the new length
        rec_off, _ = _find_ovl_dirrec(iso.path, name)
        iso.wr(rec_off + 10, len(container).to_bytes(4, "little"))
        iso.wr(rec_off + 14, len(container).to_bytes(4, "big"))
    finally:
        _SUPPRESS_MOD = prev
    return {"name": name, "decSize": len(edited), "newCompSize": len(comp),
            "container": len(container), "slot": budget, "slack": budget - len(container)}


def table_addr(table, cid):
    base, stride, _ = F.TABLES[table]; return base + cid * stride


def read_table(iso, table, cid):
    base = table_addr(table, cid)
    return [{"label": l, "off": o, "width": w, "kind": k, "value": iso.ru(base + o, w)}
            for (l, o, w, k) in F.TABLES[table][2]]


def read_character(iso, cid):
    # In PAL, the char sub-section tables (starting equipment/items) aren't offset-mapped
    # yet — skip them so we never surface wrong data.
    skip = set(F.GATED_IN_PAL) if F.REGION == "pal" else set()
    return {t: read_table(iso, t, cid) for t in F.TABLES if t not in skip}


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

def read_rune_prices(iso):
    out = []
    for i in range(F.RUNEPRICE_COUNT):
        b = F.RUNEPRICE_BASE + i * F.RUNEPRICE_STRIDE
        out.append({"index": i,
                    "name": F.RUNEPRICE_NAMES[i] if i < len(F.RUNEPRICE_NAMES) else f"Rune {i}",
                    "buy": iso.ru(b, 3), "sell": iso.ru(b + 4, 3)})
    return out

def write_rune_price(iso, index, field, value):
    if not (0 <= index < F.RUNEPRICE_COUNT): raise KeyError(f"no rune {index}")
    off = {"buy": 0, "sell": 4}.get(field)
    if off is None: raise KeyError(field)
    iso.wu(F.RUNEPRICE_BASE + index * F.RUNEPRICE_STRIDE + off, 3, value)

def read_heal_prices(iso):
    out = []
    for i in range(F.HEALPRICE_COUNT):
        b = F.HEALPRICE_BASE + i * F.HEALPRICE_STRIDE
        out.append({"index": i,
                    "name": F.HEALPRICE_NAMES[i] if i < len(F.HEALPRICE_NAMES) else f"Item {i}",
                    "buy": iso.ru(b, 3), "sell": iso.ru(b + 4, 3)})
    return out

def write_heal_price(iso, index, field, value):
    if not (0 <= index < F.HEALPRICE_COUNT): raise KeyError(f"no healing item {index}")
    off = {"buy": 0, "sell": 4}.get(field)
    if off is None: raise KeyError(field)
    iso.wu(F.HEALPRICE_BASE + index * F.HEALPRICE_STRIDE + off, 3, value)


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


# ---- Unite attacks (packed variable-length table; see s5fields UNITE_BASE) ----
def parse_unites(iso):
    """Walk the packed unite table. Returns [{id, off, jpName, desc, count, idsOff, ids}]."""
    p = F.UNITE_BASE
    out = []
    def read_str(p):
        s = p
        while iso.ru(p, 1) != 0: p += 1
        return iso.rd(s, p - s), p
    while len(out) < F.UNITE_COUNT and p < F.UNITE_SCAN_END:
        while iso.ru(p, 1) == 0: p += 1
        start = p
        raw, p = read_str(p)
        try: jp = raw.decode("cp932")
        except Exception: jp = raw.decode("cp932", "replace")
        descs = []
        while True:
            while iso.ru(p, 1) == 0: p += 1
            b = iso.ru(p, 1)
            if 2 <= b <= 6: break
            raw, p = read_str(p)
            try: descs.append(raw.decode("cp932"))
            except Exception: descs.append(raw.decode("cp932", "replace"))
            if len(descs) > 4: raise ValueError(f"unite parse ran away @0x{p:X}")
        cnt = iso.ru(p, 1); p += 1
        ids_off = p
        ids = [iso.ru(p + k, 1) for k in range(cnt)]
        p += cnt
        out.append({"id": len(out), "off": start, "jpName": jp, "desc": " / ".join(descs),
                    "count": cnt, "idsOff": ids_off, "ids": ids})
    return out

def read_unites(iso, char_names=None):
    """Unites with English name/effect + resolved member names."""
    cn = dict(char_names or {})
    cn.update(F.UNITE_EXTRA_CHARS)
    out = []
    for u in parse_unites(iso):
        meta = F.UNITE_NAMES[u["id"]] if u["id"] < len(F.UNITE_NAMES) else {}
        u["name"] = meta.get("name", u["jpName"] or f"Unite {u['id']}")
        u["effect"] = meta.get("effect", "")
        u["members"] = [{"id": i, "name": cn.get(i, f"#{i}")} for i in u["ids"]]
        out.append(u)
    return out

def write_unite_member(iso, uid, slot, char_id):
    """Set one participant slot of a unite (same count — records are packed)."""
    us = parse_unites(iso)
    if not (0 <= uid < len(us)): raise KeyError(f"no unite {uid}")
    u = us[uid]
    if not (0 <= slot < u["count"]): raise KeyError(f"unite {uid} has {u['count']} slots")
    if not (0 <= int(char_id) <= 255): raise ValueError("bad char id")
    iso.wu(u["idsOff"] + slot, 1, int(char_id))
    return True


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


# When False, no .bak copies are made before writes (toggle in the UI / persisted state).
BACKUPS = True

def backup(path):
    if not BACKUPS: return None
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
    with Iso(a.iso) as g: r = region_of(g)
    print("VALID " + F.REGION_NAMES[r] if r else "NOT recognized"); return 0 if r else 2

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
    a = p.parse_args(argv)
    if getattr(a, "iso", None) and os.path.exists(a.iso):
        try: set_region_for(a.iso)  # rebind bases to the ISO's region before any read/write
        except Exception: pass
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
