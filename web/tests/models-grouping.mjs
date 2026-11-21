/* Field models: runs the Python checks in tests/models_grouping.py
 * (real-table model groups on real ISO slices, a grouped set and reset
 * through the verbatim web glue, and the set_model guards) and
 * re-asserts them here. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));

let n = 0, bad = 0;
function chk(name, cond, extra = "") {
  n++;
  console.log((cond ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : ""));
  if (!cond) bad++;
}

let data = null;
if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status === 0) {
  const r = spawnSync("python3", [path.join(here, "models_grouping.py")],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (r.status === 0) { try { data = JSON.parse(r.stdout); } catch (_) {} }
  else console.log("models_grouping.py failed:", (r.stderr || "").slice(0, 500));
}
if (!data || data.skip) {
  console.log("SKIP models-grouping: " + (data ? data.skip : "python3 unavailable"));
  console.log("models-grouping: 0/0 checks passed");
  process.exit(0);
}
for (const c of data.checks) chk(c.name, c.ok, c.extra);
console.log(`models-grouping: ${n - bad}/${n} checks passed`);
process.exit(bad ? 1 : 0);
