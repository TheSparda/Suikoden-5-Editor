#!/usr/bin/env python3
"""Static dev server for the web editor.

Serves the repo root so /web and /Editor both resolve (the app fetches its Python
engine from ../Editor). Supports HTTP Range, which the browser needs for partial
reads and which the stdlib handler doesn't do.

    python3 web/serve.py [port]      ->  http://127.0.0.1:8000/web/index.html
"""
import os, re, sys, http.server, socketserver

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw): super().__init__(*a, directory=ROOT, **kw)
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()
    def send_head(self):
        rng = self.headers.get("Range")
        if not rng: return super().send_head()
        m = re.match(r"bytes=(\d+)-(\d*)", rng)
        path = self.translate_path(self.path)
        if not m or not os.path.isfile(path): return super().send_head()
        size = os.path.getsize(path)
        start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end: 
            self.send_error(416); return None
        f = open(path, "rb"); f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        remaining = end - start + 1
        while remaining > 0:
            chunk = f.read(min(1 << 20, remaining))
            if not chunk: break
            self.wfile.write(chunk); remaining -= len(chunk)
        f.close()
        return None

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler) as httpd:
        print(f"serving {ROOT} at http://127.0.0.1:{port}/web/index.html")
        httpd.serve_forever()
