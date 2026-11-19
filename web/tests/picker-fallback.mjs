/* Picker fallback regression: when the File System Access API exists but the
 * platform blocks showOpenFilePicker (embedded or automated browsers refuse
 * with "The request is not allowed by the user agent or the platform in the
 * current context"), both pickers must fall back to the plain file input
 * instead of dead-ending on an error toast. A user cancel (AbortError) must
 * stay silent with no fallback. The real picker functions are extracted from
 * the shipped iso.js / app.js and run in a vm sandbox with a stubbed DOM, so
 * this exercises the shipped decision logic, not a copy of it. */
import fs from "fs";
import path from "path";
import vm from "vm";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.join(here, "..");
const read = (f) => fs.readFileSync(path.join(web, f), "utf8");

let n = 0, fail = 0;
const ok = (name, cond, extra) => {
  n++; if (!cond) { fail++; console.error(`FAIL ${name}${extra ? "  " + extra : ""}`); }
  else console.log(`PASS ${name}`);
};

/* Extract one top-level function declaration (optionally async) by name,
 * scanning for its balanced closing brace. Returns null when absent. */
function extractFn(src, name) {
  const m = src.match(new RegExp("(?:async\\s+)?function\\s+" + name + "\\s*\\("));
  if (!m) return null;
  let depth = 0;
  for (let j = src.indexOf("{", m.index); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(m.index, j + 1); }
  }
  return null;
}

const blocked = Object.assign(
  new Error("The request is not allowed by the user agent or the platform in the current context."),
  { name: "NotAllowedError" });
const cancelled = Object.assign(new Error("The user aborted a request."), { name: "AbortError" });

/* ---------------- iso.js pickISO ---------------- */
const isoSrc = read("iso.js");
const isoCode = ["pickISO", "pickISOviaInput"].map((f) => extractFn(isoSrc, f)).filter(Boolean).join("\n");
ok("iso.js pickISO is extractable", isoCode.includes("function pickISO"));
ok("iso.js factors the plain-input path into pickISOviaInput", isoCode.includes("function pickISOviaInput"));

function isoSandbox(picker) {
  const calls = { inputs: [], opened: [], toasts: [] };
  const sandbox = {
    HAS_FS_ACCESS: true,
    console,
    window: { showOpenFilePicker: picker },
    document: {
      createElement() {
        const el = { type: "", accept: "", files: [], onchange: null, clicked: false,
                     click() { this.clicked = true; } };
        calls.inputs.push(el);
        return el;
      },
    },
    openISO: async (src) => { calls.opened.push(src); },
    toast: (msg, kind) => { calls.toasts.push({ msg, kind }); },
  };
  vm.createContext(sandbox);
  vm.runInContext(isoCode, sandbox);
  return { sandbox, calls };
}

{ // (a) blocked picker: falls back to a clicked plain file input, no error toast
  const { sandbox, calls } = isoSandbox(async () => { throw blocked; });
  await sandbox.pickISO();
  ok("pickISO falls back to the plain input when the picker is blocked",
     calls.inputs.length === 1 && calls.inputs[0].clicked === true,
     `inputs=${calls.inputs.length}`);
  ok("pickISO fallback shows no error toast", calls.toasts.length === 0,
     JSON.stringify(calls.toasts));
  ok("pickISO fallback does not open anything yet", calls.opened.length === 0);
  // the fallback input is wired: choosing a file opens it (read-only path)
  const fakeFile = { name: "game.iso" };
  if (calls.inputs[0]) { calls.inputs[0].files = [fakeFile]; calls.inputs[0].onchange(); }
  ok("pickISO fallback input opens the chosen file", calls.opened[0] === fakeFile);
}
{ // (b) user cancel: silent, no fallback, no toast
  const { sandbox, calls } = isoSandbox(async () => { throw cancelled; });
  await sandbox.pickISO();
  ok("pickISO does NOT fall back on AbortError", calls.inputs.length === 0);
  ok("pickISO stays silent on AbortError", calls.toasts.length === 0 && calls.opened.length === 0);
}
{ // success path unchanged: the handle goes straight to openISO (save-in-place)
  const handle = { name: "handle" };
  const { sandbox, calls } = isoSandbox(async () => [handle]);
  await sandbox.pickISO();
  ok("pickISO success opens via the picker handle", calls.opened[0] === handle && calls.inputs.length === 0);
}

/* ---------------- app.js pickViaFS ---------------- */
const appSrc = read("app.js");
const appCode = extractFn(appSrc, "pickViaFS");
ok("app.js pickViaFS is extractable", !!appCode);

function appSandbox(picker) {
  const calls = { fileClicks: 0, opened: [], toasts: [] };
  const fileEl = { click() { calls.fileClicks++; } };
  const sandbox = {
    console,
    window: { showOpenFilePicker: picker },
    $: (id) => (id === "file" ? fileEl : {}),
    openFile: (f, h) => { calls.opened.push([f, h]); },
    toast: (msg, kind) => { calls.toasts.push({ msg, kind }); },
  };
  vm.createContext(sandbox);
  vm.runInContext(appCode, sandbox);
  return { sandbox, calls };
}

{ // (c) blocked picker: falls back to clicking the plain input, no error toast
  const { sandbox, calls } = appSandbox(async () => { throw blocked; });
  await sandbox.pickViaFS();
  ok("pickViaFS falls back to the plain input when the picker is blocked",
     calls.fileClicks === 1, `clicks=${calls.fileClicks}`);
  ok("pickViaFS fallback shows no error toast", calls.toasts.length === 0,
     JSON.stringify(calls.toasts));
  ok("pickViaFS fallback does not open anything yet", calls.opened.length === 0);
}
{ // (d) user cancel: silent, no fallback, no toast
  const { sandbox, calls } = appSandbox(async () => { throw cancelled; });
  await sandbox.pickViaFS();
  ok("pickViaFS does NOT fall back on AbortError", calls.fileClicks === 0);
  ok("pickViaFS stays silent on AbortError", calls.toasts.length === 0 && calls.opened.length === 0);
}
{ // success path unchanged: handle kept for save-in-place, file opened with it
  const fakeFile = { name: "save.psu" };
  const handle = { getFile: async () => fakeFile };
  const { sandbox, calls } = appSandbox(async () => [handle]);
  await sandbox.pickViaFS();
  ok("pickViaFS success opens the file with its handle",
     calls.opened.length === 1 && calls.opened[0][0] === fakeFile && calls.opened[0][1] === handle);
  ok("pickViaFS success keeps the writable handle", sandbox.saveHandle === handle);
  ok("pickViaFS success does not touch the plain input", calls.fileClicks === 0);
}

console.log(`\n${n - fail}/${n} passed`);
process.exit(fail ? 1 : 0);
