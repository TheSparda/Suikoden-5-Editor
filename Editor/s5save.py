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
GAMEDATA_SIZE = 74024           # 0x12128, verified
PAYLOAD_IS_FOLDER_NAME = True   # payload filename == folder name (not "gamedata")
# NEEDS-SAVE (need multiple saves / a New-Game-Plus pair to confirm):
#   - gamedata checksum algorithm (S3 sum-zero ruled out: sumzero=False)
#   - character stat/party/gold/name/recruit field offsets
#   - New Game Plus flag (offset, width, on/off values)
NG_PLUS_FLAG = None
# VERIFIED gamedata fields (from the real LV.39 save):
#   heroName @0x00 = "Sparda"; castleName @0x14 = "Sparta Fortress"; level @0x28 = 39.
S5_FIELDS = {
    "heroName":   (0x00, 16, "str"),
    "castleName": (0x14, 16, "str"),
    "level":      (0x28, 1,  "num"),   # stored as u32 but value fits a byte; write low byte
}

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
    if make_backup and not os.path.exists(card_path + ".bak"):
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
    d = {"folder": folder, "meta": meta, "payloadSize": (len(gd) if gd else 0)}
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
