/* Runs the Python .ROM/DATA.PAK round-trip suite (rom_pak.py) against the real
 * disc. Skips cleanly (exit 0) if python3 isn't available; the Python side
 * itself skips when no real ISO is present (S5_ISO or <repo>/ISO/). */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));

function hasPython() {
  const r = spawnSync("python3", ["--version"], { encoding: "utf8" });
  return r.status === 0;
}
if (!hasPython()) {
  console.log("SKIP rom-pak: python3 not found (engine test skipped).");
  process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "rom_pak.py")], { stdio: "inherit" });
process.exit(r.status || 0);
