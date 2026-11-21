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
for (const s of ["common.js", "app.js", "iso.js", "diff-core.js", "s5save.py", "s5patch.py", "s5fields.py", "s5toggles.py"])
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

// 3a) Pyodide module manifest: every Editor module imported on the boot path
// must be in the app.js modules list, because boot writes only those files
// into the Pyodide FS before running the GLUE. v1.19.0 shipped without
// s5toggles.py in the list: the GLUE imports it eagerly, so both editors died
// with ModuleNotFoundError while the native suite stayed green (on disk the
// file sits beside s5patch.py; the web manifest is separate). Scanned here:
// the GLUE block in app.js plus every import in each bootstrapped module.
// Function-level imports inside s5patch's desktop CLI handlers (s5rom, s5pak,
// s5vcdiff) never run in the browser and are allowlisted; any other sibling
// import missing from the list fails this check.
{
  const boot = app.match(/const modules = \[([\s\S]*?)\]/);
  ok("app.js declares the Pyodide modules list", !!boot);
  const shipped = boot ? [...boot[1].matchAll(/"([^"]+\.py)"/g)].map((x) => x[1]) : [];
  const editorDir = path.join(web, "..", "Editor");
  const cliOnly = new Set(["s5rom.py", "s5pak.py", "s5vcdiff.py"]);
  const imported = new Set();
  const scan = (src) => {
    for (const mm of src.matchAll(/(?:^|\n)\s*(?:import|from)\s+(s5\w+)\b/g))
      imported.add(mm[1] + ".py");
  };
  scan(app);   // the GLUE block lives in app.js and runs at boot
  for (const mod of shipped) {
    ok(`bootstrapped module ${mod} exists`, fs.existsSync(path.join(editorDir, mod)));
    scan(fs.readFileSync(path.join(editorDir, mod), "utf8"));
  }
  const missing = [...imported].filter((f) =>
    fs.existsSync(path.join(editorDir, f)) && !shipped.includes(f) && !cliOnly.has(f));
  ok("every Editor module imported on the boot path is in the app.js modules list",
     missing.length === 0, missing.join(","));
  ok("app.js modules list includes s5toggles.py", shipped.includes("s5toggles.py"));
}

// 3b) new-feature wiring guards (behavioral coverage for undo/redo is in e2e/manual)
const isoSrc = read("iso.js"), commonSrc = read("common.js"), appSrc = read("app.js");
const fieldsSrc = fs.readFileSync(path.join(web, "..", "Editor", "s5fields.py"), "utf8");
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

