/* Issue #50 regression: the starting-loadout skill selects were built from
 * the caps snapshot taken when the Characters panel rendered, so raising a
 * skill cap in the equipable skills table left the skill unselectable until
 * the ISO was reopened, even though the engine would have accepted the rank
 * all along. The fix refreshes the selects in place after a successful caps
 * write (refreshLoadoutCaps, hooked from afterIsoWrite): options rebuilt
 * over the new 0..cap range, cap pills and LO_CUR updated, still-valid
 * selections preserved, and a selection above a newly lowered cap clamped
 * through the normal loadout write path.
 *
 * This suite runs the REAL page code extracted verbatim from web/iso.js
 * (renderLoadoutSection, commitIso/onIsoField, applyIsoWrite, trackIso,
 * afterIsoWrite, refreshLoadoutCaps, loSetSkill) in a vm sandbox against a
 * stub engine that implements the documented PYISO contract (caps writes
 * land immediately; loadout() re-reads caps at call time; setloadout
 * rejects a rank above the cap, like s5patch.write_loadout_skill). The DOM
 * is a minimal stub keyed to the ids and classes the shipped markup uses
 * (loEff<idx> spans inside .fld blocks, .pill cap labels, one select per
 * skill), seeded from the real rendered HTML.
 *
 * Against the pre-fix iso.js this suite fails: refreshLoadoutCaps does not
 * exist and afterIsoWrite has no caps hook. */
import fs from "fs";
import path from "path";
import vm from "vm";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..");
const isoSrc = fs.readFileSync(path.join(web, "iso.js"), "utf8");
const commonSrc = fs.readFileSync(path.join(web, "common.js"), "utf8");

let n = 0, fail = 0;
const ok = (name, cond, extra) => {
  n++; if (!cond) { fail++; console.error(`FAIL ${name}${extra ? "  " + extra : ""}`); }
  else console.log(`PASS ${name}`);
};

/* Extract one top-level function declaration (optionally async) by name.
 * The scanner is string, comment and template-literal aware, including
 * ${ } expressions nesting further template literals (renderLoadoutSection
 * builds option markup exactly that way). Returns null when absent. */
function extractFn(src, name) {
  const m = src.match(new RegExp("(?:async\\s+)?function\\s+" + name + "\\s*\\("));
  if (!m) return null;
  const start = m.index;
  let i = src.indexOf("{", start), depth = 0;
  const stack = [];
  let str = null, esc = false, line = false, block = false;
  for (; i < src.length; i++) {
    const c = src[i], n2 = src[i + 1];
    if (line) { if (c === "\n") line = false; continue; }
    if (block) { if (c === "*" && n2 === "/") { block = false; i++; } continue; }
    if (str === "`") {
      if (esc) { esc = false; continue; }
      if (c === "\\") { esc = true; continue; }
      if (c === "`") { str = null; continue; }
      if (c === "$" && n2 === "{") { stack.push(depth); depth = 0; str = null; i++; continue; }
      continue;
    }
    if (str) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === str) str = null;
      continue;
    }
    if (c === "/" && n2 === "/") { line = true; i++; continue; }
    if (c === "/" && n2 === "*") { block = true; i++; continue; }
    if (c === '"' || c === "'") { str = c; continue; }
    if (c === "`") { str = "`"; continue; }
    if (c === "{") { depth++; continue; }
    if (c === "}") {
      if (depth === 0 && stack.length) { depth = stack.pop(); str = "`"; continue; }
      depth--;
      if (depth === 0 && !stack.length) return src.slice(start, i + 1);
    }
  }
  return null;
}
const decl = (name) => {
  const m = isoSrc.match(new RegExp("let " + name + " = null;[^\\n]*"));
  return m ? m[0] : "";
};

const FNS = ["commitIso", "onIsoField", "applyIsoWrite", "trackIso", "markDirty",
             "afterIsoWrite", "loWordLabel", "renderLoadoutSection",
             "refreshLoadoutCaps", "loTrack", "loSetSkill"];
/* common.js loads before iso.js on the page, so its noteFold helper is
 * part of the environment the extracted code runs in. */
const parts = [extractFn(commonSrc, "noteFold"),
               decl("LO_CUR"), decl("LO_ORIG"), decl("LO_CID"),
               ...FNS.map((f) => extractFn(isoSrc, f))];
ok("iso.js declares the loadout state (LO_CUR / LO_ORIG / LO_CID)",
   parts[1] && parts[2] && parts[3]);
ok("iso.js defines refreshLoadoutCaps", !!extractFn(isoSrc, "refreshLoadoutCaps"));
ok("afterIsoWrite hooks the equipable skills table",
   (extractFn(isoSrc, "afterIsoWrite") || "").includes("equipable skills"));
