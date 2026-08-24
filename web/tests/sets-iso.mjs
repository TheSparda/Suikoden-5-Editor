/* Runs the equipment-set engine test. Skips cleanly without python3 (or without an
 * ISO — the script itself handles that), so minimal CI never breaks. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status !== 0) {
  console.log("SKIP sets-iso: python3 not found."); process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "sets_iso.py")], { stdio: "inherit" });
process.exit(r.status || 0);
