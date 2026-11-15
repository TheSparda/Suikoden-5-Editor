#!/usr/bin/env python3
"""
Streaming reader for Suikoden V's DATA.PAK (a CRI CVM / ROFS volume).

DATA.PAK is not a custom archive format. It is a CRI Middleware CVM
volume: a 0x1800-byte "CVMH" header (ROFS builder stamp "ROFSROFSBLD
Ver.1.52 2003-06-09"), then a completely standard, unscrambled ISO9660
image whose files are stored plainly (no compression, no encryption at
this layer; most payloads are themselves .ROM containers, see s5rom.py).

Nothing about the layout is hardcoded. DATA.PAK is located through the
game ISO's own ISO9660 directory, and the embedded volume is located by
scanning sector-aligned offsets at the start of DATA.PAK for the PVD
signature and checking that the PVD's volume size accounts for the rest
of the file exactly. All reads are seeks plus bounded buffers, so the
2.4 GB archive is never loaded into memory; extraction streams in
1 MiB chunks.

Usage as a library:

    with DataPak("suikoden5-us.iso") as pak:
        for e in pak.entries(): ...
        pak.extract("COMMON/GEN5_ADX.AFS", "out.afs")
        head = pak.read_head("README.TXT", 64)

Self-contained, stdlib only, no dependency on s5patch/s5fields.
"""
import os
import struct

SECTOR = 2048
_CHUNK = 1 << 20


