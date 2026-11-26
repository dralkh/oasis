#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
package="$here/package"
id=org.dralk.ailimits
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
installed="$data_home/plasma/plasmoids/$id"

if ! command -v kpackagetool6 >/dev/null 2>&1; then
    echo "kpackagetool6 not found (install plasma-framework / plasma-sdk)." >&2
    exit 1
fi

# Build and bundle the integrated Oasis Proxy engine when source & cargo are present.
bundled_proxy="$package/contents/bin/oasis-proxy"
if [ "${OASIS_SKIP_PROXY_BUILD:-0}" != "1" ] && [ -f "$here/proxy/Cargo.toml" ]; then
    if [ ! -x "$bundled_proxy" ] || [ "$here/proxy/Cargo.toml" -nt "$bundled_proxy" ]; then
        if command -v cargo >/dev/null 2>&1; then
            echo "Building integrated Oasis Proxy engine…"
            cargo build --release --locked --manifest-path "$here/proxy/Cargo.toml" --bin oasis-proxy
            mkdir -p "$package/contents/bin"
            cp -f "$here/proxy/target/release/oasis-proxy" "$bundled_proxy"
            chmod 0755 "$bundled_proxy"
        else
            echo "Note: cargo not found; installing Oasis in Direct CLI mode without bundled Oasis Proxy." >&2
        fi
    fi
fi

if [ -d "$installed" ]; then
    kpackagetool6 -t Plasma/Applet -u "$package"
else
    kpackagetool6 -t Plasma/Applet -i "$package"
fi

find "$installed" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

helper="$installed/contents/code/ai_limits.py"
chmod 0755 "$helper" 2>/dev/null || true
chmod 0755 "$installed/contents/code/oasisctl.py" 2>/dev/null || true
chmod 0755 "$installed/contents/code/oasis_intel.py" 2>/dev/null || true
chmod 0755 "$installed/contents/code/oasis_accounts.py" 2>/dev/null || true
chmod 0755 "$installed/contents/code/oasis_proxy.py" 2>/dev/null || true
chmod 0755 "$installed/contents/bin/oasis-proxy" 2>/dev/null || true

# Launcher entry. Oasis is a tray applet, so this adds it to the tray panel
# instead of opening a separate window.
apps_dir="$data_home/applications"
icons_dir="$data_home/icons/hicolor/scalable/apps"
mkdir -p "$apps_dir" "$icons_dir"
cp "$package/contents/icons/ai-limits.svg" "$icons_dir/$id.svg"
escaped_ctl=$(printf '%s' "$installed/contents/code/oasisctl.py" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\$/\\$/g; s/`/\\`/g')
cat > "$apps_dir/$id.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Oasis
Comment=AI subscription usage in the system tray
Exec=python3 "$escaped_ctl" launch
Icon=$id
Terminal=false
Categories=Utility;
Keywords=Oasis;AI;usage;quota;limits;
StartupNotify=false
EOF
chmod 0644 "$apps_dir/$id.desktop" "$icons_dir/$id.svg"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps_dir" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
fi

echo
python3 "$helper" selftest
python3 "$installed/contents/code/oasis_proxy.py" status >/dev/null 2>&1 || true
echo
echo "Installed as $id."
echo "Launch Oasis from the application menu, or add it with:"
echo "  right-click the panel → Add Widgets… → Oasis"
