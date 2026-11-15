# Suikoden V Editor: overlay and DATA.PAK commands

The desktop engine (`Editor/s5patch.py`) can now work with the two packed
layers of the disc that the table editors cannot reach: the `.ROM` code
overlays in `OVL/`, and the 7,734 files inside `DATA.PAK`. Six commands,
all read-only except `overlay-set`. They are desktop CLI only; the web
editor does not expose them.

**Contents**

- [The .ROM container](#the-rom-container)
- [Overlay commands](#overlay-commands)
- [The size-fit limitation](#the-size-fit-limitation)
- [DATA.PAK commands](#datapak-commands)
- [Safety rules](#safety-rules)
- [What is not covered](#what-is-not-covered)

---

## The .ROM container

Every `.ROM` in the game (the 17 overlays, and the ~7,000 data ROMs inside
DATA.PAK) is a chain of members with a 0x40-byte header each. Two member
types are supported:

- **non**: stored, a plain copy.
- **szl**: Okumura LZSS (4096-byte ring, write position starting at 0xFEE,
  flag bits LSB-first, match length 3 to 18). Every overlay is a single
  szl member.

Two more types exist in DATA.PAK files, **hff** (Huffman) and **epb**
(byte-pair), and are *not* decoded by the strict codec in `Editor/s5rom.py`:
reading a container that uses them raises an "unsupported" error rather
than returning partial data. Nothing in `OVL/` uses them.

## Overlay commands

```
python3 Editor/s5patch.py overlays <iso>
python3 Editor/s5patch.py overlay-dump <iso> --name WAR --out war.bin
python3 Editor/s5patch.py overlay-set <iso> --name WAR --image war.bin
python3 Editor/s5patch.py overlay-verify <iso>
```

- **overlays** lists each `OVL/*.ROM` with its packed size, unpacked size,
  and member types.
- **overlay-dump** writes one overlay's unpacked image (the exact bytes
  the game loads at 0xA51600). The name may omit the `.ROM` suffix.
- **overlay-verify** unpacks, repacks, and unpacks every overlay and
  byte-compares the two images. On a stock US disc: 17/17 clean.
- **overlay-set** repacks a supplied image as a single szl member and
  writes it back into the ISO in place, at the file's original LBA. The
  image does not have to be the same length as the original unpacked
  overlay, but it does have to be a valid overlay image. After writing,
  the command re-extracts the overlay from the ISO and byte-compares it
  against the supplied image before reporting success.

## The size-fit limitation

`overlay-set` never moves another file. The repacked container must fit
inside the target file's existing sector allocation (its directory size
rounded up to a whole number of 2048-byte sectors); the slack is
zero-filled and the directory size is updated in both endians. If the
repack is even one byte too large, the command refuses, prints both
numbers, and writes nothing.

This is a real limit, not a theoretical one. The editor's LZSS encoder is
not the 2006 encoder that shipped the game, and on 5 of the 17 overlays
it lands slightly larger than stock. Four of those five (BATLE, DBGMENU,
IKKI, SNDTEST) still fit inside their sector allocation and write back
fine. HENSEI is the standing exception: its unmodified image repacks to
71,814 bytes against a 71,680-byte allocation, 134 bytes too many, so
HENSEI cannot be written back until either the image is edited to
compress smaller or a full ISO rebuild path exists. There is no rebuild
path yet. Either way, `overlay-set` tells you which side of the line you
landed on before it writes anything.

## DATA.PAK commands

```
python3 Editor/s5patch.py pak-list <iso> [--filter STR]
python3 Editor/s5patch.py pak-extract <iso> --path FIELD00/BG000.ROM --out bg000.rom
```

`DATA.PAK` is a CRI CVM/ROFS volume: a small CVMH header, then a standard
ISO9660 filesystem. `Editor/s5pak.py` locates it through the game ISO's
own directory (no hardcoded position), walks the embedded filesystem, and
reads by seeking, so the 2.4 GB archive is never loaded into memory.

- **pak-list** prints every internal file with its size and LBA, 7,734
  files on a stock US disc. `--filter` is a case-insensitive substring
  match, or a glob (`FIELD00/*.ROM`) when the pattern contains glob
  characters.
- **pak-extract** streams one file out by its internal path. The bytes
  are the raw stored payload: for `.ROM` files that is the container,
  which `s5rom` decodes (see `overlay-dump` for the overlay equivalent).

## Safety rules

- **Work on a copy.** `overlay-set` writes in place. It makes the usual
  `.bak` first (unless backups are off), but a backup of a 4.5 GB ISO is
  only as safe as the disc it sits on.
- **Whole-sector writes stay out of the mod recipe.** Like the older
  string-edit reinsert path, an overlay write is suppressed from the
  `.s5mod.json` byte journal; a several-hundred-KB journal entry would
  make the recipe useless. Table edits are journaled as before.
- **A write is not done until it re-reads correctly.** `overlay-set`
  byte-verifies against the re-extracted overlay, and the test suite
  (`web/tests/rom_pak.py`) additionally proves that a write touches no
  byte outside the target file's slot and its directory size.

## What is not covered

- No web UI for any of this; the commands are desktop CLI only.
- No DATA.PAK write-back: files can be extracted but not replaced, and
  DATA.PAK itself cannot be rebuilt.
- No hff or epb decoding in `s5rom`: epb-first ROMs (roughly 1 in 8 of
  the data ROMs) raise the unsupported error. The web asset browser has a
  separate best-effort epb decoder for viewing portraits; nothing writes
  epb back.
- No full ISO rebuild, so an overlay that outgrows its slot (HENSEI)
  cannot be replaced yet.
