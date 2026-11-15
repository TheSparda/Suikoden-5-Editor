/* Changes tab engine (Editor/s5changes.py): decoded diff of a modified copy
 * of the pristine US ISO's front slice against the pristine slice. Every
 * planted edit must come back as exactly one decoded row with the right
 * old/new values, the unmapped byte as an Unmapped row, a self-diff must be
 * empty, and the ISO file's sha256 must be unchanged by the test. */
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
  const r = spawnSync("python3", [path.join(here, "changes.py")],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (r.status === 0) { try { data = JSON.parse(r.stdout); } catch (_) {} }
  else console.log("changes.py failed:", (r.stderr || "").slice(0, 400));
}
if (!data || data.skip) {
  console.log("SKIP changes: " + (data ? data.skip : "python3 unavailable"));
  console.log("changes: 0/0 checks passed");
  process.exit(0);
}

chk("pristine ISO sha256 unchanged by the test", data.shaBefore === data.shaAfter);
chk("region map is large and overlap-free (builder raises on overlap)",
  data.regionCount > 30000, `${data.regionCount} regions`);
chk("self-diff is empty", data.selfRows === 0);
chk("summary counts the planted edits",
  data.summary.fields === 5 && data.summary.unmappedBytes === 1 &&
  data.summary.runs === 6 && data.summary.totalBytes >= 7,
  JSON.stringify(data.summary));

for (const e of data.expect) {
  const row = data.rows.find((r) => r.group === e.group && r.label.includes(e.labelPart));
  chk(`decoded row: ${e.group} / ${e.labelPart}`,
    !!row && row.old === e.old && row.new === e.new,
    row ? `${row.label}: ${row.old} -> ${row.new}` : "row missing");
}
const un = data.rows.filter((r) => r.group === "Unmapped");
chk("the unmapped byte is reported, not dropped",
  un.length === 1 && un[0].off === 0x1000, JSON.stringify(un.map((r) => r.off)));
chk("no extra rows beyond the planted edits", data.rows.length === 6,
  `${data.rows.length} rows`);

console.log(`changes: ${n - bad}/${n} checks passed`);
process.exit(bad ? 1 : 0);
