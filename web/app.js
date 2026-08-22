/* Suikoden V Save Editor — browser front-end.
 *
 * The save logic is NOT reimplemented here. We run the desktop editor's own
 * pure-Python module (../Editor/s5save.py) unchanged inside Pyodide (CPython in
 * WebAssembly). The picked file is written into Pyodide's in-memory filesystem at
 * /save.bin, we call the module's normal path-based read/write functions, then read
 * the edited bytes back out and hand them to the browser as a download. The save
 * never leaves the device. Names for dropdowns come from the same JSON tables the
 * desktop server serves. */

"use strict";

const EDITOR_DIR = "../Editor";               // reused desktop module + name tables
const SAVE_PATH = "/save.bin";                 // Pyodide MEMFS path we operate on

let pyodide = null;
let PY = null;                                  // { open, chars, write } Python callables
let NAMES = null;                              // { chars, armorNames, runeNames, skillNames, rankNames }
let saves = [];                                // decoded save list for the current file
let CHARDATA = {};                             // per-save-card cached {chars:[...]}
let curFileName = "save.bin";
let dirty = false;

/* ---- Hardcoded name tables (identical to the desktop /api/savechars response) ---- */
const SKILL_NAMES = ["Stamina","Attack","Defense","Technique","Vitality","Agility","Magic",
  "Magic Defense","Incantation","Sword of Magic","Raging Lion","Fate Control",
  "Karmic Effect","Armor of Gods","Swift Foot","Triple Harmony","All-out Strike",
  "Untold Clarity","Divine Right","Zen Sword","Sacred Oath","Royal Paradise","Thief",
  "Mow Down","Pierce","Freeze","(unused)","Barrage","Long Throw","Dragon Special",
  "Forge","Combat Teacher","Chain Magic","Analyze","Potch Finder","Treasure Hunt",
  "Escape Route","Healing","Treatment","Haggle","Trade In","Cook","Rune Sage","Bard",
  "Perfect Pitch","Appraisal","Bath","Tutor"];
const RANK_NAMES = ["None","E","D","C","B","A","S","SS"];

/* ---------- small DOM / UX helpers ---------- */
const $ = (id) => document.getElementById(id);
const esc = (x) => String(x == null ? "" : x).replace(/&/g,"&amp;").replace(/</g,"&lt;")
  .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
function toast(msg, kind){
  const t = document.createElement("div");
  t.className = "tst" + (kind ? " " + kind : "");
  t.textContent = msg;
  $("toast").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 3600);
  setTimeout(() => t.remove(), 4100);
}
const spin = (on) => $("spin").classList.toggle("on", !!on);
function setBoot(msg, pct){ if($("bootmsg")) $("bootmsg").textContent = msg;
  if(pct != null && $("bootfill")) $("bootfill").style.width = pct + "%"; }

/* ---------- boot: Pyodide + reused module + name tables ---------- */
async function boot(){
  try{
    setBoot("Downloading the Python runtime (Pyodide)…", 12);
    pyodide = await loadPyodide({ indexURL: `https://cdn.jsdelivr.net/pyodide/v${window.PYODIDE_VERSION}/full/` });

    setBoot("Loading the save engine…", 55);
    const src = await (await fetch(`${EDITOR_DIR}/s5save.py`)).text();
    pyodide.FS.mkdirTree("/editor");
    pyodide.FS.writeFile("/editor/s5save.py", src);

    setBoot("Wiring things up…", 72);
    pyodide.runPython(GLUE);
    PY = {
      open:  pyodide.globals.get("open_save"),
      chars: pyodide.globals.get("save_chars"),
      write: pyodide.globals.get("save_write"),
    };

    setBoot("Loading name tables…", 88);
    NAMES = await loadNames();

    setBoot("Ready.", 100);
    $("boot").style.display = "none";
    $("opencard").style.display = "";
    $("file").addEventListener("change", onFile);
    registerSW();
  }catch(e){
    setBoot("Failed to load: " + (e && e.message ? e.message : e), 100);
    $("bootfill").style.background = "var(--bad)";
    toast("Could not start the editor — check your connection and reload.", "bad");
    console.error(e);
  }
}

