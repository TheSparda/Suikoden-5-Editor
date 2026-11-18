// Save health audit for Suikoden V, shared by the save editor (app.js) and the
// Node tests. No DOM / no Pyodide: a plain snapshot in (built by
// s5save.audit_snapshot), findings out, so every rule is unit-testable.
//
// Every rule rests on a fact proven against the game or real saves, never on
// a guess about what "looks wrong":
//   - Recruitment state is the per-character status word (issue #39): the
//     snapshot carries each character's word band (active / initialized /
//     none) as wordState. Eresh (10) and Euram (108) are mutually exclusive
//     in the story, so no save the game writes shows both as recruited.
//   - Every party member in every genuine save checked holds an active
//     status record; the party arrays are the real party storage (issue
//     #32), and every real save keeps the hero in the party and no
//     character twice.
//   - Inventory entries obey the append functions' rules: the stack-limit
//     byte equals the disc's master-table byte (s5save.STACK_LIMITS, verified
//     on 283 real entries), quantity sits in 1..limit, and one-per-slot items
//     (limit 0) carry quantity 0. List counts never exceed capacity.
//   - addPotch clamps potch at 99,999,999, so a larger stored value was not
//     written by the game.
//   - Name slots are 17 bytes holding at most 16 characters + a terminator.
// A finding is {id, sev, group, title, detail}; severity order error > warn
// > info. Correct or absent, never wrong: a rule that needs data the
// snapshot does not carry simply does not run.
(function (root) {
  const SEV_RANK = { error: 0, warn: 1, info: 2 };
  const POTCH_MAX = 99999999;
  const NAME_MAX = 16;
  const LIST_LABEL = { bag: "Party bag", warehouse: "Warehouse", keyItems: "Key items" };
  const NAME_LABEL = { heroName: "Hero", armyName: "Army", castleName: "Castle" };

  function audit(snap, opts) {
    opts = opts || {};
    const charName = (id) => (opts.charName ? opts.charName(id) : "#" + id);
    const out = [];
    const add = (f) => out.push(f);
    if (!snap) return out;

    // --- recruitment -------------------------------------------------------
    // wordState is the status word's band alone (no party override), so the
    // party check below can compare the party against the word itself.
    // Two rules from the old bitfield era are gone (issue #39): the
    // antagonist-flags rule, whose premise was the fake field (Sialeeds
    // legitimately holds an active word during her story stint, so it fired
    // on genuine saves), and the bulk-write fingerprint, which described
    // that same field's shape; the Eresh/Euram rule covers the impossible
    // state it was really pointing at.
    const rec = new Map((snap.roster || []).map((c) => [c.idx, c]));
    const isRec = (id) => { const c = rec.get(id); return !!c && !!c.recruited; };
    const wordState = (id) => { const c = rec.get(id); return c ? c.wordState : undefined; };
    if (isRec(10) && isRec(108)) {
      add({ id: "recruit-eres-euram", sev: "error", group: "Recruitment",
        title: "Both Eresh and Euram are marked recruited",
        detail: "The story makes them mutually exclusive: recruiting one sends the other " +
          "away, so no save the game writes itself can show both as recruited. One of " +
          "their records was set outside gameplay." });
    }

    // --- party ---------------------------------------------------------------
    const party = snap.party || [];
    const seen = new Map();
    party.forEach((v, slot) => {
      if (v == null || v === 256) return;
      if (seen.has(v)) {
        add({ id: `party-dup-${slot}`, sev: "warn", group: "Party",
          title: `${charName(v)} is in party slots ${seen.get(v) + 1} and ${slot + 1}`,
          detail: "The same character twice in the party is not a state the game builds." });
      } else seen.set(v, slot);
      const ws = wordState(v);
      if (ws && ws !== "active") {
        add({ id: `party-unrecruited-${slot}`, sev: "warn", group: "Party",
          title: `Party slot ${slot + 1} holds ${charName(v)}, whose recruitment record is not active`,
          detail: "Every party member in a save the game wrote holds an active status " +
            "record; this character's record reads " + ws + " instead. The party arrays " +
            "are the real party storage, so the assignment itself is written correctly, " +
            "but whether the game keeps them in the party on load is unproven (issue #39). " +
            "Clear the slot if that was not deliberate." });
      }
    });
    if (party.length && !party.includes(0)) {
      add({ id: "party-hero-missing", sev: "error", group: "Party",
        title: "The hero is in no party slot",
        detail: "Every save the game writes keeps the hero in the party; without him " +
          "the party cannot be rebuilt on load." });
    }

    // --- inventory -----------------------------------------------------------
    const limits = snap.limits || {}, catMaxId = snap.catMaxId || {};
    for (const [list, entries] of Object.entries(snap.inventory || {})) {
      const label = LIST_LABEL[list] || list;
      const raw = (snap.counts || {})[list], cap = (snap.caps || {})[list];
      if (raw != null && cap != null && raw > cap) {
        add({ id: `inv-count-${list}`, sev: "error", group: "Inventory",
          title: `${label} count is ${raw}, over its capacity of ${cap}`,
          detail: "The game never lets a list grow past its capacity; a larger count " +
            "was written by something else and the tail entries will not load." });
      }
      (entries || []).forEach((e, k) => {
        const maxId = catMaxId[String(e.cat)];
        if (!maxId || e.id < 1 || e.id > maxId) {
          add({ id: `inv-unknown-${list}-${k}`, sev: "error", group: "Inventory",
            title: `${label} entry ${k + 1} is category ${e.cat} #${e.id}, which is not in the game's item table`,
            detail: "No item in the game's data has that category and id." });
          return;
        }
        const lim = ((limits[String(e.cat)] || [])[e.id - 1]) || 0;
        if (e.max !== lim) {
          add({ id: `inv-limit-${list}-${k}`, sev: "warn", group: "Inventory",
            title: `${label} entry ${k + 1} (category ${e.cat} #${e.id}) has stack limit ${e.max}; the game's table says ${lim}`,
            detail: "Every entry the game writes carries the master table's stack-limit " +
              "byte. A different byte means the entry was written without the table." });
        }
        if (lim > 0 && (e.qty < 1 || e.qty > lim)) {
          add({ id: `inv-qty-${list}-${k}`, sev: "error", group: "Inventory",
            title: `${label} entry ${k + 1} (category ${e.cat} #${e.id}) has quantity ${e.qty}, outside 1..${lim}`,
            detail: "Stacks the game writes always hold between 1 and the item's stack limit." });
        }
        if (lim === 0 && e.qty !== 0) {
          add({ id: `inv-single-${list}-${k}`, sev: "error", group: "Inventory",
            title: `${label} entry ${k + 1} (category ${e.cat} #${e.id}) carries a quantity of ${e.qty}`,
            detail: "That item is one-per-slot: the game stores each copy as its own entry " +
              "with quantity 0. A quantity on such an entry is a shape it never writes." });
        }
      });
    }

    // --- global --------------------------------------------------------------
    if (snap.potch > POTCH_MAX) {
      add({ id: "potch-over", sev: "error", group: "Save file",
        title: `Potch is ${snap.potch}, over the game's maximum of 99,999,999`,
        detail: "The game's add-potch routine clamps at 99,999,999, so a larger stored " +
          "value was not written by the game." });
    }
    for (const [k, s] of Object.entries(snap.nameSlots || {})) {
      if (s && s.bytes > NAME_MAX) {
        add({ id: `name-over-${k}`, sev: "error", group: "Save file",
          title: `The ${NAME_LABEL[k] || k} name fills all ${s.bytes} bytes of its slot, with no terminator`,
          detail: "The game stores names in 17-byte slots holding at most 16 characters " +
            "plus a terminator. A full, unterminated slot was not written by the game." });
      }
    }

    out.sort((a, b) => SEV_RANK[a.sev] - SEV_RANK[b.sev]);
    return out;
  }

  function counts(findings) {
    const c = { error: 0, warn: 0, info: 0 };
    findings.forEach((f) => { c[f.sev] = (c[f.sev] || 0) + 1; });
    return c;
  }

  const api = { audit, counts, POTCH_MAX, NAME_MAX };
  if (typeof module !== "undefined" && module.exports) module.exports = api;   // Node (CJS)
  root.HealthCore = api;                                                       // browser global
})(typeof self !== "undefined" ? self : globalThis);
