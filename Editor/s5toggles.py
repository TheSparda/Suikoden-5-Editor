#!/usr/bin/env python3
"""Code-patch toggle framework: a registry of named on/off code patches,
each declaring its patch sites, with the same safety rules for all of
them (the patterns are the Suikoden III editor's Test tab audit and the
Suikoden IV editor's ISO toggles):

  - Sites are located by the toggle's own finder on the actual disc, and
    a signature-located site must be UNIQUE: the Dawn Rune finder
    refuses an ambiguous disc (anything but exactly one hit).
  - Before any write, every span of every site is classified: stock,
    patched, or FOREIGN (bytes the toggle does not recognise). A foreign
    span refuses the whole toggle before a single byte is written, so a
    disc patched by something else is never half-overwritten.
  - After writing, every span is re-read and verified against the
    intended bytes.
  - Restore is exact. Self-restoring toggles (the Dawn Rune: the stock
    word is a constant) restore from the registry alone. Gate toggles
    (the rune always-on gates) have per-site stock bytes (branch words
    differ per call site), so apply() returns the originals it displaced
    and restore requires them back, the same contract
    s5patch.write_rune_always_on already uses; the web editor keeps them
    in its mod journal, the CLI prints them as JSON.

Registered toggles are honest about their in-game effect. The Dawn Rune
unlock is proven (its mechanism is documented in s5fields). The three
perk always-on toggles (Fortune, Prosperity, Godspeed) are registered
with effect "not-working": the lab proved their gates feed only a
script-VM condition resolver that battle code never consumes
(ps2-re games/suikoden5/PASSIVES_CATHARI.md), and the reward-path trace
(ps2-re games/suikoden5/BATLE_REWARDS.md) found no statically provable
patch point that would make them work. They are in the registry so a
disc already patched with them can be verified and restored exactly,
not as a recommendation to apply them.
"""
import struct

import s5fields as F
import s5patch as P

_NOP = b"\x00\x00\x00\x00"


# ---------------------------------------------------------------------------
# Site finders. A site is {"label", "spans": [{"off", "stock", "patched"}]}
# where stock is the expected pristine bytes, or None when the stock shape
# is validated structurally instead of bytewise (gate branch words differ
# per call site); a span with stock None carries "stockOk": the finder has
# already validated the shape, and "cur": the bytes currently on disc.
# ---------------------------------------------------------------------------

_DAWN_ANCHOR_RE = None


def _dawn_locate(iso):
    """Locate the Dawn site by its invariant prefix alone (the clamp word
    + the jal to the setter), so a site word patched by something else
    still locates and can be classified foreign, instead of vanishing
    the way s5patch._dawn_site's full signature (which includes the
    variable word) makes it do. The prefix must hit exactly once."""
    global _DAWN_ANCHOR_RE
    if _DAWN_ANCHOR_RE is None:
        import re
        anchor = re.escape(struct.pack("<I", F.DAWN_ANCHOR_WORD))
        jal = b"..." + re.escape(b"\x0c")
        _DAWN_ANCHOR_RE = re.compile(anchor + jal, re.S)
    lo, hi = F.DAWN_SCAN_LO, F.DAWN_SCAN_HI
    blob = iso.rd(lo, hi - lo)
    hits = [m.start() for m in _DAWN_ANCHOR_RE.finditer(blob)]
    if len(hits) != 1:
        return None
    return lo + hits[0] + 8


def _dawn_sites(iso):
    off = _dawn_locate(iso)
    if off is None:
        return []
    s = {"off": off}
    stock = struct.pack("<I", F.DAWN_STOCK_WORD)
    patched = struct.pack("<I", F.DAWN_FORCE_WORD | F.DAWN_MAX)
    cur = iso.rd(s["off"], 4)
    w = struct.unpack("<I", cur)[0]
    # Any forced count (FORCE|1..4) is the on state, not just this
    # toggle's own count of 4: the Passives tab's finer control must not
    # read as foreign bytes.
    shape = ("off" if cur == stock else
             "on" if (w & 0xFFFF0000) == F.DAWN_FORCE_WORD else "foreign")
    return [{"label": "Dawn Rune spell-count word @0x%X" % s["off"],
             "shape": shape,
             "spans": [{"off": s["off"], "stock": stock, "patched": patched,
                        "cur": cur}]}]


