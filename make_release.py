#!/usr/bin/env python3
"""Build the single-file editor: dist/Suikoden5Editor.pyz (stdlib zipapp).

Bundles Editor/*.py + the s5_*.json reference tables into one executable Python
archive. Run it with `python3 dist/Suikoden5Editor.pyz` (double-click on Windows
with the Python launcher installed); a macOS double-click wrapper .command is
emitted alongside.
"""
import os, sys, shutil, stat, tempfile, zipapp, zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "Editor")
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "Suikoden5EditorPackage.pyz")

MAIN = "import s5editor\ns5editor.main()\n"

# Two small launchers, each written with the line endings its OS needs (cmd.exe wants
# CRLF; sh breaks on a stray CR). Both just find Python 3 and run the bundled .pyz that
# sits beside them. Emitted together from this one build step.
SH_LAUNCHER = '''#!/bin/sh
# Suikoden V Editor — macOS / Linux launcher (double-click on macOS).
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "Python 3 is required. Install it from https://www.python.org/downloads/"
  read -r _; exit 1
fi
exec "$PY" "$DIR/Suikoden5EditorPackage.pyz" "$@"
'''
BAT_LAUNCHER = '''@echo off
rem Suikoden V Editor - Windows launcher (double-click).
where py >nul 2>nul && ( py -3 "%~dp0Suikoden5EditorPackage.pyz" %* & goto :eof )
where python >nul 2>nul && ( python "%~dp0Suikoden5EditorPackage.pyz" %* & goto :eof )
echo Python 3 is required. Install from https://www.python.org/downloads/ and tick "Add to PATH".
pause
'''

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
    launchers = []
    mac = os.path.join(DIST, "Suikoden5EditorLauncher (Mac, Linux).command")
    with open(mac, "w", newline="\n") as f: f.write(SH_LAUNCHER)
    os.chmod(mac, os.stat(mac).st_mode | stat.S_IEXEC); launchers.append(mac)
    win = os.path.join(DIST, "Suikoden5EditorLauncher (Windows).bat")
    with open(win, "w", newline="\r\n") as f: f.write(BAT_LAUNCHER)
    launchers.append(win)
    print(f"bundled {n} files -> {OUT} ({os.path.getsize(OUT)//1024} KB)")
    for p in launchers: print(f"launcher -> {p}")

    # Package ONLY the 3 end-user files into the release zip (no source tree).
    ver = sys.argv[1] if len(sys.argv) > 1 else ""
    zname = f"Suikoden5Editor-{ver}.zip" if ver else "Suikoden5Editor.zip"
    zpath = os.path.join(DIST, zname)
    payload = [OUT] + launchers
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in payload:
            zi = zipfile.ZipInfo(os.path.basename(p))              # flat, no dirs
            zi.external_attr = (os.stat(p).st_mode & 0xFFFF) << 16  # keep +x bit
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as f: z.writestr(zi, f.read())
    print(f"release zip -> {zpath} ({os.path.getsize(zpath)//1024} KB, {len(payload)} files)")

if __name__ == "__main__":
    main()
