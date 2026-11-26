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
    property bool accountBusy: false
    property bool switcherOpen: false

    signal switchAccountRequested(string providerId, string accountId)
    signal manageAccountsRequested(string providerId)

    readonly property var meta: F.meta(provider ? provider.id : "")
    readonly property bool ok: provider
        && provider.status === "ok"
        && provider.windows
        && provider.windows.length > 0
    readonly property bool antigravity: provider && provider.id === "antigravity"
    readonly property bool hasAccountSupport: !!(provider && provider.supports_accounts)
    readonly property var accountsList: provider && provider.accounts ? provider.accounts : []
    readonly property string activeAccountLabel: provider && provider.account ? String(provider.account) : ""
    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.70)
    readonly property color groupColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.82)

    function previewChipColor(chip) {
        if (!chip) return row.mutedColor
        if (chip.ready) return Kirigami.Theme.positiveTextColor
        if (chip.used >= row.criticalThreshold) return Kirigami.Theme.negativeTextColor
        if (chip.used >= row.warnThreshold) return Kirigami.Theme.neutralTextColor
        return Kirigami.Theme.highlightColor
    }

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

        // Inline account switcher pill
        Rectangle {
            id: accountPill
            visible: row.hasAccountSupport && (row.activeAccountLabel.length > 0 || row.accountsList.length > 0)
            radius: 4
            implicitWidth: Math.min(138, accPillRow.implicitWidth + 10)
            implicitHeight: 16
            Layout.alignment: Qt.AlignVCenter
            color: row.switcherOpen
                ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                : (accPillHover.hovered
                    ? Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.14)
                    : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08))
            border.width: row.switcherOpen ? 1 : 0
            border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.45)

            RowLayout {
                id: accPillRow
                anchors.centerIn: parent
                width: Math.min(implicitWidth, accountPill.implicitWidth - 8)
                spacing: 3

                Text {
                    text: F.shortAccountLabel(row.activeAccountLabel || "Accounts")
                    color: row.switcherOpen || accPillHover.hovered
                        ? Kirigami.Theme.textColor
                        : row.mutedColor
                    font.pixelSize: 9
                    font.bold: row.accountsList.length > 1 || row.switcherOpen
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }

                Text {
                    visible: row.accountsList.length > 1
                    text: "(" + row.accountsList.length + ")"
                    color: Kirigami.Theme.highlightColor
                    font.pixelSize: 8
                    font.bold: true
                }

                Text {
                    text: row.switcherOpen ? "▴" : "▾"
                    color: row.mutedColor
                    font.pixelSize: 8
                }
            }

            HoverHandler { id: accPillHover }
            TapHandler {
                onTapped: row.switcherOpen = !row.switcherOpen
            }

            QQC2.ToolTip.visible: accPillHover.hovered
            QQC2.ToolTip.delay: 450
            QQC2.ToolTip.text: (row.activeAccountLabel ? row.activeAccountLabel + "\n" : "")
                + (row.accountsList.length > 1
                    ? "Click to switch between " + row.accountsList.length + " accounts or add one"
                    : "Click to save or add another account")
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

    // Inline expandable account switcher tray
    Rectangle {
        visible: row.switcherOpen && row.hasAccountSupport
        Layout.fillWidth: true
        Layout.leftMargin: 23
        Layout.topMargin: 2
        Layout.bottomMargin: 3
        radius: 6
        implicitHeight: trayCol.implicitHeight + 10
        color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.05)
        border.width: 1
        border.color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.13)

        ColumnLayout {
            id: trayCol
            anchors.fill: parent
            anchors.margins: 5
            spacing: 3

            Repeater {
                model: row.accountsList

                delegate: Rectangle {
                    id: accItem
                    required property var modelData
                    readonly property bool isActive: !!modelData.active
                    readonly property var chips: F.accountPreviewChips(modelData, row.nowMs)
                    readonly property string statusBadge: F.accountStatusBadge(modelData)

                    Layout.fillWidth: true
                    implicitHeight: accItemRow.implicitHeight + 8
                    radius: 4
                    color: isActive
                        ? Qt.rgba(row.meta.color.r, row.meta.color.g, row.meta.color.b, 0.14)
                        : (accItemHover.hovered
                            ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.14)
                            : "transparent")

                    RowLayout {
                        id: accItemRow
                        anchors.fill: parent
                        anchors.margins: 4
                        spacing: 5

                        Rectangle {
                            implicitWidth: 6
                            implicitHeight: 6
                            radius: 3
                            color: accItem.isActive
                                ? Kirigami.Theme.positiveTextColor
                                : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.25)
                        }

                        Text {
                            text: String(modelData.label || row.meta.name)
                            color: Kirigami.Theme.textColor
                            font.pixelSize: 10
                            font.bold: accItem.isActive
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }

                        // Compact live usage preview chips
                        RowLayout {
                            visible: accItem.chips.length > 0
                            spacing: 3

                            Repeater {
                                model: accItem.chips

                                delegate: Rectangle {
                                    required property var modelData
                                    readonly property color tone: row.previewChipColor(modelData)
                                    radius: 3
                                    implicitWidth: miniTxt.implicitWidth + 6
                                    implicitHeight: 14
                                    color: Qt.rgba(tone.r, tone.g, tone.b, 0.15)

                                    Text {
                                        id: miniTxt
                                        anchors.centerIn: parent
                                        text: modelData.text
                                        color: tone
                                        font.pixelSize: 8
                                        font.bold: true
                                    }
                                }
                            }
                        }

                        Text {
                            visible: accItem.chips.length === 0 && accItem.statusBadge.length > 0
                            text: accItem.statusBadge
                            color: modelData.status === "error"
                                ? Kirigami.Theme.negativeTextColor
                                : row.mutedColor
                            font.pixelSize: 8
                        }

                        Text {
                            visible: !accItem.isActive && accItemHover.hovered
                            text: "Switch"
                            color: Kirigami.Theme.highlightColor
                            font.pixelSize: 9
                            font.bold: true
                        }
                    }

                    HoverHandler { id: accItemHover }
                    TapHandler {
                        enabled: !row.accountBusy && !accItem.isActive
                        onTapped: {
                            if (row.provider) {
                                row.switcherOpen = false
                                row.switchAccountRequested(row.provider.id, modelData.id)
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.10)
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                QQC2.ToolButton {
                    id: addInlineBtn
                    enabled: !row.accountBusy
                    implicitHeight: 20
                    leftPadding: 6
                    rightPadding: 6
                    topPadding: 1
                    bottomPadding: 1
                    onClicked: {
                        if (row.provider) {
                            row.switcherOpen = false
                            row.manageAccountsRequested(row.provider.id)
                        }
                    }

                    contentItem: Text {
                        text: "+ Add account"
                        color: Kirigami.Theme.highlightColor
                        font.pixelSize: 9
                        font.bold: true
                        verticalAlignment: Text.AlignVCenter
                    }

                    background: Rectangle {
                        radius: 4
                        color: addInlineBtn.hovered
                            ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.18)
                            : "transparent"
                    }
                }

                Item { Layout.fillWidth: true }

                QQC2.ToolButton {
                    id: manageInlineBtn
                    enabled: !row.accountBusy
                    implicitHeight: 20
                    leftPadding: 6
                    rightPadding: 6
                    topPadding: 1
                    bottomPadding: 1
                    onClicked: {
                        if (row.provider) {
                            row.switcherOpen = false
                            row.manageAccountsRequested(row.provider.id)
                        }
                    }

                    contentItem: Text {
                        text: "Manage"
                        color: row.mutedColor
                        font.pixelSize: 9
                        verticalAlignment: Text.AlignVCenter
                    }

                    background: Rectangle {
                        radius: 4
                        color: manageInlineBtn.hovered
                            ? Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.10)
                            : "transparent"
                    }
                }
            }
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
