#!/usr/bin/env bash
# Date when SPTM entered a chip's boot chain by diffing BuildManifests across
# macOS releases. Fetches ONLY BuildManifest.plist remotely (HTTP range), not
# the ~15 GB IPSW. Any Mac device works — the manifest is the universal one.
#
# Usage: ./date-sptm-activation.sh [device]   (default Mac15,3 = M3 MBP 14")

set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$DIR/../../tools/ipsw"
DEV="${1:-Mac15,3}"

# Sample across the release history. Add/adjust versions as needed.
VERSIONS=(14.1 15.0 15.1 15.2 15.4 15.6 26.0 26.5)

for v in "${VERSIONS[@]}"; do
    out="$DIR/$v"; mkdir -p "$out"
    if ! ls "$out"/*/BuildManifest.plist >/dev/null 2>&1; then
        (cd "$out" && "$TOOL" download ipsw --device "$DEV" --version "$v" \
            --pattern '^BuildManifest.plist$' >/dev/null 2>&1) || { echo "$v: unavailable"; continue; }
    fi
    python3 "$DIR/check2.py" "$out"/*/BuildManifest.plist 2>/dev/null | grep -E "^[0-9]|M2 |M3 |M4 |M5 " || true
done
