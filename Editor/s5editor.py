#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) editor — local web app.

Runs an HTTP server on your machine and opens a browser tab. Nothing is uploaded.
ISO editing (characters/stats/skills/equipment/runes/prices/Hard Mode/names/text) and
PS2 save editing (hero/castle name + New Game Plus). See Suikoden5_ISO_offsets.md.
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
 <button class="ghost mini" onclick=toggleTheme()>◐ Theme</button>
</header>
<nav id=nav>
 <button data-tab=char class=on onclick=showTab('char')>Characters</button>
 <button data-tab=rune onclick=showTab('rune')>Runes &amp; Spells</button>
 <button data-tab=save onclick=showTab('save')>Saves</button>
 <div class=navdrop>
  <button id=otherbtn onclick="event.stopPropagation();toggleOther()">Other ▾</button>
  <div class=navmenu id=othermenu>
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

 <section class=panel id=p-price>
  <h2>Item &amp; Equipment Prices</h2>
  <p class=sub>Buy / sell prices (verified vs stat guide; sell = buy ÷ 2). Records in item-id order.</p>
  <div class=row><button onclick=loadPrices()>Load prices</button>
   <input id=pricefilter size=12 placeholder="min buy…" oninput=priceShow()>
   <span id=pricenote class=note></span></div>
  <div class=scroll id=prices></div>
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
  <p class=sub>Item / rune / spell / skill / enemy names from the boot ELF. Edit a name and press Enter to write it back (byte-capped, in place).</p>
  <div class=row>
   <select id=refcat onchange=refShow()></select>
   <input id=reffilter size=20 placeholder="search names…" oninput=refShow()>
   <span id=refcount class=note></span>
  </div>
  <div class=scroll id=refout style=padding:6px>Loading reference…</div>
 </section>

 <section class=panel id=p-save>
  <h2>Memory-Card Saves</h2>
  <p class=sub>Scan PS2 cards for Suikoden V saves. Edit hero / castle name and toggle New Game Plus (enables fast-forward). ECC + .bak handled automatically.</p>
  <div class=row>
   <span class=note>Search folder</span>
   <input id=saveroot size=40 placeholder="(defaults to ./Saves)">
   <button onclick=scanSaves()>Scan for saves</button>
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
 </section>
</main>
<div id=spin><div class=sun></div></div>
<div id=toast></div>
<script>
let CHARS=[], CUR=null, ORIG={}, REF={}, PRICES=[], MAPS={items:{},runes:{},ranks:[],elements:{},targets:{}};
let SPELLS=[], SCUR=null, SORIG={};
let _busy=0;
function ctrl(r,key){const v=r.value;
 const ch='onchange="this.classList.toggle(\'chg\',this.value!=ORIG[this.dataset.k])"';
 if(r.kind=='rank'){const R=MAPS.ranks||[];let o='';
  if(v<0||v>=R.length)o+=`<option value=${v} selected>${v} · (raw)</option>`;
  for(let i=0;i<R.length;i++)o+=`<option value=${i} ${i==v?'selected':''}>${i} · ${R[i]}</option>`;
  return `<select data-k="${key}" ${ch}>${o}</select>`;}
 if(r.kind=='item'||r.kind=='rune'||r.kind=='element'||r.kind=='target'){
  const it=({item:MAPS.items,rune:MAPS.runes,element:MAPS.elements,target:MAPS.targets}[r.kind])||{};
  const nm=id=>{const e=it[id];return e?(e.name||e):('#'+id)};
  let o=`<option value=${v} selected>${v} · ${nm(v)}</option>`;
  Object.keys(it).forEach(id=>{if(+id!=v)o+=`<option value=${id}>${id} · ${nm(id)}</option>`});
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
 const ob=document.getElementById('otherbtn');if(ob)ob.classList.toggle('on',['price','hard','ref','tools'].includes(name));
 const om=document.getElementById('othermenu');if(om)om.classList.remove('open');}
function toggleOther(){document.getElementById('othermenu').classList.toggle('open');}
document.addEventListener('click',e=>{const d=document.querySelector('.navdrop');
 if(d&&!d.contains(e.target)){const om=document.getElementById('othermenu');if(om)om.classList.remove('open');}});
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
  document.getElementById('runehint').textContent='';loadRune();}
 else toast(s.msg,'bad');}
function fillChars(){const sel=document.getElementById('csel'),f=(document.getElementById('cfilter').value||'').toLowerCase();
 sel.innerHTML=CHARS.filter(c=>!f||c.name.toLowerCase().includes(f)||(''+c.id).includes(f))
  .map(c=>`<option value="${c.id}">${c.id} — ${c.name}</option>`).join('');}