// Save editor pickers: onPick carries single-quoted args (pickArmor(0,'helm',0)), so the
// onclick attribute must be double-quoted or the handler is cut off and clicks do nothing (#17).
ok("pickBtn double-quotes its onclick", /class="pickbtn"[^`]*onclick="\$\{onPick\}"/.test(appSrc));

// Recruitment display (#24, #39): the UI warns on the impossible Eresh+Euram
// pair. Recruitment comes from the game's status records; since the #39
// toggle shipped, the warning names an outside change or the editor's own
// toggle as the source and unrecruiting or a backup restore as the repair.
ok("app.js defines recruitPairWarn (#24)", appSrc.includes("function recruitPairWarn"));
ok("app.js pair warning names the toggle-era sources (#24)",
   appSrc.includes("Eresh and Euram are both shown as recruited")
   && appSrc.includes("the Recruited toggle in this editor"));
// The per-character recruit toggle (#39): the UI renders it, driven by the
// engine's status-word states, and the engine carries the one write path.
ok("app.js renders the recruit toggle (#39)",
   appSrc.includes("function recruitToggleHTML") && appSrc.includes("recruitToggleHTML(i, c)"));
ok("engine carries set_recruited (#39)",
   fs.readFileSync(path.join(web, "..", "Editor", "s5save.py"), "utf8").includes("def set_recruited"));

// Save-editor rune names: save bytes follow the rune DEFINITION table's order,
// not s5_rune_ids.json (the equip/code list). The save list must be the one the
// save editor loads, and its anchors are the innate-rune calibration.
const saveRunes = JSON.parse(fs.readFileSync(path.join(web, "..", "Editor", "s5_rune_save_ids.json"), "utf8"));
ok("save rune list has one entry per definition record", saveRunes.length === 99, `${saveRunes.length}`);
for (const [idx, nm] of [[13, "Star Rune"], [16, "Beast Rune"], [20, "Darkness Rune"],
                         [38, "White Rose Rune"], [39, "Shrike Rune"], [41, "Falcon Rune"],
                         [57, "Fire Lizard Rune"], [58, "Ice Dragon Rune"],
                         [83, "Parent Turtle Rn"], [84, "Godspeed Rune"], [85, "Raven Rune"],
                         [91, "Charm Rune"], [98, "Sun Rune"]])
  ok(`save rune ${idx} is ${nm}`, saveRunes[idx] === nm, saveRunes[idx]);
ok("save rune list has no phantom Critical/Magic",
   !saveRunes.includes("Critical Rune") && !saveRunes.includes("Magic Rune"));
ok("app.js loads the save-space rune names", appSrc.includes("s5_rune_save_ids.json"));

// Service worker shell: every module the app loads at runtime is precached
// (health-core.js and vcdiff.js were missing; noted in the v1.17.8 ship
// report). The cache name bumps together with the version bump at ship time.
{
  const swSrc = fs.readFileSync(path.join(web, "sw.js"), "utf8");
  ok("sw.js precaches health-core.js", swSrc.includes('"./health-core.js"'));
  ok("sw.js precaches vcdiff.js", swSrc.includes('"./vcdiff.js"'));
  ok("sw.js cache name bumped with the v1.22.0 version bump",
     swSrc.includes('const CACHE = "s5editor-v37"'));
}

// Rune slot availability (#6, display only): the save character view shows
// per-slot states from the community-derived threshold table (labelled),
// the record's fixed-rune flags, and warns when a removal strands a slot.
ok("app.js renders rune slot states (#6)",
   appSrc.includes("runeStatesHTML") && appSrc.includes("community-derived"));
ok("app.js warns on stranded rune slots (#6)", appSrc.includes("empty and locked until Lv"));
ok("s5save carries the community threshold table (#6)",
   fs.readFileSync(path.join(web, "..", "Editor", "s5save.py"), "utf8").includes("RUNE_SLOT_THRESHOLDS")
   && fs.readFileSync(path.join(web, "..", "Editor", "s5save.py"), "utf8").includes("rune_slot_states"));

// Starting loadouts (#28): the ISO character panel carries a new-game-facing
// loadout section driven by the glue's loadout adapters and the engine's
// validated writers (rune words are signed, so they cannot ride the generic
// numeric field path).
ok("app.js glue defines iso_loadout (#28)", appSrc.includes("def iso_loadout"));
ok("app.js glue defines iso_setloadout (#28)", appSrc.includes("def iso_setloadout"));
ok("iso.js renders the starting loadout section (#28)",
   isoSrc.includes("starting loadout (new game)") && isoSrc.includes("renderLoadoutSection"));
ok("iso.js labels loadouts new-game-facing (#28)", isoSrc.includes("New-game-facing"));
ok("s5patch defines the loadout engine (#28)",
   fs.readFileSync(path.join(web, "..", "Editor", "s5patch.py"), "utf8").includes("def read_loadout")
   && fs.readFileSync(path.join(web, "..", "Editor", "s5patch.py"), "utf8").includes("def write_loadout_skill"));

// New Game Plus control (#2/#19, corrected by the dash investigation):
// the byte at 0x12 is only a save-time display copy; the control binds to
// the real carry byte at 0x120E8. The old false claims must not return.
const saveSrc = fs.readFileSync(path.join(web, "..", "Editor", "s5save.py"), "utf8");
ok("app.js labels the control New Game Plus (#2)", appSrc.includes('data-lbl="New Game Plus"')
   && appSrc.includes("real NG+ carry byte")
   && !appSrc.includes("enables the fast-forward option"));
ok("s5save.py comments carry the correction (#2)", saveSrc.includes("cleared-game marker")
   && saveSrc.includes("save-time COPY")
   && saveSrc.includes('"newGamePlus": (0x120E8, 1, "num")')
   && !saveSrc.includes("enables fast-forward"));
// In-game verification (2026-11-21) showed the carry-over build resets the
// party and character levels to the NG+ opening state, so every user-facing
// description of the control must carry that warning.
const ngWarn = "resets the party and character levels to the New Game Plus opening state";
ok("NG+ descriptions warn about the carry-over reset (#18)",
   appSrc.includes(ngWarn) && appSrc.includes("Potch and your position carry over unchanged")
   && fs.readFileSync(path.join(web, "..", "README.md"), "utf8").includes(ngWarn)
   && fs.readFileSync(path.join(web, "..", "docs", "editing-guide.md"), "utf8").includes(ngWarn));

// 3b) Passives tab: exposes the verified passive runes, correctly labelled
ok("iso.js registers the Passives tab",
   /id:\s*"passive",\s*label:\s*"Passives"/.test(isoSrc) && isoSrc.includes("VIEW_RENDER.passive"));
ok("iso.js can force a rune always on", isoSrc.includes('view === "runealways"'));
const grpM = isoSrc.match(/const GROUPS = \[([\s\S]*?)\n  \];/);
ok("passive groups are defined", !!grpM);
const gsrc = grpM ? grpM[1] : "";
// Verified against the disc's own description pool (names cross-checked id 73-92).
ok("Champion's 79 = fewer encounters", /79:\s*"fewer encounters/.test(gsrc));
ok("Great Firefly 80 = more encounters", /80:\s*"more encounters/.test(gsrc));
// Fortune/Prosperity/Godspeed only reach the script-facing resolver gate; players confirmed
// no in-game effect (#18, #20). They must stay labelled as not working until that changes.
for (const [id, nm] of [[77, "Fortune"], [78, "Prosperity"], [82, "Godspeed"]])
  ok(`${nm} ${id} is labelled as not working`, new RegExp(`${id}:\\s*"NOT working`).test(gsrc));
ok("party-perk group says it doesn't work", /Party perks \(not working\)/.test(gsrc));
ok("Firefly 62 is not offered (it is Bull's Eye, not an encounter rune)", !/\b62\s*:/.test(gsrc));
ok("Raven 83 is not offered (it has no forceable gate)", !/\b83\s*:/.test(gsrc));
ok("checkbox caption states the mode, not a requirement",
   isoSrc.includes("Off: only works while equipped") && isoSrc.includes("always active, no rune needed"));
ok("tab bails out rather than half-render", isoSrc.includes("missing.length"));
ok("glue exposes the always-on adapters",
   appSrc.includes("def iso_runealways") && appSrc.includes("def iso_setrunealways"));
ok("engine keeps the gate scan inside the resolver",
   /RUNE_GATE_LO\s*=\s*0x253C00/.test(fieldsSrc) && /RUNE_GATE_HI\s*=\s*0x255D00/.test(fieldsSrc));

// 3c) Dawn Rune fourth-spell unlock. The patch site is found by signature so it works in
// both regions. Issue #47: the unlock lives only in Code patches (the s5toggles
// registry); the rune panel used to carry a duplicate checkbox wired to the same patch
// word. These guard the removal, the pointer that replaced the checkbox, and the
// signature words the surviving toggle relies on, all checkable without a disc
// (dawn_rune.py and toggles.py cover the real-disc behaviour).
ok("iso.js no longer offers the Dawn unlock on the rune panel", !isoSrc.includes("dawnrune"));
ok("rune panel points at the Code patches Dawn toggle",
   isoSrc.includes("is under Code patches") && isoSrc.includes("Dawn Rune: unlock all four spells"));
ok("Dawn pointer keeps the magic-level caveat", isoSrc.includes("still needs the magic level"));
ok("glue no longer exposes the duplicate Dawn adapters",
   !appSrc.includes("def iso_dawnrune") && !appSrc.includes("def iso_setdawnrune")
   && !appSrc.includes("setdawnrune"));
ok("rune panel flags the Dawn Rune without reading unlock state",
   appSrc.includes('"dawn": dawn') && !appSrc.includes("P.read_dawn_unlock"));
ok("engine pins the Dawn signature words",
   /DAWN_ANCHOR_WORD\s*=\s*0x24030004/.test(fieldsSrc)      // addiu v1,zero,4
   && /DAWN_STOCK_WORD\s*=\s*0x306400FF/.test(fieldsSrc)    // andi a0,v1,255
   && /DAWN_FORCE_WORD\s*=\s*0x24040000/.test(fieldsSrc));  // addiu a0,zero,N
ok("Dawn scan stays inside ELF text in both regions",
   /DAWN_SCAN_LO\s*=\s*0x0AD900/.test(fieldsSrc) && /DAWN_SCAN_HI\s*=\s*0x432C00/.test(fieldsSrc));
// Dawn + Twilight are real records, not synthetic placeholders (corrected 2026-09-13).
ok("grant table starts on the Dawn Rune",
   /RUNE_GRANT_BASE,\s*RUNE_GRANT_STRIDE,\s*RUNE_GRANT_COUNT\s*=\s*0x4E6D16,\s*0x46,\s*26/.test(fieldsSrc)
   && /"runegrant":\s*0x4FAE76/.test(fieldsSrc));
ok("Dawn and Twilight are no longer synthetic",
   /RUNE_GRANT_NAMES = \[\s*\n\s*"Dawn Rune", "Twilight Rune",/.test(fieldsSrc)
   && !/\{"name": "Dawn Rune"/.test(fieldsSrc));

// 3e) Gear equip requirements (issue #49): the Gear view's four threshold
// fields and the Code patches toggle share one site table in s5patch.
// These guard the wiring and the pinned gate words without a disc
// (gear_req.py and toggles.py cover the real-disc behaviour).
const patchSrc = fs.readFileSync(path.join(web, "..", "Editor", "s5patch.py"), "utf8");
const togSrc = fs.readFileSync(path.join(web, "..", "Editor", "s5toggles.py"), "utf8");
ok("iso.js offers the gear requirement fields", isoSrc.includes('view === "gearreq"')
   && isoSrc.includes("renderGearReqs"));
ok("gear panel carries the Cloak maximum-kills note",
   isoSrc.includes("Saint's Cloak maximum kills"));
ok("glue exposes the gear requirement adapters",
   appSrc.includes("def iso_gearreqs") && appSrc.includes("def iso_setgearreq")
   && appSrc.includes('gearreqs:g("iso_gearreqs")'));
ok("engine pins the gear gate shape and site offsets",
   /GEAR_REQ_TOP\s*=\s*0x2842/.test(patchSrc)
   && patchSrc.includes("0x337ECC") && patchSrc.includes("0x338D14"));
ok("toggle registry carries gear-requirements-off",
   togSrc.includes('"gear-requirements-off"')
   && togSrc.includes("Remove armor equip requirements"));

// 3g) Dash tuning (issue #18): the godspeed-dash toggle plus the two
// multiplier fields in the Code patches view, and the save editor's NG+
// control bound to the real carry byte. These guard the wiring and the
// pinned sites without a disc (dash_tuning.py, toggles.py and
// save_fields.py cover the real behaviour).
ok("iso.js offers the dash multiplier selects", isoSrc.includes('view === "dashmul"')
   && isoSrc.includes("dashtuning"));
ok("glue exposes the dash tuning adapters",
   appSrc.includes("def iso_dashtuning") && appSrc.includes("def iso_setdashmul")
   && appSrc.includes('dashtuning:g("iso_dashtuning")'));
ok("engine pins the dash sites for both regions",
   patchSrc.includes("0x1F4F34") && patchSrc.includes("0x1F5494")
   && patchSrc.includes("0x1F5080") && patchSrc.includes("0x1F55E0")
   && patchSrc.includes("0x1F5288") && patchSrc.includes("0x1F57E8"));
ok("toggle registry carries godspeed-dash in its own group",
   togSrc.includes('"godspeed-dash"') && togSrc.includes("Godspeed dash always on")
   && togSrc.includes('"Dash tuning"'));
ok("dash presets include the 4.0 tier in the engine",
   patchSrc.includes("DASH_SPEED_PRESETS = [1.5, 2.0, 2.5, 3.0, 4.0]")
   && patchSrc.includes("DASH_ANIM_PRESETS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]"));
ok("godspeed-dash is labelled proven after the in-game A/B run",
   /"godspeed-dash"[\s\S]{0,200}"effect": "proven"/.test(togSrc));

// 3d) Field-models tab: registered in both editors, and the two ELF tables it patches sit
// back-to-back on the disc (the pointer array starts exactly where the name table ends) —
// the invariant that pins the bases, since the real-disc check needs a disc CI hasn't got.
ok("iso.js registers the Field models tab",
   /id:\s*"model",\s*label:\s*"Field models"/.test(isoSrc) && isoSrc.includes("VIEW_RENDER.model"));
ok("iso.js can repoint a model", isoSrc.includes("onModelPick") && isoSrc.includes("PYISO.setmodel"));
ok("iso.js keeps a character's looks together", /MODEL_LINK\s*=\s*true/.test(isoSrc) && isoSrc.includes("modelLink"));
ok("s5patch groups a character's model ids", fs.readFileSync(path.join(web,"..","Editor","s5patch.py"),"utf8").includes("def model_group"));

// 3e) DATA.PAK asset tools: the engine's byte-oriented half (the browser does the reading,
// since the asset volume sits ~2 GB past the front slice the editor keeps in memory)
{
  const psrc = fs.readFileSync(path.join(web, "..", "Editor", "s5patch.py"), "utf8");
  for (const fn of ["def dir_entries", "def rofs_volume_at", "def rom_decode", "def rom_codec",
                    "def render_textures_data", "def decode_faces_data", "def model_info_data",
                    "def render_portrait_sheet_data", "def render_portrait_zip_data"])
    ok("s5patch exposes " + fn.slice(4), psrc.includes(fn));
  ok("iso.js registers the Assets tab",
     /id:\s*"assets",\s*label:\s*"Assets"/.test(isoSrc) && isoSrc.includes("VIEW_RENDER.assets"));
  ok("iso.js reads the disc in ranges", isoSrc.includes("function readRange") && isoSrc.includes("pakIndex"));
  for (const a of ["pakvolume", "pakdirents", "pakdecode", "paktextures", "pakfaces",
                   "paksheet", "pakzip", "pakmodelinfo"])
    ok("app.js exposes iso_" + a, appSrc.includes("def iso_" + a));
}
ok("app.js exposes the model adapters", appSrc.includes("def iso_models") && appSrc.includes("def iso_setmodel"));

// 3f) Excel / CSV round-trip + the enemy scaler. Behaviour is covered by
// tests/iso_roundtrip.py (which drives these very adapters); guard the wiring here.
{
  const psrc = fs.readFileSync(path.join(web, "..", "Editor", "s5patch.py"), "utf8");
  ok("iso.js registers the Excel / CSV tab",
     /id:\s*"csv",\s*label:\s*"Excel \/ CSV"/.test(isoSrc) && isoSrc.includes("VIEW_RENDER.csv"));
  for (const a of ["csvdatasets", "csvexport", "csvimport", "enemyscale", "esrestore"])
    ok("app.js exposes iso_" + a, appSrc.includes("def iso_" + a));
  ok("iso.js wires the CSV buttons", isoSrc.includes('$("csvExport")') && isoSrc.includes('$("csvImport")'));
  ok("CSV import strips the Excel BOM", isoSrc.includes("\\uFEFF") && psrc.includes('lstrip("\\ufeff")'));
  ok("CSV import reports capped values", isoSrc.includes("x.clamped") && psrc.includes('"clamped": clamped'));
  ok("s5patch caps instead of failing the write", psrc.includes("def _csv_field_widths"));
  ok("the CSV list offers the spell and rune tables",
     /"spells":\s*"Spells/.test(psrc) && /"runes":\s*"Runes/.test(psrc) &&
     /dataset == "spells"[\s\S]{0,400}write_spell_field/.test(psrc) &&
     /dataset == "runes"[\s\S]{0,400}write_rune_field/.test(psrc));
  ok("coded columns are refused rather than capped",
     psrc.includes("def _csv_code_columns") && psrc.includes("unknown code"));
  ok("the CSV tab prints the legend for coded columns",
     psrc.includes("def csv_legends") && appSrc.includes("P.csv_legends()") &&
     isoSrc.includes('$("csvLegend")'));
  ok("s5patch defines the enemy scaler",
     psrc.includes("def enemy_scale") && psrc.includes("def enemy_scale_restore"));
  ok("the CSV list offers the gear tables (issue #25)",
     ["gear-head", "gear-body", "gear-arm", "gear-foot", "gear-accessory"]
       .every((ds) => psrc.includes(`"${ds}"`)) && psrc.includes("_CSV_GEAR"));
  ok("gear CSV caps signed fields to their own range (issue #25)",
     psrc.includes("def _csv_field_ranges") && psrc.includes("write_armor_field"));
  ok("s5patch defines the growth scaler (issue #13)",
     psrc.includes("def growth_scale") && psrc.includes("def growth_scale_restore")
     && psrc.includes("_GS_STATS"));
  ok("glue exposes the growth scaler adapters",
     appSrc.includes("def iso_growthscale") && appSrc.includes("def iso_gsrestore"));
  ok("iso.js wires the growth scaler", isoSrc.includes('$("gsApply")') && isoSrc.includes('$("gsRestore")'));
  ok("the enemy scaler leaves rewards and drops alone",
     /_ES_STATS = \[f for f in F\.ENEMY_FIELDS if f\[0\] in/.test(psrc) &&
     !/_ES_STATS[\s\S]{0,220}Potch/.test(psrc));
  ok("iso.js wires the enemy scaler", isoSrc.includes('$("esApply")') && isoSrc.includes('$("esRestore")'));
  // the 60000 HP ceiling used to erase heavily-modded enemies from the list (issue #3)
  ok("enemy detection no longer has a 60000 HP ceiling", !psrc.includes("hp < 60000"));
}
{
  const fsrc = fs.readFileSync(path.join(web, "..", "Editor", "s5fields.py"), "utf8");
  const num = (k) => { const m = fsrc.match(new RegExp(k + "\\s*=\\s*(0x[0-9A-Fa-f]+|\\d+)")); return m ? Number(m[1]) : NaN; };
  const nameBase = num("RESOURCE_NAME_BASE"), stride = num("RESOURCE_NAME_STRIDE");
  const count = num("RESOURCE_NAME_COUNT"), ptrBase = num("MODEL_PTR_BASE");
  ok("NTSC name table runs straight into the pointer array",
     nameBase + stride * count === ptrBase, "0x" + (nameBase + stride * count).toString(16) + " vs 0x" + ptrBase.toString(16));
  const pal = fsrc.match(/"resource_names":\s*(0x[0-9A-Fa-f]+),\s*"model_ptr":\s*(0x[0-9A-Fa-f]+)/);
  ok("PAL bases are mapped", !!pal);
  if (pal) ok("PAL name table runs straight into its pointer array",
     Number(pal[1]) + stride * count === Number(pal[2]), pal[1] + " + " + stride * count);
  ok("model ids stay inside the slice", ptrBase + 4 * num("MODEL_PTR_COUNT") < 0x6A0000);
}

// 3b-ii) Skill-effect names: a row is one sub-effect, and every skill lists its effects
// in the same canonical order. Royal Paradise (98..120) grants all 23, so it spells that
// order out; each other group must be a RISING subsequence of it. The shipped names had
// the base block's ids 20..22 rotated (Sword Magic + sat before the two Incantation
// effects, giving a "% reduced casting time" row of 0,0,110..130), which this catches.
{
  const fx = JSON.parse(fs.readFileSync(path.join(web, "..", "Editor", "s5_skilleffect_names.json"), "utf8"));
  ok("skill-effect names count matches SKILLFX_COUNT", fx.length === 165, String(fx.length));
  const norm = (s) => {
    let t = (s.includes("(") ? s.slice(s.indexOf("(") + 1).replace(/\)\s*$/, "") : s).toLowerCase();
    for (const [a, b] of [["critical", "crit"], ["defense", "def"], ["technique", "tech"],
                          ["damg", "dmg"], ["damage", "dmg"], ["multiples", "multiple"],
                          ["mag def", "mdef"], ["magic def", "mdef"],
                          ["resist mag dmg", "resistmag"], ["resist magic dmg", "resistmag"],
                          ["reduced casting time", "casting"],
                          ["chance to change single target spell to area spell", "area"],
                          ["change single target spell to area spell", "area"]]) t = t.split(a).join(b);
    return t.replace(/[^a-z+% ]/g, " ").split(/\s+/).filter(Boolean).join(" ");
  };
  const canon = fx.slice(98, 121).map(norm);                    // Royal Paradise: all 23
  ok("Royal Paradise spells out all 23 effects", new Set(canon).size === 23, String(new Set(canon).size));
  const groups = { "base": [0, 23], "Raging Lion": [23, 25], "Fate Control": [25, 30],
    "Karmic Effect": [30, 38], "Armor of Gods": [38, 44], "Swift Foot": [44, 52],
    "Triple Harmony": [52, 60], "All out Strike": [60, 69], "Untold Clarity": [69, 85],
    "Divine Right": [85, 88], "Zen Sword": [88, 92], "Sacred Oath": [92, 98] };
  for (const [label, [a, b]] of Object.entries(groups)) {
    const pos = fx.slice(a, b).map((s) => canon.indexOf(norm(s)));
    ok(`${label} effects follow the canonical order`,
       pos.every((p, i) => p >= 0 && (i === 0 || p > pos[i - 1])), pos.join(","));
  }
  ok("Sword Magic + comes last in every group that grants it",
     [22, 91, 97, 120].every((i) => /Sword Magic \+/.test(fx[i]) && norm(fx[i]) === canon[22]));
  // The panel has to explain the two things that read as bugs: a row is a sub-effect, so a
  // 0 at E/D is "locked until C", and the scale is per-row rather than per-label.
  const isoFx = read("iso.js").slice(read("iso.js").indexOf("VIEW_RENDER.skillfx"));
  ok("skill-effect panel explains sub-effects and the per-row scale",
     /sub-effect/.test(isoFx) && /0 at E\/D/.test(isoFx) && /100-based/.test(isoFx));
}

// 3d) The field reference has to keep up with the editor: every ISO tab needs a section,
// and the three grade scales (which don't agree with each other) have to be spelled out.
{
  const guide = fs.readFileSync(path.join(web, "..", "docs", "editing-guide.md"), "utf8");
  const labels = [...read("iso.js").matchAll(/{ id: "\w+",\s*label: "([^"]+)" }/g)].map((m) => m[1]);
  ok("iso.js declares its views", labels.length >= 15, `${labels.length} views`);
  const missing = labels.filter((l) => !guide.includes(l));
  ok("the field reference covers every ISO tab", !missing.length, missing.join(", "));
  for (const s of ["Skill rank", "Character affinity", "Enemy affinity"])
    ok(`the field reference documents the ${s} scale`, guide.includes(s));
  ok("README links the field reference",
     fs.readFileSync(path.join(web, "..", "README.md"), "utf8").includes("docs/editing-guide.md"));
}

// 3c) Cache correctness: every versioned asset must carry the displayed release stamp,
// and same-origin fetches must revalidate (GitHub Pages sends max-age=600).
const idxSrc = read("index.html"), swSrc = read("sw.js");
const shown = (idxSrc.match(/id="ver">v([0-9.]+)</) || [])[1];
ok("index.html shows a version", !!shown, shown);
const stamps = [...idxSrc.matchAll(/(?:src|href)="(?:[\w.-]+)\?v=([0-9.]+)"/g)].map((m) => m[1]);
ok("versioned assets are stamped", stamps.length >= 5, `${stamps.length} stamped`);
ok("every stamp matches the shown version", stamps.every((v) => v === shown),
   [...new Set(stamps)].join(","));
ok("sw revalidates same-origin fetches", swSrc.includes('cache: "no-cache"'));
ok("offline fallback ignores the version query", swSrc.includes("ignoreSearch: true"));

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
