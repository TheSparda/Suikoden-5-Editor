#!/usr/bin/env node
// Tiny bridge between the MCP server (Python) and the editor's own
// web/vcdiff.js, the exact module the web editor's .xdelta export runs.
// The server holds no VCDIFF logic; this file only translates one JSON
// request on stdin into a buildXdelta call and writes the patch file.
//
// Request:  {sourceSize, edits: [{off, dataB64}], outPath}
// Response: one JSON line, {ok: true, patchBytes} or {ok: false, error}.
import fs from "fs";
import { createRequire } from "module";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  try {
    const req = JSON.parse(input);
    const { buildXdelta } = require(path.join(HERE, "..", "web", "vcdiff.js"));
    if (typeof buildXdelta !== "function")
      throw new Error("web/vcdiff.js did not export buildXdelta");
    const edits = (req.edits || []).map((e) => ({
      off: e.off,
      data: new Uint8Array(Buffer.from(e.dataB64, "base64")),
    }));
    const patch = buildXdelta(req.sourceSize, edits);
    fs.writeFileSync(req.outPath, patch);
    process.stdout.write(JSON.stringify({ ok: true, patchBytes: patch.length }) + "\n");
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.message || e) }) + "\n");
  }
});
