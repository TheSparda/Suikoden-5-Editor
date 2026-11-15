#!/usr/bin/env python3
"""
Suikoden V .ROM container codec (the SN Systems "rom" container).

Every .ROM in the game (the 17 code overlays in OVL/, and the ~7000 data
ROMs inside DATA.PAK) is a chain of members. Member header:

  +0x00  u32  container magic on member 0: 0x726F6D00 ("rom\\0")
  +0x04  u32  header_len - 4   (member data starts at member + this + 4;
              0x40 total header in every file seen so far)
  +0x08  u32  member type word: 0x6E6F6E00 "non" = stored (plain copy)
                                0x6C7A7300 "szl" = LZSS (see below)
                                0x68666600 "hff" = Huffman (unsupported)
                                0x62706500 "epb" = byte-pair (unsupported)
  +0x0C  u32  uncompressed size of this member
  +0x10  u32  total container size (member 0 only, informational)

"szl" is textbook Okumura LZSS, mirroring the game's own decompressor:
a 4096-byte ring buffer, zero-filled, write position starting at 0xFEE;
flag bits consumed LSB-first (a flag byte is loaded as byte | 0xFF00 and
bit 0x100 is the reload sentinel); bit 1 = literal byte, bit 0 = match
(2 bytes: pos = b0 | (b1 & 0xF0) << 4, len = (b1 & 0x0F) + 3, copied from
the ring, overlap allowed). Decoding stops after exactly the member's
uncompressed size; the input bytes consumed locate the next member,
which in practice starts on a 0x10 boundary.

This module is deliberately strict: a bad magic, an unknown type word, or
a stream that runs out early raises RomError instead of returning partial
data, because the write-back path in s5patch.py relies on it to prove a
repacked container decodes to the intended bytes. The "hff" and "epb"
member types raise UnsupportedMemberError: neither decoder is ported
here yet, and a chain cannot be walked past a member whose compressed
length is unknown without decoding it.

Self-contained, stdlib only, no dependency on s5patch/s5fields.
"""
import struct

MAGIC = 0x726F6D00
HEADER_LEN = 0x40

TYPES = {0x6E6F6E00: "non", 0x6C7A7300: "szl",
         0x68666600: "hff", 0x62706500: "epb"}
TYPE_WORDS = {name: word for word, name in TYPES.items()}


class RomError(ValueError):
    """The bytes are not a well-formed .ROM container."""


class UnsupportedMemberError(RomError):
    """A member uses a codec this module does not implement (hff/epb)."""


# ---- szl: Okumura LZSS -------------------------------------------------------

def lzss_decompress(src, out_size):
    """Decompress one szl stream. Returns (out, consumed): the decompressed
    bytes (exactly out_size of them) and how many input bytes were used,
    which is what locates the next member in the chain."""
    ring = bytearray(4096)
    r = 0xFEE
    out = bytearray()
    pos = 0
    flags = 0
    n = len(src)
    while len(out) < out_size:
        flags >>= 1
        if not (flags & 0x100):
            if pos >= n:
                raise RomError("szl stream truncated: ran out of input at "
                               f"{len(out)} of {out_size} output bytes")
            flags = src[pos] | 0xFF00
            pos += 1
        if flags & 1:
            if pos >= n:
                raise RomError("szl stream truncated: ran out of input at "
                               f"{len(out)} of {out_size} output bytes")
            c = src[pos]
            pos += 1
            out.append(c)
            ring[r] = c
            r = (r + 1) & 0xFFF
        else:
            if pos + 1 >= n:
                raise RomError("szl stream truncated: ran out of input at "
                               f"{len(out)} of {out_size} output bytes")
            b0 = src[pos]
            b1 = src[pos + 1]
            pos += 2
            mpos = b0 | ((b1 & 0xF0) << 4)
            mlen = (b1 & 0x0F) + 3
            for i in range(mlen):
                c = ring[(mpos + i) & 0xFFF]
                out.append(c)
                ring[r] = c
                r = (r + 1) & 0xFFF
                if len(out) >= out_size:
                    break
    return bytes(out), pos


def lzss_compress(data):
    """Greedy + 1-byte-lazy LZSS encoder producing a stream lzss_decompress
    (and the game's own decompressor) decodes back to `data`.

    A match (distance d in 1..4096, length 3..18) at output position i is
    valid iff data[i+k] == data[i+k-d] for all k < length, where negative
    indices read as 0 (the decoder's ring starts zero-filled). The encoded
    ring position is (0xFEE + i - d) & 0xFFF.
    """
    n = len(data)
    out = bytearray()
    # hash chains: 3-byte sequence -> ascending list of positions
    chains = {}

    def best_match(i):
        best_len, best_d = 0, 0
        if i + 3 > n:
            return best_len, best_d
        cands = chains.get(data[i:i + 3])
        if not cands:
            return best_len, best_d
        max_len = min(18, n - i)
        tried = 0
        for j in reversed(cands):
            d = i - j
            if d > 4096:
                break
            tried += 1
            if tried > 64:
                break
            l = 0
            while l < max_len and data[i + l] == data[i + l - d]:
                l += 1
            if l >= 3 and l > best_len:
                best_len, best_d = l, d
                if l >= max_len:
                    break
        return best_len, best_d

    def index_upto(i):
        # add position i to the chains (called for every consumed position)
        if i + 3 <= n:
            chains.setdefault(data[i:i + 3], []).append(i)

    i = 0
    while i < n:
        # reserve a flag byte, fill up to 8 items LSB-first
        flag_pos = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            mlen, md = best_match(i)
            if mlen >= 3:
                # lazy: a literal now wins if the next position matches longer
                if i + 1 < n:
                    nlen, _ = best_match(i + 1)
                    if nlen > mlen + 1:
                        mlen = 0
                if mlen >= 3:
                    p = (0xFEE + i - md) & 0xFFF
                    out.append(p & 0xFF)
                    out.append(((p >> 4) & 0xF0) | (mlen - 3))
                    for k in range(mlen):
                        index_upto(i + k)
                    i += mlen
                    continue
            flags |= (1 << bit)
            out.append(data[i])
            index_upto(i)
            i += 1
        out[flag_pos] = flags
    return bytes(out)


