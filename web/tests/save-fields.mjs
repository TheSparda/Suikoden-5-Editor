/* Runs the save-field round-trip (save_fields.py). Skips cleanly (exit 0) if python3
 * isn't available, so minimal CI never breaks on a missing interpreter. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));

if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status !== 0) {
  console.log("SKIP save-fields: python3 not found (engine test skipped).");
  process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "save_fields.py")], { stdio: "inherit" });
process.exit(r.status || 0);
