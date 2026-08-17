#!/usr/bin/env python3
"""Build the single-file editor: dist/Suikoden5Editor.pyz (stdlib zipapp).

Bundles Editor/*.py + the s5_*.json reference tables into one executable Python
archive. Run it with `python3 dist/Suikoden5Editor.pyz` (double-click on Windows
with the Python launcher installed); a macOS double-click wrapper .command is
emitted alongside.
"""
import os, shutil, stat, tempfile, zipapp

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "Editor")
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "Suikoden5Editor.pyz")

MAIN = "import s5editor\ns5editor.main()\n"
COMMAND = """#!/bin/bash
cd "$(dirname "$0")"
exec python3 "./Suikoden5Editor.pyz"
"""

def main():
    os.makedirs(DIST, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        n = 0
        for name in sorted(os.listdir(SRC)):
            if name.endswith(".py") or (name.startswith("s5_") and name.endswith(".json")):
                shutil.copy2(os.path.join(SRC, name), os.path.join(tmp, name)); n += 1
        with open(os.path.join(tmp, "__main__.py"), "w") as f:
            f.write(MAIN)
        zipapp.create_archive(tmp, OUT, interpreter="/usr/bin/env python3", compressed=True)
    os.chmod(OUT, os.stat(OUT).st_mode | stat.S_IEXEC)
    cmd = os.path.join(DIST, "Suikoden5Editor (Mac).command")
    with open(cmd, "w") as f: f.write(COMMAND)
    os.chmod(cmd, os.stat(cmd).st_mode | stat.S_IEXEC)
    print(f"bundled {n} files -> {OUT} ({os.path.getsize(OUT)//1024} KB)")
    print(f"mac double-click wrapper -> {cmd}")

if __name__ == "__main__":
    main()