async function loadNames(){
  const [chars, armor, runes] = await Promise.all([
    fetch(`${EDITOR_DIR}/s5_characters.json`).then(r => r.json()),
    fetch(`${EDITOR_DIR}/s5_armor_names.json`).then(r => r.json()),
    fetch(`${EDITOR_DIR}/s5_rune_ids.json`).then(r => r.json()),
  ]);
  const cnames = {};
  for(const c of chars) cnames[c.id] = c.name;
  // desktop maps head->helm; body/glove/foot pass through
  const armorNames = { helm: armor.head||{}, body: armor.body||{},
                       glove: armor.glove||{}, foot: armor.foot||{} };
  const runeNames = {};
  runes.forEach((n, i) => { runeNames[String(i)] = n; });
  return { cnames, armorNames, runeNames, skillNames: SKILL_NAMES, rankNames: RANK_NAMES };
}

/* ---------- Python glue (runs inside Pyodide) ---------- */
const GLUE = `
import sys, json
sys.path.insert(0, "/editor")
import s5save as SV
SV.BACKUPS = False   # no on-disk original in the browser; the user keeps their own file

def _individual(path):
    head = open(path, "rb").read(20)
    return head[:4] == b"CFU\\x00" or head[:17] == b"\\x0d\\x00\\x00\\x00SharkPortSave"

def open_save(path):
    try:
        if _individual(path):
            s = SV.read_individual_save(path)
            if not s:
                return json.dumps({"error": "no Suikoden V save found in that file"})
            s["individual"] = True; s["editable"] = True
            s.setdefault("meta", {"title": ""})
            saves = [s]
        else:
            saves = SV.read_all_saves(path)
        if not saves:
            return json.dumps({"error": "no Suikoden V save found in that file"})
        return json.dumps({"saves": saves})
    except Exception as e:
        return json.dumps({"error": "could not read save: " + str(e)})

def save_chars(path, folder):
    try:
        gd = SV.read_gamedata_payload(path, folder or None)
        if not gd:
            return json.dumps({"error": "could not read gamedata payload"})
        return json.dumps({"chars": SV.read_all_characters(gd)})
    except Exception as e:
        return json.dumps({"error": str(e)})

def save_write(path, folder, edits_json):
    try:
        edits = json.loads(edits_json)
        if _individual(path):
            r = SV.write_individual_save(path, edits)
        else:
            r = SV.write_save_fields(path, folder, edits)
        return json.dumps(r)
    except Exception as e:
        return json.dumps({"error": str(e)})
`;

/* ---------- open a file ---------- */
async function onFile(ev){
  const f = ev.target.files && ev.target.files[0];
  if(!f) return;
  curFileName = f.name;
  $("filename").textContent = `${f.name} · ${(f.size/1048576).toFixed(2)} MB`;
  spin(true);
  try{
    const buf = new Uint8Array(await f.arrayBuffer());
    pyodide.FS.writeFile(SAVE_PATH, buf);
    dirty = false; CHARDATA = {};
    const res = JSON.parse(PY.open(SAVE_PATH));
    if(res.error){ $("saves").innerHTML = `<p class="bad" style="padding:8px">${esc(res.error)}</p>`;
      toast(res.error, "bad"); return; }
    saves = res.saves;
    saves.forEach(s => s.card = curFileName);
    renderSaves();
    toast(`Opened ${saves.length} save(s)`, "ok");
  }catch(e){
    toast("Could not read that file: " + (e.message||e), "bad"); console.error(e);
  }finally{ spin(false); }
}

/* ---------- render the list of saves ---------- */
function renderSaves(){
  const d = $("saves");
  d.innerHTML = saves.map((sv, i) => {
    const fl = sv.fields || {};
    const badge = sv.region
      ? `<span class="badge ${sv.region=='PAL'?'b-pal':sv.region=='NTSC-U'?'b-ntsc':'b-jp'}">${esc(sv.region)}</span>` : "";
    const ro = sv.editable === false;
    const foot = ro
      ? `<span class="note">Read-only format. Export to .xps or a memory card to edit.</span>`
      : `<button onclick="saveWrite(${i})">Apply name / NG+</button>
         <button class="ghost" onclick="openChars(${i})">Characters (equipment &amp; runes)…</button>
         <button class="ghost" onclick="openRecruit(${i})">Recruitment…</button>
         <span class="note">Applies hero/castle name + New Game Plus to the working copy.</span>`;
    return `<div class="sec">
      <div class="card-hd">${esc(sv.folder)} ${badge}
        <span class="note">· ${esc(sv.card)} · ${esc((sv.meta&&sv.meta.title)||"")}</span></div>
      <div class="grid">
        <div class="fld"><label>Hero name</label><div class="in">
          <input id="sv${i}_heroName" value="${esc(fl.heroName)}" maxlength="15" ${ro?"disabled":""}></div></div>
        <div class="fld"><label>Castle name</label><div class="in">
          <input id="sv${i}_castleName" value="${esc(fl.castleName)}" maxlength="15" ${ro?"disabled":""}></div></div>
        <div class="fld"><label>Level <span class="note">(display only)</span></label><div class="in">
          <input type="number" value="${fl.level||0}" disabled title="Save-select display level. Edit unit levels in the Characters panel."></div></div>
        <div class="fld"><label>New Game Plus</label><div class="in">
          <label class="chk"><input type="checkbox" id="sv${i}_ngp" ${fl.newGamePlus?"checked":""} ${ro?"disabled":""}></label></div></div>
      </div>
      <div class="card-ft">${foot}</div>
      <div id="chars${i}" class="note" style="margin:0 14px 8px"></div>
      <div id="recruit${i}" style="margin:0 14px 10px"></div>
    </div>`;
  }).join("");
  updateDlBar();
}

