/* Unit tests for the pure byte-diff module (no DOM, no Pyodide). */
import { createRequire } from "module";
import { fileURLToPath } from "url";
import path from "path";
const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const { diffRuns, runStats, toHex } = require(path.join(here, "..", "diff-core.js"));

let n = 0, fail = 0;
const eq = (name, got, want) => {
  n++; const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fail++; console.error(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`); }
  else console.log(`PASS ${name}`);
};

const A = (arr) => Uint8Array.from(arr);

// no change → no runs
eq("identical → 0 runs", diffRuns(A([1,2,3]), A([1,2,3])).length, 0);

// single byte change
{
  const r = diffRuns(A([1,2,3,4]), A([1,9,3,4]));
  eq("one change: count", r.length, 1);
  eq("one change: off/len", [r[0].off, r[0].len], [1, 1]);
  eq("one change: bytes", [...r[0].bytes], [9]);
}

// two changes within the gap coalesce (default gap 64)
{
  const a = A(new Array(10).fill(0)), b = a.slice(); b[1] = 5; b[4] = 7;
  const r = diffRuns(a, b);
  eq("near changes coalesce", r.length, 1);
  eq("coalesced run span", [r[0].off, r[0].len], [1, 4]);
}

// two changes beyond the gap stay separate (gap 0)
{
  const a = A(new Array(10).fill(0)), b = a.slice(); b[1] = 5; b[4] = 7;
  const r = diffRuns(a, b, 0);
  eq("gap 0 keeps separate", r.length, 2);
}

// runStats totals
{
  const a = A(new Array(20).fill(0)), b = a.slice(); b[0]=1; b[1]=2; b[10]=3;
  const s = runStats(diffRuns(a, b, 0));
  eq("runStats", [s.runs, s.bytes], [2, 3]);
}

// length mismatch throws
{
  let threw = false;
  try { diffRuns(A([1]), A([1,2])); } catch (_) { threw = true; }
  eq("length mismatch throws", threw, true);
}

// toHex
eq("toHex", toHex(A([0, 15, 16, 255])), "000f10ff");

console.log(`\n${n - fail}/${n} passed`);
process.exit(fail ? 1 : 0);
