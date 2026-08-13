#!/usr/bin/env python3
"""
Suikoden V (USA, SLUS-21291) editor — local web app.

Runs a small HTTP server on your machine and opens a browser tab. Nothing is
uploaded; the server only touches the ISO you point it at.

STATUS: early scaffold. The ISO is validated by serial and you can read/poke raw
bytes at absolute offsets (a safe research surface). The named character editor is
gated until the record base/stride is verified (see s5fields.py / the offsets doc).
"""
import http.server, json, os, socketserver, urllib.parse, webbrowser, threading

import s5patch as P
import s5fields as F

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".s5editor.json")
PORT = 8055


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}

def save_state(s):
    try:
        json.dump(s, open(STATE, "w"))
    except Exception:
        pass


PAGE = """<!doctype html><meta charset=utf-8>
<title>Suikoden V Editor</title>
<style>
 body{font:14px system-ui;margin:0;background:#1a1113;color:#f2e6d8}
 header{background:#3a0d12;padding:12px 18px;border-bottom:2px solid #c8102e}
 h1{margin:0;font-size:18px;color:#f0c05a}
 main{padding:18px;max-width:900px}
 input,button{font:14px system-ui;padding:6px 8px;border-radius:6px;border:1px solid #6b3b2e;background:#241619;color:#f2e6d8}
 button{background:#c8102e;border:0;cursor:pointer;color:#fff}
 .row{margin:10px 0}
 .ok{color:#8bd450}.bad{color:#ff6b6b}
 pre{background:#0f0a0b;padding:10px;border-radius:6px;overflow:auto}
 .note{color:#c9a96a;font-size:12px}
</style>
<header><h1>Suikoden V — ISO Editor <span class=note>(scaffold)</span></h1></header>
<main>
 <div class=row>
   ISO path: <input id=iso size=60 placeholder="/path/to/Suikoden V.iso">
   <button onclick=verify()>Open / Verify</button>
   <span id=status></span>
 </div>
 <div class=row>
   Read raw: offset <input id=roff size=10 value="0x828BD"> len <input id=rlen size=5 value="16">
   <button onclick=peek()>Read</button>
 </div>
 <pre id=out>Pick your ISO and click Open / Verify.</pre>
 <div class=row>
   <button onclick=names()>List characters</button>
   <span class=note>edit a name (≤7 chars) and Save to rename in the ISO</span>
 </div>
 <div id=names></div>
 <p class=note>Character <b>renaming</b> is verified (8-byte name table @0x691600). Stat
 editing is still research — see <code>Editor/Suikoden5_ISO_offsets.md</code>.</p>
</main>
<script>
async function j(u,b){const r=await fetch(u,{method:b?'POST':'GET',body:b&&JSON.stringify(b),
  headers:{'content-type':'application/json'}});return r.json()}
async function verify(){const iso=document.getElementById('iso').value;
  const s=await j('/api/verify',{iso});const el=document.getElementById('status');
  el.textContent=s.ok?' ✓ '+s.msg:' ✗ '+s.msg; el.className=s.ok?'ok':'bad';}
async function peek(){const iso=document.getElementById('iso').value;
  const off=document.getElementById('roff').value, len=document.getElementById('rlen').value;
  const s=await j('/api/peek',{iso,off,len});
  document.getElementById('out').textContent = s.error? s.error : s.hex+'\\n'+s.ascii;}
async function names(){const iso=document.getElementById('iso').value;
  const s=await j('/api/names',{iso}); const d=document.getElementById('names');
  if(s.error){d.textContent=s.error;return}
  d.innerHTML='<table>'+s.names.map(e=>
    `<tr><td>#${e.index}</td><td>0x${e.off.toString(16)}</td>`+
    `<td><input id="n${e.index}" value="${e.name}" maxlength=7 size=9></td>`+
    `<td><button onclick="rename(${e.index})">Save</button></td></tr>`).join('')+'</table>';}
async function rename(i){const iso=document.getElementById('iso').value;
  const name=document.getElementById('n'+i).value;
  const s=await j('/api/rename',{iso,index:i,name});
  alert(s.error?('Error: '+s.error):('Renamed #'+i+' -> '+name));}
(function(){const st=%STATE%; if(st.iso){document.getElementById('iso').value=st.iso}})();
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
            st = json.dumps(load_state())
            self._send(200, PAGE.replace("%STATE%", st), "text/html; charset=utf-8")
        else:
            self._send(404, "{}")

    def _body(self):
        n = int(self.headers.get("content-length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        try:
            data = self._body()
            if self.path == "/api/verify":
                iso = data.get("iso", "")
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"ok": False, "msg": "file not found"}))
                with P.Iso(iso) as g:
                    ok = P.is_valid(g)
                if ok:
                    s = load_state(); s["iso"] = iso; save_state(s)
                return self._send(200, json.dumps(
                    {"ok": ok, "msg": "Valid SLUS-21291" if ok else "not a recognized S5 (USA) ISO"}))
            if self.path == "/api/peek":
                iso = data.get("iso", "")
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "file not found"}))
                off = int(str(data.get("off", "0")), 0)
                ln = max(1, min(256, int(str(data.get("len", "16")), 0)))
                with P.Iso(iso) as g:
                    b = g.rd(off, ln)
                ascii_ = "".join(chr(c) if 32 <= c < 127 else "." for c in b)
                return self._send(200, json.dumps(
                    {"hex": b.hex(" "), "ascii": ascii_}))
            if self.path == "/api/names":
                iso = data.get("iso", "")
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "file not found"}))
                with P.Iso(iso) as g:
                    ns = P.read_names(g)
                return self._send(200, json.dumps({"names": ns}))
            if self.path == "/api/rename":
                iso = data.get("iso", "")
                if not os.path.exists(iso):
                    return self._send(200, json.dumps({"error": "file not found"}))
                try:
                    P.backup(iso)
                    with P.Iso(iso, writable=True) as g:
                        P.set_name(g, int(data["index"]), str(data["name"]))
                    return self._send(200, json.dumps({"ok": True}))
                except Exception as e:
                    return self._send(200, json.dumps({"error": str(e)}))
            self._send(404, "{}")
        except Exception as e:
            self._send(200, json.dumps({"error": str(e)}))


def main():
    with socketserver.TCPServer(("127.0.0.1", PORT), H) as httpd:
        url = f"http://127.0.0.1:{PORT}/"
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        print(f"Suikoden V editor running at {url}  (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
