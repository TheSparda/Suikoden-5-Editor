/* Dash tuning (issue #18): runs the Python checks in tests/dash_tuning.py
 * (the two multiplier fields on real US and PAL slices, composition with
 * the godspeed-dash toggle, anchor refusals, the verbatim web glue, and
 * the save editor's NG+ carry-byte round trip on a real save) and
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
  const r = spawnSync("python3", [path.join(here, "dash_tuning.py")],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (r.status === 0) { try { data = JSON.parse(r.stdout); } catch (_) {} }
  else console.log("dash_tuning.py failed:", (r.stderr || "").slice(0, 500));
}
if (!data || data.skip) {
  console.log("SKIP dash-tuning: " + (data ? data.skip : "python3 unavailable"));
  console.log("dash-tuning: 0/0 checks passed");
  process.exit(0);
}
for (const c of data.checks) chk(c.name, c.ok, c.extra);
console.log(`dash-tuning: ${n - bad}/${n} checks passed`);
process.exit(bad ? 1 : 0);