def _gate_scan(iso, rune_id):
    """Canonical, forced and MALFORMED gates for one rune, mirroring
    s5patch._rune_gate_sites' scan of the resolver window exactly, except
    that gates whose shape is neither canonical nor forced are reported
    (as foreign) instead of skipped: a half-patched gate is precisely
    what the framework exists to catch."""
    lo, hi = F.RUNE_GATE_LO, F.RUNE_GATE_HI
    blob = iso.rd(lo - F.VADDR_DELTA, hi - lo)
    cnt = len(blob) // 4
    ws = struct.unpack("<%dI" % cnt, blob[:cnt * 4])
    target = (F.RUNE_HAS_FN & 0x0FFFFFFF) >> 2
    out = []
    for i, w in enumerate(ws):
        if (w >> 26) != 3 or (w & 0x3FFFFFF) != target:
            continue
        rid = None
        for k in range(max(0, i - 8), i + 2):
            u = ws[k]
            if (u >> 26) == 0x09 and ((u >> 21) & 31) == 0 and ((u >> 16) & 31) == 5:
                rid = u & 0xFFFF
        if rid != rune_id or i + 8 >= cnt:
            continue
        b1, andi, b2 = ws[i + 2], ws[i + 6], ws[i + 7]
        ok1 = (b1 >> 26) == 4 and ((b1 >> 21) & 31) == 2 and ((b1 >> 16) & 31) == 0
        ok2 = ((andi >> 26) == 0x0C and (andi & 0xFFFF) == 2
               and (b2 >> 26) == 4 and ((b2 >> 16) & 31) == 0)
        forced = b1 == 0 and b2 == 0 and (andi >> 26) == 0x0C and (andi & 0xFFFF) == 2
        shape = "foreign"
        if forced:
            shape = "on"
        elif ok1 and ok2:
            t1 = (lo + (i + 2) * 4) + 4 + P._s16(b1 & 0xFFFF) * 4
            t2 = (lo + (i + 7) * 4) + 4 + P._s16(b2 & 0xFFFF) * 4
            if t1 == t2:
                shape = "off"
        off1 = (lo + (i + 2) * 4) - F.VADDR_DELTA
        off2 = (lo + (i + 7) * 4) - F.VADDR_DELTA
        out.append({"label": "gate @0x%X" % (lo + i * 4), "shape": shape,
                    "spans": [
                        {"off": off1, "stock": None, "patched": _NOP,
                         "cur": struct.pack("<I", b1)},
                        {"off": off2, "stock": None, "patched": _NOP,
                         "cur": struct.pack("<I", b2)}]})
    return out


def _gate_sites(rune_id):
    def find(iso):
        return _gate_scan(iso, rune_id)
    return find


TOGGLES = [
    {"id": "dawn-unlock", "label": "Dawn Rune: unlock all four spells",
     "group": "Code patches", "effect": "proven",
     "blurb": "Forces the Dawn Rune's spell count to 4 from the start "
              "(write_dawn_unlock's mechanism, one signature-located word).",
     "find": _dawn_sites, "self_restoring": True},
    {"id": "fortune-always", "label": "Fortune always-on (not working in game)",
     "group": "Code patches", "effect": "not-working",
     "blurb": "NOPs Fortune's script-resolver gates. Proven not to reach "
              "battle rewards (PASSIVES_CATHARI.md); registered for exact "
              "verification and restore of already-patched discs.",
     "find": _gate_sites(77), "self_restoring": False},
    {"id": "prosperity-always", "label": "Prosperity always-on (not working in game)",
     "group": "Code patches", "effect": "not-working",
     "blurb": "NOPs Prosperity's script-resolver gates. Proven not to "
              "reach battle rewards (PASSIVES_CATHARI.md); registered for "
              "exact verification and restore of already-patched discs.",
     "find": _gate_sites(78), "self_restoring": False},
    {"id": "godspeed-always", "label": "Godspeed always-on (not working in game)",
     "group": "Code patches", "effect": "not-working",
     "blurb": "NOPs Godspeed's script-resolver gates. Proven not to reach "
              "movement or escape code (PASSIVES_CATHARI.md); registered "
              "for exact verification and restore of already-patched discs.",
     "find": _gate_sites(82), "self_restoring": False},
]
_BY_ID = {t["id"]: t for t in TOGGLES}


