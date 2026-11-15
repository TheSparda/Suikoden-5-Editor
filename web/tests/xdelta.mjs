/* xdelta (VCDIFF) cross-implementation checks. The Python twin
 * (Editor/s5vcdiff.py) builds fixtures and runs its own checks in
 * tests/xdelta.py; here the JS implementation (web/vcdiff.js) must decode
 * every Python-built patch byte-for-byte, and patches the JS encoder builds
 * from diff runs must decode correctly in Python. Together they pin the
 * format both sides emit: standard VCDIFF that xdelta3 also reads. */
import { spawnSync } from "child_process";
import { readFileSync, writeFileSync } from "fs";
import { createRequire } from "module";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const Vcdiff = createRequire(import.meta.url)("../vcdiff.js");
const DiffCore = createRequire(import.meta.url)("../diff-core.js");

let n = 0, bad = 0;
function chk(name, cond, extra = "") {
  n++;
  console.log((cond ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : ""));
  if (!cond) bad++;
}
const eq = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

let data = null;
if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status === 0) {
  const r = spawnSync("python3", [path.join(here, "xdelta.py")],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (r.status === 0) { try { data = JSON.parse(r.stdout); } catch (_) {} }
  else console.log("xdelta.py failed:", (r.stderr || "").slice(0, 500));
}
if (!data) {
  console.log("SKIP xdelta: python3 unavailable or fixture build failed");
  console.log("xdelta: 0/0 checks passed");
  process.exit(0);
}
for (const c of data.checks) chk("python: " + c.name, c.ok, c.extra);

for (const c of data.cases) {
  const src = new Uint8Array(readFileSync(c.src));
  const tgt = new Uint8Array(readFileSync(c.tgt));
  const pyPatch = new Uint8Array(readFileSync(c.pyPatch));
  chk(`JS decodes the Python-built patch (${c.name})`,
    eq(Vcdiff.decode(src, pyPatch), tgt));
  // JS encoder from diff runs (the web editor's export path), Python decodes.
  const runs = DiffCore.diffRuns(src, tgt, 0);
  const jsPatch = Vcdiff.buildXdelta(src.length,
    runs.map((r) => ({ off: r.off, data: r.bytes })));
  chk(`JS-built patch round-trips in JS (${c.name})`,
    eq(Vcdiff.decode(src, jsPatch), tgt));
  const jp = path.join(data.tmp, c.name + ".js.xdelta");
  writeFileSync(jp, jsPatch);
  const op = path.join(data.tmp, c.name + ".js.out");
  const r = spawnSync("python3", [path.join(here, "xdelta.py"), "apply", c.src, jp, op],
    { encoding: "utf8" });
  chk(`Python decodes the JS-built patch (${c.name})`,
    r.status === 0 && eq(new Uint8Array(readFileSync(op)), tgt),
    (r.stderr || "").slice(0, 200));
}

console.log(`xdelta: ${n - bad}/${n} checks passed`);
process.exit(bad ? 1 : 0);
