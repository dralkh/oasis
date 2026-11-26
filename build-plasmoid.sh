#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
version=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["KPlugin"]["Version"])' "$here/package/metadata.json" 2>/dev/null || echo "dev")
out="$here/dist/oasis-${version}.plasmoid"

# Build and bundle integrated Oasis Proxy engine when source & cargo are present.
bundled_proxy="$here/package/contents/bin/oasis-proxy"
if [ "${OASIS_SKIP_PROXY_BUILD:-0}" != "1" ] && [ -f "$here/proxy/Cargo.toml" ]; then
    if [ ! -x "$bundled_proxy" ] || [ "$here/proxy/Cargo.toml" -nt "$bundled_proxy" ]; then
        if command -v cargo >/dev/null 2>&1; then
            cargo build --release --locked --manifest-path "$here/proxy/Cargo.toml" --bin oasis-proxy
            mkdir -p "$here/package/contents/bin"
            cp -f "$here/proxy/target/release/oasis-proxy" "$bundled_proxy"
            chmod 0755 "$bundled_proxy"
        fi
    fi
fi

mkdir -p "$here/dist"
rm -f "$out"

python3 - "$here/package" "$out" <<'PY'
import os
import sys
import zipfile
from pathlib import Path

pkg_dir = Path(sys.argv[1]).resolve()
out_path = Path(sys.argv[2]).resolve()

FIXED_TIME = (2026, 1, 1, 0, 0, 0)
SKIP_DIRS = {"__pycache__"}
SKIP_FILES = {".DS_Store"}
SKIP_SUFFIXES = (".pyc", ".tmp")

files = []
for root, dirs, filenames in os.walk(pkg_dir):
    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
    for fn in sorted(filenames):
        if fn in SKIP_FILES or fn.endswith(SKIP_SUFFIXES):
            continue
        full = Path(root) / fn
        rel = full.relative_to(pkg_dir).as_posix()
        files.append((rel, full))

files.sort(key=lambda pair: pair[0])

with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    for rel, full in files:
        zi = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
        zi.compress_type = zipfile.ZIP_DEFLATED
        is_exec = (rel.startswith("contents/code/") and rel.endswith(".py")) or rel.startswith("contents/bin/")
        mode = 0o755 if is_exec else 0o644
        zi.external_attr = (0o100000 | mode) << 16
        zf.writestr(zi, full.read_bytes())
PY

(
    cd "$here/dist"
    sha256sum "oasis-${version}.plasmoid" > "SHA256SUMS.txt"
)

echo "Built $out"
cat "$here/dist/SHA256SUMS.txt"
