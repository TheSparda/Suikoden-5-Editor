/* Recruitment guide reference (Editor/s5_recruit_guide.json, built by
 * Editor/build_recruit_refs.py): coverage and content checks. The data must
 * cover exactly the recruitable ids 0..108, every entry must say where and
 * how, the characters the GameFAQs FAQ flags as missable must carry a
 * cutoff note, and Bastan's note must state his actual cutoff relative to
 * the Sol-Falena battle. */
import { readFileSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const guide = JSON.parse(readFileSync(path.join(here, "../../Editor/s5_recruit_guide.json"), "utf8"));
const chars = JSON.parse(readFileSync(path.join(here, "../../Editor/s5_characters.json"), "utf8"));

let n = 0, bad = 0;
function chk(name, cond, extra = "") {
  n++;
  console.log((cond ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : ""));
  if (!cond) bad++;
}

const keys = Object.keys(guide).map(Number).sort((a, b) => a - b);
chk("covers exactly the recruitable ids 0..108",
  keys.length === 109 && keys.every((k, i) => k === i), `${keys.length} entries`);
chk("every entry has a non-empty where and how",
  keys.every((k) => guide[k].where.trim() && guide[k].how.trim()));
chk("every key matches the editor character table",
  keys.every((k) => chars[k] && chars[k].id === k));

// The FAQ's hard missables (a cutoff, not just a soft warning).
const HARD_MISSABLE = [10, 14, 15, 19, 33, 34, 41, 42, 46, 56, 59, 65, 70, 83, 86, 95, 102, 108];
chk("FAQ-flagged missable characters all carry a cutoff note",
  HARD_MISSABLE.every((k) => (guide[k].missable || "").trim()),
  "missing: " + HARD_MISSABLE.filter((k) => !(guide[k].missable || "").trim()).join(","));
chk("automatic story recruits carry no invented cutoff",
  [0, 8, 24, 47, 106].every((k) => !(guide[k].missable || "").trim()));

const b = guide[46];
chk("Bastan: found in Haud Village with a ? Painting",
  /Haud/.test(b.where) && /Painting/.test(b.how));
chk("Bastan: cutoff stated relative to the Sol-Falena battle",
  /Sol-Falena/.test(b.missable) && /too late/.test(b.missable) && /Barows/.test(b.missable),
  b.missable.slice(0, 80) + "...");
chk("Eresh and Euram entries name the mutual exclusivity",
  /Euram/.test(guide[10].missable) && /Eresh/.test(guide[108].missable));
chk("no em dashes anywhere in the guide text",
  keys.every((k) => !/—|–/.test(guide[k].where + guide[k].how + guide[k].missable)));

console.log(`recruit-guide: ${n - bad}/${n} checks passed`);
process.exit(bad ? 1 : 0);
