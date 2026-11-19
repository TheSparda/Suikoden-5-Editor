/* Picker fallback regression: when the File System Access API exists but the
 * platform blocks showOpenFilePicker (embedded or automated browsers refuse
 * with "The request is not allowed by the user agent or the platform in the
 * current context"), both pickers must fall back to the plain file input
 * instead of dead-ending on an error toast. A user cancel (AbortError) must
 * stay silent with no fallback. The real picker functions are extracted from
 * the shipped iso.js / app.js and run in a vm sandbox with a stubbed DOM, so
 * this exercises the shipped decision logic, not a copy of it.
 *
 * The fallback inputs must also be attached to the document before they are
 * clicked: a detached input cannot be located by environments that find
 * inputs in the page (browser automation, some embedded webviews, assistive
 * tooling), which dead-ends the open just as surely as a blocked picker.
 * Each input removes itself once its change event fires, and a new pick
 * removes the previous input so cancelled picks do not pile up.
 *
 * The fallback inputs carry stable ids, and the ISO Editor also has a
 * permanent labelled drop zone (id isoDrop, wired by wireIsoDrop in iso.js)
 * mirroring the Save Editor's: a dropped file opens directly, and a click
 * anywhere on the zone runs the same pick flow as the Choose ISO button. */
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

/* ---------------- iso.js pickISO + importRecipe ---------------- */
const isoSrc = read("iso.js");
const isoState = isoSrc.match(/let pendingPickerInput = null;/);
const isoCode = [isoState ? isoState[0] : "",
  ...["pickISO", "pickISOviaInput", "attachPickerInput", "dropPickerInput", "importRecipe", "wireIsoDrop"]
  .map((f) => extractFn(isoSrc, f)).filter(Boolean)].join("\n");
ok("iso.js declares the pending picker input state", !!isoState);
ok("iso.js pickISO is extractable", isoCode.includes("function pickISO"));
ok("iso.js factors the plain-input path into pickISOviaInput", isoCode.includes("function pickISOviaInput"));
ok("iso.js attaches picker inputs via attachPickerInput", isoCode.includes("function attachPickerInput"));
ok("iso.js importRecipe is extractable", isoCode.includes("function importRecipe"));
ok("iso.js wires the ISO drop zone via wireIsoDrop", isoCode.includes("function wireIsoDrop"));

/* ---------------- index.html: the permanent ISO drop zone ---------------- */
const htmlSrc = read("index.html");
ok("index.html has the ISO drop zone element", htmlSrc.includes('id="isoDrop"'));
ok("ISO drop zone carries visible drop wording",
   htmlSrc.includes("Drop a Suikoden V ISO here"));

function fakeZone() {
  const listeners = {};
  const classes = new Set();
  return {
    listeners,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
    dispatch(ev, e) { (listeners[ev] || []).slice().forEach((fn) => fn(e)); },
  };
}

