#!/bin/sh
set -eu

id=org.dralk.ailimits
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
installed="$data_home/plasma/plasmoids/$id"

# Stop optional non-systemd Oasis proxy process cleanly before removing the plasmoid files.
if [ -f "$installed/contents/code/oasis_proxy.py" ] && command -v python3 >/dev/null 2>&1; then
    python3 "$installed/contents/code/oasis_proxy.py" stop --direct >/dev/null 2>&1 || true
fi

kpackagetool6 -t Plasma/Applet -r "$id" || true

# Stop optional Oasis proxy systemd user service if present (never deletes user credentials).
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user stop oasis-proxy.service >/dev/null 2>&1 || true
    systemctl --user disable oasis-proxy.service >/dev/null 2>&1 || true
fi
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/oasis-proxy.service"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

rm -f "$data_home/applications/$id.desktop"
rm -f "$data_home/icons/hicolor/scalable/apps/$id.svg"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$data_home/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi
if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
fi

echo "Removed $id and its launcher entry. Restart Plasma or remove the leftover item from the panel."
