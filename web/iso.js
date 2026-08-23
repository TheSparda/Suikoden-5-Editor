/* Suikoden V — ISO / disc editor (desktop Chromium only; needs File System Access).
 *
 * Does NOT reimplement the field tables: it reuses the desktop engine (s5patch.py +
 * s5fields.py) UNCHANGED inside Pyodide (glue + handles set up by app.js), operating on
 * a ~6.6 MB TRUNCATED FRONT-SLICE of the ISO written to Pyodide's /iso.bin. Every
 * editable table (serial, stats, gear, spells, runes, prices, enemies, unites, MP,
 * skill effects, names) lives below that offset, so absolute-seek reads/writes work on
 * the slice exactly as on the full disc. On Save we diff the edited slice against the
 * pristine one and write ONLY the changed byte-runs back into the real 4 GB file in
 * place (keepExistingData) — never the whole disc. A .s5mod recipe lets edits be shared
 * without the disc. Version/region is gated before opening.
 *
 * Editing model: write-through — a field change writes into /iso.bin immediately (the
 * verified Python writer does the byte encoding), and a labelled `isoEdits` map drives
 * the dirty highlight, the unsaved badge and the review-before-save list. */

"use strict";

const ISO_END = 0x6A0000;          // slice length: covers serial @0x828BD + all tables
const ISO_PATH = "/iso.bin";

let isoHandle = null;              // FileSystemFileHandle (read+write)
let ORIG = null;                  // pristine slice (Uint8Array) for diffing
let isoRegion = null;
let isoMAPS = null;
let SPELL_LIST = [];              // [{id,name}] for spellid pickers
let isoEdits = {};               // key -> { label, group, to }
let isoInited = false;
let isoBadgeRAF = 0;
/* undo/redo (B18): each step = the byte-runs an action changed, plus a snapshot of the
 * labelled edit map, so both the disc bytes and the review list restore together. */
let ISO_PREV = null;             // last-committed slice snapshot (baseline for the next step)
let ISO_PREV_EDITS = {};
const undoStack = [], redoStack = [];
const UNDO_MAX = 300;

/* Views (mirror the desktop ISO tabs, minus the disc-wide graphics/text extras). */
const ISO_VIEWS = [
  { id: "char",     label: "Characters" },
  { id: "gear",     label: "Gear" },
  { id: "spell",    label: "Spells" },
  { id: "rune",     label: "Runes" },
  { id: "price",    label: "Prices" },
  { id: "runeprice",label: "Rune prices" },
  { id: "healprice",label: "Heal prices" },
  { id: "enemy",    label: "Enemies" },
  { id: "unite",    label: "Unites" },
  { id: "mp",       label: "MP growth" },
  { id: "skillfx",  label: "Skill effects" },
  { id: "balance",  label: "Balance" },
  { id: "name",     label: "Names" },
  { id: "ref",      label: "Reference" },
];

/* Called by app.js once Pyodide + engines are ready. */
window.isoReady = function () {
  $("isoOpen").classList.remove("hidden");
  $("isoPickBtn").onclick = pickISO;
  $("isoImportBtn").onclick = importRecipe;
  $("isoExportBtn").onclick = exportRecipe;
  $("isoSaveBtn").onclick = saveISO;
  $("isoUndoBtn").onclick = isoUndo;
  $("isoRedoBtn").onclick = isoRedo;
  document.addEventListener("keydown", (e) => {
    if (!ORIG || document.querySelector('.mode-pane[data-mode="iso"]').hidden) return;   // ISO mode only
    const mod = e.ctrlKey || e.metaKey; if (!mod) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); isoUndo(); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); isoRedo(); }
  });
  // v2/A3: usable everywhere. Without File System Access we can't write the 4 GB file
  // in place, but you can still open + edit + export a .s5mod recipe to apply on desktop.
  if (!HAS_FS_ACCESS) {
    const note = $("isoNoFsNote"); if (note) note.classList.remove("hidden");
    const sv = $("isoSaveBtn"); if (sv) sv.textContent = "Export recipe";
  }
  restoreLastISO();
};
/* Lazily nothing extra on tab-show (kept for parity with common.js hook). */
window.onIsoModeShown = function () {};
window.isoHasUnsaved = () => !!ORIG && recomputeIsoDirty().runs > 0;

