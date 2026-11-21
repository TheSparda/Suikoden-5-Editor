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
let GUIDE = {};               // recruit guide by character id (s5_recruit_guide.json)
let saves = [];               // decoded saves for the current file
let CHARDATA = {};            // per-save cached { chars:[...] }
let ROSTER = {};              // per-save roster (recruited flags) for the party pickers
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
  "s5_characters.json","s5_armor_names.json","s5_rune_ids.json","s5_rune_save_ids.json","s5_rune_names.json",
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
    const modules = ["s5save.py","s5patch.py","s5fields.py","s5changes.py","s5toggles.py"];
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
      invnames: pyodide.globals.get("save_item_names"),
      audit: pyodide.globals.get("save_audit"),
    };
    window.PYISO = isoGlueHandles();     // expose ISO adapters for iso.js

    setBoot("Loading name tables…", 94);
    NAMES = await loadNames();
    try{ GUIDE = await (await fetch(`${EDITOR_DIR}/s5_recruit_guide.json`)).json(); }catch(_){ GUIDE = {}; }

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
    changes:g("iso_changes"), stageinfo:g("iso_stageinfo"),
    chars:g("iso_chars"), char:g("iso_char"), setchar:g("iso_setchar"),
    loadout:g("iso_loadout"), setloadout:g("iso_setloadout"),
    spellnames:g("iso_spellnames"), spell:g("iso_spell"), setspell:g("iso_setspell"),
    runes:g("iso_runes"), rune:g("iso_rune"), setrune:g("iso_setrune"),
    gear:g("iso_gear"), gearitem:g("iso_gearitem"), setgear:g("iso_setgear"),
    setgearname:g("iso_setgearname"),
    gearreqs:g("iso_gearreqs"), setgearreq:g("iso_setgearreq"),
    enemies:g("iso_enemies"), enemy:g("iso_enemy"), setenemy:g("iso_setenemy"),
    droprates:g("iso_droprates"), setdroprates:g("iso_setdroprates"), dropratesrestore:g("iso_dropratesrestore"),
    runeprices:g("iso_runeprices"), setruneprice:g("iso_setruneprice"),
    healprices:g("iso_healprices"), sethealprice:g("iso_sethealprice"),
    mp:g("iso_mp"), setmp:g("iso_setmp"), skillfx:g("iso_skillfx"), setskillfx:g("iso_setskillfx"),
    unites:g("iso_unites"), setunite:g("iso_setunite"),
    names:g("iso_names"), setname:g("iso_setname"),
    models:g("iso_models"), setmodel:g("iso_setmodel"),
    pakvolume:g("iso_pakvolume"), pakdirents:g("iso_pakdirents"), pakcodec:g("iso_pakcodec"),
    pakdecode:g("iso_pakdecode"), paktextures:g("iso_paktextures"), pakfaces:g("iso_pakfaces"),
    paksheet:g("iso_paksheet"), pakzip:g("iso_pakzip"), pakmodelinfo:g("iso_pakmodelinfo"),
    hardmode:g("iso_hardmode"), hmrestore:g("iso_hmrestore"),
    growthscale:g("iso_growthscale"), gsrestore:g("iso_gsrestore"),
    enemyscale:g("iso_enemyscale"), esrestore:g("iso_esrestore"),
    csvdatasets:g("iso_csvdatasets"), csvexport:g("iso_csvexport"), csvimport:g("iso_csvimport"),
    sets:g("iso_sets"), setmember:g("iso_setmember"), setbonus:g("iso_setbonus"),
    sethandler:g("iso_sethandler"), setdesc:g("iso_setdesc"), accnames:g("iso_accnames"),
    setgate:g("iso_setgate"), setgatechar:g("iso_setgatechar"),
    runealways:g("iso_runealways"), setrunealways:g("iso_setrunealways"),
    toggles:g("iso_toggles"), settoggle:g("iso_settoggle"),
    effecttargets:g("iso_effecttargets"), customsetbonus:g("iso_customsetbonus"),
    exportmod:g("iso_exportmod"), importmod:g("iso_importmod"), modstatus:g("iso_modstatus"),
  };
}

async function loadNames(){
  const [chars, armor, runes] = await Promise.all([
    fetch(`${EDITOR_DIR}/s5_characters.json`).then(r => r.json()),
    fetch(`${EDITOR_DIR}/s5_armor_names.json`).then(r => r.json()),
    fetch(`${EDITOR_DIR}/s5_rune_save_ids.json`).then(r => r.json()),
  ]);
  const cnames = {}; for(const c of chars) cnames[c.id] = c.name;
  const armorNames = { helm: armor.head||{}, body: armor.body||{},
                       glove: armor.glove||{}, foot: armor.foot||{} };
  const accNames = armor.accessory || {};      // 5th slot (may be absent in older tables)
  const runeNames = {}; runes.forEach((n, i) => { runeNames[String(i)] = n; });
  return { cnames, armorNames, accNames, runeNames, skillNames: SKILL_NAMES, rankNames: RANK_NAMES };
}

