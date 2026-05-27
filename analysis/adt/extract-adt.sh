#!/usr/bin/env bash
# Extract Apple Device Trees for all M4/M5 SoCs from the Universal IPSW.
# Run this once the IPSW download completes.
#
# Output: analysis/adt/<soc>/*.json  (one file per SoC per device variant)
# Requires: ipsw tool in ../../tools/ipsw

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IPSW="$PROJECT_ROOT/ipsw/UniversalMac_26.5_25F71_Restore.ipsw"
TOOL="$PROJECT_ROOT/tools/ipsw"
OUT="$SCRIPT_DIR"

if [[ ! -f "$IPSW" ]]; then
    echo "ERROR: IPSW not found at $IPSW"
    echo "Still downloading? Check: ls -lh $PROJECT_ROOT/ipsw/"
    exit 1
fi

echo "=== Extracting kernels for M4/M5 SoCs ==="
KERNELS=(
    "kernelcache.release.t8132"   # M4 base
    "kernelcache.release.t6040"   # M4 Pro
    "kernelcache.release.t6041"   # M4 Max
    "kernelcache.release.t8142"   # M5 base
    "kernelcache.release.t6050"   # M5 Pro
    "kernelcache.release.t6051"   # M5 Max
)

mkdir -p "$OUT/kernels"
"$TOOL" extract --kernel "$IPSW" --output "$OUT/kernels/" 2>&1 || true
ls -lh "$OUT/kernels/"

echo ""
echo "=== IPSW component inventory ==="
"$TOOL" info "$IPSW" 2>&1 | tee "$OUT/ipsw-inventory.txt"

echo ""
echo "=== Extracting device trees ==="
mkdir -p "$OUT/dtrees"
"$TOOL" extract --dtree "$IPSW" --output "$OUT/dtrees/" 2>&1 || true
ls "$OUT/dtrees/" 2>/dev/null || echo "No dtrees extracted — may need different flag"

echo ""
echo "=== Searching for SPTM-related components ==="
python3 - <<'PYEOF'
import zipfile, sys, os

ipsw_path = os.environ.get('IPSW', '')
if not ipsw_path:
    ipsw_path = sys.argv[1] if len(sys.argv) > 1 else None

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
ipsw_path = os.path.join(project_root, 'ipsw', 'UniversalMac_26.5_25F71_Restore.ipsw')

print(f"Scanning: {ipsw_path}")
with zipfile.ZipFile(ipsw_path) as z:
    names = z.namelist()
    print(f"Total entries: {len(names)}")
    print()

    # Look for SPTM and related security firmware
    keywords = ['sptm', 'SPTM', 'sep', 'SEP', 'TrustCache', 'localPolicy',
                'kernelmanagement', 'iboot', 'iBoot', 'RestoreRamDisk',
                'cryptex', 'Firmware']
    print("=== Potentially interesting components ===")
    for name in sorted(names):
        if any(kw.lower() in name.lower() for kw in keywords):
            info = z.getinfo(name)
            size_mb = info.file_size / 1024 / 1024
            print(f"  {name:70s} {size_mb:8.1f} MB")

    print()
    print("=== All .im4p / .img4 components (firmware blobs) ===")
    for name in sorted(names):
        if name.endswith(('.im4p', '.img4', '.dmg')):
            info = z.getinfo(name)
            size_mb = info.file_size / 1024 / 1024
            print(f"  {name:70s} {size_mb:8.1f} MB")
PYEOF

echo ""
echo "Done. Next steps:"
echo "  1. Check analysis/adt/ipsw-inventory.txt for component list"
echo "  2. Run: ../../tools/ipsw macho info kernels/kernelcache.release.t8132 --symbols | grep -i sptm"
echo "  3. Check whether SPTM blob is encrypted or accessible"
