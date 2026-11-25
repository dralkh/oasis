import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/format.js" as F

ColumnLayout {
    id: row

    property var provider: null
    property double nowMs: 0
    property int warnThreshold: 80
    property int criticalThreshold: 95

    readonly property var meta: F.meta(provider ? provider.id : "")
    readonly property bool ok: provider
        && provider.status === "ok"
        && provider.windows
        && provider.windows.length > 0
    readonly property bool antigravity: provider && provider.id === "antigravity"
    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.70)
    readonly property color groupColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.82)

    Layout.fillWidth: true
    spacing: 2

    RowLayout {
        Layout.fillWidth: true
        spacing: 6

        Rectangle {
            width: 17
            height: 17
            radius: 4
            color: Qt.rgba(row.meta.color.r, row.meta.color.g, row.meta.color.b,
                           row.ok ? 0.18 : 0.08)

            Text {
                anchors.centerIn: parent
                text: row.meta.short
                color: row.ok ? row.meta.color : row.mutedColor
                font.pixelSize: 8
                font.bold: true
            }
        }

        Text {
            text: row.meta.name
            color: Kirigami.Theme.textColor
            font.pixelSize: 12
            font.bold: true
            Layout.alignment: Qt.AlignVCenter
        }

        Text {
            id: planAndSubText
            readonly property string rawPlan: row.provider && row.provider.plan
                ? String(row.provider.plan)
                : ""
            readonly property string planLabel: rawPlan.length > 0
                ? rawPlan.charAt(0).toUpperCase() + rawPlan.slice(1)
                : ""
            readonly property string subLabel: F.subscriptionText(row.provider, row.nowMs)
            readonly property string subTip: F.subscriptionTooltip(row.provider, row.nowMs)
            text: {
                if (planLabel.length > 0 && subLabel.length > 0) {
                    return planLabel + " · " + subLabel
                }
                return planLabel.length > 0 ? planLabel : subLabel
            }
            color: row.mutedColor
            font.pixelSize: 10
            elide: Text.ElideRight
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter

            HoverHandler {
                id: subHover
            }

            QQC2.ToolTip.visible: subHover.hovered && planAndSubText.subTip.length > 0
            QQC2.ToolTip.delay: 400
            QQC2.ToolTip.text: planAndSubText.subTip
        }

        Rectangle {
            id: resetChip
            readonly property var summary: F.resetCreditsSummary(row.provider, row.nowMs)
            readonly property bool atLimit: row.ok && F.peak(row.provider) >= 100
            readonly property color chipTone: {
                if (!summary) return row.mutedColor
                if (summary.expiringSoon) return Kirigami.Theme.neutralTextColor
                if (atLimit) return Kirigami.Theme.positiveTextColor
                return Kirigami.Theme.highlightColor
            }
            visible: row.ok && summary !== null
            radius: 4
            implicitWidth: resetLabel.implicitWidth + 10
            implicitHeight: 16
            Layout.alignment: Qt.AlignVCenter
            color: Qt.rgba(chipTone.r, chipTone.g, chipTone.b, 0.16)

            Text {
                id: resetLabel
                anchors.centerIn: parent
                text: F.resetBadgeText(row.provider, row.nowMs)
                color: resetChip.chipTone
                font.pixelSize: 9
                font.bold: true
            }

            HoverHandler {
                id: resetHover
            }

            QQC2.ToolTip.visible: resetHover.hovered && resetChip.visible
            QQC2.ToolTip.delay: 400
            QQC2.ToolTip.text: F.resetBadgeTooltip(row.provider, row.nowMs)
        }

        Text {
            visible: text.length > 0
            text: {
                if (!row.provider) return "";
                if (row.provider.stale) return "stale";
                if (row.provider.status === "expired") return "expired";
                if (row.provider.status === "error") return "error";
                if (row.provider.status === "ok" && !row.ok) return "no quota";
                return "";
            }
            color: row.provider && row.provider.status === "error"
                ? Kirigami.Theme.negativeTextColor
                : (row.provider && row.provider.stale
                    ? Kirigami.Theme.neutralTextColor
                    : row.mutedColor)
            font.pixelSize: 10
            Layout.alignment: Qt.AlignVCenter
        }
    }

    // Antigravity: one group label, with the group's reported windows stacked
    // underneath. This gives each bar the full width and keeps the weekly value
    // visibly attached to its model group.
    ColumnLayout {
        Layout.fillWidth: true
        Layout.leftMargin: 23
        spacing: 4
        visible: row.ok && row.antigravity

        Repeater {
            model: F.antigravityGroups(row.provider)

            delegate: RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Text {
                    text: modelData.label
                    Layout.preferredWidth: 52
                    Layout.alignment: Qt.AlignVCenter
                    color: row.groupColor
                    font.pixelSize: 10
                    elide: Text.ElideRight
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1

                    LimitBar {
                        window: modelData.five
                        nowMs: row.nowMs
                        warnThreshold: row.warnThreshold
                        criticalThreshold: row.criticalThreshold
                        labelOverride: "5h"
                        Layout.fillWidth: true
                    }

                    LimitBar {
                        window: modelData.weekly
                        nowMs: row.nowMs
                        warnThreshold: row.warnThreshold
                        criticalThreshold: row.criticalThreshold
                        labelOverride: "1w"
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }

    // Every other provider gets a full-width bar per window: easier to read
    // than two or three miniature bars sharing one line.
    ColumnLayout {
        Layout.fillWidth: true
        Layout.leftMargin: 23
        spacing: 1
        visible: row.ok && !row.antigravity

        Repeater {
            model: row.ok ? F.primaryWindows(row.provider) : []

            delegate: LimitBar {
                window: modelData
                nowMs: row.nowMs
                warnThreshold: row.warnThreshold
                criticalThreshold: row.criticalThreshold
                Layout.fillWidth: true
            }
        }
    }

    Text {
        Layout.fillWidth: true
        Layout.leftMargin: 23
        visible: !row.ok
        text: {
            if (!row.provider) return "";
            if (row.provider.status === "error") {
                return row.provider.error || row.provider.hint || "unavailable";
            }
            return row.provider.hint || F.statusText(row.provider);
        }
        color: row.provider && row.provider.status === "error"
            ? Kirigami.Theme.negativeTextColor
            : row.mutedColor
        font.pixelSize: 10
        elide: Text.ElideRight

        HoverHandler {
            id: alertHover
        }

        QQC2.ToolTip.visible: alertHover.hovered && text.length > 0
        QQC2.ToolTip.delay: 600
        QQC2.ToolTip.text: text
    }
}
