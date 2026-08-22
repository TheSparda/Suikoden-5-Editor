/* Runs the Python ISO-engine round-trip (iso_roundtrip.py). Skips cleanly (exit 0)
 * if python3 isn't available, so minimal CI never breaks on a missing interpreter. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));

function hasPython() {
  const r = spawnSync("python3", ["--version"], { encoding: "utf8" });
  return r.status === 0;
}
if (!hasPython()) {
  console.log("SKIP iso-roundtrip: python3 not found (engine test skipped).");
  process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "iso_roundtrip.py")], { stdio: "inherit" });
process.exit(r.status || 0);
