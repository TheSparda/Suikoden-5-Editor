/* Suikoden V — Save editor + shared Pyodide boot.
 *
 * The engines are NOT reimplemented: this runs the desktop editor's own pure-Python
 * modules (../Editor/s5save.py for saves, ../Editor/s5patch.py + s5fields.py for the
 * ISO) UNCHANGED inside Pyodide. boot() loads Pyodide once and both editors share it
 * (iso.js reaches the same PY handles). Nothing leaves the device.
 *
 * Save-editor model: edits are written through to Pyodide's in-memory /save.bin as you
 * change fields (the real byte layout + any checksum/ECC come from s5save.py), while a
 * labelled `edits` map drives the dirty highlight, the "N unsaved" badge and the
 * review-before-you-save list. "Save" reviews the changes, then writes the edited copy
 * back to your file (File System Access), shares it, or downloads it. */

"use strict";

const EDITOR_DIR = "../Editor";
const SAVE_PATH = "/save.bin";

let pyodide = null;
let PY = null;                 // save adapters { open, chars, write }
let NAMES = null;             // { cnames, armorNames, runeNames, skillNames, rankNames }
let saves = [];               // decoded saves for the current file
let CHARDATA = {};            // per-save cached { chars:[...] }
let curFileName = "save.bin";
let saveHandle = null;        // FileSystemFileHandle when opened via the FS-Access picker
let sEdits = {};              // labelled pending edits: key -> { label, group, to }
let badgeRAF = 0;

/* Name tables identical to the desktop /api/savechars response. */
const SKILL_NAMES = ["Stamina","Attack","Defense","Technique","Vitality","Agility","Magic",
  "Magic Defense","Incantation","Sword of Magic","Raging Lion","Fate Control",
  "Karmic Effect","Armor of Gods","Swift Foot","Triple Harmony","All-out Strike",
  "Untold Clarity","Divine Right","Zen Sword","Sacred Oath","Royal Paradise","Thief",
  "Mow Down","Pierce","Freeze","(unused)","Barrage","Long Throw","Dragon Special",
  "Forge","Combat Teacher","Chain Magic","Analyze","Potch Finder","Treasure Hunt",
  "Escape Route","Healing","Treatment","Haggle","Trade In","Cook","Rune Sage","Bard",
  "Perfect Pitch","Appraisal","Bath","Tutor"];
const RANK_NAMES = ["None","E","D","C","B","A","S","SS"];

/* ↺ per-field undo button (shown only when its field is dirty, via CSS :has). */
const REVERT_BTN_SAVE = `<button type="button" class="revert" title="Undo this change" onclick="revertSaveField(this)" tabindex="-1">↺</button>`;

/* JSON tables the Pyodide engines read from /editor (s5fields.res_json opens them there). */
const ENGINE_JSON = [
  "s5_characters.json","s5_armor_names.json","s5_rune_ids.json","s5_rune_names.json",
  "s5_skill_names.json","s5_runeprice_names.json","s5_healprice_names.json",
  "s5_unite_names.json","s5_skilleffect_names.json","s5_drop_items.json",
  "s5_armor_stat_names.json","s5_item_names.json","s5_held_items.json",
  "s5_held_items_pal.json","s5_ref_english.json","s5_reference.json",
  "s5_enemy_names.json","s5_spell_names.json"
];

function setBoot(msg, pct){ if($("bootmsg")) $("bootmsg").textContent = msg;
  if(pct != null && $("bootfill")) $("bootfill").style.width = pct + "%"; }

/* ---------------- shared boot: Pyodide + both engines ---------------- */
async function boot(){
  try{
    initTheme(); initPWA(); initModeTabs(); initUpdateCheck();
    setBoot("Downloading the Python runtime (Pyodide)…", 10);
    pyodide = await loadPyodide({ indexURL: `https://cdn.jsdelivr.net/pyodide/v${window.PYODIDE_VERSION}/full/` });
    window.pyodide = pyodide;

    setBoot("Loading the editor engines…", 45);
    pyodide.FS.mkdirTree("/editor");
    const modules = ["s5save.py","s5patch.py","s5fields.py"];
    await Promise.all(modules.map(async (m) => {
      const src = await (await fetch(`${EDITOR_DIR}/${m}`)).text();
      pyodide.FS.writeFile(`/editor/${m}`, src);
    }));

    setBoot("Loading data tables…", 70);
    await Promise.all(ENGINE_JSON.map(async (j) => {
      try{
        const txt = await (await fetch(`${EDITOR_DIR}/${j}`)).text();
        pyodide.FS.writeFile(`/editor/${j}`, txt);
      }catch(_){ /* optional table missing → engine falls back to empty */ }
    }));

    setBoot("Wiring things up…", 85);
    pyodide.runPython(GLUE);
    PY = {
      open:  pyodide.globals.get("open_save"),
      chars: pyodide.globals.get("save_chars"),
      write: pyodide.globals.get("save_write"),
    };
    window.PYISO = isoGlueHandles();     // expose ISO adapters for iso.js

    setBoot("Loading name tables…", 94);
    NAMES = await loadNames();

    setBoot("Ready.", 100);
    $("boot").style.display = "none";
    $("opencard").style.display = "";
    wireSaveInputs();
    if (typeof window.isoReady === "function") window.isoReady();   // let iso.js finish setup
    restoreLastOpened();
    pickSharedFile();
    beforeUnloadGuard();
  }catch(e){
    setBoot("Failed to load: " + (e && e.message ? e.message : e), 100);
    if($("bootfill")) $("bootfill").style.background = "var(--bad)";
    toast("Could not start the editor — check your connection and reload.", "bad");
    console.error(e);
  }
}
function isoGlueHandles(){
  const g = (n) => pyodide.globals.get(n);
  return {
    load:g("iso_load"), verify:g("iso_verify"), maps:g("iso_maps"), reference:g("iso_reference"),
    chars:g("iso_chars"), char:g("iso_char"), setchar:g("iso_setchar"),
    spellnames:g("iso_spellnames"), spell:g("iso_spell"), setspell:g("iso_setspell"),
    runes:g("iso_runes"), rune:g("iso_rune"), setrune:g("iso_setrune"),
    gear:g("iso_gear"), gearitem:g("iso_gearitem"), setgear:g("iso_setgear"),
    enemies:g("iso_enemies"), enemy:g("iso_enemy"), setenemy:g("iso_setenemy"),
    prices:g("iso_prices"), setprice:g("iso_setprice"),
    runeprices:g("iso_runeprices"), setruneprice:g("iso_setruneprice"),
    healprices:g("iso_healprices"), sethealprice:g("iso_sethealprice"),
    mp:g("iso_mp"), setmp:g("iso_setmp"), skillfx:g("iso_skillfx"), setskillfx:g("iso_setskillfx"),
    unites:g("iso_unites"), setunite:g("iso_setunite"),
    names:g("iso_names"), setname:g("iso_setname"),
    hardmode:g("iso_hardmode"), hmrestore:g("iso_hmrestore"),
    exportmod:g("iso_exportmod"), importmod:g("iso_importmod"), modstatus:g("iso_modstatus"),
  };
}

