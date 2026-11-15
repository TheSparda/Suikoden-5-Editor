/* Health audit (web/health-core.js): acceptance on the two real fixture saves
 * (Kevin's US save must pass clean; Eachna's PAL save must be flagged for its
 * bulk-set recruitment flags) plus one synthetic negative control per rule,
 * each tripping exactly its own rule. Skips the real-save part when python3
 * or the fixtures are unavailable. */
import { spawnSync } from "child_process";
import { createRequire } from "module";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const HealthCore = createRequire(import.meta.url)("../health-core.js");

let n = 0, bad = 0;
function chk(name, cond, extra = "") {
  n++;
  console.log((cond ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : ""));
  if (!cond) bad++;
}
const ids = (fs) => fs.map((f) => f.id).sort();

// ---- real saves -------------------------------------------------------------
let snaps = { us: null, pal: null };
if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status === 0) {
  const r = spawnSync("python3", [path.join(here, "health_audit.py")], { encoding: "utf8" });
  if (r.status === 0) { try { snaps = JSON.parse(r.stdout); } catch (_) {} }
}
if (snaps.us) {
  const fs = HealthCore.audit(snaps.us);
  chk("US save: no findings", fs.length === 0, JSON.stringify(ids(fs)));
} else console.log("SKIP US acceptance: fixture not found");
if (snaps.pal) {
  const fs = HealthCore.audit(snaps.pal);
  const got = ids(fs);
  chk("PAL save: flagged for Eresh+Euram", got.includes("recruit-eres-euram"), JSON.stringify(got));
  chk("PAL save: flagged for the bulk-set fingerprint", got.includes("recruit-bulk"));
  chk("PAL save: no inventory or party findings (its lists are well-formed)",
    !got.some((x) => x.startsWith("inv-") || x.startsWith("party-")), JSON.stringify(got));
} else console.log("SKIP PAL acceptance: fixture not found");

// ---- synthetic controls -------------------------------------------------------
function base() {
  const roster = [];
  for (let i = 0; i < 120; i++) roster.push({ idx: i, recruited: i === 0 });
  return {
    potch: 100,
    party: [0, 256, 256, 256, 256, 256, 256, 256, 256, 256],
    nameSlots: { heroName: { bytes: 6 }, armyName: { bytes: 5 }, castleName: { bytes: 6 } },
    roster,
    inventory: { bag: [], warehouse: [], keyItems: [] },
    counts: { bag: 0, warehouse: 0, keyItems: 0 },
    caps: { bag: 70, warehouse: 300, keyItems: 64 },
    catMaxId: { "6": 70, "9": 41 },
    limits: { "9": [5, 3, 2, 0, 6, 6, 6, 0, 0, 0, 3, 3, 3, 3, 0, 0, 0, 0, 0, 0, 0,
                  0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
  };
}
function control(name, mutate, expect) {
  const s = base();
  mutate(s);
  const got = ids(HealthCore.audit(s));
  chk(name, JSON.stringify(got) === JSON.stringify(expect.slice().sort()),
    `got ${JSON.stringify(got)}`);
}
chk("base snapshot: no findings", HealthCore.audit(base()).length === 0);
control("control: Eresh+Euram", (s) => { s.roster[10].recruited = 1; s.roster[108].recruited = 1; },
  ["recruit-eres-euram"]);
control("control: antagonist flag", (s) => { s.roster[115].recruited = 1; },
  ["recruit-antagonists"]);
control("control: bulk-set fingerprint", (s) => { s.roster.forEach((c) => { if (c.idx <= 111) c.recruited = 1; }); },
  ["recruit-bulk", "recruit-eres-euram"]);   // the pair rule necessarily co-fires
control("control: unrecruited party member", (s) => { s.party[1] = 20; },
  ["party-unrecruited-1"]);
control("control: duplicated party member", (s) => {
    s.roster[8].recruited = 1; s.party[1] = 8; s.party[2] = 8; },
  ["party-dup-2"]);
control("control: hero missing from party", (s) => {
    s.roster[8].recruited = 1; s.party = [8, 256, 256, 256, 256, 256, 256, 256, 256, 256]; },
  ["party-hero-missing"]);
control("control: quantity over the stack limit", (s) => {
    s.inventory.bag = [{ slot: 0, cat: 9, id: 1, qty: 6, max: 5, value: 0 }]; s.counts.bag = 1; },
  ["inv-qty-bag-0"]);
control("control: quantity on a one-per-slot item", (s) => {
    s.inventory.bag = [{ slot: 0, cat: 6, id: 1, qty: 2, max: 0, value: 0 }]; s.counts.bag = 1; },
  ["inv-single-bag-0"]);
control("control: stack-limit byte off the table", (s) => {
    s.inventory.bag = [{ slot: 0, cat: 9, id: 1, qty: 5, max: 4, value: 0 }]; s.counts.bag = 1; },
  ["inv-limit-bag-0"]);
control("control: unknown item", (s) => {
    s.inventory.bag = [{ slot: 0, cat: 9, id: 42, qty: 0, max: 0, value: 0 }]; s.counts.bag = 1; },
  ["inv-unknown-bag-0"]);
control("control: list count over capacity", (s) => { s.counts.bag = 71; },
  ["inv-count-bag"]);
control("control: potch over the clamp", (s) => { s.potch = 100000000; },
  ["potch-over"]);
control("control: unterminated name slot", (s) => { s.nameSlots.castleName.bytes = 17; },
  ["name-over-castleName"]);

console.log(`health-core: ${n - bad}/${n} checks passed`);
process.exit(bad ? 1 : 0);
