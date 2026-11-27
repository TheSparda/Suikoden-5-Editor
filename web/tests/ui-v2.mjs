/* v2.0 UI: two-pane workspace, per-view edit counts, embedded art, theme
 * first visit, small-screen action sheet, boot failure copy.
 *
 * Static and extraction checks over the real sources: the layout mover and
 * the count registry are extracted verbatim from web/iso.js and run in a vm
 * sandbox (the same pattern as models-layout.mjs); the theme picker is
 * extracted from web/common.js. The precache and art checks read the real
 * files on disk. Against the pre-v2 tree this suite fails throughout: no
 * rail, no groups, no registry, no art files. */
import fs from "fs";
import path from "path";
import vm from "vm";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..");
const read = (f) => fs.readFileSync(path.join(web, f), "utf8");
const isoSrc = read("iso.js"), commonSrc = read("common.js"), appSrc = read("app.js");
const indexSrc = read("index.html"), cssSrc = read("style.css"), swSrc = read("sw.js");

let n = 0, fail = 0;
const ok = (name, cond, extra) => {
  n++; if (!cond) { fail++; console.error(`FAIL ${name}${extra ? "  " + extra : ""}`); }
  else console.log(`PASS ${name}`);
};

/* Extract a top-level const literal (array/object) by balanced-bracket scan. */
function extractConst(src, name) {
  const i = src.indexOf("const " + name);
  if (i < 0) return null;
  const eq = src.indexOf("=", i);
  let j = eq + 1;
  while (/\s/.test(src[j])) j++;
  const open = src[j], close = open === "[" ? "]" : "}";
  let depth = 0, str = null, esc = false;
  for (let k = j; k < src.length; k++) {
    const c = src[k];
    if (str) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === str) str = null; continue; }
    if (c === '"' || c === "'" || c === "`") { str = c; continue; }
    if (c === open) depth++;
    else if (c === close) { depth--; if (depth === 0) return src.slice(j, k + 1); }
  }
  return null;
}
/* Extract one top-level function declaration by brace matching. */
function extractFn(src, name) {
  const m = src.match(new RegExp("(?:async\\s+)?function\\s+" + name + "\\s*\\("));
  if (!m) return null;
  const start = m.index;
  let i = src.indexOf("{", start), depth = 0, str = null, esc = false;
  for (; i < src.length; i++) {
    const c = src[i];
    if (str) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === str) str = null; continue; }
    if (c === '"' || c === "'" || c === "`") { str = c; continue; }
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  return null;
}

/* ---------- layout: one DOM, two homes ---------- */
ok("index has the rail with nav and session slots",
  indexSrc.includes('class="iso-rail"') && indexSrc.includes('id="railNavSlot"') && indexSrc.includes('id="railBarSlot"'));
ok("index has the card homes for the same nodes",
  indexSrc.includes('id="navSlot"') && indexSrc.includes('id="barSlot"'));
ok("exactly one #isoTabs and one #isoToolbar in the page",
  (indexSrc.match(/id="isoTabs"/g) || []).length === 1 &&
  (indexSrc.match(/id="isoToolbar"/g) || []).length === 1);
ok("iso.js moves the chrome with placeChrome",
  isoSrc.includes("function placeChrome()") && isoSrc.includes("railNavSlot") && isoSrc.includes("navSlot") &&
  isoSrc.includes('matchMedia("(min-width:1024px)")'));
ok("nav carries ARIA tab semantics",
  indexSrc.includes('role="tablist"') && isoSrc.includes('role="tab"') && isoSrc.includes('aria-selected') &&
  isoSrc.includes("ArrowRight"));

/* ---------- groups ---------- */
const viewsLit = extractConst(isoSrc, "ISO_VIEWS");
const groupsLit = extractConst(isoSrc, "ISO_VIEW_GROUPS");
ok("ISO_VIEWS and ISO_VIEW_GROUPS extract", !!viewsLit && !!groupsLit);
const VIEWS = vm.runInNewContext("(" + viewsLit + ")");
const GROUPS = vm.runInNewContext("(" + groupsLit + ")");
const grouped = GROUPS.flatMap(([, ids]) => ids);
ok("groups cover every view exactly once",
  grouped.length === VIEWS.length && new Set(grouped).size === VIEWS.length &&
  VIEWS.every(v => grouped.includes(v.id)),
  `grouped ${grouped.length} of ${VIEWS.length}`);
