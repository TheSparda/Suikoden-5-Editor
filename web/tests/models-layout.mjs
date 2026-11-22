/* Field models tab layout: the unified per-character layout. Runs the
 * REAL composition code extracted verbatim from web/iso.js
 * (modelLayoutHTML) in a vm sandbox against fabricated payloads in the
 * exact shapes the glue returns (read_models / model_targets /
 * read_battle_models / battle_model_targets rows), and pins the
 * structure: one block per character holding that character's field and
 * battle pickers together, a plain note where a character has no model
 * on one side, the character-less rows in a trailing list, the pairing
 * toggle (on by default) beside the two keep-together toggles, and the
 * degraded render when the battle overlay cannot be read.
 *
 * Against the pre-rework iso.js this suite fails: modelLayoutHTML does
 * not exist (the tab was two separate sections). */
import fs from "fs";
import path from "path";
import vm from "vm";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..");
const isoSrc = fs.readFileSync(path.join(web, "iso.js"), "utf8");

let n = 0, fail = 0;
const ok = (name, cond, extra) => {
  n++; if (!cond) { fail++; console.error(`FAIL ${name}${extra ? "  " + extra : ""}`); }
  else console.log(`PASS ${name}`);
};

/* Extract one top-level function declaration (optionally async) by name.
 * The scanner is string, comment and template-literal aware, including
 * ${ } expressions nesting further template literals. Returns null when
 * absent. (Same extractor as loadout-refresh.mjs.) */
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

const layoutFn = extractFn(isoSrc, "modelLayoutHTML");
ok("iso.js defines modelLayoutHTML", !!layoutFn);
ok("iso.js declares pairing on by default (let PAIR_MODELS = true)",
   /let PAIR_MODELS = true;/.test(isoSrc));

/* ---- fabricated payloads in the glue's shapes -------------------------
 * The Prince (3 field looks, 3 battle looks), Georg (3 field, 2 battle),
 * Roy (1 field, 2 battle), Raja (field only), one character-less field
 * row and one character-less battle row. */
const frow = (id, slot, file, char, look, looks) => ({
  id, slot, file, default_slot: slot, default_file: file, char,
  variant: 1, look, looks, placeholder: false, changed: false });
const fdata = {
  models: [
    { id: 0, placeholder: true, char: "", slot: -1, file: "", default_file: "(placeholder)", look: 1, looks: 1, changed: false },
    frow(2, 1, "pc001c.rom", "Hero/Prince", 1, 3),
    frow(3, 2, "pc002c.rom", "Raja", 1, 1),
    frow(22, 21, "pc021c.rom", "Georg", 1, 3),
    frow(108, 107, "pc107c.rom", "Roy", 1, 1),
    frow(115, 120, "pc121c.rom", "", 1, 1),
    frow(119, 220, "pc201c.rom", "Hero/Prince", 2, 3),
    frow(122, 221, "pc221c.rom", "Georg", 2, 3),
    frow(125, 321, "pc321c.rom", "Georg", 3, 3),
    frow(128, 400, "pc401c.rom", "Hero/Prince", 3, 3),
  ],
  targets: [
    { slot: 1, file: "pc001c.rom", char: "Hero/Prince", look: 1, looks: 3, group: "Cast" },
    { slot: 2, file: "pc002c.rom", char: "Raja", look: 1, looks: 1, group: "Cast" },
    { slot: 20, file: "pc020c.rom", char: "Cathari", look: 1, looks: 2, group: "Cast" },
    { slot: 21, file: "pc021c.rom", char: "Georg", look: 1, looks: 3, group: "Cast" },
    { slot: 107, file: "pc107c.rom", char: "Roy", look: 1, looks: 1, group: "Cast" },
    { slot: 120, file: "pc121c.rom", char: "", look: 1, looks: 1, group: "Cast" },
    { slot: 128, file: "ec000c.rom", char: "", look: 1, looks: 1, group: "Other characters" },
    { slot: 220, file: "pc201c.rom", char: "Hero/Prince", look: 2, looks: 3, group: "Story alternates" },
    { slot: 221, file: "pc221c.rom", char: "Georg", look: 2, looks: 3, group: "Story alternates" },
    { slot: 321, file: "pc321c.rom", char: "Georg", look: 3, looks: 3, group: "Story alternates" },
    { slot: 400, file: "pc401c.rom", char: "Hero/Prince", look: 3, looks: 3, group: "Story alternates" },
  ],
};
const brow = (id, name, char, char_id, look, looks) => ({
  id, name, file: name.toUpperCase() + ".ROM", default_name: name,
  default_file: name.toUpperCase() + ".ROM", char, char_id,
  look, looks, changed: false });