/* ---------------- Python glue (save + iso adapters) ---------------- */
const GLUE = `
import sys, json
sys.path.insert(0, "/editor")
import s5fields as F
import s5patch as P
import s5save as SV
import s5changes as CH
import s5toggles as TG
SV.BACKUPS = False; P.BACKUPS = False   # no real files in the browser FS

# ===== Save editor =====
def _individual(path):
    head = open(path, "rb").read(20)
    return head[:4] == b"CFU\\x00" or head[:17] == b"\\x0d\\x00\\x00\\x00SharkPortSave"
def open_save(path):
    try:
        if _individual(path):
            s = SV.read_individual_save(path)
            if not s: return json.dumps({"error": "no Suikoden V save found in that file (supported formats: memory-card images .ps2/.mc2/.mcd/.bin, .psu, .cbs, .xps/.sps)"})
            s["individual"] = True; s["editable"] = True; s.setdefault("meta", {"title": ""})
            saves = [s]
        else:
            saves = SV.read_all_saves(path)
        if not saves: return json.dumps({"error": "no Suikoden V save found in that file (supported formats: memory-card images .ps2/.mc2/.mcd/.bin, .psu, .cbs, .xps/.sps)"})
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
def save_audit(path, folder):
    try:
        gd = SV.read_gamedata_payload(path, folder or None)
        if not gd: return json.dumps({"error": "could not read gamedata payload"})
        return json.dumps({"snapshot": SV.audit_snapshot(gd)})
    except Exception as e: return json.dumps({"error": str(e)})
def save_item_names():
    # (cat, id) -> display name for the inventory editor: the editor's verified
    # drop table, plus category 15 (Trade Goods), which never drops and so is
    # absent there; its names were read from the ELF records at 0x5238A8
    # (ps2-re games/suikoden5/VM_HANDLERS.md section 4).
    cats = dict(F.DROP_TABLE.get("categories", {}))
    items = dict(F.DROP_TABLE.get("items", {}))
    cats["15"] = "Trade Goods"
    for i, n in enumerate(["Sugar","Salt","Soybeans","Vegetables","Fruit","Wheat",
        "Rice","Folk Crafts","Spices","Coffee","Tea","Iron","Gold and Silver",
        "Pearls","Miso and Soy Sauce","Beer","Wine","Sake","Golden Craftwork"], 1):
        items[f"15:{i}"] = n
    return json.dumps({"categories": cats, "items": items,
                       "maxid": SV.ITEM_CAT_MAXID, "limits": SV.STACK_LIMITS})

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
def iso_changes():
    # Decoded diff of the open disc (/iso.bin) against a pristine base disc
    # whose front slice the page wrote to /base.bin (Editor/s5changes.py).
    try:
        cur = open(ISO, "rb").read()
        base = open("/base.bin", "rb").read()
        return json.dumps(CH.diff_report(cur, base))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_stageinfo(path):
    # {lba, size, slot} of the BATLE.ROM overlay on the disc image at path,
    # or {} when the disc has none. The overlay's directory record lives in
    # the front slice, so this works on the slice alone; the overlay BYTES
    # sit far past it (US: byte 0xB54000), which is why the page stages them
    # from the retained full-disc File at this lba/size before the drop-rate
    # panel's engine calls run (issue #46).
    try:
        ov = next((o for o in P.list_overlays(path) if o["name"] == "BATLE.ROM"), None)
        if not ov: return json.dumps({})
        slot = ((ov["size"] + 2047) // 2048) * 2048
        return json.dumps({"lba": ov["lba"], "size": ov["size"], "slot": slot})
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
    weapons = {}
    try:
        if ISO:
            with P.Iso(ISO) as g:
                weapons = {str(k): v for k, v in P.weapon_type_names(g).items()}
    except Exception: weapons = {}
    return json.dumps({"items": items, "runes": runes, "armor": armor, "held": held,
        "weapons": weapons,
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
        with P.Iso(ISO) as g:
            tbls = P.read_character(g, int(cid))
            weapon = P.read_weapon_for_char(g, int(cid))
        return json.dumps({"tables": tbls, "weapon": weapon})
    except Exception as e: return json.dumps({"error": str(e)})
iso_setchar = _setter(lambda g, ident, e: P.write_field(g, e["table"], int(ident["id"]), e["field"], int(e["value"])))

def iso_loadout(cid):
    try:
        with P.Iso(ISO) as g: return json.dumps({"loadout": P.read_loadout(g, int(cid))})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setloadout(cid, kind, index, value):
    try:
        with P.Iso(ISO, writable=True) as g:
            if kind == "rune": P.write_loadout_rune(g, int(cid), int(index), int(value))
            elif kind == "perm": P.write_loadout_perm(g, int(cid), int(index), int(value))
            elif kind == "skill": P.write_loadout_skill(g, int(cid), int(index), int(value))
            else: return json.dumps({"error": "unknown loadout field " + str(kind)})
        return json.dumps({"ok": True})
    except Exception as e: return json.dumps({"error": str(e)})

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
            # The Dawn Rune ignores its own count byte (the game reads a runtime
            # counter the story script lowers), so its panel cannot edit the count.
            # The payload only flags which rune this is, so the panel can point at
            # the one unlock toggle, which lives under Code patches (issue #47).
            dawn = (not synth) and rid == F.DAWN_RUNE_INDEX
        return json.dumps({"name": name, "synthetic": synth, "grant": grant,
                           "spells": spells, "dawn": dawn})
    except Exception as e: return json.dumps({"error": str(e)})
iso_setrune = _setter(lambda g, ident, e: P.write_rune_field(g, int(ident["id"]), e["field"], int(e["value"])))

def iso_gear(slot):
    try:
        with P.Iso(ISO) as g: return json.dumps({"slot": slot, "items": P.list_armor(g, slot)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_gearitem(slot, i):
    try:
        with P.Iso(ISO) as g:
            r = P.read_armor_item(g, slot, int(i))
            r["nameCap"] = P.armor_name_cap(g, slot, int(i))
            r["descCap"] = P.armor_summary_cap(g, slot, int(i))
        return json.dumps(r)
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setgearname(slot, i, text):
    try:
        with P.Iso(ISO, writable=True) as g:
            return json.dumps(P.write_armor_name(g, slot, int(i), str(text)))
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

# ---- DATA.PAK assets (full disc) =============================================
# The editor only keeps the disc's front slice in memory, and DATA.PAK sits ~2 GB in, so
# the browser reads byte ranges out of the File and these adapters do the parsing and
# decoding. Nothing here writes: the asset tools are read-only.
import base64
def _buf(b):
    """Bytes from whatever the browser handed us (JS Uint8Array, memoryview, bytes)."""
    if isinstance(b, (bytes, bytearray, memoryview)): return bytes(b)
    try: return bytes(b.to_py())
    except Exception: return bytes(b)
def _b64(x): return base64.b64encode(x).decode()

def iso_pakvolume(buf, off):
    """Root directory of an ISO9660 volume from a window containing its descriptor —
    the disc itself (window at sector 16) and DATA.PAK's embedded volume both."""
    try: return json.dumps(P.rofs_volume_at(_buf(buf), int(off)))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_pakdirents(buf):
    try: return json.dumps({"entries": P.dir_entries(_buf(buf))})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_pakcodec(buf):
    try: return json.dumps({"codec": P.rom_codec(_buf(buf))})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_pakdecode(buf):
    """Decode one .ROM container -> base64 payload, for download."""
    try:
        codec, data, ok = P.rom_decode(_buf(buf))
        return json.dumps({"codec": codec, "decoded": ok, "size": len(data), "b64": _b64(data)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_paktextures(buf, label):
    try:
        _c, data, _ok = P.rom_decode(_buf(buf))
        tx = P.render_textures_data(data, label)
        return json.dumps({"imgs": [{"src": "data:image/png;base64," + _b64(png), "w": w, "h": h}
                                    for png, w, h in tx]})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_pakfaces(buf, label):
    try:
        _c, data, _ok = P.rom_decode(_buf(buf))
        faces, W, H = P.decode_faces_data(data, label)
        return json.dumps({"w": W, "h": H,
                           "faces": ["data:image/png;base64," + _b64(P._png(W, H, f)) for f in faces]})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_paksheet(buf, label, cols=8):
    try:
        _c, data, _ok = P.rom_decode(_buf(buf))
        png, n = P.render_portrait_sheet_data(data, label, int(cols))
        return json.dumps({"count": n, "b64": _b64(png)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_pakzip(buf, label):
    try:
        _c, data, _ok = P.rom_decode(_buf(buf))
        blob, n = P.render_portrait_zip_data(data, label)
        return json.dumps({"count": n, "b64": _b64(blob)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_pakmodelinfo(buf, label):
    try:
        _c, data, _ok = P.rom_decode(_buf(buf))
        return json.dumps(P.model_info_data(data, label))
    except Exception as e: return json.dumps({"error": str(e)})

# ---- Field models: which file each model id loads (pointer swap, see s5patch.set_model)
def iso_models():
    try:
        with P.Iso(ISO) as g:
            return json.dumps({"models": P.read_models(g), "targets": P.model_targets(g)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setmodel(mid, slot, group=0):
    try:
        mid = int(mid); slot = int(slot)
        with P.Iso(ISO) as g:
            ids = P.model_group(g, mid) if int(group) else [mid]
        with P.Iso(ISO, writable=True) as g:
            for i in ids:
                if slot < 0: P.reset_model(g, i)
                else: P.set_model(g, i, slot)
        return json.dumps({"ok": True, "ids": ids})
    except Exception as e: return json.dumps({"error": str(e)})

# ---- Equipment sets (membership + bonus magnitudes + effect assignment + piece text)
def iso_sets():
    try:
        with P.Iso(ISO) as g: d = P.read_sets(g)
        # attach readable labels + the documented bonus, and per-piece names/descriptions
        acc = {}
        grade_offs = {t[1] for t in F.SET_EFFECT_TARGETS if t[4] == "grade"}
        for s in d["sets"]:
            s["docBonus"] = F.SET_DOC_BONUS.get(s["index"], "")
            for e in s["effects"]:
                e["field"] = F.SET_FIELD_HINT.get(e.get("charOff"), None)
                e["grade"] = e.get("charOff") in grade_offs
            for m in s["members"]:
                slot = m["slot"]; sid = int(m["id"]) - 1     # equip id -> stat record
                m["statId"] = sid
                try:
                    with P.Iso(ISO) as g2:
                        it = P.read_armor_item(g2, slot if slot != "arm" else "arm", sid)
                    m["name"] = it.get("name") or ("#%d" % m["id"])
                    m["desc"] = it.get("summaryEn") or ""
                    m["descRaw"] = it.get("summary") or ""
                    with P.Iso(ISO) as g3:
                        m["descCap"] = P.armor_summary_cap(g3, slot, sid)
                except Exception:
                    m["name"] = "#%d" % m["id"]; m["desc"] = ""; m["descRaw"] = ""; m["descCap"] = 0
        # distinct handlers available to assign (this is what enables custom set bonuses)
        seen = {}
        for s in d["sets"]:
            h = s["handler"]
            if h not in seen:
                seen[h] = {"handler": h, "from": s["name"],
                           "summary": ("(no effect)" if s["noop"] else
                                       ", ".join(("%s %s%s" % (F.SET_FIELD_HINT.get(e.get("charOff"), "char+%s" % e.get("charOff")),
                                                  "=" if e.get("kind") == "set" else "+", e.get("value")))
                                                 if e.get("kind") in ("add", "set") else "(float)"
                                                 for e in s["effects"]))}
        d["handlers"] = list(seen.values())
        d["slots"] = list(F.SET_SLOT_ORDER)
        return json.dumps(d)
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setmember(idx, slot, equip_id):
    try:
        with P.Iso(ISO, writable=True) as g: return json.dumps(P.write_set_member(g, int(idx), slot, int(equip_id)))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setbonus(idx, eff, value):
    try:
        with P.Iso(ISO, writable=True) as g: return json.dumps(P.write_set_bonus(g, int(idx), int(eff), int(value)))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setgate(idx, enabled, original_word):
    try:
        with P.Iso(ISO, writable=True) as g:
            return json.dumps(P.write_set_gate(g, int(idx), bool(int(enabled)),
                                               int(original_word) if original_word else None))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_runealways():
    try:
        with P.Iso(ISO) as g: return json.dumps(P.read_rune_always_on(g))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setrunealways(rid, enabled, originals_json):
    try:
        with P.Iso(ISO, writable=True) as g:
            orig = json.loads(originals_json) if originals_json else None
            return json.dumps(P.write_rune_always_on(g, int(rid), bool(int(enabled)), orig))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_gearreqs():
    try:
        with P.Iso(ISO) as g: return json.dumps(P.read_gear_requirements(g))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setgearreq(field, value):
    try:
        with P.Iso(ISO, writable=True) as g: return json.dumps(P.write_gear_requirement(g, str(field), int(value)))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_toggles():
    try:
        with P.Iso(ISO) as g: return json.dumps({"toggles": TG.list_toggles(g)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_settoggle(tid, on, originals_json):
    try:
        orig = json.loads(originals_json) if originals_json else None
        with P.Iso(ISO, writable=True) as g:
            return json.dumps(TG.set_toggle(g, tid, bool(int(on)), orig))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_effecttargets():
    try:
        return json.dumps({"targets": P.set_effect_targets(),
                           "grades": P.set_grade_names(),
                           "capacity": P.read_custom_set_capacity(),
                           "customHandler": F.SET_CUSTOM_VADDR})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_customsetbonus(idx, effects_json):
    try:
        with P.Iso(ISO, writable=True) as g:
            return json.dumps(P.write_custom_set_bonus(g, int(idx), json.loads(effects_json)))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setgatechar(idx, char_id, original_word):
    try:
        with P.Iso(ISO, writable=True) as g:
            return json.dumps(P.write_set_gate_char(g, int(idx), int(char_id),
                                                    int(original_word) if original_word else None))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_sethandler(idx, handler):
    try:
        with P.Iso(ISO, writable=True) as g: return json.dumps(P.write_set_handler(g, int(idx), int(handler)))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setdesc(slot, stat_id, text):
    try:
        with P.Iso(ISO, writable=True) as g:
            return json.dumps(P.write_armor_summary(g, slot, int(stat_id), str(text)))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_accnames():
    try:
        with P.Iso(ISO) as g:
            out = {}
            for i in range(F.ARMOR_TABLES["accessory"][1]):
                nm, en, jp = P._armor_name(g, "accessory", i, P.armor_addr("accessory", i))
                out[str(i + 1)] = nm or ("#%d" % i)          # key = equip id
        return json.dumps({"accessory": out})
    except Exception as e: return json.dumps({"error": str(e)})

def iso_droprates():
    try:
        with P.Iso(ISO) as g: return json.dumps(P.read_drop_rates(g))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_setdroprates(slots_json, dish, cook_json):
    try:
        slots = [int(x) for x in json.loads(slots_json)]
        cook = [int(x) for x in json.loads(cook_json)]
        with P.Iso(ISO, writable=True) as g:
            r = P.write_drop_rates(g, slots, int(dish), cook)
        return json.dumps(dict(r, ok=True))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_dropratesrestore():
    try:
        with P.Iso(ISO, writable=True) as g: r = P.restore_drop_rates(g)
        return json.dumps(dict(r, ok=True))
    except Exception as e: return json.dumps({"error": str(e)})

def iso_hardmode(factor):
    try: return json.dumps({"ok": True, "count": P.hardmode_apply(ISO, float(factor))})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_hmrestore():
    try: return json.dumps({"ok": True, "count": P.hardmode_restore(ISO)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_growthscale(factor):
    try: return json.dumps({"ok": True, "count": P.growth_scale(ISO, float(factor))})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_gsrestore():
    try: return json.dumps({"ok": True, "count": P.growth_scale_restore(ISO)})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_enemyscale(factor, hp_factor):
    try:
        r = P.enemy_scale(ISO, float(factor), float(hp_factor))
        return json.dumps(dict(r, ok=True))
    except Exception as e: return json.dumps({"error": str(e)})
def iso_esrestore():
    try: return json.dumps(dict(P.enemy_scale_restore(ISO), ok=True))
    except Exception as e: return json.dumps({"error": str(e)})

def iso_csvdatasets():
    try: return json.dumps({"datasets": P.CSV_DATASETS, "legends": P.csv_legends()})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_csvexport(dataset):
    try:
        fn, text = P.csv_export(ISO, str(dataset))
        return json.dumps({"ok": True, "filename": fn, "csv": text})
    except Exception as e: return json.dumps({"error": str(e)})
def iso_csvimport(dataset, csv_text):
    try: return json.dumps(P.csv_import(ISO, str(dataset), str(csv_text), make_backup=False))
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
      accept: { "application/octet-stream": [".ps2",".mc2",".mcd",".bin",".psu",".cbs",".xps",".sps"] } }] });
    saveHandle = h; const f = await h.getFile(); openFile(f, h);
  }catch(e){
    if(e && e.name === "AbortError") return;   // user cancelled the picker: stay silent
    // The API exists but the platform blocked the call (embedded or automated
    // browsers refuse with "not allowed by the user agent or the platform").
    // Fall back to the plain file input so a save can still be opened.
    $("file").click();
  }
}

async function openFile(f, handle){
  if(!f) return;
  curFileName = f.name; saveHandle = handle || (handle === undefined ? saveHandle : null);
  $("filename").textContent = `${f.name} · ${(f.size/1048576).toFixed(2)} MB`;
  spin(true);
  try{
    const buf = new Uint8Array(await f.arrayBuffer());
    pyodide.FS.writeFile(SAVE_PATH, buf);
    sEdits = {}; CHARDATA = {}; ROSTER = {};
    const res = JSON.parse(PY.open(SAVE_PATH));
    if(res.error){ $("saves").innerHTML = `<p class="bad" style="padding:8px">${esc(res.error)}</p>`;
      toast(res.error, "bad"); return; }
    saves = res.saves; saves.forEach(s => s.card = curFileName);
    // Prefetch each save's roster so party pickers can offer recruited characters only.
    for(let i=0;i<saves.length;i++){
      if(saves[i].editable === false) continue;
      try{ const r = await fetchChars(saves[i]); if(!r.error) ROSTER[i] = r.chars; }catch(_){}
    }
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
         <button class="ghost" onclick="openInv(${i})">Inventory…</button>
         <button class="ghost" onclick="openHealth(${i})">Health check…</button>
         <span class="note">Change fields freely — nothing is written to your file until you press Save.</span>`;
    return `<div class="sec">
      <div class="card-hd">${esc(sv.folder)} ${badge}
        <span class="note">· ${esc(sv.card)} · ${esc((sv.meta&&sv.meta.title)||"")}</span></div>
      <!-- read-only progress facts, stated as text so they don't look like broken inputs -->
      <div class="ro-row">
        <span class="ro-item"><b>Level</b> ${fl.level||0}</span>
        <span class="ro-item"><b>Playtime</b> ${esc(fl.playtime)}</span>
        <span class="note">from the save-select display · read-only (edit unit levels under Characters)</span>
      </div>
      <div class="subhd">Names</div>
      <div class="grid" style="padding-top:6px">
        ${nameFld(i, "heroName",   "Hero name",   fl.heroName,   sv.folder, ro, "shown in menus &amp; dialogue")}
        ${nameFld(i, "castleName", "Castle name", fl.castleName, sv.folder, ro, "your HQ")}
        ${nameFld(i, "armyName",   "Army name",   fl.armyName,   sv.folder, ro, "your faction")}
      </div>
      <div class="subhd">Resources</div>
      <div class="grid" style="padding-top:6px">
        ${numFld(i, "potch",   "potch",   "Potch",    fl.potch,   99999999, sv.folder, ro)}
        ${numFld(i, "psp",     "partySP", "Party SP", fl.partySP, 999999,   sv.folder, ro)}
        <div class="fld"><label>New Game Plus</label><div class="in">
          <label class="chk"><input type="checkbox" id="sv${i}_ngp" ${fl.newGamePlus?"checked":""} ${ro?"disabled":""}
            data-sk="sv${i}:newGamePlus" data-orig="${fl.newGamePlus?1:0}" data-lbl="New Game Plus" data-grp="${esc(sv.folder)}">
            <span class="note">NG+ run</span></label>${ro?"":REVERT_BTN_SAVE}</div>
          <div class="fnote">On: the game treats this save as a New Game Plus run the next time it is loaded, and runs its carry-over build on load. The faster dash tier is part of that state. This writes the real NG+ carry byte; the old control wrote a display copy the load path never reads.</div></div>
      </div>
      ${ro?"":`<div class="subhd">Active party
        <span class="note">: tap a slot to choose; recruited characters are listed first</span></div>
      <div id="pwarn${i}"></div>
      ${partyGroup(i, fl.party||[], 0, 6, "Battle members")}
      ${partyGroup(i, fl.party||[], 6, 10, "Support members")}`}
      <div class="card-ft">${foot}</div>
      <div id="chars${i}" class="note" style="margin:0 14px 8px"></div>
      <div id="recruit${i}" style="margin:0 14px 10px"></div>
      <div id="inv${i}" style="margin:0 14px 10px"></div>
      <div id="health${i}" style="margin:0 14px 10px"></div>
    </div>`;
  }).join("");
  wireSaveHeaderInputs();
  updateSaveToolbar();
}