/* ---------------- open an ISO ---------------- */
async function pickISO() {
  if (HAS_FS_ACCESS) {
    try {
      const [h] = await window.showOpenFilePicker({
        types: [{ description: "PS2 ISO", accept: { "application/octet-stream": [".iso",".bin",".img"] } }] });
      await openISO(h);
    } catch (e) { if (e && e.name !== "AbortError") toast("Could not open ISO: " + e.message, "bad"); }
    return;
  }
  // fallback: plain file input (works on mobile / Firefox / Safari). No writable handle,
  // so save-in-place is unavailable — but editing + recipe export work.
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = ".iso,.bin,.img,application/octet-stream";
  inp.onchange = () => { if (inp.files[0]) openISO(inp.files[0]); };
  inp.click();
}
/* Accepts a FileSystemFileHandle (desktop, writable) or a plain File (read-only reach). */
async function openISO(src) {
  spin(true);
  try {
    const isHandle = src && typeof src.getFile === "function";
    const file = isHandle ? await src.getFile() : src;
    if (file.size < ISO_END) { toast("That file is too small to be a Suikoden V disc.", "bad"); return; }
    const slice = new Uint8Array(await file.slice(0, ISO_END).arrayBuffer());
    pyodide.FS.writeFile(ISO_PATH, slice);
    const load = JSON.parse(window.PYISO.load());
    if (load.error) { toast(load.error, "bad"); return; }
    isoHandle = isHandle ? src : null; ORIG = slice.slice(); ISO_PREV = slice.slice();
    isoRegion = load.region; isoEdits = {}; ISO_PREV_EDITS = {}; undoStack.length = 0; redoStack.length = 0;
    $("isoFilename").textContent = `${file.name} · ${(file.size/1073741824).toFixed(2)} GB · ${load.regionName}${isHandle ? "" : " · read-only (recipe export only)"}`;
    isoMAPS = JSON.parse(window.PYISO.maps());
    try { SPELL_LIST = (JSON.parse(window.PYISO.spellnames()).spells || [])
      .map((n, i) => ({ id: i, name: n || ("Spell " + i) })); } catch (_) { SPELL_LIST = []; }
    $("isoOpen").classList.add("hidden");
    $("isoViews").classList.remove("hidden");
    $("isoToolbar").classList.remove("hidden");
    renderTabs();
    selectView("char");
    if (isHandle) await idbSet("iso:last", { name: file.name, handle: src, when: Date.now() });
    updateIsoToolbar(); updateUndoButtons();
    toast(`Opened ${file.name} (${load.regionName})`, "ok");
  } catch (e) { toast("Could not read the ISO: " + (e.message || e), "bad"); console.error(e); }
  finally { spin(false); }
}
async function restoreLastISO() {
  const s = await idbGet("iso:last"); if (!s || !s.handle) return;
  const row = $("isoLast"); row.classList.remove("hidden");
  row.innerHTML = `<button class="ghost mini" id="isoLastBtn">↻ Last opened: ${esc(s.name)}</button>
                   <button class="ghost mini" id="isoLastX" title="Forget">✕</button>`;
  $("isoLastBtn").onclick = async () => {
    try {
      let p = await s.handle.queryPermission({ mode: "readwrite" });
      if (p !== "granted") p = await s.handle.requestPermission({ mode: "readwrite" });
      if (p === "granted") return openISO(s.handle);
      toast("Permission needed to reopen that ISO.", "bad");
    } catch (e) { toast("Could not reopen: " + e.message, "bad"); }
  };
  $("isoLastX").onclick = async () => { await idbDel("iso:last"); row.classList.add("hidden"); };
}

/* ---------------- tab strip ---------------- */
function renderTabs() {
  $("isoTabs").innerHTML = ISO_VIEWS.map(v =>
    `<button class="tab" data-view="${v.id}">${esc(v.label)}</button>`).join("");
  qa("#isoTabs .tab").forEach(t => t.onclick = () => selectView(t.dataset.view));
}
let curView = null;
async function selectView(id) {
  curView = id;
  qa("#isoTabs .tab").forEach(t => t.classList.toggle("on", t.dataset.view === id));
  const body = $("isoBody"); body.innerHTML = `<div class="note" style="padding:14px">Loading…</div>`;
  try { await VIEW_RENDER[id](body); }
  catch (e) { body.innerHTML = `<p class="bad" style="padding:14px">${esc(e.message || e)}</p>`; console.error(e); }
}

/* ---------------- generic field rendering ---------------- */
function arrList(a) { return (a || []).map((n, i) => ({ id: i, name: n })); }
function mapList(o) { return Object.keys(o || {}).map(k => ({ id: k, name: o[k] })); }
function kindList(kind) {
  const M = isoMAPS || {};
  switch (kind) {
    case "rank": return arrList(M.ranks);
    case "grade": return arrList(M.grades);
    case "egrade": return arrList(M.egrades);
    case "element": return mapList(M.elements);
    case "target": return mapList(M.targets);
    case "spellstatus": return mapList(M.spellstatus);
    case "drop": return mapList(M.dropitems);
    case "item": return mapList(M.items);
    case "helditem": return mapList(M.held);
    case "rune": return mapList(M.runes);
    case "armorhead": return mapList((M.armor || {}).head);
    case "armorbody": return mapList((M.armor || {}).body);
    case "armorarm": return mapList((M.armor || {}).glove);
    case "armorfoot": return mapList((M.armor || {}).foot);
    case "spellid": return SPELL_LIST;
    default: return null;
  }
}
const SMALL_KINDS = new Set(["rank","grade","egrade","element","target","spellstatus"]);
const PICKER_KINDS = new Set(["item","helditem","rune","armorhead","armorbody","armorarm","armorfoot","drop","spellid"]);
function nameFor(kind, value) {
  const list = kindList(kind); if (!list) return String(value);
  const hit = list.find(x => String(x.id) === String(value));
  return hit ? hit.name : ("#" + value);
}

/* Render one field. `attrs` carries the dispatch context (view + ident + table/field). */
function renderField(f, attrs) {
  const kind = f.kind || "num";
  const key = attrs.key;
  const dataset = `data-key="${esc(key)}" data-view="${esc(attrs.view)}" data-ident='${esc(attrs.ident)}'`
    + ` data-field="${esc(f.field != null ? f.field : f.label)}" data-table="${esc(attrs.table || "")}"`
    + ` data-kind="${esc(kind)}" data-orig="${esc(f.value)}" data-lbl="${esc((attrs.prefix||"") + f.label)}"`
    + ` data-grp="${esc(attrs.group || "")}"`;
  let inner;
  if (SMALL_KINDS.has(kind)) {
    const list = kindList(kind) || [];
    let o = "";
    if (!list.some(x => String(x.id) === String(f.value))) o += `<option value="${f.value}" selected>#${f.value}</option>`;
    for (const it of list) o += `<option value="${it.id}" ${String(it.id)===String(f.value)?"selected":""}>${esc(it.name)}</option>`;
    inner = `<select ${dataset} onchange="onIsoField(this)">${o}</select>`;
  } else if (PICKER_KINDS.has(kind)) {
    const nm = nameFor(kind, f.value);
    inner = `<button type="button" class="pickbtn" ${dataset} onclick="onIsoPick(this)">
      <span class="pickbtn-name">${esc(nm)}</span><span class="pickbtn-id note">#${esc(f.value)}</span></button>`;
  } else {
    const max = f.width === 1 ? 255 : f.width === 2 ? 65535 : f.width === 3 ? 16777215 : 4294967295;
    const min = f.signed ? -(max>>1)-1 : 0, hi = f.signed ? (max>>1) : max;
    inner = `<input type="number" ${dataset} value="${f.value}" min="${min}" max="${hi}" onchange="onIsoField(this)">`;
  }
  return `<div class="fld"><label>${esc(f.label)}
    <span class="pill">${kind==="num"?(f.width+"B"):kind}</span></label><div class="in">${inner}${REVERT_BTN}</div></div>`;
}

