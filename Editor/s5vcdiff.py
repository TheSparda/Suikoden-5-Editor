#!/usr/bin/env python3
"""VCDIFF (RFC 3284) encode/decode for Suikoden V ISO patches (.xdelta).

A .s5mod recipe can only express the field edits the engine knows how to
name. An .xdelta patch captures EVERYTHING else: overlay rewrites,
arbitrary byte changes, code patches. This module creates and applies
standard VCDIFF patches that interoperate with xdelta3 and with the web
editor's vcdiff.js (ported from the Suikoden IV editor, whose format
choices this module mirrors exactly):

  - header: magic D6 C3 C4 00, Hdr_Indicator 0x00 (no secondary compressor,
    no app header, no checksum on OUR patches; foreign patches carrying
    xdelta3's VCD_ADLER32 window checksums are verified on apply)
  - per window: VCD_SOURCE, source segment = the same byte range the
    window outputs, so a decoder needs no other context
  - instructions use only the RFC default code table opcodes 1 (ADD,
    explicit size) and 19 (COPY, explicit size, mode 0 = VCD_SELF)

The decoder reads the full default code table, all nine address modes
with both caches, RUN and VCD_TARGET windows, so patches produced by
`xdelta3 -e -S none` apply here. Patches using secondary compression
(xdelta3's default) are refused with the remedy named, exactly as the
web decoder refuses them: shipping an LZMA decoder for one feature is
not worth it, and a mis-decode would be far worse than a clear refusal.

Both directions stream window by window, so multi-GB discs never sit in
memory. The encoder diffs the two files itself (the CLI has both files;
the web editor instead synthesizes a patch from its tracked edit runs
with the same window format).
"""
import os, zlib

WINDOW = 8 * 1024 * 1024
_GAP = 64          # diff runs separated by fewer unchanged bytes merge


def _push_int(out, n):
    if n < 0:
        raise ValueError("vcdiff int < 0")
    b = bytearray()
    b.append(n & 0x7F)
    n >>= 7
    while n:
        b.append((n & 0x7F) | 0x80)
        n >>= 7
    out.extend(reversed(b))


def _read_int(buf, pos):
    n, guard = 0, 0
    while True:
        if pos[0] >= len(buf):
            raise ValueError("vcdiff: truncated integer")
        guard += 1
        if guard > 8:
            raise ValueError("vcdiff: integer too large")
        b = buf[pos[0]]
        pos[0] += 1
        n = n * 128 + (b & 0x7F)
        if not (b & 0x80):
            return n


def _diff_runs(a, b):
    """Changed runs between equal-length bytes a (source) and b (target),
    as (offset, new_bytes) with runs closer than _GAP merged."""
    runs = []
    i, n = 0, len(b)
    while i < n:
        if a[i] != b[i]:
            s = i
            last = i
            i += 1
            while i < n:
                if a[i] != b[i]:
                    last = i
                    i += 1
                elif i - last > _GAP:
                    break
                else:
                    i += 1
            runs.append((s, b[s:last + 1]))
        else:
            i += 1
    return runs


def create_patch(pristine_path, modified_path, out_path, window=WINDOW):
    """Write a VCDIFF patch that turns the pristine file into the modified
    one. Returns (patch_bytes, changed_bytes)."""
    src_size = os.path.getsize(pristine_path)
    tgt_size = os.path.getsize(modified_path)
    changed = 0
    with open(pristine_path, "rb") as sf, open(modified_path, "rb") as tf, \
            open(out_path, "wb") as out:
        out.write(bytes([0xD6, 0xC3, 0xC4, 0x00, 0x00]))
        ws = 0
        while ws < tgt_size:
            we = min(ws + window, tgt_size)
            tlen = we - ws
            sf.seek(ws)
            seg_src = sf.read(tlen) if ws < src_size else b""
            seg_len = len(seg_src)       # < tlen only past the source's EOF
            tf.seek(ws)
            seg_tgt = tf.read(tlen)
            data, inst, addr = bytearray(), bytearray(), bytearray()

            def emit_copy(rel, ln):
                if ln <= 0:
                    return
                inst.append(19)
                _push_int(inst, ln)
                _push_int(addr, rel)

            def emit_add(bs):
                if not bs:
                    return
                inst.append(1)
                _push_int(inst, len(bs))
                data.extend(bs)

            p = 0
            # Fast path: most windows of a real disc diff are untouched, and
            # a bytes comparison is C-speed; the Python diff loop only runs
            # over windows that actually changed.
            runs = [] if seg_src == seg_tgt[:seg_len] else \
                _diff_runs(seg_src, seg_tgt[:seg_len])
            for (off, bs) in runs:
                emit_copy(p, off - p)
                emit_add(bs)
                changed += len(bs)
                p = off + len(bs)
            emit_copy(p, seg_len - p)
            if seg_len < tlen:           # target extends past source EOF
                emit_add(seg_tgt[seg_len:])
                changed += tlen - seg_len

            block = bytearray()
            _push_int(block, tlen)
            block.append(0x00)                       # Delta_Indicator: none
            _push_int(block, len(data))
            _push_int(block, len(inst))
            _push_int(block, len(addr))
            block += data + inst + addr

            head = bytearray()
            head.append(0x01)                        # Win_Indicator = VCD_SOURCE
            _push_int(head, seg_len)                 # source segment size
            _push_int(head, ws)                      # source segment position
            _push_int(head, len(block))
            out.write(head + block)
            ws = we
    return os.path.getsize(out_path), changed


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

