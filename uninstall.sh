#!/bin/sh
set -eu

id=org.dralk.ailimits
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"

kpackagetool6 -t Plasma/Applet -r "$id" || true

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