/* ---------- save-card field builders ---------- */
const fmtNum = (n) => Number(n || 0).toLocaleString();

function nameFld(i, key, label, val, folder, ro, hint){
  return `<div class="fld"><label>${label}</label><div class="in">
    <input id="sv${i}_${key}" value="${esc(val)}" maxlength="16" ${ro?"disabled":""}
      data-sk="sv${i}:${key}" data-orig="${esc(val)}" data-lbl="${label}" data-grp="${esc(folder)}">${ro?"":REVERT_BTN_SAVE}</div>
    <div class="fnote">${hint} · up to 15 characters</div></div>`;
}
/* Number field with a formatted read-out, its cap, and a one-tap Max.
   Order matters: the ↺ must sit directly after the input (revertSaveField uses
   previousElementSibling), so Max goes last. */
function numFld(i, idk, key, label, val, max, folder, ro){
  return `<div class="fld"><label>${label}</label><div class="in">
    <input type="number" id="sv${i}_${idk}" value="${val||0}" min="0" max="${max}" ${ro?"disabled":""}
      data-sk="sv${i}:${key}" data-orig="${val||0}" data-lbl="${label}" data-grp="${esc(folder)}"
      data-note="sv${i}_${idk}_note" data-max="${max}">${ro?"":REVERT_BTN_SAVE}
    ${ro?"":`<button type="button" class="chip mini" title="Set to the game's maximum"
      onclick="setSaveMax(${i},'${idk}','${key}',${max})">Max</button>`}</div>
    <div class="fnote" id="sv${i}_${idk}_note">${fmtNum(val)} · max ${fmtNum(max)}</div></div>`;
}
function setSaveMax(i, idk, key, max){
  const el = $(`sv${i}_${idk}`); if(!el) return;
  el.value = String(max);
  stageSaveField(i, key, max, el);
}