/* ---------- header fields write ---------- */
async function saveWrite(i){
  const sv = saves[i];
  const edits = {
    heroName:   $(`sv${i}_heroName`).value,
    castleName: $(`sv${i}_castleName`).value,
    newGamePlus: $(`sv${i}_ngp`).checked ? 1 : 0,
  };
  await applyEdits(sv, edits, (r) => toast(`Applied ${r.changed} field(s)`, "ok"));
}

/* ---------- characters editor ---------- */
async function openChars(i){
  const sv = saves[i], box = $(`chars${i}`);
  if(box._loading) return;
  if(box._open){ box._open = false; box.innerHTML = ""; return; }
  box._loading = true; box.innerHTML = "loading characters…";
  const r = await fetchChars(sv);
  box._loading = false;
  if(r.error){ box.innerHTML = `<span class="bad">${esc(r.error)}</span>`; return; }
  box._open = true; CHARDATA[i] = r;
  const opts = r.chars.map(c => `<option value="${c.idx}">${c.idx}: ${esc(nameOf(c.idx))}${
    c.recruited||!c.recruitable ? "" : " (not recruited)"}</option>`).join("");
  box.innerHTML = `<div class="row"><span class="note">Character</span>
    <select id="csel${i}" onchange="renderChar(${i})">${opts}</select>
    <button onclick="writeChar(${i})">Apply character</button>
    <button class="ghost" onclick="recruitAll(${i})" title="Set the recruited flag for every recruitable character (108 Stars)">Recruit ALL</button>
    </div><div id="cfld${i}"></div>`;
  renderChar(i);
}
const nameOf = (idx) => NAMES.cnames[idx] || ("Character " + idx);

