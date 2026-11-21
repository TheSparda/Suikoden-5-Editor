/* Runs the gear equip requirements test (issue #49): the Gear view's
 * threshold fields on real US and PAL slices, plus the verbatim web glue
 * adapters. Skips cleanly without python3 (or without an ISO, the script
 * itself handles that), so minimal CI never breaks. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status !== 0) {
  console.log("SKIP gear-req: python3 not found."); process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "gear_req.py")], { stdio: "inherit" });
process.exit(r.status || 0);
