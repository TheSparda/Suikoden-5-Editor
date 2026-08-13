# Suikoden V ISO & Save Editor

A cross-platform editor for **Suikoden V** (PS2, USA `SLUS-21291`), modeled on the
[Suikoden III editor](https://github.com/TheSparda/Suikoden-3-Editor). It runs as a
local web app in your browser. Nothing is uploaded; the server only touches the file
you point it at. It contains **no game data** — supply your own legally-obtained ISO.

> **Status: early scaffold.** The ISO is validated by serial and you can read/poke raw
> bytes at absolute offsets. The named character editor is still gated while the record
> layout is verified (see below).

## Run

- **macOS:** double-click `Start Editor (Mac).command`
- **Windows:** double-click `Start Editor (Windows).bat`
- **Any:** `cd Editor && python3 s5editor.py`

Requires Python 3.8+ (standard library only, no pip installs).

## Layout

```
Editor/
  s5editor.py            local web app (open ISO, verify, raw hex peek)
  s5patch.py             ISO engine + CLI (verify / dump-char / set-field)
  s5fields.py            character field schema (offsets PROVISIONAL — see header)
  s5_char_struct.json    RAM save struct (from the cheat table) — reliable
  s5_iso_seeks.json      ISO seek offsets per exe method (research)
  Suikoden5_ISO_offsets.md  reverse-engineering notes
Start Editor (Mac).command / (Windows).bat   launchers
```

## CLI

```bash
cd Editor
python3 s5patch.py verify    "/path/to/Suikoden V.iso"
python3 s5patch.py dump-char "/path/to/Suikoden V.iso" --index 0
```

## Reverse-engineering status

- **Serial / ISO validation:** done — `SLUS_212.91` at `0x828BD`.
- **Save-side RAM struct:** extracted from the Cheat Engine table (`s5_char_struct.json`),
  reliable; basis for the upcoming memory-card save editor.
- **ISO character table:** the exe (`Patch1_Click`) seeks per sub-block and derives each
  character's ROM address from the RTF name list embedded in `Form1.resources` — it is
  **not** a plain `base + index*stride`. Verifying that mapping (by extracting the RTF) is
  the next step before named ISO character editing is enabled.

## Credits

ISO offsets reverse-engineered from Tony H's Suikoden V editor
([codehut.gshi.org](https://codehut.gshi.org/)). Not affiliated with Konami.
