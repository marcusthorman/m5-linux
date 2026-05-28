#!/usr/bin/env bash
# Extract iBoot blobs from the IPSW (LZFSE-compressed, NOT encrypted on M4+).
# Output: analysis/iboot/iBoot.<board>.RELEASE.payload  (gitignored)
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
IPSW="$ROOT/ipsw/UniversalMac_26.5_25F71_Restore.ipsw"
TOOL="$ROOT/tools/ipsw"
BOARDS="${@:-j604 j704 j716s}"   # M4, M5, M5 Pro by default

python3 - "$IPSW" "$DIR" "$BOARDS" <<'PY'
import zipfile, sys, os
ipsw, outdir, boards = sys.argv[1], sys.argv[2], sys.argv[3].split()
z = zipfile.ZipFile(ipsw)
for b in boards:
    p = f"Firmware/all_flash/iBoot.{b}.RELEASE.im4p"
    try:
        open(os.path.join(outdir, os.path.basename(p)), 'wb').write(z.read(p))
        print("extracted", b)
    except KeyError:
        print("missing", b)
PY

for b in $BOARDS; do
    f="$DIR/iBoot.$b.RELEASE.im4p"
    [[ -f "$f" ]] || continue
    "$TOOL" img4 im4p extract "$f" >/dev/null 2>&1 && echo "decompressed iBoot.$b"
done