const code = parts.filter(Boolean).join("\n");

/* ---- stub engine: the documented PYISO contract over in-memory tables -- */
const NAMES = ["Stamina", "Attack", "Defense", "Technique", "Vitality", "Agility",
  "Magic", "Magic Defense", "Incantation", "Sword of Magic", "Raging Lion",
  "Fate Control", "Karmic Effect", "Armor of Gods", "Swift Foot", "Triple Harmony",
  "All-out Strike", "Untold Clarity", "Divine Right", "Zen Sword", "Sacred Oath",
  "Royal Paradise", "Thief", "Mow Down", "Pierce", "Freeze", "??? (unused)",
  "Barrage", "Long Throw", "Dragon Special", "Forge", "Combat Teacher",
  "Chain Magic", "Analyze", "Potch Finder", "Treasure Hunt", "Escape Route",
  "Healing", "Treatment", "Haggle", "Trade In", "Cook", "Rune Sage", "Bard",
  "Perfect Pitch", "Appraisal", "Bath", "Tutor"];
const RANKS = ["None", "E", "D", "C", "B", "A", "S", "SS"];
const FREEZE = 25, ANALYZE = 33, AGILITY = 5;
const caps = new Array(48).fill(0);
caps[AGILITY] = 6;                       // a normal skill with a real cap
const starts = new Array(48).fill(0);
let loadoutCalls = 0;
const payload = () => ({
  gated: false, id: 0, level: 1, armor: [0, 0, 0, 0],
  runes: ["head", "right", "left"].map((slot) => ({ slot, word: 0, perm: 0, kind: "empty" })),
  runeNames: [],
  equippedSlots: starts.map((s, i) => (s > 0 ? i + 1 : 0)).filter(Boolean).slice(0, 2),
  skills: NAMES.map((name, i) => ({
    index: i, name, start: starts[i], cap: caps[i],
    effective: starts[i] ? starts[i] : (caps[i] ? 1 : 0),
  })),
});
const PYISO = {
  loadout(cid) { loadoutCalls++; return JSON.stringify({ loadout: payload() }); },
  setchar(identJson, editsJson) {
    for (const e of JSON.parse(editsJson)) {
      if (e.table !== "equipable skills") continue;
      const idx = NAMES.indexOf(e.field);
      if (idx >= 0) caps[idx] = e.value;
    }
    return JSON.stringify({ ok: true });
  },
  setloadout(cid, kind, idx, value) {
    if (kind === "skill") {
      if (value > caps[idx])
        return JSON.stringify({ error: `rank ${value} exceeds this character's cap ${caps[idx]} for the skill` });
      starts[idx] = value;
    }
    return JSON.stringify({ ok: true });
  },
  chars() { return JSON.stringify({ chars: [{ id: 0, name: "Prince" }] }); },
};

