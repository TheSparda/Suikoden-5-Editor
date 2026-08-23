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
