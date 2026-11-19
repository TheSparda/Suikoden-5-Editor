/* Runs the CT-survey save-feature checks (ct_save_fields.py): held items,
 * EXP/SP, weapon sharpen level and rune spell uses on a synthetic payload
 * plus the real fixture saves. Skips cleanly (exit 0) if python3 isn't
 * available. */
import { spawnSync } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));

if (spawnSync("python3", ["--version"], { encoding: "utf8" }).status !== 0) {
  console.log("SKIP ct-save-fields: python3 not found (engine test skipped).");
  process.exit(0);
}
const r = spawnSync("python3", [path.join(here, "ct_save_fields.py")], { stdio: "inherit" });
process.exit(r.status || 0);
