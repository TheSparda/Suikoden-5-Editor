#!/usr/bin/env python3
"""Round-trip + negative controls for the .ROM overlay and DATA.PAK commands
(s5rom.py / s5pak.py and the s5patch CLI built on them), against the real disc.

Drives the actual CLI (python3 Editor/s5patch.py ...) as a subprocess, not
mocks. Write tests run on a throwaway copy of the ISO placed next to the
original and deleted afterwards; the pristine ISO is only ever read.

Skips cleanly (exit 0) when no real ISO is available: set S5_ISO or keep the
disc at <repo>/ISO/suikoden5-us.iso.

Run directly (python3 tests/rom_pak.py) or via tests/rom-pak.mjs."""
import os, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.dirname(HERE)
REPO = os.path.dirname(WEB)
ED = os.path.join(REPO, "Editor")
sys.path.insert(0, ED)

ISO = os.environ.get("S5_ISO") or os.path.join(REPO, "ISO", "suikoden5-us.iso")
PATCH = os.path.join(ED, "s5patch.py")

n = [0]; bad = [0]
def chk(name, cond, extra=""):
    n[0] += 1
    print(("PASS " if cond else "FAIL ") + name + ("  " + extra if extra else ""))
    if not cond: bad[0] += 1

def cli(*args):
    return subprocess.run([sys.executable, PATCH, *args],
                          capture_output=True, text=True)

def expect_rom_error(name, fn, kinds):
    """fn must raise one of `kinds` (the codec's own clean errors). Any other
    exception, or none, is a failure: corrupt input must not leak raw
    IndexError/struct.error tracebacks out of the codec."""
    try:
        fn()
    except kinds:
        chk(name, True)
    except Exception as e:
        chk(name, False, f"wrong exception {type(e).__name__}: {e}")
    else:
        chk(name, False, "no exception raised")


