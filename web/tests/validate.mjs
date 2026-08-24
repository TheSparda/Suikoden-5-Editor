/* Static validation — no browser, no Pyodide. Runs fast in CI / on session start.
 * Checks: every client JS parses; the app shell is wired (script tags, both mode
 * tabs); the service worker precaches the shell + every engine data file app.js
 * loads; the ISO slice window covers the highest table offset; manifest declares
 * the share target. */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..");
const read = (f) => fs.readFileSync(path.join(web, f), "utf8");

let n = 0, fail = 0;
const ok = (name, cond, extra) => {
  n++; if (!cond) { fail++; console.error(`FAIL ${name}${extra ? "  " + extra : ""}`); }
  else console.log(`PASS ${name}`);
};

// 1) every client JS parses
for (const f of ["diff-core.js", "common.js", "app.js", "iso.js", "sw.js"]) {
  try { new Function(read(f)); ok(`parses ${f}`, true); }
  catch (e) { ok(`parses ${f}`, false, e.message); }
}

// 2) index.html wiring
const html = read("index.html");
for (const s of ["diff-core.js", "common.js", "app.js", "iso.js", "pyodide.js"])
  ok(`index loads ${s}`, html.includes(s));
ok("index has Save tab", /data-mode="save"/.test(html));
ok("index has ISO tab", /data-mode="iso"/.test(html));
ok("index has force-refresh (B17)", html.includes('id="forceRefresh"'));
ok("index has undo/redo buttons (B18)", html.includes('id="isoUndoBtn"') && html.includes('id="isoRedoBtn"'));
ok("index has no-FS ISO note (A3)", html.includes('id="isoNoFsNote"'));

// 3) service worker precaches shell + every engine JSON app.js loads
const sw = read("sw.js");
for (const s of ["common.js", "app.js", "iso.js", "diff-core.js", "s5save.py", "s5patch.py", "s5fields.py"])
  ok(`sw precaches ${s}`, sw.includes(s));
const app = read("app.js");
const m = app.match(/const ENGINE_JSON = \[([\s\S]*?)\]/);
ok("app.js declares ENGINE_JSON", !!m);
if (m) {
  const jsons = [...m[1].matchAll(/"([^"]+\.json)"/g)].map((x) => x[1]);
  ok("ENGINE_JSON non-empty", jsons.length >= 10, `${jsons.length} files`);
  const missing = jsons.filter((j) => !sw.includes(j));
  ok("sw precaches every ENGINE_JSON", missing.length === 0, missing.join(","));
}

// 3b) new-feature wiring guards (behavioral coverage for undo/redo is in e2e/manual)
const isoSrc = read("iso.js"), commonSrc = read("common.js");
for (const fn of ["captureUndoStep", "isoUndo", "isoRedo", "charSkillPreset"])
  ok(`iso.js defines ${fn}`, isoSrc.includes("function " + fn));
ok("iso.js opens without FS Access (file input fallback)", /type = "file"/.test(isoSrc) && isoSrc.includes("isHandle"));
for (const fn of ["forceRefresh", "checkVersionBehind", "initUpdateCheck"])
  ok(`common.js defines ${fn}`, commonSrc.includes("function " + fn));
ok("app.js calls initUpdateCheck", app.includes("initUpdateCheck()"));

// 3c) equipment-set feature wiring + the RE'd constants (real-ISO behaviour is covered by
// tests/sets_iso.py, which CI skips for lack of a disc — so guard the constants here)
for (const fn of ["read_sets", "write_set_member", "write_set_bonus", "write_set_handler", "write_armor_summary", "write_set_gate", "write_set_gate_char", "write_armor_name", "armor_name_cap", "write_custom_set_bonus", "set_effect_targets"])
  ok(`s5patch defines ${fn}`, fs.readFileSync(path.join(web, "..", "Editor", "s5patch.py"), "utf8").includes("def " + fn));
{
  const fld = fs.readFileSync(path.join(web, "..", "Editor", "s5fields.py"), "utf8");
  ok("SET_DETECT_OFF == 0x281AD0", /SET_DETECT_OFF\s*=\s*0x281AD0/.test(fld));
  ok("SET_JT_OFF == 0x687B00", /SET_JT_OFF\s*=\s*0x687B00/.test(fld));
  ok("SET_COUNT == 10", /SET_COUNT\s*=\s*10/.test(fld));
  ok("set field labels verified vs the guide", /20: "HP"/.test(fld) && /46: "MDEF"/.test(fld));
  ok("save accessory slot @0xF9", /CHAR_ACCESSORY\s*=\s*0xF9/.test(
      fs.readFileSync(path.join(web, "..", "Editor", "s5save.py"), "utf8")));
}
ok("iso.js has the Sets view", isoSrc.includes('id: "sets"') && isoSrc.includes("function renderSet"));
ok("iso.js can reassign a set effect (custom sets)", isoSrc.includes('view === "sethandler"'));
ok("iso.js can edit piece descriptions", isoSrc.includes('view === "setdesc"'));
ok("iso.js has the custom bonus builder", isoSrc.includes("function cbApply") && isoSrc.includes("customsetbonus"));
ok("free code gap is the verified unreferenced one", /SET_CUSTOM_VADDR\s*=\s*0x4484B0/.test(fs.readFileSync(path.join(web,"..","Editor","s5fields.py"),"utf8")));
ok("iso.js can edit gear names", isoSrc.includes('view === "gearname"'));
ok("iso.js can edit the per-character restriction", isoSrc.includes('view === "setgate"') && isoSrc.includes("pickSetGateChar"));
ok("s5patch defines armor_summary_cap", fs.readFileSync(path.join(web,"..","Editor","s5patch.py"),"utf8").includes("def armor_summary_cap"));
ok("armor names table has an accessory list",
   !!JSON.parse(fs.readFileSync(path.join(web, "..", "Editor", "s5_armor_names.json"), "utf8")).accessory);

// 4) ISO slice window covers the highest editable table (name table ~0x699300)
const iso = read("iso.js");
const endM = iso.match(/ISO_END\s*=\s*(0x[0-9a-fA-F]+)/);
ok("iso.js defines ISO_END", !!endM);
if (endM) ok("ISO_END covers all tables", parseInt(endM[1], 16) >= 0x699300, endM[1]);

// 5) manifest share target + icons
const man = JSON.parse(read("manifest.webmanifest"));
ok("manifest has share_target", !!man.share_target && man.share_target.method === "POST");
ok("manifest has maskable icon", (man.icons || []).some((i) => i.purpose === "maskable"));

console.log(`\n${n - fail}/${n} passed`);
process.exit(fail ? 1 : 0);