const groupOf = Object.fromEntries(GROUPS.map(([g, ids]) => [g, ids]));
ok("Roster group is Characters, Char names, Field models",
  JSON.stringify(groupOf["Roster"]) === JSON.stringify(["char", "name", "model"]));
ok("Items and Economy group", JSON.stringify(groupOf["Items and Economy"]) ===
  JSON.stringify(["gear", "sets", "runeprice", "healprice", "csv"]));
ok("Combat group", JSON.stringify(groupOf["Combat"]) ===
  JSON.stringify(["spell", "rune", "passive", "enemy", "unite", "mp", "skillfx", "balance"]));
ok("System group", JSON.stringify(groupOf["System"]) ===
  JSON.stringify(["patches", "assets", "changes", "ref"]));

/* ---------- per-view edit counts ---------- */
const regLit = extractConst(isoSrc, "EDIT_VIEW_BY_PREFIX");
const viewForEditKey = extractFn(isoSrc, "viewForEditKey");
const isoViewCounts = extractFn(isoSrc, "isoViewCounts");
ok("count registry and functions extract", !!regLit && !!viewForEditKey && !!isoViewCounts);
const ctx = vm.createContext({});
vm.runInContext(`const EDIT_VIEW_BY_PREFIX = ${regLit};`, ctx);
vm.runInContext(viewForEditKey + "\n" + isoViewCounts, ctx);
const count = (edits) => vm.runInContext(`isoViewCounts(${JSON.stringify(edits)})`, ctx);
ok("char field counts to Characters", count({ "char:0:stats:HP": {} }).counts.char === 1);
ok("battle model counts to Field models", count({ "battlemodel:3": {} }).counts.model === 1);
ok("drop rates count to Enemies", count({ "droprates": {} }).counts.enemy === 1);
ok("rune-always counts to Passives", count({ "runealways:82": {} }).counts.passive === 1);
ok("dash multiplier counts to Code patches", count({ "dashmul:speed": {} }).counts.patches === 1);
ok("csv import counts to Excel / CSV", count({ "csv:characters": {} }).counts.csv === 1);
ok("imported recipe is a dot, never a count", (() => { const r = count({ "imported": {} }); return r.dot === true && Object.keys(r.counts).length === 0; })());
ok("imported xdelta is a dot, never a count", (() => { const r = count({ "xdelta": {} }); return r.dot === true && Object.keys(r.counts).length === 0; })());
ok("counts aggregate across views", (() => {
  const r = count({ "char:1:a:b": {}, "char:2:a:b": {}, "spell:9:Power": {}, "imported": {} });
  return r.counts.char === 2 && r.counts.spell === 1 && r.dot === true;
})());
ok("empty edit map counts nothing", (() => { const r = count({}); return r.dot === false && Object.keys(r.counts).length === 0; })());
ok("toolbar refresh updates the nav badges",
  isoSrc.includes("updateNavCounts()") && isoSrc.includes("isoViewCounts(isoEdits)"));

/* ---------- session panel: Close and Revert all ---------- */
ok("Close and Revert all are wired",
  indexSrc.includes('id="isoCloseBtn"') && indexSrc.includes('id="isoRevertBtn"') &&
  isoSrc.includes("function closeISO()") && isoSrc.includes("function revertAllISO()"));
ok("Revert all restores the baseline bytes and clears the stacks",
  isoSrc.includes("pyodide.FS.writeFile(ISO_PATH, ORIG.slice())") &&
  isoSrc.includes("undoStack.length = 0; redoStack.length = 0"));

/* ---------- art: files, budget, precache ---------- */
const artFiles = ["crest.webp", "crest-sil.webp", "fret.svg", "boot-art.webp"];
ok("art files exist", artFiles.every(f => fs.existsSync(path.join(web, f))));
const alwaysBytes = ["crest.webp", "crest-sil.webp", "fret.svg"]
  .reduce((s, f) => s + fs.statSync(path.join(web, f)).size, 0);