/* dispatch a field write to the right Pyodide setter (write-through). */
/* single write path shared by field-change, picker-pick and per-field revert */
function commitIso(el, value) {
  return applyIsoWrite(el, value).then((ok) => {
    if (ok === false) return;
    trackIso(el, value);
    afterIsoWrite(el.dataset.view, el.dataset.ident);
    captureUndoStep();
  });
}
function onIsoField(el) { commitIso(el, el.value); }
function setPickDisplay(el, id) {
  q(".pickbtn-name", el).textContent = nameFor(el.dataset.kind, id);
  q(".pickbtn-id", el).textContent = "#" + id;
  el.dataset.origLive = id;
}
function onIsoPick(el) {
  const kind = el.dataset.kind;
  const cur = el.dataset.origLive != null ? el.dataset.origLive : q(".pickbtn-id", el).textContent.replace("#","");
  openPicker("Choose " + el.dataset.lbl, kindList(kind) || [], cur, (id) => {
    setPickDisplay(el, id); commitIso(el, id);
  }, { hideId: kind !== "spellid" && kind !== "drop" });
}
/* Per-field undo: revert a control to the value it had when this view was rendered.
 * The ↺ button sits right after its control, so previousElementSibling is the field. */
function revertIsoField(btn) {
  const el = btn.previousElementSibling;
  if (!el || el.dataset.orig == null) return;
  const orig = el.dataset.orig;
  if (el.classList.contains("pickbtn")) setPickDisplay(el, orig);
  else el.value = orig;
  commitIso(el, orig);
}
/* Changing a rune's spell set (start/count) changes which spells it teaches → re-render
 * so the per-spell editors below refresh to the new set. */
function afterIsoWrite(view, ident) {
  if (view === "rune") { try { renderRune(JSON.parse(ident).id); } catch (_) {} }
}
/* the ↺ button markup placed after a control inside its .in / table cell */
const REVERT_BTN = `<button type="button" class="revert" title="Undo this change" onclick="revertIsoField(this)" tabindex="-1">↺</button>`;
async function applyIsoWrite(el, value) {
  const view = el.dataset.view, field = el.dataset.field;
  const ident = JSON.parse(el.dataset.ident || "{}");
  let res;
  spin(true);
  try {
    if (view === "char") res = window.PYISO.setchar(JSON.stringify({ id: ident.id }), JSON.stringify([{ table: el.dataset.table, field, value: +value }]));
    else if (view === "spell") res = window.PYISO.setspell(JSON.stringify({ id: ident.id }), JSON.stringify([{ field, value: +value }]));
    else if (view === "rune") res = window.PYISO.setrune(JSON.stringify({ id: ident.id }), JSON.stringify([{ field, value: +value }]));
    else if (view === "gear") res = window.PYISO.setgear(JSON.stringify({ slot: ident.slot, id: ident.id }), JSON.stringify([{ field, value: +value }]));
    else if (view === "enemy") res = window.PYISO.setenemy(JSON.stringify({ id: ident.id }), JSON.stringify([{ field, value: +value }]));
    else if (view === "price") res = window.PYISO.setprice(ident.index, field, +value);
    else if (view === "runeprice") res = window.PYISO.setruneprice(ident.index, field, +value);
    else if (view === "healprice") res = window.PYISO.sethealprice(ident.index, field, +value);
    else if (view === "mp") res = window.PYISO.setmp(ident.group, ident.idx, +value);
    else if (view === "skillfx") res = window.PYISO.setskillfx(ident.id, ident.rank, +value);
    else if (view === "unite") res = window.PYISO.setunite(ident.id, ident.slot, +value);
    else if (view === "name") res = window.PYISO.setname(ident.index, value);
    const r = JSON.parse(res);
    if (r.error) { toast("Write rejected: " + r.error, "bad"); return false; }
    return true;
  } catch (e) { toast("Write failed: " + (e.message || e), "bad"); console.error(e); return false; }
  finally { spin(false); }
}
function trackIso(el, value) {
  const key = el.dataset.key, orig = el.dataset.orig, kind = el.dataset.kind;
  const changed = String(value) !== String(orig);
  const disp = (SMALL_KINDS.has(kind) || PICKER_KINDS.has(kind)) ? nameFor(kind, value) : String(value);
  if (changed) { isoEdits[key] = { label: el.dataset.lbl, group: el.dataset.grp, to: disp }; markDirty(el, true); }
  else { delete isoEdits[key]; markDirty(el, false); }
  updateIsoToolbar();
}
function markDirty(el, on) {
  const host = el.classList.contains("pickbtn") ? el : el;
  host.classList.toggle("dirty", on);
}

/* ---------------- view renderers ---------------- */
const VIEW_RENDER = {};

