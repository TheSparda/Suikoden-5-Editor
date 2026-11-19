/* Real-path regression test for the drop-rate panel staging (issue #46).
 *
 * The Python web-model suite (drop_rates_web.py) stages the BATLE.ROM bytes
 * itself and never executes the page's JavaScript, so it cannot catch a
 * staging failure in web/iso.js. v1.20.2 shipped exactly such a failure:
 * stageBatleInto compared the bytes a File can return for the overlay's
 * sector-rounded slot against the slot length itself, so any source whose
 * file ends inside the overlay's final sector (the whole 243,956-byte
 * container present, the file stopping at its last byte) silently staged
 * nothing, and the live panel kept showing "BATLE.ROM is not in the
 * expected container form" even though the Python model was green.
 *
 * This test runs the REAL page code path in Node:
 *   - stageBatleInto / ensureBatleStaged extracted verbatim from web/iso.js
 *   - the Python GLUE extracted verbatim from web/app.js, run in a real
 *     Pyodide runtime with the real Editor/*.py modules and data tables
 *   - disc images fed as File objects (browser slice semantics: a slice
 *     past EOF returns only the bytes that exist)
 * against three variants built from the real US ISO:
 *   A. container-complete, file ends at the container's last byte
 *      (the live failure: must stage and read/write/restore cleanly)
 *   B. the same bytes zero-padded past the slot (full-size proxy: every
 *      byte the page reads is identical to a full disc's, whose slot
 *      tail past the container is zeros)
 *   C. the container itself truncated by 100 bytes (staging must still
 *      refuse and the panel error must remain: the guard is not weakened)
 *
 * On v1.20.5 sources variant A fails (5 checks) and B/C pass; the fix makes
 * all three pass. Skips cleanly (exit 0) when the pyodide npm package or
 * the US ISO is unavailable, like the other disc suites.
 * Pyodide lookup: $PYODIDE_DIR, web/node_modules/pyodide, then the local
 * pwtest checkout. ISO lookup: $S5_ISO, then <repo>/ISO/suikoden5-us.iso. */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.join(here, "..");
const ROOT = path.join(WEB, "..");

function skip(msg) { console.log("SKIP realpath-droprates: " + msg); process.exit(0); }

if (typeof File !== "function") skip("Node File global unavailable in this runtime");

const PY_CANDIDATES = [
  process.env.PYODIDE_DIR,
  path.join(WEB, "node_modules", "pyodide"),
  path.join(os.homedir(), "workspace", "suikoden5-debug", "pwtest", "node_modules", "pyodide"),
].filter(Boolean);
const PYDIR = PY_CANDIDATES.find((d) => fs.existsSync(path.join(d, "pyodide.js")));
if (!PYDIR) skip("pyodide npm package not found (set PYODIDE_DIR)");

const ISO_PATH_SRC = process.env.S5_ISO || path.join(ROOT, "ISO", "suikoden5-us.iso");
if (!fs.existsSync(ISO_PATH_SRC)) skip("US ISO not found at " + ISO_PATH_SRC + " (set S5_ISO)");

/* ---- extract the page's own sources, verbatim ------------------------- */
const appSrc = fs.readFileSync(path.join(WEB, "app.js"), "utf8");
const isoSrc = fs.readFileSync(path.join(WEB, "iso.js"), "utf8");

