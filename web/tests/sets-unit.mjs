/* Unit tests for the equipment-set engine (synthetic fixture, no disc needed).
 * Skips cleanly without python3 so minimal CI never breaks. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status !== 0) {
  console.log("SKIP sets-unit: python3 not found."); process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "sets_unit.py")], { stdio: "inherit" });
process.exit(r.status || 0);
