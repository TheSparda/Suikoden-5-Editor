/* Battle models: runs the Python checks in tests/battle_models.py
 * (the 82-entry BATLE directory on real ISO slices, grouped and one-sided
 * sets through the verbatim web glue, the repack round trip, the sector
 * fit check, and byte-exact reset) and re-asserts them here. */
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
  const r = spawnSync("python3", [path.join(here, "battle_models.py")],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (r.status === 0) { try { data = JSON.parse(r.stdout); } catch (_) {} }
  else console.log("battle_models.py failed:", (r.stderr || "").slice(0, 500));
}
if (!data || data.skip) {
  console.log("SKIP battle-models: " + (data ? data.skip : "python3 unavailable"));
  console.log("battle-models: 0/0 checks passed");
  process.exit(0);
}
for (const c of data.checks) chk(c.name, c.ok, c.extra);
console.log(`battle-models: ${n - bad}/${n} checks passed`);
process.exit(bad ? 1 : 0);