async function loadNames(){
  const [chars, armor, runes] = await Promise.all([
    fetch(`${EDITOR_DIR}/s5_characters.json`).then(r => r.json()),
    fetch(`${EDITOR_DIR}/s5_armor_names.json`).then(r => r.json()),
    fetch(`${EDITOR_DIR}/s5_rune_ids.json`).then(r => r.json()),
  ]);
  const cnames = {}; for(const c of chars) cnames[c.id] = c.name;
  const armorNames = { helm: armor.head||{}, body: armor.body||{},
                       glove: armor.glove||{}, foot: armor.foot||{} };
  const runeNames = {}; runes.forEach((n, i) => { runeNames[String(i)] = n; });
  return { cnames, armorNames, runeNames, skillNames: SKILL_NAMES, rankNames: RANK_NAMES };
}

/* ---------------- Python glue (save + iso adapters) ---------------- */
const GLUE = `
import sys, json
sys.path.insert(0, "/editor")
import s5fields as F
import s5patch as P
import s5save as SV
SV.BACKUPS = False; P.BACKUPS = False   # no real files in the browser FS

# ===== Save editor =====
def _individual(path):
    head = open(path, "rb").read(20)
    return head[:4] == b"CFU\\x00" or head[:17] == b"\\x0d\\x00\\x00\\x00SharkPortSave"
def open_save(path):
    try:
        if _individual(path):
            s = SV.read_individual_save(path)
            if not s: return json.dumps({"error": "no Suikoden V save found in that file"})
            s["individual"] = True; s["editable"] = True; s.setdefault("meta", {"title": ""})
            saves = [s]
        else:
            saves = SV.read_all_saves(path)
        if not saves: return json.dumps({"error": "no Suikoden V save found in that file"})
        return json.dumps({"saves": saves})
    except Exception as e:
        return json.dumps({"error": "could not read save: " + str(e)})
def save_chars(path, folder):
    try:
        gd = SV.read_gamedata_payload(path, folder or None)
        if not gd: return json.dumps({"error": "could not read gamedata payload"})
        return json.dumps({"chars": SV.read_all_characters(gd)})
    except Exception as e: return json.dumps({"error": str(e)})
def save_write(path, folder, edits_json):
    try:
        edits = json.loads(edits_json)
        r = SV.write_individual_save(path, edits) if _individual(path) else SV.write_save_fields(path, folder, edits)
        return json.dumps(r)
    except Exception as e: return json.dumps({"error": str(e)})

# ===== ISO editor (operates on a truncated front-slice at /iso.bin) =====
ISO = "/iso.bin"
def iso_load():
    try:
        with P.Iso(ISO) as g: reg = P.region_of(g)
        if not reg: return json.dumps({"error": "not a recognized Suikoden V ISO"})
        F.set_region(reg)
        try: P.clear_mod(ISO)
        except Exception: pass
        return json.dumps({"region": reg, "regionName": F.REGION_NAMES.get(reg, ""),
                           "gated": F.GATED_IN_PAL if reg == "pal" else []})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_verify():
    try:
        with P.Iso(ISO) as g: reg = P.region_of(g)
        return json.dumps({"ok": reg is not None, "region": reg, "regionName": F.REGION_NAMES.get(reg, "")})
    except Exception as e: return json.dumps({"error": str(e)})

def _setter(fn):
    def run(id_json, edits_json):
        try:
            ident = json.loads(id_json); edits = json.loads(edits_json)
            with P.Iso(ISO, writable=True) as g:
                for e in edits: fn(g, ident, e)
            return json.dumps({"ok": True})
        except Exception as e: return json.dumps({"error": str(e)})
    return run

def iso_maps():
    try: items = F.res_json("s5_item_names.json")
    except Exception: items = {}
    try:
        _rn = F.res_json("s5_rune_names.json")
        runes = {str(i): (e.get("name") if isinstance(e, dict) else e) for i, e in enumerate(_rn)}
    except Exception: runes = {}
    try:
        _ar = F.res_json("s5_armor_names.json")
        armor = {slot: _ar.get(slot, {}) for slot in ("head","body","glove","foot")}
    except Exception: armor = {"head":{},"body":{},"glove":{},"foot":{}}
    heldfile = "s5_held_items_pal.json" if F.REGION == "pal" else "s5_held_items.json"
    try: held = F.res_json(heldfile).get("map", {})
    except Exception: held = {}
    return json.dumps({"items": items, "runes": runes, "armor": armor, "held": held,
        "ranks": F.RANK_NAMES, "grades": F.AFFINITY_GRADES, "egrades": F.ENEMY_AFFINITY_GRADES,
        "spellstatus": {str(k): v for k, v in F.SPELL_STATUS_NAMES.items()},
        "dropitems": {str((int(k.split(":")[1]) << 8) | int(k.split(":")[0])):
                      F.DROP_TABLE['categories'].get(k.split(':')[0], 'Cat '+k.split(':')[0]) + " · " + v
                      for k, v in sorted(F.DROP_TABLE["items"].items(),
                                         key=lambda kv: (int(kv[0].split(":")[0]), int(kv[0].split(":")[1])))},
        "help": F.SECTION_HELP, "globalHelp": F.GLOBAL_HELP,
        "elements": {str(k): v for k, v in F.ELEMENT_NAMES.items()},
        "targets": {str(k): v for k, v in F.TARGET_NAMES.items()}})

def iso_reference():
    out = {}
    try:
        en = F.res_json("s5_ref_english.json")
        for cat, names in en.items(): out[cat] = [{"i": i, "name": n} for i, n in enumerate(names)]
    except Exception: pass
    return json.dumps(out)

def iso_chars():
    chars = F.load_characters()
    try:
        base, stride, _ = F.TABLES["stats"]
        with P.Iso(ISO) as g:
            for c in chars: c["hasStats"] = any(g.rd(base + c["id"]*stride, stride))
    except Exception: pass
    return json.dumps({"chars": chars})
def iso_char(cid):
    try:
        with P.Iso(ISO) as g: tbls = P.read_character(g, int(cid))
        return json.dumps({"tables": tbls})
    except Exception as e: return json.dumps({"error": str(e)})
iso_setchar = _setter(lambda g, ident, e: P.write_field(g, e["table"], int(ident["id"]), e["field"], int(e["value"])))

def iso_spellnames():
    try: return json.dumps({"spells": F.res_json("s5_spell_names.json")})
    except Exception: return json.dumps({"spells": []})
def iso_spell(sid):
    try:
        with P.Iso(ISO) as g: return json.dumps({"fields": P.read_spell(g, int(sid))})
    except Exception as e: return json.dumps({"error": str(e)})
iso_setspell = _setter(lambda g, ident, e: P.write_spell_field(g, int(ident["id"]), e["field"], int(e["value"])))

def iso_runes():
    try:
        with P.Iso(ISO) as g: return json.dumps({"runes": P.read_runes(g)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_rune(rid):
    try:
        rid = int(rid)
        try: spn = F.res_json("s5_spell_names.json")
        except Exception: spn = []
        synth = rid >= F.SYNTH_RUNE_BASE
        with P.Iso(ISO) as g:
            if synth:
                sr = F.SYNTH_RUNES[rid - F.SYNTH_RUNE_BASE]
                name, start, cnt, grant = sr["name"], sr["start"], sr["count"], []
            else:
                grant = P.read_rune(g, rid); start, cnt = grant[0]["value"], grant[1]["value"]
                name = F.RUNE_GRANT_NAMES[rid] if rid < len(F.RUNE_GRANT_NAMES) else "Rune %d" % rid
            spells = []
            for k in range(cnt):
                sid = start + k
                if 0 <= sid < F.SPELL_COUNT:
                    spells.append({"id": sid, "level": k+1,
                        "name": spn[sid] if sid < len(spn) and spn[sid] else "Spell %d" % sid,
                        "fields": P.read_spell(g, sid)})
        return json.dumps({"name": name, "synthetic": synth, "grant": grant, "spells": spells})
    except Exception as e: return json.dumps({"error": str(e)})
iso_setrune = _setter(lambda g, ident, e: P.write_rune_field(g, int(ident["id"]), e["field"], int(e["value"])))

def iso_gear(slot):
    try:
        with P.Iso(ISO) as g: return json.dumps({"slot": slot, "items": P.list_armor(g, slot)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_gearitem(slot, i):
    try:
        with P.Iso(ISO) as g: return json.dumps(P.read_armor_item(g, slot, int(i)))
    except Exception as e: return json.dumps({"error": str(e)})
iso_setgear = _setter(lambda g, ident, e: P.write_armor_field(g, ident["slot"], int(ident["id"]), e["field"], int(e["value"])))

def iso_enemies():
    try:
        try: names = F.res_json("s5_enemy_names.json")
        except Exception: names = {}
        with P.Iso(ISO) as g: return json.dumps({"enemies": P.read_enemies(g, names)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_enemy(eid):
    try:
        with P.Iso(ISO) as g: return json.dumps({"fields": P.read_enemy(g, int(eid))})
    except Exception as e: return json.dumps({"error": str(e)})
iso_setenemy = _setter(lambda g, ident, e: P.write_enemy_field(g, int(ident["id"]), e["field"], int(e["value"])))

def iso_prices():
    try:
        with P.Iso(ISO) as g: return json.dumps({"prices": P.read_prices(g)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setprice(index, field, value):
    try:
        with P.Iso(ISO, writable=True) as g: P.write_price(g, int(index), field, int(value))
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_runeprices():
    try:
        with P.Iso(ISO) as g: return json.dumps({"prices": P.read_rune_prices(g)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setruneprice(index, field, value):
    try:
        with P.Iso(ISO, writable=True) as g: P.write_rune_price(g, int(index), field, int(value))
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_healprices():
    try:
        with P.Iso(ISO) as g: return json.dumps({"prices": P.read_heal_prices(g)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_sethealprice(index, field, value):
    try:
        with P.Iso(ISO, writable=True) as g: P.write_heal_price(g, int(index), field, int(value))
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})

def iso_mp():
    try:
        with P.Iso(ISO) as g: return json.dumps({"groups": P.read_mp(g), "fields": F.MP_FIELD_LABELS})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setmp(group, idx, value):
    try:
        with P.Iso(ISO, writable=True) as g: P.write_mp(g, int(group), int(idx), int(value))
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_skillfx():
    try:
        with P.Iso(ISO) as g: return json.dumps({"skills": P.read_skillfx(g), "ranks": F.SKILLFX_RANKS})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setskillfx(sid, rank, value):
    try:
        with P.Iso(ISO, writable=True) as g: P.write_skillfx(g, int(sid), int(rank), int(value))
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_unites():
    try:
        cn = {c["id"]: c["name"] for c in F.load_characters()}
        with P.Iso(ISO) as g: return json.dumps({"unites": P.read_unites(g, cn)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setunite(uid, slot, cid):
    try:
        with P.Iso(ISO, writable=True) as g: P.write_unite_member(g, int(uid), int(slot), int(cid))
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})

def iso_names(limit):
    try:
        with P.Iso(ISO) as g: return json.dumps({"names": P.read_names(g, int(limit or 0))})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setname(index, name):
    try:
        with P.Iso(ISO, writable=True) as g: P.set_name(g, int(index), str(name))
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})

def iso_hardmode(factor):
    try: return json.dumps({"ok": True, "count": P.hardmode_apply(ISO, float(factor))})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_hmrestore():
    try: return json.dumps({"ok": True, "count": P.hardmode_restore(ISO)})
    except Exception as e: return json.dumps({"error": str(e)})

def iso_exportmod(note):
    try: return json.dumps({"ok": True, "mod": P.export_mod(ISO, note or "")})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_importmod(mod_json):
    try: return json.dumps(P.apply_mod(ISO, json.loads(mod_json), make_backup=False))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_modstatus():
    try: return json.dumps(P.mod_status(ISO))
    except Exception as e: return json.dumps({"error": str(e)})
`;