def _default_code_table():
    NOOP, ADD, RUN, COPY = 0, 1, 2, 3
    t = [[RUN, 0, 0, NOOP, 0, 0]]
    for s in range(18):
        t.append([ADD, s, 0, NOOP, 0, 0])
    for m in range(9):
        t.append([COPY, 0, m, NOOP, 0, 0])
        for s in range(4, 19):
            t.append([COPY, s, m, NOOP, 0, 0])
    for m in range(6):
        for a in range(1, 5):
            for c in range(4, 7):
                t.append([ADD, a, 0, COPY, c, m])
    for m in range(6, 9):
        for a in range(1, 5):
            t.append([ADD, a, 0, COPY, 4, m])
    for m in range(9):
        t.append([COPY, 4, m, ADD, 1, 0])
    if len(t) != 256:
        raise AssertionError("code table is %d, expected 256" % len(t))
    return t


_CODE_TABLE = None


class _AddrCache:
    def __init__(self, near_size=4, same_size=3):
        self.near_size, self.same_size = near_size, same_size
        self.reset()

    def reset(self):
        self.near = [0] * self.near_size
        self.same = [0] * (self.same_size * 256)
        self.next_slot = 0

    def _update(self, addr):
        if self.near_size:
            self.near[self.next_slot] = addr
            self.next_slot = (self.next_slot + 1) % self.near_size
        if self.same_size:
            self.same[addr % (self.same_size * 256)] = addr

    def decode(self, mode, here, addr_buf, pos):
        if mode == 0:
            addr = _read_int(addr_buf, pos)
        elif mode == 1:
            addr = here - _read_int(addr_buf, pos)
        elif mode - 2 < self.near_size:
            addr = self.near[mode - 2] + _read_int(addr_buf, pos)
        else:
            if pos[0] >= len(addr_buf):
                raise ValueError("vcdiff: truncated address")
            b = addr_buf[pos[0]]
            pos[0] += 1
            addr = self.same[(mode - (2 + self.near_size)) * 256 + b]
        self._update(addr)
        return addr


def _decode_window(inst, data, addr, src, source_len, target_len, cache):
    NOOP, ADD, RUN, COPY = 0, 1, 2, 3
    out = bytearray()
    ip, ap, dp = [0], [0], 0
    cache.reset()
    while ip[0] < len(inst):
        row = _CODE_TABLE[inst[ip[0]]]
        ip[0] += 1
        for half in (0, 1):
            op, sz0, mode = row[half * 3], row[half * 3 + 1], row[half * 3 + 2]
            if op == NOOP:
                continue
            size = _read_int(inst, ip) if sz0 == 0 else sz0
            if len(out) + size > target_len:
                raise ValueError("vcdiff: instruction overruns the window")
            if op == ADD:
                out += data[dp:dp + size]
                dp += size
            elif op == RUN:
                out += bytes([data[dp]]) * size
                dp += 1
            else:  # COPY
                a = cache.decode(mode, source_len + len(out), addr, ap)
                # Slice-copy the non-overlapping spans (the whole window, for
                # an ordinary source COPY); only a self-referential overlap
                # (the RLE-style encoding) needs the periodic extension.
                while size > 0:
                    if a < source_len:
                        take = min(size, source_len - a)
                        out += src[a:a + take]
                        a += take
                        size -= take
                    else:
                        oa = a - source_len
                        if oa + size <= len(out):
                            out += out[oa:oa + size]
                            a += size
                            size = 0
                        else:
                            d = len(out) - oa       # fixed repeat period
                            pat = bytes(out[oa:])
                            while size > 0:
                                take = min(size, d)
                                out += pat[:take]
                                a += take
                                size -= take
    if len(out) != target_len:
        raise ValueError("vcdiff: window produced %d of %d bytes"
                         % (len(out), target_len))
    return bytes(out)