VIEW_RENDER.char = async (body) => {
  const roster = JSON.parse(window.PYISO.chars()).chars || [];
  const withStats = roster.filter(c => c.hasStats), without = roster.filter(c => !c.hasStats);
  const opt = c => `<option value="${c.id}">${c.id}: ${esc(c.name)}</option>`;
  body.innerHTML = `<div class="row" style="padding:10px 14px 0">
      <span class="note">Character</span>
      <select id="isoCsel">${withStats.map(opt).join("")}
        ${without.length?`<optgroup label="No editable stats">${without.map(opt).join("")}</optgroup>`:""}</select>
      <span class="note">${esc((isoMAPS.globalHelp)||"")}</span></div>
    <div id="isoCbody"></div>`;
  $("isoCsel").onchange = () => renderCharTables(+$("isoCsel").value);
  renderCharTables(+$("isoCsel").value);
};
function renderCharTables(cid) {
  const r = JSON.parse(window.PYISO.char(cid));
  if (r.error) { $("isoCbody").innerHTML = `<p class="bad" style="padding:14px">${esc(r.error)}</p>`; return; }
  const name = (JSON.parse(window.PYISO.chars()).chars.find(c => c.id === cid) || {}).name || ("Char " + cid);
  let h = "";
  for (const [table, fields] of Object.entries(r.tables)) {
    const help = (isoMAPS.help || {})[table];
    h += `<div class="subhd">${esc(table)}</div>`;
    if (help) h += `<div class="note" style="margin:0 14px">${esc(help)}</div>`;
    if (table === "equipable skills")   // B18 presets: fill the whole cap array at once
      h += `<div class="row" style="padding:0 14px"><span class="note">Presets:</span>
        <button class="ghost mini" onclick="charSkillPreset(${cid},7)">Max all (SS)</button>
        <button class="ghost mini" onclick="charSkillPreset(${cid},6)">All S</button>
        <button class="ghost mini" onclick="charSkillPreset(${cid},0)">Clear</button></div>`;
    h += `<div class="grid" style="padding-top:8px">` + fields.map(f =>
      renderField(f, { view: "char", ident: JSON.stringify({ id: cid }), table,
        key: `char:${cid}:${table}:${f.label}`, prefix: `${name} · `, group: name })).join("") + `</div>`;
  }
  $("isoCbody").innerHTML = h;
}
/* Preset: set every equipable-skill cap for a character to one grade (staged, undoable). */
function charSkillPreset(cid, value) {
  const r = JSON.parse(window.PYISO.char(cid));
  const fields = (r.tables || {})["equipable skills"] || [];
  if (!fields.length) return;
  const edits = fields.map(f => ({ table: "equipable skills", field: f.label, value }));
  const res = JSON.parse(window.PYISO.setchar(JSON.stringify({ id: cid }), JSON.stringify(edits)));
  if (res.error) { toast(res.error, "bad"); return; }
  const name = (JSON.parse(window.PYISO.chars()).chars.find(c => c.id === cid) || {}).name || ("Char " + cid);
  const grade = (isoMAPS.ranks || [])[value] || String(value);
  isoEdits[`char:${cid}:equipable skills:preset`] =
    { label: `${name} · all equipable-skill caps → ${grade}`, group: name, to: grade };
  renderCharTables(cid);
  updateIsoToolbar(); captureUndoStep();
  toast(`Set ${edits.length} skill caps to ${grade}.`, "ok");
}

VIEW_RENDER.gear = async (body) => {
  const slots = ["head","body","arm","foot","accessory"];
  body.innerHTML = `<div class="row" style="padding:10px 14px 0">
      <span class="note">Slot</span>
      <select id="isoGslot">${slots.map(s=>`<option value="${s}">${s}</option>`).join("")}</select>
      <button class="ghost mini" id="isoGpick">Choose item…</button>
      <span id="isoGname" class="note"></span></div>
    <div class="note" style="margin:6px 14px">${esc((isoMAPS.help||{}).gear||"")}</div>
    <div id="isoGbody"></div>`;
  const loadSlot = async () => {
    const slot = $("isoGslot").value;
    const list = (JSON.parse(window.PYISO.gear(slot)).items || [])
      .map(it => ({ id: it.id, name: it.name, desc: it.effect, cat: "DEF " + it.def }));
    $("isoGpick").onclick = () => openPicker("Choose " + slot, list, gearCur[slot] || 0,
      (id) => { gearCur[slot] = +id; renderGearItem(slot, +id); }, {});
    renderGearItem(slot, gearCur[slot] || (list[0] ? +list[0].id : 0));
  };
  $("isoGslot").onchange = loadSlot;
  loadSlot();
};
const gearCur = {};
function renderGearItem(slot, id) {
  gearCur[slot] = id;
  const r = JSON.parse(window.PYISO.gearitem(slot, id));
  if (r.error) { $("isoGbody").innerHTML = `<p class="bad" style="padding:14px">${esc(r.error)}</p>`; return; }
  $("isoGname").textContent = `${r.name || ("#"+id)}  ${r.summaryEn ? "· " + r.summaryEn : ""}`;
  $("isoGbody").innerHTML = `<div class="grid" style="padding-top:8px">` + r.fields.map(f =>
    renderField(f, { view: "gear", ident: JSON.stringify({ slot, id }), key: `gear:${slot}:${id}:${f.label}`,
      prefix: `${r.name||("#"+id)} · `, group: `Gear · ${slot}` })).join("") + `</div>`;
}

VIEW_RENDER.spell = async (body) => {
  body.innerHTML = `<div class="row" style="padding:10px 14px 0"><span class="note">Spell</span>
      <button class="ghost mini" id="isoSpick">Choose spell…</button><span id="isoSname" class="note"></span></div>
    <div id="isoSbody"></div>`;
  $("isoSpick").onclick = () => openPicker("Choose spell", SPELL_LIST, spellCur,
    (id) => renderSpell(+id), {});
  renderSpell(spellCur);
};
let spellCur = 0;
function renderSpell(sid) {
  spellCur = sid;
  const r = JSON.parse(window.PYISO.spell(sid));
  if (r.error) { $("isoSbody").innerHTML = `<p class="bad" style="padding:14px">${esc(r.error)}</p>`; return; }
  const nm = (SPELL_LIST[sid] || {}).name || ("Spell " + sid);
  $("isoSname").textContent = `${sid}: ${nm}`;
  $("isoSbody").innerHTML = `<div class="grid" style="padding-top:8px">` + r.fields.map(f =>
    renderField(f, { view: "spell", ident: JSON.stringify({ id: sid }), key: `spell:${sid}:${f.label}`,
      prefix: `${nm} · `, group: "Spells" })).join("") + `</div>`;
}

