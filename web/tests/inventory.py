#!/usr/bin/env python3
"""Inventory list round-trip + real-save verification (issue: sibling parity,
save inventory editor). The layout was verified 2026-11-15 against two real
saves and the ELF append rules (Editor/s5save.py INV_LISTS block):
  synthetic payload: set/qty/append/remove-compact semantics + error cases;
  real saves (when present): decode US + PAL fixtures, assert every entry is
  valid, every stack limit matches the master-table byte, and in-place edits
  round-trip byte-identically. Real-save part skips cleanly when the fixture
  files are absent. Run via tests/inventory.mjs (skips without python3)."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "Editor")))
import s5save as SV

n = bad = 0
def chk(name, cond, extra=""):
    global n, bad
    n += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad += 1

def slot_bytes(value, cat, iid, qty, mx):
    return value.to_bytes(4, "little") + bytes((cat, iid, qty, mx))

EMPTY = slot_bytes(0, SV.INV_EMPTY_CAT, 0, 0, 0)

def blank_payload():
    gd = bytearray(SV.GAMEDATA_SIZE)
    for name, (base, cap) in SV.INV_LISTS.items():
        for i in range(cap):
            gd[base + 8 * i:base + 8 * i + 8] = EMPTY
    return bytes(gd)

# ---------- synthetic payload ----------
gd = bytearray(blank_payload())
BAG, _ = SV.INV_LISTS["bag"]
# bag: Medicine x5 (stack limit 5), a Fire Orb (cat 6, one per slot)
gd[BAG:BAG + 4] = (2).to_bytes(4, "little")
gd[BAG + 4:BAG + 8] = bytes((9, 1, 5, 5))
gd[BAG + 8:BAG + 16] = slot_bytes(0, 6, 1, 0, 0)
gd = bytes(gd)

inv = SV.read_inventory(gd)
chk("synthetic decode: bag entries", [(e["cat"], e["id"], e["qty"], e["max"]) for e in inv["bag"]]
    == [(9, 1, 5, 5), (6, 1, 0, 0)])
chk("synthetic decode: other lists empty", inv["warehouse"] == [] and inv["keyItems"] == [])

# quantity change in place: only the qty byte moves
gd2, ch = SV.apply_gamedata_edits(gd, {"inv_bag_0": {"cat": 9, "id": 1, "qty": 3}})
chk("qty edit applies", ch == 1 and gd2[BAG + 6] == 3 and gd2[:BAG + 6] == gd[:BAG + 6]
    and gd2[BAG + 7:] == gd[BAG + 7:])

# identity change zeroes the instance value and writes the table stack limit
gd3 = bytearray(gd)
gd3[BAG + 8:BAG + 12] = (1250).to_bytes(4, "little")     # antique-style value on slot 1
gd3[BAG + 12:BAG + 16] = bytes((17, 11, 0, 0))
gd3 = bytes(gd3)
gd4, _ = SV.apply_gamedata_edits(gd3, {"inv_bag_1": {"cat": 9, "id": 2, "qty": 2}})
chk("identity change: value zeroed, limit from table",
    gd4[BAG + 8:BAG + 16] == slot_bytes(0, 9, 2, 2, 3))
gd5, _ = SV.apply_gamedata_edits(gd3, {"inv_bag_1": {"cat": 17, "id": 11, "qty": 0}})
chk("qty-only edit keeps the instance value",
    gd5[BAG + 8:BAG + 16] == slot_bytes(1250, 17, 11, 0, 0))

# append at slot == count, including onto an empty list (slot 0 holds the count)
gd6, _ = SV.apply_gamedata_edits(gd, {"inv_bag_2": {"cat": 7, "id": 1, "qty": 4}})
chk("append: entry + count", gd6[BAG:BAG + 4] == (3).to_bytes(4, "little")
    and gd6[BAG + 16:BAG + 24] == slot_bytes(0, 7, 1, 4, 4)
    and SV.read_inventory(gd6)["bag"][2]["cat"] == 7)
gd7, _ = SV.apply_gamedata_edits(gd, {"inv_keyItems_0": {"cat": 8, "id": 3, "qty": 0}})
KB, _ = SV.INV_LISTS["keyItems"]
chk("append onto empty list keeps count in slot 0 u32",
    gd7[KB:KB + 4] == (1).to_bytes(4, "little") and gd7[KB + 4:KB + 8] == bytes((8, 3, 0, 0)))

# removal compacts and restores the empty-form tail; removing slot 0 keeps the count
gd8, _ = SV.apply_gamedata_edits(gd6, {"inv_bag_0": {"cat": 0}})
chk("remove slot 0: compaction + count",
    gd8[BAG:BAG + 4] == (2).to_bytes(4, "little")
    and gd8[BAG + 4:BAG + 8] == bytes((6, 1, 0, 0))
    and gd8[BAG + 8:BAG + 16] == slot_bytes(0, 7, 1, 4, 4)
    and gd8[BAG + 16:BAG + 24] == EMPTY)

def raises(edits, payload=None):
    try:
        SV.apply_gamedata_edits(payload or gd, edits)
        return False
    except ValueError:
        return True

chk("reject unverified category", raises({"inv_bag_0": {"cat": 19, "id": 1, "qty": 0}}))
chk("reject unverified id", raises({"inv_bag_0": {"cat": 9, "id": 42, "qty": 1}}))
chk("reject quantity over the stack limit", raises({"inv_bag_0": {"cat": 9, "id": 1, "qty": 6}}))
chk("reject quantity on a one-per-slot item", raises({"inv_bag_0": {"cat": 6, "id": 1, "qty": 1}}))
chk("reject slot past the append position", raises({"inv_bag_9": {"cat": 9, "id": 1, "qty": 1}}))
chk("reject removal of a missing entry", raises({"inv_bag_2": {"cat": 0}}))
full = bytearray(blank_payload())
full[BAG:BAG + 4] = (70).to_bytes(4, "little")
for i in range(70):
    full[BAG + 8 * i + 4:BAG + 8 * i + 8] = bytes((6, 1, 0, 0))
chk("reject append to a full list", raises({"inv_bag_70": {"cat": 6, "id": 2, "qty": 0}}, bytes(full)))

# decode_gamedata exposes the inventory alongside the header fields
f = SV.decode_gamedata(gd)
chk("decode_gamedata carries inventory", f.get("inventory", {}).get("bag", [{}])[0].get("cat") == 9)

# ---------- real saves (skip when absent) ----------
HOME = os.path.expanduser("~")
US_PAYLOAD = os.path.join(HOME, "workspace/suikoden5-debug/saves/library/sparda-us-slot00.payload.bin")
PAL_CANDIDATES = [os.path.join(HOME, "workspace/user/files/SuikodenV_03NIV53_075_31.psu"),
                  os.path.join(HOME, "workspace/suikoden5-debug/fixtures/pal.psu")]
real = []
if os.path.exists(US_PAYLOAD):
    real.append(("US", open(US_PAYLOAD, "rb").read(), (6, 134, 10)))
pal_path = next((p for p in PAL_CANDIDATES if os.path.exists(p)), None)
if pal_path:
    real.append(("PAL", SV.read_gamedata_payload(pal_path), (65, 39, 29)))
if not real:
    print("SKIP real-save inventory checks: no fixture saves found")
for label, payload, counts in real:
    inv = SV.read_inventory(payload)
    got = tuple(len(inv[k]) for k in ("bag", "warehouse", "keyItems"))
    chk(f"{label}: list counts", got == counts, str(got))
    ok = True
    for entries in inv.values():
        for e in entries:
            if e["cat"] not in SV.ITEM_CAT_MAXID or not 1 <= e["id"] <= SV.ITEM_CAT_MAXID[e["cat"]]:
                ok = False
            if e["max"] != SV.stack_limit(e["cat"], e["id"]):
                ok = False
            if e["max"] and not 1 <= e["qty"] <= e["max"]:
                ok = False
            if not e["max"] and e["qty"] != 0:
                ok = False
    chk(f"{label}: every entry valid, limits match the disc tables", ok)
    same, ch = SV.apply_gamedata_edits(payload, {})
    chk(f"{label}: empty edit set is a byte-identical no-op", ch == 0 and same == payload)
    # in-place quantity round trip on the first stackable entry restores the bytes
    spot = next((("inv_%s_%d" % (ln, e["slot"]), e) for ln in ("bag", "warehouse")
                 for e in inv[ln] if e["max"] > 1), None)
    if spot:
        key, e = spot
        other = 1 if e["qty"] != 1 else 2
        step, _ = SV.apply_gamedata_edits(payload, {key: {"cat": e["cat"], "id": e["id"], "qty": other}})
        back, _ = SV.apply_gamedata_edits(step, {key: {"cat": e["cat"], "id": e["id"], "qty": e["qty"]}})
        chk(f"{label}: quantity round trip is byte-identical", back == payload)

print(f"inventory: {n - bad}/{n} checks passed")
sys.exit(1 if bad else 0)
