/* Runs the recruitment status-word suite (recruitment.py, issue #39).
 * Skips cleanly (exit 0) if python3 isn't available. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));

if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status !== 0) {
  console.log("SKIP recruitment: python3 not found (engine test skipped).");
  process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "recruitment.py")], { stdio: "inherit" });
process.exit(r.status || 0);
