#!/usr/bin/env bash
# Extract and decompress the SPTM / TXM monitor binaries from the Universal IPSW.
#
# SPTM ships as a per-chip firmware blob: Firmware/sptm.<chip>.release.im4p.
# The payload is LZFSE-compressed and NOT encrypted (no KBAG) — it decompresses
# to a plain ARM64e Mach-O (MH_EXECUTE) that can be disassembled directly.
#
# Output: analysis/sptm/sptm.<chip>.release.payload  (gitignored — Apple firmware)
# Requires: ipsw tool in ../../tools/ipsw

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IPSW="$ROOT/ipsw/UniversalMac_26.5_25F71_Restore.ipsw"
TOOL="$ROOT/tools/ipsw"

# Chips with SPTM blobs in 25F71. Note: no t6040 (M4 Pro) or t6051 (M5 Max).
CHIPS=(t8132 t6041 t8142 t6050)   # M4, M4 Max, M5, M5 Pro

python3 - "$IPSW" "$SCRIPT_DIR" <<'PY'
import zipfile, sys, os
ipsw, outdir = sys.argv[1], sys.argv[2]
z = zipfile.ZipFile(ipsw)
for n in z.namelist():
    base = n.rsplit('/', 1)[-1]
    if base.startswith(('sptm.', 'txm.')) and n.endswith('.im4p'):
        open(os.path.join(outdir, base), 'wb').write(z.read(n))
        print("extracted", base)
PY

for c in "${CHIPS[@]}"; do
    f="$SCRIPT_DIR/sptm.$c.release.im4p"
    [[ -f "$f" ]] || { echo "skip $c (no blob)"; continue; }
    "$TOOL" img4 im4p extract "$f" >/dev/null 2>&1
    echo "decompressed sptm.$c -> $(basename "$f" .im4p).payload"
done

echo "Done. Disassemble with: $TOOL macho disass sptm.t8132.release.payload"