const bdata = {
  models: [
    brow(0, "btl_pc001", "Hero/Prince", 0, 1, 3),
    brow(1, "btl_pc401", "Hero/Prince", 0, 2, 3),
    brow(2, "btl_pc201", "Hero/Prince", 0, 3, 3),
    brow(18, "btl_pc021", "Georg", 20, 1, 2),
    brow(19, "btl_pc221", "Georg", 20, 2, 2),
    brow(74, "btl_pc107", "Roy", 106, 1, 2),
    brow(75, "btl_pc207", "Roy", 106, 2, 2),
    brow(78, "btl_pc121", "", 120, 1, 1),
  ],
  targets: [
    { name: "btl_pc001", file: "BTL_PC001.ROM", char: "Hero/Prince", char_id: 0, look: 1, looks: 3, group: "Cast" },
    { name: "btl_pc401", file: "BTL_PC401.ROM", char: "Hero/Prince", char_id: 0, look: 2, looks: 3, group: "Story alternates" },
    { name: "btl_pc201", file: "BTL_PC201.ROM", char: "Hero/Prince", char_id: 0, look: 3, looks: 3, group: "Story alternates" },
    { name: "btl_pc020", file: "BTL_PC020.ROM", char: "Cathari", char_id: 19, look: 1, looks: 2, group: "Cast" },
    { name: "btl_pc021", file: "BTL_PC021.ROM", char: "Georg", char_id: 20, look: 1, looks: 2, group: "Cast" },
    { name: "btl_pc221", file: "BTL_PC221.ROM", char: "Georg", char_id: 20, look: 2, looks: 2, group: "Story alternates" },
    { name: "btl_pc107", file: "BTL_PC107.ROM", char: "Roy", char_id: 106, look: 1, looks: 2, group: "Cast" },
    { name: "btl_pc207", file: "BTL_PC207.ROM", char: "Roy", char_id: 106, look: 2, looks: 2, group: "Story alternates" },
    { name: "btl_pc121", file: "BTL_PC121.ROM", char: "", char_id: 120, look: 1, looks: 1, group: "Cast" },
  ],
};

const sandbox = {
  console, Set,
  esc: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
  MODEL_LINK: true, BATTLE_LINK: true, PAIR_MODELS: true,
};
vm.createContext(sandbox);
vm.runInContext(layoutFn, sandbox);
const html = sandbox.modelLayoutHTML(fdata, bdata);

/* Split into headed blocks: chunk 0 is the intro + toggles, each later
 * chunk starts with its subhd text. */
const chunks = html.split('<div class="subhd">').slice(1);
const block = (head) => chunks.find((c) => c.startsWith(head)) || "";
const hero = block("Hero/Prince"), georg = block("Georg"), roy = block("Roy"),
      raja = block("Raja"), stray = block("Models with no character");

ok("the hero's block comes first", chunks.length > 1 && chunks[0].startsWith("Hero/Prince"),
   chunks.length ? chunks[0].slice(0, 30) : "no blocks");
ok("the hero's block holds his field and battle pickers together",
   hero.includes('data-mid="2"') && hero.includes('data-bmid="0"'));
ok("the hero's block holds all three looks of both systems",
   [2, 119, 128].every((i) => hero.includes(`data-mid="${i}"`))
   && [0, 1, 2].every((i) => hero.includes(`data-bmid="${i}"`)));
ok("look cells are labelled with their look number",
   hero.includes("look 1 of 3") && georg.includes("look 3 of 3"));
ok("Georg's third look pairs his field picker with the no-battle note",
   georg.includes('data-mid="125"') && georg.includes("No battle model"));
ok("a field-only character's block shows the note and no battle picker",
   raja.includes('data-mid="3"') && raja.includes("No battle model")
   && !raja.includes("data-bmid"));
ok("Roy's second battle look pairs with the no-field note",
   roy.includes('data-bmid="75"') && roy.includes("No field model"));
ok("the trailing list holds the character-less rows of both systems",
   stray.includes('data-mid="115"') && stray.includes('data-bmid="78"'));
ok("the pairing toggle renders beside the two keep-together toggles",
   html.includes('id="pairModels"') && html.includes('id="modelLink"')
   && html.includes('id="battleModelLink"'));
ok("the pairing toggle is checked while pairing is on",
   html.includes('id="pairModels" checked'));
const flat = html.replace(/\s+/g, " ");
ok("the intro states the pairing default and the separation of the sets",
   flat.includes("set the field and battle models together by default")
   && flat.includes("a field swap never shows in battle"));
ok("the intro keeps the proven-in-game wording",
   flat.includes("proven in game"));

sandbox.PAIR_MODELS = false;
const htmlOff = sandbox.modelLayoutHTML(fdata, bdata);
ok("the pairing toggle is unchecked while pairing is off",
   !htmlOff.includes('id="pairModels" checked') && htmlOff.includes('id="pairModels"'));

const htmlErr = sandbox.modelLayoutHTML(fdata, { error: "overlay not recognised" });
ok("a battle read error still renders the field pickers",
   htmlErr.includes('data-mid="2"'));
ok("a battle read error shows the error and no battle pickers or notes",
   htmlErr.includes("overlay not recognised") && !htmlErr.includes("data-bmid")
   && !htmlErr.includes("No battle model"));

console.log(`\n${n - fail}/${n} models-layout checks passed`);
process.exit(fail ? 1 : 0);