def main():
    if not os.path.exists(ISO):
        print(f"SKIP rom-pak: no real ISO at {ISO} (set S5_ISO).")
        return 0
    import s5rom, s5pak, s5patch

    tmp = tempfile.mkdtemp(prefix="s5rompak-")
    copy_iso = os.path.join(os.path.dirname(ISO), ".rompak-test-copy.iso")
    try:
        # ---- codec: synthetic round trips ----------------------------------
        payload = bytes(range(256)) * 40 + b"Suikoden V overlay test " * 64
        for t in ("szl", "non", "epb"):
            blob = s5rom.pack_container(payload, t)
            chk(f"pack/unpack round trip ({t})",
                s5rom.unpack_image(blob) == payload)
            infos = s5rom.member_info(blob)
            chk(f"member_info ({t})", len(infos) == 1 and infos[0]["type"] == t
                and infos[0]["usize"] == len(payload))
        chk("pack/unpack round trip (empty payload)",
            s5rom.unpack_image(s5rom.pack_container(b"", "szl")) == b"")

        # hand-built two-member chain: non member, 0x10 alignment, szl member
        a, b = b"A" * 100, bytes(range(200)) * 3
        def hdr(type_word, usize, total):
            h = struct.pack("<IIIII", s5rom.MAGIC, 0x3C, type_word, usize, total)
            return h + b"\xFF" * (0x40 - len(h))
        szl_b = s5rom.lzss_compress(b)
        m0 = hdr(s5rom.TYPE_WORDS["non"], len(a), 0) + a
        m0 += b"\x00" * (-len(m0) % 0x10)
        chain = m0 + hdr(s5rom.TYPE_WORDS["szl"], len(b), 0) + szl_b
        members = s5rom.unpack(chain)
        chk("two-member chain unpacks in order",
            [m[2] for m in members] == [a, b]
            and [m[1] for m in members] == ["non", "szl"])
        chk("two-member chain image is the concatenation",
            s5rom.unpack_image(chain) == a + b)

        # ---- codec: unsupported types --------------------------------------
        for t in ("hff",):
            expect_rom_error(f"pack_container refuses to encode {t}",
                             lambda t=t: s5rom.pack_container(payload, t),
                             (s5rom.UnsupportedMemberError,))
            junk = hdr(s5rom.TYPE_WORDS[t], 16, 0x50) + b"\x00" * 16
            expect_rom_error(f"unpack raises unsupported for {t} member",
                             lambda junk=junk: s5rom.unpack(junk),
                             (s5rom.UnsupportedMemberError,))

        # ---- codec: corrupt input errors cleanly ---------------------------
        with open(ISO, "rb") as f:
            war = next(o for o in s5patch.list_overlays(ISO)
                       if o["name"] == "WAR.ROM")
            f.seek(war["lba"] * 2048); war_raw = f.read(war["size"])
        expect_rom_error("truncated .ROM raises RomError",
                         lambda: s5rom.unpack(war_raw[:len(war_raw) // 2]),
                         (s5rom.RomError,))
        bad_magic = struct.pack("<I", 0xDEADBEEF) + war_raw[4:]
        expect_rom_error("bad magic raises RomError",
                         lambda: s5rom.unpack(bad_magic), (s5rom.RomError,))
        bad_type = war_raw[:8] + struct.pack("<I", 0x12345678) + war_raw[12:]
        expect_rom_error("unknown type word raises RomError",
                         lambda: s5rom.unpack(bad_type), (s5rom.RomError,))

        # ---- CLI: overlays / overlay-verify / overlay-dump ------------------
        r = cli("overlays", ISO)
        lines = [l for l in r.stdout.splitlines() if l.strip().endswith("szl")]
        chk("CLI overlays lists 17 szl overlays", r.returncode == 0
            and len(lines) == 17, f"rc={r.returncode} lines={len(lines)}")
        war_line = next((l for l in lines if l.startswith("WAR.ROM")), "")
        chk("CLI overlays WAR sizes", "174751" in war_line
            and "392260" in war_line, war_line.strip())

        r = cli("overlay-verify", ISO)
        chk("CLI overlay-verify 17/17", r.returncode == 0
            and "17/17 overlays round-trip clean" in r.stdout,
            r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[:200])

        war_bin = os.path.join(tmp, "WAR.bin")
        r = cli("overlay-dump", ISO, "--name", "WAR", "--out", war_bin)
        dumped = open(war_bin, "rb").read() if os.path.exists(war_bin) else b""
        chk("CLI overlay-dump WAR matches the engine unpack",
            r.returncode == 0 and dumped == s5rom.unpack_image(war_raw),
            f"{len(dumped)} bytes")

        # ---- CLI: pak-list / pak-extract ------------------------------------
        with s5pak.DataPak(ISO) as pak:
            entries = pak.entries()
        chk("s5pak finds 7,734 DATA.PAK files", len(entries) == 7734,
            f"{len(entries)}")
        r = cli("pak-list", ISO)
        listed = [l for l in r.stdout.splitlines() if "  LBA " in l]
        chk("CLI pak-list lists every file", r.returncode == 0
            and len(listed) == len(entries), f"{len(listed)} lines")
        r = cli("pak-list", ISO, "--filter", "README.TXT")
        chk("CLI pak-list substring filter",
            r.returncode == 0 and len(r.stdout.strip().splitlines()) == 1
            and "README.TXT" in r.stdout)
        import fnmatch
        want = [e for e in entries
                if fnmatch.fnmatchcase(e["path"].lower(), "field00/*.rom")]
        r = cli("pak-list", ISO, "--filter", "FIELD00/*.ROM")
        got = [l for l in r.stdout.splitlines() if "  LBA " in l]
        chk("CLI pak-list glob filter", r.returncode == 0
            and len(got) == len(want), f"{len(got)} vs {len(want)}")

        readme = os.path.join(tmp, "README.TXT")
        r = cli("pak-extract", ISO, "--path", "README.TXT", "--out", readme)
        chk("CLI pak-extract README.TXT (6,098 bytes)",
            r.returncode == 0 and os.path.getsize(readme) == 6098)

        # ---- real epb-first ROMs now unpack, cross-checked ----------------
        epb_checked = None
        with s5pak.DataPak(ISO) as pak:
            for e in entries:
                if not e["path"].endswith(".ROM"): continue
                lba = e["extents"][0][0]
                pak.f.seek(pak.base + lba * 2048)
                if struct.unpack_from("<I", pak.f.read(16), 8)[0] != 0x62706500:
                    continue
                raw_path = os.path.join(tmp, "epb.rom")
                pak.extract(e["path"], raw_path)
                blob = open(raw_path, "rb").read()
                try:
                    members = s5rom.unpack(blob)
                except s5rom.UnsupportedMemberError:
                    continue  # chain reaches an hff member; try the next
                codec, first, decoded = s5patch.rom_decode(blob)
                if decoded and members and members[0][2] == first:
                    epb_checked = (e["path"], len(members))
                    break
        chk("a real epb-first DATA.PAK ROM unpacks in s5rom, first member "
            "matching the engine's independent bpe decoder",
            epb_checked is not None,
            f"{epb_checked[0]} ({epb_checked[1]} members)"
            if epb_checked else "")

        # ---- writes: overlay-set on a throwaway copy -------------------------
        import shutil
        shutil.copy2(ISO, copy_iso)
        hensei = next(o for o in s5patch.list_overlays(ISO)
                      if o["name"] == "HENSEI.ROM")
        with open(ISO, "rb") as f:
            f.seek(hensei["lba"] * 2048); hensei_before = f.read(hensei["size"])

        r = cli("overlay-set", copy_iso, "--name", "WAR", "--image", war_bin)
        chk("CLI overlay-set WAR (unmodified image) succeeds + self-verifies",
            r.returncode == 0 and "byte-identical" in r.stdout,
            (r.stdout + r.stderr).strip()[:200])
        chk("overlay-set made a .bak of the pre-write copy",
            os.path.exists(copy_iso + ".bak")
            and os.path.getsize(copy_iso + ".bak") == os.path.getsize(ISO))

        war_bin2 = os.path.join(tmp, "WAR2.bin")
        cli("overlay-dump", copy_iso, "--name", "WAR", "--out", war_bin2)
        chk("re-dumped WAR from the written copy is byte-identical",
            open(war_bin2, "rb").read() == dumped)

        # every differing byte must sit inside WAR's sector slot or its
        # 8 directory-size bytes; nothing else on the disc may move
        slot = ((war["size"] + 2047) // 2048) * 2048
        rec_off, _ = s5patch._find_ovl_dirrec(ISO, "WAR.ROM")
        allowed = [(war["lba"] * 2048, war["lba"] * 2048 + slot),
                   (rec_off + 10, rec_off + 18)]
        def diff_ranges(path_a, path_b):
            out = []; CH = 1 << 22; pos = 0
            with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
                while True:
                    x, y = fa.read(CH), fb.read(CH)
                    if not x: break
                    if x != y:
                        for s in range(0, len(x), 65536):
                            xs, ys = x[s:s + 65536], y[s:s + 65536]
                            if xs != ys:
                                for i in range(len(xs)):
                                    if xs[i] != ys[i]: out.append(pos + s + i)
                    pos += len(x)
            return out
        diffs = diff_ranges(ISO, copy_iso)
        stray = [o for o in diffs
                 if not any(s <= o < e for s, e in allowed)]
        chk("overlay-set touched only WAR's slot + directory size",
            len(diffs) > 0 and not stray,
            f"{len(diffs)} diff bytes, {len(stray)} stray")

        # negative control: HENSEI's unmodified image repacks to 71,814 bytes,
        # 134 past its 71,680-byte sector allocation, and must be refused
        hensei_bin = os.path.join(tmp, "HENSEI.bin")
        cli("overlay-dump", ISO, "--name", "HENSEI", "--out", hensei_bin)
        r = cli("overlay-set", copy_iso, "--name", "HENSEI",
                "--image", hensei_bin)
        chk("CLI overlay-set refuses HENSEI (repack outgrows the slot)",
            r.returncode == 2 and "71814" in r.stderr
            and "71680" in r.stderr, r.stderr.strip()[:160])
        with open(copy_iso, "rb") as f:
            f.seek(hensei["lba"] * 2048); hensei_after = f.read(hensei["size"])
        chk("refused write left HENSEI's bytes untouched",
            hensei_after == hensei_before)
    finally:
        for p in (copy_iso, copy_iso + ".bak", copy_iso + ".s5mod.json"):
            if os.path.exists(p): os.remove(p)
        import shutil; shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{n[0] - bad[0]}/{n[0]} passed")
    return 1 if bad[0] else 0


if __name__ == "__main__":
    raise SystemExit(main())