def _span_state(span):
    cur = span["cur"]
    if cur == span["patched"]:
        return "on"
    if span["stock"] is not None:
        return "off" if cur == span["stock"] else "foreign"
    return None     # gate span: the site's shape decides, not the bytes


def toggle_status(iso, toggle_id):
    """{id, label, effect, state, sites} where state is one of
    off / on / mixed / foreign / not-found, decided span by span."""
    t = _BY_ID[toggle_id]
    sites = t["find"](iso)
    if not sites:
        return {"id": toggle_id, "label": t["label"], "effect": t["effect"],
                "state": "not-found", "sites": []}
    out_sites, states = [], []
    for s in sites:
        if "shape" in s:                       # gate site
            st = {"off": "off", "on": "on", "foreign": "foreign"}[s["shape"]]
        else:
            ss = [_span_state(sp) for sp in s["spans"]]
            st = ss[0] if all(x == ss[0] for x in ss) else "mixed"
        states.append(st)
        out_sites.append({"label": s["label"], "state": st,
                          "spans": [{"off": sp["off"],
                                     "cur": sp["cur"].hex()} for sp in s["spans"]]})
    if all(x == states[0] for x in states):
        state = states[0]
    elif "foreign" in states:
        state = "foreign"
    else:
        state = "mixed"
    return {"id": toggle_id, "label": t["label"], "effect": t["effect"],
            "state": state, "sites": out_sites}


def list_toggles(iso):
    return [toggle_status(iso, t["id"]) for t in TOGGLES]


def set_toggle(iso, toggle_id, on, originals=None):
    """Apply (on=True) or restore (on=False) a registered toggle.

    Apply: every span must be stock-shaped or already patched; a foreign
    span refuses the whole toggle before anything is written. Returns
    {"ok", "changed", "originals"} where originals maps each changed
    span's offset (as a string) to its displaced bytes (hex): the exact
    restore token for non-self-restoring toggles.

    Restore: self-restoring toggles write their constant stock bytes.
    Gate toggles require `originals` (the map apply returned); without
    it, or with a span missing from it, the restore refuses rather than
    guess a branch word."""
    t = _BY_ID[toggle_id]
    st = toggle_status(iso, toggle_id)
    if st["state"] == "not-found":
        raise KeyError("toggle %s: no patch sites found on this disc" % toggle_id)
    if st["state"] == "foreign":
        raise ValueError("toggle %s: a patch site holds bytes this toggle "
                         "does not recognise; refusing to touch it" % toggle_id)
    sites = t["find"](iso)
    plan = []          # (off, bytes_to_write, displaced_hex)
    if on:
        for s in sites:
            for sp in s["spans"]:
                if sp["cur"] != sp["patched"]:
                    plan.append((sp["off"], sp["patched"], sp["cur"].hex()))
    else:
        orig = {int(k): bytes.fromhex(v) for k, v in (originals or {}).items()}
        for s in sites:
            for sp in s["spans"]:
                if sp["cur"] == sp["patched"] or (sp["stock"] is not None
                                                  and sp["cur"] != sp["stock"]):
                    if sp["off"] in orig:
                        plan.append((sp["off"], orig[sp["off"]], sp["cur"].hex()))
                    elif t["self_restoring"]:
                        plan.append((sp["off"], sp["stock"], sp["cur"].hex()))
                    else:
                        raise ValueError(
                            "toggle %s: restoring needs the original bytes "
                            "for the span at 0x%X (apply returns them); "
                            "refusing to guess" % (toggle_id, sp["off"]))
    displaced = {}
    for off, data, cur_hex in plan:
        iso.wr(off, data)
        displaced[str(off)] = cur_hex
    for off, data, _ in plan:                       # post-write verify
        if iso.rd(off, len(data)) != data:
            raise AssertionError("toggle %s: write at 0x%X did not verify"
                                 % (toggle_id, off))
    return {"ok": True, "changed": len(plan), "originals": displaced}