# ---- Container ---------------------------------------------------------------

def pack_container(payload, type_name="szl"):
    """Build a single-member .ROM container around an uncompressed payload,
    byte-layout-identical to the game's own files (0x40 header, 0xFF pad)."""
    if type_name not in TYPE_WORDS:
        raise RomError(f"unknown member type {type_name!r}")
    if type_name in ("hff", "epb"):
        raise UnsupportedMemberError(
            f"cannot pack member type {type_name!r}: encoder not implemented")
    if type_name == "szl":
        stream = lzss_compress(payload)
    else:  # "non"
        stream = bytes(payload)
    total = HEADER_LEN + len(stream)
    hdr = struct.pack("<IIIII", MAGIC, 0x3C, TYPE_WORDS[type_name],
                      len(payload), total)
    hdr += b"\xFF" * (HEADER_LEN - len(hdr))
    return hdr + stream


def _member_header(data, off, idx):
    """(type_name, usize, data_offset) for the member at `off`."""
    if off + 0x10 > len(data):
        raise RomError(f"member {idx} at {off:#x}: truncated header "
                       f"({len(data) - off} bytes left)")
    if idx == 0:
        magic = struct.unpack_from("<I", data, 0)[0]
        if magic != MAGIC:
            raise RomError(f"not a .ROM container: magic {magic:#010x}, "
                           f"expected {MAGIC:#010x}")
    hdr_len = struct.unpack_from("<I", data, off + 4)[0] + 4
    type_word = struct.unpack_from("<I", data, off + 8)[0]
    usize = struct.unpack_from("<I", data, off + 0xC)[0]
    if type_word not in TYPES:
        raise RomError(f"member {idx} at {off:#x}: unknown type "
                       f"{type_word:#010x}")
    if hdr_len < 0x14 or off + hdr_len > len(data):
        raise RomError(f"member {idx} at {off:#x}: bad header length "
                       f"{hdr_len:#x}")
    return TYPES[type_word], usize, off + hdr_len


def _is_padding(data, off):
    """True when everything from `off` on is one repeated pad byte (0x00 or
    0xFF): the tail of a sector-padded read, not another member."""
    tail = data[off:]
    if not tail:
        return True
    return tail[:1] in (b"\x00", b"\xFF") and tail == tail[:1] * len(tail)


def member_info(data):
    """Walk the member chain without keeping payloads. Returns a list of
    dicts: {index, offset, type, usize, data_offset, raw_size}, where
    raw_size is the member's compressed byte count (for "non", == usize).
    Raises UnsupportedMemberError if the chain reaches an hff/epb member:
    its compressed length cannot be known without decoding it."""
    infos = []
    off = 0
    idx = 0
    while off + 0x10 <= len(data):
        if idx > 0 and _is_padding(data, off):
            break
        tname, usize, dstart = _member_header(data, off, idx)
        if tname == "non":
            if dstart + usize > len(data):
                raise RomError(f"member {idx}: stored data truncated "
                               f"({len(data) - dstart} of {usize} bytes)")
            consumed = usize
        elif tname == "szl":
            _, consumed = lzss_decompress(data[dstart:], usize)
        else:
            raise UnsupportedMemberError(
                f"member {idx} at {off:#x}: type {tname!r} not implemented")
        infos.append({"index": idx, "offset": off, "type": tname,
                      "usize": usize, "data_offset": dstart,
                      "raw_size": consumed})
        nxt = (dstart + consumed + 0xF) & ~0xF   # members are 0x10-aligned
        if nxt >= len(data):
            break
        off = nxt
        idx += 1
    if not infos:
        raise RomError("empty container: no members")
    return infos


def unpack(data):
    """Unpack all members; returns [(index, type_name, payload), ...]."""
    members = []
    for info in member_info(data):
        dstart = info["data_offset"]
        if info["type"] == "non":
            payload = bytes(data[dstart:dstart + info["usize"]])
        else:  # szl (member_info has already raised for hff/epb)
            payload, _ = lzss_decompress(data[dstart:], info["usize"])
        if len(payload) != info["usize"]:
            raise RomError(f"member {info['index']}: got {len(payload)} "
                           f"bytes, expected {info['usize']}")
        members.append((info["index"], info["type"], payload))
    return members


def unpack_image(data):
    """Unpack a container and concatenate its member payloads: the loaded
    image, for the (universal so far) single-member case just the payload."""
    return b"".join(payload for _, _, payload in unpack(data))
