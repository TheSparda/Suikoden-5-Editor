/* Suikoden V web editor — PURE byte-diff logic (no DOM, no Pyodide).
 *
 * Used by the ISO editor to turn "original slice vs edited slice" into the minimal
 * set of changed byte-runs, so only those runs are written back into the real 4 GB
 * disc (via a File System Access ranged write) — never the whole file. Kept DOM-free
 * so it is unit-testable in Node (tests/diff-logic.mjs). UMD footer: classic <script>
 * in the browser (sets self.DiffCore); require()-able as CommonJS from the tests. */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api; // Node tests
  root.DiffCore = api;                                                        // browser
})(typeof self !== "undefined" ? self : globalThis, function () {
  "use strict";

  /* Changed byte-runs between two equal-length byte arrays.
   * Returns [{off, len, bytes}] where bytes is a Uint8Array of the NEW bytes.
   * Adjacent changed bytes are coalesced into one run; runs separated by fewer
   * than `gap` unchanged bytes are merged too (fewer, larger writes = fewer
   * FileSystemWritableFileStream seeks, which dominate wall-clock on big files). */
  function diffRuns(orig, cur, gap) {
    if (orig.length !== cur.length)
      throw new Error(`diffRuns: length mismatch ${orig.length} != ${cur.length}`);
    gap = gap == null ? 64 : gap;
    const runs = [];
    let start = -1, lastChange = -1;
    const n = cur.length;
    for (let i = 0; i < n; i++) {
      if (orig[i] !== cur[i]) {
        if (start < 0) start = i;
        else if (i - lastChange - 1 > gap) {           // gap too big → close the run
          runs.push(mkRun(cur, start, lastChange));
          start = i;
        }
        lastChange = i;
      }
    }
    if (start >= 0) runs.push(mkRun(cur, start, lastChange));
    return runs;
  }
  function mkRun(cur, start, end) {
    return { off: start, len: end - start + 1, bytes: cur.slice(start, end + 1) };
  }

  /* Total changed bytes across runs (for the "N bytes in M runs" status line). */
  function runStats(runs) {
    return { runs: runs.length, bytes: runs.reduce((s, r) => s + r.len, 0) };
  }

  /* Hex string for one run's new bytes (used when building a portable recipe in JS;
   * the Python engine also produces recipes — this is the fallback / parity check). */
  function toHex(u8) {
    let s = "";
    for (let i = 0; i < u8.length; i++) s += (u8[i] < 16 ? "0" : "") + u8[i].toString(16);
    return s;
  }

  return { diffRuns, runStats, toHex };
});
