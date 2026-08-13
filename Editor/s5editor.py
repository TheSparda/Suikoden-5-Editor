#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) editor — local web app.

Runs an HTTP server on your machine and opens a browser tab. Nothing is uploaded.
Character editor (stats/skills/equipment/runes/thresholds) over the VERIFIED ISO
tables, plus name renaming and a raw hex peek. See Suikoden5_ISO_offsets.md.
"""
import http.server, json, os, socketserver, webbrowser, threading
import s5patch as P
import s5fields as F
import s5save as SV

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".s5editor.json")
PORT = 8055

def load_state():
    try: return json.load(open(STATE))
    except Exception: return {}
def save_state(s):
    try: json.dump(s, open(STATE, "w"))
    except Exception: pass

PAGE = r"""<!doctype html><meta charset=utf-8><title>Suikoden V Editor</title>
<style>
 body{font:14px system-ui;margin:0;background:#1a1113;color:#f2e6d8}
 header{background:#3a0d12;padding:10px 16px;border-bottom:2px solid #c8102e}
 h1{margin:0;font-size:17px;color:#f0c05a}
 main{padding:16px;max-width:1000px}
 input,button,select{font:14px system-ui;padding:5px 7px;border-radius:6px;border:1px solid #6b3b2e;background:#241619;color:#f2e6d8}
 button{background:#c8102e;border:0;cursor:pointer;color:#fff}
 button.ghost{background:#3a2a2d}
 .row{margin:9px 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
 .ok{color:#8bd450}.bad{color:#ff6b6b}.note{color:#c9a96a;font-size:12px}
 .sec{margin:14px 0;border:1px solid #4a2b26;border-radius:8px}
 .sec h3{margin:0;padding:8px 12px;background:#2a1518;border-radius:8px 8px 0 0;font-size:14px;color:#f0c05a;cursor:pointer}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px;padding:12px}
 .fld label{display:block;font-size:12px;color:#c9a96a;margin-bottom:2px}
 .fld input{width:100%}
 .fld input.chg{border-color:#f0c05a;background:#2e2410}
 pre{background:#0f0a0b;padding:10px;border-radius:6px;overflow:auto}
</style>
<header><h1>Suikoden V — ISO Editor</h1></header>
<main>
 <div class=row>
   ISO: <input id=iso size=52 placeholder="/path/to/Suikoden V.iso">
   <button onclick=verify()>Open / Verify</button><span id=status></span>
 </div>
 <div class=row id=charrow style=display:none>
   Character: <select id=csel onchange=loadChar()></select>
   <input id=cfilter size=14 placeholder="filter…" oninput=filterChars()>
   <button onclick=saveChar()>Save changes</button>
   <span id=csave class=note></span>
 </div>
 <div id=sections></div>
 <hr style="border-color:#4a2b26;margin:18px 0">
 <h3 style="color:#f0c05a;font-size:15px">Memory-card saves</h3>
 <div class=row>
   Search folder: <input id=saveroot size=40 placeholder="(defaults to ./Saves)">
   <button class=ghost onclick=scanSaves()>Scan for S5 saves</button>
 </div>
 <div id=saves></div>
 <hr style="border-color:#4a2b26;margin:18px 0">
 <div class=row>
   Raw read: off <input id=roff size=10 value=0x828BD> len <input id=rlen size=4 value=16>
   <button class=ghost onclick=peek()>Read</button>
 </div>
 <pre id=out>Open your ISO to begin.</pre>
 <p class=note>Fields marked (?) hold verified per-character data; exact stat labels are
 still being confirmed. A .bak is made before the first write.</p>
</main>
<script>
let CHARS=[], CUR=null, ORIG={};
async function j(u,b){const r=await fetch(u,{method:b?'POST':'GET',body:b&&JSON.stringify(b),
  headers:{'content-type':'application/json'}});return r.json()}
function iso(){return document.getElementById('iso').value}
async function verify(){const s=await j('/api/verify',{iso:iso()});
  const el=document.getElementById('status');el.textContent=s.ok?' ✓ '+s.msg:' ✗ '+s.msg;
  el.className=s.ok?'ok':'bad';
  if(s.ok){CHARS=(await j('/api/chars',{iso:iso()})).chars;fillChars();
    document.getElementById('charrow').style.display='';loadChar();}}
function fillChars(){const sel=document.getElementById('csel');const f=(document.getElementById('cfilter').value||'').toLowerCase();
  sel.innerHTML=CHARS.filter(c=>!f||c.name.toLowerCase().includes(f)||(''+c.id).includes(f))
    .map(c=>`<option value="${c.id}">${c.id} - ${c.name}</option>`).join('');}
function filterChars(){fillChars();loadChar();}
async function loadChar(){const sel=document.getElementById('csel');if(!sel.value)return;
  CUR=parseInt(sel.value);const s=await j('/api/char',{iso:iso(),id:CUR});
  if(s.error){document.getElementById('sections').innerHTML='<p class=bad>'+s.error+'</p>';return}
  ORIG={};const secs=document.getElementById('sections');secs.innerHTML='';
  for(const [tbl,rows] of Object.entries(s.tables)){
    const div=document.createElement('div');div.className='sec';
    div.innerHTML=`<h3 onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display=='none'?'grid':'none'">${tbl}</h3>`;
    const g=document.createElement('div');g.className='grid';
    rows.forEach(r=>{const key=tbl+'|'+r.label;ORIG[key]=r.value;
      const max=r.width==1?255:65535;
      g.innerHTML+=`<div class=fld><label>${r.label} <span class=note>(${r.width}B ≤${max})</span></label>`+
        `<input type=number min=0 max=${max} value=${r.value} data-k="${key}" `+
        `oninput="this.classList.toggle('chg',this.value!=ORIG[this.dataset.k])"></div>`;});
    div.appendChild(g);secs.appendChild(div);}
  document.getElementById('csave').textContent='';}
async function saveChar(){const inps=[...document.querySelectorAll('#sections input[data-k]')];
  const edits=[];inps.forEach(i=>{if(i.value!=ORIG[i.dataset.k]){const [t,f]=i.dataset.k.split('|');
    edits.push({table:t,field:f,value:parseInt(i.value)});}});
  if(!edits.length){document.getElementById('csave').textContent='no changes';return}
  const s=await j('/api/setchar',{iso:iso(),id:CUR,edits});
  document.getElementById('csave').textContent=s.error?('error: '+s.error):('saved '+edits.length+' field(s)');
  if(!s.error)loadChar();}
async function scanSaves(){const s=await j('/api/savescan',{root:document.getElementById('saveroot').value});
  const d=document.getElementById('saves');
  if(s.error){d.textContent=s.error;return}
  if(!s.saves.length){d.innerHTML='<p class=note>No Suikoden V saves found.</p>';return}
  d.innerHTML=s.saves.map((sv,i)=>{const fl=sv.fields||{};
    return `<div class=sec><h3>${sv.card} — ${sv.folder} <span class=note>${(sv.meta&&sv.meta.title)||''}</span></h3>`+
    `<div class=grid>`+
    `<div class=fld><label>Hero name</label><input id="sv${i}_heroName" value="${fl.heroName||''}" maxlength=15></div>`+
    `<div class=fld><label>Castle name</label><input id="sv${i}_castleName" value="${fl.castleName||''}" maxlength=15></div>`+
    `<div class=fld><label>Level</label><input id="sv${i}_level" type=number min=0 max=255 value="${fl.level||0}"></div>`+
    `<div class=fld><label>&nbsp;</label><button onclick='saveWrite(${i})'>Write to card</button></div>`+
    `</div></div>`;}).join('');
  window._saves=s.saves;}
async function saveWrite(i){const sv=window._saves[i];
  const edits={heroName:document.getElementById('sv'+i+'_heroName').value,
    castleName:document.getElementById('sv'+i+'_castleName').value,
    level:parseInt(document.getElementById('sv'+i+'_level').value)};
  if(!confirm('Write to '+sv.card+'? A .bak is made. (Save checksum unverified — verify it loads in-game.)'))return;
  const r=await j('/api/savewrite',{card:sv.cardPath,folder:sv.folder,edits});
  alert(r.error?('Error: '+r.error):('Wrote '+r.changed+' field(s). '+(r.warn||'')));}
async function peek(){const s=await j('/api/peek',{iso:iso(),
  off:document.getElementById('roff').value,len:document.getElementById('rlen').value});
  document.getElementById('out').textContent=s.error?s.error:(s.hex+'\n'+s.ascii);}
(function(){const st=%STATE%;if(st.iso){document.getElementById('iso').value=st.iso}})();
</script>
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
                with P.Iso(iso) as g: tbls = P.read_character(g, int(d["id"]))
                return self._send(200, json.dumps({"tables": tbls}))
            if self.path == "/api/setchar":
                P.backup(iso)
                with P.Iso(iso, writable=True) as g:
                    for e in d.get("edits", []):
                        P.write_field(g, e["table"], int(d["id"]), e["field"], int(e["value"]))
                return self._send(200, json.dumps({"ok": True}))
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
