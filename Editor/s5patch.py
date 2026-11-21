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
  models   <iso>                         list the field-model table (id -> file, who)
  set-model <iso> --id N (--slot S | --reset) [--one]
                                         point a model id at another model file; by default
                                         the character's other looks follow (--one to skip)
  ladder   <iso>                         dump the 0x4986C0 ladder
  peek     <iso> --off 0x.. --len N
  poke     <iso> --off 0x.. --u8/--u16/--hex ..
  overlays <iso>                         list OVL/*.ROM (packed/unpacked, member types)
  overlay-dump <iso> --name WAR --out F  write one overlay's unpacked image to F
  overlay-set  <iso> --name WAR --image F
                                         repack F as szl and write it back in place
                                         (refused if it outgrows the file's sector slot;
                                         re-extracted and byte-verified after writing)
  overlay-verify <iso>                   unpack/repack/unpack round trip, all overlays
  pak-list <iso> [--filter STR]          list DATA.PAK contents (substring or glob)
  pak-extract <iso> --path P --out F     extract one DATA.PAK file (raw, streaming)
"""
import argparse, collections, os, re, struct, sys, shutil
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
# The disc's OVL/ folder holds compressed engine overlays (BATLE.ROM, WAR.ROM,
# FISHING.ROM, ...; LZSS on stock discs). Container: byte-reversed ASCII tags. "\x00mor"=rom hdr
# (u32 tag, u32 hdrSize), then "\x00szl"=lzs chunk (u32 decSize, u32 compSize),
# payload at fileStart+0x40. Compression is classic LZSS (4096-byte window, zero-init,
# r=N-18, flag bit 1=literal else [lo | (hi&0xF0)<<4 offset, (hi&0x0F)+3 length]).
_SECT = 2048

# ---- Byte-oriented disc access ------------------------------------------------
# The web editor can't hand Python a file object. It holds an async File in the browser
# and DATA.PAK sits ~2 GB into the disc, far past the front slice it keeps in memory. So
# the locating half of every disc walk is a pure function over the bytes of one extent,
# and the browser does the reading. It stays cheap: DATA.PAK's embedded volume descriptor
# is at +0x9800, and its whole directory tree is ~600 KB across 126 extents, so a full
# asset listing costs well under a megabyte of range reads.

def dir_entries(data):
    """Parse one ISO9660 directory extent -> [{name, lba, size, dir}]. The disc's own
    filesystem and DATA.PAK's embedded ROFS volume share this record layout. The '.' and
    '..' records (single 0x00/0x01 name bytes) are dropped."""
    out = []; i = 0
    while i < len(data):
        ln = data[i]
        if ln == 0:                                   # padding to the next sector
            i = (i // _SECT + 1) * _SECT
            if i >= len(data): break
            continue
        rec = data[i:i + ln]
        nl = rec[32]; nm = rec[33:33 + nl].decode("latin1").split(";")[0]
        if nm not in ("\x00", "\x01"):
            out.append({"name": nm, "lba": int.from_bytes(rec[2:6], "little"),
                        "size": int.from_bytes(rec[10:14], "little"),
                        "dir": bool(rec[25] & 2)})
        i += ln
    return out

def rofs_volume_at(window, window_off):
    """Locate DATA.PAK's embedded ISO9660 volume from a window of bytes read at
    `window_off` on the disc -> {vol_start, root_lba, root_size}. LBAs inside the volume
    are relative to vol_start, which is 16 sectors before the embedded PVD."""
    p = window.find(b"\x01CD001\x01")
    if p < 0: raise ValueError("no embedded ISO9660 volume in DATA.PAK")
    root = window[p + 156:p + 156 + 34]
    return {"vol_start": window_off + p - 16 * _SECT,
            "root_lba": int.from_bytes(root[2:6], "little"),
            "root_size": int.from_bytes(root[10:14], "little")}

ROM_CODECS = {b"\x00non": "non", b"\x00szl": "szl", b"\x00epb": "bpe", b"\x00ffh": "ffh",
              b"\x00mor": "mor"}

def rom_codec(head):
    """Codec name from a .ROM container's first 12 bytes."""
    return ROM_CODECS.get(bytes(head[8:12]), bytes(head[8:12]).hex())

def rom_decode(blob):
    """Decode one .ROM container from its raw bytes -> (codec, data, decoded). non/szl/bpe
    are unpacked; any other codec comes back as the raw container with decoded=False."""
    codec = rom_codec(blob)
    dec = int.from_bytes(blob[0x0C:0x10], "little")
    comp = int.from_bytes(blob[0x10:0x14], "little")
    if codec == "non": return codec, blob[0x40:0x40 + dec] if dec else blob[0x40:], True
    if codec == "szl": return codec, _lzss_decompress(blob[0x40:0x40 + comp], dec), True
    if codec == "bpe": return codec, _bpe_decompress(blob[0x40:0x40 + comp], dec), True
    return codec, blob, False

def _iso_listdir(f, lba, size):
    f.seek(lba * _SECT); return dir_entries(f.read(size))

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
    f.seek(vol_start + lba * _SECT); return dir_entries(f.read(size))

def _rofs_codec(f, vol_start, lba):
    f.seek(vol_start + lba * _SECT); return rom_codec(f.read(12))

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


def _bpe_encode_header(Z, Y):
    """Serialize a 256-entry epb pair table (Z/Y lists of ints) into the
    run-coded block header _bpe_decompress parses. Literal runs are skipped
    with >= 0x80 control bytes (each skip is followed by exactly one explicit
    entry, unless the skip completes the table); pair entries are emitted in
    explicit runs (< 0x80 control bytes, at most 128 entries per run),
    absorbing isolated single literals between pairs into the run when that
    is cheaper than a separate skip. Mirrors s5rom._epb_encode_header."""
    out = bytearray()

    def explicit(idx):
        out.append(Z[idx])
        if Z[idx] != idx:
            out.append(Y[idx])

    i = 0
    while i < 256:
        if Z[i] == i:
            run = 0
            while i + run < 256 and Z[i + run] == i + run:
                run += 1
            if i + run == 256:
                rem = run
                base = i
                while rem > 0:
                    if rem <= 128:
                        out.append(0x7F + rem)
                        rem = 0
                    else:
                        out.append(0xFF)  # skip 128, one explicit follows
                        out.append(base + 128)  # explicit literal
                        base += 129
                        rem -= 129
                i = 256
            elif run <= 128:
                out.append(0x7F + run)  # skip the run, one explicit follows
                i += run
                explicit(i)  # the non-literal that ended the run
                i += 1
            else:
                out.append(0xFF)  # skip 128, one explicit follows
                out.append(i + 128)  # explicit literal
                i += 129
        else:
            count = 0
            j = i
            while j < 256 and count < 128:
                if Z[j] != j:
                    count += 1
                    j += 1
                elif (j + 1 < 256 and Z[j + 1] != j + 1
                        and count + 2 <= 128):
                    count += 1  # the gap literal; the pair counts next pass
                    j += 1
                else:
                    break
            if count == 0:
                count = 1
            out.append(count - 1)
            for k in range(i, i + count):
                explicit(k)
            i += count
    return bytes(out)


# Output bytes per epb block; 2500 was measured to give the best size on
# this corpus (smaller blocks see fewer distinct byte values, which leaves
# more of the 256 symbols free to serve as pairs).
_BPE_BLOCK_OUT = 2500
# A pair must occur at least this many times in the current sequence to be
# worth its 2 header bytes (self-pairs need one extra occurrence because
# overlapping occurrences cannot all be replaced).
_BPE_MIN_FREQ = 3


def _bpe_compress(data):
    """Greedy per-block byte-pair encoder inverting _bpe_decompress (and
    mirroring s5rom.epb_compress): decode(encode(x)) == x.

    The payload is split into blocks of at most _BPE_BLOCK_OUT bytes. Per
    block, byte values present in the block must stay literals (a literal
    symbol i is the only way to emit byte i), so pair symbols are allocated
    from the byte values absent from the block. Merges are greedy: count
    adjacent symbol pairs, take the most frequent (ties go to the lowest
    pair), assign it the next free symbol, and replace all its
    non-overlapping occurrences left to right. Counting uses
    collections.Counter; replacement uses bytes split/join, whose
    left-to-right non-overlapping matches are exactly the occurrences a
    pair can claim (a pair of two different bytes can never overlap itself,
    and split/join claims every other occurrence of a self-pair)."""
    from collections import Counter
    out = bytearray()
    for off in range(0, len(data), _BPE_BLOCK_OUT):
        chunk = data[off:off + _BPE_BLOCK_OUT]
        distinct = set(chunk)
        free = sorted(set(range(256)) - distinct)
        Z = list(range(256))
        Y = [0] * 256
        seq = bytes(chunk)
        while free and len(seq) >= 2:
            counts = Counter(zip(seq, seq[1:]))
            a = b = 0
            cnt = -1
            for pair, c in counts.items():
                if c > cnt or (c == cnt and pair < (a, b)):
                    (a, b), cnt = pair, c
            if cnt < _BPE_MIN_FREQ + (1 if a == b else 0):
                break
            s = free.pop(0)
            Z[s] = a
            Y[s] = b
            seq = bytes([s]).join(seq.split(bytes([a, b])))
        out += _bpe_encode_header(Z, Y)
        out += len(seq).to_bytes(2, "big")
        out += seq
    return bytes(out)


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
    codec, data, _ok = rom_decode(blob)
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

# S5 textures live in a `\x00dxt` container (optionally bpe/szl-compressed, or a nested
# `\x00epb` chunk sequence). Inside are `ff ff 03 18`-marked pixel records whose 20-byte
# header self-describes W(u32)/H(u32)/bpp(u32)/stride(u32). 8bpp = W*H indices + a 256-color
# RGBA CLUT (1024 B, LINEAR — verified vs SR_CHR/BTL_FACE); 32bpp = W*H direct RGBA. PS2
# stores alpha as 0x80=opaque, so alpha is doubled. Covers portraits (FACE_*), field sprites
# (SR_CHR*), effect/particle art (*_TEX*), and UI skins (TLK_WIN/GMF*).

def _bpe_chunks(data):
    """Split a nested \\x00epb chunk sequence (FACE_PC/EC/ME payloads) and bpe-decompress
    each chunk. Per chunk: u32@4 = skip to next chunk (next = off+skip+4), 0 on the last
    chunk (so a single-chunk file has skip 0); u32@8 = header size (data at +hdr+8), u32@0xC
    = compressed size; the compressed stream itself starts with a u32 true decompressed size."""
    out = []; off = 0
    while off + 16 <= len(data) and data[off:off+4] == b"\x00epb":
        skip = int.from_bytes(data[off+4:off+8], "little")
        hdr  = int.from_bytes(data[off+8:off+12], "little")
        comp = int.from_bytes(data[off+12:off+16], "little")
        doff = off + hdr + 8
        if comp < 5 or doff + comp > len(data): break
        s = data[doff:doff + comp]
        out.append(_bpe_decompress(s[4:], int.from_bytes(s[:4], "little")))
        if skip == 0: break            # last (or only) chunk
        off += skip + 4                # game's own next-chunk pointer (4-byte aligned)
    return out

def _texture_records(body):
    """Yield (rgba, W, H) at native size for each valid pixel record in a dxt-style body:
    marker `ffff0318`, then u32 W/H/bpp/stride. 8bpp = W*H indices + 1024-byte linear RGBA
    CLUT; 32bpp = W*H direct RGBA. ffff0318 also appears inside data, so records are validated
    by sane dims and a large-enough gap to the next marker."""
    marks = []; i = 0
    while True:
        j = body.find(b"\xff\xff\x03\x18", i)
        if j < 0: break
        marks.append(j); i = j + 4
    marks.append(len(body))
    for k in range(len(marks) - 1):
        m = marks[k]; gap = marks[k+1] - m
        if m + 20 > len(body): continue
        W = int.from_bytes(body[m+4:m+8], "little")
        H = int.from_bytes(body[m+8:m+12], "little")
        bpp = int.from_bytes(body[m+12:m+16], "little")
        if not (8 <= W <= 2048 and 8 <= H <= 2048): continue
        if bpp == 8:
            need = W * H + 1024
            if gap < 20 + need: continue
            s = body[m+20:m+20 + need]; idx = s[:W*H]; clut = s[W*H:]
            rgba = bytearray(W * H * 4)
            for p in range(W * H):
                ci = idx[p] * 4
                rgba[p*4], rgba[p*4+1], rgba[p*4+2] = clut[ci], clut[ci+1], clut[ci+2]
                rgba[p*4+3] = min(255, clut[ci+3] * 2)
            yield bytes(rgba), W, H
        elif bpp == 32:
            need = W * H * 4
            if gap < 20 + need: continue
            s = body[m+20:m+20 + need]
            rgba = bytearray(s)
            for p in range(3, need, 4):
                rgba[p] = min(255, rgba[p] * 2)
            yield bytes(rgba), W, H

def _texture_bodies_data(data, label="file"):
    """The decompressed dxt bodies of an already-decoded texture file (a single dxt body,
    or the nested epb chunk list). Raises ValueError if it isn't a dxt-style texture."""
    if data[:4] == b"\x00dxt": return [data[0x40:]]
    if data[:4] == b"\x00epb": return _bpe_chunks(data)
    raise ValueError(f"{label} is not a dxt texture (tag {data[:4].hex()})")

def _texture_bodies(iso_path, name):
    _, data, codec = _datapak_read(iso_path, name)
    try: return _texture_bodies_data(data, name)
    except ValueError: raise ValueError(f"{name} is not a dxt texture "
                                        f"(tag {data[:4].hex()}, codec {codec})")

def render_textures_data(data, label="file"):
    """Every texture image in an already-decoded dxt/epb file as (png_bytes, W, H) at
    native size — any dimensions, 8bpp (indexed) or 32bpp (direct). Works for sprites
    (SR_CHR*), effect textures (*_TEX*), UI skins (TLK_WIN/GMF*) and portraits alike."""
    out = []
    for body in _texture_bodies_data(data, label):
        for rgba, W, H in _texture_records(body):
            out.append((_png(W, H, rgba), W, H))
    if not out: raise ValueError(f"{label} has no decodable textures")
    return out

def render_textures(iso_path, name):
    out = []
    for body in _texture_bodies(iso_path, name):
        for rgba, W, H in _texture_records(body):
            out.append((_png(W, H, rgba), W, H))
    if not out:
        raise ValueError(f"{name} has no decodable textures")
    return out

def _double_rows(rgba, W, H):
    """Stretch an RGBA buffer 2x vertically (each row twice) -> (rgba, H*2)."""
    out = bytearray(W * H * 2 * 4); row = W * 4
    for y in range(H):
        r = rgba[y*row:(y+1)*row]
        out[(y*2)*row:(y*2+1)*row] = r; out[(y*2+1)*row:(y*2+2)*row] = r
    return bytes(out), H * 2

def decode_faces_data(data, label="file"):
    """Portrait view over already-decoded bytes. Wide-short battle faces (H*2 < W, i.e.
    BTL_FACE 128x32) are stretched 2x vertically; faces of a non-matching size are dropped
    so the gallery/sheet grid stays uniform. Returns (faces, W, H)."""
    faces = []; W = H = None
    for body in _texture_bodies_data(data, label):
        for rgba, w, h in _texture_records(body):
            if h * 2 < w: rgba, h = _double_rows(rgba, w, h)
            if W is None: W, H = w, h
            if (w, h) != (W, H): continue
            faces.append(rgba)
    if not faces: raise ValueError(f"no pixel records found in {label}")
    return faces, W, H

def _decode_faces(iso_path, name):
    """Portrait view: decode a FACE file to same-size RGBA faces. Wide-short battle faces
    (H*2 < W, i.e. BTL_FACE 128x32) are stretched 2x vertically; faces of a non-matching
    size are dropped so the gallery/sheet grid stays uniform. Returns (faces, W, H)."""
    faces = []; W = H = None
    for body in _texture_bodies(iso_path, name):
        for rgba, w, h in _texture_records(body):
            if h * 2 < w: rgba, h = _double_rows(rgba, w, h)
            if W is None: W, H = w, h
            if (w, h) != (W, H): continue
            faces.append(rgba)
    if not faces:
        raise ValueError(f"no pixel records found in {name}")
    return faces, W, H

def render_portraits(iso_path, name):
    """List of PNG portraits (RGBA, 128x64) for a FACE/BTL_FACE dxt texture file."""
    faces, W, H = _decode_faces(iso_path, name)
    return [_png(W, H, f) for f in faces]

def _compose_sheet(faces, W, H, cols=8):
    """Tile same-size RGBA faces into one sprite sheet -> (png_bytes, count)."""
    if not faces: raise ValueError("no portraits to compose")
    cols = max(1, min(cols, len(faces))); rows = (len(faces) + cols - 1) // cols
    SW, SH = cols * W, rows * H
    sheet = bytearray(SW * SH * 4)
    for fi, f in enumerate(faces):
        ox, oy = (fi % cols) * W, (fi // cols) * H
        for y in range(H):
            dst = ((oy + y) * SW + ox) * 4
            sheet[dst:dst + W * 4] = f[y * W * 4:(y + 1) * W * 4]
    return _png(SW, SH, sheet), len(faces)

def render_portrait_sheet_data(data, label="file", cols=8):
    faces, W, H = decode_faces_data(data, label)
    return _compose_sheet(faces, W, H, cols)

def render_portrait_zip_data(data, label, cols=8):
    """Zip every portrait of an already-decoded FACE file. Returns (zip_bytes, count)."""
    import io, zipfile
    faces, W, H = decode_faces_data(data, label)
    base = label.split(".")[0]; mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for i, f in enumerate(faces):
            z.writestr("%s_%02d.png" % (base, i), _png(W, H, f))
    return mem.getvalue(), len(faces)

def render_portrait_sheet(iso_path, name, cols=8):
    """Compose all portraits into a single RGBA sprite-sheet PNG. Returns (png_bytes, count)."""
    faces, W, H = _decode_faces(iso_path, name)
    if not faces: raise ValueError(f"no portraits found in {name}")
    cols = max(1, min(cols, len(faces))); rows = (len(faces) + cols - 1) // cols
    SW, SH = cols * W, rows * H
    sheet = bytearray(SW * SH * 4)
    for fi, f in enumerate(faces):
        ox, oy = (fi % cols) * W, (fi // cols) * H
        for y in range(H):
            dst = ((oy + y) * SW + ox) * 4
            sheet[dst:dst + W * 4] = f[y * W * 4:(y + 1) * W * 4]
    return _png(SW, SH, sheet), len(faces)

def render_portrait_zip(iso_path, name):
    """Zip every portrait of a FACE file as individual PNGs. Returns (zip_bytes, count)."""
    import io, zipfile
    faces, W, H = _decode_faces(iso_path, name)
    base = name.split(".")[0]
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for i, f in enumerate(faces):
            z.writestr("%s_%02d.png" % (base, i), _png(W, H, f))
    return mem.getvalue(), len(faces)

def render_all_portraits_zip(iso_path):
    """Zip EVERY portrait in DATA.PAK — walks all FACE containers, decodes each, and writes
    the faces into per-set folders (<SET>/<SET>_NN.png). Files that hold no portraits (e.g.
    the empty FACE_PC000 stub) are skipped. Returns (zip_bytes, sets, faces)."""
    import io, zipfile
    names = [e["name"] for e in datapak_list(iso_path, "FACE")
             if "FACE" in e["name"] and e["size"] > 64]
    def rank(n): return 0 if n.startswith("BTL") else 1 if n.startswith("FACE_PC") else 2
    names.sort(key=lambda n: (rank(n), n))
    mem = io.BytesIO(); sets = faces = 0
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for nm in names:
            try:
                bufs, W, H = _decode_faces(iso_path, nm)
            except (ValueError, KeyError):
                continue
            base = nm.split(".")[0]; sets += 1
            for i, f in enumerate(bufs):
                z.writestr("%s/%s_%02d.png" % (base, base, i), _png(W, H, f)); faces += 1
    return mem.getvalue(), sets, faces


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
    codec, data, decoded = rom_decode(blob)
    os.makedirs(out_dir, exist_ok=True)
    ext = ".bin" if decoded else "." + codec + ".rom"
    out = os.path.join(out_dir, hit["name"].split(".")[0] + ext)
    with open(out, "wb") as w: w.write(data)
    return {"name": hit["name"], "codec": codec, "size": len(data), "decoded": decoded, "out": os.path.abspath(out)}


def reinsert_overlay(iso, name, bin_path):
    """Re-compress an edited overlay .bin and write it back into the ISO in place.
    The edited bin MUST be the same length as the original decompressed size (edit
    values, not layout). The container is rebuilt in the SAME form the disc
    already uses (szl on stock discs; non or epb on rebuilt ones): the original
    header bytes, including the type word, are preserved and only the stream is
    re-encoded. Fails if the rebuilt container exceeds the file's sector slot.
    Updates the ISO9660 directory size (LE @+10 + BE @+14)."""
    ov = next((o for o in list_overlays(iso.path) if o["name"] == name), None)
    if not ov: raise KeyError(f"no overlay {name!r}")
    header = bytearray(iso.rd(ov["lba"] * _SECT, 0x40))   # preserve original header bytes
    codec = rom_codec(header) if bytes(header[0:4]) == b"\x00mor" else None
    if codec not in ("szl", "non", "bpe"):
        raise ValueError(f"{name} is not a compressed overlay")
    dec_size = int.from_bytes(header[0x0C:0x10], "little")
    edited = open(bin_path, "rb").read()
    if len(edited) != dec_size:
        raise ValueError(f"edited bin is {len(edited)} bytes; must equal original "
                         f"decompressed size {dec_size} (edit values, not size)")
    if codec == "szl":
        comp = _lzss_compress(edited)
    elif codec == "non":
        comp = edited
    else:  # bpe
        comp = _bpe_compress(edited)
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


# ---- .ROM overlays + DATA.PAK (desktop CLI) ----------------------------------
# The codec and the archive reader live in s5rom.py / s5pak.py and are imported
# lazily inside each command: the web editor loads this file into Pyodide with
# only s5save/s5patch/s5fields present, so a top-level import would break it.

def _overlay_entry(iso_path, name):
    """Directory entry for OVL/<name>; the name may omit the .ROM suffix."""
    nm = name.upper()
    if not nm.endswith(".ROM"): nm += ".ROM"
    for o in list_overlays(iso_path):
        if o["name"] == nm: return o
    raise KeyError(f"no overlay {name!r} in OVL/")

def _overlays(a):
    import s5rom
    with Iso(a.iso) as g:
        print(f"{'overlay':<14} {'packed':>9} {'unpacked':>9}  members")
        for o in list_overlays(a.iso):
            raw = g.rd(o["lba"] * _SECT, o["size"])
            try:
                infos = s5rom.member_info(raw)
                types = ",".join(i["type"] for i in infos)
                dec = str(sum(i["usize"] for i in infos))
            except s5rom.UnsupportedMemberError:
                types = "unsupported"; dec = "?"
            print(f"{o['name']:<14} {o['size']:>9} {dec:>9}  {types}")
    return 0

def _overlaydump(a):
    import s5rom
    o = _overlay_entry(a.iso, a.name)
    with Iso(a.iso) as g: raw = g.rd(o["lba"] * _SECT, o["size"])
    image = s5rom.unpack_image(raw)
    with open(a.out, "wb") as w: w.write(image)
    print(f"{o['name']}: {len(raw)} packed -> {len(image)} unpacked -> {a.out}")
    return 0

def _overlayset(a):
    import s5rom
    image = open(a.image, "rb").read()
    container = s5rom.pack_container(image, "szl")
    o = _overlay_entry(a.iso, a.name)
    slot = ((o["size"] + _SECT - 1) // _SECT) * _SECT   # the file's sector allocation
    if len(container) > slot:
        print(f"refused: repacked {o['name']} is {len(container)} bytes but its "
              f"sector allocation is {slot} (directory size {o['size']}). "
              f"Nothing was written.", file=sys.stderr)
        return 2
    backup(a.iso)
    global _SUPPRESS_MOD
    prev = _SUPPRESS_MOD; _SUPPRESS_MOD = True   # whole-sector write: keep it out of the .s5mod recipe, as in reinsert_overlay
    try:
        with Iso(a.iso, writable=True) as g:
            g.wr(o["lba"] * _SECT, container + b"\x00" * (slot - len(container)))
            rec_off, _ = _find_ovl_dirrec(a.iso, o["name"])
            g.wr(rec_off + 10, len(container).to_bytes(4, "little"))
            g.wr(rec_off + 14, len(container).to_bytes(4, "big"))
    finally:
        _SUPPRESS_MOD = prev
    # prove it: re-extract from the ISO and byte-compare with the supplied image
    o2 = _overlay_entry(a.iso, a.name)
    with Iso(a.iso) as g: raw = g.rd(o2["lba"] * _SECT, o2["size"])
    if s5rom.unpack_image(raw) != image:
        print(f"FAIL: {o['name']} re-extracted from the ISO does not match the "
              f"supplied image", file=sys.stderr)
        return 2
    print(f"{o['name']}: wrote {len(container)} bytes (image {len(image)}, slot "
          f"{slot}, slack {slot - len(container)}); re-extract verified byte-identical")
    return 0

def _overlayverify(a):
    import s5rom
    bad = 0; total = 0
    with Iso(a.iso) as g:
        for o in list_overlays(a.iso):
            total += 1
            raw = g.rd(o["lba"] * _SECT, o["size"])
            try:
                img = s5rom.unpack_image(raw)
                again = s5rom.unpack_image(s5rom.pack_container(img, "szl"))
                ok = again == img
                note = f"{len(raw)} packed, {len(img)} unpacked"
            except Exception as e:
                ok = False; note = str(e)
            print(("PASS " if ok else "FAIL ") + o["name"] + "  " + note)
            if not ok: bad += 1
    print(f"{total - bad}/{total} overlays round-trip clean")
    return 0 if not bad else 2

def _pak_match(path, filt):
    """--filter semantics: a glob when the pattern has glob characters, else a
    case-insensitive substring match (the datapak_list convention)."""
    if any(c in filt for c in "*?["):
        import fnmatch
        return fnmatch.fnmatchcase(path.lower(), filt.lower())
    return filt.lower() in path.lower()

def _paklist(a):
    import s5pak
    with s5pak.DataPak(a.iso) as pak:
        entries = pak.entries()
    if a.filter: entries = [e for e in entries if _pak_match(e["path"], a.filter)]
    for e in entries:
        print(f"{e['size']:>12}  LBA {e['extents'][0][0]:>8}  {e['path']}")
    print(f"{len(entries)} files, {sum(e['size'] for e in entries)} bytes",
          file=sys.stderr)
    return 0

def _pakextract(a):
    import s5pak
    with s5pak.DataPak(a.iso) as pak:
        e = pak.extract(a.path, a.out)
    print(f"{e['path']}: {e['size']} bytes -> {a.out}")
    return 0


def table_addr(table, cid):
    base, stride, _ = F.TABLES[table]; return base + cid * stride


def read_table(iso, table, cid):
    base = table_addr(table, cid)
    return [{"label": l, "off": o, "width": w, "kind": k, "value": iso.ru(base + o, w)}
            for (l, o, w, k) in F.TABLES[table][2]]


def weapon_id_for_char(iso, cid):
    """The weapon record id W for a character: byte +0x01 of the character's
    starting-equipment record (issue #22). The builder table covers ids
    0..133; the playable roster is 0..119. W is 0 for non-combat, support
    and body-weapon units (no weapon record)."""
    base, stride, _ = F.TABLES["starting equipment"]
    return iso.ru(base + cid * stride + F.EQUIP_WEAPON_OFF, 1)


def _sjis_field(raw):
    """Decode one NUL-padded Shift-JIS string field from a weapon record."""
    b = raw.split(b"\x00")[0]
    try: return b.decode("cp932")
    except Exception: return ""


def read_weapon_record(iso, wid):
    """One weapon record (id W, 0..81) from the reframed weapon table:
    the 16 growth bytes (the only writable part), the SJIS type and stage
    names as read-only labels, the +0x0F range/class byte and +0x10 affinity
    index byte (read-only: their gameplay semantics are unproven), and the
    list of characters whose starting-equipment record carries this W
    (an edit to the record affects all of them)."""
    if not (0 <= wid < F.WEAPON_RECORD_COUNT):
        raise KeyError(f"no weapon record {wid}")
    base = F.TABLES["weapon growth"][0] + wid * F.WEAPON_TABLE_STRIDE
    raw = iso.rd(base, F.WEAPON_TABLE_STRIDE)
    names = {c["id"]: c["name"] for c in F.load_characters()}
    users = []
    for cid in range(134):
        if weapon_id_for_char(iso, cid) == wid and wid != 0:
            users.append({"id": cid, "name": names.get(cid, "#%d" % cid)})
    return {"id": wid,
            "typeName": _sjis_field(raw[0x00:0x0F]),
            "stageNames": [_sjis_field(raw[0x11:0x26]), _sjis_field(raw[0x26:0x3B]),
                           _sjis_field(raw[0x3B:0x50])],
            "rangeByte": raw[0x0F], "indexByte": raw[0x10],
            "growth": [raw[F.WEAPON_GROWTH_OFF + i] for i in range(16)],
            "users": users}


def weapon_type_names(iso):
    """{weapon id: type name} for the starting-equipment Weapon id dropdown
    (issue #42): id 0 is the empty record ("No weapon record"); the rest are
    the disc's own type-name strings read from each weapon record. Type
    names repeat across records (several records share one weapon type),
    so callers show the id alongside the name."""
    base = F.TABLES["weapon growth"][0]
    out = {0: "No weapon record"}
    for wid in range(1, F.WEAPON_RECORD_COUNT):
        out[wid] = (_sjis_field(iso.rd(base + wid * F.WEAPON_TABLE_STRIDE, 0x0F))
                    or ("Weapon record %d" % wid))
    return out


def read_weapon_for_char(iso, cid):
    """Weapon context for one character: its W byte plus the full weapon
    record when W is nonzero (None when the character has no weapon record)."""
    w = weapon_id_for_char(iso, cid)
    return {"w": w, "record": read_weapon_record(iso, w) if w else None}


def write_weapon_record(iso, wid, label, value):
    """Write one growth field of weapon record `wid` directly (the CSV
    sheet is keyed by weapon record id, not by character)."""
    if not (0 <= wid < F.WEAPON_RECORD_COUNT):
        raise KeyError(f"no weapon record {wid}")
    for (l, o, w, k) in F.TABLES["weapon growth"][2]:
        if l == label:
            iso.wu(F.TABLES["weapon growth"][0] + wid * F.WEAPON_TABLE_STRIDE + o, w, value)
            return True
    raise KeyError(f"no field {label!r} in weapon growth")


def read_character(iso, cid):
    # In PAL, the char sub-section tables (starting equipment/items) aren't offset-mapped
    # yet — skip them so we never surface wrong data.
    skip = set(F.GATED_IN_PAL) if F.REGION == "pal" else set()
    out = {}
    for t in F.TABLES:
        if t in skip: continue
        if t == "weapon growth":
            # Issue #22: growth is indexed by the character's weapon record
            # id W, not by character id. W = 0 means no weapon record, so
            # there is nothing to show; every other character (ids 81-119
            # included) reads its shared record.
            w = weapon_id_for_char(iso, cid)
            if w: out[t] = read_table(iso, t, w)
            continue
        out[t] = read_table(iso, t, cid)
    return out


def write_field(iso, table, cid, label, value):
    if table == "weapon growth":
        # cid is a CHARACTER id here (the character panels' write path);
        # the byte lands in the character's shared weapon record.
        w = weapon_id_for_char(iso, cid)
        if not w:
            raise KeyError(f"character {cid} has no weapon record (weapon id 0)")
        return write_weapon_record(iso, w, label, value)
    if table == "starting equipment" and label == "Weapon id":
        if not (0 <= int(value) < F.WEAPON_RECORD_COUNT):
            raise ValueError(f"weapon id must be 0..{F.WEAPON_RECORD_COUNT - 1}")
    for (l, o, w, k) in F.TABLES[table][2]:
        if l == label:
            iso.wu(table_addr(table, cid) + o, w, value); return True
    raise KeyError(f"no field {label!r} in {table}")


# ---- Starting loadouts (issue #28) ------------------------------------------
# New-game-facing: these tables are what the builder func_002CEE90 reads when
# it materializes a character record. Existing saves carry their own record
# copies and are not changed by edits here. NTSC-U only (PAL bases unmapped).

def _loadout_gated():
    return F.REGION != "ntsc-u"


def _signed32(v):
    return v - (1 << 32) if v >= (1 << 31) else v


def _save_rune_names():
    try: return F.res_json("s5_rune_save_ids.json")
    except Exception: return []


def read_loadout(iso, cid):
    """Decode one character's starting loadout from the ELF tables:
    starting level + armor (the starting-equipment record), starting runes
    with unlock levels and permanent flags (rune table words 0..5), and
    starting skill ranks merged with the caps table. Returns
    {"gated": True} in PAL, where these tables are not mapped."""
    if _loadout_gated(): return {"gated": True, "id": cid}
    if not (0 <= cid < F.LOADOUT_COUNT): raise KeyError(f"no character {cid}")
    names = _save_rune_names()
    eq_base, eq_stride, _ = F.TABLES["starting equipment"]
    eq = iso.rd(eq_base + cid * eq_stride, eq_stride)
    level_byte = eq[F.EQUIP_LEVEL_OFF]
    rbase = F.LOADOUT_RUNE_BASE + cid * F.LOADOUT_RUNE_STRIDE
    words = [_signed32(iso.ru(rbase + i * 4, 4)) for i in range(6)]
    slots = ("head", "right", "left")
    runes = []
    for s in range(3):
        w, perm = words[s], words[3 + s]
        e = {"slot": slots[s], "word": w, "perm": perm}
        if w > 0:
            e.update({"kind": "rune", "runeId": w,
                      "runeName": names[w] if w < len(names) else "#%d" % w})
        elif w < 0:
            e.update({"kind": "unlock", "unlockLevel": -w})
        else:
            e.update({"kind": "empty"})
        runes.append(e)
    sbase = F.LOADOUT_SKILL_BASE + cid * F.LOADOUT_SKILL_STRIDE
    raw = iso.rd(sbase, F.LOADOUT_SKILL_STRIDE)
    cap_base, cap_stride, cap_fields = F.TABLES["equipable skills"]
    skills = []
    for s in range(48):
        start = raw[1 + s]
        cap = iso.ru(cap_base + cid * cap_stride + s, 1)
        eff = start if start else (1 if cap else 0)
        skills.append({"index": s, "name": cap_fields[s][0], "start": start,
                       "cap": cap, "effective": eff})
    equipped = [s + 1 for s in range(48) if raw[1 + s] > 0][:2]
    return {"gated": False, "id": cid,
            "levelByte": level_byte, "level": level_byte if level_byte else 1,
            "armor": [eq[2 + i] for i in range(4)],
            "runes": runes, "skills": skills, "equippedSlots": equipped,
            "runeNames": names}


def build_loadout_enumeration(iso, cid):
    """The loadout in the shape of the lab enumeration
    (ps2-re loadout_work/loadouts_elf.json), for verification: level,
    per-slot rune words decoded, derived equipped slots, and effective
    ranks after the caps merge (nonzero only)."""
    lo = read_loadout(iso, cid)
    if lo.get("gated"): raise KeyError("loadouts are NTSC-U only")
    runes = []
    for r in lo["runes"]:
        e = {"slot": r["slot"], "word": r["word"], "perm_flag": r["perm"]}
        if r["kind"] == "rune":
            e["rune_id"] = r["runeId"]; e["rune"] = r["runeName"]
        elif r["kind"] == "unlock":
            e["unlock_level"] = r["unlockLevel"]
        runes.append(e)
    ranks = {sk["name"]: sk["effective"] for sk in lo["skills"] if sk["effective"]}
    chars = {c["id"]: c["name"] for c in F.load_characters()}
    return {"name": chars.get(cid, "#%d" % cid), "level": lo["level"],
            "level_byte": lo["levelByte"], "runes": runes,
            "slots": lo["equippedSlots"],
            "slot_names": [lo["skills"][s - 1]["name"] for s in lo["equippedSlots"]],
            "ranks": ranks}


def write_loadout_rune(iso, cid, slot, word):
    """Write one rune-table slot word (s32). Positive = starting rune id in
    save id space (must name a real save-space rune); negative -N = empty
    slot unlocking at level N (1..99); 0 = no starting rune."""
    if _loadout_gated(): raise KeyError("loadouts are NTSC-U only")
    if not (0 <= cid < F.LOADOUT_COUNT): raise KeyError(f"no character {cid}")
    if not (0 <= slot <= 2): raise KeyError(f"no rune slot {slot}")
    word = int(word)
    names = _save_rune_names()
    if word > 0 and not (word < len(names)):
        raise ValueError(f"rune id {word} is outside save id space (0..{len(names) - 1})")
    if word < -99:
        raise ValueError(f"unlock level {-word} is outside 1..99")
    off = F.LOADOUT_RUNE_BASE + cid * F.LOADOUT_RUNE_STRIDE + slot * 4
    iso.wr(off, word.to_bytes(4, "little", signed=True))
    return True


def write_loadout_perm(iso, cid, slot, flag):
    """Write one rune-table permanent flag word (0 or 1)."""
    if _loadout_gated(): raise KeyError("loadouts are NTSC-U only")
    if not (0 <= cid < F.LOADOUT_COUNT): raise KeyError(f"no character {cid}")
    if not (0 <= slot <= 2): raise KeyError(f"no rune slot {slot}")
    if int(flag) not in (0, 1): raise ValueError("permanent flag must be 0 or 1")
    off = F.LOADOUT_RUNE_BASE + cid * F.LOADOUT_RUNE_STRIDE + 12 + slot * 4
    iso.wu(off, 4, int(flag))
    return True


def write_loadout_skill(iso, cid, skill_index, value):
    """Write one starting skill rank byte, validated against the character's
    cap for that skill (stock data never exceeds the cap; a rank above it
    could never be equipped). Values are ranks 0..7."""
    if _loadout_gated(): raise KeyError("loadouts are NTSC-U only")
    if not (0 <= cid < F.LOADOUT_COUNT): raise KeyError(f"no character {cid}")
    if not (0 <= skill_index < 48): raise KeyError(f"no skill {skill_index}")
    value = int(value)
    if not (0 <= value <= 7): raise ValueError("skill rank must be 0..7")
    cap_base, cap_stride, _ = F.TABLES["equipable skills"]
    cap = iso.ru(cap_base + cid * cap_stride + skill_index, 1)
    if value > cap:
        raise ValueError(f"rank {value} exceeds this character's cap {cap} for the skill")
    off = F.LOADOUT_SKILL_BASE + cid * F.LOADOUT_SKILL_STRIDE + 1 + skill_index
    iso.wu(off, 1, value)
    return True


# (read_prices/write_price removed in v1.17.6, issue #30: the "prices" table was
# the Arm armor table read one record late. See the note in s5fields.py.)

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
    _ARMOR_EN = F.res_json("s5_armor_stat_names.json")
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
    fields = [{"label": l, "off": o, "width": w,
               # Gear Type is a weight class on the four armor slots (issue
               # #42); the accessory table's Type is a different, undocumented
               # value set, so it stays a plain number there.
               "kind": "geartype" if (l == "Type" and slot != "accessory") else "num",
               "signed": s, "value": _rd_signed(iso, base + o, w, s)}
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

def write_armor_summary(iso, slot, i, text):
    """Edit a gear piece's in-game description (the Shift-JIS summary at record+0x08).
    The game has NO separate 'set bonus' description string (verified: no such text in the
    ELF), so this per-piece line is the text a player actually reads for a set piece.
    Capped to the EXISTING string's byte length — the nominal field is longer than the
    string but runs into the stat block, so we never write past the current terminator."""
    base = armor_addr(slot, i) + F.ARMOR_SUMMARY_OFF
    cap = armor_summary_cap(iso, slot, i)
    if cap == 0: raise ValueError("this piece has no description slot to edit")
    enc = str(text).encode("cp932", "replace")[:cap]
    iso.wr(base, enc + b"\x00" * (cap - len(enc)))
    return {"ok": True, "cap": cap, "wrote": len(enc)}

def armor_name_cap(iso, slot, i):
    """Writable bytes for a gear piece's own (in-record) name at record−0x1C: the string
    plus its trailing NUL padding, inside the 0x18-byte window so it can never reach the
    record start. Same discipline as the description cap."""
    base = armor_addr(slot, i) - 0x1C
    win = 0x18
    cur = iso.rd(base, win)
    e = cur.find(b"\x00")
    if e < 0: return win
    k = e
    while k < win and cur[k] == 0: k += 1
    return k

def write_armor_name(iso, slot, i, text):
    """Edit a gear piece's name as stored in the ISO (Shift-JIS, at record−0x1C).
    NOTE the editor's English gear labels come from the curated s5_armor_stat_names.json,
    so changing this updates the DISC's string, not that display label."""
    cap = armor_name_cap(iso, slot, i)
    if cap == 0: raise ValueError("this piece has no name slot to edit")
    enc = str(text).encode("cp932", "replace")[:cap]
    iso.wr(armor_addr(slot, i) - 0x1C, enc + b"\x00" * (cap - len(enc)))
    return {"ok": True, "cap": cap, "wrote": len(enc)}

def armor_summary_cap(iso, slot, i):
    """Writable bytes for a piece's description: the existing string PLUS its trailing
    NUL padding, bounded by the first real stat field (+0x41). Using the padding too
    means a shortened description can still be lengthened again later (a plain
    strlen cap would shrink permanently), while never touching the stat block —
    verified: 15 of 219 records DO hold data before +0x41, so we stop at the padding
    run rather than assuming the whole window is free."""
    base = armor_addr(slot, i) + F.ARMOR_SUMMARY_OFF
    win = 0x41 - F.ARMOR_SUMMARY_OFF                 # never write into the stat block
    cur = iso.rd(base, win)
    e = cur.find(b"\x00")
    if e < 0: return win
    k = e
    while k < win and cur[k] == 0: k += 1
    return k


def write_armor_field(iso, slot, i, label, value):
    for (l, o, w, s) in F.ARMOR_FIELDS:
        if l == label:
            if s and w == 1: value &= 0xFF          # two's-complement for signed byte
            iso.wu(armor_addr(slot, i) + o, w, value); return True
    raise KeyError(f"no armor field {label!r}")


# ---- Equipment SET completion bonuses (see s5fields for the RE notes) ---------
# The detector is code, so we PARSE it: trace each set's block with light register
# tracking, recording the file offset of every id/magnitude immediate so the UI can
# patch them. Fully editable: set membership, bonus magnitude, and (via the jump
# table) which effect a set uses at all -> custom sets.
def _s16(x): return x - 0x10000 if x >= 0x8000 else x

def _set_words(iso):
    raw = iso.rd(F.SET_DETECT_OFF, F.SET_DETECT_LEN)
    return list(struct.unpack("<%dI" % (F.SET_DETECT_LEN // 4), raw))

def _set_trace(ws, entry, det_v, seed_regs=None):
    """Follow fall-through from `entry` (a vaddr), tracking registers, until the set
    index is returned. Returns (members, index). $v1/$v0 are seeded with the
    function-wide convention (head/body id) so blocks entered mid-chain resolve.
    `seed_regs` carries register values that are already live at `entry` — for a
    block entered through the dispatch chain, the `li` that held the head id is
    still live and may be compared against ANOTHER slot later in the block
    (Pale Moon: head id 22 is loaded once in the dispatch and reused for the
    accessory compare, because the Casque and the Necklace share equip id 22)."""
    at = lambda va: ws[(va - det_v) // 4]
    regs = dict(seed_regs or {})   # reg -> (value, file offset of the `li`)
    loaded = {3: 68, 2: 69}        # reg -> live-struct offset it was loaded from
    members, idx, va = [], None, entry
    for _ in range(160):
        if not (det_v <= va < det_v + F.SET_DETECT_LEN): break
        w = at(va); fo = va - F.VADDR_DELTA
        op = w >> 26; rs = (w >> 21) & 31; rt = (w >> 16) & 31; imm = w & 0xFFFF; fun = w & 0x3F
        if op in (0x24, 0x20, 0x21, 0x25):                  # load -> rt holds a struct field
            loaded[rt] = _s16(imm); regs.pop(rt, None)
        elif op == 0x09 and rs == 0:                        # li rt, imm
            regs[rt] = (imm, fo); loaded.pop(rt, None)
        elif op in (4, 5):                                  # beq / bne
            if rs == 0 and rt == 0:
                if va + 4 + _s16(imm) * 4 == F.SET_EXIT_VADDR:
                    d = at(va + 4)
                    if (d >> 26) == 0x09 and ((d >> 21) & 31) == 0 and ((d >> 16) & 31) == 2:
                        idx = d & 0xFFFF
                    elif 2 in regs: idx = regs[2][0]
                    return members, idx
            else:
                lo = rs if rs in loaded else (rt if rt in loaded else None)
                ri = rt if lo == rs else rs
                if lo is not None and ri in regs:
                    soff = loaded[lo]; val, imoff = regs[ri]
                    slot = F.SET_STRUCT_SLOT.get(soff, "accessory" if soff == F.SET_ACC_ID_OFF else None)
                    if slot: members.append({"slot": slot, "id": val, "off": imoff})
        elif op == 0 and fun == 8:                          # jr $ra (fall-through exit)
            if 2 in regs: idx = regs[2][0]
            return members, idx
        va += 4
    return members, idx

def _set_effects(iso, handler_vaddr):
    """Decode a handler into editable effects: read-modify-add, or li+store."""
    if not handler_vaddr or handler_vaddr == F.SET_NOOP_VADDR: return []
    fo = handler_vaddr - F.VADDR_DELTA
    hw = list(struct.unpack("<28I", iso.rd(fo, 112)))
    out, pend, stop_after = [], None, None
    slti_imms = set()                                       # imm of every slti seen so far
    for k, w in enumerate(hw):
        fk = fo + k * 4
        op = w >> 26; rs = (w >> 21) & 31; rt = (w >> 16) & 31; imm = w & 0xFFFF
        if op == 0x0A:                                      # slti -> a range guard
            slti_imms.add(imm)
        elif op in (0x21, 0x25, 0x24, 0x20):                # lh/lhu/lbu/lb -> read target
            pend = {"charOff": _s16(imm), "width": "h" if op in (0x21, 0x25) else "b"}
        elif op == 0x09 and rs == 0 and rt != 0:            # li -> a "set" style effect
            pend = {"setval": (imm, fk)}
        elif op == 0x09 and rs != 0 and pend and "setval" not in pend:
            pend.update({"kind": "add", "value": _s16(imm), "immOff": fk}); out.append(pend); pend = None
        elif op in (0x28, 0x29, 0x2B):                      # sb/sh/sw -> commits a li
            if pend and "setval" in pend:
                coff, val = _s16(imm), pend["setval"][0]
                # A li+store sitting in a branch's delay slot, storing value V to a
                # field the handler already ADDs to, right after an slti guard
                # against V+1, is not a bonus — it is the game's CAP on that field
                # (Samurai: Double Critical is clamped to 100, issue #29). Report
                # it as a read-only "cap" instead of an editable "set" effect.
                prev = hw[k - 1] if k else 0
                in_delay = (prev >> 26) == 2 or ((prev >> 26) in (4, 5)
                             and ((prev >> 21) & 31) == 0 and ((prev >> 16) & 31) == 0)
                is_cap = (in_delay and (val + 1) in slti_imms
                          and any(e.get("kind") == "add" and e.get("charOff") == coff for e in out))
                out.append({"kind": "cap" if is_cap else "set", "charOff": coff,
                            "width": {0x28: "b", 0x29: "h", 0x2B: "w"}[op],
                            "value": val, "immOff": pend["setval"][1]})
                pend = None
        elif (op in (4, 5) and rs == 0 and rt == 0) or op == 2 or (op == 0 and (w & 0x3F) == 8):
            stop_after = k + 1                              # MIPS: the delay slot still runs
        elif op == 0x11 or (w & 0xFC00003F) == 0x44000000:
            out.append({"kind": "float", "charOff": None, "width": None, "value": None, "immOff": None})
        if stop_after is not None and k >= stop_after: break
    return out

def _set_gate(iso, handler_vaddr):
    """Detect a per-character RESTRICTION at the top of a handler.

    The Sun set is documented "Prince only", and its handler opens with:
        lbu $v1, 16($s0)          ; a per-character field
        bne $v1, $zero, <skip>    ; not that character -> skip the whole bonus
    So the restriction is one conditional branch. Replacing it with a NOP makes the
    bonus apply to whoever wears the set. Returns None when a handler is unrestricted.
    """
    if not handler_vaddr or handler_vaddr == F.SET_NOOP_VADDR: return None
    fo = handler_vaddr - F.VADDR_DELTA
    w0, w1, w2 = struct.unpack("<3I", iso.rd(fo, 12))
    if (w0 >> 26) not in (0x24, 0x20, 0x21, 0x25): return None   # must start with a load
    base = {"charOff": _s16(w0 & 0xFFFF), "branchOff": fo + 4}
    if w1 == 0:                                                  # restriction removed (NOP)
        return dict(base, word=0, restricted=False, kind="nop", charId=None, target=None)
    op1 = w1 >> 26
    # re-pointed by us: `addiu $at,$zero,N` then a branch against $at
    if op1 == 0x09 and ((w1 >> 21) & 31) == 0 and ((w1 >> 16) & 31) == 1 and (w2 >> 26) in (4, 5):
        return dict(base, word=w1, restricted=True, kind="retargeted",
                    charId=w1 & 0xFFFF,
                    target=handler_vaddr + 12 + _s16(w2 & 0xFFFF) * 4)
    if op1 not in (4, 5): return None                            # otherwise must be beq/bne
    rs, rt = (w1 >> 21) & 31, (w1 >> 16) & 31
    if rt != 0 and rs != 0: return None                          # compared against $zero
    return dict(base, word=w1, restricted=True, kind="bne" if op1 == 5 else "beq",
                charId=0,                                        # stock gate: character 0
                target=handler_vaddr + 8 + _s16(w1 & 0xFFFF) * 4)

def write_set_gate(iso, set_index, enabled, original_word=None):
    """Turn a set's per-character restriction on or off. Disabling writes a NOP over the
    conditional branch (4 bytes, fully reversible); enabling restores the original
    instruction word, which the caller supplies from the earlier read."""
    data = read_sets(iso)
    s = next((x for x in data["sets"] if x["index"] == int(set_index)), None)
    if not s: raise KeyError("no set %s" % set_index)
    gate = s.get("gate")
    off = gate["branchOff"] if gate else None
    if off is None:
        # already NOPed out: the branch sits right after the handler's first load
        h = s.get("handler")
        if not h or h == F.SET_NOOP_VADDR: raise ValueError("this set has no restriction to change")
        off = (h - F.VADDR_DELTA) + 4
    if enabled:
        if not original_word: raise ValueError("restoring a restriction needs its original instruction")
        iso.wu(off, 4, int(original_word) & 0xFFFFFFFF)
    else:
        iso.wu(off, 4, 0)                                     # nop
    return {"ok": True, "restricted": bool(enabled), "off": off}

def write_set_gate_char(iso, set_index, char_id, original_word=None):
    """Re-point a set's per-character restriction at a DIFFERENT character.

    The stock gate compares against $zero (character 0 = the Prince):
        lbu   $vX, 16($s0)
        bne   $vX, $zero, <skip>
        nop                       <- a free delay slot we can use
    To gate on character N we use that spare slot:
        lbu   $vX, 16($s0)
        addiu $at, $zero, N
        bne   $vX, $at, <skip>
    The new branch's delay slot becomes the handler's first real instruction, which is a
    plain load in every stock handler, so it is harmless either way.

    NOTE the compared field (char+16) is inferred to be the character id: the Sun set is
    documented "Prince only", the Prince is character 0, and the stock test is "== 0".
    Consistent, but not independently proven — treat the id mapping as best-effort.
    """
    data = read_sets(iso)
    s = next((x for x in data["sets"] if x["index"] == int(set_index)), None)
    if not s: raise KeyError("no set %s" % set_index)
    h = s.get("handler")
    if not h or h == F.SET_NOOP_VADDR: raise ValueError("this set has no handler to gate")
    n = int(char_id)
    if not (0 <= n <= 0xFF): raise ValueError("character id out of range")
    fo = h - F.VADDR_DELTA
    w0, w1, w2 = struct.unpack("<3I", iso.rd(fo, 12))
    if (w0 >> 26) not in (0x24, 0x20, 0x21, 0x25):
        raise ValueError("handler does not start with a per-character load")
    reg = (w0 >> 16) & 31
    AT = 1
    LI = lambda v: (0x09 << 26) | (AT << 16) | (v & 0xFFFF)      # addiu $at,$zero,v
    # already re-pointed by us? then just patch the compared value in place
    if (w1 >> 26) == 0x09 and ((w1 >> 21) & 31) == 0 and ((w1 >> 16) & 31) == AT and (w2 >> 26) in (4, 5):
        iso.wu(fo + 4, 4, LI(n))
        return {"ok": True, "charId": n, "off": fo + 4, "mode": "patched"}
    # otherwise synthesize the two-instruction form, using the stock branch for the target
    ow = int(original_word) if original_word else w1
    if (ow >> 26) not in (4, 5):
        raise ValueError("need the original restriction instruction to know where it skips to")
    if w2 != 0 and (w2 >> 26) not in (4, 5):
        raise ValueError("no free delay slot to place the comparison")
    target = (h + 4) + 4 + _s16(ow & 0xFFFF) * 4                 # stock branch sat at h+4
    disp = (target - ((h + 8) + 4)) >> 2                         # new branch sits at h+8
    if not (-32768 <= disp <= 32767): raise ValueError("skip target out of branch range")
    bne = (0x05 << 26) | (reg << 21) | (AT << 16) | (disp & 0xFFFF)
    iso.wu(fo + 4, 4, LI(n))
    iso.wu(fo + 8, 4, bne)
    return {"ok": True, "charId": n, "off": fo + 4, "mode": "synthesized"}

def read_set_gate_char(iso, handler_vaddr):
    """If a gate has been re-pointed, return the character id it now compares against."""
    if not handler_vaddr or handler_vaddr == F.SET_NOOP_VADDR: return None
    fo = handler_vaddr - F.VADDR_DELTA
    w0, w1, w2 = struct.unpack("<3I", iso.rd(fo, 12))
    if (w1 >> 26) == 0x09 and ((w1 >> 21) & 31) == 0 and ((w1 >> 16) & 31) == 1 and (w2 >> 26) in (4, 5):
        return w1 & 0xFFFF
    return 0 if (w1 >> 26) in (4, 5) else None                   # stock gate == character 0

def read_sets(iso):
    """All equipment sets: members (with the file offset of each id immediate), the
    jump-table slot, and the decoded bonus effects. Names come from the armor tables
    (s5_armor_names tags each piece with its set)."""
    det_v = F.SET_DETECT_OFF + F.VADDR_DELTA
    ws = _set_words(iso)
    at = lambda va: ws[(va - det_v) // 4]
    # dispatch map: `beq $v1,<li head id>, target` -> (head id, li file offset, li reg)
    disp, dregs = {}, {}
    for k in range(F.SET_DETECT_LEN // 4):
        va = det_v + k * 4; w = ws[k]
        op = w >> 26; rs = (w >> 21) & 31; rt = (w >> 16) & 31; imm = w & 0xFFFF
        if op == 0x09 and rs == 0: dregs[rt] = (imm, va - F.VADDR_DELTA, rt)
        elif op == 4 and (rs == 3 or rt == 3):
            other = rt if rs == 3 else rs
            if other in dregs: disp[va + 4 + _s16(imm) * 4] = dregs[other]
    # candidate block starts: every branch target + every fall-through
    targets = set()
    for k in range(F.SET_DETECT_LEN // 4):
        va = det_v + k * 4; w = ws[k]
        if (w >> 26) in (4, 5, 6, 7):
            targets.add(va + 4 + _s16(w & 0xFFFF) * 4); targets.add(va + 8)
    def score(m):
        if sum(1 for x in m if x["slot"] == "head") > 1: return -1   # a dispatch chain
        return len({x["slot"] for x in m}) * 100 + len(m)
    # A set's block can be entered at several points (its dispatch target, or a
    # fall-through part-way in), and each entry point's trace sees only part of
    # the member compares — Pale Moon's dispatch trace found head/body/arm/foot
    # but not the accessory, a mid-block trace found the accessory but not the
    # head, and the tie between them silently dropped the Casque (issue #26).
    # So keep every credible candidate and UNION their members: dispatch-entry
    # traces first (they own the head compare), then by score.
    cands = {}
    for t in sorted(targets):
        if not (det_v <= t < det_v + F.SET_DETECT_LEN): continue
        seed = None
        if t in disp:
            hid, ho, hreg = disp[t]; seed = {hreg: (hid, ho)}
        m, i = _set_trace(ws, t, det_v, seed_regs=seed)
        if i is None or i >= F.SET_COUNT: continue
        if t in disp and not any(x["slot"] == "head" for x in m):
            hid, ho, _ = disp[t]; m = [{"slot": "head", "id": hid, "off": ho}] + m
        sc = score(m)
        if sc < 200: continue
        cands.setdefault(i, []).append((t in disp, sc, t, m))
    best = {}
    for i, lst in cands.items():
        lst.sort(key=lambda c: (not c[0], -c[1], c[2]))
        merged, seen = [], set()
        for _, _, _, m in lst:
            for mem in m:
                # accessories may legitimately repeat (two slots); other slots
                # take the first (highest-priority) trace's id
                key = (mem["slot"], mem["id"]) if mem["slot"] == "accessory" else (mem["slot"],)
                if key in seen: continue
                seen.add(key); merged.append(mem)
        order = {"head": 0, "body": 1, "arm": 2, "foot": 3, "accessory": 4}
        merged.sort(key=lambda mem: order.get(mem["slot"], 9))
        best[i] = (merged, lst[0][2], lst[0][1])
    jt = list(struct.unpack("<%dI" % F.SET_COUNT, iso.rd(F.SET_JT_OFF, F.SET_COUNT * 4)))
    names = _set_names()
    out = []
    for i in sorted(best):
        m, entry, _ = best[i]
        out.append({"index": i, "name": _set_name_for(m, names, i), "entry": entry,
                    "members": m, "handler": jt[i], "jtOff": F.SET_JT_OFF + i * 4,
                    "noop": jt[i] == F.SET_NOOP_VADDR,
                    "gate": _set_gate(iso, jt[i]),
                    "effects": _set_effects(iso, jt[i])})
    return {"sets": out, "jumpTable": jt}

def _set_names():
    """equip id -> set name, from the '(SetName)' suffix in s5_armor_names.json."""
    import re
    try: ar = F.res_json("s5_armor_names.json")
    except Exception: return {}
    out = {}
    for slot, m in ar.items():
        if not isinstance(m, dict) or slot.startswith("_"): continue
        for eid, nm in m.items():
            if not str(eid).isdigit(): continue          # skip metadata keys
            mt = re.search(r"\(([^)]+)\)\s*$", str(nm))
            if mt: out[(slot if slot != "glove" else "arm", int(eid))] = mt.group(1).strip()
    return out

def _set_name_for(members, names, idx):
    for m in members:
        nm = names.get((m["slot"], m["id"]))
        if nm: return nm
    return "Set %d" % idx

def write_set_member(iso, set_index, slot, equip_id):
    """Change which item a set requires in one slot (patches the compare immediate)."""
    data = read_sets(iso)
    s = next((x for x in data["sets"] if x["index"] == int(set_index)), None)
    if not s: raise KeyError("no set %s" % set_index)
    mem = next((m for m in s["members"] if m["slot"] == slot), None)
    if not mem: raise KeyError("set %s has no %s slot to edit" % (set_index, slot))
    v = int(equip_id)
    if not (0 <= v <= 0xFF): raise ValueError("equip id out of range")
    iso.wu(mem["off"], 2, v)                     # the 16-bit immediate of the compare
    return {"ok": True, "slot": slot, "id": v, "off": mem["off"]}

def write_set_bonus(iso, set_index, effect_index, value):
    """Change a set bonus magnitude (patches the handler's immediate)."""
    data = read_sets(iso)
    s = next((x for x in data["sets"] if x["index"] == int(set_index)), None)
    if not s: raise KeyError("no set %s" % set_index)
    eff = s["effects"][int(effect_index)]
    if eff.get("kind") == "cap": raise ValueError("that value is the game's built-in cap on the field, not an editable bonus")
    if eff.get("immOff") is None: raise ValueError("this effect isn't a simple numeric bonus")
    v = int(value)
    if not (-32768 <= v <= 65535): raise ValueError("value out of range")
    iso.wu(eff["immOff"], 2, v & 0xFFFF)
    return {"ok": True, "value": v, "off": eff["immOff"]}

def write_set_handler(iso, set_index, handler_vaddr):
    """Point a set at a different effect handler (pure jump-table data edit) — this is
    how a set with no bonus (or any set) can be given another set's bonus."""
    i = int(set_index)
    if not (0 <= i < F.SET_COUNT): raise KeyError("set index out of range")
    iso.wu(F.SET_JT_OFF + i * 4, 4, int(handler_vaddr) & 0xFFFFFFFF)
    return {"ok": True, "index": i, "handler": int(handler_vaddr)}


def _rune_gate_sites(iso):
    """Every hasRune() call whose gate matches the canonical two-branch shape, so it can
    be forced permanently on. Returns [{runeId, vaddr, off1, off2, word1, word2, forced}]."""
    lo, hi = F.RUNE_GATE_LO, F.RUNE_GATE_HI
    blob = iso.rd(lo - F.VADDR_DELTA, hi - lo)
    cnt = len(blob) // 4
    ws = struct.unpack("<%dI" % cnt, blob[:cnt * 4])
    target = (F.RUNE_HAS_FN & 0x0FFFFFFF) >> 2
    out = []
    for i, w in enumerate(ws):
        if (w >> 26) != 3 or (w & 0x3FFFFFF) != target: continue
        rid = None
        for k in range(max(0, i - 8), i + 2):
            u = ws[k]
            if (u >> 26) == 0x09 and ((u >> 21) & 31) == 0 and ((u >> 16) & 31) == 5:
                rid = u & 0xFFFF
        if rid is None or i + 8 >= cnt: continue
        b1, andi, b2 = ws[i + 2], ws[i + 6], ws[i + 7]
        ok1 = (b1 >> 26) == 4 and ((b1 >> 21) & 31) == 2 and ((b1 >> 16) & 31) == 0
        ok2 = ((andi >> 26) == 0x0C and (andi & 0xFFFF) == 2
               and (b2 >> 26) == 4 and ((b2 >> 16) & 31) == 0)
        forced = b1 == 0 and b2 == 0 and (andi >> 26) == 0x0C and (andi & 0xFFFF) == 2
        if not (ok1 and ok2):
            if not forced: continue
        if ok1 and ok2:
            t1 = (lo + (i + 2) * 4) + 4 + _s16(b1 & 0xFFFF) * 4
            t2 = (lo + (i + 7) * 4) + 4 + _s16(b2 & 0xFFFF) * 4
            if t1 != t2: continue
        out.append({"runeId": rid, "vaddr": lo + i * 4,
                    "off1": (lo + (i + 2) * 4) - F.VADDR_DELTA,
                    "off2": (lo + (i + 7) * 4) - F.VADDR_DELTA,
                    "word1": b1, "word2": b2, "forced": forced})
    return out

def read_rune_always_on(iso):
    """Runes whose effect can be forced permanently on, grouped by rune."""
    try: names = F.res_json("s5_rune_ids.json")
    except Exception: names = []
    by = {}
    for g in _rune_gate_sites(iso):
        rid = g["runeId"]
        e = by.setdefault(rid, {"runeId": rid,
                                "name": names[rid] if rid < len(names) else "Rune %d" % rid,
                                "sites": [], "forced": 0})
        e["sites"].append(g)
        if g["forced"]: e["forced"] += 1
    out = []
    for rid in sorted(by):
        e = by[rid]
        e["siteCount"] = len(e["sites"])
        e["allForced"] = e["forced"] == len(e["sites"])
        out.append(e)
    return {"runes": out, "hasRuneFn": F.RUNE_HAS_FN}

def write_rune_always_on(iso, rune_id, enabled, originals=None):
    """Force (or restore) a rune's effect so it applies without the rune equipped.
    Enabling NOPs both gate branches at every canonical site for that rune; disabling
    restores the original instruction words supplied by the caller."""
    sites = [g for g in _rune_gate_sites(iso) if g["runeId"] == int(rune_id)]
    if not sites: raise KeyError("rune %s has no forceable gate" % rune_id)
    if enabled:
        for g in sites:
            iso.wu(g["off1"], 4, 0); iso.wu(g["off2"], 4, 0)
        return {"ok": True, "runeId": int(rune_id), "sites": len(sites), "forced": True}
    if not originals: raise ValueError("restoring needs the original instruction words")
    orig = {int(k): v for k, v in (originals or {}).items()}
    n = 0
    for g in sites:
        pair = orig.get(g["vaddr"])
        if not pair: continue
        iso.wu(g["off1"], 4, int(pair[0]) & 0xFFFFFFFF)
        iso.wu(g["off2"], 4, int(pair[1]) & 0xFFFFFFFF)
        n += 1
    if not n: raise ValueError("no original instructions matched this rune's sites")
    return {"ok": True, "runeId": int(rune_id), "sites": n, "forced": False}

_DAWN_RE = None

def _dawn_pattern():
    """`addiu v1,zero,4` / `jal <setter>` / <patch site>, where the patch site is either
    the stock `andi a0,v1,255` or an already-applied `addiu a0,zero,N`. Exactly one hit
    per disc in both regions."""
    global _DAWN_RE
    if _DAWN_RE is None:
        anchor = re.escape(struct.pack("<I", F.DAWN_ANCHOR_WORD))   # addiu v1,zero,4
        jal    = b"..." + re.escape(b"\x0c")                        # jal <anything>
        stock  = re.escape(struct.pack("<I", F.DAWN_STOCK_WORD))    # andi a0,v1,255
        # addiu a0,zero,N — the immediate is the low byte, so leave it open
        forced = b"[\\x00-\\xff]" + re.escape(struct.pack("<I", F.DAWN_FORCE_WORD)[1:])
        _DAWN_RE = re.compile(anchor + jal + b"(?:" + stock + b"|" + forced + b")", re.S)
    return _DAWN_RE

def _dawn_site(iso):
    """ISO offset + current word of the Dawn Rune spell-count patch site, or None."""
    lo, hi = F.DAWN_SCAN_LO, F.DAWN_SCAN_HI
    blob = iso.rd(lo, hi - lo)
    hits = [m.start() for m in _dawn_pattern().finditer(blob)]
    if len(hits) != 1: return None                 # ambiguous -> refuse to guess
    at = hits[0] + 8
    return {"off": lo + at, "word": struct.unpack_from("<I", blob, at)[0]}

def read_dawn_unlock(iso):
    """How many Dawn Rune spells are unlocked from the start. `count` 0 means vanilla —
    the story script decides, so the fourth spell only arrives near the endgame."""
    s = _dawn_site(iso)
    if not s: return {"found": False, "count": 0, "max": F.DAWN_MAX}
    w = s["word"]
    forced = (w & 0xFFFF0000) == F.DAWN_FORCE_WORD
    return {"found": True, "off": s["off"], "word": w, "max": F.DAWN_MAX,
            "count": (w & 0xFF) if forced else 0, "stock": w == F.DAWN_STOCK_WORD}

def write_dawn_unlock(iso, count):
    """Force the Dawn Rune's spell count (1..DAWN_MAX), or pass 0 to restore the stock
    instruction and hand the count back to the story script. One word, reversible."""
    n = int(count)
    if not (0 <= n <= F.DAWN_MAX):
        raise ValueError("dawn spell count must be 0 (vanilla) .. %d" % F.DAWN_MAX)
    s = _dawn_site(iso)
    if not s: raise KeyError("Dawn Rune spell-count patch site not found on this disc")
    word = F.DAWN_STOCK_WORD if n == 0 else (F.DAWN_FORCE_WORD | n)
    iso.wu(s["off"], 4, word)
    return {"ok": True, "off": s["off"], "count": n, "word": word}

def set_effect_targets():
    """Catalog of char-struct fields a custom set bonus can touch."""
    return [{"label": l, "charOff": o, "width": w, "verified": v, "kind": k}
            for (l, o, w, v, k) in F.SET_EFFECT_TARGETS]

def set_grade_names():
    """Tier labels for `grade` targets (index == stored byte)."""
    return list(F.AFFINITY_GRADES)

def read_custom_set_capacity():
    """How many effects fit in the free gap, by op mix. `add` costs 3 instructions
    (load / addiu / store), `set` costs 2 (li / store); the tail `j`+nop costs 2."""
    words = F.SET_CUSTOM_LEN // 4
    return {"words": words, "maxAdd": (words - 2) // 3, "maxSet": (words - 2) // 2}

def write_custom_set_bonus(iso, set_index, effects):
    """Assemble a CUSTOM bonus handler into the free gap and point a set at it.

    effects: [{"charOff":n, "width":"b"|"h", "op":"add"|"set", "value":v}, ...]
    Emits, per effect:
        add ->  lhu/lbu $v1,off($s0) ; addiu $v1,$v1,V ; sh/sb $v1,off($s0)
        set ->  addiu $v1,$zero,V                      ; sh/sb $v1,off($s0)
    then `j <shared epilogue>` + nop. The stock handlers are packed with no slack, so
    this is the only way to ADD effects rather than retune existing ones.
    """
    V1, S0 = 3, F.SET_STRUCT_REG
    words, budget = [], F.SET_CUSTOM_LEN // 4
    valid = {(t[1], t[2]): t[4] for t in F.SET_EFFECT_TARGETS}
    for e in (effects or []):
        off = int(e["charOff"]); wid = e.get("width", "h")
        op = e.get("op", "add"); val = int(e.get("value", 0))
        kind = valid.get((off, wid))
        if kind is None:
            raise ValueError("unknown effect target +%d/%s" % (off, wid))
        if kind == "grade":
            # a tier, not a quantity: only ever forced, and only within the grade scale
            if op != "set":
                raise ValueError("%s is a rank — it can only be forced to a tier, not added to"
                                 % dict((t[1], t[0]) for t in F.SET_EFFECT_TARGETS).get(off, "that field"))
            if not (0 <= val < len(F.AFFINITY_GRADES)):
                raise ValueError("rank must be 0..%d (%s)" % (len(F.AFFINITY_GRADES) - 1,
                                 ", ".join("%d=%s" % (i, g) for i, g in enumerate(F.AFFINITY_GRADES))))
        if not (0 <= off <= 0x7FFF): raise ValueError("target offset out of range")
        lim = 0xFF if wid == "b" else 0xFFFF
        if not (-32768 <= val <= lim): raise ValueError("value out of range for that field")
        st = (0x28 if wid == "b" else 0x29)                   # sb / sh
        if op == "set":
            words += [(0x09 << 26) | (V1 << 16) | (val & 0xFFFF),               # li $v1,V
                      (st << 26) | (S0 << 21) | (V1 << 16) | (off & 0xFFFF)]
        elif op == "add":
            ld = (0x25 if wid == "b" else 0x21)               # lhu / lbu(0x24)->use lbu for b
            if wid == "b": ld = 0x24
            words += [(ld << 26) | (S0 << 21) | (V1 << 16) | (off & 0xFFFF),
                      (0x09 << 26) | (V1 << 21) | (V1 << 16) | (val & 0xFFFF),  # addiu $v1,$v1,V
                      (st << 26) | (S0 << 21) | (V1 << 16) | (off & 0xFFFF)]
        else:
            raise ValueError("op must be 'add' or 'set'")
    if len(words) + 2 > budget:
        raise ValueError("too many effects: needs %d instructions, only %d available"
                         % (len(words) + 2, budget))
    words.append((0x02 << 26) | ((F.SET_RETURN_VADDR >> 2) & 0x3FFFFFF))        # j epilogue
    words.append(0)                                                            # nop
    words += [0] * (budget - len(words))                       # clear the rest of the gap
    fo = F.SET_CUSTOM_VADDR - F.VADDR_DELTA
    iso.wr(fo, struct.pack("<%dI" % len(words), *words))
    iso.wu(F.SET_JT_OFF + int(set_index) * 4, 4, F.SET_CUSTOM_VADDR)
    return {"ok": True, "handler": F.SET_CUSTOM_VADDR, "instructions": len(words),
            "effects": len(effects or [])}

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
    """List unit records that have HP (enemies + units), with best-effort names.

    HP is u16, so ANY non-zero value is a plausible record; a slot is only rejected
    when it reads as 0xFF padding. The ceiling used to be 60000, which quietly dropped
    an enemy off the list the moment a mod pushed its HP past that — the record was
    still on the disc, but the editor stopped showing it and a CSV re-import called its
    id unknown. 65535 HP is a legitimate (if silly) edit, so it has to survive too.

    That leniency only works because F.ENEMY_MAX stops the scan at the end of the table.
    It used to be 584 instead of 219, and every "HP" past the roster was a byte of some
    unrelated ELF structure — which is where the placeholder "Enemy 221"+ rows with
    impossible Levels and five-digit stats came from (see the F.ENEMY_MAX note)."""
    names = names or {}
    out = []
    for eid in range(F.ENEMY_MAX):
        base = enemy_addr(eid)
        hp = iso.ru(base + 2, 2)
        if hp == 0: continue
        if hp == 0xFFFF and iso.rd(base, 16) == b"\xff" * 16: continue   # unused slot
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


# ---- Field models: the swap table (see s5fields RESOURCE_NAME_BASE / MODEL_PTR_BASE) ----
# A model id is redirected by rewriting its one pointer, so a swap is 4 bytes, reversible,
# and never touches the shared resource-path strings (which other ids may also name).

def _resource_off(slot): return F.RESOURCE_NAME_BASE + slot * F.RESOURCE_NAME_STRIDE

def _resource_slot_of(vaddr):
    """Which name-table slot a model pointer targets, or -1 if it lands outside the table
    (the factory value for model 0 is a 'hogehoge' placeholder just before the table)."""
    off = vaddr - F.MODEL_VADDR_DELTA - F.RESOURCE_NAME_BASE
    if off < 0 or off % F.RESOURCE_NAME_STRIDE: return -1
    slot = off // F.RESOURCE_NAME_STRIDE
    return slot if slot < F.RESOURCE_NAME_COUNT else -1

def read_resource_names(iso):
    """The engine's flat resource-path table: [{slot, off, path, file, vol}]. `vol` is
    VOL_COM (always-resident /COMMON assets) or VOL_USR (per-field assets)."""
    out = []
    for i in range(F.RESOURCE_NAME_COUNT):
        path = iso.rd(_resource_off(i), F.RESOURCE_NAME_STRIDE).split(b"\x00")[0].decode("latin1")
        vol, _, fn = path.partition(":")
        out.append({"slot": i, "off": _resource_off(i), "path": path, "file": fn or path, "vol": vol})
    return out

_MODEL_RE = re.compile(r"^pc(\d{3})c\.rom$", re.I)

def _model_owner(file, chars):
    """Who a pcNNNc.rom model belongs to, as (character name, variant).

    NNN is the character id + 1, so pc001c is the Prince (id 0) and pc009c is Lyon (id 8).
    Files numbered 2xx/3xx/4xx are extra looks for the SAME character — pc401c is
    1 + 400, i.e. the Prince's 4th model — which is how the cast changes appearance as
    the story moves. variant is 1 for the everyday model, 2/3/4 for an alternate.

    The +200/+300/+400 reading is confirmed by the game itself. The ELF carries a table
    (NTSC 0x211250..0x211330) that folds a character's alternate ids back onto one entry —
    it groups {2, 119, 128} (the Prince's three), {10, 120} (Lyon), {14, 121} (Kyle) and
    {22, 122} (Georg). The four it doesn't list are confirmed by palette instead: each
    alternate's 256-colour CLUT is a near-exact match for its base model's (pc320c/pc020c
    Cathari, pc321c/pc221c Georg, pc346c/pc046c Gunde, pc373c/pc073c Miakis, all within
    0.6/768 mean RGB). Only pc314c (Zegai) rests on the numbering alone — it ships no
    palette to compare and the ELF table doesn't mention it."""
    m = _MODEL_RE.match(file or "")
    if not m: return "", 0
    n = int(m.group(1))
    # pc000c..pc124c are the everyday models (the number IS the character); only 2xx and up
    # split into variant + character, so pc101c stays Faylen rather than reading as 1+100.
    variant, base = divmod(n, 100) if n >= 200 else (1, n)
    return chars.get(base - 1, ""), variant

def read_models(iso):
    """The 129-entry field-model table. One row per model id the engine can be asked to
    load; `slot` is the resource slot it currently points at, `default_slot` the factory
    one (model k ships pointing at name slot k-1)."""
    names = read_resource_names(iso)
    chars = {c["id"]: c["name"] for c in F.load_characters()}
    out = []
    for i in range(F.MODEL_PTR_COUNT):
        off = F.MODEL_PTR_BASE + i * 4
        slot = _resource_slot_of(iso.ru(off, 4))
        base = i - 1
        home = names[base] if 0 <= base < len(names) else None
        cur = names[slot] if 0 <= slot < len(names) else None
        who, variant = _model_owner(home["file"], chars) if home else ("", 0)
        out.append({"id": i, "off": off, "slot": slot,
                    "file": cur["file"] if cur else "(off-table)",
                    "path": cur["path"] if cur else "",
                    "default_slot": base if home else -1,
                    "default_file": home["file"] if home else "(placeholder)",
                    "char": who, "variant": variant, "alternate": variant > 1,
                    "placeholder": home is None,
                    "changed": slot != base})
    _number_looks(out)
    return out

def _number_looks(rows):
    """Label each row "look N of M" within its character. The ordinal counts the rows in
    id order rather than reusing the 2xx/3xx/4xx series number, because a character's
    series aren't consecutive — the Prince's are 1, 2 and 4, which would read "4 of 3"."""
    byname = collections.defaultdict(list)
    for r in rows:
        if r.get("char"): byname[r["char"]].append(r)
    for group in byname.values():
        for n, r in enumerate(group, 1): r["look"], r["looks"] = n, len(group)
    for r in rows: r.setdefault("look", 1); r.setdefault("looks", 1)

def model_group(iso, model_id):
    """Every model id that belongs to the same character as this one. Someone with more
    than one look — the Prince ships three — is normally re-skinned as a set, because the
    scripts switch to the alternates by themselves for particular scenes; changing only
    the everyday model leaves those scenes looking like the old one."""
    rows = read_models(iso)
    if not 0 <= model_id < len(rows):
        raise ValueError(f"model id must be 0..{len(rows) - 1}")
    who = rows[model_id]["char"]
    return [r["id"] for r in rows if r["char"] == who] if who else [model_id]

def model_targets(iso):
    """Resource slots that are safe swap targets: the /COMMON character models. VOL_USR
    entries are per-field files that aren't resident during normal play, so they're left
    out of the picker (set_model still accepts any slot for scripted use)."""
    chars = {c["id"]: c["name"] for c in F.load_characters()}
    out = []
    for n in read_resource_names(iso):
        if n["vol"] != "VOL_COM": continue
        f = n["file"].lower()
        if re.match(r"^pc\d{3}c\.rom$", f): group = "Cast"
        elif re.match(r"^ec\d{3}c\.rom$", f): group = "Other characters"
        else: continue
        who, variant = _model_owner(n["file"], chars)
        out.append({"slot": n["slot"], "file": n["file"], "char": who, "variant": variant,
                    "group": "Story alternates" if variant > 1 else group})
    _number_looks(out)
    return out

def set_model(iso, model_id, slot):
    """Point one model id at another resource slot. 4-byte write; reset_model undoes it."""
    if not 0 <= model_id < F.MODEL_PTR_COUNT:
        raise ValueError(f"model id must be 0..{F.MODEL_PTR_COUNT - 1}")
    if not 0 <= slot < F.RESOURCE_NAME_COUNT:
        raise ValueError(f"resource slot must be 0..{F.RESOURCE_NAME_COUNT - 1}")
    iso.wu(F.MODEL_PTR_BASE + model_id * 4, 4, _resource_off(slot) + F.MODEL_VADDR_DELTA)

def reset_model(iso, model_id):
    """Restore a model id's factory pointer (name slot id-1)."""
    if model_id < 1: raise ValueError("model 0 is a placeholder with no factory model")
    set_model(iso, model_id, model_id - 1)

def model_info(iso_path, file):
    """Structure summary of a field model in DATA.PAK — bone count, geometry sizes and
    animation count, read straight out of its RenderWare clump. Two models with the same
    bone count and matching geometry sizes are almost always the same character redressed,
    which is how you tell a story alternate (pc401c) from an unrelated model."""
    name = file.split(":")[-1].upper()
    if not name.endswith(".ROM"): name += ".ROM"
    _, data, codec = _datapak_read(iso_path, name)
    try: return model_info_data(data, name)
    except ValueError: raise ValueError(f"{name} is not a model "
                                        f"(tag {data[:4].hex()}, codec {codec})")

def model_info_data(data, name="model"):
    """model_info over already-decoded bytes."""
    if data[:4] != b"\x00swr":
        raise ValueError(f"{name} is not a model (tag {data[:4].hex()})")
    bones, geos, anims = 0, [], 0
    for start, end in _rws_blocks(data):
        off = start
        while off + 12 <= end:
            t, sz, _v = struct.unpack_from("<III", data, off)
            if off + 12 + sz > end: break
            if t == 0x1B: anims += 1                        # animation
            elif t == 0x10:                                 # clump: struct, framelist, geometrylist
                c = off + 12
                _t, s, _v = struct.unpack_from("<III", data, c); c += 12 + s
                _t, s, _v = struct.unpack_from("<III", data, c)          # frame list
                bones = struct.unpack_from("<I", data, c + 24)[0]        # its struct's count
                c += 12 + s
                _t, sg, _v = struct.unpack_from("<III", data, c)         # geometry list
                g = c + 12
                _t, s, _v = struct.unpack_from("<III", data, g); g += 12 + s   # count struct
                while g < c + 12 + sg:
                    _t, s, _v = struct.unpack_from("<III", data, g)
                    geos.append(s); g += 12 + s
            off += 12 + sz
    return {"file": name, "size": len(data), "bones": bones,
            "geometries": len(geos), "geometry_sizes": geos, "animations": anims}

def _rws_blocks(data):
    """(start, end) of each RenderWare chunk stream in a model file. A .ROM holds several
    back-to-back 'rws' sub-blocks (clump first, then the animation banks); each has its own
    little header whose size sits at +8, with the payload 8 bytes past that."""
    heads = []
    for i in range(0, len(data) - 12, 4):
        if data[i:i+4] != b"\x00swr": continue
        hdr = struct.unpack_from("<I", data, i + 8)[0]
        if 0x20 <= hdr <= 0x100 and i + hdr + 20 <= len(data): heads.append((i, i + hdr + 8))
    return [(p, heads[k+1][0] if k + 1 < len(heads) else len(data))
            for k, (_h, p) in enumerate(heads)]


# When False, no .bak copies are made before writes (toggle in the UI / persisted state).
BACKUPS = True

def backup(path):
    if not BACKUPS: return None
    bak = path + ".bak"
    if not os.path.exists(bak): shutil.copy2(path, bak)
    return bak


# ---- CSV export/import (Excel round-trip) -------------------------------------
# Every dataset is (record id, field label, int value) through the SAME validated
# writers the UI uses, so imports get range checks, .bak and recipe journaling for
# free. Import writes only cells that differ; blank/non-numeric cells are skipped.
CSV_DATASETS = {
    "char-stats":       "Characters — stats & growths",
    "char-affinities":  "Characters — elemental affinities (0=None..6=S)",
    "char-skillcaps":   "Characters — equipable-skill caps (0=None..7=SS)",
    "char-weapon":      "Weapon records: growth by weapon id (sharpen Lv1-16 attack)",
    "char-equipment":   "Characters — starting equipment (armor ids)",
    "enemies":          "Enemies — stats/rewards/affinities/drops",
    "spells":           "Spells — element / power / target / status",
    "runes":            "Runes — which spells each rune teaches",
    "skillfx":          "Skill effects — magnitude per rank (E..SS)",
    "mp":               "MP growth — thresholds per magic level",
    "gear-head":        "Gear - Head pieces",
    "gear-body":        "Gear - Body pieces",
    "gear-arm":         "Gear - Arm pieces",
    "gear-foot":        "Gear - Foot pieces",
    "gear-accessory":   "Gear - Accessories",
}
_CSV_CHAR_TABLE = {"char-stats": "stats", "char-affinities": "affinities",
                   "char-skillcaps": "equipable skills", "char-weapon": "weapon growth",
                   "char-equipment": "starting equipment"}
# Gear sheets are keyed by EQUIP id (the number the Gear tab shows, issue #23),
# which is the engine's stat id + 1; _csv_rows adds it, csv_import subtracts it.
_CSV_GEAR = {"gear-head": "head", "gear-body": "body", "gear-arm": "arm",
             "gear-foot": "foot", "gear-accessory": "accessory"}

def csv_legends():
    """dataset -> notes explaining the columns that hold a CODE rather than a plain
    number. Built from the engine's own maps so the sheet's legend can't drift from
    what the pickers in the tabs offer."""
    codes = lambda m: ", ".join(f"{k}={v}" for k, v in sorted(m.items()))
    out = {
        "char-weapon": [
            "The id column is the WEAPON RECORD id (0-81), not a character id: "
            "each character's Weapon id (Starting equipment) picks its record, "
            "records are shared, and one edit changes every character listed "
            "in the row's name. Record 0 is the empty no-weapon record.",
        ],
        "char-equipment": [
            "Weapon id must be 0-81 (0 = no weapon record); it is validated, "
            "not capped. Starting level: 0 builds as level 1. Most characters "
            "with 0 are set to a scaled level by event scripts when they appear, "
            "so 0 is not the level they join at.",
        ],
        "spells": [
            "Element: " + codes(F.ELEMENT_NAMES),
            "Target: " + codes(F.TARGET_NAMES),
            "Status: " + codes(F.SPELL_STATUS_NAMES),
            f"Power / heal holds 0-65535; 9999 on a healing spell means a full heal. "
            f"The id column is the spell id used by the Runes sheet (0-{F.SPELL_COUNT - 1}).",
        ],
        "runes": [
            "Start spell is a spell id from the Spells sheet — export that table too to "
            "see which id is which.",
            "A rune teaches the CONTIGUOUS run Start spell … Start spell + Spell count - 1, "
            "one spell per rune level, so Spell count is also how many levels it has.",
            "Only runes with a grant record of their own are listed; the Runes tab's "
            "“fixed spell set” entries have no record to write.",
        ],
    }
    gear = [
        "The id column is the equip id the Gear tab shows (the internal stat id + 1).",
        "Type: 1=Light, 2=Medium, 3=Heavy for armor pieces; accessories use their own "
        "set (1=Cape .. 6=Ring).",
        "The stat-bonus columns (HP, Attack, Technique, Magic, Evasion, MDEF, Speed, "
        "Luck) and the element ATK/DEF columns are SIGNED: a negative value is a real "
        "penalty on the disc and imports as-is (range -128..127).",
        "Prices, DEF, SPD penalty and the % effect columns are unsigned.",
    ]
    for ds in _CSV_GEAR:
        out[ds] = gear
    return out

def _csv_rows(iso_path, dataset):
    """-> (id_headers, field_labels, rows) where each row = [ids..., name, values...]."""
    with Iso(iso_path) as g:
        if dataset in _CSV_CHAR_TABLE:
            t = _CSV_CHAR_TABLE[dataset]
            labels = [l for (l, o, w, k) in F.TABLES[t][2]]
            rows = []
            if t == "weapon growth":
                # Keyed by WEAPON RECORD id (issue #22): one row per record
                # 0..81, named by its SJIS type name. Record 0 is the
                # all-zero "no weapon" record, kept so the sheet covers the
                # whole table.
                for wid in range(F.WEAPON_RECORD_COUNT):
                    rec = read_weapon_record(g, wid)
                    nm = rec["typeName"] or ("Weapon record %d" % wid)
                    users = ", ".join(u["name"] for u in rec["users"][:4])
                    if len(rec["users"]) > 4: users += ", ..."
                    rows.append([wid, nm + ((" (%s)" % users) if users else "")] + rec["growth"])
                return ["id"], labels, rows
            for c in F.load_characters():
                vals = [f["value"] for f in read_table(g, t, c["id"])]
                rows.append([c["id"], c["name"]] + vals)
            return ["id"], labels, rows
        if dataset == "enemies":
            try: names = F.res_json("s5_enemy_names.json")
            except Exception: names = {}
            labels = [l for (l, o, w, k) in F.ENEMY_FIELDS]
            rows = []
            for e in read_enemies(g):
                vals = [f["value"] for f in read_enemy(g, e["id"])]
                rows.append([e["id"], names.get(str(e["id"]), e["name"])] + vals)
            return ["id"], labels, rows
        if dataset == "spells":
            try: names = F.res_json("s5_spell_names.json")
            except Exception: names = []
            labels = [l for (l, o, w, k) in F.SPELL_FIELDS]
            rows = []
            for sid in range(F.SPELL_COUNT):
                nm = (names[sid] if sid < len(names) else None) or f"Spell {sid}"
                rows.append([sid, nm] + [f["value"] for f in read_spell(g, sid)])
            return ["id"], labels, rows
        if dataset == "runes":
            labels = [l for (l, o, w, k) in F.RUNE_GRANT_FIELDS]
            rows = []
            for r in read_runes(g):
                # Synthetic runes are a UI convenience for spells no grant record owns;
                # there is nothing on the disc to write back, so they stay out of the sheet.
                if r.get("synthetic"): continue
                rows.append([r["id"], r["name"]] + [f["value"] for f in read_rune(g, r["id"])])
            return ["id"], labels, rows
        if dataset == "skillfx":
            rows = [[r["id"], r["name"]] + r["values"] for r in read_skillfx(g)]
            return ["id"], list(F.SKILLFX_RANKS), rows
        if dataset in _CSV_GEAR:
            slot = _CSV_GEAR[dataset]
            labels = [l for (l, o, w, s) in F.ARMOR_FIELDS]
            rows = []
            for it in list_armor(g, slot):
                vals = [f["value"] for f in read_armor_item(g, slot, it["id"])["fields"]]
                rows.append([it["id"] + 1, it["name"]] + vals)   # equip id (issue #23)
            return ["id"], labels, rows
        if dataset == "mp":
            rows = [[r["group"], r["label"]] + r["values"] for r in read_mp(g)]
            return ["id"], list(F.MP_FIELD_LABELS), rows
    raise KeyError(f"unknown dataset {dataset!r}")

def csv_export(iso_path, dataset):
    import csv, io
    idh, labels, rows = _csv_rows(iso_path, dataset)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(idh + ["name"] + labels)
    for r in rows: w.writerow(r)
    return f"s5_{dataset}.csv", buf.getvalue()

def _csv_field_widths(dataset):
    """label -> storage width in bytes, so an import can CAP a too-big value instead of
    failing that write. Doubling or tripling a column is the common bulk edit and every
    one of these fields is a fixed-width integer, so "as high as this field goes" beats
    an error that leaves the rest of the sheet half-applied."""
    if dataset in _CSV_CHAR_TABLE:
        return {l: w for (l, o, w, k) in F.TABLES[_CSV_CHAR_TABLE[dataset]][2]}
    if dataset == "enemies":
        return {l: w for (l, o, w, k) in F.ENEMY_FIELDS}
    if dataset == "spells":
        return {l: w for (l, o, w, k) in F.SPELL_FIELDS}
    if dataset == "runes":
        return {l: w for (l, o, w, k) in F.RUNE_GRANT_FIELDS}
    if dataset == "skillfx":
        return {r: 2 for r in F.SKILLFX_RANKS}
    if dataset == "mp":
        return {l: 2 for l in F.MP_FIELD_LABELS}
    if dataset in _CSV_GEAR:
        return {l: w for (l, o, w, s) in F.ARMOR_FIELDS}
    return {}

def _csv_field_ranges(dataset):
    """label -> (min, max) for SIGNED fields. The generic import caps a value to
    0..(2^bits - 1); a signed gear byte holding -5 (a real Speed penalty on the
    disc) would be capped to 0 and silently lose the penalty, so signed fields
    cap to their own two's-complement range instead. Datasets without signed
    fields keep the unsigned behavior exactly."""
    if dataset in _CSV_GEAR:
        return {l: (-(1 << (8 * w - 1)), (1 << (8 * w - 1)) - 1)
                for (l, o, w, s) in F.ARMOR_FIELDS if s}
    return {}

def _csv_code_columns(dataset):
    """label -> {code: name} for columns that hold a CODE, not a quantity. Capping one of
    these the way a stat is capped would silently write a DIFFERENT element or target, so
    an unknown code is refused instead (the rest of the sheet still applies)."""
    if dataset == "spells":
        return {"Element": F.ELEMENT_NAMES, "Target": F.TARGET_NAMES,
                "Status": F.SPELL_STATUS_NAMES}
    if dataset == "runes":
        # Ids past the end of the spell table make the rune teach whatever follows it.
        return {"Start spell": {i: None for i in range(F.SPELL_COUNT)}}
    return {}

def _csv_codes_hint(codes):
    ks = sorted(codes)
    if len(ks) > 16 or any(codes[k] is None for k in ks):
        return f"valid values are {ks[0]}-{ks[-1]}"
    return "valid values: " + ", ".join(f"{k}={codes[k]}" for k in ks)

def csv_import(iso_path, dataset, csv_text, make_backup=True):
    import csv, io
    idh, labels, cur_rows = _csv_rows(iso_path, dataset)
    current = {r[0]: dict(zip(labels, r[2:])) for r in cur_rows}
    widths = _csv_field_widths(dataset)
    rdr = csv.reader(io.StringIO(csv_text.lstrip("\ufeff")))    # Excel writes a BOM
    try: header = next(rdr)
    except StopIteration: return {"error": "empty CSV"}
    header = [h.strip().lstrip("\ufeff") for h in header]
    if not header or header[0].lower() != "id":
        return {"error": "first column must be 'id' (export a CSV first to see the format)"}
    colmap = {i: h for i, h in enumerate(header) if h in labels}
    if not colmap:
        return {"error": "no known field columns found — headers must match the exported CSV"}
    # Tables share stat names (HP/Attack/Speed/…), so a sheet exported from one table
    # imports "successfully" into another and quietly writes nonsense — enemy HP into a
    # character's u8 HP, say. Whichever table the columns match best wins; ties go to the
    # table the caller asked for, so a trimmed-down sheet still imports.
    owner = max(CSV_DATASETS, key=lambda ds: (len(set(header) & set(_csv_field_widths(ds))),
                                              ds == dataset))
    if owner != dataset:
        return {"error": f"these columns are the “{CSV_DATASETS[owner]}” table, not "
                         f"“{CSV_DATASETS[dataset]}” — nothing was written. "
                         f"Pick “{CSV_DATASETS[owner]}” in the Table list and import again."}
    codecols = _csv_code_columns(dataset)
    ranges = _csv_field_ranges(dataset)
    # A code the disc itself uses is legitimate even where the legend doesn't name it, and
    # undoing an edit has to stay possible, so each coded column also accepts every value
    # currently in that column.
    allowed_vals = {lab: set(codes) | {r.get(lab) for r in current.values()}
                    for lab, codes in codecols.items()}
    changed = skipped = clamped = 0; errors = []; clamps = []
    if make_backup: backup(iso_path)
    with Iso(iso_path, writable=True) as g:
        for ln, row in enumerate(rdr, start=2):
            if not row or not row[0].strip(): continue
            try: rid = int(row[0])
            except ValueError:
                errors.append(f"line {ln}: bad id {row[0]!r}"); continue
            cur = current.get(rid)
            if cur is None:
                # Enemy ids address a fixed-stride table, so every slot in range is
                # writable even when its record isn't in the detected enemy list.
                if dataset == "enemies" and 0 <= rid < F.ENEMY_MAX:
                    cur = {f["label"]: f["value"] for f in read_enemy(g, rid)}
                else:
                    errors.append(f"line {ln}: unknown id {rid}"); continue
            for ci, label in colmap.items():
                if ci >= len(row): continue
                cell = row[ci].strip()
                if cell == "": skipped += 1; continue
                try: val = int(float(cell))   # Excel may emit "12.0"
                except ValueError: skipped += 1; continue
                # An untouched cell is a no-op whatever it holds, so settle that first:
                # re-importing an unedited sheet can then never trip the guards below,
                # even where the disc itself holds a code the legend doesn't list.
                if cur.get(label) == val: continue
                allowed = allowed_vals.get(label)
                if allowed is not None and val not in allowed:
                    errors.append(f"line {ln} {label}={cell}: unknown code "
                                  f"({_csv_codes_hint(codecols[label])})")
                    continue
                lo, hi = ranges.get(label, (0, (1 << 8 * widths.get(label, 2)) - 1))
                if not (lo <= val <= hi):
                    capped = max(lo, min(hi, val))
                    clamped += 1
                    if len(clamps) < 20:
                        clamps.append(f"line {ln} {label}: {val} -> {capped} (field holds {lo}-{hi})")
                    val = capped
                    if cur.get(label) == val: continue
                try:
                    if dataset == "char-weapon":
                        # Rows are weapon record ids, not character ids.
                        write_weapon_record(g, rid, label, val)
                    elif dataset in _CSV_CHAR_TABLE:
                        write_field(g, _CSV_CHAR_TABLE[dataset], rid, label, val)
                    elif dataset == "enemies":
                        write_enemy_field(g, rid, label, val)
                    elif dataset == "spells":
                        write_spell_field(g, rid, label, val)
                    elif dataset == "runes":
                        write_rune_field(g, rid, label, val)
                    elif dataset == "skillfx":
                        write_skillfx(g, rid, labels.index(label), val)
                    elif dataset == "mp":
                        write_mp(g, rid, labels.index(label), val)
                    elif dataset in _CSV_GEAR:
                        write_armor_field(g, _CSV_GEAR[dataset], rid - 1, label, val)
                    changed += 1
                except Exception as e:
                    errors.append(f"line {ln} {label}={cell}: {e}")
    return {"changed": changed, "skippedCells": skipped, "errors": errors[:20],
            "errorCount": len(errors), "clamped": clamped, "clamps": clamps}

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


# ---- Character growth scaler (issue #13): the other half of the stats table.
# Hard Mode above scales the 9 STARTING stats and deliberately leaves the 9
# per-level growth fields (offsets 9-17) alone, which is exactly what #13 asks
# to be able to scale. Same baseline-sidecar discipline: originals are stored
# once, re-applying scales the ORIGINALS instead of compounding, Restore exact.
_GS_STATS = [f for f in F.TABLES["stats"][2] if f[3] == "num" and f[1] >= 9]  # 9 growth fields @off 9-17 (u8)

def _gs_sidecar(iso_path): return iso_path + ".growthscale.json"

def growth_scale(iso_path, factor):
    """Multiply all characters' per-level stat growths by `factor` (0.1..10).
    Stores a baseline on first apply. Returns count of characters touched."""
    factor = float(factor)
    if not (0.1 <= factor <= 10): raise ValueError("factor must be between 0.1 and 10")
    chars = [c["id"] for c in F.load_characters()]
    side = _gs_sidecar(iso_path)
    base = {}
    if os.path.exists(side):
        base = _json.load(open(side))
    backup(iso_path)
    with Iso(iso_path, writable=True) as g:
        for cid in chars:
            a = table_addr("stats", cid); key = str(cid)
            if key not in base:
                base[key] = [g.ru(a + off, w) for (_, off, w, _k) in _GS_STATS]
            for i, (_, off, w, _k) in enumerate(_GS_STATS):
                v = int(base[key][i] * factor)
                g.wu(a + off, w, max(0, min((1 << 8*w) - 1, v)))
    _json.dump(base, open(side, "w"))
    return len(chars)

def growth_scale_restore(iso_path):
    side = _gs_sidecar(iso_path)
    if not os.path.exists(side): return 0
    base = _json.load(open(side))
    with Iso(iso_path, writable=True) as g:
        for key, vals in base.items():
            a = table_addr("stats", int(key))
            for i, (_, off, w, _k) in enumerate(_GS_STATS):
                g.wu(a + off, w, vals[i])
    os.remove(side)
    return len(base)


# ---- Enemy scaler: the same baseline trick, applied to every enemy's COMBAT stats.
# Rewards (potch / skill points), elemental affinities and item drops are deliberately
# left alone — scaling those changes the economy, not the difficulty. Each stat is u16,
# so a big multiplier caps at 65535 rather than failing the write.
_ES_STATS = [f for f in F.ENEMY_FIELDS if f[0] in
             ("HP", "Attack", "Technique", "Accuracy", "Magic",
              "Evasion", "PDF", "MDF", "Speed", "Luck")]

def _es_sidecar(iso_path): return iso_path + ".enemyscale.json"

def enemy_scale(iso_path, factor, hp_factor=None):
    """Multiply every enemy's combat stats by `factor`, HP by `hp_factor` (defaults to
    `factor`). Baseline is stored once, so re-applying scales the ORIGINALS instead of
    compounding and Restore is exact. -> {count, clamped, repaired}.

    Ids outside the enemy table are never written. A sidecar left by a build that scaled
    past the end of the table (the old F.ENEMY_MAX = 584) still holds those slots' real
    bytes, so this puts them back and forgets them — scaling again repairs the disc
    instead of re-scaling ELF data that was never an enemy."""
    factor = float(factor)
    hp_factor = float(factor if hp_factor is None else hp_factor)
    for f in (factor, hp_factor):
        if not (0.1 <= f <= 20): raise ValueError("factor must be between 0.1 and 20")
    side = _es_sidecar(iso_path)
    base = {}
    if os.path.exists(side):
        try: base = _json.load(open(side))
        except Exception: base = {}
    backup(iso_path)
    clamped = repaired = 0
    with Iso(iso_path, writable=True) as g:
        ids = sorted({int(k) for k in base} | {e["id"] for e in read_enemies(g)})
        for eid in ids:
            a = enemy_addr(eid); key = str(eid)
            if not (0 <= eid < F.ENEMY_MAX):
                old = base.pop(key, None)                          # stale sidecar: undo it
                if old and len(old) == len(_ES_STATS):
                    for i, (_l, off, w, _k) in enumerate(_ES_STATS): g.wu(a + off, w, old[i])
                    repaired += 1
                continue
            if key not in base:
                base[key] = [g.ru(a + off, w) for (_l, off, w, _k) in _ES_STATS]
            for i, (lbl, off, w, _k) in enumerate(_ES_STATS):
                lim = (1 << 8*w) - 1
                v = int(base[key][i] * (hp_factor if lbl == "HP" else factor))
                if v > lim: clamped += 1
                g.wu(a + off, w, max(0, min(lim, v)))
    _json.dump(base, open(side, "w"))
    return {"count": len(base), "clamped": clamped, "repaired": repaired}

def enemy_scale_restore(iso_path):
    side = _es_sidecar(iso_path)
    if not os.path.exists(side): return {"count": 0}
    base = _json.load(open(side))
    with Iso(iso_path, writable=True) as g:
        for key, vals in base.items():
            a = enemy_addr(int(key))
            for i, (_l, off, w, _k) in enumerate(_ES_STATS):
                g.wu(a + off, w, vals[i])
    os.remove(side)
    return {"count": len(base)}


# ---- Global drop rates (issue #40): the rates are constants in the battle
# code, not record bytes (lab: ENEMY_DROP_BLOCK.md). The five slot rates and
# the food dish rate are addiu immediates in the BATLE overlay's drop
# processor; the Cook gate chances are data in the support-skill table
# (record 0x98, read by the settlement through func_2E2910). Editing them is
# GLOBAL: one set of rates for every enemy. The registration code clamps
# every rate to 99, so 99 is the effective ceiling; the dish's stock
# immediate is 100, which plays identically to 99.
DROP_RATE_STOCK = (40, 20, 10, 5, 1)     # slots 1..5, by slot position
DROP_DISH_STOCK = 100                    # slot 6 registration immediate
DROP_COOK_STOCK = (20, 30)               # Cook record ranks 6 and 7
# Support-skill table ISO offsets by region (ELF 0x501208 NTSC-U, 0x506BA8
# PAL; both proven: the three records' rank columns match the traced values,
# and each region's own BATLE code loads exactly this table address).
_SKILL_TABLE_ISO = {"ntsc-u": 0x4AEB08, "pal": 0x4B44A8}
_SKILL_TD_A = (3, 5, 10, 15, 20, 25, 30)  # record 0x90 ranks 1..7 (signature)
_SKILL_TD_B = (0, 0, 0, 3, 5, 8, 10)      # record 0x91 ranks 1..7 (signature)
_SKILL_COOK_REC = 0x98


def _skill_rank_iso(table_iso, rec, rank):
    """ISO offset of one rank column: func_2E2910 reads the s16 at
    table + rec*36 + rank*2 + 0x12 (rank 1..7)."""
    return table_iso + rec * 36 + rank * 2 + 0x12


def _cook_offsets(iso):
    """(rank6_off, rank7_off) of the Cook award chances on this disc, after
    validating the skill table's layout against the two Treasure Discovery
    records (which this feature never writes, so they are a pure signature)
    and the Cook record's untouched ranks 1..5. Raises ValueError on a disc
    whose table does not match the traced layout."""
    region = region_of(iso)
    if region not in _SKILL_TABLE_ISO:
        raise ValueError("unrecognized disc: no skill table base for region %r"
                         % (region,))
    base = _SKILL_TABLE_ISO[region]
    for rec, want in ((0x90, _SKILL_TD_A), (0x91, _SKILL_TD_B)):
        got = tuple(iso.ru(_skill_rank_iso(base, rec, k), 2) for k in range(1, 8))
        if got != want:
            raise ValueError("skill table layout not recognised "
                             "(record 0x%02X ranks %s)" % (rec, got))
    low = tuple(iso.ru(_skill_rank_iso(base, _SKILL_COOK_REC, k), 2)
                for k in range(1, 6))
    if low != (0, 0, 0, 0, 0):
        raise ValueError("Cook record layout not recognised "
                         "(ranks 1..5 are %s, expected all zero)" % (low,))
    return (_skill_rank_iso(base, _SKILL_COOK_REC, 6),
            _skill_rank_iso(base, _SKILL_COOK_REC, 7))


def read_cook_chances(iso):
    """[rank6, rank7] Cook award chances (percent) on this disc."""
    o6, o7 = _cook_offsets(iso)
    vals = [iso.ru(o6, 2), iso.ru(o7, 2)]
    if any(v > 99 for v in vals):
        raise ValueError("Cook chances on this disc are out of range: %s"
                         % (vals,))
    return vals


# Rate sites in the DECOMPRESSED BATLE overlay: (slot, immediate offset,
# expected top halfword of the addiu, pair-address addiu offset and word,
# registration jal offset). The pair word encodes which record slot the call
# registers (U-frame +0x4C/+0x54/+0x5C/+0x64/+0x6C/+0x74), so the six sites
# are identified structurally, not by the immediate values (which this
# feature itself rewrites). Proven on both regions' stock overlays: the
# rate instructions sit at the same offsets with the same shapes; only the
# jal targets and table addresses around them differ by region.
_DROP_SITES = (
    (1, 0xF718, 0x2602, 0xF728, 0x2684004C, 0xF72C),
    (2, 0xF6F0, 0x2602, 0xF6F4, 0x26840054, 0xF704),
    (3, 0xF6CC, 0x2602, 0xF6C8, 0x2684005C, 0xF6DC),
    (4, 0xF69C, 0x2602, 0xF6A0, 0x26840064, 0xF6B0),
    (5, 0xF770, 0x2602, 0xF780, 0x2684006C, 0xF788),
    (6, 0xF738, 0x2405, 0xF734, 0x26840074, 0xF744),
)


# Display names for the .ROM container forms, keyed by rom_codec's names:
# the canonical names are s5rom.TYPES' (this module calls the byte-pair
# codec "bpe" and the Huffman codec "ffh"; users know them as epb and hff).
_BATLE_FORM_NAMES = {"non": "non", "szl": "szl", "bpe": "epb", "ffh": "hff"}


def _batle_bin(iso):
    """The decompressed BATLE overlay bytes from this disc, in memory.
    Stock discs store the overlay as an szl (LZSS) container, but rebuilt
    discs (the undub patch, some rips) may store it as a non (stored) or
    epb (byte-pair) container instead; all three forms are read. Any other
    form is refused with the form named, never guessed at."""
    ov = next((o for o in list_overlays(iso.path) if o["name"] == "BATLE.ROM"),
              None)
    if ov is None:
        raise ValueError("this disc has no BATLE.ROM overlay")
    raw = iso.rd(ov["lba"] * _SECT, ov["size"])
    if raw[0:4] != b"\x00mor":
        raise ValueError("BATLE.ROM is not in the expected container form")
    codec = rom_codec(raw)
    dec = int.from_bytes(raw[0x0C:0x10], "little")
    if codec == "szl":
        return _lzss_decompress(raw[0x40:], dec)
    if codec == "non":
        return bytes(raw[0x40:0x40 + dec])
    if codec == "bpe":
        return _bpe_decompress(raw[0x40:], dec)
    name = _BATLE_FORM_NAMES.get(codec)
    if name is not None:
        raise ValueError("BATLE.ROM on this disc is stored in %s form, "
                         "which the drop-rate editor cannot read" % name)
    raise ValueError("BATLE.ROM on this disc has an unrecognised container "
                     "form (type word 0x%08X), which the drop-rate editor "
                     "cannot read" % int.from_bytes(raw[8:12], "little"))


def _drop_site_rates(data):
    """Validate the drop processor's six rate sites in a decompressed BATLE
    overlay and return {slot: current immediate}. Raises ValueError if the
    code shape is not the traced one (a foreign or differently-built
    overlay is never written over)."""
    out = {}
    for slot, off, top, poff, pword, joff in _DROP_SITES:
        w = int.from_bytes(data[off:off + 4], "little")
        if w >> 16 != top:
            raise ValueError("drop-rate site for slot %d not recognised "
                             "(word 0x%08X at overlay 0x%X)" % (slot, w, off))
        if int.from_bytes(data[poff:poff + 4], "little") != pword:
            raise ValueError("drop processor layout not recognised "
                             "(slot %d pair instruction)" % slot)
        jw = int.from_bytes(data[joff:joff + 4], "little")
        if jw >> 26 != 0x03:                       # jal opcode
            raise ValueError("drop processor layout not recognised "
                             "(slot %d registration call)" % slot)
        imm = w & 0xFFFF
        if imm >= 0x8000:
            imm -= 0x10000
        if not 0 <= imm <= 100:
            raise ValueError("drop rate for slot %d on this disc is out of "
                             "range: %d" % (slot, imm))
        out[slot] = imm
    return out


def read_drop_rates(iso):
    """Current global drop rates on this disc:
    {region, slots: [1..5], dish, cook: [rank6, rank7], stock: {...}}."""
    rates = _drop_site_rates(_batle_bin(iso))
    return {"region": region_of(iso),
            "slots": [rates[s] for s in range(1, 6)],
            "dish": rates[6],
            "cook": read_cook_chances(iso),
            "stock": {"slots": list(DROP_RATE_STOCK),
                      "dish": DROP_DISH_STOCK,
                      "cook": list(DROP_COOK_STOCK)}}


def write_drop_rates(iso, slots, dish, cook):
    """Write global drop rates: five slot rates and the Cook chances 0..99,
    the dish rate 0..100 (its stock value; the game clamps rolls at 99).
    The BATLE overlay is rewritten through reinsert_overlay (which refuses
    if the recompressed container outgrows its sector slot) BEFORE the Cook
    bytes are touched, so a refused overlay write changes nothing. Every
    span is re-read and verified after writing."""
    slots = [int(v) for v in slots]
    cook = [int(v) for v in cook]
    dish = int(dish)
    if len(slots) != 5 or any(not 0 <= v <= 99 for v in slots):
        raise ValueError("slot rates must be five values from 0 to 99")
    if len(cook) != 2 or any(not 0 <= v <= 99 for v in cook):
        raise ValueError("Cook chances must be two values from 0 to 99")
    if not 0 <= dish <= 100:
        raise ValueError("the dish rate must be from 0 to 100")
    read_drop_rates(iso)                    # layout validation before any write
    backup(iso.path)
    data = bytearray(_batle_bin(iso))
    want = {1: slots[0], 2: slots[1], 3: slots[2], 4: slots[3], 5: slots[4],
            6: dish}
    for slot, off, top, _poff, _pword, _joff in _DROP_SITES:
        data[off:off + 4] = ((top << 16) | want[slot]).to_bytes(4, "little")
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="s5_batle_", suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(bytes(data))
        res = reinsert_overlay(iso, "BATLE.ROM", tmp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    o6, o7 = _cook_offsets(iso)
    iso.wu(o6, 2, cook[0])
    iso.wu(o7, 2, cook[1])
    now = read_drop_rates(iso)
    if now["slots"] != slots or now["dish"] != dish or now["cook"] != cook:
        raise AssertionError("drop-rate write did not verify: %r" % (now,))
    return {"ok": True, "slots": slots, "dish": dish, "cook": cook,
            "overlaySlack": res["slack"]}


def restore_drop_rates(iso):
    """Restore the stock global drop rates (40/20/10/5/1, dish 100,
    Cook 20/30). Exact at the value level: the stock rates are constants."""
    return write_drop_rates(iso, list(DROP_RATE_STOCK), DROP_DISH_STOCK,
                            list(DROP_COOK_STOCK))


# ---- Gear equip requirements (issue #49): the Corruption Shell (equip id
# 70) and the Saint's Cloak (equip id 71) are the only Type 6 body armors,
# and Type 6 routes to a special gate inside the shared can-equip
# predicate func_0038A4B0 instead of the generic class-allowance check.
# The requirements are NOT in the armor records (a full-record scan finds
# no threshold values anywhere); they are four slti immediates in that
# predicate:
#   Shell: pass iff max HP >= imm AND kills >= imm
#   Cloak: pass iff max HP >= imm AND kills < imm
# "HP" is MAX HP (the gate loads the live record field at +0x14); the
# kill count is the per-character u16 at live record +0x148. Proven in
# game at the exact boundaries, and with tweak and disable ISOs carrying
# edited immediates (lab: GEAR_REQUIREMENTS.md). The Cloak's cap is
# "kills < imm", so its field shows a maximum-kills value of imm - 1:
# stock imm 31 displays as 30 (the item description's "under 30" is off
# by one from the code; exactly 30 kills can equip the Cloak).
GEAR_REQ_TOP = 0x2842                    # slti $v0, $v0, imm: top halfword
GEAR_REQ_FIELDS = [
    # (field, label, stock imm, display shift, max display value,
    #  {region: ISO offset of the gate word})
    ("shell_hp", "Corruption Shell: minimum max HP", 350, 0, 0x7FFF,
     {"ntsc-u": 0x337ECC, "pal": 0x338C9C}),
    ("shell_kills", "Corruption Shell: minimum kills", 100, 0, 0x7FFF,
     {"ntsc-u": 0x337EE8, "pal": 0x338CB8}),
    ("cloak_hp", "Saint's Cloak: minimum max HP", 300, 0, 0x7FFF,
     {"ntsc-u": 0x337F24, "pal": 0x338CF4}),
    ("cloak_kills", "Saint's Cloak: maximum kills", 31, -1, 0x7FFE,
     {"ntsc-u": 0x337F44, "pal": 0x338D14}),
]
# The gear-requirements-off toggle's patched immediates: the >= compares
# pass at 0, and the Cloak's < compare passes below 0x7FFF (the kill
# counter is u16 and real counts never approach it).
GEAR_REQ_OFF_IMMS = {"shell_hp": 0, "shell_kills": 0, "cloak_hp": 0,
                     "cloak_kills": 0x7FFF}


def _gear_req_spec(field):
    for spec in GEAR_REQ_FIELDS:
        if spec[0] == field:
            return spec
    raise KeyError("unknown gear requirement field %r" % (field,))


def _gear_req_offset(iso, field):
    region = region_of(iso)
    offs = _gear_req_spec(field)[5]
    if region not in offs:
        raise ValueError("unrecognized disc: no gear requirement sites "
                         "for region %r" % (region,))
    return offs[region]


def _gear_req_word(iso, field):
    """(offset, word) of one gate instruction, after checking the word
    really is the traced slti: a disc whose code shape differs is never
    written over."""
    off = _gear_req_offset(iso, field)
    w = iso.ru(off, 4)
    if w >> 16 != GEAR_REQ_TOP:
        raise ValueError("gear requirement site for %s not recognised "
                         "(word 0x%08X at 0x%X)" % (field, w, off))
    return off, w


def read_gear_requirements(iso):
    """The four equip-requirement thresholds on this disc, as display
    values: {region, fields: [{field, label, value, stock, min, max,
    kind, width}]}. The Cloak's maximum kills is the stored immediate
    minus 1 (the gate passes iff kills < imm)."""
    out = []
    for field, label, stock_imm, shift, vmax, _offs in GEAR_REQ_FIELDS:
        _off, w = _gear_req_word(iso, field)
        out.append({"field": field, "label": label,
                    "value": (w & 0xFFFF) + shift,
                    "stock": stock_imm + shift,
                    "min": 0, "max": vmax, "kind": "num", "width": 2})
    return {"region": region_of(iso), "fields": out}


def write_gear_requirement(iso, field, value):
    """Write one equip-requirement threshold (a display value; the
    Cloak's maximum kills is stored as value + 1). The site word is
    anchor-checked before writing and re-read after."""
    _f, label, _stock, shift, vmax, _offs = _gear_req_spec(field)
    v = int(value)
    if not 0 <= v <= vmax:
        raise ValueError("%s must be from 0 to %d" % (label, vmax))
    off, _w = _gear_req_word(iso, field)
    imm = v - shift
    iso.wu(off, 4, (GEAR_REQ_TOP << 16) | imm)
    if iso.ru(off, 4) != (GEAR_REQ_TOP << 16) | imm:
        raise AssertionError("gear requirement write at 0x%X did not "
                             "verify" % off)
    return {"ok": True, "field": field, "value": v, "imm": imm}


def gear_requirement_sites(iso):
    """The four gate words as s5toggles sites (stock forms vs the
    requirements-off patched forms), shared by the Gear view's threshold
    fields and the gear-requirements-off toggle so the two can never
    disagree about where the gates live. An unrecognized region finds
    no sites (the toggle reads not-found); a word that is neither the
    stock nor the patched form keeps its bytes and classifies as
    foreign in the toggle, which then refuses to write."""
    region = region_of(iso)
    out = []
    for field, label, stock_imm, _shift, _vmax, offs in GEAR_REQ_FIELDS:
        if region not in offs:
            return []
        off = offs[region]
        out.append({"label": "%s gate @0x%X" % (label, off),
                    "spans": [{"off": off,
                               "stock": struct.pack(
                                   "<I", (GEAR_REQ_TOP << 16) | stock_imm),
                               "patched": struct.pack(
                                   "<I", (GEAR_REQ_TOP << 16)
                                   | GEAR_REQ_OFF_IMMS[field]),
                               "cur": iso.rd(off, 4)}]})
    return out


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

def _models(a):
    with Iso(a.iso) as g:
        for m in read_models(g):
            if m["placeholder"]: continue
            who = m["char"] + (f" (look {m['variant']})" if m["alternate"] else "")
            mark = "  <- " + m["file"] if m["changed"] else ""
            print(f"  #{m['id']:>3} {m['default_file']:<14} {who}{mark}")
    return 0

def _setmodel(a):
    if a.slot is None and not a.reset:
        raise SystemExit("give --slot N (see `models`) or --reset")
    with Iso(a.iso) as g:
        ids = [a.id] if a.one else model_group(g, a.id)
    backup(a.iso)
    with Iso(a.iso, writable=True) as g:
        for i in ids:
            if a.reset: reset_model(g, i)
            else: set_model(g, i, a.slot)
    with Iso(a.iso) as g: rows = read_models(g)
    for i in ids: print(f"model #{i} -> {rows[i]['file']}")
    return 0

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
    try: ref = F.res_json("s5_reference.json")
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

def _toggles(a):
    import s5toggles as T
    with Iso(a.iso) as g:
        for st in T.list_toggles(g):
            print("%-18s %-10s effect=%s  %s" % (st["id"], st["state"],
                  st["effect"], st["label"]))
    return 0

def _toggle_set(a, on):
    import json
    import s5toggles as T
    backup(a.iso)
    originals = None
    if getattr(a, "originals", None):
        raw = open(a.originals).read() if os.path.exists(a.originals) else a.originals
        originals = json.loads(raw)
    with Iso(a.iso, writable=True) as g:
        r = T.set_toggle(g, a.id, on, originals)
    print(json.dumps(r))
    return 0

def _xdelta_create(a):
    import s5vcdiff
    size, changed = s5vcdiff.create_patch(a.pristine, a.iso, a.out)
    print(f"wrote {a.out}: {size} bytes of patch for {changed} changed bytes "
          f"(turns the pristine disc into this one)")
    return 0

def _xdelta_apply(a):
    import s5vcdiff
    total = s5vcdiff.apply_patch(a.iso, a.patch, a.out)
    print(f"wrote {a.out}: {total} bytes reconstructed from the pristine disc + patch")
    return 0

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
    add("models", _models)
    add("set-model", _setmodel, [(("--id",), dict(type=int, required=True)),
        (("--slot",), dict(type=int)), (("--reset",), dict(action="store_true")),
        (("--one",), dict(action="store_true", help="just this id, not the character's other looks"))])
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
    def _gs(a):
        if a.restore: n = growth_scale_restore(a.iso); print(f"restored {n} chars")
        else: n = growth_scale(a.iso, a.factor); print(f"scaled growths of {n} chars x{a.factor}")
        return 0
    add("growthscale", _gs, [(("--factor",), dict(type=float, default=1.5)), (("--restore",), dict(action="store_true"))])
    add("overlays", _overlays)
    add("overlay-dump", _overlaydump, [(("--name",), dict(required=True)), (("--out",), dict(required=True))])
    add("overlay-set", _overlayset, [(("--name",), dict(required=True)), (("--image",), dict(required=True))])
    add("overlay-verify", _overlayverify)
    add("pak-list", _paklist, [(("--filter",), dict())])
    add("pak-extract", _pakextract, [(("--path",), dict(required=True)), (("--out",), dict(required=True))])
    add("peek", _peek, [(("--off",), dict(required=True)), (("--len",), dict(type=int, default=16))])
    add("toggles", _toggles)
    add("toggle-on", lambda a: _toggle_set(a, True), [(("--id",), dict(required=True))])
    add("toggle-off", lambda a: _toggle_set(a, False), [(("--id",), dict(required=True)),
        (("--originals",), dict())])
    add("xdelta-create", _xdelta_create, [(("--pristine",), dict(required=True)),
        (("--out",), dict(required=True))])
    add("xdelta-apply", _xdelta_apply, [(("--patch",), dict(required=True)),
        (("--out",), dict(required=True))])
    add("poke", _poke, [(("--off",), dict(required=True)), (("--u8",), dict(type=int)),
                        (("--u16",), dict(type=int)), (("--hex",), dict())])
    a = p.parse_args(argv)
    if getattr(a, "iso", None) and os.path.exists(a.iso):
        try: set_region_for(a.iso)  # rebind bases to the ISO's region before any read/write
        except Exception: pass
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
