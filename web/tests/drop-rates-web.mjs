import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const here = dirname(fileURLToPath(import.meta.url));
const r = spawnSync("python3", [join(here, "drop_rates_web.py")], { stdio: "inherit" });
process.exit(r.status ?? 1);