function filterChars(){fillChars();loadChar();}
async function loadChar(){const sel=document.getElementById('csel');if(!sel.value)return;
 CUR=parseInt(sel.value);const s=await j('/api/char',{iso:iso(),id:CUR});
 const secs=document.getElementById('sections');
 if(s.error){secs.innerHTML='<p class=bad>'+s.error+'</p>';return}
 ORIG={};secs.innerHTML='';
 for(const [tbl,rows] of Object.entries(s.tables)){
  const div=document.createElement('div');div.className='sec';
  const g=document.createElement('div');g.className='grid';
  const hp=(MAPS.help&&MAPS.help[tbl])?`<div class=note style="grid-column:1/-1;margin:-2px 0 2px">${MAPS.help[tbl]}</div>`:'';
  g.innerHTML=hp;
  rows.forEach(r=>{const key=tbl+'|'+r.label;ORIG[key]=r.value;
   g.innerHTML+=`<div class=fld><label>${r.label} <span class=pill>${r.kind=='rank'?'rank':r.kind=='item'?'item':r.width+'B'}</span></label>`+
    `<div class=in>${ctrl(r,key)}`+
    `<button class="ghost mini" title=restore onclick=restoreField(this) data-k="${key}">↺</button></div></div>`;});
  div.innerHTML=`<h3 onclick=toggleSec(this)>${tbl}</h3>`;div.appendChild(g);secs.appendChild(div);}
 if(s.rawStats){const rd=document.createElement('div');rd.className='sec';
  rd.innerHTML=`<h3 class=closed onclick=toggleSec(this)>raw bytes · stats @0x${(s.rawOff||0).toString(16)}</h3>`+
   `<div style="display:none;padding:12px"><pre style="white-space:pre-wrap">${s.rawStats}</pre></div>`;
  secs.appendChild(rd);}
 document.getElementById('csave').textContent='';}
function toggleSec(h){h.classList.toggle('closed');const b=h.nextElementSibling;
 b.style.display=b.style.display=='none'?(b.className=='grid'?'grid':'block'):'none';}
function restoreField(btn){const i=document.querySelector('#sections [data-k="'+btn.dataset.k+'"]');
 if(i){i.value=ORIG[btn.dataset.k];i.classList.remove('chg')}}
function revertChar(){document.querySelectorAll('#sections [data-k]').forEach(i=>{i.value=ORIG[i.dataset.k];i.classList.remove('chg')});toast('Reverted unsaved changes')}
async function saveChar(){if(!needIso())return;const edits=[];
 document.querySelectorAll('#sections [data-k]').forEach(i=>{if(i.value!=ORIG[i.dataset.k]){const[t,f]=i.dataset.k.split('|');edits.push({table:t,field:f,value:parseInt(i.value)})}});
 if(!edits.length){toast('No changes to save');return}
 const s=await j('/api/setchar',{iso:iso(),id:CUR,edits});
 if(s.error)toast('Error: '+s.error,'bad');else{toast('Saved '+edits.length+' field(s)','ok');loadChar()}}

// ---- Runes & Spells: pick a rune, edit its spells inline (no spell dropdown) ----
function pillFor(r){return r.kind=='element'?'element':r.kind=='target'?'target':r.width+'B';}
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
 runeSetPreview();document.getElementById('rsave').textContent='';}
function runeSetPreview(){const sEl=document.querySelector('#runesections [data-k="grant|Start spell"]');
 const cEl=document.querySelector('#runesections [data-k="grant|Spell count"]');
 const box=document.getElementById('setpreview');if(!sEl||!cEl||!box)return;
 const start=+sEl.value,cnt=+cEl.value;let items='';
 for(let k=0;k<cnt;k++){const id=start+k;items+=`<li><b>${SPELLS[id]||('#'+id)}</b> <span class=note>(spell ${id})</span></li>`;}
 box.innerHTML=items||'<li class=note>(no spells)</li>';}
function restoreRuneField(btn){const i=document.querySelector('#runesections [data-k="'+btn.dataset.k+'"]');
 if(i){i.value=RORIG[btn.dataset.k];i.classList.remove('chg')}}
function revertRune(){document.querySelectorAll('#runesections [data-k]').forEach(i=>{i.value=RORIG[i.dataset.k];i.classList.remove('chg')});toast('Reverted unsaved changes')}
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

async function loadPrices(){if(!needIso())return;const s=await j('/api/prices',{iso:iso()});
 if(s.error){toast(s.error,'bad');return}PRICES=s.prices.filter(p=>p.buy||p.sell);priceShow();toast('Loaded '+PRICES.length+' priced items','ok')}
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

async function hardmode(restore){if(!needIso())return;
 const factor=parseFloat(document.getElementById('hmfactor').value);
 if(!restore && !confirm('Scale ALL characters’ starting stats ×'+factor+'?  (.bak made; Restore available)'))return;
 const r=await j('/api/hardmode',{iso:iso(),factor,restore});
 if(r.error){toast('Error: '+r.error,'bad');return}
 const m=restore?('Restored '+r.n+' characters'):('Scaled '+r.n+' characters ×'+factor);
 document.getElementById('hmstatus').textContent=m;toast(m,'ok')}