/* ---------- inventory editor ---------- */
let INVNAMES = null;   // {categories, items, maxid, limits} from the engine (save_item_names)
const INV_LIST_LABEL = { bag: "Party bag", warehouse: "Warehouse (HQ storage)", keyItems: "Key items" };
const INV_CAPS = { bag: 70, warehouse: 300, keyItems: 64 };
function invItemName(cat, id){
  return (INVNAMES && INVNAMES.items[`${cat}:${id}`]) || `Category ${cat} #${id}`;
}
function invCatName(cat){ return (INVNAMES && INVNAMES.categories[String(cat)]) || ("Category " + cat); }
async function openInv(i){
  const box = $(`inv${i}`);
  if(box._open){ box._open = false; box.innerHTML = ""; return; }
  if(!INVNAMES){
    try{ INVNAMES = JSON.parse(PY.invnames()); }
    catch(_){ INVNAMES = { categories: {}, items: {}, maxid: {}, limits: {} }; }
  }
  box._open = true;
  renderInv(i);
}
function renderInv(i){
  const box = $(`inv${i}`); if(!box || !box._open) return;
  const inv = (saves[i].fields && saves[i].fields.inventory) || {};
  let h = "";
  for(const list of ["bag", "warehouse", "keyItems"]){
    const entries = inv[list] || [];
    h += `<div class="subhd">${INV_LIST_LABEL[list]} <span class="note">${entries.length}/${INV_CAPS[list]}</span></div>`;
    if(!entries.length) h += `<div class="note" style="padding:2px 2px 6px">empty</div>`;
    h += entries.map((e) => invRow(i, list, e)).join("");
    if(entries.length < INV_CAPS[list])
      h += `<div style="padding:2px 0 8px"><button type="button" class="ghost" onclick="pickInvItem(${i},'${list}',${entries.length})">+ Add item…</button></div>`;
  }
  h += `<div class="fnote" style="padding-bottom:8px">Removing an entry compacts the list, the packed shape the game itself keeps. Trade goods and antiques carry a per-item value set by the game (price paid, appraised value): it is kept when only the quantity changes and reset when the item is replaced.</div>`;
  box.innerHTML = h;
}
function invRow(i, list, e){
  const qty = e.max > 0
    ? `<input type="number" style="width:64px" min="1" max="${e.max}" value="${e.qty}" onchange="setInvQty(${i},'${list}',${e.slot},${e.cat},${e.id},this.value)"> <span class="note">of ${e.max} max</span>`
    : `<span class="note">single</span>`;
  return `<div class="row" style="align-items:center;gap:8px;padding:2px 0">
    <button type="button" class="pickbtn" onclick="pickInvItem(${i},'${list}',${e.slot})"><span class="pickbtn-name">${esc(invItemName(e.cat, e.id))}</span><span class="pickbtn-id note">${esc(invCatName(e.cat))} #${e.id}</span></button>
    ${qty}
    <button type="button" class="chip mini" title="Remove this entry" onclick="removeInvItem(${i},'${list}',${e.slot})">Remove</button>
  </div>`;
}
function invPickerList(){
  const list = [];
  if(!INVNAMES) return list;
  for(const cat of Object.keys(INVNAMES.maxid).map(Number).sort((a, b) => a - b))
    for(let id = 1; id <= INVNAMES.maxid[cat]; id++)
      list.push({ id: `${cat}:${id}`, name: invItemName(cat, id), desc: invCatName(cat) });
  return list;
}
function pickInvItem(i, list, slot){
  const entries = ((saves[i].fields || {}).inventory || {})[list] || [];
  const cur = slot < entries.length ? `${entries[slot].cat}:${entries[slot].id}` : "";
  openPicker(`Choose item (${INV_LIST_LABEL[list]})`, invPickerList(), cur, (sel) => {
    const [cat, id] = sel.split(":").map(Number);
    const lim = ((INVNAMES.limits || {})[String(cat)] || [])[id - 1] || 0;
    writeInvField(i, list, slot, { cat, id, qty: lim ? 1 : 0 });
  }, { cap: 1000 });
}
function setInvQty(i, list, slot, cat, id, v){ writeInvField(i, list, slot, { cat, id, qty: +v }); }
function removeInvItem(i, list, slot){ writeInvField(i, list, slot, { cat: 0 }); }
async function writeInvField(i, list, slot, val){
  const sv = saves[i];
  spin(true);
  try{
    const r = JSON.parse(PY.write(SAVE_PATH, sv.folder || "", JSON.stringify({ [`inv_${list}_${slot}`]: val })));
    if(r.error){ toast("Error: " + r.error, "bad"); return; }
    const re = JSON.parse(PY.open(SAVE_PATH));
    if(!re.error){ saves = re.saves; saves.forEach(s => s.card = curFileName); }
    sEdits[`inv:${list}:${slot}`] = { label: `${INV_LIST_LABEL[list]} · ${val.cat ? invItemName(val.cat, val.id) : "entry removed"}`,
      group: sv.folder, to: val.cat ? (val.qty ? `x${val.qty}` : "set") : "removed" };
    updateSaveToolbar();
    renderInv(i);
  }catch(e){ toast("Write failed: " + (e.message || e), "bad"); console.error(e); }
  finally{ spin(false); }
}