function selHTML(id, names, val){
  let h = `<select id="${id}">`;
  const keys = Object.keys(names);
  if(!(String(val) in names)) h += `<option value="${val}" selected>#${val} (unknown)</option>`;
  for(const k of keys) h += `<option value="${k}" ${String(val)===k?"selected":""}>${k}: ${esc(names[k])}</option>`;
  return h + "</select>";
}
function renderChar(i){
  const r = CHARDATA[i], idx = +$(`csel${i}`).value;
  const c = r.chars.find(x => x.idx === idx);
  const A = NAMES.armorNames, RN = NAMES.runeNames, SN = NAMES.skillNames, RK = NAMES.rankNames;
  const asel = (slot,lbl,val) => `<div class="fld"><label>${lbl}</label><div class="in">${selHTML("ce"+i+"_"+slot, A[slot], val)}</div></div>`;
  const rsel = (slot,lbl,val) => `<div class="fld"><label>${lbl}</label><div class="in">${selHTML("ce"+i+"_"+slot, RN, val)}</div></div>`;
  const sub = (t) => `<div class="subhd">${t}</div>`;
  const g = (inner) => `<div class="grid" style="padding-top:8px">${inner}</div>`;
  $(`cfld${i}`).innerHTML =
    sub("Level & recruitment")
    + `<div class="grid" style="grid-template-columns:150px 260px;padding-top:8px">`
    + `<div class="fld"><label>Level</label><div class="in"><input type="number" id="ce${i}_level" value="${c.level}" min="1" max="99"></div></div>`
    + `<div class="fld"><label>Recruited</label><div class="in"><label class="chk"><input type="checkbox" id="ce${i}_rec" ${c.recruited?"checked":""} ${c.recruitable?"":'disabled title="Not recruitable (story/antagonist)"'}> <span class="note">${c.recruitable?"in the roster":"not recruitable"}</span></label></div></div>`
    + `</div>`
    + sub("Equipment")
    + g(asel("helm","Helm",c.armor.helm)+asel("body","Armor",c.armor.body)+asel("glove","Gloves",c.armor.glove)+asel("foot","Boots",c.armor.foot))
    + sub("Runes")
    + g(rsel("rhead","Head",c.runes.rhead)+rsel("rright","Right hand",c.runes.rright)+rsel("rleft","Left hand",c.runes.rleft))
    + sub("Equipped skill slots")
    + g([0,1].map(k => { const names={"0":"— empty —"}; SN.forEach((n,s)=>{ if(s!==26) names[String(s+1)]=n; });
        return `<div class="fld"><label>Slot ${k+1}</label><div class="in">${selHTML("ce"+i+"_ss"+k, names, (c.slots||[0,0])[k])}</div></div>`; }).join(""))
    + sub("Skill ranks")
    + g((c.skills||[]).map((v,s)=> s===26 ? "" :
        `<div class="fld"><label>${esc(SN[s]||("Skill "+s))}</label><div class="in">${selHTML("ce"+i+"_sk"+s, RK, v)}</div></div>`).join(""))
    + `<div style="padding:8px 14px 12px"><span class="note">Level, armor, runes and skill ranks are reverse-engineered and cross-verified. Rune 0 = empty slot.${
        c.recruited||!c.recruitable ? "" : " · <b>This character is not yet recruited in this save.</b>"}</span></div>`;
}
async function writeChar(i){
  const sv = saves[i], idx = +$(`csel${i}`).value, edits = {};
  for(const s of ["level","helm","body","glove","foot","rhead","rright","rleft"]){
    const el = $(`ce${i}_${s}`); if(el) edits[`c${idx}_${s}`] = +el.value;
  }
  for(let s=0;s<48;s++){ const el = $(`ce${i}_sk${s}`); if(el) edits[`c${idx}_sk${s}`] = +el.value; }
  for(const s of ["ss0","ss1"]){ const el = $(`ce${i}_${s}`); if(el) edits[`c${idx}_${s}`] = +el.value; }
  const rec = $(`ce${i}_rec`); if(rec && !rec.disabled) edits[`c${idx}_rec`] = rec.checked ? 1 : 0;
  await applyEdits(sv, edits, (r) => toast(`Applied ${r.changed} field(s) to ${nameOf(idx)}`, "ok"), i);
}

/* ---------- recruitment roster ---------- */
async function openRecruit(i){
  const sv = saves[i], box = $(`recruit${i}`);
  if(box._loading) return;
  if(box._open){ box._open = false; box.innerHTML = ""; return; }
  box._loading = true; box.innerHTML = `<span class="note">loading roster…</span>`;
  const r = await fetchChars(sv);
  box._loading = false;
  if(r.error){ box.innerHTML = `<span class="bad">${esc(r.error)}</span>`; return; }
  box._open = true; CHARDATA[i] = r;
  box.innerHTML = `<div class="sec"><h3 style="padding:10px 14px;background:var(--panel2);color:var(--gold2);text-transform:uppercase;font-size:13px">Recruitment — <span id="reccount${i}"></span></h3>
    <div class="row" style="padding:10px 14px 0">
      <input id="recfilter${i}" size="18" placeholder="search name…" oninput="recruitFilter(${i})">
      <button class="ghost mini" onclick="recruitCheckAll(${i},true)">Check all</button>
      <button class="ghost mini" onclick="recruitCheckAll(${i},false)">Uncheck all</button>
      <button onclick="writeRecruit(${i})">Apply changes</button>
      <span class="note">greyed = not recruitable (story/antagonist)</span>
    </div>
    <div id="recgrid${i}" class="recgrid"></div></div>`;
  $(`recgrid${i}`).innerHTML = r.chars.map(c => {
    const dis = !c.recruitable;
    return `<label class="chk" data-name="${esc(nameOf(c.idx).toLowerCase())}" style="${dis?"opacity:.4":""}">
      <input type="checkbox" data-idx="${c.idx}" ${c.recruited?"checked":""} ${dis?"disabled":""} onchange="recruitCount(${i})">
      <span class="note" style="text-transform:none">${c.idx}: ${esc(nameOf(c.idx))}</span></label>`;
  }).join("");
  recruitCount(i);
}
function recruitCount(i){
  const boxes = [...document.querySelectorAll(`#recgrid${i} input[type=checkbox]`)];
  const on = boxes.filter(b => b.checked).length;
  const el = $(`reccount${i}`); if(el) el.textContent = `${on} / ${boxes.filter(b=>!b.disabled).length} recruited`;
}
function recruitFilter(i){
  const q = $(`recfilter${i}`).value.trim().toLowerCase();
  document.querySelectorAll(`#recgrid${i} label`).forEach(l => {
    l.style.display = (!q || l.dataset.name.includes(q)) ? "" : "none";
  });
}
function recruitCheckAll(i, on){
  document.querySelectorAll(`#recgrid${i} input[type=checkbox]`).forEach(b => {
    if(!b.disabled && b.closest("label").style.display !== "none") b.checked = on;
  });
  recruitCount(i);
}
async function writeRecruit(i){
  const sv = saves[i], r = CHARDATA[i], edits = {}; let n = 0;
  document.querySelectorAll(`#recgrid${i} input[type=checkbox]`).forEach(b => {
    if(b.disabled) return;
    const idx = +b.dataset.idx, c = r.chars.find(x => x.idx === idx);
    if(c && (!!c.recruited) !== b.checked){ edits[`c${idx}_rec`] = b.checked ? 1 : 0; n++; }
  });
  if(!n){ toast("No recruitment changes", "ok"); return; }
  await applyEdits(sv, edits, (res) => toast(`Applied ${res.changed} recruitment change(s)`, "ok"), i);
}
async function recruitAll(i){
  const sv = saves[i], r = CHARDATA[i], edits = {}; let n = 0;
  for(const c of r.chars){ if(c.recruitable && !c.recruited){ edits[`c${c.idx}_rec`] = 1; n++; } }
  if(!n){ toast("Everyone recruitable is already recruited", "ok"); return; }
  await applyEdits(sv, edits, (res) => toast(`Recruited ${res.changed} character(s)`, "ok"), i);
}