VIEW_RENDER.rune = async (body) => {
  const runes = JSON.parse(window.PYISO.runes()).runes || [];
  body.innerHTML = `<div class="row" style="padding:10px 14px 0"><span class="note">Rune</span>
      <select id="isoRsel">${runes.map(r=>`<option value="${r.id}">${esc(r.name)}${r.synthetic?" (fixed spell set)":""}</option>`).join("")}</select></div>
    <div id="isoRbody"></div>`;
  $("isoRsel").onchange = () => renderRune(+$("isoRsel").value);
  renderRune(runes.length ? +runes[0].id : 0);
};
function renderRune(rid) {
  const r = JSON.parse(window.PYISO.rune(rid));
  if (r.error) { $("isoRbody").innerHTML = `<p class="bad" style="padding:14px">${esc(r.error)}</p>`; return; }
  let h = `<div class="subhd">${esc(r.name)}</div>`;
  h += `<div class="note" style="margin:0 14px">Edit every spell this rune teaches — element, damage/heal power,
    target (single / all / row / column / cluster) and status.${
      r.synthetic
        ? " This rune's spell list is fixed (no grant record), but each spell below is fully editable."
        : " Use “Spell set” below to change <b>which</b> spells it teaches."}</div>`;

  // Spell-set builder (real grant records only): which contiguous spells the rune grants.
  if (!r.synthetic && r.grant.length) {
    h += `<div class="subhd">Spell set — which spells this rune teaches</div>`;
    h += `<div class="grid" style="padding-top:8px">` + r.grant.map((f) => {
      const disp = f.label === "Start spell" ? "First spell (Lv1)" : f.label === "Spell count" ? "Levels" : f.label;
      return renderField({ ...f, field: f.label, label: disp },
        { view: "rune", ident: JSON.stringify({ id: rid }), key: `rune:${rid}:${f.label}`,
          prefix: `${r.name} · `, group: "Runes" });
    }).join("") + `</div>`;
  }

  // One editable field grid per spell the rune currently teaches (element/power/target/status).
  if (r.spells.length) {
    for (const s of r.spells) {
      h += `<div class="subhd">Lv${s.level} · ${esc(s.name)} <span class="note">(spell #${s.id})</span></div>`;
      h += `<div class="grid" style="padding-top:8px">` + s.fields.map((f) =>
        renderField(f, { view: "spell", ident: JSON.stringify({ id: s.id }), key: `spell:${s.id}:${f.label}`,
          prefix: `${s.name} · `, group: `Rune: ${r.name}` })).join("") + `</div>`;
    }
  } else {
    h += `<div class="note" style="padding:8px 14px">This rune teaches no spells.</div>`;
  }
  $("isoRbody").innerHTML = h;
}

/* simple record-list views (prices / rune prices / heal prices) */
function priceView(loadFn, view, cols) {
  return async (body) => {
    const rows = (JSON.parse(loadFn()).prices) || [];
    let h = `<div style="padding:10px 14px"><input class="pick-q" id="${view}Q" type="search" placeholder="filter…" style="max-width:260px"></div>
      <div class="tablewrap"><table><thead><tr><th>#</th><th>Name</th>${cols.map(c=>`<th>${c.label}</th>`).join("")}</tr></thead><tbody id="${view}Body">`;
    h += rows.map(p => rowFor(view, p, cols)).join("");
    body.innerHTML = h + `</tbody></table></div>`;
    $(view+"Q").oninput = () => {
      const f = $(view+"Q").value.trim().toLowerCase();
      qa(`#${view}Body tr`).forEach(tr => tr.style.display = (!f || tr.dataset.name.includes(f)) ? "" : "none");
    };
  };
}
function rowFor(view, p, cols) {
  const nm = p.name || ("#" + (p.index));
  return `<tr data-name="${esc(nm.toLowerCase())}"><td class="note">${p.index}</td><td>${esc(nm)}</td>` +
    cols.map(c => `<td class="cellwrap"><input type="number" min="0" value="${p[c.field]}"
      data-key="${view}:${p.index}:${c.field}" data-view="${view}" data-ident='${esc(JSON.stringify({index:p.index}))}'
      data-field="${c.field}" data-kind="num" data-orig="${p[c.field]}" data-lbl="${esc(nm+" "+c.label)}" data-grp="${esc(c.group)}"
      onchange="onIsoField(this)" style="width:100px">${REVERT_BTN}</td>`).join("") + `</tr>`;
}
VIEW_RENDER.price = priceView(() => window.PYISO.prices(), "price",
  [{ field: "buy", label: "Buy", group: "Item prices" }, { field: "sell", label: "Sell", group: "Item prices" }]);
VIEW_RENDER.runeprice = priceView(() => window.PYISO.runeprices(), "runeprice",
  [{ field: "buy", label: "Buy", group: "Rune prices" }, { field: "sell", label: "Sell", group: "Rune prices" }]);
VIEW_RENDER.healprice = priceView(() => window.PYISO.healprices(), "healprice",
  [{ field: "buy", label: "Buy", group: "Heal prices" }, { field: "sell", label: "Sell", group: "Heal prices" }]);