/* ---------- per-character held items (fixed slots, no compaction) ---------- */
function heldRow(i, k, e){
  if(!e || !e.cat)
    return `<div class="row" style="align-items:center;gap:8px;padding:2px 0">
      <span class="note" style="min-width:52px">Slot ${k+1}</span><span class="note">empty</span>
      <button type="button" class="ghost mini" onclick="pickHeldItem(${i},${k})">+ Add item…</button></div>`;
  const qty = e.max > 0
    ? `<input type="number" style="width:64px" min="1" max="${e.max}" value="${e.qty}" onchange="setHeldQty(${i},${k},${e.cat},${e.id},this.value)"> <span class="note">of ${e.max} max</span>`
    : `<span class="note">single</span>`;
  return `<div class="row" style="align-items:center;gap:8px;padding:2px 0">
    <span class="note" style="min-width:52px">Slot ${k+1}</span>
    <button type="button" class="pickbtn" onclick="pickHeldItem(${i},${k})"><span class="pickbtn-name">${esc(invItemName(e.cat, e.id))}</span><span class="pickbtn-id note">${esc(invCatName(e.cat))} #${e.id}</span></button>
    ${qty}
    <button type="button" class="chip mini" title="Remove this entry" onclick="removeHeldItem(${i},${k})">Remove</button>
  </div>`;
}
function heldEntry(i, k){
  const idx = +$(`csel${i}`).value;
  const c = ((CHARDATA[i] || {}).chars || []).find(x => x.idx === idx);
  return c ? (c.held || [])[k] : null;
}
function pickHeldItem(i, k){
  const e = heldEntry(i, k);
  const cur = e && e.cat ? `${e.cat}:${e.id}` : "";
  openPicker(`Choose held item (slot ${k+1})`, invPickerList(), cur, (sel) => {
    const [cat, id] = sel.split(":").map(Number);
    const lim = ((INVNAMES.limits || {})[String(cat)] || [])[id - 1] || 0;
    writeHeldField(i, k, { cat, id, qty: lim ? 1 : 0 });
  }, { cap: 1000 });
}
function setHeldQty(i, k, cat, id, v){ writeHeldField(i, k, { cat, id, qty: +v }); }
function removeHeldItem(i, k){ writeHeldField(i, k, { cat: 0 }); }
async function writeHeldField(i, k, val){
  const sv = saves[i], idx = +$(`csel${i}`).value;
  spin(true);
  try{
    const r = JSON.parse(PY.write(SAVE_PATH, sv.folder || "", JSON.stringify({ [`c${idx}_held${k}`]: val })));
    if(r.error){ toast("Error: " + r.error, "bad"); return; }
    sEdits[`c${idx}:held${k}`] = { label: `${nameOf(idx)} · Held item ${k+1}`,
      group: sv.folder, to: val.cat ? invItemName(val.cat, val.id) : "removed" };
    updateSaveToolbar();
    // Re-render the whole panel: held slot 1 shares its byte with Accessory.
    const rr = await fetchChars(sv); if(!rr.error) CHARDATA[i] = rr;
    renderChar(i);
  }catch(e){ toast("Write failed: " + (e.message || e), "bad"); console.error(e); }
  finally{ spin(false); }
}

/* ---------- save health audit ---------- */
async function openHealth(i){
  const box = $(`health${i}`);
  if(box._open){ box._open = false; box.innerHTML = ""; return; }
  box._open = true; box.innerHTML = `<span class="note">checking this save against the game's own rules…</span>`;
  let r;
  try{ r = JSON.parse(PY.audit(SAVE_PATH, saves[i].folder || "")); }
  catch(e){ box.innerHTML = `<span class="bad">${esc(e.message || e)}</span>`; return; }
  if(r.error){ box.innerHTML = `<span class="bad">${esc(r.error)}</span>`; return; }
  const findings = HealthCore.audit(r.snapshot, { charName: (id) => nameOf(id) });
  if(!findings.length){
    box.innerHTML = `<div class="subhd">Health check</div>
      <div class="ok" style="padding:2px 0 8px">No problems found. This save matches every rule the game itself keeps.</div>`;
    return;
  }
  const c = HealthCore.counts(findings);
  const sevTag = (s) => s === "error" ? `<span class="bad"><b>Error</b></span>`
    : s === "warn" ? `<span style="color:var(--warn)"><b>Warning</b></span>` : `<span class="note"><b>Note</b></span>`;
  box.innerHTML = `<div class="subhd">Health check <span class="note">${c.error} error${c.error === 1 ? "" : "s"}, ${c.warn} warning${c.warn === 1 ? "" : "s"}</span></div>`
    + findings.map((f) => `<div style="padding:4px 0;border-bottom:1px solid var(--line)">
        ${sevTag(f.sev)} <b>${esc(f.title)}</b>
        <div class="note" style="padding-top:2px">${esc(f.detail)}</div></div>`).join("")
    + `<div class="fnote" style="padding:6px 0 8px">The audit only reports states the game itself never writes (proven against the game's code and real saves). It changes nothing.</div>`;
}