async function refInit(){REF=await j('/api/reference',{});
 document.getElementById('refcat').innerHTML=Object.keys(REF).map(k=>`<option>${k} (${REF[k].length})</option>`).join('');refShow();}
function refShow(){const cat=(document.getElementById('refcat').value||'').split(' ')[0];
 const f=(document.getElementById('reffilter').value||'').toLowerCase();
 const rows=(REF[cat]||[]).filter(e=>!f||e.name.toLowerCase().includes(f)).slice(0,500);
 document.getElementById('refcount').textContent=rows.length+' shown · edit + Enter to write';
 let h='<table><thead><tr><th>Offset</th><th>Name</th></tr></thead><tbody>';
 rows.forEach(e=>{h+=`<tr><td class=note>${e.off}</td><td><input value="${e.name.replace(/"/g,'&quot;')}" data-off="${e.off}" size=26 onkeydown="if(event.key==='Enter')refWrite(this)"></td></tr>`});
 document.getElementById('refout').innerHTML=h+'</tbody></table>';}
async function refWrite(inp){if(!needIso())return;
 const r=await j('/api/setstring',{iso:iso(),off:inp.dataset.off,text:inp.value});
 inp.classList.toggle('chg',!r.error);if(r.error)toast(r.error,'bad');else toast('Wrote name @'+inp.dataset.off,'ok')}

async function scanSaves(){const s=await j('/api/savescan',{root:document.getElementById('saveroot').value});
 const d=document.getElementById('saves');
 if(s.error){d.innerHTML='<p class=bad>'+s.error+'</p>';return}
 if(!s.saves.length){d.innerHTML='<p class=note>No Suikoden V saves found in that folder.</p>';return}
 window._saves=s.saves;
 d.innerHTML=s.saves.map((sv,i)=>{const fl=sv.fields||{};
  return `<div class=sec><div class=card-hd>${sv.folder} <span class=note>· ${sv.card} · ${(sv.meta&&sv.meta.title)||''}</span></div><div class=grid>`+
   `<div class=fld><label>Hero name</label><div class=in><input id="sv${i}_heroName" value="${(fl.heroName||'').replace(/"/g,'&quot;')}" maxlength=15></div></div>`+
   `<div class=fld><label>Castle name</label><div class=in><input id="sv${i}_castleName" value="${(fl.castleName||'').replace(/"/g,'&quot;')}" maxlength=15></div></div>`+
   `<div class=fld><label>New Game Plus (fast-forward)</label><div class=in><label class=note style=padding-top:7px><input type=checkbox id="sv${i}_ngp" ${fl.newGamePlus?'checked':''}> enabled</label></div></div>`+
   `<div class=fld><label>&nbsp;</label><button onclick=saveWrite(${i})>Write to card</button></div>`+
   `</div></div>`}).join('');
 toast('Found '+s.saves.length+' save(s)','ok')}
async function saveWrite(i){const sv=window._saves[i];
 const edits={heroName:document.getElementById('sv'+i+'_heroName').value,
  castleName:document.getElementById('sv'+i+'_castleName').value,
  newGamePlus:document.getElementById('sv'+i+'_ngp').checked?1:0};
 if(!confirm('Write to '+sv.card+'?  A .bak is made.'))return;
 const r=await j('/api/savewrite',{card:sv.cardPath,folder:sv.folder,edits});
 if(r.error)toast('Error: '+r.error,'bad');else toast('Wrote '+r.changed+' field(s) to card','ok')}

async function peek(){const s=await j('/api/peek',{iso:iso(),off:document.getElementById('roff').value,len:document.getElementById('rlen').value});
 document.getElementById('out').textContent=s.error?s.error:(s.hex+'\n'+s.ascii)}

(async function(){try{if(localStorage.s5theme=='light')document.body.classList.add('light')}catch(e){}
 MAPS=await j('/api/maps',{}); refInit();
 const st=%STATE%;if(st.iso){LASTISO=st.iso;
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
                return self._send(200, json.dumps({"chars": F.load_characters()}))
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
                return self._send(200, json.dumps({"items": items, "runes": runes,
                    "ranks": F.RANK_NAMES, "help": F.SECTION_HELP, "globalHelp": F.GLOBAL_HELP,
                    "elements": {str(k): v for k, v in F.ELEMENT_NAMES.items()},
                    "targets": {str(k): v for k, v in F.TARGET_NAMES.items()}}))
            if self.path == "/api/reference":
                try:
                    ref = json.load(open(os.path.join(HERE, "s5_reference.json")))
                except Exception:
                    ref = {}
                return self._send(200, json.dumps(ref))
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
                saves = []
                for card in SV.scan_memcards(roots):
                    if not card["hasS5"]: continue
                    try:
                        for s in SV.read_all_saves(card["path"]):
                            s["card"] = card["name"]; s["cardPath"] = card["path"]; saves.append(s)
                    except Exception: pass
                return self._send(200, json.dumps({"saves": saves}))
            if self.path == "/api/savewrite":
                r = SV.write_save_fields(d["card"], d["folder"], d.get("edits", {}))
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