VIEW_RENDER.enemy = async (body) => {
  const list = JSON.parse(window.PYISO.enemies()).enemies || [];
  body.innerHTML = `<div class="row" style="padding:10px 14px 0"><span class="note">Enemy/unit</span>
      <select id="isoEsel">${list.map(e=>`<option value="${e.id}">${e.id}: ${esc(e.name)} (HP ${e.hp})</option>`).join("")}</select></div>
    <div id="isoEbody"></div>`;
  $("isoEsel").onchange = () => renderEnemy(+$("isoEsel").value);
  if (list.length) renderEnemy(+list[0].id); else $("isoEbody").innerHTML = `<p class="note" style="padding:14px">No enemy records found.</p>`;
};
function renderEnemy(eid) {
  const r = JSON.parse(window.PYISO.enemy(eid));
  if (r.error) { $("isoEbody").innerHTML = `<p class="bad" style="padding:14px">${esc(r.error)}</p>`; return; }
  $("isoEbody").innerHTML = `<div class="grid" style="padding-top:8px">` + r.fields.map(f =>
    renderField(f, { view: "enemy", ident: JSON.stringify({ id: eid }), key: `enemy:${eid}:${f.label}`,
      prefix: `Enemy ${eid} · `, group: "Enemies" })).join("") + `</div>`;
}

VIEW_RENDER.unite = async (body) => {
  const list = JSON.parse(window.PYISO.unites()).unites || [];
  body.innerHTML = `<div class="row" style="padding:10px 14px 0"><span class="note">Unite</span>
      <select id="isoUsel">${list.map(u=>`<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select></div>
    <div id="isoUbody"></div>`;
  window._unites = list;
  $("isoUsel").onchange = () => renderUnite(+$("isoUsel").value);
  if (list.length) renderUnite(+list[0].id); else $("isoUbody").innerHTML = `<p class="note" style="padding:14px">No unites found.</p>`;
};
function renderUnite(uid) {
  const u = (window._unites || []).find(x => x.id === uid); if (!u) return;
  const roster = JSON.parse(window.PYISO.chars()).chars || [];
  const list = roster.map(c => ({ id: c.id, name: c.name }));
  let h = `<div class="subhd">${esc(u.name)}</div><div class="note" style="margin:0 14px">${esc(u.effect||"")}</div>
    <div class="grid" style="padding-top:8px">`;
  h += u.members.map((m, slot) =>
    `<div class="fld"><label>Member ${slot+1}</label><div class="in">
      <button type="button" class="pickbtn" data-key="unite:${uid}:${slot}" data-view="unite"
        data-ident='${esc(JSON.stringify({id:uid,slot}))}' data-field="member" data-kind="charid"
        data-orig="${m.id}" data-lbl="${esc(u.name+" · member "+(slot+1))}" data-grp="Unites"
        onclick='pickUniteMember(this, ${JSON.stringify(list).replace(/'/g,"&#39;")})'>
        <span class="pickbtn-name">${esc(m.name)}</span><span class="pickbtn-id note">#${m.id}</span></button>
      </div></div>`).join("");
  $("isoUbody").innerHTML = h + `</div>`;
}
function pickUniteMember(el, list) {
  openPicker("Choose member", list, q(".pickbtn-id", el).textContent.replace("#",""), (id) => {
    const nm = (list.find(x => String(x.id) === String(id)) || {}).name || ("#" + id);
    q(".pickbtn-name", el).textContent = nm; q(".pickbtn-id", el).textContent = "#" + id;
    applyIsoWrite(el, id).then(() => {
      const changed = String(id) !== String(el.dataset.orig);
      if (changed) { isoEdits[el.dataset.key] = { label: el.dataset.lbl, group: "Unites", to: nm }; el.classList.add("dirty"); }
      else { delete isoEdits[el.dataset.key]; el.classList.remove("dirty"); }
      updateIsoToolbar(); captureUndoStep();
    });
  }, {});
}

VIEW_RENDER.mp = async (body) => {
  const r = JSON.parse(window.PYISO.mp());
  let h = "";
  for (const grp of r.groups) {
    h += `<div class="subhd">${esc(grp.label)}</div><div class="grid" style="padding-top:8px">`;
    h += grp.values.map((v, k) => renderField(
      { label: r.fields[k] || ("MP " + (k+1)), width: 2, kind: "num", value: v },
      { view: "mp", ident: JSON.stringify({ group: grp.group, idx: k }), key: `mp:${grp.group}:${k}`,
        prefix: `${grp.label} · `, group: "MP growth" })).join("");
    h += `</div>`;
  }
  body.innerHTML = h;
};

VIEW_RENDER.skillfx = async (body) => {
  const r = JSON.parse(window.PYISO.skillfx());
  let h = `<div style="padding:10px 14px"><input class="pick-q" id="fxQ" type="search" placeholder="filter skill…" style="max-width:260px"></div>
    <div class="tablewrap"><table><thead><tr><th>#</th><th>Skill</th>${r.ranks.map(rk=>`<th>${rk}</th>`).join("")}</tr></thead><tbody id="fxBody">`;
  h += r.skills.map(s => `<tr data-name="${esc((s.name||"").toLowerCase())}"><td class="note">${s.id}</td><td>${esc(s.name)}</td>` +
    s.values.map((v, k) => `<td class="cellwrap"><input type="number" min="0" value="${v}" style="width:78px"
      data-key="skillfx:${s.id}:${k}" data-view="skillfx" data-ident='${esc(JSON.stringify({id:s.id,rank:k}))}'
      data-field="v" data-kind="num" data-orig="${v}" data-lbl="${esc(s.name+" @"+r.ranks[k])}" data-grp="Skill effects"
      onchange="onIsoField(this)">${REVERT_BTN}</td>`).join("") + `</tr>`).join("");
  body.innerHTML = h + `</tbody></table></div>`;
  $("fxQ").oninput = () => { const f = $("fxQ").value.trim().toLowerCase();
    qa("#fxBody tr").forEach(tr => tr.style.display = (!f || tr.dataset.name.includes(f)) ? "" : "none"); };
};