def _parse_dir(buf):
    """Yield (name_bytes, lba, size, flags) from one ISO9660 directory
    extent. Shared by the game disc's filesystem and the embedded volume:
    both use the same record layout."""
    i = 0
    while i < len(buf):
        ln = buf[i]
        if ln == 0:  # padding: skip to the next sector boundary
            i = ((i // SECTOR) + 1) * SECTOR
            continue
        rec = buf[i:i + ln]
        if len(rec) < 34:
            break
        lba = struct.unpack_from("<I", rec, 2)[0]
        size = struct.unpack_from("<I", rec, 10)[0]
        flags = rec[25]
        nlen = rec[32]
        name = rec[33:33 + nlen]
        yield name, lba, size, flags
        i += ln


def _clean_name(raw):
    """ISO9660 name -> display name: drop the ;version suffix and the
    empty-extension trailing dot ('MAKEFILE.;1' -> 'MAKEFILE')."""
    name = raw.decode("ascii", "replace")
    if ";" in name:
        name = name.split(";", 1)[0]
    if name.endswith("."):
        name = name[:-1]
    return name


class DataPak:
    """DATA.PAK opened in place inside a Suikoden V game ISO."""

    def __init__(self, iso_path):
        self.iso_path = iso_path
        self.f = open(iso_path, "rb")
        try:
            self.pak_lba, self.pak_size = self._find_datapak()
            self.base, self.pvd = self._find_volume()
        except Exception:
            self.f.close()
            raise
        root = self.pvd[156:156 + 34]
        self.root_lba = struct.unpack_from("<I", root, 2)[0]
        self.root_size = struct.unpack_from("<I", root, 10)[0]
        self.volume_id = self.pvd[40:72].decode("ascii", "replace").strip()
        self.system_id = self.pvd[8:40].decode("ascii", "replace").strip()

    def close(self):
        try: self.f.close()
        except Exception: pass

    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    # -- locating ------------------------------------------------------------

    def _find_datapak(self):
        """(lba, size) of DATA.PAK from the game ISO's root directory."""
        self.f.seek(16 * SECTOR)
        pvd = self.f.read(SECTOR)
        if len(pvd) < 190 or pvd[0] != 1 or pvd[1:6] != b"CD001":
            raise ValueError("not an ISO9660 image (no PVD at sector 16)")
        root = pvd[156:156 + 34]
        rlba = struct.unpack_from("<I", root, 2)[0]
        rsize = struct.unpack_from("<I", root, 10)[0]
        self.f.seek(rlba * SECTOR)
        for name, lba, size, flags in _parse_dir(self.f.read(rsize)):
            if _clean_name(name) == "DATA.PAK" and not (flags & 2):
                return lba, size
        raise ValueError("DATA.PAK not found in the ISO root directory")

    def _find_volume(self):
        """Locate the embedded ISO9660 image inside DATA.PAK: try
        sector-aligned bases and validate the PVD (signature + volume
        size accounting against DATA.PAK's directory-record size).
        Returns (absolute byte offset of the embedded image in the game
        ISO, PVD bytes)."""
        pak_off = self.pak_lba * SECTOR
        for rel in range(0, 0x10000, SECTOR):
            if rel + 17 * SECTOR > self.pak_size:
                break
            self.f.seek(pak_off + rel + 16 * SECTOR)
            pvd = self.f.read(SECTOR)
            if len(pvd) < 190 or pvd[0] != 1 or pvd[1:6] != b"CD001":
                continue
            vol_sectors = struct.unpack_from("<I", pvd, 80)[0]
            if rel + vol_sectors * SECTOR == self.pak_size:
                return pak_off + rel, pvd
        raise ValueError("no embedded ISO9660 PVD found in DATA.PAK "
                         "(not a CVM/ROFS volume?)")

    # -- reading -------------------------------------------------------------

    def _read(self, lba, size):
        self.f.seek(self.base + lba * SECTOR)
        return self.f.read(size)

    def entries(self):
        """Walk the embedded tree. Returns a sorted list of dicts with
        path, extents (a list of (lba, size), for multi-extent files)
        and size. Directory extents are read whole (the whole tree is
        ~600 KB); file payloads are never touched."""
        out = []
        stack = [(self.root_lba, self.root_size, "")]
        seen_dirs = set()
        while stack:
            lba, size, prefix = stack.pop()
            if (lba, prefix) in seen_dirs:
                continue
            seen_dirs.add((lba, prefix))
            pending = None  # multi-extent accumulator
            for name, elba, esize, flags in _parse_dir(self._read(lba, size)):
                if name in (b"\x00", b"\x01"):  # '.' and '..'
                    continue
                disp = _clean_name(name)
                if flags & 2:  # directory
                    stack.append((elba, esize, prefix + disp + "/"))
                    continue
                if pending is not None and pending["path"] == prefix + disp:
                    pending["extents"].append((elba, esize))
                    pending["size"] += esize
                    if not (flags & 0x80):
                        out.append(pending)
                        pending = None
                    continue
                entry = {"path": prefix + disp,
                         "extents": [(elba, esize)], "size": esize}
                if flags & 0x80:  # more extents follow
                    pending = entry
                else:
                    out.append(entry)
            if pending is not None:
                out.append(pending)
        out.sort(key=lambda e: e["path"])
        return out

    def find(self, path):
        """The entry for an internal path ('FIELD00/BG000.ROM'), matched
        case-insensitively, a leading '/' tolerated. KeyError if absent."""
        want = path.strip("/").lower()
        for e in self.entries():
            if e["path"].lower() == want:
                return e
        raise KeyError(f"{path} not found in DATA.PAK")

    def read_head(self, path, n):
        """The first n bytes of a stored file (codec sniffing, headers)
        without extracting it."""
        e = self.find(path)
        lba, esize = e["extents"][0]
        self.f.seek(self.base + lba * SECTOR)
        return self.f.read(min(n, esize))

    def extract(self, path, out_path):
        """Stream one file's stored bytes to out_path (parent directories
        are created). Returns the entry dict. The bytes are the raw
        archive payload: for .ROM files that is the container, which
        s5rom.unpack decodes."""
        e = self.find(path) if isinstance(path, str) else path
        parent = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(parent, exist_ok=True)
        written = 0
        with open(out_path, "wb") as out:
            for lba, esize in e["extents"]:
                self.f.seek(self.base + lba * SECTOR)
                remaining = esize
                while remaining > 0:
                    chunk = self.f.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise IOError(f"short read in {e['path']}")
                    out.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
        if written != e["size"]:
            raise IOError(f"size mismatch extracting {e['path']}: "
                          f"{written} of {e['size']} bytes")
        return e
