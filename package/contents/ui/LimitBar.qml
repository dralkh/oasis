import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/format.js" as F

RowLayout {
    id: limit

    property var window: null
    property double nowMs: 0
    property int warnThreshold: 80
    property int criticalThreshold: 95
    property bool compact: false
    property string labelOverride: ""

    readonly property real used: window ? Math.max(0, Math.min(100, Number(window.used) || 0)) : 0
    readonly property bool idle: F.isIdle(window)
    readonly property bool passed: F.windowResetAvailable(window, nowMs)
    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.70)
    readonly property color tone: {
        if (!window) return Kirigami.Theme.disabledTextColor;
        if (idle || passed) return Kirigami.Theme.positiveTextColor;
        if (used >= criticalThreshold) return Kirigami.Theme.negativeTextColor;
        if (used >= warnThreshold) return Kirigami.Theme.neutralTextColor;
        return Kirigami.Theme.highlightColor;
    }

    Layout.fillWidth: true
    spacing: compact ? 3 : 5
    visible: window !== null && window !== undefined

    Text {
        text: limit.labelOverride.length > 0
            ? limit.labelOverride
            : (limit.window ? limit.window.short : "")
        Layout.preferredWidth: limit.compact ? 14 : 24
        Layout.alignment: Qt.AlignVCenter
        color: limit.mutedColor
        font.pixelSize: limit.compact ? 10 : 10
        font.bold: limit.compact
        elide: Text.ElideRight
    }

    UsageBar {
        Layout.fillWidth: true
        Layout.preferredHeight: limit.compact ? 5 : 8
        Layout.alignment: Qt.AlignVCenter
        value: limit.used
        tone: limit.tone
        estimated: limit.window ? !!limit.window.estimated : false
    }

    Text {
        text: F.percent(limit.window)
        Layout.preferredWidth: limit.compact ? 29 : 40
        Layout.alignment: Qt.AlignVCenter
        horizontalAlignment: Text.AlignRight
        color: limit.tone
        font.pixelSize: limit.compact ? 10 : 11
        font.bold: true
    }

    Text {
        text: F.resetShort(limit.window, limit.nowMs)
        Layout.preferredWidth: limit.compact ? 27 : 44
        Layout.alignment: Qt.AlignVCenter
        color: (limit.idle || limit.passed)
            ? Kirigami.Theme.positiveTextColor
            : limit.mutedColor
        font.pixelSize: 10
        elide: Text.ElideRight
        visible: text.length > 0
    }

    HoverHandler {
        id: hover
    }

    QQC2.ToolTip.visible: hover.hovered && limit.window !== null && limit.window !== undefined
    QQC2.ToolTip.delay: 600
    QQC2.ToolTip.text: {
        if (!limit.window) return "";
        var lines = [];
        lines.push(limit.window.label + ": " + F.percent(limit.window) + " used");
        var detail = F.resetDetail(limit.window, limit.nowMs);
        if (detail.length > 0) lines.push("Resets " + detail);
        else if (limit.idle) lines.push("Ready — no active window");
        if (limit.window.estimated) {
            lines.push("Estimated: the provider did not report this bucket");
        }
        return lines.join("\n");
    }
}
