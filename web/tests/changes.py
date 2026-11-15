#!/usr/bin/env python3
"""Decoded-diff checks (Editor/s5changes.py) against the pristine US ISO and
a modified in-memory copy of its front slice. Emits JSON on stdout for
tests/changes.mjs. The ISO file itself is only ever read: its sha256 is
hashed before and after and must be identical. Skips (prints {"skip": ...})
when the ISO is absent."""
import hashlib, json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "Editor")))
ISO_PATH = os.environ.get("S5_ISO") or os.path.abspath(
    os.path.join(HERE, "..", "..", "ISO", "suikoden5-us.iso"))
if not os.path.exists(ISO_PATH):
    print(json.dumps({"skip": "ISO not found at " + ISO_PATH}))
    sys.exit(0)

import s5changes as C
import s5fields as F
import s5patch as P


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


sha_before = sha256(ISO_PATH)
with open(ISO_PATH, "rb") as f:
    base = f.read(0x6A0000)
mod = bytearray(base)
expect = []

# 1. a character stat (TABLES "stats", char 1, field index 1)
sb, ss, sfields = F.TABLES["stats"]
off = sb + 1 * ss + sfields[1][1]
mod[off] = base[off] + 7
expect.append({"group": "Characters", "labelPart": sfields[1][0],
               "old": str(base[off]), "new": str(base[off] + 7)})

# 2. a spell power (u16 at SPELL_BASE + 2, spell 0)
off = F.SPELL_BASE + 2
old = struct.unpack_from("<H", base, off)[0]
struct.pack_into("<H", mod, off, old + 111)
expect.append({"group": "Spells", "labelPart": "Power",
               "old": str(old), "new": str(old + 111)})

# 3. an armor field (head record 0, "Buy price" u32)
ab, _ac = F.ARMOR_TABLES["head"]
old = struct.unpack_from("<I", base, ab)[0]
struct.pack_into("<I", mod, ab, old + 500)
expect.append({"group": "Gear", "labelPart": "Buy price",
               "old": str(old), "new": str(old + 500)})

# 4. a unite participant byte (dynamic region, parsed from the base)
u = P.parse_unites(C._BytesISO(base))[0]
off = u["idsOff"]
old_id = base[off]
new_id = (old_id + 1) % 120
mod[off] = new_id
cn = {c["id"]: c["name"] for c in F.res_json("s5_characters.json")}
expect.append({"group": "Unites", "labelPart": "member 1",
               "old": cn[old_id], "new": cn[new_id]})

# 5. a character name-table entry (dynamic region): change its first letter
off = F.NAME_TABLE_BASE
old_name = base[off:off + 8].split(b"\x00")[0].decode("latin-1")
new_name = ("Z" if old_name[0] != "Z" else "Y") + old_name[1:]
mod[off] = ord(new_name[0])
expect.append({"group": "Char names", "labelPart": "Name entry 0",
               "old": old_name, "new": new_name})

# 6. a byte no table claims (ELF header area)
mod[0x1000] = base[0x1000] ^ 0xFF

rep = C.diff_report(bytes(mod), base)
self_rep = C.diff_report(base, base)
regions = C.static_regions()
print(json.dumps({
    "shaBefore": sha_before, "shaAfter": sha256(ISO_PATH),
    "regionCount": len(regions),
    "selfRows": len(self_rep["rows"]),
    "rows": rep["rows"], "summary": rep["summary"], "expect": expect,
}))