def apply_patch(pristine_path, patch_path, out_path):
    """Apply a VCDIFF patch to the pristine file, writing the reconstructed
    target to out_path. Returns the target size. The pristine file is only
    read; out_path must not be the pristine path."""
    global _CODE_TABLE
    if os.path.abspath(pristine_path) == os.path.abspath(out_path):
        raise ValueError("output must not overwrite the pristine file")
    patch = open(patch_path, "rb").read()
    if len(patch) < 5 or patch[:3] != bytes([0xD6, 0xC3, 0xC4]):
        raise ValueError("not a VCDIFF/.xdelta patch (bad magic)")
    if patch[3] != 0x00:
        raise ValueError("unsupported VCDIFF version %d" % patch[3])
    pos = [4]
    hdr = patch[pos[0]]
    pos[0] += 1
    if hdr & 0x01:
        raise ValueError("this patch uses secondary compression, which this "
                         "editor can't read. Re-create it with: "
                         "xdelta3 -e -S none -s <source> <target> <patch>")
    if hdr & 0x02:
        raise ValueError("this patch ships a custom code table, which this "
                         "editor can't read")
    if hdr & 0x04:
        n = _read_int(patch, pos)
        pos[0] += n                                  # app header: skip
    if _CODE_TABLE is None:
        _CODE_TABLE = _default_code_table()
    cache = _AddrCache()
    total = 0
    with open(pristine_path, "rb") as sf, open(out_path, "wb") as out:
        while pos[0] < len(patch):
            win = patch[pos[0]]
            pos[0] += 1
            if win & ~(0x01 | 0x02 | 0x04):
                raise ValueError("unsupported window indicator 0x%X" % win)
            source_len = source_start = 0
            if win & (0x01 | 0x02):
                source_len = _read_int(patch, pos)
                source_start = _read_int(patch, pos)
            _read_int(patch, pos)                    # delta encoding length
            target_len = _read_int(patch, pos)
            delta_ind = patch[pos[0]]
            pos[0] += 1
            if delta_ind:
                raise ValueError("this patch compresses its delta sections, "
                                 "which this editor can't read. Re-create "
                                 "it with: xdelta3 -e -S none")
            dlen = _read_int(patch, pos)
            ilen = _read_int(patch, pos)
            alen = _read_int(patch, pos)
            adler = None
            if win & 0x04:
                adler = int.from_bytes(patch[pos[0]:pos[0] + 4], "big")
                pos[0] += 4
            data = patch[pos[0]:pos[0] + dlen]; pos[0] += dlen
            inst = patch[pos[0]:pos[0] + ilen]; pos[0] += ilen
            addr = patch[pos[0]:pos[0] + alen]; pos[0] += alen
            if pos[0] > len(patch):
                raise ValueError("vcdiff: truncated window")
            if win & 0x02:                           # VCD_TARGET
                out.flush()
                with open(out_path, "rb") as back:
                    back.seek(source_start)
                    src = back.read(source_len)
            elif source_len:
                sf.seek(source_start)
                src = sf.read(source_len)
            else:
                src = b""
            if len(src) < source_len:
                raise ValueError("vcdiff: short source segment")
            decoded = _decode_window(inst, data, addr, src, source_len,
                                     target_len, cache)
            if adler is not None and (zlib.adler32(decoded) & 0xFFFFFFFF) != adler:
                raise ValueError("checksum mismatch: this patch was built "
                                 "against a different source file (or the "
                                 "disc has already been modified)")
            out.write(decoded)
            total += target_len
    return total