/* ================= Save editor: file open ================= */
function wireSaveInputs(){
  const drop = $("drop"), file = $("file"), pick = $("pickBtn");
  pick.onclick = () => {
    if (HAS_FS_ACCESS) return pickViaFS();     // keeps a writable handle for save-in-place
    file.click();
  };
  file.onchange = () => { if(file.files[0]) openFile(file.files[0]); };
  ["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add("hot"); }));
  ["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove("hot"); }));
  drop.addEventListener("drop", e => {
    const f = e.dataTransfer && e.dataTransfer.files[0]; if(f){ saveHandle = null; openFile(f); } });
}
async function pickViaFS(){
  try{
    const [h] = await window.showOpenFilePicker({ types: [{ description: "PS2 save",
      accept: { "application/octet-stream": [".ps2",".mc2",".mcd",".bin",".psu",".psv",".cbs",".xps",".sps",".max"] } }] });
    saveHandle = h; const f = await h.getFile(); openFile(f, h);
  }catch(e){ if(e && e.name !== "AbortError") toast("Could not open file: " + e.message, "bad"); }
}

async function openFile(f, handle){
  if(!f) return;
  curFileName = f.name; saveHandle = handle || (handle === undefined ? saveHandle : null);
  $("filename").textContent = `${f.name} · ${(f.size/1048576).toFixed(2)} MB`;
  spin(true);
  try{
    const buf = new Uint8Array(await f.arrayBuffer());
    pyodide.FS.writeFile(SAVE_PATH, buf);
    sEdits = {}; CHARDATA = {};
    const res = JSON.parse(PY.open(SAVE_PATH));
    if(res.error){ $("saves").innerHTML = `<p class="bad" style="padding:8px">${esc(res.error)}</p>`;
      toast(res.error, "bad"); return; }
    saves = res.saves; saves.forEach(s => s.card = curFileName);
    renderSaves();
    await rememberSave(buf, f.name, saveHandle);
    toast(`Opened ${saves.length} save(s)`, "ok");
  }catch(e){ toast("Could not read that file: " + (e.message||e), "bad"); console.error(e); }
  finally{ spin(false); }
}

/* ---------- last-opened (IndexedDB) ---------- */
async function rememberSave(bytes, name, handle){
  await idbSet("save:last", { name, bytes, handle: handle || null, when: Date.now() });
  renderSaveLast({ name });
}
async function restoreLastOpened(){
  const s = await idbGet("save:last");
  if(s) renderSaveLast(s);
}
function renderSaveLast(s){
  const row = $("saveLast"); if(!row) return;
  row.classList.remove("hidden");
  row.innerHTML = `<button class="ghost mini" id="saveLastBtn">↻ Last opened: ${esc(s.name)}</button>
                   <button class="ghost mini" id="saveLastX" title="Forget">✕</button>`;
  $("saveLastBtn").onclick = reopenLastSave;
  $("saveLastX").onclick = async () => { await idbDel("save:last"); row.classList.add("hidden"); };
}
/* A save shared into the installed PWA (Android Web Share target). */
async function pickSharedFile(){
  if(!/[?&]shared=1/.test(location.search)) return;
  try{
    const res = await caches.match("shared-file");
    if(!res) return;
    const name = res.headers.get("X-Filename") || "shared.ps2";
    const buf = await res.arrayBuffer();
    saveHandle = null;
    openFile(new File([buf], name), null);
    try{ const c = await caches.open("s5share"); await c.delete("shared-file"); }catch(_){}
    history.replaceState(null, "", location.pathname);
  }catch(_){}
}
async function reopenLastSave(){
  const s = await idbGet("save:last"); if(!s) return;
  if(s.handle && s.handle.queryPermission){
    try{
      let p = await s.handle.queryPermission({ mode: "readwrite" });
      if(p !== "granted") p = await s.handle.requestPermission({ mode: "readwrite" });
      if(p === "granted"){ const f = await s.handle.getFile(); return openFile(f, s.handle); }
    }catch(_){}
  }
  if(s.bytes){ saveHandle = null; openFile(new File([s.bytes], s.name), null); }
}

/* ================= Save editor: render ================= */
function renderSaves(){
  const d = $("saves");
  d.innerHTML = saves.map((sv, i) => {
    const fl = sv.fields || {};
    const badge = sv.region
      ? `<span class="badge ${sv.region=='PAL'?'b-pal':sv.region=='NTSC-U'?'b-ntsc':'b-jp'}">${esc(sv.region)}</span>` : "";
    const ro = sv.editable === false;
    const foot = ro
      ? `<span class="note">Read-only format. Export to .xps or a memory card to edit.</span>`
      : `<button class="ghost" onclick="openChars(${i})">Characters (equipment &amp; runes)…</button>
         <button class="ghost" onclick="openRecruit(${i})">Recruitment…</button>
         <span class="note">Change fields freely — nothing is written to your file until you press Save.</span>`;
    return `<div class="sec">
      <div class="card-hd">${esc(sv.folder)} ${badge}
        <span class="note">· ${esc(sv.card)} · ${esc((sv.meta&&sv.meta.title)||"")}</span></div>
      <div class="grid">
        <div class="fld"><label>Hero name</label><div class="in">
          <input id="sv${i}_heroName" value="${esc(fl.heroName)}" maxlength="15" ${ro?"disabled":""}
            data-sk="sv${i}:heroName" data-orig="${esc(fl.heroName)}" data-lbl="Hero name" data-grp="${esc(sv.folder)}">${ro?"":REVERT_BTN_SAVE}</div></div>
        <div class="fld"><label>Castle name</label><div class="in">
          <input id="sv${i}_castleName" value="${esc(fl.castleName)}" maxlength="15" ${ro?"disabled":""}
            data-sk="sv${i}:castleName" data-orig="${esc(fl.castleName)}" data-lbl="Castle name" data-grp="${esc(sv.folder)}">${ro?"":REVERT_BTN_SAVE}</div></div>
        <div class="fld"><label>Army name</label><div class="in">
          <input id="sv${i}_armyName" value="${esc(fl.armyName)}" maxlength="15" ${ro?"disabled":""}
            data-sk="sv${i}:armyName" data-orig="${esc(fl.armyName)}" data-lbl="Army name" data-grp="${esc(sv.folder)}">${ro?"":REVERT_BTN_SAVE}</div></div>
        <div class="fld"><label>Potch</label><div class="in">
          <input type="number" id="sv${i}_potch" value="${fl.potch||0}" min="0" max="99999999" ${ro?"disabled":""}
            data-sk="sv${i}:potch" data-orig="${fl.potch||0}" data-lbl="Potch" data-grp="${esc(sv.folder)}">${ro?"":REVERT_BTN_SAVE}</div></div>
        <div class="fld"><label>Party SP</label><div class="in">
          <input type="number" id="sv${i}_psp" value="${fl.partySP||0}" min="0" max="999999" ${ro?"disabled":""}
            data-sk="sv${i}:partySP" data-orig="${fl.partySP||0}" data-lbl="Party SP" data-grp="${esc(sv.folder)}">${ro?"":REVERT_BTN_SAVE}</div></div>
        <div class="fld"><label>Level <span class="note">(display only)</span></label><div class="in">
          <input type="number" value="${fl.level||0}" disabled title="Save-select display level. Edit unit levels in the Characters panel."></div></div>
        <div class="fld"><label>Playtime <span class="note">(display only)</span></label><div class="in">
          <input value="${esc(fl.playtime)}" disabled></div></div>
        <div class="fld"><label>New Game Plus</label><div class="in">
          <label class="chk"><input type="checkbox" id="sv${i}_ngp" ${fl.newGamePlus?"checked":""} ${ro?"disabled":""}
            data-sk="sv${i}:newGamePlus" data-orig="${fl.newGamePlus?1:0}" data-lbl="New Game Plus" data-grp="${esc(sv.folder)}"></label>${ro?"":REVERT_BTN_SAVE}</div></div>
      </div>
      ${ro?"":`<div class="subhd">Active party <span class="note">— 6 battle + 4 support slots; slot 1 is the hero</span></div>
      <div class="grid" style="padding-top:8px">${(fl.party||[]).map((pid,k)=>{
        const lbl = k===0?"Slot 1 · Hero":(k<6?`Slot ${k+1} · battle`:`Slot ${k+1} · support`);
        return `<div class="fld"><label>${lbl}</label><div class="in">${partySelHTML(i,k,pid,k===0)}${k===0?"":REVERT_BTN_SAVE}</div></div>`;
      }).join("")}</div>`}
      <div class="card-ft">${foot}</div>
      <div id="chars${i}" class="note" style="margin:0 14px 8px"></div>
      <div id="recruit${i}" style="margin:0 14px 10px"></div>
    </div>`;
  }).join("");
  wireSaveHeaderInputs();
  updateSaveToolbar();
}

/* Party slot <select>: 0x0100 = empty; slot 0 (hero) locked. */
const PARTY_EMPTY = 256;
function partySelHTML(i, slot, val, locked){
  let h = `<select id="sv${i}_party${slot}" ${locked?"disabled":""}
    data-sk="sv${i}:party${slot}" data-orig="${val}" data-lbl="Party slot ${slot+1}" data-grp="Party">`;
  h += `<option value="${PARTY_EMPTY}" ${val===PARTY_EMPTY?"selected":""}>— empty —</option>`;
  for(const id of Object.keys(NAMES.cnames))
    h += `<option value="${id}" ${String(val)===String(id)?"selected":""}>${id}: ${esc(NAMES.cnames[id])}</option>`;
  return h + `</select>`;
}
function partyDisplay(v){ return +v === PARTY_EMPTY ? "empty" : (NAMES.cnames[v] || ("#"+v)); }

/* Header field write-through (hero/castle/army/potch/SP/NG+/party). */
function wireSaveHeaderInputs(){
  saves.forEach((sv, i) => {
    if (sv.editable === false) return;
    ["heroName","castleName","armyName"].forEach(k => {
      const el = $(`sv${i}_${k}`); if(el) el.onchange = () => stageSaveField(i, k, el.value, el);
    });
    [["potch","potch"],["psp","partySP"]].forEach(([idk,k]) => {
      const el = $(`sv${i}_${idk}`); if(el) el.onchange = () => stageSaveField(i, k, +el.value, el);
    });
    for(let k2=0;k2<10;k2++){
      const el = $(`sv${i}_party${k2}`);
      if(el && !el.disabled) el.onchange = () => stageSaveField(i, `party${k2}`, +el.value, el);
    }
    const ngp = $(`sv${i}_ngp`);
    if(ngp) ngp.onchange = () => stageSaveField(i, "newGamePlus", ngp.checked?1:0, ngp);
  });
}
const nameOf = (idx) => NAMES.cnames[idx] || ("Character " + idx);

/* Write one save field through to /save.bin and track it for review/badge. */
async function stageSaveField(i, key, value, el){
  const sv = saves[i];
  const orig = el.dataset.orig;
  const changed = String(value) !== String(orig);
  const sk = el.dataset.sk;
  spin(true);
  try{
    const r = JSON.parse(PY.write(SAVE_PATH, sv.folder || "", JSON.stringify({ [key]: value })));
    if(r.error){ toast("Error: " + r.error, "bad"); return; }
    if(changed){
      const disp = key === "newGamePlus" ? (value ? "On" : "Off")
        : key.startsWith("party") ? partyDisplay(value) : String(value);
      sEdits[sk] = { label: el.dataset.lbl, group: el.dataset.grp, to: disp };
      el.classList.add("dirty");
    }else{ delete sEdits[sk]; el.classList.remove("dirty"); }
    // refresh decoded header so re-open baselines stay accurate
    const re = JSON.parse(PY.open(SAVE_PATH));
    if(!re.error){ saves = re.saves; saves.forEach(s => s.card = curFileName); }
    updateSaveToolbar();
  }catch(e){ toast("Write failed: " + (e.message||e), "bad"); console.error(e); }
  finally{ spin(false); }
}

/* ---------- character editor ---------- */
async function fetchChars(sv){ return JSON.parse(PY.chars(SAVE_PATH, sv.folder || "")); }
async function openChars(i){
  const sv = saves[i], box = $(`chars${i}`);
  if(box._loading) return;
  if(box._open){ box._open = false; box.innerHTML = ""; return; }
  box._loading = true; box.innerHTML = "loading characters…";
  const r = await fetchChars(sv); box._loading = false;
  if(r.error){ box.innerHTML = `<span class="bad">${esc(r.error)}</span>`; return; }
  box._open = true; CHARDATA[i] = r;
  const opts = r.chars.map(c => `<option value="${c.idx}">${c.idx}: ${esc(nameOf(c.idx))}${
    c.recruited||!c.recruitable ? "" : " (not recruited)"}</option>`).join("");
  box.innerHTML = `<div class="row"><span class="note">Character</span>
    <select id="csel${i}" onchange="renderChar(${i})">${opts}</select>
    <button class="ghost" onclick="recruitAll(${i})" title="Set the recruited flag for every recruitable character (108 Stars)">Recruit ALL</button>
    </div><div id="cfld${i}"></div>`;
  renderChar(i);
}

/* picker-button field for id-based selects (armor / rune / skill-slot). */
function pickBtn(sk, cf, curId, curName, onPick){
  return `<button type="button" class="pickbtn" data-sk="${sk}" data-cf="${cf}" data-orig="${esc(curId)}" onclick='${onPick}'>
    <span class="pickbtn-name">${esc(curName)}</span><span class="pickbtn-id note">#${esc(curId)}</span></button>${REVERT_BTN_SAVE}`;
}
function armorList(slot){
  const m = NAMES.armorNames[slot] || {};
  const list = Object.keys(m).map(k => ({ id:k, name:m[k] }));
  list.unshift({ id:"0", name:"— Nothing —" });
  return list;
}
function runeList(){
  const m = NAMES.runeNames;
  return Object.keys(m).map(k => ({ id:k, name:m[k] }));
}
function skillSlotList(){
  const l = [{ id:"0", name:"— empty —" }];
  SKILL_NAMES.forEach((n,s) => { if(s!==26) l.push({ id:String(s+1), name:n }); });
  return l;
}

function renderChar(i){
  const r = CHARDATA[i], idx = +$(`csel${i}`).value;
  const c = r.chars.find(x => x.idx === idx);
  const RK = NAMES.rankNames;
  const armorName = (slot,val) => (NAMES.armorNames[slot]||{})[String(val)] || (String(val)==="0"?"— Nothing —":"#"+val);
  const runeName  = (val) => NAMES.runeNames[String(val)] || (String(val)==="0"?"— Nothing —":"#"+val);
  const slotName  = (val) => { if(!val) return "— empty —"; const n=SKILL_NAMES[val-1]; return n||("#"+val); };
  const sub = (t) => `<div class="subhd">${t}</div>`;
  const g = (inner) => `<div class="grid" style="padding-top:8px">${inner}</div>`;
  const rankSel = (id,val) => selHTML(id, RK, val);
  const aBtn = (slot,lbl,val) => `<div class="fld"><label>${lbl}</label><div class="in">${
    pickBtn(`c${i}_${slot}`, `${i}:${slot}`, val, armorName(slot,val),
      `pickArmor(${i},'${slot}',${val})`)}</div></div>`;
  const rBtn = (slot,lbl,val) => `<div class="fld"><label>${lbl}</label><div class="in">${
    pickBtn(`c${i}_${slot}`, `${i}:${slot}`, val, runeName(val),
      `pickRune(${i},'${slot}',${val})`)}</div></div>`;
  $(`cfld${i}`).innerHTML =
    sub("Level & recruitment")
    + `<div class="grid" style="grid-template-columns:150px 260px;padding-top:8px">`
    + `<div class="fld"><label>Level</label><div class="in"><input type="number" id="ce${i}_level" value="${c.level}" min="1" max="99" data-cf="${i}:level" data-orig="${c.level}" onchange="writeCharField(${i},'level',this.value,this)">${REVERT_BTN_SAVE}</div></div>`
    + `<div class="fld"><label>Recruited</label><div class="in"><label class="chk"><input type="checkbox" id="ce${i}_rec" ${c.recruited?"checked":""} ${c.recruitable?"":'disabled title="Not recruitable"'} data-cf="${i}:rec" data-orig="${c.recruited?1:0}" onchange="writeCharField(${i},'rec',this.checked?1:0,this)"> <span class="note">${c.recruitable?"in the roster":"not recruitable"}</span></label>${c.recruitable?REVERT_BTN_SAVE:""}</div></div>`
    + `</div>`
    + sub("Equipment")
    + g(aBtn("helm","Helm",c.armor.helm)+aBtn("body","Armor",c.armor.body)+aBtn("glove","Gloves",c.armor.glove)+aBtn("foot","Boots",c.armor.foot))
    + sub("Runes")
    + g(rBtn("rhead","Head",c.runes.rhead)+rBtn("rright","Right hand",c.runes.rright)+rBtn("rleft","Left hand",c.runes.rleft))
    + sub("Equipped skill slots")
    + g([0,1].map(k => `<div class="fld"><label>Slot ${k+1}</label><div class="in">${
        pickBtn(`c${i}_ss${k}`, `${i}:ss${k}`, (c.slots||[0,0])[k], slotName((c.slots||[0,0])[k]),
          `pickSlot(${i},${k},${(c.slots||[0,0])[k]})`)}</div></div>`).join(""))
    + sub("Skill ranks")
    + g((c.skills||[]).map((v,s)=> s===26 ? "" :
        `<div class="fld"><label>${esc(SKILL_NAMES[s]||("Skill "+s))}</label><div class="in">${
          selHTML(`ce${i}_sk${s}`, RK, v, `writeCharField(${i},'sk${s}',this.value,this)`, `data-cf="${i}:sk${s}" data-orig="${v}"`)}${REVERT_BTN_SAVE}</div></div>`).join(""))
    + `<div style="padding:8px 14px 12px"><span class="note">Reverse-engineered &amp; cross-verified. Rune 0 = empty slot.${
        c.recruited||!c.recruitable ? "" : " · <b>Not yet recruited in this save.</b>"}</span></div>`;
}
function selHTML(id, names, val, onchange, attrs){
  let h = `<select id="${id}" ${onchange?`onchange="${onchange}"`:""} ${attrs||""}>`;
  if(!(String(val) in names)) h += `<option value="${val}" selected>#${val} (unknown)</option>`;
  for(const k of Object.keys(names)) h += `<option value="${k}" ${String(val)===k?"selected":""}>${k}: ${esc(names[k])}</option>`;
  return h + "</select>";
}

/* picker openers for character equipment/runes/skills */
function pickArmor(i, slot, cur){
  openPicker("Choose "+slot, armorList(slot), cur, (id) => setCharPick(i, slot, id, (NAMES.armorNames[slot]||{})[id]||(id==="0"?"— Nothing —":"#"+id)), { hideId:true });
}
function pickRune(i, slot, cur){
  openPicker("Choose rune", runeList(), cur, (id) => setCharPick(i, slot, id, NAMES.runeNames[id]||"#"+id), {});
}
function pickSlot(i, k, cur){
  openPicker("Choose skill", skillSlotList(), cur, (id) => setCharPick(i, "ss"+k, id, (id==="0"?"— empty —":SKILL_NAMES[id-1]||"#"+id)), {});
}
async function setCharPick(i, field, id, displayName){
  const btn = q(`.pickbtn[data-sk="c${i}_${field}"]`);
  if(btn){ q(".pickbtn-name", btn).textContent = displayName; q(".pickbtn-id", btn).textContent = "#"+id; }
  await writeCharField(i, field, id, btn);
}

async function writeCharField(i, field, value, el){
  const sv = saves[i], idx = +$(`csel${i}`).value;
  const key = `c${idx}_${field}`;
  spin(true);
  try{
    const r = JSON.parse(PY.write(SAVE_PATH, sv.folder || "", JSON.stringify({ [key]: +value })));
    if(r.error){ toast("Error: " + r.error, "bad"); return; }
    // keep the cache fresh so re-opening the character shows current values
    const rr = await fetchChars(sv); if(!rr.error) CHARDATA[i] = rr;
    // dirty vs the value this field had when the panel was rendered (data-orig)
    const sk = `c${idx}:${field}`;
    const orig = el && el.dataset ? el.dataset.orig : undefined;
    const changed = orig != null && String(value) !== String(orig);
    if(changed){
      sEdits[sk] = { label: `${nameOf(idx)} · ${charFieldLabel(field)}`, group: sv.folder, to: charFieldDisplay(field, +value) };
      if(el && el.classList) el.classList.add("dirty");
    }else{ delete sEdits[sk]; if(el && el.classList) el.classList.remove("dirty"); }
    updateSaveToolbar();
  }catch(e){ toast("Write failed: " + (e.message||e), "bad"); console.error(e); }
  finally{ spin(false); }
}

/* Per-field undo for the Save editor: revert a control to its rendered value. */
function revertSaveField(btn){
  let el = btn.previousElementSibling;
  if(el && el.tagName === "LABEL") el = el.querySelector("input");   // NG+/recruited checkbox
  if(!el || el.dataset.orig == null) return;
  const orig = el.dataset.orig;
  if(el.dataset.sk && el.dataset.sk.indexOf("sv") === 0){            // header field
    const [svk, key] = el.dataset.sk.split(":");
    const i = +svk.slice(2);
    if(el.type === "checkbox"){ el.checked = (orig === "1" || orig === 1); stageSaveField(i, key, el.checked?1:0, el); }
    else { el.value = orig; stageSaveField(i, key, el.value, el); }
    return;
  }
  if(el.dataset.cf){                                                 // character field
    const [i, field] = el.dataset.cf.split(":");
    if(el.classList.contains("pickbtn")){
      q(".pickbtn-name", el).textContent = charFieldDisplay(field, +orig);
      q(".pickbtn-id", el).textContent = "#" + orig;
      writeCharField(+i, field, +orig, el);
    }else if(el.type === "checkbox"){
      el.checked = (orig === "1" || orig === 1); writeCharField(+i, field, el.checked?1:0, el);
    }else{
      el.value = orig; writeCharField(+i, field, +orig, el);
    }
  }
}
function charFieldLabel(field){
  if(field === "level") return "Level"; if(field === "rec") return "Recruited";
  if(field.startsWith("sk")) return (SKILL_NAMES[+field.slice(2)]||field)+" rank";
  if(field.startsWith("ss")) return "Skill slot "+(+field.slice(2)+1);
  return { helm:"Helm", body:"Armor", glove:"Gloves", foot:"Boots",
           rhead:"Rune (head)", rright:"Rune (right)", rleft:"Rune (left)" }[field] || field;
}
function charFieldDisplay(field, v){
  if(field === "rec") return v ? "Recruited" : "Not recruited";
  if(field.startsWith("sk")) return RANK_NAMES[v] || String(v);
  if(field.startsWith("ss")) return v ? (SKILL_NAMES[v-1]||"#"+v) : "empty";
  if(["helm","body","glove","foot"].includes(field)) return (NAMES.armorNames[field]||{})[String(v)] || (String(v)==="0"?"Nothing":"#"+v);
  if(["rhead","rright","rleft"].includes(field)) return NAMES.runeNames[String(v)] || (String(v)==="0"?"Nothing":"#"+v);
  return String(v);
}

/* ---------- recruitment roster ---------- */
async function openRecruit(i){
  const sv = saves[i], box = $(`recruit${i}`);
  if(box._loading) return;
  if(box._open){ box._open = false; box.innerHTML = ""; return; }
  box._loading = true; box.innerHTML = `<span class="note">loading roster…</span>`;
  const r = await fetchChars(sv); box._loading = false;
  if(r.error){ box.innerHTML = `<span class="bad">${esc(r.error)}</span>`; return; }
  box._open = true; CHARDATA[i] = r;
  box.innerHTML = `<div class="sec"><h3>Recruitment — <span id="reccount${i}"></span></h3>
    <div class="row" style="padding:10px 14px 0">
      <input id="recfilter${i}" size="18" placeholder="search name…" oninput="recruitFilter(${i})">
      <button class="ghost mini" onclick="recruitCheckAll(${i},true)">Check all</button>
      <button class="ghost mini" onclick="recruitCheckAll(${i},false)">Uncheck all</button>
      <span class="note">greyed = not recruitable</span>
    </div>
    <div id="recgrid${i}" class="recgrid"></div></div>`;
  $(`recgrid${i}`).innerHTML = r.chars.map(c => {
    const dis = !c.recruitable;
    return `<label class="chk" data-name="${esc(nameOf(c.idx).toLowerCase())}" style="${dis?"opacity:.4":""}">
      <input type="checkbox" data-idx="${c.idx}" ${c.recruited?"checked":""} ${dis?"disabled":""} onchange="toggleRecruit(${i},this)">
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
  const query = $(`recfilter${i}`).value.trim().toLowerCase();
  document.querySelectorAll(`#recgrid${i} label`).forEach(l => {
    l.style.display = (!query || l.dataset.name.includes(query)) ? "" : "none"; });
}
async function toggleRecruit(i, box){
  const sv = saves[i], idx = +box.dataset.idx;
  await writeRecruitOne(sv, i, idx, box.checked);
  recruitCount(i);
}
async function recruitCheckAll(i, on){
  const sv = saves[i]; spin(true);
  try{
    const boxes = [...document.querySelectorAll(`#recgrid${i} input[type=checkbox]`)]
      .filter(b => !b.disabled && b.closest("label").style.display !== "none");
    for(const b of boxes){ b.checked = on; await writeRecruitOne(sv, i, +b.dataset.idx, on, true); }
    const rr = await fetchChars(sv); if(!rr.error) CHARDATA[i] = rr;
    updateSaveToolbar(); recruitCount(i);
  } finally { spin(false); }
}
async function recruitAll(i){
  const sv = saves[i], r = CHARDATA[i]; spin(true);
  try{
    let n=0;
    for(const c of r.chars){ if(c.recruitable && !c.recruited){ await writeRecruitOne(sv, i, c.idx, true, true); n++; } }
    const rr = await fetchChars(sv); if(!rr.error) CHARDATA[i] = rr;
    updateSaveToolbar();
    toast(n ? `Recruited ${n} character(s)` : "Everyone recruitable is already recruited", "ok");
  } finally { spin(false); }
}
async function writeRecruitOne(sv, i, idx, on, quiet){
  const r = JSON.parse(PY.write(SAVE_PATH, sv.folder || "", JSON.stringify({ [`c${idx}_rec`]: on?1:0 })));
  if(r.error){ if(!quiet) toast("Error: " + r.error, "bad"); return; }
  const sk = `c${idx}:rec`;
  const c0 = (CHARDATA[i].chars.find(x=>x.idx===idx)||{});
  if((!!c0.recruited) !== on) sEdits[sk] = { label: `${nameOf(idx)} · Recruited`, group: sv.folder, to: on?"Recruited":"Not recruited" };
  else delete sEdits[sk];
  if(!quiet){ const rr = await fetchChars(sv); if(!rr.error) CHARDATA[i] = rr; updateSaveToolbar(); }
}

/* ================= Save editor: sticky toolbar + Save ================= */
function updateSaveToolbar(){
  if(badgeRAF) return;
  badgeRAF = requestAnimationFrame(() => {
    badgeRAF = 0;
    let bar = $("saveToolbar");
    const n = Object.keys(sEdits).length;
    if(!bar){
      bar = document.createElement("div"); bar.id = "saveToolbar"; bar.className = "toolbar";
      bar.innerHTML = `<span id="saveUnsaved" class="badge-unsaved"></span><span class="sp"></span>
        <button id="saveDoBtn" class="primary">Save<span class="dot hidden"></span></button>`;
      const pane = q('.mode-pane[data-mode="save"]'); pane.appendChild(bar);
      $("saveDoBtn").onclick = doSave;
    }
    bar.classList.toggle("hidden", saves.length === 0);
    $("saveUnsaved").textContent = n ? `${n} unsaved change${n===1?"":"s"}` : "No changes";
    $("saveUnsaved").classList.toggle("on", n>0);
    const dot = q("#saveDoBtn .dot"); if(dot) dot.classList.toggle("hidden", n===0);
  });
}

function buildSaveReview(){
  const byGroup = {};
  Object.values(sEdits).forEach(e => { (byGroup[e.group] = byGroup[e.group] || []).push(e); });
  return Object.keys(byGroup).map(gt => ({ title: gt,
    rows: byGroup[gt].map(e => ({ label: e.label, from: "", to: e.to })) }));
}
function doSave(){
  const groups = buildSaveReview();
  const outName = editedName(curFileName);
  const canShare = canShareFiles([new File([new Uint8Array(1)], outName)]);
  const dest = saveHandle ? `Save to ${curFileName}` : (canShare ? "Share edited save…" : "Download edited save");
  confirmReview(curFileName, groups, dest, outputSave);
}
async function outputSave(){
  const bytes = pyodide.FS.readFile(SAVE_PATH);
  const outName = editedName(curFileName);
  // 0) if we have no writable handle but Web Share is available, share it (Android)
  if(!saveHandle){
    const file = new File([bytes], outName, { type: "application/octet-stream" });
    if(canShareFiles([file])){
      try{
        await navigator.share({ files: [file], title: outName });
        sEdits = {}; document.querySelectorAll(".dirty").forEach(e=>e.classList.remove("dirty")); updateSaveToolbar();
        toast("Shared the edited save.", "ok"); return;
      }catch(e){ if(e && e.name === "AbortError") return; /* else fall through to download */ }
    }
  }
  // 1) save in place if we hold a writable handle
  if(saveHandle){
    try{
      let p = await saveHandle.queryPermission({ mode: "readwrite" });
      if(p !== "granted") p = await saveHandle.requestPermission({ mode: "readwrite" });
      if(p === "granted"){
        const w = await saveHandle.createWritable(); await w.write(bytes); await w.close();
        sEdits = {}; document.querySelectorAll(".dirty").forEach(e=>e.classList.remove("dirty"));
        updateSaveToolbar();
        toast(`Saved to ${curFileName}.`, "ok");
        // refresh baseline
        const re = JSON.parse(PY.open(SAVE_PATH)); if(!re.error){ saves = re.saves; saves.forEach(s=>s.card=curFileName); }
        return;
      }
    }catch(e){ console.error(e); }
  }
  // 2) fall back to download (works everywhere)
  downloadBlob(bytes, outName);
  sEdits = {}; document.querySelectorAll(".dirty").forEach(e=>e.classList.remove("dirty"));
  updateSaveToolbar();
  toast("Downloaded — keep your original until it loads in-game.", "ok");
}

/* unsaved-changes guard for both editors */
function beforeUnloadGuard(){
  window.addEventListener("beforeunload", (e) => {
    const dirty = Object.keys(sEdits).length || (typeof window.isoHasUnsaved === "function" && window.isoHasUnsaved());
    if(dirty){ e.preventDefault(); e.returnValue = ""; }
  });
}

boot();
