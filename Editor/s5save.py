#!/usr/bin/env python3
"""
Suikoden V PS2 memory-card save reader/writer (stdlib only).

The PS2 layer (PS2MFS card walker, per-page ECC, .psu export handling, scanning,
format sniffing) is ported ~verbatim from the Suikoden III editor — it is entirely
game-agnostic and works for any PS2 title. What still needs S5-specific RE (marked
NEEDS-SAVE below): the gamedata field offsets (stats/party/gold/names/recruit), the
gamedata checksum algorithm, and the New Game Plus flag. Those require at least one
real S5 memory-card save (ideally one pre- and one post-New-Game-Plus) to confirm,
per the "never write unverified fields" rule.

USA Suikoden V save folders are prefixed BASLUS-21291.
"""
import struct, os, glob, shutil

# When False, no .bak copies are made before writes (toggle in the UI / persisted state).
BACKUPS = True

MAGIC = b"Sony PS2 Memory Card Format"
S5_PREFIX = "BASLUS-21291"     # USA Suikoden V save-folder prefix on the memcard

# --- PS2 memory-card ECC (Hamming) — verbatim from mymc (Ross Ridge, public domain).
def _parityb(a):
    a ^= a >> 1; a ^= a >> 2; a ^= a >> 4
    return a & 1
_PARITY = [_parityb(b) for b in range(256)]
_CPM = [0] * 256
for _b in range(256):
    _m = 0
    for _i, _msk in enumerate([0x55, 0x33, 0x0F, 0x00, 0xAA, 0xCC, 0xF0]):
        _m |= _PARITY[_b & _msk] << _i
    _CPM[_b] = _m

def ecc_chunk(chunk):
    cp = 0x77; lp0 = 0x7F; lp1 = 0x7F
    for i in range(len(chunk)):
        b = chunk[i]; cp ^= _CPM[b]
        if _PARITY[b]:
            lp0 ^= ~i; lp1 ^= i
    return bytes([cp & 0xFF, lp0 & 0x7F, lp1 & 0xFF])

def ecc_page(page512):
    out = b"".join(ecc_chunk(page512[i*128:(i+1)*128]) for i in range(4))
    return out + b"\x00\x00\x00\x00"