VIEW_RENDER.balance = async (body) => {
  body.innerHTML = `<div style="padding:14px">
    <div class="note">${esc((isoMAPS.help||{}).stats||"")}</div>
    <p class="note">Scale every character's <b>starting stats</b> by a factor (baseline is remembered, so
      re-applying doesn't compound). Great for a quick Hard Mode. Applies to a new game.</p>
    <div class="row">
      <label>Factor <input type="number" id="hmFactor" value="1.5" min="0.1" max="10" step="0.1" style="width:90px"></label>
      <button id="hmApply">Apply multiplier</button>
      <button class="ghost" id="hmRestore">Restore original</button>
    </div>
    <div id="hmMsg" class="note"></div></div>`;
  $("hmApply").onclick = async () => {
    spin(true);
    try {
      const r = JSON.parse(window.PYISO.hardmode(+$("hmFactor").value));
      if (r.error) { toast(r.error, "bad"); return; }
      isoEdits["balance"] = { label: `Hard Mode ×${$("hmFactor").value} (all starting stats)`, group: "Balance", to: "applied" };
      $("hmMsg").textContent = `Scaled ${r.count} characters.`; updateIsoToolbar(); captureUndoStep();
    } finally { spin(false); }
  };
  $("hmRestore").onclick = async () => {
    spin(true);
    try {
      const r = JSON.parse(window.PYISO.hmrestore());
      if (r.error) { toast(r.error, "bad"); return; }
      delete isoEdits["balance"];
      $("hmMsg").textContent = `Restored ${r.count} characters.`; updateIsoToolbar(); captureUndoStep();
    } finally { spin(false); }
  };
};

VIEW_RENDER.name = async (body) => {
  const r = JSON.parse(window.PYISO.names(0));
  if (r.error) { body.innerHTML = `<p class="bad" style="padding:14px">${esc(r.error)}</p>`; return; }
  let h = `<div class="note" style="padding:10px 14px">Character name table (ASCII, max 7 chars). Edits apply to a new game.</div>
    <div class="grid" style="padding:0 14px 14px">`;
  h += (r.names || []).map(n => `<div class="fld"><label>#${n.index}</label><div class="in">
      <input maxlength="7" value="${esc(n.name)}" data-key="name:${n.index}" data-view="name"
        data-ident='${esc(JSON.stringify({index:n.index}))}' data-field="name" data-kind="str"
        data-orig="${esc(n.name)}" data-lbl="Name #${n.index}" data-grp="Names" onchange="onIsoField(this)">${REVERT_BTN}</div></div>`).join("");
  body.innerHTML = h + `</div>`;
};

VIEW_RENDER.ref = async (body) => {
  const ref = JSON.parse(window.PYISO.reference());
  const cats = Object.keys(ref);
  if (!cats.length) { body.innerHTML = `<p class="note" style="padding:14px">No reference lists.</p>`; return; }
  body.innerHTML = `<div class="row" style="padding:10px 14px 0"><span class="note">List</span>
      <select id="refSel">${cats.map(c=>`<option>${esc(c)}</option>`).join("")}</select>
      <input class="pick-q" id="refQ" type="search" placeholder="filter…" style="max-width:220px"></div>
    <div class="note" style="margin:6px 14px">Read-only name reference.</div>
    <div id="refBody" class="grid" style="padding:0 14px 14px"></div>`;
  const show = () => {
    const list = ref[$("refSel").value] || [], f = $("refQ").value.trim().toLowerCase();
    $("refBody").innerHTML = list.filter(x => !f || String(x.name).toLowerCase().includes(f) || String(x.i).includes(f))
      .slice(0, 500).map(x => `<div class="note">${x.i}: ${esc(x.name)}</div>`).join("");
  };
  $("refSel").onchange = show; $("refQ").oninput = show; show();
};

/* ---------------- dirty tracking, toolbar, Save, recipe ---------------- */
function recomputeIsoDirty() {
  if (!ORIG) return { runs: 0, bytes: 0 };
  const cur = pyodide.FS.readFile(ISO_PATH);
  return DiffCore.runStats(DiffCore.diffRuns(ORIG, cur));
}
function updateIsoToolbar() {
  if (isoBadgeRAF) return;
  isoBadgeRAF = requestAnimationFrame(() => {
    isoBadgeRAF = 0;
    const d = recomputeIsoDirty();
    const badge = $("isoUnsaved");
    badge.textContent = d.runs ? `${d.bytes} byte${d.bytes===1?"":"s"} in ${d.runs} run${d.runs===1?"":"s"}` : "No changes";
    badge.classList.toggle("on", d.runs > 0);
    const dot = q("#isoSaveBtn .dot"); if (dot) dot.classList.toggle("hidden", d.runs === 0);
  });
}
/* ---------------- undo / redo (B18) ----------------
 * After each user action, diff /iso.bin vs the last snapshot into changed runs and push
 * one step (with the before/after bytes AND the edit-map snapshot). One action = one step,
 * with zero per-call-site wiring beyond the single captureUndoStep() in commitIso. */
