// Save health audit for Suikoden V, shared by the save editor (app.js) and the
// Node tests. No DOM / no Pyodide: a plain snapshot in (built by
// s5save.audit_snapshot), findings out, so every rule is unit-testable.
//
// Every rule rests on a fact proven against the game or real saves, never on
// a guess about what "looks wrong":
//   - Eresh (10) and Euram (108) are mutually exclusive in the story, and ids
//     112-119 (Sialeeds and the antagonists) never carry a recruitment bit in
//     a save the game wrote (the completionist's only clear bits in 0-119).
//   - The game rebuilds the party from recruitment state on load and drops
//     members whose bit is clear (issue #32); every real save keeps the hero
//     in the party and no character twice.
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
    const rec = new Map((snap.roster || []).map((c) => [c.idx, !!c.recruited]));
    const isRec = (id) => rec.get(id) === true;
    if (isRec(10) && isRec(108)) {
      add({ id: "recruit-eres-euram", sev: "error", group: "Recruitment",
        title: "Both Eresh and Euram are marked recruited",
        detail: "The story makes them mutually exclusive: recruiting one sends the other " +
          "away, so no save the game writes itself can hold both flags. One of them was " +
          "set outside gameplay." });
    }
    const antag = [];
    for (let id = 112; id <= 119; id++) if (isRec(id)) antag.push(id);
    if (antag.length) {
      add({ id: "recruit-antagonists", sev: "error", group: "Recruitment",
        title: `Antagonist recruitment flags are set (${antag.map(charName).join(", ")})`,
        detail: "Ids 112-119 are Sialeeds and the villains; the game never sets their " +
          "recruitment bits. In a completionist save the only clear bits in 0-119 are " +
          "exactly these." });
    }
    let setCount = 0;
    for (let id = 0; id <= 111; id++) if (isRec(id)) setCount++;
    if (setCount === 112) {
      add({ id: "recruit-bulk", sev: "error", group: "Recruitment",
        title: "Every recruitment flag from 0 to 111 is set",
        detail: "That includes both mutually exclusive rivals and every missable " +
          "character. It is the fingerprint of a bulk recruit-all write (an editor " +
          "check-all or a cheat patch), not of played progress. If that was not " +
          "deliberate, restore a backup and check again." });
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
      if (!isRec(v)) {
        add({ id: `party-unrecruited-${slot}`, sev: "error", group: "Party",
          title: `Party slot ${slot + 1} holds ${charName(v)}, who is not recruited`,
          detail: "The game rebuilds the party from recruitment state on load and drops " +
            "members whose flag is clear (the Cathari case, issue #32). Recruit them or " +
            "clear the slot." });
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
