#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=$(python3 -c 'import json; print(json.load(open("package/metadata.json"))["KPlugin"]["Version"])' 2>/dev/null || echo "dev")
out="$here/dist/oasis-${version}.plasmoid"

mkdir -p "$here/dist"
rm -f "$out"

(
    cd "$here/package"
    zip -r -9 "$out" . -x '*/__pycache__/*' '*.pyc' '*.tmp' '.DS_Store'
)

(
    cd "$here/dist"
    sha256sum "oasis-${version}.plasmoid" > "SHA256SUMS.txt"
)

echo "Built $out"
cat "$here/dist/SHA256SUMS.txt"