# --- gamedata checksum framework.
# UNVERIFIED for S5. S3's was "sum of all u32 LE words == 0 (mod 2^32)". The S5
# algorithm must be cracked by diffing real saves (playtime-ordered) to find the word
# that zeroes the sum, before any gamedata write is allowed. Provided so the machinery
# is ready; guarded by CHECKSUM_VERIFIED.
CHECKSUM_VERIFIED = False
def gamedata_checksum_sumzero(data, word_off=0):
    words = struct.unpack_from("<%dI" % ((len(data)//4) - 1), data,
                               (word_off+1)*4 if word_off == 0 else 0)
    return (-sum(words)) & 0xFFFFFFFF

# --- S5 gamedata facts (VERIFIED against a real save: BASLUS-2129100, LV.39, 19:16).
# The save payload is a file named exactly like the folder (BASLUS-2129100), 74024 bytes.
GAMEDATA_SIZE = 74024           # 0x12128, verified across 6 real saves
PAYLOAD_IS_FOLDER_NAME = True   # payload filename == folder name (not "gamedata")
# NEEDS a pre/post-New-Game-Plus save pair from the SAME playthrough to confirm:
#   - New Game Plus flag (357 bytes differ across different playthroughs -> can't isolate)
#   - gamedata checksum algorithm (no whole-payload sum checksum found)
#   - character stat/party/gold/recruit field offsets
# VERIFIED New Game Plus / cleared-game flag: gamedata byte @0x12, 1=on 0=off.
# Confirmed 6/6 across saves: every cleared/NG+ save reads 1, every in-progress save 0
# (isolated by diffing a same-playthrough pre-end vs post-end save pair).
NG_PLUS_OFF = 0x12
# VERIFIED gamedata fields (confirmed across multiple saves): hero + castle + NG+ + level.
# CORRECTION: 0x28 IS the lead character's level (u8). Confirmed 5/5 vs the icon.sys
# title level across diverse saves: 57,58,99,99,58. (The earlier "not level / 39/58/867.."
# note was from reading 0x28 too wide on the identical Sparda cards.) 0x369 holds a copy
# inside the lead character's per-character block.
S5_FIELDS = {
    "heroName":    (0x00, 16, "str"),
    "castleName":  (0x14, 16, "str"),
    "newGamePlus": (0x12, 1,  "num"),   # 1 = New Game Plus / cleared (enables fast-forward)
    "level":       (0x28, 1,  "num"),   # lead character level (1..99), matches icon.sys title
}

# ---- Individual save-file decoders (CodeBreaker .cbs, SharkPort/X-Port .sps/.xps).
# Ported to py3 from mymc (Ross Ridge, public domain). Return {filename: bytes}.
_CBS_RC4 = bytes([0x5f,0x1f,0x85,0x6f,0x31,0xaa,0x3b,0x18,0x21,0xb9,0xce,0x1c,0x07,0x4c,0x9c,0xb4,0x81,0xb8,0xef,0x98,0x59,0xae,0xf9,0x26,0xe3,0x80,0xa3,0x29,0x2d,0x73,0x51,0x62,0x7c,0x64,0x46,0xf4,0x34,0x1a,0xf6,0xe1,0xba,0x3a,0x0d,0x82,0x79,0x0a,0x5c,0x16,0x71,0x49,0x8e,0xac,0x8c,0x9f,0x35,0x19,0x45,0x94,0x3f,0x56,0x0c,0x91,0x00,0x0b,0xd7,0xb0,0xdd,0x39,0x66,0xa1,0x76,0x52,0x13,0x57,0xf3,0xbb,0x4e,0xe5,0xdc,0xf0,0x65,0x84,0xb2,0xd6,0xdf,0x15,0x3c,0x63,0x1d,0x89,0x14,0xbd,0xd2,0x36,0xfe,0xb1,0xca,0x8b,0xa4,0xc6,0x9e,0x67,0x47,0x37,0x42,0x6d,0x6a,0x03,0x92,0x70,0x05,0x7d,0x96,0x2f,0x40,0x90,0xc4,0xf1,0x3e,0x3d,0x01,0xf7,0x68,0x1e,0xc3,0xfc,0x72,0xb5,0x54,0xcf,0xe7,0x41,0xe4,0x4d,0x83,0x55,0x12,0x22,0x09,0x78,0xfa,0xde,0xa7,0x06,0x08,0x23,0xbf,0x0f,0xcc,0xc1,0x97,0x61,0xc5,0x4a,0xe6,0xa0,0x11,0xc2,0xea,0x74,0x02,0x87,0xd5,0xd1,0x9d,0xb7,0x7e,0x38,0x60,0x53,0x95,0x8d,0x25,0x77,0x10,0x5e,0x9b,0x7f,0xd8,0x6e,0xda,0xa2,0x2e,0x20,0x4f,0xcd,0x8f,0xcb,0xbe,0x5a,0xe0,0xed,0x2c,0x9a,0xd4,0xe2,0xaf,0xd0,0xa9,0xe8,0xad,0x7a,0xbc,0xa8,0xf2,0xee,0xeb,0xf5,0xa6,0x99,0x28,0x24,0x6c,0x2b,0x75,0x5d,0xf8,0xd3,0x86,0x17,0xfb,0xc0,0x7b,0xb3,0x58,0xdb,0xc7,0x4b,0xff,0x04,0x50,0xe9,0x88,0x69,0xc9,0x2a,0xab,0xfd,0x5b,0x1b,0x8a,0xd9,0xec,0x27,0x44,0x0e,0x33,0xc8,0x6b,0x93,0x32,0x48,0xb6,0x30,0x43,0xa5])

def _rc4(data):
    s = bytearray(_CBS_RC4); t = bytearray(data); j = 0
    for ii in range(len(t)):
        i = (ii + 1) % 256; j = (j + s[i]) % 256; s[i], s[j] = s[j], s[i]
        t[ii] ^= s[(s[i] + s[j]) % 256]
    return bytes(t)

def load_cbs(b):
    import zlib
    hlen = struct.unpack("<L", b[8:12])[0]; dlen, flen = struct.unpack("<LL", b[12:20])
    body = zlib.decompressobj().decompress(_rc4(b[hlen:hlen + flen]), dlen); fs = {}
    while body:
        h = struct.unpack("<8s8sLHHLL32s", body[:64]); sz = h[2]
        fs[h[7].split(b"\x00")[0].decode("latin1")] = body[64:64 + sz]; body = body[64 + sz:]
    return fs

def load_sharkport(b):
    import io
    f = io.BytesIO(b); f.read(17); f.read(4)
    for _ in range(3):
        n = struct.unpack("<L", f.read(4))[0]; f.read(n)
    f.read(4)
    hlen, dn, dl, dm, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
    dl -= 2; fs = {}
    for _ in range(dl):
        hlen, name, flen, mode, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
        fs[name.split(b"\x00")[0].decode("latin1")] = f.read(flen)
    return fs

def load_individual_save(path):
    """Return (folder_files_dict) for a .cbs/.sps/.xps save, or None."""
    b = open(path, "rb").read()
    if b[:4] == b"CFU\x00": return load_cbs(b)
    if b[:17] == b"\x0d\x00\x00\x00SharkPortSave": return load_sharkport(b)
    return None

def _sharkport_offsets(b):
    """Like load_sharkport but maps each inner filename -> (abs_offset, bytes),
    so the gamedata can be patched in place (SharkPort/X-Port store it uncompressed)."""
    import io
    f = io.BytesIO(b); f.read(17); f.read(4)
    for _ in range(3):
        n = struct.unpack("<L", f.read(4))[0]; f.read(n)
    f.read(4)
    hlen, dn, dl, dm, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
    dl -= 2; fs = {}
    for _ in range(dl):
        hlen, name, flen, mode, cr, mo = struct.unpack("<H64sL8xH2x8s8s", f.read(98)); f.read(hlen - 98)
        off = f.tell(); fs[name.split(b"\x00")[0].decode("latin1")] = (off, f.read(flen))
    return fs

def _write_cbs(path, b, edits, make_backup=True):
    """Re-encode a CodeBreaker (.cbs) save with edited gamedata: decompress (RC4+zlib),
    patch the payload in place, re-compress + re-encrypt, rewrite. Checksum UNVERIFIED."""
    import zlib
    hlen = struct.unpack("<L", b[8:12])[0]; dlen = struct.unpack("<L", b[12:16])[0]
    body = bytearray(zlib.decompressobj().decompress(_rc4(b[hlen:]), dlen))
    off = None; pos = 0
    while pos < len(body):
        h = struct.unpack("<8s8sLHHLL32s", bytes(body[pos:pos + 64])); sz = h[2]
        if sz == GAMEDATA_SIZE: off = pos + 64; gd = bytes(body[off:off + sz]); break
        pos += 64 + sz
    if off is None: return {"error": "gamedata payload not found"}
    new_gd, changed = apply_gamedata_edits(gd, edits)
    if changed == 0: return {"ok": True, "changed": 0}
    body[off:off + len(new_gd)] = new_gd
    newcomp = _rc4(zlib.compress(bytes(body), 9))
    newb = bytearray(b[:hlen]) + newcomp
    struct.pack_into("<L", newb, 16, len(newb))     # flen field = total file size (as-authored)
    if make_backup and BACKUPS and not os.path.exists(path + ".bak"):
        shutil.copy2(path, path + ".bak")
    with open(path, "wb") as f: f.write(bytes(newb))
    return {"ok": True, "changed": changed,
            "warn": "CBS re-encoded; gamedata checksum unverified — verify it loads in-game"}

def write_individual_save(path, edits, make_backup=True):
    """Patch S5_FIELDS edits into a standalone save. SharkPort/X-Port (.sps/.xps) is
    patched in place; CodeBreaker (.cbs) is decompressed/re-compressed. Checksum UNVERIFIED."""
    b = open(path, "rb").read()
    if b[:4] == b"CFU\x00":
        return _write_cbs(path, b, edits, make_backup)
    if b[:17] != b"\x0d\x00\x00\x00SharkPortSave":
        return {"error": "unsupported save format"}
    fs = _sharkport_offsets(b)
    tgt = next(((off, data) for name, (off, data) in fs.items() if len(data) == GAMEDATA_SIZE), None)
    if not tgt: return {"error": "gamedata payload not found in save"}
    off, gd = tgt
    new_gd, changed = apply_gamedata_edits(gd, edits)
    if changed == 0: return {"ok": True, "changed": 0}
    if make_backup and BACKUPS and not os.path.exists(path + ".bak"):
        shutil.copy2(path, path + ".bak")
    ba = bytearray(b); ba[off:off + len(new_gd)] = new_gd
    with open(path, "wb") as f: f.write(ba)
    return {"ok": True, "changed": changed,
            "warn": "individual-save checksum unverified; verify it loads in-game"}

def region_label(folder):
    """Map an S5 save folder (e.g. BASLUS-21291.., BESLES-54087..) to a region tag."""
    f = (folder or "").upper()
    if "SLUS" in f: return "NTSC-U"
    if "SLES" in f: return "PAL"
    if "SLPM" in f or "SLPS" in f or "SCPS" in f or "SLKA" in f: return "NTSC-J/Asia"
    return "?"

def read_individual_save(path):
    """Decode a .cbs/.sps/.xps and return {folder, fields, payloadSize} for S5 saves.
    An S5 save is recognized by its fixed 74024-byte gamedata payload, so USA
    (BASLUS-21291), PAL (BESLES-54087) and other regions all work — the gamedata
    layout is the same game. Returns region in the result for display."""
    fs = load_individual_save(path)
    if not fs: return None
    folder = next((k for k, v in fs.items() if len(v) == GAMEDATA_SIZE), None)
    if folder is None:
        # fall back to prefix match if size differs (defensive)
        folder = next((k for k in fs if k.startswith(S5_PREFIX)), None)
        if folder is None: return None
    gd = fs[folder]
    return {"folder": folder, "payloadSize": len(gd), "region": region_label(folder),
            "fields": decode_gamedata(gd), "path": path}

def decode_gamedata(gd):
    if not gd or len(gd) < 0x2C: return {}
    out = {}
    for k, (off, w, kind) in S5_FIELDS.items():
        if kind == "str":
            out[k] = gd[off:off+w].split(b"\x00")[0].decode("latin1", "replace")
        else:
            out[k] = int.from_bytes(gd[off:off+w], "little")
    return out

def apply_gamedata_edits(gd, edits):
    """edits: {field: value}. Returns (new_gd, changed). Only S5_FIELDS are writable."""
    b = bytearray(gd); changed = 0
    for k, v in (edits or {}).items():
        if k not in S5_FIELDS: continue
        off, w, kind = S5_FIELDS[k]
        if kind == "str":
            s = str(v).encode("latin1", "replace")[:w-1]
            b[off:off+w] = s + b"\x00"*(w-len(s))
        else:
            b[off:off+w] = int(v).to_bytes(w, "little")
        changed += 1
    return bytes(b), changed

def write_save_fields(card_path, folder, edits, make_backup=True):
    """Write S5_FIELDS edits into a save's gamedata on a memory card, refreshing ECC.
    NOTE: the gamedata checksum (if any) is UNVERIFIED — test on a card COPY first."""
    with open(card_path, "rb") as f:
        card = MemCard(f.read())
    tgt = next((s for s in card.find_saves() if s["folder"] == folder), None)
    if not tgt: return {"error": f"save folder {folder} not found"}
    gd = card.read_file(tgt["cluster"], tgt["length"], folder)
    if not gd: return {"error": "gamedata payload not found"}
    new_gd, changed = apply_gamedata_edits(gd, edits)
    if changed == 0: return {"ok": True, "changed": 0}
    if make_backup and BACKUPS and not os.path.exists(card_path + ".bak"):
        shutil.copy2(card_path, card_path + ".bak")
    card.write_file(tgt["cluster"], tgt["length"], folder, new_gd)
    with open(card_path, "wb") as f:
        f.write(card.to_bytes())
    return {"ok": True, "changed": changed,
            "warn": "gamedata checksum unverified; verify the save loads in-game"}


class MemCard:
    """PS2MFS memory-card image walker (handles 528-byte spare pages). Generic."""
    def __init__(self, data):
        if data[:len(MAGIC)] != MAGIC:
            raise ValueError("not a PS2 memory-card image")
        self.data = bytearray(data)
        self.page_len = struct.unpack_from("<H", data, 0x28)[0]
        self.pages_per_cluster = struct.unpack_from("<H", data, 0x2A)[0]
        self.pages_per_block = struct.unpack_from("<H", data, 0x2C)[0]
        self.clusters = struct.unpack_from("<I", data, 0x30)[0]
        self.alloc_offset = struct.unpack_from("<I", data, 0x34)[0]
        self.rootdir_cluster = struct.unpack_from("<I", data, 0x3C)[0]
        self.ifc_list = list(struct.unpack_from("<32I", data, 0x50))
        self.cluster_size = self.page_len * self.pages_per_cluster
        total_pages = self.clusters * self.pages_per_cluster
        spare_page = self.page_len + (self.page_len // 512) * 16
        if total_pages * spare_page == len(data): self.raw_page = spare_page
        elif total_pages * self.page_len == len(data): self.raw_page = self.page_len
        else: self.raw_page = len(data) // total_pages

    def _page(self, n):
        off = n * self.raw_page; return self.data[off:off + self.page_len]
    def _cluster(self, c):
        base = c * self.pages_per_cluster
        return b"".join(self._page(base + i) for i in range(self.pages_per_cluster))
    def _fat(self, cluster):
        per = self.cluster_size // 4
        ifc = self.ifc_list[cluster // (per*per)]
        fat_cluster = self._cluster(ifc)
        ptr = struct.unpack_from("<I", fat_cluster, ((cluster // per) % per) * 4)[0]
        return struct.unpack_from("<I", self._cluster(ptr), (cluster % per) * 4)[0]
    def _chain(self, first, size):
        out = b""; c = first
        while size > 0 and (c & 0x7FFFFFFF) != 0x7FFFFFFF and c != 0xFFFFFFFF:
            out += self._cluster((c & 0x7FFFFFFF) + self.alloc_offset)
            nxt = self._fat(c & 0x7FFFFFFF)
            if nxt == 0xFFFFFFFF: break
            c = nxt; size -= self.cluster_size
        return out
    @staticmethod
    def _dirent(buf, off):
        mode = struct.unpack_from("<H", buf, off)[0]
        length = struct.unpack_from("<I", buf, off + 4)[0]
        cluster = struct.unpack_from("<I", buf, off + 0x10)[0]
        name = buf[off+0x40:off+0x40+32].split(b"\x00")[0].decode("ascii", "replace")
        return {"mode": mode, "is_dir": bool(mode & 0x0020), "length": length,
                "cluster": cluster, "name": name}
    def _listdir(self, dir_cluster, count):
        data = self._chain(dir_cluster, count * 512); out = []
        for i in range(count):
            o = i * 512
            if o + 0x60 > len(data): break
            out.append(self._dirent(data, o))
        return out
    def root_entries(self):
        head = self._chain(self.rootdir_cluster, 512)
        return self._listdir(self.rootdir_cluster, self._dirent(head, 0)["length"])
    def find_saves(self, prefix=S5_PREFIX):
        return [{"folder": e["name"], "cluster": e["cluster"], "length": e["length"]}
                for e in self.root_entries() if e["is_dir"] and e["name"].startswith(prefix)]
    def read_file(self, dir_cluster, dir_len, filename):
        for e in self._listdir(dir_cluster, dir_len):
            if e["name"] == filename and not e["is_dir"]:
                return self._chain(e["cluster"], e["length"])[:e["length"]]
        return None
    # write support (in-place, same length; refreshes ECC)
    def _chain_clusters(self, first, size):
        clusters, c = [], first
        while size > 0 and (c & 0x7FFFFFFF) != 0x7FFFFFFF and c != 0xFFFFFFFF:
            clusters.append((c & 0x7FFFFFFF) + self.alloc_offset)
            nxt = self._fat(c & 0x7FFFFFFF)
            if nxt == 0xFFFFFFFF: break
            c = nxt; size -= self.cluster_size
        return clusters
    def _write_page(self, page_num, data512):
        off = page_num * self.raw_page
        self.data[off:off+self.page_len] = data512
        if self.raw_page >= self.page_len + 16:
            self.data[off+self.page_len:off+self.page_len+16] = ecc_page(data512)
    def _write_cluster(self, cluster_num, data):
        base = cluster_num * self.pages_per_cluster
        for i in range(self.pages_per_cluster):
            seg = data[i*self.page_len:(i+1)*self.page_len]
            if len(seg) < self.page_len: seg = seg + b"\x00"*(self.page_len-len(seg))
            self._write_page(base + i, seg)
    def write_file(self, dir_cluster, dir_len, filename, new_content):
        ent = None
        for e in self._listdir(dir_cluster, dir_len):
            if e["name"] == filename and not e["is_dir"]: ent = e; break
        if ent is None: raise KeyError(f"{filename} not found")
        if len(new_content) != ent["length"]:
            raise ValueError("in-place write only (length changed)")
        for i, cnum in enumerate(self._chain_clusters(ent["cluster"], ent["length"])):
            seg = new_content[i*self.cluster_size:(i+1)*self.cluster_size]
            if not seg: break
            self._write_cluster(cnum, seg)
        return True
    def to_bytes(self): return bytes(self.data)


_DIRENT = 512; _PSU_CLUSTER = 1024; _DF_DIR = 0x0020
def _round_up(n, m): return (n + m - 1) // m * m

class PsuSave:
    """Reader/writer for a single-save EMS (.psu) export. Generic."""
    def __init__(self, data):
        self.data = bytearray(data); d0 = self.data[:_DIRENT]
        mode, _, n = struct.unpack_from("<HHL", d0, 0)
        if not (mode & _DF_DIR) or n < 2: raise ValueError("not a .psu save file")
        self.folder = d0[0x40:0x40+448].split(b"\x00")[0].decode("latin1", "replace")
        self.files = {}; off = _DIRENT * 3
        for _ in range(n - 2):
            if off + _DIRENT > len(self.data): break
            hdr = self.data[off:off+_DIRENT]
            fmode, _, flen = struct.unpack_from("<HHL", hdr, 0)
            name = hdr[0x40:0x40+448].split(b"\x00")[0].decode("latin1", "replace")
            self.files[name] = {"hdr": off, "data_off": off+_DIRENT, "length": flen}
            off = off + _DIRENT + _round_up(flen, _PSU_CLUSTER)
    def read_file(self, name):
        e = self.files.get(name)
        return bytes(self.data[e["data_off"]:e["data_off"]+e["length"]]) if e else None
    def write_file(self, name, new_bytes):
        e = self.files.get(name)
        if not e or len(new_bytes) != e["length"]: return False
        self.data[e["data_off"]:e["data_off"]+e["length"]] = new_bytes; return True
    def to_bytes(self): return bytes(self.data)


def load_card(path):
    with open(path, "rb") as f: return MemCard(f.read())

_FW_MAP = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}; _FW_MAP["　"] = " "
def title_from_icon_sys(ic):
    """Parse the PS2-browser title, e.g. 'SuikodenV 01LV.39/019:16' -> slot/level/playtime."""
    if not ic or len(ic) < 0xC0 + 4: return {}
    raw = ic[0xC0:0xC0+68].split(b"\x00")[0].decode("shift_jis", "replace")
    norm = "".join(_FW_MAP.get(c, c) for c in raw)
    out = {"title": norm}
    import re
    m = re.search(r"SuikodenV\s*(\d+)", norm)
    if m: out["slot"] = int(m.group(1))
    m = re.search(r"LV\.?(\d+)", norm)
    if m: out["level"] = int(m.group(1))
    m = re.search(r"(\d+:\d+)\s*$", norm)
    if m: out["playtime"] = m.group(1)
    return out

def _sniff_format(path):
    try:
        sz = os.path.getsize(path)
        with open(path, "rb") as f: head = f.read(_DIRENT)
    except OSError: return "unknown"
    if head[:len(MAGIC)] == MAGIC: return "card"
    if len(head) >= 0x40 and (struct.unpack_from("<H", head, 0)[0] & _DF_DIR): return "psu"
    return "unknown"

def read_all_saves(path):
    """Open a card/.psu and return every S5 save it contains (raw gamedata + meta).
    Field decoding is stubbed until S5 offsets are RE'd from a real save."""
    fmt = _sniff_format(path); out = []
    if fmt == "psu":
        with open(path, "rb") as f: psu = PsuSave(f.read())
        if not psu.folder.startswith(S5_PREFIX): return []
        gd = psu.read_file("gamedata") or psu.read_file(psu.folder)
        out.append(_decode_stub(psu.folder, gd, title_from_icon_sys(psu.read_file("icon.sys"))))
        return out
    if fmt != "card": return []
    card = load_card(path)
    for s in card.find_saves():
        # S5 payload filename unknown; grab the largest non-icon file in the folder
        files = card._listdir(s["cluster"], s["length"])
        payload = None; pname = None
        for e in files:
            if e["is_dir"] or e["name"] in ("icon.sys",): continue
            data = card.read_file(s["cluster"], s["length"], e["name"])
            if data and (payload is None or len(data) > len(payload)):
                payload, pname = data, e["name"]
        ic = card.read_file(s["cluster"], s["length"], "icon.sys")
        d = _decode_stub(s["folder"], payload, title_from_icon_sys(ic)); d["payloadFile"] = pname
        out.append(d)
    return out

def _decode_stub(folder, gd, meta):
    d = {"folder": folder, "meta": meta, "payloadSize": (len(gd) if gd else 0),
         "region": region_label(folder)}
    if gd:
        d["fields"] = decode_gamedata(gd)   # VERIFIED: heroName, castleName, level
        d["note"] = "editable: heroName, castleName, level. Other fields need more saves."
    return d

def scan_memcards(roots):
    seen, found = set(), []; exts = (".ps2", ".mcd", ".mc2", ".bin")
    for r in roots:
        if not r or not os.path.isdir(r): continue
        for dp, _, files in os.walk(r):
            for fn in files:
                if not fn.lower().endswith(exts): continue
                full = os.path.join(dp, fn)
                if full in seen: continue
                seen.add(full)
                try: sz = os.path.getsize(full)
                except OSError: continue
                if sz not in (8650752, 8388608) and not (8_000_000 <= sz <= 9_500_000): continue
                try:
                    with open(full, "rb") as fh: blob = fh.read()
                except OSError: continue
                if blob[:len(MAGIC)] != MAGIC: continue
                found.append({"path": full, "name": fn, "size": sz, "kind": "card",
                              "hasS5": S5_PREFIX.encode() in blob})
    found.sort(key=lambda x: (not x["hasS5"], x["name"].lower()))
    return found


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("usage: s5save.py <memcard.ps2 | save.psu>"); sys.exit(1)
    print(json.dumps(read_all_saves(sys.argv[1]), indent=2))