ok("always-used art stays under the 90 KB budget", alwaysBytes <= 90 * 1024, `${alwaysBytes} bytes`);
ok("crest and fret are precached", swSrc.includes('"./crest.webp"') && swSrc.includes('"./crest-sil.webp"') && swSrc.includes('"./fret.svg"'));
ok("boot backdrop is NOT precached (lazy)", !swSrc.includes("boot-art"));
ok("crest-based PWA icons exist", ["icon-192.png", "icon-512.png", "icon-maskable-512.png"]
  .every(f => fs.existsSync(path.join(web, "icons", f))));
/* precache integrity: every SHELL entry exists on disk */
const shellLit = (() => {
  const i = swSrc.indexOf("const SHELL");
  const j = swSrc.indexOf("[", i), k = swSrc.indexOf("];", j);
  return swSrc.slice(j, k + 1);
})();
const SHELL = vm.runInNewContext("(" + shellLit + ")");
const missing = SHELL.filter(u => !fs.existsSync(path.resolve(web, u)));
ok("every SHELL precache entry exists on disk", missing.length === 0, missing.join(","));

/* ---------- theme: header switch, first visit ---------- */
ok("theme switcher lives in the header", (() => {
  const h = indexSrc.indexOf("<header>"), f = indexSrc.indexOf("<footer>");
  const t = indexSrc.indexOf('data-theme="twilight"');
  return h >= 0 && t > h && t < f;
})());
ok("footer no longer carries the theme buttons", !indexSrc.slice(indexSrc.indexOf("<footer>")).includes("data-theme="));
const pickTheme = extractFn(commonSrc, "pickTheme");
ok("pickTheme extracts", !!pickTheme);
const pickCtx = vm.createContext({});
vm.runInContext(pickTheme, pickCtx);
const pick = (saved, light) => vm.runInContext(`pickTheme(${JSON.stringify(saved)}, ${light})`, pickCtx);
ok("saved choice wins over the system", pick("sun", false) === "sun" && pick("twilight", true) === "twilight");
ok("first visit follows the system", pick(null, true) === "sun" && pick(null, false) === "twilight");
ok("old stored values migrate", pick("light", false) === "sun" && pick("dark", true) === "twilight");

/* ---------- small-screen action sheet ---------- */
ok("SMALL_MQ in iso.js matches the CSS media query",
  isoSrc.includes('"(max-width:820px), (max-height:660px) and (max-width:1023px)"') &&
  cssSrc.includes("@media (max-width:820px), (max-height:660px) and (max-width:1023px)"));
ok("Actions reveal carries aria state",
  indexSrc.includes('id="isoBarReveal"') && indexSrc.includes('aria-controls="isoToolbar"') &&
  isoSrc.includes("function setSheetOpen("));
ok("More toggle folds the session actions in the sheet",
  indexSrc.includes('id="isoMoreBtn"') && cssSrc.includes(".toolbar.more .opt"));
ok("reduced motion turns the sheet slide off",
  cssSrc.includes("@media (prefers-reduced-motion: reduce) { #isoToolbar.sheet { transition:none; } }"));

/* ---------- boot panel ---------- */
ok("boot panel carries the crest and the failure block",
  indexSrc.includes("boot-panel") && indexSrc.includes('id="bootFail"') && indexSrc.includes('id="bootDetail"'));
ok("boot failure copy is plain, raw error demoted",
  appSrc.includes('setBoot("The editor could not start.", 100)') &&
  appSrc.includes('$("bootDetail")') && !appSrc.includes("Failed to load: "));

/* ---------- save label: one logic ---------- */
ok("one save-label logic, rendered into its own span",
  isoSrc.includes("function setSaveLabel()") && indexSrc.includes('id="isoSaveLbl"') &&
  !isoSrc.includes('sv.textContent = "Export recipe"'));

console.log(`\nui-v2: ${n - fail}/${n} passed`);
process.exit(fail ? 1 : 0);