function evalLiteralArray(src, name) {
  const m = src.match(new RegExp("const " + name + " = (\\[[\\s\\S]*?\\]);"));
  if (!m) throw new Error("could not extract " + name + " from app.js");
  return eval(m[1]);
}
function extractGlue(src) {
  const start = src.indexOf("const GLUE = `");
  if (start < 0) throw new Error("GLUE not found in app.js");
  const from = start + "const GLUE = `".length;
  const end = src.indexOf("`", from);
  const raw = src.slice(from, end);
  if (raw.includes("${")) throw new Error("GLUE gained an interpolation; extractor needs updating");
  return new Function("return `" + raw + "`")();   // same evaluation the page performs
}
function extractFn(src, name) {
  const start = src.indexOf("async function " + name + "(");
  if (start < 0) throw new Error(name + " not found in iso.js");
  let i = src.indexOf("{", start), depth = 0, str = null, esc = false, line = false, block = false;
  for (; i < src.length; i++) {
    const c = src[i], n2 = src[i + 1];
    if (line) { if (c === "\n") line = false; continue; }
    if (block) { if (c === "*" && n2 === "/") { block = false; i++; } continue; }
    if (str) {
      if (esc) esc = false;
      else if (c === "\\") esc = true;
      else if (c === str) str = null;
      continue;
    }
    if (c === "/" && n2 === "/") { line = true; i++; continue; }
    if (c === "/" && n2 === "*") { block = true; i++; continue; }
    if (c === '"' || c === "'" || c === "`") { str = c; continue; }
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error("unterminated " + name);
}
function constNum(src, name) {
  const m = src.match(new RegExp("const " + name + " = (0x[0-9A-Fa-f]+|\\d+)"));
  if (!m) throw new Error("const " + name + " not found in iso.js");
  return Number(m[1]);
}

const MODULES = evalLiteralArray(appSrc, "modules");
const ENGINE_JSON = evalLiteralArray(appSrc, "ENGINE_JSON");
const GLUE = extractGlue(appSrc);
const ISO_END = constNum(isoSrc, "ISO_END");
const SECT = constNum(isoSrc, "SECT");
const stageSrc = extractFn(isoSrc, "stageBatleInto");
const ensureSrc = extractFn(isoSrc, "ensureBatleStaged");

/* ---- boot Pyodide exactly as app.js boot() does ----------------------- */
const req = createRequire(path.join(PYDIR, "package.json"));
const { loadPyodide } = req(path.join(PYDIR, "pyodide.js"));
const pyodide = await loadPyodide({ indexURL: PYDIR + path.sep });
pyodide.FS.mkdirTree("/editor");
for (const m of MODULES) {
  pyodide.FS.writeFile("/editor/" + m, fs.readFileSync(path.join(ROOT, "Editor", m), "utf8"));
}
for (const j of ENGINE_JSON) {
  const p = path.join(ROOT, "Editor", j);
  if (fs.existsSync(p)) pyodide.FS.writeFile("/editor/" + j, fs.readFileSync(p, "utf8"));
}
pyodide.runPython(GLUE);
const PYISO = {
  stageinfo: pyodide.globals.get("iso_stageinfo"),
  droprates: pyodide.globals.get("iso_droprates"),
  setdroprates: pyodide.globals.get("iso_setdroprates"),
  dropratesrestore: pyodide.globals.get("iso_dropratesrestore"),
};

/* The staging functions close over iso.js's module-level state; recreate
 * that scope and expose the one openISO step the staging depends on
 * (front slice into /iso.bin, retained File, pristine + undo baselines). */
const scope = new Function("window", "pyodide", "console", `
  let isoFile = null, ORIG = null, ISO_PREV = null, batleStaged = false;
  const ISO_PATH = "/iso.bin", ISO_END = ${ISO_END}, SECT = ${SECT};
  ${stageSrc}
  ${ensureSrc}
  return {
    openDisc(file, slice) {
      pyodide.FS.writeFile(ISO_PATH, slice);
      isoFile = file; ORIG = slice.slice(); ISO_PREV = slice.slice(); batleStaged = false;
    },
    ensureBatleStaged,
    baselines() { return { ORIG, ISO_PREV }; },
  };
`)({ PYISO }, pyodide, console);

/* ---- disc variants from the real US ISO ------------------------------- */
const fd = fs.openSync(ISO_PATH_SRC, "r");
function readRange(off, len) {
  const buf = Buffer.alloc(len);
  fs.readSync(fd, buf, 0, len, off);
  return new Uint8Array(buf);
}
const slice = readRange(0, ISO_END);
scope.openDisc(new File([slice], "probe.iso"), slice);   // stageinfo runs on the bare slice, as in the page
const info = JSON.parse(PYISO.stageinfo("/iso.bin"));
if (!info.lba) { console.log("FAIL stageinfo found no BATLE.ROM on the US ISO"); process.exit(1); }
const OFF = info.lba * SECT, SIZE = info.size, SLOT = info.slot;
const prefix = readRange(0, OFF + SIZE);                  // disc bytes through the container's last byte
fs.closeSync(fd);

const variants = {
  A: new File([prefix], "container-complete.iso"),
  B: new File([prefix, new Uint8Array(OFF + SLOT + 2 * 1024 * 1024 - prefix.length)], "padded.iso"),
  C: new File([prefix.subarray(0, prefix.length - 100)], "container-truncated.iso"),
};

let n = 0, bad = 0;
const chk = (name, cond, extra = "") => {
  n++; console.log((cond ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : "")); if (!cond) bad++;
};
const imgLen = () => pyodide.FS.readFile("/iso.bin").length;
const rates = () => JSON.parse(PYISO.droprates());
const STOCK = { slots: [40, 20, 10, 5, 1], dish: 100, cook: [20, 30] };
const CUSTOM = { slots: [41, 22, 13, 7, 3], dish: 90, cook: [25, 35] };
const isRates = (r, w) => !r.error && JSON.stringify(r.slots) === JSON.stringify(w.slots)
  && r.dish === w.dish && JSON.stringify(r.cook) === JSON.stringify(w.cook);

async function fullFlow(tag, file) {
  scope.openDisc(file, slice);
  await scope.ensureBatleStaged();
  chk(tag + ": staging extends /iso.bin to the end of the slot", imgLen() === OFF + SLOT, "len=" + imgLen());
  const img = pyodide.FS.readFile("/iso.bin");
  const { ORIG, ISO_PREV } = scope.baselines();
  const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
  chk(tag + ": baselines extended identically (staging registers as no change)",
      same(ORIG, img) && same(ISO_PREV, img));
  chk(tag + ": droprates reads stock rates", isRates(rates(), STOCK), JSON.stringify(rates()));
  const set = JSON.parse(PYISO.setdroprates(JSON.stringify(CUSTOM.slots), CUSTOM.dish, JSON.stringify(CUSTOM.cook)));
  chk(tag + ": setdroprates applies", !!set.ok, JSON.stringify(set));
  chk(tag + ": custom rates read back (the edit sticks)", isRates(rates(), CUSTOM), JSON.stringify(rates()));
  const rst = JSON.parse(PYISO.dropratesrestore());
  chk(tag + ": restore returns stock rates", !!rst.ok && isRates(rates(), STOCK), JSON.stringify(rates()));
}

await fullFlow("A container-complete", variants.A);
await fullFlow("B padded", variants.B);

scope.openDisc(variants.C, slice);
await scope.ensureBatleStaged();
chk("C container-truncated: staging still refuses (image left at the slice)", imgLen() === ISO_END, "len=" + imgLen());
const rc = rates();
chk("C container-truncated: panel error remains", !!rc.error && rc.error.includes("container form"), JSON.stringify(rc));

console.log(`\n${n - bad}/${n} realpath-droprates checks passed`);
process.exit(bad ? 1 : 0);
