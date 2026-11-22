/* Collapsible notes: long descriptive blocks render as a one-sentence
 * summary with the rest behind a More/Less expander (common.js noteFold /
 * noteFoldText, a <details> element, collapsed by default, full text kept
 * in the DOM). Runs the REAL helpers extracted verbatim from web/common.js
 * in a vm sandbox and pins: the markup shape, the sentence splitter
 * (quoted sentence ends, abbreviations, decimals), and the fold rule
 * (3+ sentences and long, or a true wall; short notes pass through
 * untouched). Also pins the static uses in index.html: the two mode
 * intros and the save privacy note carry the same markup by hand, with
 * every original sentence still present.
 *
 * Against the pre-fold code this suite fails: common.js defines none of
 * the helpers and index.html has no <details> notes. */
import fs from "fs";
import path from "path";
import vm from "vm";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..");
const commonSrc = fs.readFileSync(path.join(web, "common.js"), "utf8");
const htmlSrc = fs.readFileSync(path.join(web, "index.html"), "utf8");

let n = 0, fail = 0;
const ok = (name, cond, extra) => {
  n++; if (!cond) { fail++; console.error(`FAIL ${name}${extra ? "  " + extra : ""}`); }
  else console.log(`PASS ${name}`);
};

/* Extract one top-level function declaration by name (same extractor as
 * models-layout.mjs: string, comment and template-literal aware). */
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

const fns = ["noteFold", "splitNoteText", "noteFoldText"].map((f) => extractFn(commonSrc, f));
ok("common.js defines noteFold, splitNoteText and noteFoldText", fns.every(Boolean));

const sandbox = {
  console,
  esc: (x) => String(x == null ? "" : x)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
};
vm.createContext(sandbox);
vm.runInContext(fns.join("\n"), sandbox);
const { noteFold, splitNoteText, noteFoldText } = sandbox;

/* ---- noteFold markup: summary visible, rest present but collapsed ---- */
{
  const h = noteFold("First sentence.", "Second sentence. Third sentence.");
  ok("noteFold renders a details element", h.startsWith('<details class="fold">'));
  ok("noteFold is collapsed by default (no open attribute)", !h.includes(" open"));
  ok("noteFold puts the summary in the summary element",
     h.includes("<summary>First sentence.</summary>"));
  ok("noteFold keeps the rest in the DOM",
     h.includes('<div class="fold-rest">Second sentence. Third sentence.</div>'));
}

/* ---- splitNoteText: first sentence vs the rest ---- */
{
  const p = splitNoteText("Edits apply to a new game. Do not use emulator save states; use in-game save files.");
  ok("splitNoteText splits after the first sentence",
     !!p && p[0] === "Edits apply to a new game."
     && p[1] === "Do not use emulator save states; use in-game save files.", JSON.stringify(p));
}
{
  const p = splitNoteText('The skill menu refuses every skill from Thief upward with the message "Can\'t remove unique skills." This widens the check behind that refusal. It grants no ranks.');
  ok("splitNoteText handles a quoted sentence end",
     !!p && p[0].endsWith('unique skills."') && p[1].startsWith("This widens"), JSON.stringify(p));
}
{
  const p = splitNoteText("Mapped for the U.S. version only. It also works on PAL.");
  ok("splitNoteText does not split inside the U.S. abbreviation",
     !!p && p[0] === "Mapped for the U.S. version only." && p[1] === "It also works on PAL.", JSON.stringify(p));
}
{
  const p = splitNoteText("Scale by 1.5 or 2.0 at most. The baseline is remembered.");
  ok("splitNoteText does not split on decimals",
     !!p && p[0] === "Scale by 1.5 or 2.0 at most." && p[1] === "The baseline is remembered.", JSON.stringify(p));
}
ok("splitNoteText returns null for a single sentence",
   splitNoteText("Rune 0 = empty slot.") === null);
ok("splitNoteText returns null for empty text", splitNoteText("") === null);

/* ---- noteFoldText: the fold rule ---- */
{
  const two = "Starting stats plus each stat's per-level growth. Starting stats apply to a new game; growth affects level-ups.";
  ok("noteFoldText leaves a two-sentence note untouched",
     noteFoldText(two) === two);
}
{
  const short3 = "Elemental affinity grades, one per element. Higher is better at casting and resisting that element. Applies to a new game.";
  ok("noteFoldText leaves a short three-sentence note untouched",
     noteFoldText(short3) === short3);
}
{
  const long = "The five drop slots roll at fixed rates set by slot position, not by item: 40%, 20%, 10%, 5% and 1% in order, each on its own roll. "
    + "A Treasure Discovery support skill in the party adds a rank bonus on top: up to +30 on the 40/20/10 slots and up to +10 on the 5/1 slots at rank 7. "
    + "To maximise the chance of one item, put it in the 40% slot, or stack the same item in several slots: an item in all five drops about 59 percent of the time. "
    + "A duplicate success merges into the same entry, so one enemy never yields more than one copy.";
  const h = noteFoldText(long);
  ok("noteFoldText folds a long multi-sentence note", h.includes('<details class="fold">'));
  ok("noteFoldText summary is the first sentence",
     h.includes("<summary>The five drop slots roll at fixed rates set by slot position, not by item: 40%, 20%, 10%, 5% and 1% in order, each on its own roll.</summary>"));
  const flat = h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  ok("noteFoldText keeps the full text in the DOM",
     flat.includes("A duplicate success merges into the same entry, so one enemy never yields more than one copy.")
     && flat.includes("up to +30 on the 40/20/10 slots"));
}
{
  const h = noteFoldText("A <b>bold</b> claim. " + "More text follows here to make the note long enough to fold under the rule. ".repeat(3));
  ok("noteFoldText escapes markup in plain text", h.includes("A &lt;b&gt;bold&lt;/b&gt; claim."));
}

/* ---- index.html: the static intros carry the same pattern by hand ---- */
ok("index.html folds both mode intros",
   (htmlSrc.match(/<details class="sub fold">/g) || []).length === 2);
ok("index.html folds the save privacy note",
   htmlSrc.includes('<details class="note fold" id="privacy"'));
ok("index.html keeps the save intro sentences",
   htmlSrc.includes("A full save editor: hero, castle and army names")
   && htmlSrc.includes("Keep a backup of your save."));
ok("index.html keeps the ISO intro sentences",
   htmlSrc.includes("Edit the game disc itself: characters, gear,")
   && htmlSrc.includes("Back up first, or export a recipe."));

console.log(`\n${n - fail}/${n} note-fold checks passed`);
process.exit(fail ? 1 : 0);
