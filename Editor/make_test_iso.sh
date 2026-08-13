#!/usr/bin/env bash
# Clone the ISO (instant APFS copy-on-write), apply a sample character edit, and
# print how to verify it — never touches your original. Usage:
#   ./make_test_iso.sh "../ISO/Suikoden V - OG.iso" [charId] [hp]
set -euo pipefail
SRC="${1:?usage: make_test_iso.sh <iso> [charId] [hp]}"
CID="${2:-43}"   # 43 = Lyon
HP="${3:-999}"
DST="${SRC%.iso}.TEST.iso"
echo "Cloning $SRC -> $DST (cp -c, instant on APFS)…"
cp -c "$SRC" "$DST" 2>/dev/null || cp "$SRC" "$DST"
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/s5patch.py" verify "$DST"
echo "Before:"; python3 "$here/s5patch.py" dump "$DST" --id "$CID" --table stats | grep -E "Level|HP " | head -3
python3 "$here/s5patch.py" set "$DST" --id "$CID" --table stats --field HP --value "$HP"
echo "After:"; python3 "$here/s5patch.py" dump "$DST" --id "$CID" --table stats | grep "HP " | head -1
cat <<EOF

Test ISO written: $DST
Verify in an emulator (PCSX2):
  1. Load $DST as the disc.
  2. Start a NEW game (ISO edits apply to new games, not existing saves).
  3. Check character #$CID's HP.
Delete the test ISO when done:  rm "$DST"
EOF