/* ---- stub DOM, seeded from the real rendered HTML ---------------------- */
const flds = [];        // per skill: {pill, sel, eff}
const eqEl = { innerHTML: "" };
const sandbox = {
  console, JSON, Set,
  window: { PYISO },
  isoMAPS: { ranks: RANKS },
  isoEdits: {},
  SMALL_KINDS: new Set(), PICKER_KINDS: new Set(),
  RUNE_GATE_WORDS: {}, TOGGLE_ORIGINALS: {},
  esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
  nameFor: (kind, v) => "#" + v,
  spin: () => {},
  toast: () => {},
  updateIsoToolbar: () => {},
  captureUndoStep: () => {},
  q: () => null,
  $: (id) => {
    if (id === "loEquipped") return eqEl;
    const m = /^loEff(\d+)$/.exec(id);
    return m && flds[+m[1]] ? flds[+m[1]].eff : null;
  },
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const probe = (expr) => vm.runInContext(expr, sandbox);

const html = sandbox.renderLoadoutSection(0, "Prince");
ok("renderLoadoutSection renders the loadout section", typeof html === "string" && html.includes("starting loadout"));
const chunks = html.split('<div class="fld">').slice(1);
const skillChunks = chunks.filter((c) => c.includes("start rank"));
ok("the render has one field block per skill", skillChunks.length === 48,
   `got ${skillChunks.length}`);
const optCount = (s) => (s.match(/<option/g) || []).length;
for (let i = 0; i < 48; i++) {
  const chunk = skillChunks.find((c) => c.includes(`${NAMES[i]} start rank`)) || "";
  const selHtml = (chunk.match(/<select[^>]*>([\s\S]*?)<\/select>/) || [])[1] || "";
  const fld = {
    pill: { textContent: ((chunk.match(/<span class="pill">([^<]*)<\/span>/) || [])[1] || "") },
    sel: { innerHTML: selHtml, value: String(starts[i]), dataset: { orig: String(starts[i]) } },
    eff: null,
  };
  fld.eff = { textContent: "", closest: () => fld };
  fld.querySelector = (sel) => (sel === "select" ? fld.sel : sel === ".pill" ? fld.pill : null);
  flds[i] = fld;
}
const aChunk = skillChunks.find((c) => c.includes("Analyze start rank")) || "";
ok("at render, Analyze (cap 0) offers only the None option",
   optCount(aChunk) === 1 && aChunk.includes('cap None'), aChunk.slice(0, 120));
ok("at render, Agility (cap 6) offers ranks 0..6",
   optCount(skillChunks.find((c) => c.includes("Agility start rank")) || "") === 7);

/* A caps-table field element, shaped like renderField's output for the
 * equipable skills table (data-field is the skill name; data-orig is the
 * render-time baseline, which the refresh must not disturb). */
const capEl = (idx, value) => ({
  type: "number", value: String(value),
  dataset: { view: "char", ident: JSON.stringify({ id: 0 }),
             table: "equipable skills", field: NAMES[idx],
             key: `char:0:equipable skills:${NAMES[idx]}`, orig: "0",
             lbl: NAMES[idx], grp: "Prince", kind: "rank" },
  classList: { contains: () => false, toggle: () => {} },
});

/* 1. Raise Analyze's cap 0 -> 7 (SS): the select must offer ranks at once. */
await sandbox.onIsoField(capEl(ANALYZE, 7));
ok("the caps write landed in the engine", caps[ANALYZE] === 7);
ok("after the cap raise, the Analyze select offers ranks 0..7 without a reload",
   optCount(flds[ANALYZE].sel.innerHTML) === 8,
   `options=${optCount(flds[ANALYZE].sel.innerHTML)}`);
ok("the Analyze cap pill updates to cap SS",
   flds[ANALYZE].pill.textContent === "cap SS", flds[ANALYZE].pill.textContent);
ok("LO_CUR carries the new cap",
   probe("LO_CUR.skills[33].cap") === 7);
ok("an untouched cap-0 skill (Freeze) still offers only None",
   optCount(flds[FREEZE].sel.innerHTML) === 1
   && flds[FREEZE].pill.textContent === "cap None");

/* 2. Choose rank 5 (A) through the real loadout write path. */
flds[ANALYZE].sel.value = "5";
sandbox.loSetSkill(0, ANALYZE, flds[ANALYZE].sel);
ok("choosing rank 5 writes through to the engine", starts[ANALYZE] === 5);
ok("LO_CUR tracks the chosen rank", probe("LO_CUR.skills[33].start") === 5);

/* 3. Lower the cap 7 -> 6: rank 5 is still valid and must be preserved. */
await sandbox.onIsoField(capEl(ANALYZE, 6));
ok("lowering the cap to 6 rebuilds the options over 0..6",
   optCount(flds[ANALYZE].sel.innerHTML) === 7);
ok("a still-valid selection is preserved (rank 5 kept)",
   flds[ANALYZE].sel.value === "5" && starts[ANALYZE] === 5);

/* 4. Lower the cap 6 -> 3, below the chosen rank: the selection clamps
 * and the clamp is written through, so DOM, engine and journal agree. */
await sandbox.onIsoField(capEl(ANALYZE, 3));
ok("lowering the cap to 3 rebuilds the options over 0..3",
   optCount(flds[ANALYZE].sel.innerHTML) === 4);
ok("the out-of-range selection clamps to the new cap",
   flds[ANALYZE].sel.value === "3", `value=${flds[ANALYZE].sel.value}`);
ok("the clamp is written through to the engine",
   starts[ANALYZE] === 3, `start=${starts[ANALYZE]}`);
ok("LO_CUR tracks the clamped rank",
   probe("LO_CUR.skills[33].start") === 3
   && probe("LO_CUR.skills[33].cap") === 3);

/* 5. A non-caps character write must not trigger a loadout refresh. */
const before = loadoutCalls;
await sandbox.onIsoField({
  type: "number", value: "5",
  dataset: { view: "char", ident: JSON.stringify({ id: 0 }),
             table: "starting equipment", field: "Level",
             key: "char:0:starting equipment:Level", orig: "1",
             lbl: "Level", grp: "Prince", kind: "num" },
  classList: { contains: () => false, toggle: () => {} },
});
ok("a non-caps write does not re-read the loadout", loadoutCalls === before,
   `calls ${before} -> ${loadoutCalls}`);

console.log(`\n${n - fail}/${n} loadout-refresh checks passed`);
process.exit(fail ? 1 : 0);