/* ---------- active party ---------- */
const PARTY_EMPTY = 256;
function partyDisplay(v){ return +v === PARTY_EMPTY ? "— empty —" : (NAMES.cnames[v] || ("#"+v)); }

function partyGroup(i, party, from, to, title){
  let h = `<div class="fnote" style="margin:8px 16px 0">${title}</div>
    <div class="grid" style="padding-top:6px">`;
  for(let k=from;k<to;k++){
    const val = party[k] == null ? PARTY_EMPTY : party[k];
    const hero = val === 0;
    h += `<div class="fld"><label>Slot ${k+1}${hero?" · hero":""}</label><div class="in">
      <button type="button" class="pickbtn" id="sv${i}_party${k}"
        data-sk="sv${i}:party${k}" data-orig="${val}" data-lbl="Party slot ${k+1}" data-grp="Party"
        onclick="pickParty(${i},${k})">
        <span class="pickbtn-name">${esc(partyDisplay(val))}</span>
        <span class="pickbtn-id note">${val===PARTY_EMPTY?"":"#"+val}</span></button>${REVERT_BTN_SAVE}</div></div>`;
  }
  return h + `</div>`;
}
/* Searchable picker (not a 120-entry native dropdown): recruited characters first,
   already-in-party marked. Unrecruited characters are listed last and labelled with
   what assigning them does: the write changes only the party arrays (the real
   party storage, issue #32). Assigning never writes recruitment state
   (issue #39), so an assigned character's status record stays as it was and
   the health audit flags the mismatch. */