/* ---------- shared write + refresh ---------- */
async function fetchChars(sv){
  return JSON.parse(PY.chars(SAVE_PATH, sv.folder || ""));
}
async function applyEdits(sv, edits, ok, refreshIdx){
  spin(true);
  try{
    const r = JSON.parse(PY.write(SAVE_PATH, sv.folder || "", JSON.stringify(edits)));
    if(r.error){ toast("Error: " + r.error, "bad"); return; }
    if(!r.changed){ toast("No changes to apply", "ok"); return; }
    dirty = true;
    ok && ok(r);
    // refresh decoded header fields + cached characters so the UI shows new values
    const re = JSON.parse(PY.open(SAVE_PATH));
    if(!re.error){ saves = re.saves; saves.forEach(s => s.card = curFileName); }
    if(refreshIdx != null){
      const rr = await fetchChars(sv);
      if(!rr.error) CHARDATA[refreshIdx] = rr;
    }
    updateDlBar();
  }catch(e){
    toast("Write failed: " + (e.message||e), "bad"); console.error(e);
  }finally{ spin(false); }
}

/* ---------- download the edited working copy ---------- */
function updateDlBar(){
  let bar = $("dlbar");
  if(!dirty){ if(bar) bar.remove(); return; }
  if(!bar){
    bar = document.createElement("div");
    bar.id = "dlbar";
    bar.style = "position:fixed;left:14px;bottom:14px;z-index:70";
    bar.innerHTML = `<button onclick="downloadSave()" style="box-shadow:var(--shadow)">⤓ Download edited save</button>`;
    document.body.appendChild(bar);
  }
}
function downloadSave(){
  const bytes = pyodide.FS.readFile(SAVE_PATH);           // Uint8Array
  const blob = new Blob([bytes], { type: "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const dot = curFileName.lastIndexOf(".");
  a.download = dot > 0 ? `${curFileName.slice(0,dot)}.edited${curFileName.slice(dot)}`
                       : `${curFileName}.edited`;
  a.href = url; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  toast("Downloaded — keep your original until it loads in-game.", "ok");
}

/* ---------- theme + PWA ---------- */
$("themeToggle").addEventListener("change", (e) => {
  document.body.classList.toggle("light", e.target.checked);
  try{ localStorage.setItem("s5theme", e.target.checked ? "light" : "dark"); }catch(_){}
});
try{ if(localStorage.getItem("s5theme") === "light"){ document.body.classList.add("light"); $("themeToggle").checked = true; } }catch(_){}

function registerSW(){
  if("serviceWorker" in navigator){
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

boot();