function isoSandbox(picker) {
  const calls = { inputs: [], opened: [], toasts: [], body: null, zone: fakeZone() };
  const body = {
    children: [],
    appendChild(el) { el.parentNode = body; body.children.push(el); return el; },
    removeChild(el) {
      const i = body.children.indexOf(el);
      if (i >= 0) body.children.splice(i, 1);
      el.parentNode = null;
      return el;
    },
  };
  calls.body = body;
  const sandbox = {
    HAS_FS_ACCESS: true,
    console,
    $: (id) => (id === "isoDrop" ? calls.zone : null),
    window: {
      showOpenFilePicker: picker,
      PYISO: { importmod: () => JSON.stringify({ patchCount: 2, appliedBytes: 5, mismatchedRuns: 0 }) },
    },
    document: {
      body,
      createElement() {
        const el = { type: "", accept: "", files: [], onchange: null, clicked: false,
                     style: {}, parentNode: null, attachedAtClick: false,
                     click() { this.clicked = true; this.attachedAtClick = !!this.parentNode; },
                     remove() { if (this.parentNode) this.parentNode.removeChild(this); } };
        calls.inputs.push(el);
        return el;
      },
    },
    openISO: async (src) => { calls.opened.push(src); },
    importXdelta: async () => {},
    isoEdits: {},
    curView: null,
    selectView: () => {},
    updateIsoToolbar: () => {},
    captureUndoStep: () => {},
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
  // the fallback input must be in the document when clicked, and invisible
  ok("pickISO fallback input is attached to the document before the click",
     calls.inputs.length === 1 && calls.inputs[0].attachedAtClick === true);
  ok("pickISO fallback input is invisible", calls.inputs.length === 1 && calls.inputs[0].style.display === "none");
  ok("pickISO fallback input carries the isoFileInput id",
     calls.inputs.length === 1 && calls.inputs[0].id === "isoFileInput",
     `id=${calls.inputs[0] && calls.inputs[0].id}`);
  ok("pickISO fallback input sits in the document body until a file is chosen",
     calls.inputs.length === 1 && calls.body.children.includes(calls.inputs[0]));
  // the fallback input is wired: choosing a file opens it (read-only path)
  const fakeFile = { name: "game.iso" };
  if (calls.inputs[0]) { calls.inputs[0].files = [fakeFile]; calls.inputs[0].onchange(); }
  ok("pickISO fallback input opens the chosen file", calls.opened[0] === fakeFile);
  ok("pickISO fallback input is gone from the document after the change",
     calls.inputs.length === 1 && !calls.body.children.includes(calls.inputs[0]));
}
{ // a second pick replaces the first input instead of piling up in the page
  const { sandbox, calls } = isoSandbox(async () => { throw blocked; });
  await sandbox.pickISO();
  await sandbox.pickISO();
  ok("a later pick removes the previous fallback input",
     calls.inputs.length === 2 && calls.inputs[0].parentNode === null);
  ok("only the newest fallback input stays in the document",
     calls.inputs.length === 2 && calls.body.children.length === 1 && calls.body.children[0] === calls.inputs[1]);
}
{ // importRecipe: attached before the click, removed after the change
  const { sandbox, calls } = isoSandbox(async () => { throw blocked; });
  sandbox.importRecipe();
  ok("importRecipe creates one input", calls.inputs.length === 1);
  const inp = calls.inputs[0];
  ok("importRecipe input is attached to the document before the click",
     !!inp && inp.attachedAtClick === true && inp.clicked === true);
  ok("importRecipe input is invisible", !!inp && inp.style.display === "none");
  ok("importRecipe input carries the isoImportInput id",
     !!inp && inp.id === "isoImportInput", `id=${inp && inp.id}`);
  const recipeFile = {
    name: "patch.s5mod",
    slice: () => ({ arrayBuffer: async () => new Uint8Array([0x7b, 0x22, 0x7d]).buffer }),
    text: async () => JSON.stringify({ patches: [] }),
  };
  if (inp) { inp.files = [recipeFile]; await inp.onchange(); }
  ok("importRecipe input is gone from the document after the change",
     !!inp && !calls.body.children.includes(inp));
  ok("importRecipe still applies the chosen recipe",
     calls.toasts.some((t) => t.msg.startsWith("Applied recipe")) && !!sandbox.isoEdits.imported,
     JSON.stringify(calls.toasts));
}
{ // importRecipe with no file chosen still removes its input
  const { sandbox, calls } = isoSandbox(async () => { throw blocked; });
  sandbox.importRecipe();
  const inp = calls.inputs[0];
  if (inp) { inp.files = []; await inp.onchange(); }
  ok("importRecipe input is gone from the document after an empty change",
     !!inp && !calls.body.children.includes(inp));
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

{ // the ISO drop zone: highlight on dragover, drop opens the file, click picks
  const { sandbox, calls } = isoSandbox(async () => { throw blocked; });
  ok("wireIsoDrop is callable in the sandbox", typeof sandbox.wireIsoDrop === "function");
  if (typeof sandbox.wireIsoDrop === "function") sandbox.wireIsoDrop();
  const zone = calls.zone;
  ok("drop zone listens for dragover, drop and click",
     ["dragover", "drop", "click"].every((ev) => (zone.listeners[ev] || []).length > 0),
     JSON.stringify(Object.keys(zone.listeners)));
  let prevented = 0;
  zone.dispatch("dragover", { preventDefault: () => { prevented++; } });
  ok("drop zone dragover prevents the default and highlights the zone",
     prevented === 1 && zone.classList.contains("hot"));
  const fakeFile = { name: "game.iso" };
  zone.dispatch("drop", { preventDefault: () => {}, dataTransfer: { files: [fakeFile] } });
  ok("drop zone drop opens the dropped file", calls.opened[0] === fakeFile);
  ok("drop zone drop clears the highlight", !zone.classList.contains("hot"));
  zone.dispatch("drop", { preventDefault: () => {}, dataTransfer: { files: [] } });
  ok("drop zone drop with no file opens nothing", calls.opened.length === 1);
  // a click on the zone body runs the pick flow (blocked picker -> fallback input)
  zone.dispatch("click", { target: { closest: () => null } });
  await new Promise((r) => setTimeout(r, 0));
  ok("drop zone click runs the pick flow",
     calls.inputs.length === 1 && calls.inputs[0].clicked === true,
     `inputs=${calls.inputs.length}`);
  // a click on the Choose ISO button inside the zone must not start a second pick
  const btn = { closest: (sel) => (sel === "button" ? btn : null) };
  zone.dispatch("click", { target: btn });
  await new Promise((r) => setTimeout(r, 0));
  ok("drop zone click on its button does not double-pick", calls.inputs.length === 1,
     `inputs=${calls.inputs.length}`);
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