function partyList(i){
  const roster = ROSTER[i] || [];
  const inParty = new Map();
  for(let k=0;k<10;k++){
    const el = $(`sv${i}_party${k}`); if(!el) continue;
    const v = +(el.dataset.cur != null ? el.dataset.cur : el.dataset.orig);
    if(v !== PARTY_EMPTY) inParty.set(v, k+1);
  }
  const list = [{ id: PARTY_EMPTY, name: "— empty —", desc: "leave this slot open" }];
  const tag = (c) => inParty.has(c.idx) ? `already in slot ${inParty.get(c.idx)}` : "";
  for(const c of roster) if(c.recruited)
    list.push({ id: c.idx, name: nameOf(c.idx), desc: tag(c) });
  for(const c of roster) if(!c.recruited)
    list.push({ id: c.idx, name: nameOf(c.idx), desc: "not recruited in this save (assigning writes the party only; their record stays inactive)" });
  if(!roster.length)   // roster unavailable → fall back to every known name
    for(const id of Object.keys(NAMES.cnames)) list.push({ id: +id, name: NAMES.cnames[id] });
  return list;
}
function pickParty(i, slot){
  const el = $(`sv${i}_party${slot}`); if(!el) return;
  const cur = el.dataset.cur != null ? el.dataset.cur : el.dataset.orig;
  openPicker(`Party slot ${slot+1}`, partyList(i), cur, (id) => {
    const v = +id;
    q(".pickbtn-name", el).textContent = partyDisplay(v);
    q(".pickbtn-id", el).textContent = v === PARTY_EMPTY ? "" : "#" + v;
    el.dataset.cur = String(v);
    stageSaveField(i, `party${slot}`, v, el);
    refreshPartyWarn(i);
  }, {});
}
/* Warn (don't block) when one character occupies two slots — the game expects unique members. */
function refreshPartyWarn(i){
  const box = $(`pwarn${i}`); if(!box) return;
  const seen = new Map(), dupes = new Set(); let hasHero = false;
  for(let k=0;k<10;k++){
    const el = $(`sv${i}_party${k}`); if(!el) continue;
    const v = +(el.dataset.cur != null ? el.dataset.cur : el.dataset.orig);
    if(v === PARTY_EMPTY) continue;
    if(v === 0) hasHero = true;
    if(seen.has(v)) dupes.add(v); else seen.set(v, k+1);
  }
  const warns = [];
  if(dupes.size)
    warns.push(`${[...dupes].map(v=>esc(partyDisplay(v))).join(", ")} appears in more than one slot. The game expects each character once. Fix this before saving.`);
  if(!hasHero)
    warns.push(`The hero is not in the party. The game expects him in one of the slots. Add him before saving.`);
  box.innerHTML = warns.length ? `<div class="warnbox">⚠ ${warns.join("<br>")}</div>` : "";
}

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
    const ngp = $(`sv${i}_ngp`);
    if(ngp) ngp.onchange = () => stageSaveField(i, "newGamePlus", ngp.checked?1:0, ngp);
    refreshPartyWarn(i);          // party slots are pickers (wired via pickParty)
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
    // keep the formatted read-out under number fields in sync
    if(el.dataset.note){
      const nEl = $(el.dataset.note);
      if(nEl) nEl.textContent = `${fmtNum(value)} · max ${fmtNum(+el.dataset.max)}`;
    }
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
    </div><div id="cfld${i}"></div>`;
  renderChar(i);
}

/* picker-button field for id-based selects (armor / rune / skill-slot).
 * onPick carries single-quoted string args (pickArmor(0,'helm',0)), so the attribute
 * must be double-quoted — single quotes would end it early and the click would do nothing. */
function pickBtn(sk, cf, curId, curName, onPick){
  return `<button type="button" class="pickbtn" data-sk="${sk}" data-cf="${cf}" data-orig="${esc(curId)}" onclick="${onPick}">
    <span class="pickbtn-name">${esc(curName)}</span><span class="pickbtn-id note">#${esc(curId)}</span></button>${REVERT_BTN_SAVE}`;
}
function armorList(slot){
  const m = (slot === "accessory" ? (NAMES.accNames || {}) : NAMES.armorNames[slot]) || {};
  const list = Object.keys(m).map(k => ({ id:k, name:m[k] }));
  if(!("0" in m)) list.unshift({ id:"0", name:"— Nothing —" });   // tables already carry 0: "Nothing"
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
  const armorName = (slot,val) => ((slot==="accessory"?(NAMES.accNames||{}):(NAMES.armorNames[slot]||{}))[String(val)])
    || (String(val)==="0"?"— Nothing —":"#"+val);
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
  const numFld = (field, lbl, val, min, max) => `<div class="fld"><label>${lbl}</label><div class="in"><input type="number" id="ce${i}_${field}" value="${val}" min="${min}" max="${max}" data-cf="${i}:${field}" data-orig="${val}" onchange="writeCharField(${i},'${field}',this.value,this)">${REVERT_BTN_SAVE}</div></div>`;
  $(`cfld${i}`).innerHTML =
    sub("Level & recruitment")
    + `<div class="grid" style="grid-template-columns:150px 150px 150px 260px;padding-top:8px">`
    + `<div class="fld"><label>Level</label><div class="in"><input type="number" id="ce${i}_level" value="${c.level}" min="1" max="99" data-cf="${i}:level" data-orig="${c.level}" onchange="writeCharField(${i},'level',this.value,this)">${REVERT_BTN_SAVE}</div></div>`
    + numFld("exp", "EXP", c.exp || 0, 0, 65535)
    + numFld("sp", "SP", c.sp || 0, 0, 65535)
    + recruitToggleHTML(i, c)
    + `</div>`
    + `<div class="fnote" style="margin:2px 16px 0">The Recruited toggle writes the game's own status record (issue #39): checking it recruits the character, unchecking returns them to not recruited. A character joins with the level, gear and skills already in their record; nothing else in the record is created or changed.</div>`
    + guideHTML(idx)
    + sub("Equipment")
    + `<div class="grid" style="grid-template-columns:150px;padding-top:8px">`
    + numFld("wlv", "Weapon level", c.weaponLevel || 1, 1, 16)
    + `</div>`
    + `<div class="fnote" style="margin:2px 16px 0">Sharpen level of the character's own weapon (1 to 16). Which weapon it is comes from the ISO starting-loadout editor; this is the level the blacksmith raises.</div>`
    + g(aBtn("helm","Helm",c.armor.helm)+aBtn("body","Armor",c.armor.body)+aBtn("glove","Gloves",c.armor.glove)+aBtn("foot","Boots",c.armor.foot))
    + `<div class="fnote" style="margin:2px 16px 0">Accessory is the 5th equipment slot: needed to complete a 5-piece set.</div>`
    + g(aBtn("accessory","Accessory",c.armor.accessory==null?0:c.armor.accessory))
    + sub("Held items")
    + `<div style="padding-top:8px">${(c.held || []).map((e, k) => heldRow(i, k, e)).join("")}</div>`
    + `<div class="fnote" style="margin:2px 16px 0">Items the character carries personally (starting items, rune orbs, accessories). Slot 1 shares its item byte with the Accessory field above: when slot 1 holds an accessory, that accessory is the equipped one, and clearing slot 1 clears it.</div>`
    + sub("Runes")
    + g(rBtn("rhead","Head",c.runes.rhead)+rBtn("rright","Right hand",c.runes.rright)+rBtn("rleft","Left hand",c.runes.rleft))
    + runeStatesHTML(c)
    + `<div class="grid" style="grid-template-columns:repeat(4,110px);padding-top:8px">`
    + [0,1,2,3].map(k => numFld(`ruse${k}`, `Lv${k+1} uses left`, (c.runeUses || [0,0,0,0])[k], 0, 9)).join("")
    + `</div>`
    + `<div class="fnote" style="margin:2px 16px 0">Spell uses left per spell level (of ${(c.runeUsesMax || [0,0,0,0]).join(" / ")} at this level and rune). The game loads these counts from the save, so edits here are the counts the character comes back with.</div>`
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
/* Rune slot availability line (issue #6, display only): the game computes
 * availability at menu time from the character's level; there is no stored
 * count to edit. Thresholds are community-derived data (labelled as such);
 * the fixed-rune markers are the record's own flag bytes. */
function runeStatesHTML(c){
  const sts = c.slotStates || [];
  if(!sts.length) return "";
  const lbl = { rhead:"Head", rright:"Right hand", rleft:"Left hand" };
  const parts = sts.map(st => {
    let t;
    if(st.state === "open") t = "open";
    else if(st.state === "locked") t = `locked until Lv ${st.unlock}`;
    else if(st.state === "never") t = "never opens";
    else t = "no community data";
    if(st.fixed) t += " · fixed rune (cannot be removed)";
    return `${lbl[st.slot]}: ${t}`;
  });
  const anyCommunity = sts.some(st => st.community);
  return `<div class="fnote" style="margin:2px 16px 0">Rune slots${
    anyCommunity ? " (unlock levels are community-derived, not read from the game)" : ""}: ${parts.join(" · ")}</div>`;
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

/* idxOverride: callers outside the character panel (the recruitment
   roster) pass the row's character id, because the panel may be closed
   or showing someone else; panel callers leave it off and the id comes
   from the panel's character select, as before. */
async function writeCharField(i, field, value, el, idxOverride){
  const sv = saves[i], idx = idxOverride != null ? idxOverride : +$(`csel${i}`).value;
  const key = `c${idx}_${field}`;
  const c0 = ((CHARDATA[i] || {}).chars || []).find(x => x.idx === idx);   // pre-write state (for the #6 warning)
  spin(true);
  try{
    const r = JSON.parse(PY.write(SAVE_PATH, sv.folder || "", JSON.stringify({ [key]: +value })));
    if(r.error){ toast("Error: " + r.error, "bad"); return; }
    // Issue #6: removing a rune from a slot that is still below its unlock
    // level strands the slot empty in game until that level (the game gates
    // empty slots by level; a filled slot displays at any level).
    if(["rhead","rright","rleft"].includes(field) && +value === 0 && c0 && c0.runes && c0.runes[field]){
      const st = (c0.slotStates || []).find(s => s.slot === field);
      if(st && typeof st.threshold === "number" && c0.level < st.threshold)
        toast(`Warning: the ${charFieldLabel(field)} slot will be stranded empty until Lv ${st.threshold} (community-derived threshold). The rune is removed, but the empty slot stays locked in game until that level.`, "bad");
    }
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
  if(el && el.tagName === "LABEL") el = el.querySelector("input");   // NG+ checkbox
  if(!el || el.dataset.orig == null) return;
  const orig = el.dataset.orig;
  if(el.dataset.sk && el.dataset.sk.indexOf("sv") === 0){            // header / party field
    const [svk, key] = el.dataset.sk.split(":");
    const i = +svk.slice(2);
    if(el.classList.contains("pickbtn")){                            // party slot
      q(".pickbtn-name", el).textContent = partyDisplay(+orig);
      q(".pickbtn-id", el).textContent = +orig === PARTY_EMPTY ? "" : "#" + orig;
      el.dataset.cur = String(+orig);
      stageSaveField(i, key, +orig, el);
      refreshPartyWarn(i);
    }else if(el.type === "checkbox"){ el.checked = (orig === "1" || orig === 1); stageSaveField(i, key, el.checked?1:0, el); }
    else if(el.type === "number"){ el.value = orig; stageSaveField(i, key, +orig, el); }
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
/* Per-character recruit toggle (issue #39): a checkbox over the game's
   status word, written by s5save.set_recruited (0x0302 recruits, 0x0100
   unrecruits; both proven on a roster screen, ROSTER_VERIFY.md). The
   checkbox is live only where the engine will write: a recruitable
   character whose record exists and who is not in the party. Everywhere
   else it renders disabled with the reason, mirroring the engine guards. */
function recruitToggleHTML(i, c){
  const wrap = (inner) => `<div class="fld"><label>Recruited</label><div class="in">${inner}</div></div>`;
  const dis = (checked, note) => wrap(`<label class="chk"><input type="checkbox" ${checked?"checked":""} disabled> <span class="note">${note}</span></label>`);
  if(!c.recruitable) return dis(false, "not recruitable");
  if(c.inParty) return dis(true, "in party: remove from the party first to unrecruit");
  if(c.recruitState === "none") return dis(false, "no record in this save yet");
  return wrap(`<label class="chk"><input type="checkbox" id="ce${i}_rec" ${c.recruited?"checked":""} data-cf="${i}:rec" data-orig="${c.recruited?1:0}" onchange="writeCharField(${i},'rec',this.checked?1:0,this)"> <span class="note">in the roster</span></label>${REVERT_BTN_SAVE}`);
}
/* Roster-row recruit toggle: the character panel toggle
   (recruitToggleHTML) without the field wrapper, for the recruitment
   list rows. Same guards, same states: live only for a recruitable
   character whose record exists and who is not in the party; everywhere
   else it renders disabled with the panel's reason. The write goes
   through the same writeCharField staging as the panel toggle, with
   the row's own character id passed explicitly. */
function recruitRowToggleHTML(i, c){
  const dis = (checked, note) => `<label class="chk"><input type="checkbox" ${checked?"checked":""} disabled> <span class="note">${note}</span></label>`;
  if(!c.recruitable) return dis(false, "not recruitable");
  if(c.inParty) return dis(true, "in party: remove from the party first to unrecruit");
  if(c.recruitState === "none") return dis(false, "no record in this save yet");
  return `<label class="chk"><input type="checkbox" id="rec${i}_${c.idx}" ${c.recruited?"checked":""} data-orig="${c.recruited?1:0}" onchange="rosterRecToggle(${i},${c.idx},this)"> <span class="note">in the roster</span></label>`;
}
async function rosterRecToggle(i, idx, el){
  const orig = el.dataset.orig;
  await writeCharField(i, "rec", el.checked?1:0, el, idx);
  refreshRecruitRow(i, idx, orig);
}
/* After a roster toggle, re-render that row's status line and toggle
   from the refreshed cache and refresh the header count (recruitCount
   also refreshes the Eresh/Euram pair warning). A refused write leaves
   the cache untouched, so the row falls back to the true state. The new
   toggle keeps the data-orig the row was rendered with, so toggling
   back still clears the pending edit, like the character panel. */
function refreshRecruitRow(i, idx, orig){
  const r = CHARDATA[i]; if(!r) return;
  const c = r.chars.find(x => x.idx === idx); if(!c) return;
  const st = $(`recstat${i}_${idx}`); if(st) st.textContent = recruitStatusText(c);
  const cell = $(`rectog${i}_${idx}`);
  if(cell){
    cell.innerHTML = recruitRowToggleHTML(i, c);
    const nb = $(`rec${i}_${idx}`);
    if(nb){
      if(orig != null) nb.dataset.orig = orig;
      if(sEdits[`c${idx}:rec`]) nb.classList.add("dirty");
    }
  }
  recruitCount(i);
}
/* Recruitment status line (issue #39), from the game's status word via
   s5save.recruit_state. States: party / active count as recruited;
   initialized means the record exists but the character is not recruited;
   none means no record at all. */
function recruitStatusText(c){
  if(!c.recruitable) return "not recruitable";
  if(c.recruitState === "party") return "Recruited (in party)";
  if(c.recruited) return "Recruited";
  if(c.recruitState === "initialized") return "Not recruited (record initialized only)";
  return "Not recruited";
}
function charFieldLabel(field){
  if(field === "level") return "Level"; if(field === "rec") return "Recruited";
  if(field === "exp") return "EXP"; if(field === "sp") return "SP";
  if(field === "wlv") return "Weapon level";
  if(field.startsWith("ruse")) return "Lv"+(+field.slice(4)+1)+" spell uses";
  if(field.startsWith("sk")) return (SKILL_NAMES[+field.slice(2)]||field)+" rank";
  if(field.startsWith("ss")) return "Skill slot "+(+field.slice(2)+1);
  if(field === "accessory") return "Accessory";
  return { helm:"Helm", body:"Armor", glove:"Gloves", foot:"Boots",
           rhead:"Rune (head)", rright:"Rune (right)", rleft:"Rune (left)" }[field] || field;
}
function charFieldDisplay(field, v){
  if(field === "rec") return v ? "recruited" : "not recruited";
  if(field.startsWith("sk")) return RANK_NAMES[v] || String(v);
  if(field.startsWith("ss")) return v ? (SKILL_NAMES[v-1]||"#"+v) : "empty";
  if(field === "accessory") return (NAMES.accNames||{})[String(v)] || (String(v)==="0"?"Nothing":"#"+v);
  if(["helm","body","glove","foot"].includes(field)) return (NAMES.armorNames[field]||{})[String(v)] || (String(v)==="0"?"Nothing":"#"+v);
  if(["rhead","rright","rleft"].includes(field)) return NAMES.runeNames[String(v)] || (String(v)==="0"?"Nothing":"#"+v);
  return String(v);
}

/* ---------- recruitment guide reference ---------- */
/* Data: Editor/s5_recruit_guide.json, built by Editor/build_recruit_refs.py
   from the cited guide sources. Reference only; it never writes anything. */
function guideHTML(idx){
  const g = GUIDE[String(idx)];
  if(!g) return "";
  return `<div style="padding:2px 16px 4px"><div class="note" style="text-transform:none">
    <b>Recruitment guide:</b> ${esc(g.where)}. ${esc(g.how)}</div>
    ${g.missable ? `<div class="warnbox" style="margin:6px 0 2px">Missable: ${esc(g.missable)}</div>` : ""}
    </div>`;
}
function showRecGuide(i, idx){
  const box = $(`guidenote${i}`); if(!box) return;
  const g = GUIDE[String(idx)];
  box.innerHTML = g ? `<div style="padding:2px 0 4px"><b>${esc(nameOf(idx))}:</b> ${esc(g.where)}. ${esc(g.how)}
    ${g.missable ? `<div class="warnbox" style="margin:6px 0 2px">Missable: ${esc(g.missable)}</div>` : ""}</div>` : "";
}

/* ---------- recruitment roster (issue #39) ---------- */
/* Recruitment state comes from the game's own status records. Earlier
   versions toggled a field at payload +0xD7CA believing it was a
   recruitment bitfield; it is the tail of an unrelated record array, so
   the toggles corrupted data while the display lied
   (RECRUIT_FIELD_AUDIT.md). The roster list doubles as a guide index,
   and each row carries the same per-character toggle as the character
   panel (recruitRowToggleHTML mirrors recruitToggleHTML), writing the
   real status word through the same writeCharField staging. */
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
      <span class="note">greyed = not recruitable</span>
    </div>
    <div class="fnote" style="margin:8px 16px 0">Read from the game's status records (issue #39). To recruit or unrecruit someone, use the toggle on their row. Tap a name here for its recruitment guide entry.</div>
    <div id="pairwarn${i}"></div>
    <div id="guidenote${i}" style="padding:4px 16px 0"></div>
    <div id="recgrid${i}" class="recgrid"></div></div>`;
  $(`recgrid${i}`).innerHTML = r.chars.map(c => {
    const dis = !c.recruitable;
    return `<div class="recrow" data-name="${esc(nameOf(c.idx).toLowerCase())}" style="display:flex;justify-content:space-between;gap:8px;padding:2px 0;cursor:pointer;${dis?"opacity:.4":""}" onclick="showRecGuide(${i},${c.idx})">
      <span class="note" style="text-transform:none">${c.idx}: ${esc(nameOf(c.idx))}</span>
      <span style="display:flex;align-items:center;gap:8px;justify-content:flex-end;flex-wrap:wrap">
        <span class="note" style="text-transform:none" id="recstat${i}_${c.idx}">${recruitStatusText(c)}</span>
        <span id="rectog${i}_${c.idx}" onclick="event.stopPropagation()">${recruitRowToggleHTML(i, c)}</span>
      </span></div>`;
  }).join("");
  recruitCount(i);
}
function recruitCount(i){
  const r = CHARDATA[i]; if(!r) return;
  const chars = r.chars.filter(c => c.recruitable);
  const on = chars.filter(c => c.recruited).length;
  const el = $(`reccount${i}`); if(el) el.textContent = `${on} / ${chars.length} recruited`;
  recruitPairWarn(i);
}
/* Eresh (10) and Euram (108) are mutually exclusive in normal play: the game
   lets you recruit only one of them (issue #24). Both reading recruited is a
   state no played save produces, so this warns: it names the likely sources
   (a change made outside gameplay, or this editor's own recruit toggle) and
   the repair (unrecruit one of them, or restore a backup). */
function recruitPairWarn(i){
  const box = $(`pairwarn${i}`); if(!box) return;
  const r = CHARDATA[i]; if(!r){ box.innerHTML = ""; return; }
  const state = {};
  r.chars.forEach(c => { state[c.idx] = !!c.recruited; });
  box.innerHTML = (state[10] && state[108])
    ? `<div class="warnbox" style="margin:10px 14px 0">⚠ Eresh and Euram are both shown as recruited, but the game only lets you recruit one of them. A save showing both was changed outside normal gameplay (a cheat device, another tool, or the Recruited toggle in this editor). If that was not deliberate, unrecruit one of them or restore a backup.</div>`
    : "";
}
function recruitFilter(i){
  const query = $(`recfilter${i}`).value.trim().toLowerCase();
  document.querySelectorAll(`#recgrid${i} .recrow`).forEach(l => {
    l.style.display = (!query || l.dataset.name.includes(query)) ? "" : "none"; });
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
