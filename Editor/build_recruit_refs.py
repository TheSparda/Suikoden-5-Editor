#!/usr/bin/env python3
"""Build Editor/s5_recruit_guide.json from Editor/recruit_guide_source.txt.

The source file holds one entry per recruitable character (where they are
found, how they are recruited, and their missable cutoff when a source
supports one), condensed from:
  - "Recruitment (Suikoden V)", Suikoden Wiki (Fandom)
    https://suikoden.fandom.com/wiki/Recruitment_(Suikoden_V)
  - yangxu, "Suikoden V Character Recruitment Quick FAQ" (GameFAQs)
    https://gamefaqs.gamespot.com/ps2/929668-suikoden-v/faqs/42169
  - "Suikoden V Recruitment Checklist" (skyrender.net)
    http://www.skyrender.net/suiko5_recruit.txt

Following the Suikoden III editor's build_guide_refs.py approach: entries
are keyed by character name, matched here against s5_characters.json (the
editor's own id table), and the build FAILS on any name it cannot match or
any recruitable id left uncovered, so bad data breaks the build instead of
shipping. Correct or blank, never wrong: a character with no verified
cutoff gets an empty "missable" string, never a guessed one.

The DoReMi elves (ReMiFa, MiFaSo, FaSoLa, SoLaTi, LaTiDo) are recruitable
but have no character id in the editor's table, so they are out of scope.

Usage: python3 build_recruit_refs.py [--check]
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "recruit_guide_source.txt")
OUT = os.path.join(HERE, "s5_recruit_guide.json")
CHARS = os.path.join(HERE, "s5_characters.json")

# Guide spelling -> editor table spelling, where they differ.
ALIASES = {
    "Freyjadour": "Hero/Prince",
    "Haswar": "Haswer",
    "Shinro": "Shirno",
    "Killey": "Killy",
    "Fuwalafuwalu": "Fualafualu",
}
RECRUITABLE_IDS = range(0, 109)   # ids 0..108; 109+ are story/antagonist rows


def parse_source(text):
    entries, cur = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = {"where": "", "how": "", "missable": ""}
            entries[m.group(1)] = cur
            continue
        m = re.match(r"^(where|how|missable):\s*(.*)$", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2).strip()
            continue
        raise ValueError("unparseable line: %r" % raw)
    return entries


def main():
    chars = {c["name"]: c["id"] for c in json.load(open(CHARS))}
    entries = parse_source(open(SRC, encoding="utf-8").read())
    out, used = {}, set()
    for name, e in entries.items():
        editor_name = ALIASES.get(name, name)
        if editor_name not in chars:
            raise SystemExit("guide name %r (editor %r) not in s5_characters.json"
                             % (name, editor_name))
        cid = chars[editor_name]
        if cid in used:
            raise SystemExit("duplicate entry for character id %d" % cid)
        used.add(cid)
        if not e["where"] or not e["how"]:
            raise SystemExit("entry %r is missing where/how" % name)
        out[str(cid)] = e
    missing = [i for i in RECRUITABLE_IDS if i not in used]
    if missing:
        raise SystemExit("no guide entry for recruitable ids: %s" % missing)
    extra = [i for i in used if i not in RECRUITABLE_IDS]
    if extra:
        raise SystemExit("guide entries for non-recruitable ids: %s" % extra)
    blob = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if cur != blob:
            raise SystemExit("s5_recruit_guide.json is stale; rerun without --check")
        print("s5_recruit_guide.json is up to date (%d entries)" % len(out))
        return
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(blob)
    print("wrote %s (%d entries)" % (OUT, len(out)))


if __name__ == "__main__":
    main()
