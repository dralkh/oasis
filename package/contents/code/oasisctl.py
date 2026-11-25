#!/usr/bin/env python3
"""Start or quit the Oasis tray applet.

Oasis is a Plasma applet, so "quit" means removing it from the panel. The
launcher entry runs `launch`, which adds it back to the panel that contains
the system tray.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Optional

APPLET = "org.dralk.ailimits"

ADD_SCRIPT = """
var containments = desktops().concat(panels());
var present = false;
var target = null;
for (var i = 0; i < containments.length; i++) {
    var widgets = containments[i].widgets();
    for (var j = 0; j < widgets.length; j++) {
        if (widgets[j].type === "__APPLET__") present = true;
        if (widgets[j].type === "org.kde.plasma.systemtray") target = containments[i];
    }
}
if (present) {
    print("already-present");
} else if (target !== null) {
    target.addWidget("__APPLET__");
    print("added-to-tray-panel");
} else if (panels().length > 0) {
    panels()[0].addWidget("__APPLET__");
    print("added-to-first-panel");
} else {
    print("no-panel");
}
""".replace("__APPLET__", APPLET)

REMOVE_SCRIPT = """
var containments = desktops().concat(panels());
var removed = 0;
for (var i = 0; i < containments.length; i++) {
    var widgets = containments[i].widgets();
    for (var j = widgets.length - 1; j >= 0; j--) {
        if (widgets[j].type === "__APPLET__") {
            widgets[j].remove();
            removed += 1;
        }
    }
}
print(removed > 0 ? "removed" : "not-present");
""".replace("__APPLET__", APPLET)


def qdbus() -> Optional[str]:
    for name in ("qdbus6", "qdbus"):
        found = shutil.which(name)
        if found:
            return found
    return None


def evaluate(script: str) -> tuple[int, str]:
    tool = qdbus()
    if not tool:
        return 127, "qdbus not found"
    try:
        result = subprocess.run(
            [
                tool,
                "org.kde.plasmashell",
                "/PlasmaShell",
                "org.kde.PlasmaShell.evaluateScript",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode, output


def notify(message: str) -> None:
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(
            ["notify-send", "-a", "Oasis", "-i", APPLET, "Oasis", message],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else "launch"

    if action == "launch":
        status, output = evaluate(ADD_SCRIPT)
        if status != 0:
            notify(f"Could not reach Plasma: {output or 'qdbus failed'}")
            return 1
        if output == "already-present":
            notify("Oasis is already running in the system tray.")
        elif output == "no-panel":
            notify("No Plasma panel found. Add Oasis from the widget menu.")
            return 1
        elif output not in ("added-to-tray-panel", "added-to-first-panel"):
            notify(f"Could not add Oasis: {output or 'unknown error'}")
            return 1
        return 0

    if action in ("quit", "remove"):
        status, output = evaluate(REMOVE_SCRIPT)
        if status != 0:
            notify(f"Could not reach Plasma: {output or 'qdbus failed'}")
            return 1
        if output == "removed":
            print("removed")
            return 0
        if output == "not-present":
            notify("Oasis is not in the system tray.")
            return 0
        notify(f"Could not remove Oasis: {output or 'unknown error'}")
        return 1

    print(f"usage: {argv[0]} [launch|quit]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