function captureUndoStep() {
  if (!ISO_PREV) return;
  const cur = pyodide.FS.readFile(ISO_PATH);
  const runs = DiffCore.diffRuns(ISO_PREV, cur);
  if (!runs.length) return;
  const step = { runs: runs.map(r => ({ off: r.off, before: ISO_PREV.slice(r.off, r.off + r.len), after: r.bytes })),
                 editsBefore: ISO_PREV_EDITS, editsAfter: { ...isoEdits } };
  undoStack.push(step);
  if (undoStack.length > UNDO_MAX) undoStack.shift();
  redoStack.length = 0;
  ISO_PREV = cur.slice(); ISO_PREV_EDITS = { ...isoEdits };
  updateUndoButtons();
}
function applyStep(step, dir) {   // dir "undo" restores .before, "redo" restores .after
  const cur = pyodide.FS.readFile(ISO_PATH);
  for (const r of step.runs) cur.set(dir === "undo" ? r.before : r.after, r.off);
  pyodide.FS.writeFile(ISO_PATH, cur);
  isoEdits = { ...(dir === "undo" ? step.editsBefore : step.editsAfter) };
  ISO_PREV = pyodide.FS.readFile(ISO_PATH).slice(); ISO_PREV_EDITS = { ...isoEdits };
  if (curView) selectView(curView);     // re-render from the restored bytes
  updateIsoToolbar(); updateUndoButtons();
}
function isoUndo() { if (!undoStack.length) return; const s = undoStack.pop(); redoStack.push(s); applyStep(s, "undo"); }
function isoRedo() { if (!redoStack.length) return; const s = redoStack.pop(); undoStack.push(s); applyStep(s, "redo"); }
function updateUndoButtons() {
  const u = $("isoUndoBtn"), r = $("isoRedoBtn");
  if (u) u.disabled = undoStack.length === 0;
  if (r) r.disabled = redoStack.length === 0;
}

function isoReviewGroups(extraNote) {
  const byGroup = {};
  Object.values(isoEdits).forEach(e => { (byGroup[e.group] = byGroup[e.group] || []).push(e); });
  const groups = Object.keys(byGroup).map(gt => ({ title: gt,
    rows: byGroup[gt].map(e => ({ label: e.label, from: "", to: e.to })) }));
  if (extraNote) groups.push({ title: "Note", rows: [{ label: extraNote, from: "", to: "" }] });
  return groups;
}
function saveISO() {
  const d = recomputeIsoDirty();
  if (!d.runs) { toast("No changes to save.", "ok"); return; }
  if (!isoHandle) {   // opened via file input (mobile / no FS Access) → recipe is the way out
    toast("This browser can't write the ISO in place — exporting a recipe instead.", "ok");
    exportRecipe(); return;
  }
  const groups = isoReviewGroups(`Writes ${d.bytes} byte(s) in ${d.runs} run(s) into the ISO in place.`);
  confirmReview("Apply to ISO", groups, `Save to ${isoHandle.name || "ISO"}`, writeISO);
}
async function writeISO() {
  spin(true);
  try {
    let p = await isoHandle.queryPermission({ mode: "readwrite" });
    if (p !== "granted") p = await isoHandle.requestPermission({ mode: "readwrite" });
    if (p !== "granted") { toast("Write permission was not granted.", "bad"); return; }
    const cur = pyodide.FS.readFile(ISO_PATH);
    const runs = DiffCore.diffRuns(ORIG, cur);
    const w = await isoHandle.createWritable({ keepExistingData: true });
    try {
      for (const r of runs) await w.write({ type: "write", position: r.off, data: r.bytes });
    } finally { await w.close(); }
    ORIG = cur.slice(); ISO_PREV = cur.slice(); ISO_PREV_EDITS = {}; isoEdits = {};
    undoStack.length = 0; redoStack.length = 0; updateUndoButtons();
    qa("#isoBody .dirty").forEach(e => e.classList.remove("dirty"));
    updateIsoToolbar();
    toast(`Saved ${runs.length} change-run(s) into the ISO.`, "ok");
  } catch (e) { toast("Save failed: " + (e.message || e), "bad"); console.error(e); }
  finally { spin(false); }
}

/* Export a portable .s5mod recipe (built from the diff, matching what Save would write). */
function exportRecipe() {
  if (!ORIG) return;
  const cur = pyodide.FS.readFile(ISO_PATH);
  const runs = DiffCore.diffRuns(ORIG, cur, 0);   // gap 0: one entry per contiguous change
  if (!runs.length) { toast("No changes to export.", "ok"); return; }
  const serial = [];
  for (let i = 0; i < 11; i++) serial.push(String.fromCharCode(ORIG[0x828BD + i]));
  const patches = runs.map(r => ({ off: r.off, old: DiffCore.toHex(ORIG.slice(r.off, r.off + r.len)), new: DiffCore.toHex(r.bytes) }));
  const mod = { format: "s5mod", version: 1, serial: serial.join(""), note: "web editor",
    patchCount: patches.length, patches };
  downloadBlob(new TextEncoder().encode(JSON.stringify(mod, null, 0)), "edits.s5mod.json", "application/json");
  toast(`Exported recipe: ${patches.length} run(s).`, "ok");
}
function importRecipe() {
  const inp = document.createElement("input"); inp.type = "file"; inp.accept = ".json,.s5mod,application/json";
  inp.onchange = async () => {
    const f = inp.files[0]; if (!f) return;
    try {
      const mod = JSON.parse(await f.text());
      const r = JSON.parse(window.PYISO.importmod(JSON.stringify(mod)));
      if (r.error) { toast("Recipe rejected: " + r.error, "bad"); return; }
      isoEdits["imported"] = { label: `Imported recipe (${r.patchCount} run(s)${r.mismatchedRuns?`, ${r.mismatchedRuns} mismatch`:""})`, group: "Imported", to: "staged" };
      if (curView) selectView(curView);      // re-render so inputs reflect imported bytes
      updateIsoToolbar(); captureUndoStep();
      toast(`Applied recipe: ${r.appliedBytes} byte(s)${r.mismatchedRuns?` — ${r.mismatchedRuns} mismatch(es), check region`:""}.`, r.mismatchedRuns ? "bad" : "ok");
    } catch (e) { toast("Could not read recipe: " + (e.message || e), "bad"); }
  };
  inp.click();
}
