#!/bin/sh
set -eu

if command -v qdbus6 >/dev/null 2>&1; then
    qdbus=qdbus6
elif command -v qdbus >/dev/null 2>&1; then
    qdbus=qdbus
else
    echo "qdbus not found; add the widget manually from the panel menu." >&2
    exit 1
fi

script='
var target = null;
var already = false;
for (var i = 0; i < panelIds.length; i++) {
    var p = panelById(panelIds[i]);
    var widgets = p.widgets();
    for (var j = 0; j < widgets.length; j++) {
        if (widgets[j].type === "org.kde.plasma.systemtray") {
            target = p;
        }
        if (widgets[j].type === "org.dralk.ailimits") {
            already = true;
        }
    }
}
if (already) {
    print("already-present");
} else if (target !== null) {
    target.addWidget("org.dralk.ailimits");
    print("added-to-tray-panel");
} else if (panelIds.length > 0) {
    panelById(panelIds[0]).addWidget("org.dralk.ailimits");
    print("added-to-first-panel");
} else {
    print("no-panel");
}
'

"$qdbus" org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "$script"
