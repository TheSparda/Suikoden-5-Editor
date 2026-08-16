#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) editor — local web app.

Runs an HTTP server on your machine and opens a browser tab. Nothing is uploaded.
ISO editing (characters/stats/skills/equipment/runes/prices/Hard Mode/names/text) and
PS2 save editing (hero/castle name + New Game Plus).
"""
import http.server, json, os, socketserver, webbrowser, threading
import s5patch as P
import s5fields as F
import s5save as SV

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".s5editor.json")
PORT = int(os.environ.get("PORT", "8055"))

def load_state():
    try: return json.load(open(STATE))
    except Exception: return {}
def save_state(s):
    try: json.dump(s, open(STATE, "w"))
    except Exception: pass

def _apply_backup_pref():
    """Set the .bak toggle from persisted state (default ON) so writes honor it."""
    on = bool(load_state().get("backups", True))
    P.BACKUPS = on; SV.BACKUPS = on
_apply_backup_pref()

def pick_iso_dialog():
    """Open a native OS file-open dialog on the server machine (it runs locally, so
    the dialog appears on the user's own desktop). macOS uses AppleScript; other
    platforms fall back to tkinter. Returns {path} / {cancelled} / {error}."""
    import sys
    try:
        if sys.platform == "darwin":
            import subprocess
            script = ('set f to choose file with prompt "Select a Suikoden V ISO" '
                      'of type {"iso","bin","img"}\nPOSIX path of f')
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
            out = r.stdout.strip()
            if r.returncode != 0 or not out: return {"cancelled": True}
            return {"path": out}
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title="Select a Suikoden V ISO",
            filetypes=[("PS2 ISO", "*.iso *.bin *.img"), ("All files", "*.*")])
        root.update(); root.destroy()
        return {"path": path} if path else {"cancelled": True}
    except Exception as e:
        return {"error": f"no native file dialog available: {e}"}


def pick_save_dialog():
    """Native file-open dialog for a PS2 memory-card / save file. Same behavior as
    pick_iso_dialog. Returns {path} / {cancelled} / {error}."""
    import sys
    try:
        if sys.platform == "darwin":
            import subprocess
            script = ('set f to choose file with prompt "Select a PS2 save / memory card"\n'
                      'POSIX path of f')
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=300)
            out = r.stdout.strip()
            if r.returncode != 0 or not out: return {"cancelled": True}
            return {"path": out}
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title="Select a PS2 save / memory card",
            filetypes=[("PS2 saves", "*.ps2 *.psu *.psv *.bin *.mcd *.mcr *.max *.cbs *.sps *.xps"),
                       ("All files", "*.*")])
        root.update(); root.destroy()
        return {"path": path} if path else {"cancelled": True}
    except Exception as e:
        return {"error": f"no native file dialog available: {e}"}

PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Suikoden V Editor</title>
<style>
/* ===== Suikoden V — "Falena" themes (Queendom blue + Sun Rune gold) =====
   All colors flow through CSS variables. Default is the dark "Falena Twilight";
   body.light switches to the pale "Sun Rune" parchment theme. */
:root{
 --bg:#0b1524; --bg2:#0e1a2d; --panel:#132340; --panel2:#1b2f52; --raise:#22406e;
 --ink:#eaf1fb; --mut:#93a7c6; --line:#294066;
 --gold:#e6b84e; --gold2:#f4d071; --goldink:#2a1c04;   /* Sun Rune gold buttons/tabs */
 --teal:#4fb0d4;                                        /* Falena water accent */
 --sun:#e8823a;                                         /* Sun Rune orange dividers */
 --input:#0a1526; --thead:#16294a;
 --ok:#67c07a; --bad:#ff7a76; --warn:#e6b84e;
 --chg-bd:#f4d071; --chg-bg:#2a2708;
 --title:"Trajan Pro","Palatino Linotype",Palatino,Georgia,"Times New Roman",serif;
 --shadow:0 3px 14px rgba(0,0,0,.45); --focus:rgba(230,184,78,.30);
 --hdr:60px; --navh:48px;
}
body.light{
 --bg:#dfe7f2; --bg2:#eef3fa; --panel:#f4f7fc; --panel2:#e6edf7; --raise:#d6e2f2;
 --ink:#182a44; --mut:#5a6f90; --line:#c2d1e6;
 --gold:#b9821f; --gold2:#d19c33; --goldink:#fff7e6;
 --teal:#1f7fa6; --sun:#c25a1b;
 --input:#ffffff; --thead:#dde7f4;
 --chg-bd:#b9821f; --chg-bg:#fdf3d6;
 --shadow:0 2px 10px rgba(30,60,110,.20); --focus:rgba(185,130,31,.28);
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
 background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}
a{color:var(--teal)}
header{position:sticky;top:0;z-index:30;height:var(--hdr);display:flex;align-items:center;gap:14px;
 padding:0 18px;background:linear-gradient(180deg,var(--panel2),var(--panel));
 border-bottom:2px solid var(--gold);box-shadow:var(--shadow)}
header .logo{width:26px;height:26px;border-radius:50%;flex:0 0 auto;
 background:radial-gradient(circle at 50% 45%,var(--gold2),var(--sun) 70%,#7a3d12);
 box-shadow:0 0 10px rgba(232,130,58,.6)}
header b{font-family:var(--title);font-size:18px;letter-spacing:.03em;color:var(--gold2)}
header .sp{flex:1}
header .iso{color:var(--mut);font-size:12px;max-width:48vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
nav{position:sticky;top:var(--hdr);z-index:29;display:flex;gap:4px;padding:7px 14px;flex-wrap:wrap;
 background:var(--bg2);border-bottom:1px solid var(--line)}
nav button{background:transparent;color:var(--mut);border:0;padding:8px 15px;border-radius:9px;
 cursor:pointer;font:inherit;transition:.13s}
nav button:hover:not(.on){background:var(--panel2);color:var(--ink)}
nav button.on{background:linear-gradient(180deg,var(--gold2),var(--gold));color:var(--goldink);font-weight:600}
.navdrop{position:relative;display:inline-block}
.navmenu{position:absolute;top:calc(100% + 4px);left:0;min-width:180px;display:none;z-index:40;
 background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:5px;
 box-shadow:0 8px 24px rgba(0,0,0,.35)}
.navmenu.open{display:block}
.navmenu button{display:block;width:100%;text-align:left;border-radius:7px}
#isobar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px 18px;background:var(--bg2);
 border-bottom:1px solid var(--line)}
main{padding:18px;max-width:1080px;margin:0 auto}
.panel{display:none}.panel.on{display:block;animation:fade .18s ease}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1}}
h2{font-family:var(--title);color:var(--gold2);font-size:19px;margin:2px 0 4px}
.sub{color:var(--mut);font-size:12px;margin:0 0 14px}
input,button,select{font:14px system-ui;padding:7px 9px;border-radius:8px;
 border:1px solid var(--line);background:var(--input);color:var(--ink);outline:none}
input:focus,select:focus{border-color:var(--gold);box-shadow:0 0 0 3px var(--focus)}
button{background:linear-gradient(180deg,var(--gold2),var(--gold));border:0;color:var(--goldink);
 font-weight:600;cursor:pointer;transition:filter .12s}
button:hover{filter:brightness(1.07)}button:active{filter:brightness(.94)}
button.ghost{background:var(--raise);color:var(--ink);font-weight:500}
button.mini{padding:4px 8px;font-size:13px}
.row{margin:10px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.note{color:var(--mut);font-size:12px}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.sec{margin:14px 0;border:1px solid var(--line);border-radius:11px;overflow:hidden;background:var(--panel)}
.sec>h3{margin:0;padding:10px 14px;background:var(--panel2);font-size:13px;font-weight:600;
 color:var(--gold2);cursor:pointer;letter-spacing:.03em;text-transform:uppercase;
 display:flex;justify-content:space-between;align-items:center;user-select:none}
.sec>h3::after{content:"▾";color:var(--mut);font-size:11px}
.sec>h3.closed::after{content:"▸"}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;padding:14px}
.fld label{display:block;font-size:11px;color:var(--mut);margin-bottom:3px;text-transform:uppercase;letter-spacing:.03em}
.fld .in{display:flex;gap:5px}.fld input{width:100%}
input.chg,select.chg,input[type=range].chg{border-color:var(--chg-bd);background:var(--chg-bg)}
.fld .in>.mini{display:none}.fld .in.chg>.mini{display:inline-flex}
.sld{display:flex;align-items:center;gap:10px}
.sld input[type=range]{flex:1;accent-color:var(--gold)}
.sld .sldval{min-width:38px;text-align:right;font-variant-numeric:tabular-nums;color:var(--mut);font-size:13px}
.sec.build{border:1px solid var(--gold);box-shadow:0 0 0 1px rgba(230,184,78,.15)}
.setlist{margin:4px 0 0;padding-left:26px}
.setlist li{padding:3px 0}
.setlist li::marker{color:var(--gold);font-weight:700}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
thead th{position:sticky;top:0;background:var(--thead);color:var(--mut);font-size:11px;
 text-transform:uppercase;letter-spacing:.04em;z-index:2}
.scroll{max-height:62vh;overflow:auto;border:1px solid var(--line);border-radius:11px;background:var(--panel)}
pre{background:var(--input);padding:12px;border-radius:9px;overflow:auto;border:1px solid var(--line);margin:0}
.card-hd{padding:12px 14px;background:var(--panel2);border-bottom:1px solid var(--line);
 font-weight:600;color:var(--gold2);border-radius:11px 11px 0 0}
/* checkbox field (Save Editor NG+ etc.) */
.fld .in .chk{display:flex;align-items:center;gap:9px;min-height:38px;text-transform:none;
 letter-spacing:normal;color:var(--ink);cursor:pointer;font-size:14px}
.chk input[type=checkbox]{width:18px;height:18px;flex:0 0 auto;accent-color:var(--gold);cursor:pointer;margin:0}
.card-ft{display:flex;align-items:center;gap:12px;padding:0 14px 14px}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;
 padding:2px 8px;border-radius:999px;vertical-align:middle;color:#0c1524}
.badge.b-ntsc{background:#67c07a}.badge.b-pal{background:#4fb0d4}.badge.b-jp{background:#e6b84e}
/* spinner overlay */
#spin{position:fixed;inset:0;z-index:60;display:none;align-items:center;justify-content:center;
 background:rgba(6,12,22,.45);backdrop-filter:blur(1.5px)}
#spin.on{display:flex}
.sun{width:52px;height:52px;border-radius:50%;border:5px solid rgba(230,184,78,.25);
 border-top-color:var(--gold2);border-right-color:var(--sun);animation:spin .8s linear infinite;
 box-shadow:0 0 18px rgba(232,130,58,.5)}
@keyframes spin{to{transform:rotate(360deg)}}
/* toasts */
#toast{position:fixed;right:18px;bottom:18px;z-index:70;display:flex;flex-direction:column;gap:8px}
.tst{background:var(--panel2);border:1px solid var(--line);border-left:4px solid var(--gold);
 color:var(--ink);padding:10px 14px;border-radius:9px;box-shadow:var(--shadow);max-width:340px;
 animation:tin .2s ease}
.tst.ok{border-left-color:var(--ok)}.tst.bad{border-left-color:var(--bad)}
@keyframes tin{from{opacity:0;transform:translateX(12px)}to{opacity:1}}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;background:var(--raise);color:var(--mut);font-size:11px}
</style></head>
<body>
<header>
 <span class=logo></span><b>Suikoden V</b><span class=note>ISO &amp; Save Editor</span>
 <span class=sp></span>
 <span class=iso id=isoLabel>no ISO loaded</span>
 <label class=chk title="When on, a .bak copy is made before the first write to each file. Turn off to write without backups."><input type=checkbox id=bakToggle checked onchange=toggleBackups()> <span class=note>.bak backups</span></label>
 <button class="ghost mini" onclick=toggleTheme()>◐ Theme</button>
</header>
<nav id=nav>
 <button data-tab=char class=on onclick=showTab('char')>Characters</button>
 <button data-tab=rune onclick=showTab('rune')>Runes &amp; Spells</button>
 <button data-tab=gear onclick=showTab('gear')>Gear</button>
 <button data-tab=mp onclick=showTab('mp')>MP Growth</button>
 <button data-tab=skillfx onclick=showTab('skillfx')>Skill Effects</button>
 <button data-tab=unite onclick=showTab('unite')>Unites</button>
 <button data-tab=save onclick=showTab('save')>Save Editor</button>
 <div class=navdrop>
  <button id=otherbtn onclick="event.stopPropagation();toggleOther()">Other ▾</button>
  <div class=navmenu id=othermenu>
   <button data-tab=enemy onclick=showTab('enemy')>Enemies</button>
   <button data-tab=price onclick=showTab('price')>Prices</button>
   <button data-tab=hard onclick=showTab('hard')>Hard Mode</button>
   <button data-tab=ref onclick=showTab('ref')>Reference / Text</button>
   <button data-tab=tools onclick=showTab('tools')>Tools</button>
  </div>
 </div>
</nav>
<div id=isobar>
 <span class=note>ISO</span>
 <input id=iso size=46 placeholder="/path/to/Suikoden V.iso">
 <button onclick=browseIso()>Browse…</button>
 <button onclick=verify()>Open / Verify</button>
 <button class=ghost id=lastbtn onclick=reopenLast() style=display:none title="">Reopen last ISO</button>
 <span id=status class=note></span>
</div>
<main>
 <section class="panel on" id=p-char>
  <h2>Character Editor</h2>
  <p class=sub>Starting stats, growth/skill ranks, equipment and magic thresholds — verified ISO tables.</p>
  <div class=row id=charrow style=display:none>
   <span class=note>Character</span>
   <select id=csel onchange=loadChar()></select>
   <input id=cfilter size=14 placeholder="filter name / id…" oninput=filterChars()>
   <button onclick=saveChar()>Save changes</button>
   <button class=ghost onclick=revertChar()>Revert</button>
   <span id=csave class=note></span>
  </div>
  <div id=sections></div>
  <p class=note id=charhint>Open your ISO above to begin.</p>
 </section>

 <section class=panel id=p-rune>
  <h2>Runes &amp; Spells</h2>
  <p class=sub>Pick a rune, then use <b>Build spell set</b> to choose which spells it teaches (custom runes), and edit each spell's Element, Power and Target right below.</p>
  <div class=row id=rrow style=display:none>
   <span class=note>Rune</span>
   <select id=rsel onchange=loadRune()></select>
   <input id=rfilter size=14 placeholder="filter name / id…" oninput=filterRunes()>
   <button onclick=saveRune()>Save changes</button>
   <button class=ghost onclick=revertRune()>Revert</button>
   <span id=rsave class=note></span>
  </div>
  <div id=runesections></div>
  <p class=note id=runehint>Open your ISO above to begin.</p>
 </section>

 <section class=panel id=p-gear>
  <h2>Gear (Armor) Editor</h2>
  <p class=sub>Edit armor <b>DEF</b>, <b>buy/sell price</b>, <b>weight Type + SPD penalty</b>, <b>stat bonuses</b>, <b>proc effects</b> (auto-heal / drain / counter / …), and <b>per-element ATK &amp; DEF</b> for all 14 elements. Verified vs the Armor List guide. The summary at the bottom is the game's own description text.</p>
  <div class=row id=gearrow style=display:none>
   <span class=note>Slot</span>
   <select id=gslot onchange=loadGear()>
    <option value=head>Head</option><option value=body selected>Body</option>
    <option value=arm>Arm</option><option value=foot>Foot</option>
    <option value=accessory>Accessory</option>
   </select>
   <span class=note>Piece</span>
   <select id=gsel onchange=loadGearItem()></select>
   <input id=gfilter size=16 placeholder="filter effect / id…" oninput=filterGear()>
   <button onclick=saveGear()>Save changes</button>
   <button class=ghost onclick=revertGear()>Revert</button>
   <span id=gsave class=note></span>
  </div>
  <div id=gearsections></div>
  <p class=note id=gearhint>Open your ISO above to begin.</p>
 </section>

 <section class=panel id=p-enemy>
  <h2>Enemy Editor</h2>
  <p class=sub>Edit enemy <b>Level</b>, combat stats, <b>Potch / Skill-Point rewards</b>, <b>elemental affinities</b> (E–S), and <b>item drops</b> (40/20/10/5/1% slots, picked by item name). Verified vs the game data (Nariqua = Lv45, 1800 HP, drops Drain Piece).</p>
  <div class=row id=enrow style=display:none>
   <span class=note>Enemy</span>
   <select id=ensel onchange=loadEnemy()></select>
   <input id=enfilter size=14 placeholder="filter name / id…" oninput=filterEnemies()>
   <button onclick=saveEnemy()>Save changes</button>
   <button class=ghost onclick=revertEnemy()>Revert</button>
   <span id=ensave class=note></span>
  </div>
  <div id=enemysections></div>
  <p class=note id=enemyhint>Open your ISO above to begin.</p>
 </section>

 <section class=panel id=p-price>
  <h2>Item &amp; Equipment Prices</h2>
  <p class=sub>Buy / sell prices (verified vs stat guide; sell = buy ÷ 2). Records in item-id order.</p>
  <div class=row><button onclick=loadPrices()>Load prices</button>
   <input id=pricefilter size=12 placeholder="min buy…" oninput=priceShow()>
   <span id=pricenote class=note></span></div>
  <div class=scroll id=prices></div>
  <h2 style="margin-top:18px">Rune (Orb) Prices</h2>
  <p class=sub>Buy / sell for each rune orb (verified vs the rune guide; sell = buy ÷ 2; event-only orbs have buy 0).</p>
  <div class=row><input id=runepricefilter size=14 placeholder="filter rune…" oninput=runePriceShow()>
   <span id=runepricenote class=note></span></div>
  <div class=scroll id=runeprices></div>
  <h2 style="margin-top:18px">Healing Item Prices</h2>
  <p class=sub>Buy / sell for medicines, incenses and foods.</p>
  <div class=row><input id=healpricefilter size=14 placeholder="filter item…" oninput=healPriceShow()>
   <span id=healpricenote class=note></span></div>
  <div class=scroll id=healprices></div>
 </section>

 <section class=panel id=p-mp>
  <h2>MP Growth</h2>
  <p class=sub>MP-cost thresholds per magic level (Lv1–Lv4). Each row is one spell level; columns are the MP required as a caster gains more casts of that level. Verified vs the game data. Note: this tunes MP requirements but can't raise the 9/9/7/5 casts-per-level cap. Applies to a NEW GAME.</p>
  <div class=row id=mprow style=display:none><button onclick=loadMP()>Reload</button>
   <span id=mpnote class=note></span></div>
  <div class=scroll id=mpsections></div>
 </section>

 <section class=panel id=p-skillfx>
  <h2>Skill Effects</h2>
  <p class=sub>The magnitude of each skill at every rank (E → SS). Values are the skill's effect (e.g. Attack + is a flat bonus; "% …" skills are percentages, 100 = no change). Global — shared by all units. Verified vs the game data. Applies to a NEW GAME.</p>
  <div class=row id=skillfxrow style=display:none><button onclick=loadSkillfx()>Reload</button>
   <input id=skillfxfilter size=16 placeholder="filter skill…" oninput=skillfxShow()>
   <span id=skillfxnote class=note></span></div>
  <div class=scroll id=skillfxsections></div>
 </section>

 <section class=panel id=p-unite>
  <h2>Unite Attacks</h2>
  <p class=sub>Edit which characters perform each unite. Verified against the Unites guide (49/49). The member count is fixed (the table is packed), but every slot is a dropdown of the full roster. Effects shown are the guide's — the game applies the unite's built-in damage/target. Applies to a NEW GAME.</p>
  <div class=row id=uniterow style=display:none>
   <span class=note>Unite</span>
   <select id=usel onchange=loadUnite()></select>
   <input id=ufilter size=14 placeholder="filter name…" oninput=filterUnites()>
   <button onclick=saveUnite()>Save changes</button>
   <button class=ghost onclick=revertUnite()>Revert</button>
   <span id=unitehint class=note></span>
  </div>
  <div id=unitesections></div>
 </section>

 <section class=panel id=p-hard>
  <h2>Hard Mode</h2>
  <p class=sub>Party-wide starting-stat scaler. Idempotent: scales the original values; Restore is byte-exact.</p>
  <div class=row>
   <span class=note>Factor</span>
   <input id=hmfactor type=number step=0.05 min=0.1 max=10 value=0.5 size=6>
   <button onclick="hardmode(false)">Apply to all characters</button>
   <button class=ghost onclick="hardmode(true)">Restore</button>
  </div>
  <p class=note id=hmstatus>0.5 halves every character's starting stats. A .bak is made before writing.</p>
 </section>

 <section class=panel id=p-ref>
  <h2>Reference &amp; Text Editor</h2>
  <p class=sub>Clean English name lists (Characters, Spells, Skills, Runes, Enemies, Healing Items, Gear) for reference — read-only. The "ELF text · editable" categories are the raw boot-ELF strings (all languages): edit one and press Enter to write it back in place (byte-capped).</p>
  <div class=row>
   <select id=refcat onchange=refShow()></select>
   <input id=reffilter size=20 placeholder="search names…" oninput=refShow()>
   <span id=refcount class=note></span>
  </div>
  <div class=scroll id=refout style=padding:6px>Loading reference…</div>
 </section>

 <section class=panel id=p-save>
  <h2>Save Editor</h2>
  <p class=sub>Edit PS2 memory-card saves. Verified fields: hero name, castle name, and New Game Plus (fast-forward). ECC and a <code>.bak</code> are handled automatically on write.</p>
  <div class=row>
   <button onclick=browseSave()>Open save file…</button>
   <input id=savepath size=40 placeholder="/path/to/save.ps2" onkeydown="if(event.key=='Enter')openSaveFile()">
   <button class=ghost onclick=openSaveFile()>Open</button>
  </div>
  <div class=row>
   <span class=note>or scan a folder</span>
   <input id=saveroot size=32 placeholder="(defaults to ./Saves)">
   <button class=ghost onclick=scanSaves()>Scan for saves</button>
  </div>
  <div id=saves></div>
 </section>

 <section class=panel id=p-tools>
  <h2>Tools</h2>
  <p class=sub>Raw hex read at any absolute ISO offset (research).</p>
  <div class=row>
   <span class=note>Offset</span><input id=roff size=12 value=0x828BD>
   <span class=note>Length</span><input id=rlen size=5 value=16>
   <button class=ghost onclick=peek()>Read</button>
  </div>
  <pre id=out>—</pre>
  <h2 style="margin-top:18px">Overlays (OVL/*.ROM)</h2>
  <p class=sub>The disc's compressed engine overlays (battle, war, minigames…). <b>Extract</b> decompresses one to <code>overlays_extracted/&lt;name&gt;.bin</code>; edit that file in a hex editor / disassembler (keep it the same length), then <b>Re-insert</b> to recompress and write it back into the ISO. Re-insert is guarded (must fit the file's sector slot) and makes a <code>.bak</code> when backups are on.</p>
  <div class=row><button onclick=loadOverlays()>List overlays</button>
   <button class=ghost onclick=extractAllOverlays()>Extract all</button>
   <span id=ovlnote class=note></span></div>
  <div class=scroll id=overlays></div>
  <h2 style="margin-top:18px">Overlay Text</h2>
  <p class=sub>Edit the story / dialogue text inside an overlay (endings, letters, lore, newspaper, minigame lines) — text the boot-ELF editor can't reach. Load an overlay, edit strings in place (byte-capped, press Enter), then <b>Write overlay to ISO</b> to recompress + save. A <code>.bak</code> is made when backups are on.</p>
  <div class=row>
   <select id=otxtsel></select>
   <button onclick=loadOverlayText()>Load text</button>
   <input id=otxtfilter size=16 placeholder="search text…" oninput=overlayTextShow()>
   <button onclick=writeOverlayText()>Write overlay to ISO</button>
   <span id=otxtnote class=note></span>
  </div>
  <div class=scroll id=overlaytext></div>
 </section>
</main>
<div id=spin><div class=sun></div></div>
<div id=toast></div>
<script>
let CHARS=[], CUR=null, ORIG={}, REF={}, PRICES=[], MAPS={items:{},runes:{},armor:{head:{},body:{},glove:{},foot:{}},held:{},ranks:[],grades:[],elements:{},targets:{}};
let SPELLS=[], SCUR=null, SORIG={};
let _busy=0;
function ctrl(r,key){const v=r.value;
 const ch='onchange="this.classList.toggle(\'chg\',this.value!=ORIG[this.dataset.k])"';
 if(r.kind=='rank'||r.kind=='grade'||r.kind=='egrade'){const R=(r.kind=='grade'?MAPS.grades:r.kind=='egrade'?MAPS.egrades:MAPS.ranks)||[];let o='';
  const gp=(i=>'');  // show just the tier letter (affinity grades and skill ranks)
  if(v<0||v>=R.length)o+=`<option value=${v} selected>${v} · (raw)</option>`;
  for(let i=0;i<R.length;i++)o+=`<option value=${i} ${i==v?'selected':''}>${gp(i)}${R[i]}</option>`;
  return `<select data-k="${key}" ${ch}>${o}</select>`;}
 if(r.kind=='drop'){const D=MAPS.dropitems||{};let o='';
  const cur=+v, nm=D[cur];
  if(!nm&&cur)o+=`<option value=${cur} selected>0x${cur.toString(16)} · (unknown)</option>`;
  o+=`<option value=0 ${cur==0?'selected':''}>— none —</option>`;
  Object.keys(D).forEach(k=>{o+=`<option value=${k} ${k==cur?'selected':''}>${D[k]}</option>`});
  return `<select data-k="${key}" ${ch}>${o}</select>`;}
 if(r.kind=='item'||r.kind=='rune'||r.kind=='element'||r.kind=='target'||r.kind=='spellstatus'||r.kind=='helditem'||r.kind.indexOf&&r.kind.indexOf('armor')==0){
  const A=MAPS.armor||{};
  const it=({item:MAPS.items,rune:MAPS.runes,element:MAPS.elements,target:MAPS.targets,spellstatus:MAPS.spellstatus,helditem:MAPS.held,
             armorhead:A.head,armorbody:A.body,armorarm:A.glove,armorfoot:A.foot}[r.kind])||{};
  const nm=id=>{const e=it[id];return e?(e.name||e):('#'+id)};
  const hideId=r.kind=='helditem'||(r.kind.indexOf&&r.kind.indexOf('armor')==0);  // hide raw id for held items + armor slots
  const pfx=hideId?(id=>''):(id=>id+' · ');
  let o=`<option value=${v} selected>${pfx(v)}${nm(v)}</option>`;
  Object.keys(it).forEach(id=>{if(+id!=v)o+=`<option value=${id}>${pfx(id)}${nm(id)}</option>`});
  return `<select data-k="${key}" ${ch}>${o}</select>`;}
 if(r.kind=='spellid'){let o='';const cur=+v;
  if(cur<0||cur>=SPELLS.length)o+=`<option value=${v} selected>${v} · #${v}</option>`;
  SPELLS.forEach((n,i)=>o+=`<option value=${i} ${i==cur?'selected':''}>${i} · ${n}</option>`);
  return `<select data-k="${key}" ${ch}>${o}</select>`;}
 if(r.kind=='slider'){const max=r.width==1?255:65535;
  return `<div class=sld><input type=range min=0 max=${max} value=${v} data-k="${key}" `+
   `oninput="this.classList.toggle('chg',this.value!=ORIG[this.dataset.k]);this.nextElementSibling.textContent=this.value"><span class=sldval>${v}</span></div>`;}
 const max=r.width==1?255:65535;
 return `<input type=number min=0 max=${max} value=${v} data-k="${key}" oninput="this.classList.toggle('chg',this.value!=ORIG[this.dataset.k])">`;}
function spin(on){_busy+=on?1:-1;document.getElementById('spin').classList.toggle('on',_busy>0)}
function toast(msg,kind){const t=document.createElement('div');t.className='tst '+(kind||'');t.textContent=msg;
 document.getElementById('toast').appendChild(t);setTimeout(()=>{t.style.opacity=0;setTimeout(()=>t.remove(),300)},3200)}
async function j(u,b){spin(true);try{
 const r=await fetch(u,{method:b?'POST':'GET',body:b&&JSON.stringify(b),headers:{'content-type':'application/json'}});
 return await r.json();}catch(e){toast('Request failed: '+e,'bad');return{error:String(e)}}finally{spin(false)}}
function iso(){return document.getElementById('iso').value}
let LASTISO='';
async function browseIso(){const r=await j('/api/pickiso',{});
 if(r&&r.path){document.getElementById('iso').value=r.path;verify();}
 else if(r&&r.error)toast(r.error,'bad');}
function reopenLast(){if(LASTISO){document.getElementById('iso').value=LASTISO;verify();}}
function needIso(){if(!iso()){toast('Open your ISO first','bad');showTab('char');return false}return true}
function showTab(name){document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.dataset.tab==name));
 document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('on',p.id=='p-'+name));
 const ob=document.getElementById('otherbtn');if(ob)ob.classList.toggle('on',['enemy','price','hard','ref','tools'].includes(name));
 const om=document.getElementById('othermenu');if(om)om.classList.remove('open');}
function toggleOther(){document.getElementById('othermenu').classList.toggle('open');}
document.addEventListener('click',e=>{const d=document.querySelector('.navdrop');
 if(d&&!d.contains(e.target)){const om=document.getElementById('othermenu');if(om)om.classList.remove('open');}});
async function toggleBackups(){const on=document.getElementById('bakToggle').checked;
 const r=await j('/api/backups',{on});
 toast(on?'Backups ON — a .bak is made before writes':'Backups OFF — writes make no .bak', on?'ok':'bad');}
function toggleTheme(){const l=document.body.classList.toggle('light');
 try{localStorage.s5theme=l?'light':'dark'}catch(e){}}

async function verify(){const s=await j('/api/verify',{iso:iso()});
 const el=document.getElementById('status');const msg=s.msg||s.error||'error';
 el.textContent=(s.ok?'✓ ':'✗ ')+msg;el.className='note '+(s.ok?'ok':'bad');
 if(s.ok){document.getElementById('isoLabel').textContent=iso();toast(msg,'ok');
  LASTISO=iso();{const lb=document.getElementById('lastbtn');if(lb){lb.style.display='';lb.title=iso();}}
  CHARS=(await j('/api/chars',{iso:iso()})).chars;fillChars();
  document.getElementById('charrow').style.display='';
  document.getElementById('charhint').textContent=MAPS.globalHelp||'';loadChar();
  if(!SPELLS.length)SPELLS=(await j('/api/spells',{})).spells||[];
  const rr=await j('/api/runes',{iso:iso()});RUNES=rr.runes||[];
  fillRunes();document.getElementById('rrow').style.display='';
  document.getElementById('runehint').textContent='';loadRune();
  ENEMIES=(await j('/api/enemies',{iso:iso()})).enemies||[];
  fillEnemies();document.getElementById('enrow').style.display='';
  document.getElementById('enemyhint').textContent='';loadEnemy();
  document.getElementById('gearrow').style.display='';
  document.getElementById('gearhint').textContent='';loadGear();
  loadMP();loadSkillfx();loadUnites();}
 else toast(s.msg,'bad');}
function fillChars(){const sel=document.getElementById('csel'),f=(document.getElementById('cfilter').value||'').toLowerCase();
 const match=c=>!f||c.name.toLowerCase().includes(f)||(''+c.id).includes(f);
 const opt=c=>`<option value="${c.id}">${c.id} — ${c.name}</option>`;
 const real=CHARS.filter(c=>c.hasStats!==false&&match(c));   // hasStats undefined (no ISO) => treated as real
 const none=CHARS.filter(c=>c.hasStats===false&&match(c));
 let h=real.map(opt).join('');
 if(none.length)h+=`<optgroup label="── No editable stats (story / support / boss) ──">`+none.map(opt).join('')+`</optgroup>`;
 sel.innerHTML=h;}
function filterChars(){fillChars();loadChar();}
async function loadChar(){const sel=document.getElementById('csel');if(!sel.value)return;
 CUR=parseInt(sel.value);const s=await j('/api/char',{iso:iso(),id:CUR});
 const secs=document.getElementById('sections');
 if(s.error){secs.innerHTML='<p class=bad>'+s.error+'</p>';return}
 ORIG={};secs.innerHTML='';
 // Units with an all-zero stats block aren't editable combat units (story/boss, support,
 // or level-scaled recruit). Hide ALL editable sections (stats, weapon growth, equipment,
 // items) and show a note — editing their data does nothing in-game.
 const curc=CHARS.find(c=>c.id===CUR);
 if(curc&&curc.hasStats===false){
  secs.innerHTML='<div class=sec><h3>not editable</h3><div class=note style="padding:12px">'+
   curc.name+' has no editable data — it\'s a story/boss, support, or level-scaled recruit '+
   '(all zero in the ISO). Its combat data, if any, lives in the Enemy tab.</div></div>';
  document.getElementById('csave').textContent='';return;}
 for(const [tbl,rows] of Object.entries(s.tables)){
  const div=document.createElement('div');div.className='sec';
  const g=document.createElement('div');g.className='grid';
  const hp=(MAPS.help&&MAPS.help[tbl])?`<div class=note style="grid-column:1/-1;margin:-2px 0 2px">${MAPS.help[tbl]}</div>`:'';
  g.innerHTML=hp;
  rows.forEach(r=>{const key=tbl+'|'+r.label;ORIG[key]=r.value;
   g.innerHTML+=`<div class=fld><label>${r.label} <span class=pill>${r.kind=='rank'?'rank':r.kind=='grade'?'grade':(r.kind=='item'||r.kind=='helditem'||r.kind.indexOf&&r.kind.indexOf('armor')==0)?'item':r.width+'B'}</span></label>`+
    `<div class=in>${ctrl(r,key)}`+
    `<button class="ghost mini" title=restore onclick=restoreField(this) data-k="${key}">↺</button></div></div>`;});
  div.innerHTML=`<h3 onclick=toggleSec(this)>${tbl}</h3>`;div.appendChild(g);secs.appendChild(div);}
 if(s.rawStats){const rd=document.createElement('div');rd.className='sec';
  rd.innerHTML=`<h3 class=closed onclick=toggleSec(this)>raw bytes · stats @0x${(s.rawOff||0).toString(16)}</h3>`+
   `<div style="display:none;padding:12px"><pre style="white-space:pre-wrap">${s.rawStats}</pre></div>`;
  secs.appendChild(rd);}
 document.getElementById('csave').textContent='';refreshDirty();}
function toggleSec(h){h.classList.toggle('closed');const b=h.nextElementSibling;
 b.style.display=b.style.display=='none'?(b.className=='grid'?'grid':'block'):'none';}
// Recompute dirty state everywhere; a field's ↺ button only shows when it differs
// from its section's saved original. Also fixes rune/enemy highlight (correct map).
function refreshDirty(){[['#sections',ORIG],['#runesections',RORIG],['#enemysections',ENORIG],['#gearsections',GEARORIG]].forEach(function(p){
 const MAP=p[1]||{};document.querySelectorAll(p[0]+' input[data-k],'+p[0]+' select[data-k]').forEach(function(i){
  const inn=i.closest('.in'),d=String(i.value)!=String(MAP[i.dataset.k]);
  i.classList.toggle('chg',d);if(inn)inn.classList.toggle('chg',d);});});}
document.addEventListener('input',refreshDirty);document.addEventListener('change',refreshDirty);
function restoreField(btn){const i=document.querySelector('#sections [data-k="'+btn.dataset.k+'"]');
 if(i){i.value=ORIG[btn.dataset.k];}refreshDirty();}
function revertChar(){document.querySelectorAll('#sections [data-k]').forEach(i=>{i.value=ORIG[i.dataset.k]});refreshDirty();toast('Reverted unsaved changes')}
async function saveChar(){if(!needIso())return;const edits=[];
 document.querySelectorAll('#sections [data-k]').forEach(i=>{if(i.value!=ORIG[i.dataset.k]){const[t,f]=i.dataset.k.split('|');edits.push({table:t,field:f,value:parseInt(i.value)})}});
 if(!edits.length){toast('No changes to save');return}
 const s=await j('/api/setchar',{iso:iso(),id:CUR,edits});
 if(s.error)toast('Error: '+s.error,'bad');else{toast('Saved '+edits.length+' field(s)','ok');loadChar()}}

// ---- Runes & Spells: pick a rune, edit its spells inline (no spell dropdown) ----
function pillFor(r){return r.kind=='element'?'element':r.kind=='target'?'target':r.kind=='spellstatus'?'status':r.width+'B';}
let RUNES=[], RCUR=null, RORIG={};
function fillRunes(){const sel=document.getElementById('rsel'),f=(document.getElementById('rfilter').value||'').toLowerCase();
 sel.innerHTML=RUNES.filter(r=>!f||r.name.toLowerCase().includes(f)||(''+r.id).includes(f))
  .map(r=>`<option value="${r.id}">${r.id} — ${r.name}</option>`).join('');}
function filterRunes(){fillRunes();loadRune();}
function fldHTML(r,key){return `<div class=fld><label>${r.label} <span class=pill>${pillFor(r)}</span></label>`+
  `<div class=in>${ctrl(r,key)}`+
  `<button class="ghost mini" title=restore onclick=restoreRuneField(this) data-k="${key}">↺</button></div></div>`;}
async function loadRune(){const sel=document.getElementById('rsel');if(!sel.value)return;
 RCUR=parseInt(sel.value);const s=await j('/api/rune',{iso:iso(),id:RCUR});
 const box=document.getElementById('runesections');
 if(s.error){box.innerHTML='<p class=bad>'+s.error+'</p>';return}
 RORIG={};box.innerHTML='';
 // one editable section per spell the rune currently teaches (restat inline)
 (s.spells||[]).forEach(sp=>{const sec=document.createElement('div');sec.className='sec';
  const g=document.createElement('div');g.className='grid';
  sp.fields.forEach(r=>{const key=`sp${sp.id}|${r.label}`;RORIG[key]=r.value;g.innerHTML+=fldHTML(r,key);});
  sec.innerHTML=`<h3 onclick=toggleSec(this)>Lv${sp.level} · ${sp.name} <span class=note>(spell ${sp.id})</span></h3>`;
  sec.appendChild(g);box.appendChild(sec);});
 if(!s.spells||!s.spells.length){box.insertAdjacentHTML('beforeend','<p class=note>This rune teaches no spells.</p>');}
 // SPELL SET BUILDER (real grant records only): collapsed, below the spells.
 if(s.grant&&s.grant.length){const sec=document.createElement('div');sec.className='sec build';
  const g=document.createElement('div');g.className='grid';g.style.display='none';
  const hp='<div class=note style="grid-column:1/-1;margin:-2px 0 6px">Choose which spells this rune teaches. Spells are a contiguous block, so pick the <b>first spell</b> and how many <b>levels</b>. Save to apply — the editable spells above then refresh.</div>';
  g.innerHTML=hp;
  s.grant.forEach(r=>{const key=`grant|${r.label}`;RORIG[key]=r.value;
   const disp=r.label=='Start spell'?'First spell (Lv1)':r.label=='Spell count'?'Levels':r.label;
   g.innerHTML+=fldHTML({...r,label:disp},key);});
  g.innerHTML+='<div style="grid-column:1/-1"><div class=note style="margin:2px 0">This rune will teach:</div><ol id=setpreview class=setlist></ol></div>';
  sec.innerHTML='<h3 class=closed onclick=toggleSec(this)>⚙ Build spell set (change which spells)</h3>';sec.appendChild(g);box.appendChild(sec);
  g.querySelectorAll('[data-k]').forEach(el=>{el.addEventListener('input',runeSetPreview);el.addEventListener('change',runeSetPreview);});}
 runeSetPreview();refreshDirty();document.getElementById('rsave').textContent='';}
function runeSetPreview(){const sEl=document.querySelector('#runesections [data-k="grant|Start spell"]');
 const cEl=document.querySelector('#runesections [data-k="grant|Spell count"]');
 const box=document.getElementById('setpreview');if(!sEl||!cEl||!box)return;
 const start=+sEl.value,cnt=+cEl.value;let items='';
 for(let k=0;k<cnt;k++){const id=start+k;items+=`<li><b>${SPELLS[id]||('#'+id)}</b> <span class=note>(spell ${id})</span></li>`;}
 box.innerHTML=items||'<li class=note>(no spells)</li>';}
function restoreRuneField(btn){const i=document.querySelector('#runesections [data-k="'+btn.dataset.k+'"]');
 if(i){i.value=RORIG[btn.dataset.k];}refreshDirty();runeSetPreview();}
function revertRune(){document.querySelectorAll('#runesections [data-k]').forEach(i=>{i.value=RORIG[i.dataset.k]});refreshDirty();runeSetPreview();toast('Reverted unsaved changes')}
async function saveRune(){if(!needIso())return;
 const runeEdits=[],spellEdits={};
 document.querySelectorAll('#runesections [data-k]').forEach(i=>{if(i.value==RORIG[i.dataset.k])return;
  const k=i.dataset.k;
  if(k.indexOf('grant|')==0){runeEdits.push({field:k.slice(6),value:parseInt(i.value)});}
  else{const m=k.match(/^sp(\d+)\|(.+)$/);if(m){(spellEdits[m[1]]=spellEdits[m[1]]||[]).push({field:m[2],value:parseInt(i.value)});}}});
 const total=runeEdits.length+Object.values(spellEdits).reduce((a,e)=>a+e.length,0);
 if(!total){toast('No changes to save');return}
 for(const sid in spellEdits){const r=await j('/api/setspell',{iso:iso(),id:+sid,edits:spellEdits[sid]});
  if(r.error){toast('Error: '+r.error,'bad');return}}
 if(runeEdits.length){const r=await j('/api/setrune',{iso:iso(),id:RCUR,edits:runeEdits});
  if(r.error){toast('Error: '+r.error,'bad');return}}
 toast('Saved '+total+' field(s)','ok');loadRune();}

// ---- Enemy editor ----
let ENEMIES=[], ENCUR=null, ENORIG={};
function fillEnemies(){const sel=document.getElementById('ensel'),f=(document.getElementById('enfilter').value||'').toLowerCase();
 sel.innerHTML=ENEMIES.filter(e=>!f||e.name.toLowerCase().includes(f)||(''+e.id).includes(f))
  .map(e=>`<option value="${e.id}">${e.id} — ${e.name} (HP ${e.hp})</option>`).join('');}
function filterEnemies(){fillEnemies();loadEnemy();}
async function loadEnemy(){const sel=document.getElementById('ensel');if(!sel.value)return;
 ENCUR=parseInt(sel.value);const s=await j('/api/enemy',{iso:iso(),id:ENCUR});
 const box=document.getElementById('enemysections');
 if(s.error){box.innerHTML='<p class=bad>'+s.error+'</p>';return}
 ENORIG={};const g=document.createElement('div');g.className='grid';
 s.fields.forEach(r=>{const key='en|'+r.label;ENORIG[key]=r.value;
  g.innerHTML+=`<div class=fld><label>${r.label} <span class=pill>${r.kind=='egrade'?'grade':r.kind=='drop'?'item':r.width+'B'}</span></label>`+
   `<div class=in>${ctrl(r,key)}`+
   `<button class="ghost mini" title=restore onclick=restoreEnemyField(this) data-k="${key}">↺</button></div></div>`;});
 box.innerHTML='';const sec=document.createElement('div');sec.className='sec';
 sec.innerHTML='<h3 onclick=toggleSec(this)>enemy stats & drops</h3>';sec.appendChild(g);box.appendChild(sec);
 box.insertAdjacentHTML('beforeend',`<div class=sec><h3 class=closed onclick=toggleSec(this)>raw bytes · unit @0x${(s.rawOff||0).toString(16)}</h3><div style="display:none;padding:12px"><pre style="white-space:pre-wrap">${s.raw}</pre></div></div>`);
 document.getElementById('ensave').textContent='';refreshDirty();}
function restoreEnemyField(btn){const i=document.querySelector('#enemysections [data-k="'+btn.dataset.k+'"]');
 if(i){i.value=ENORIG[btn.dataset.k];}refreshDirty();}
function revertEnemy(){document.querySelectorAll('#enemysections [data-k]').forEach(i=>{i.value=ENORIG[i.dataset.k]});refreshDirty();toast('Reverted unsaved changes')}
async function saveEnemy(){if(!needIso())return;const edits=[];
 document.querySelectorAll('#enemysections [data-k]').forEach(i=>{if(i.value!=ENORIG[i.dataset.k]){const[,f]=i.dataset.k.split('|');edits.push({field:f,value:parseInt(i.value)})}});
 if(!edits.length){toast('No changes to save');return}
 const s=await j('/api/setenemy',{iso:iso(),id:ENCUR,edits});
 if(s.error)toast('Error: '+s.error,'bad');else{toast('Saved '+edits.length+' field(s)','ok');loadEnemy()}}

// ---- Gear (Armor) editor ----
let GEAR=[], GCUR=null, GEARORIG={};
function gslot(){return document.getElementById('gslot').value;}
async function loadGear(){if(!iso())return;const s=await j('/api/gear',{iso:iso(),slot:gslot()});
 GEAR=s.items||[];fillGearSel();loadGearItem();}
function fillGearSel(){const sel=document.getElementById('gsel'),f=(document.getElementById('gfilter').value||'').toLowerCase();
 sel.innerHTML=GEAR.filter(e=>!f||(e.name||'').toLowerCase().includes(f)||(e.effect||'').toLowerCase().includes(f)||(''+e.id).includes(f))
  .map(e=>`<option value="${e.id}">${e.name}</option>`).join('');}
function filterGear(){fillGearSel();loadGearItem();}
async function loadGearItem(){const sel=document.getElementById('gsel');if(!sel.value)return;
 GCUR=parseInt(sel.value);const s=await j('/api/gearitem',{iso:iso(),slot:gslot(),id:GCUR});
 const box=document.getElementById('gearsections');
 if(s.error){box.innerHTML='<p class=bad>'+s.error+'</p>';return}
 GEARORIG={};const g=document.createElement('div');g.className='grid';
 s.fields.forEach(r=>{const key='g|'+r.label;GEARORIG[key]=r.value;
  g.innerHTML+=`<div class=fld><label>${r.label} <span class=pill>${r.signed?'±':''}${r.width}B</span></label>`+
   `<div class=in>${ctrl(r,key)}`+
   `<button class="ghost mini" title=restore onclick=restoreGearField(this) data-k="${key}">↺</button></div></div>`;});
 box.innerHTML='';const sec=document.createElement('div');sec.className='sec';
 const nm=s.nameEn||s.name||'armor stats';
 sec.innerHTML=`<h3 onclick=toggleSec(this)>${nm}</h3>`;sec.appendChild(g);box.appendChild(sec);
 const eff=s.summaryEn||'(none)';
 box.insertAdjacentHTML('beforeend',`<div class=sec><h3>in-game description (read-only)</h3><div class=note style="padding:12px">${eff}<br><br>This is the game's own summary text. The element resists &amp; procs it mentions are editable in the fields above (the text itself won't change).</div></div>`);
 document.getElementById('gsave').textContent='';refreshDirty();}
function restoreGearField(btn){const i=document.querySelector('#gearsections [data-k="'+btn.dataset.k+'"]');
 if(i){i.value=GEARORIG[btn.dataset.k];}refreshDirty();}
function revertGear(){document.querySelectorAll('#gearsections [data-k]').forEach(i=>{i.value=GEARORIG[i.dataset.k]});refreshDirty();toast('Reverted unsaved changes')}
async function saveGear(){if(!needIso())return;const edits=[];
 document.querySelectorAll('#gearsections [data-k]').forEach(i=>{if(i.value!=GEARORIG[i.dataset.k]){const[,f]=i.dataset.k.split('|');edits.push({field:f,value:parseInt(i.value)})}});
 if(!edits.length){toast('No changes to save');return}
 const s=await j('/api/setgear',{iso:iso(),slot:gslot(),id:GCUR,edits});
 if(s.error)toast('Error: '+s.error,'bad');else{toast('Saved '+edits.length+' field(s)','ok');loadGearItem()}}

async function loadPrices(){if(!needIso())return;const s=await j('/api/prices',{iso:iso()});
 if(s.error){toast(s.error,'bad');return}PRICES=s.prices.filter(p=>p.buy||p.sell);priceShow();toast('Loaded '+PRICES.length+' priced items','ok');
 const rp=await j('/api/runeprices',{iso:iso()});if(!rp.error){RUNEPRICES=rp.prices||[];runePriceShow();}
 const hp=await j('/api/healprices',{iso:iso()});if(!hp.error){HEALPRICES=hp.prices||[];healPriceShow();}}
let RUNEPRICES=[], HEALPRICES=[];
function healPriceShow(){const f=(document.getElementById('healpricefilter').value||'').toLowerCase();
 const rows=HEALPRICES.filter(p=>!f||p.name.toLowerCase().includes(f));
 document.getElementById('healpricenote').textContent=rows.length+' items';
 let h='<table><thead><tr><th>#</th><th>Item</th><th>Buy</th><th>Sell</th></tr></thead><tbody>';
 rows.forEach(p=>{h+=`<tr><td class=note>${p.index}</td><td>${p.name}</td>`+
  `<td><input type=number value=${p.buy} data-i=${p.index} data-f=buy size=8 onchange=setHealPrice(this)></td>`+
  `<td><input type=number value=${p.sell} data-i=${p.index} data-f=sell size=8 onchange=setHealPrice(this)></td></tr>`});
 document.getElementById('healprices').innerHTML=h+'</tbody></table>';}
async function setHealPrice(inp){const r=await j('/api/sethealprice',{iso:iso(),index:parseInt(inp.dataset.i),field:inp.dataset.f,value:parseInt(inp.value)});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast('Item #'+inp.dataset.i+' '+inp.dataset.f+' saved','ok')}
function runePriceShow(){const f=(document.getElementById('runepricefilter').value||'').toLowerCase();
 const rows=RUNEPRICES.filter(p=>!f||p.name.toLowerCase().includes(f));
 document.getElementById('runepricenote').textContent=rows.length+' runes';
 let h='<table><thead><tr><th>#</th><th>Rune</th><th>Buy</th><th>Sell</th></tr></thead><tbody>';
 rows.forEach(p=>{h+=`<tr><td class=note>${p.index}</td><td>${p.name}</td>`+
  `<td><input type=number value=${p.buy} data-i=${p.index} data-f=buy size=8 onchange=setRunePrice(this)></td>`+
  `<td><input type=number value=${p.sell} data-i=${p.index} data-f=sell size=8 onchange=setRunePrice(this)></td></tr>`});
 document.getElementById('runeprices').innerHTML=h+'</tbody></table>';}
async function setRunePrice(inp){const r=await j('/api/setruneprice',{iso:iso(),index:parseInt(inp.dataset.i),field:inp.dataset.f,value:parseInt(inp.value)});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast('Rune #'+inp.dataset.i+' '+inp.dataset.f+' saved','ok')}
function priceShow(){const min=parseInt(document.getElementById('pricefilter').value||'0')||0;
 const rows=PRICES.filter(p=>p.buy>=min);
 document.getElementById('pricenote').textContent=rows.length+' items';
 let h='<table><thead><tr><th>#</th><th>Item</th><th>Buy</th><th>Sell</th></tr></thead><tbody>';
 rows.slice(0,400).forEach(p=>{const it=MAPS.items[p.index];h+=`<tr><td class=note>${p.index}</td>`+
  `<td>${it?it.name:'<span class=note>—</span>'}${it&&it.desc?' <span class=note>· '+it.desc+'</span>':''}</td>`+
  `<td><input type=number value=${p.buy} data-i=${p.index} data-f=buy size=8 onchange=setPrice(this)></td>`+
  `<td><input type=number value=${p.sell} data-i=${p.index} data-f=sell size=8 onchange=setPrice(this)></td></tr>`});
 document.getElementById('prices').innerHTML=h+'</tbody></table>';}
async function setPrice(inp){const r=await j('/api/setprice',{iso:iso(),index:parseInt(inp.dataset.i),field:inp.dataset.f,value:parseInt(inp.value)});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast('Price #'+inp.dataset.i+' '+inp.dataset.f+' saved','ok')}

// ---- MP growth: 4 magic-level rows x 9 MP-cost thresholds ----
let MPFIELDS=[];
async function loadMP(){if(!iso())return;const s=await j('/api/mp',{iso:iso()});
 const box=document.getElementById('mpsections');
 if(s.error){box.innerHTML='<p class=bad>'+s.error+'</p>';return}
 MPFIELDS=s.fields||[];document.getElementById('mprow').style.display='';
 document.getElementById('mpnote').textContent=(s.groups||[]).length+' magic levels';
 let h='<table><thead><tr><th>Level</th>'+MPFIELDS.map(f=>`<th>${f}</th>`).join('')+'</tr></thead><tbody>';
 (s.groups||[]).forEach(gr=>{h+=`<tr><td class=note>${gr.label}</td>`+
  gr.values.map((v,k)=>`<td><input type=number min=0 max=65535 value=${v} data-g=${gr.group} data-i=${k} size=6 onchange=setMP(this)></td>`).join('')+'</tr>'});
 box.innerHTML=h+'</tbody></table>';}
async function setMP(inp){const r=await j('/api/setmp',{iso:iso(),group:parseInt(inp.dataset.g),idx:parseInt(inp.dataset.i),value:parseInt(inp.value)});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast(MPFIELDS[inp.dataset.i]+' saved','ok')}

// ---- Skill effects: 165 skills x 7 rank magnitudes (E..SS) ----
let SKILLFX=[], SKILLFXRANKS=[];
async function loadSkillfx(){if(!iso())return;const s=await j('/api/skillfx',{iso:iso()});
 if(s.error){document.getElementById('skillfxsections').innerHTML='<p class=bad>'+s.error+'</p>';return}
 SKILLFX=s.skills||[];SKILLFXRANKS=s.ranks||[];document.getElementById('skillfxrow').style.display='';skillfxShow();}
function skillfxShow(){const f=(document.getElementById('skillfxfilter').value||'').toLowerCase();
 const rows=SKILLFX.filter(s=>!f||s.name.toLowerCase().includes(f)||(''+s.id).includes(f));
 document.getElementById('skillfxnote').textContent=rows.length+' skills';
 let h='<table><thead><tr><th>#</th><th>Skill</th>'+SKILLFXRANKS.map(r=>`<th>${r}</th>`).join('')+'</tr></thead><tbody>';
 rows.forEach(s=>{h+=`<tr><td class=note>${s.id}</td><td>${s.name}</td>`+
  s.values.map((v,k)=>`<td><input type=number min=0 max=65535 value=${v} data-i=${s.id} data-r=${k} size=6 onchange=setSkillfx(this)></td>`).join('')+'</tr>'});
 document.getElementById('skillfxsections').innerHTML=h+'</tbody></table>';}
async function setSkillfx(inp){const r=await j('/api/setskillfx',{iso:iso(),id:parseInt(inp.dataset.i),rank:parseInt(inp.dataset.r),value:parseInt(inp.value)});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast('Skill #'+inp.dataset.i+' '+SKILLFXRANKS[inp.dataset.r]+' saved','ok')}

// ---- Unites: pick a unite, swap its member slots ----
let UNITES=[], UCUR=null, UORIG={}, UROSTER=[];
async function loadUnites(){if(!iso())return;const s=await j('/api/unites',{iso:iso()});
 if(s.error){document.getElementById('unitesections').innerHTML='<p class=bad>'+s.error+'</p>';return}
 UNITES=s.unites||[];UROSTER=s.roster||[];
 document.getElementById('uniterow').style.display='';fillUnites();loadUnite();}
function fillUnites(){const sel=document.getElementById('usel'),f=(document.getElementById('ufilter').value||'').toLowerCase();
 sel.innerHTML=UNITES.filter(u=>!f||u.name.toLowerCase().includes(f)||(''+u.id).includes(f))
  .map(u=>`<option value="${u.id}">${u.id} — ${u.name}</option>`).join('');}
function filterUnites(){fillUnites();loadUnite();}
function loadUnite(){const sel=document.getElementById('usel');if(!sel.value)return;
 UCUR=parseInt(sel.value);const u=UNITES.find(x=>x.id===UCUR);if(!u)return;
 const box=document.getElementById('unitesections');UORIG={};
 const opts=v=>UROSTER.map(c=>`<option value=${c.id} ${c.id==v?'selected':''}>${c.name}</option>`).join('');
 let g='<div class=sec><h3>'+u.name+' <span class=note>· '+u.count+' members</span></h3><div class=grid>';
 g+=`<div class=note style="grid-column:1/-1;margin:-2px 0 2px">${u.effect||''}</div>`;
 u.ids.forEach((v,k)=>{const key='u|'+k;UORIG[key]=v;
  g+=`<div class=fld><label>Member ${k+1} <span class=pill>char</span></label>`+
   `<div class=in><select data-k="${key}">${opts(v)}</select>`+
   `<button class="ghost mini" title=restore onclick=restoreUniteField(this) data-k="${key}">↺</button></div></div>`;});
 box.innerHTML=g+'</div></div>';refreshUniteDirty();}
function refreshUniteDirty(){document.querySelectorAll('#unitesections select[data-k]').forEach(i=>{
 const inn=i.closest('.in'),d=String(i.value)!=String(UORIG[i.dataset.k]);
 i.classList.toggle('chg',d);if(inn)inn.classList.toggle('chg',d);});}
document.addEventListener('change',e=>{if(e.target.closest&&e.target.closest('#unitesections'))refreshUniteDirty();});
function restoreUniteField(btn){const i=document.querySelector('#unitesections [data-k="'+btn.dataset.k+'"]');
 if(i){i.value=UORIG[btn.dataset.k];}refreshUniteDirty();}
function revertUnite(){document.querySelectorAll('#unitesections [data-k]').forEach(i=>{i.value=UORIG[i.dataset.k]});refreshUniteDirty();toast('Reverted unsaved changes')}
async function saveUnite(){if(!needIso())return;const edits=[];
 document.querySelectorAll('#unitesections [data-k]').forEach(i=>{if(i.value!=UORIG[i.dataset.k]){const slot=parseInt(i.dataset.k.split('|')[1]);edits.push({slot,charId:parseInt(i.value)})}});
 if(!edits.length){toast('No changes to save');return}
 const s=await j('/api/setunite',{iso:iso(),id:UCUR,edits});
 if(s.error)toast('Error: '+s.error,'bad');else{toast('Saved '+edits.length+' member(s)','ok');await loadUnites();
  document.getElementById('usel').value=UCUR;loadUnite();}}

async function hardmode(restore){if(!needIso())return;
 const factor=parseFloat(document.getElementById('hmfactor').value);
 if(!restore && !confirm('Scale ALL characters’ starting stats ×'+factor+'?  (.bak made; Restore available)'))return;
 const r=await j('/api/hardmode',{iso:iso(),factor,restore});
 if(r.error){toast('Error: '+r.error,'bad');return}
 const m=restore?('Restored '+r.n+' characters'):('Scaled '+r.n+' characters ×'+factor);
 document.getElementById('hmstatus').textContent=m;toast(m,'ok')}

async function refInit(){REF=await j('/api/reference',{});
 document.getElementById('refcat').innerHTML=Object.keys(REF).map(k=>`<option value="${k.replace(/"/g,'&quot;')}">${k} (${REF[k].length})</option>`).join('');refShow();}
function refShow(){const cat=document.getElementById('refcat').value||'';
 const f=(document.getElementById('reffilter').value||'').toLowerCase();
 const rows=(REF[cat]||[]).filter(e=>!f||(e.name||'').toLowerCase().includes(f)).slice(0,500);
 const editable=rows.length&&rows[0].off!==undefined;
 document.getElementById('refcount').textContent=rows.length+' shown'+(editable?' · edit + Enter to write':' · read-only English list');
 let h='<table><thead><tr><th>'+(editable?'Offset':'#')+'</th><th>Name</th></tr></thead><tbody>';
 rows.forEach(e=>{const nm=(e.name||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
  if(e.off!==undefined){h+=`<tr><td class=note>${e.off}</td><td><input value="${(e.name||'').replace(/"/g,'&quot;')}" data-off="${e.off}" size=26 onkeydown="if(event.key==='Enter')refWrite(this)"></td></tr>`;}
  else{h+=`<tr><td class=note>${e.i}</td><td>${nm}</td></tr>`;}});
 document.getElementById('refout').innerHTML=h+'</tbody></table>';}
async function refWrite(inp){if(!needIso())return;
 const r=await j('/api/setstring',{iso:iso(),off:inp.dataset.off,text:inp.value});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast('Wrote name @'+inp.dataset.off,'ok')}

async function scanSaves(){const s=await j('/api/savescan',{root:document.getElementById('saveroot').value});
 const d=document.getElementById('saves');
 if(s.error){d.innerHTML='<p class=bad>'+s.error+'</p>';return}
 if(!s.saves.length){d.innerHTML='<p class=note>No Suikoden V saves found in that folder.</p>';return}
 renderSaves(s.saves);toast('Found '+s.saves.length+' save(s)','ok')}
async function browseSave(){const r=await j('/api/picksave',{});
 if(r.error){toast(r.error,'bad');return} if(r.cancelled||!r.path)return;
 document.getElementById('savepath').value=r.path;openSaveFile()}
async function openSaveFile(){const p=(document.getElementById('savepath').value||'').trim();
 if(!p){toast('Enter a save path or use Open save file…','bad');return}
 const s=await j('/api/saveopen',{path:p});const d=document.getElementById('saves');
 if(s.error){d.innerHTML='<p class=bad>'+s.error+'</p>';toast(s.error,'bad');return}
 renderSaves(s.saves);toast('Opened '+s.saves.length+' save(s)','ok')}
function renderSaves(saves){const d=document.getElementById('saves');window._saves=saves;
 d.innerHTML=saves.map((sv,i)=>{const fl=sv.fields||{};const esc=x=>(x||'').replace(/"/g,'&quot;');
  const badge=sv.region?`<span class="badge ${sv.region=='PAL'?'b-pal':sv.region=='NTSC-U'?'b-ntsc':'b-jp'}">${sv.region}</span>`:'';
  const ro=sv.editable===false;
  const foot=ro
    ? `<span class=note>Read-only format (.cbs is compressed). Export to .xps or a memory card to edit.</span>`
    : `<button onclick=saveWrite(${i})>Write to save</button><span class=note>Writes hero/castle name + New Game Plus. A .bak is made first when backups are on (top-right toggle).</span>`;
  return `<div class=sec><div class=card-hd>${sv.folder} ${badge} <span class=note>· ${sv.card} · ${(sv.meta&&sv.meta.title)||''}</span></div><div class=grid>`+
   `<div class=fld><label>Hero name</label><div class=in><input id="sv${i}_heroName" value="${esc(fl.heroName)}" maxlength=15 ${ro?'disabled':''}></div></div>`+
   `<div class=fld><label>Castle name</label><div class=in><input id="sv${i}_castleName" value="${esc(fl.castleName)}" maxlength=15 ${ro?'disabled':''}></div></div>`+
   `<div class=fld><label>Level <span class=note>(display only)</span></label><div class=in><input type=number value="${fl.level||0}" disabled title="Save-select display level. Edit actual unit levels in the (upcoming) unit editor, not here."></div></div>`+
   `<div class=fld><label>New Game Plus</label><div class=in><label class=chk><input type=checkbox id="sv${i}_ngp" ${fl.newGamePlus?'checked':''} ${ro?'disabled':''}></label></div></div>`+
   `</div><div class=card-ft>${foot}</div></div>`}).join('');}
async function saveWrite(i){const sv=window._saves[i];
 const edits={heroName:document.getElementById('sv'+i+'_heroName').value,
  castleName:document.getElementById('sv'+i+'_castleName').value,
  newGamePlus:document.getElementById('sv'+i+'_ngp').checked?1:0};
 const bakOn=document.getElementById('bakToggle').checked;
 if(!confirm('Write to '+sv.card+'?'+(bakOn?'  A .bak is made.':'  No .bak (backups OFF).')))return;
 const r=await j('/api/savewrite',{card:sv.cardPath,folder:sv.folder,edits});
 if(r.error)toast('Error: '+r.error,'bad');else toast('Wrote '+r.changed+' field(s) to card','ok')}

async function peek(){const s=await j('/api/peek',{iso:iso(),off:document.getElementById('roff').value,len:document.getElementById('rlen').value});
 document.getElementById('out').textContent=s.error?s.error:(s.hex+'\n'+s.ascii)}

let OVERLAYS=[];
async function loadOverlays(){if(!needIso())return;const s=await j('/api/overlays',{iso:iso()});
 if(s.error){toast(s.error,'bad');return}OVERLAYS=s.overlays||[];
 document.getElementById('ovlnote').textContent=OVERLAYS.length+' overlays';
 let h='<table><thead><tr><th>Name</th><th>Compressed</th><th>Decompressed</th><th></th></tr></thead><tbody>';
 OVERLAYS.forEach(o=>{h+=`<tr><td>${o.name}</td><td class=note>${o.size.toLocaleString()}</td>`+
  `<td class=note>${o.decSize?o.decSize.toLocaleString():'(raw)'}</td>`+
  `<td><button class="ghost mini" onclick="extractOverlay('${o.name}')">Extract</button>`+
  (o.decSize?`<button class="ghost mini" onclick="reinsertOverlay('${o.name}')">Re-insert</button>`:'')+`</td></tr>`});
 document.getElementById('overlays').innerHTML=h+'</tbody></table>';
 const ts=document.getElementById('otxtsel');if(ts)ts.innerHTML=OVERLAYS.filter(o=>o.decSize).map(o=>`<option>${o.name}</option>`).join('');}
async function extractOverlay(name){const r=await j('/api/extractoverlay',{iso:iso(),name});
 if(r.error){toast(r.error,'bad');return}toast(name+' → '+r.decSize.toLocaleString()+' bytes ('+r.kind+')','ok');
 document.getElementById('ovlnote').innerHTML='Extracted to <code>'+r.path.replace(/[^/]+$/,'')+'</code>';}
async function extractAllOverlays(){if(!needIso())return;if(!OVERLAYS.length)await loadOverlays();
 const r=await j('/api/extractoverlay',{iso:iso(),name:'*'});
 if(r.error){toast(r.error,'bad');return}toast('Extracted '+r.count+' overlays','ok');
 document.getElementById('ovlnote').innerHTML=r.count+' overlays extracted to <code>'+r.dir+'</code>';}
let OTXT=[], OTXTNAME='';
async function loadOverlays_fillTextSel(){const sel=document.getElementById('otxtsel');
 if(sel && OVERLAYS.length && !sel.options.length)sel.innerHTML=OVERLAYS.filter(o=>o.decSize).map(o=>`<option>${o.name}</option>`).join('');}
async function loadOverlayText(){if(!needIso())return;const sel=document.getElementById('otxtsel');
 if(!sel.options.length){await loadOverlays();loadOverlays_fillTextSel();}
 OTXTNAME=sel.value||(OVERLAYS.find(o=>o.decSize)||{}).name;
 const s=await j('/api/overlaytext',{iso:iso(),name:OTXTNAME});
 if(s.error){toast(s.error,'bad');return}OTXT=s.strings||[];overlayTextShow();
 toast('Loaded '+OTXT.length+' strings from '+OTXTNAME,'ok');}
function overlayTextShow(){const f=(document.getElementById('otxtfilter').value||'').toLowerCase();
 const rows=OTXT.filter(e=>!f||e.text.toLowerCase().includes(f)).slice(0,500);
 document.getElementById('otxtnote').textContent=rows.length+' shown · '+OTXTNAME+' · Enter to stage';
 let h='<table><thead><tr><th>Offset</th><th>Text</th><th>Max</th></tr></thead><tbody>';
 rows.forEach(e=>{h+=`<tr><td class=note>0x${e.off.toString(16)}</td>`+
  `<td><input value="${e.text.replace(/"/g,'&quot;')}" data-off="${e.off}" size=40 maxlength=${e.cap-1} onkeydown="if(event.key==='Enter')setOverlayText(this)"></td>`+
  `<td class=note>${e.cap-1}</td></tr>`});
 document.getElementById('overlaytext').innerHTML=h+'</tbody></table>';}
async function setOverlayText(inp){const r=await j('/api/setoverlaytext',{iso:iso(),name:OTXTNAME,off:parseInt(inp.dataset.off),text:inp.value});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast('Staged @0x'+inp.dataset.off.toString(16),'ok')}
async function writeOverlayText(){if(!needIso()||!OTXTNAME)return;
 if(!confirm('Recompress '+OTXTNAME+' with your text edits and write it into the ISO?'))return;
 const r=await j('/api/reinsertoverlay',{iso:iso(),name:OTXTNAME});
 if(r.error){toast(r.error,'bad');document.getElementById('otxtnote').innerHTML='<span class=bad>'+r.error+'</span>';return}
 toast(OTXTNAME+' written ('+r.slack+' B slack)','ok');
 document.getElementById('otxtnote').textContent=OTXTNAME+' written: '+r.container.toLocaleString()+' / '+r.slot.toLocaleString()+' B slot';}
async function reinsertOverlay(name){if(!needIso())return;
 if(!confirm('Re-insert '+name+' from overlays_extracted/'+name.replace('.ROM','')+'.bin into the ISO?'))return;
 const r=await j('/api/reinsertoverlay',{iso:iso(),name});
 if(r.error){toast(r.error,'bad');document.getElementById('ovlnote').innerHTML='<span class=bad>'+r.error+'</span>';return}
 toast(name+' re-inserted ('+r.newCompSize.toLocaleString()+' B, '+r.slack+' B slack)','ok');
 document.getElementById('ovlnote').textContent=name+': recompressed '+r.container.toLocaleString()+' / '+r.slot.toLocaleString()+' B slot';}

(async function(){try{if(localStorage.s5theme=='light')document.body.classList.add('light')}catch(e){}
 MAPS=await j('/api/maps',{}); refInit();
 const st=%STATE%;
 try{document.getElementById('bakToggle').checked=st.backups!==false;}catch(e){}
 if(st.iso){LASTISO=st.iso;
  const lb=document.getElementById('lastbtn');lb.style.display='';lb.title=st.iso;
  document.getElementById('iso').value=st.iso;verify();}})();
</script></body></html>
"""


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.replace("%STATE%", json.dumps(load_state())), "text/html; charset=utf-8")
        else: self._send(404, "{}")
    def _body(self):
        n = int(self.headers.get("content-length", 0)); return json.loads(self.rfile.read(n) or b"{}")
    def do_POST(self):
        try:
            d = self._body(); iso = d.get("iso", "")
            ISO_PATHS = ("/api/verify", "/api/chars", "/api/char", "/api/setchar", "/api/peek")
            if self.path in ISO_PATHS and not os.path.exists(iso):
                return self._send(200, json.dumps({"error": "file not found"}))
            if self.path == "/api/verify":
                with P.Iso(iso) as g: ok = P.is_valid(g)
                if ok: s = load_state(); s["iso"] = iso; save_state(s)
                return self._send(200, json.dumps({"ok": ok, "msg": "Valid SLUS-21291" if ok else "not a recognized S5 ISO"}))
            if self.path == "/api/chars":
                # Authoritative playable roster in list order
                # (Hero=0 first). Records are addressed base + id*stride with THIS id.
                # Tag hasStats=False when the char-stat block (0x48A970) is all zero — those
                # units (story/boss, support, level-scaled recruits) have no editable stats;
                # the frontend groups them at the bottom of the dropdown and hides the stats.
                chars = F.load_characters()
                if os.path.exists(iso):
                    base, stride, _ = F.TABLES["stats"]
                    with P.Iso(iso) as g:
                        for c in chars:
                            c["hasStats"] = any(g.rd(base + c["id"] * stride, stride))
                return self._send(200, json.dumps({"chars": chars}))
            if self.path == "/api/char":
                cid = int(d["id"])
                with P.Iso(iso) as g:
                    tbls = P.read_character(g, cid)
                    raw = g.rd(P.table_addr("stats", cid), F.TABLES["stats"][1]).hex(" ")
                return self._send(200, json.dumps({"tables": tbls, "rawStats": raw,
                    "rawOff": P.table_addr("stats", cid)}))
            if self.path == "/api/setchar":
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    for e in d.get("edits", []):
                        P.write_field(g, e["table"], int(d["id"]), e["field"], int(e["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/pickiso":
                return self._send(200, json.dumps(pick_iso_dialog()))
            if self.path == "/api/spells":
                try: names = json.load(open(os.path.join(HERE, "s5_spell_names.json")))
                except Exception: names = []
                return self._send(200, json.dumps({"spells": names}))
            if self.path == "/api/spell":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                sid = int(d["id"])
                with P.Iso(iso) as g:
                    fields = P.read_spell(g, sid)
                    raw = g.rd(P.spell_addr(sid), F.SPELL_STRIDE).hex(" ")
                return self._send(200, json.dumps({"fields": fields, "rawOff": P.spell_addr(sid), "raw": raw}))
            if self.path == "/api/setspell":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    for e in d.get("edits", []):
                        P.write_spell_field(g, int(d["id"]), e["field"], int(e["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/runes":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                with P.Iso(iso) as g: runes = P.read_runes(g)
                return self._send(200, json.dumps({"runes": runes}))
            if self.path == "/api/rune":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                rid = int(d["id"])
                try: spnames = json.load(open(os.path.join(HERE, "s5_spell_names.json")))
                except Exception: spnames = []
                synth = rid >= F.SYNTH_RUNE_BASE
                with P.Iso(iso) as g:
                    if synth:
                        sr = F.SYNTH_RUNES[rid - F.SYNTH_RUNE_BASE]
                        name, start, cnt, grant = sr["name"], sr["start"], sr["count"], []
                    else:
                        grant = P.read_rune(g, rid)
                        start, cnt = grant[0]["value"], grant[1]["value"]
                        name = F.RUNE_GRANT_NAMES[rid] if rid < len(F.RUNE_GRANT_NAMES) else f"Rune {rid}"
                    spells = []
                    for k in range(cnt):
                        sid = start + k
                        if 0 <= sid < F.SPELL_COUNT:
                            spells.append({"id": sid, "level": k + 1,
                                "name": spnames[sid] if sid < len(spnames) and spnames[sid] else f"Spell {sid}",
                                "fields": P.read_spell(g, sid)})
                return self._send(200, json.dumps({"name": name, "synthetic": synth,
                    "grant": grant, "spells": spells}))
            if self.path == "/api/setrune":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    for e in d.get("edits", []):
                        P.write_rune_field(g, int(d["id"]), e["field"], int(e["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/gear":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                slot = d.get("slot", "body")
                if slot not in F.ARMOR_TABLES:
                    return self._send(200, json.dumps({"error": "bad slot"}))
                with P.Iso(iso) as g:
                    items = P.list_armor(g, slot)
                return self._send(200, json.dumps({"slot": slot, "items": items}))
            if self.path == "/api/gearitem":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                slot = d.get("slot", "body")
                if slot not in F.ARMOR_TABLES:
                    return self._send(200, json.dumps({"error": "bad slot"}))
                with P.Iso(iso) as g:
                    return self._send(200, json.dumps(P.read_armor_item(g, slot, int(d["id"]))))
            if self.path == "/api/setgear":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                slot = d.get("slot", "body")
                if slot not in F.ARMOR_TABLES:
                    return self._send(200, json.dumps({"error": "bad slot"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    for e in d.get("edits", []):
                        P.write_armor_field(g, slot, int(d["id"]), e["field"], int(e["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/enemies":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                try: names = json.load(open(os.path.join(HERE, "s5_enemy_names.json")))
                except Exception: names = {}
                with P.Iso(iso) as g: enemies = P.read_enemies(g, names)
                return self._send(200, json.dumps({"enemies": enemies}))
            if self.path == "/api/enemy":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                eid = int(d["id"])
                with P.Iso(iso) as g:
                    fields = P.read_enemy(g, eid)
                    raw = g.rd(P.enemy_addr(eid), F.ENEMY_STRIDE).hex(" ")
                return self._send(200, json.dumps({"fields": fields, "rawOff": P.enemy_addr(eid), "raw": raw}))
            if self.path == "/api/setenemy":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    for e in d.get("edits", []):
                        P.write_enemy_field(g, int(d["id"]), e["field"], int(e["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/prices":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                with P.Iso(iso) as g: pr = P.read_prices(g)
                return self._send(200, json.dumps({"prices": pr}))
            if self.path == "/api/setprice":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    P.write_price(g, int(d["index"]), d["field"], int(d["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/runeprices":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                with P.Iso(iso) as g: pr = P.read_rune_prices(g)
                return self._send(200, json.dumps({"prices": pr}))
            if self.path == "/api/setruneprice":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    P.write_rune_price(g, int(d["index"]), d["field"], int(d["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/overlays":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                return self._send(200, json.dumps({"overlays": P.list_overlays(iso)}))
            if self.path == "/api/extractoverlay":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                out_dir = os.path.join(os.path.dirname(os.path.abspath(iso)), "overlays_extracted")
                nm = d.get("name", "")
                if nm == "*":
                    n = 0
                    for o in P.list_overlays(iso):
                        P.extract_overlay(iso, o["name"], out_dir); n += 1
                    return self._send(200, json.dumps({"count": n, "dir": os.path.abspath(out_dir)}))
                return self._send(200, json.dumps(P.extract_overlay(iso, nm, out_dir)))
            if self.path in ("/api/overlaytext", "/api/setoverlaytext"):
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                nm = d.get("name", "")
                bin_path = os.path.join(os.path.dirname(os.path.abspath(iso)),
                                        "overlays_extracted", nm.replace(".ROM", "") + ".bin")
                try:
                    if self.path == "/api/overlaytext":
                        if not os.path.exists(bin_path):
                            out_dir = os.path.dirname(bin_path)
                            with P.Iso(iso) as g: P.extract_overlay(iso, nm, out_dir)
                        return self._send(200, json.dumps({"strings": P.overlay_strings(bin_path)}))
                    else:
                        if not os.path.exists(bin_path):
                            return self._send(200, json.dumps({"error": f"load {nm} text first"}))
                        return self._send(200, json.dumps(
                            P.write_overlay_string(bin_path, int(d["off"]), str(d["text"]))))
                except (ValueError, KeyError) as e:
                    return self._send(200, json.dumps({"error": str(e)}))
            if self.path == "/api/reinsertoverlay":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                nm = d.get("name", "")
                bin_path = os.path.join(os.path.dirname(os.path.abspath(iso)),
                                        "overlays_extracted", nm.replace(".ROM", "") + ".bin")
                if not os.path.exists(bin_path):
                    return self._send(200, json.dumps({"error": f"extract {nm} first (no {os.path.basename(bin_path)})"}))
                try:
                    P.backup(iso)
                    with P.Iso(iso, writable=True) as g:
                        res = P.reinsert_overlay(g, nm, bin_path)
                    return self._send(200, json.dumps(res))
                except (ValueError, KeyError) as e:
                    return self._send(200, json.dumps({"error": str(e)}))
            if self.path == "/api/healprices":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                with P.Iso(iso) as g: pr = P.read_heal_prices(g)
                return self._send(200, json.dumps({"prices": pr}))
            if self.path == "/api/sethealprice":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    P.write_heal_price(g, int(d["index"]), d["field"], int(d["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/mp":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                with P.Iso(iso) as g: groups = P.read_mp(g)
                return self._send(200, json.dumps({"groups": groups, "fields": F.MP_FIELD_LABELS}))
            if self.path == "/api/setmp":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    P.write_mp(g, int(d["group"]), int(d["idx"]), int(d["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/skillfx":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                with P.Iso(iso) as g: sk = P.read_skillfx(g)
                return self._send(200, json.dumps({"skills": sk, "ranks": F.SKILLFX_RANKS}))
            if self.path == "/api/setskillfx":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    P.write_skillfx(g, int(d["id"]), int(d["rank"]), int(d["value"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/unites":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                cn = {c["id"]: c["name"] for c in F.load_characters()}
                with P.Iso(iso) as g: us = P.read_unites(g, cn)
                roster = sorted(({"id": i, "name": n} for i, n in {**cn, **F.UNITE_EXTRA_CHARS}.items()),
                                key=lambda c: c["name"].lower())
                return self._send(200, json.dumps({"unites": us, "roster": roster}))
            if self.path == "/api/setunite":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    for e in d.get("edits", []):
                        P.write_unite_member(g, int(d["id"]), int(e["slot"]), int(e["charId"]))
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/hardmode":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                if d.get("restore"):
                    n = P.hardmode_restore(iso)
                else:
                    n = P.hardmode_apply(iso, float(d.get("factor", 0.5)))
                return self._send(200, json.dumps({"ok": True, "n": n}))
            if self.path == "/api/maps":
                try: items = json.load(open(os.path.join(HERE, "s5_item_names.json")))
                except Exception: items = {}
                try:
                    _rn = json.load(open(os.path.join(HERE, "s5_rune_names.json")))
                    runes = {str(i): (e.get("name") if isinstance(e, dict) else e)
                             for i, e in enumerate(_rn)}
                except Exception: runes = {}
                try:
                    _ar = json.load(open(os.path.join(HERE, "s5_armor_names.json")))
                    armor = {slot: _ar.get(slot, {}) for slot in ("head", "body", "glove", "foot")}
                except Exception: armor = {"head": {}, "body": {}, "glove": {}, "foot": {}}
                try: held = json.load(open(os.path.join(HERE, "s5_held_items.json"))).get("map", {})
                except Exception: held = {}
                return self._send(200, json.dumps({"items": items, "runes": runes, "armor": armor, "held": held,
                    "ranks": F.RANK_NAMES, "grades": F.AFFINITY_GRADES,
                    "egrades": F.ENEMY_AFFINITY_GRADES,
                    "spellstatus": {str(k): v for k, v in F.SPELL_STATUS_NAMES.items()},
                    # drop dropdown: u16 value (category | item<<8) -> "Category · Item"
                    "dropitems": {str((int(k.split(":")[1]) << 8) | int(k.split(":")[0])):
                                  f"{F.DROP_TABLE['categories'].get(k.split(':')[0], 'Cat ' + k.split(':')[0])} · {v}"
                                  for k, v in sorted(F.DROP_TABLE["items"].items(),
                                                     key=lambda kv: (int(kv[0].split(":")[0]), int(kv[0].split(":")[1])))},
                    "help": F.SECTION_HELP, "globalHelp": F.GLOBAL_HELP,
                    "elements": {str(k): v for k, v in F.ELEMENT_NAMES.items()},
                    "targets": {str(k): v for k, v in F.TARGET_NAMES.items()}}))
            if self.path == "/api/backups":
                on = bool(d.get("on", True))
                P.BACKUPS = on; SV.BACKUPS = on
                st = load_state(); st["backups"] = on; save_state(st)
                return self._send(200, json.dumps({"ok": True, "backups": on}))
            if self.path == "/api/reference":
                out = {}
                # Clean, canonical English name lists (index -> name, read-only: no ELF
                # offset). Shown first so they're the default browse view.
                try:
                    en = json.load(open(os.path.join(HERE, "s5_ref_english.json")))
                    for cat, names in en.items():
                        out[cat] = [{"i": i, "name": n} for i, n in enumerate(names)]
                except Exception:
                    pass
                # Raw editable ELF strings (all languages, offset-addressed). Relabelled so
                # it's clear these are the writable in-place strings, not the clean lists.
                try:
                    ref = json.load(open(os.path.join(HERE, "s5_reference.json")))
                    for cat, entries in ref.items():
                        out[f"{cat} (ELF text · editable)"] = entries
                except Exception:
                    pass
                return self._send(200, json.dumps(out))
            if self.path == "/api/setstring":
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "open the ISO first"}))
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    cap = P.set_cstring(g, int(str(d["off"]), 0), str(d["text"]))
                return self._send(200, json.dumps({"ok": True, "cap": cap}))
            if self.path == "/api/savescan":
                roots = [os.path.join(HERE, "..", "Saves"), os.path.join(HERE, "..")]
                if d.get("root"): roots.insert(0, d["root"])
                saves = []; seen = set()
                # 1) memory-card images
                for card in SV.scan_memcards(roots):
                    if not card["hasS5"]: continue
                    try:
                        for s in SV.read_all_saves(card["path"]):
                            s["card"] = card["name"]; s["cardPath"] = card["path"]; saves.append(s)
                    except Exception: pass
                # 2) standalone save files (.xps / .sps / .cbs)
                exts = (".xps", ".sps", ".cbs", ".psu", ".psv", ".max")
                for r in roots:
                    if not r or not os.path.isdir(r): continue
                    for dp, _, files in os.walk(r):
                        for fn in sorted(files):
                            if not fn.lower().endswith(exts): continue
                            full = os.path.join(dp, fn)
                            if full in seen: continue
                            seen.add(full)
                            try:
                                s = SV.read_individual_save(full)
                            except Exception:
                                s = None
                            if not s: continue
                            head = open(full, "rb").read(4)
                            s["card"] = os.path.join(os.path.basename(dp), fn); s["cardPath"] = full; s["individual"] = True
                            s["editable"] = True
                            s.setdefault("meta", {"title": ""})
                            saves.append(s)
                # dedup by real path + folder (overlapping roots can list a file twice)
                uniq = []; dseen = set()
                for s in saves:
                    key = (os.path.realpath(s.get("cardPath", "")), s.get("folder", ""))
                    if key in dseen: continue
                    dseen.add(key); uniq.append(s)
                return self._send(200, json.dumps({"saves": uniq}))
            if self.path == "/api/picksave":
                return self._send(200, json.dumps(pick_save_dialog()))
            if self.path == "/api/saveopen":
                path = d.get("path", "")
                if not path or not os.path.exists(path):
                    return self._send(200, json.dumps({"error": "file not found: " + path}))
                saves = []
                try:
                    head = open(path, "rb").read(20)
                    individual = head[:4] == b"CFU\x00" or head[:17] == b"\x0d\x00\x00\x00SharkPortSave"
                    if individual:
                        s = SV.read_individual_save(path)
                        if s:
                            s["card"] = os.path.basename(path); s["cardPath"] = path
                            s["individual"] = True
                            s["editable"] = True
                            s.setdefault("meta", {"title": ""})
                            saves = [s]
                    else:
                        for s in SV.read_all_saves(path):
                            s["card"] = os.path.basename(path); s["cardPath"] = path; saves.append(s)
                except Exception as e:
                    return self._send(200, json.dumps({"error": "could not read save: " + str(e)}))
                if not saves:
                    return self._send(200, json.dumps({"error": "no Suikoden V save found in that file"}))
                return self._send(200, json.dumps({"saves": saves}))
            if self.path == "/api/savewrite":
                card = d["card"]
                try: head = open(card, "rb").read(20)
                except Exception: head = b""
                individual = head[:4] == b"CFU\x00" or head[:17] == b"\x0d\x00\x00\x00SharkPortSave"
                if individual:
                    r = SV.write_individual_save(card, d.get("edits", {}))
                else:
                    r = SV.write_save_fields(card, d["folder"], d.get("edits", {}))
                return self._send(200, json.dumps(r))
            if self.path == "/api/peek":
                off = int(str(d.get("off", "0")), 0); ln = max(1, min(256, int(str(d.get("len", "16")), 0)))
                with P.Iso(iso) as g: b = g.rd(off, ln)
                return self._send(200, json.dumps({"hex": b.hex(" "),
                    "ascii": "".join(chr(c) if 32 <= c < 127 else "." for c in b)}))
            self._send(404, "{}")
        except Exception as e:
            self._send(200, json.dumps({"error": str(e)}))


def main():
    with socketserver.TCPServer(("127.0.0.1", PORT), H) as httpd:
        url = f"http://127.0.0.1:{PORT}/"
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        print(f"Suikoden V editor running at {url}  (Ctrl+C to stop)")
        try: httpd.serve_forever()
        except KeyboardInterrupt: print("\nstopped.")


if __name__ == "__main__":
    main()
