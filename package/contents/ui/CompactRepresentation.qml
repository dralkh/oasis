import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "../code/format.js" as F

MouseArea {
    id: compact

    property var providers: []
    property double nowMs: 0
    property bool showNames: false
    property bool showCountdown: true
    property int panelSlots: 4
    property int warnThreshold: 80
    property int criticalThreshold: 95
    property bool busy: false
    property bool failed: false

    signal activated()
    signal refreshRequested()

    // formFactorOverride is only used by the offscreen preview harness.
    property int formFactorOverride: -1
    readonly property bool vertical: formFactorOverride >= 0
        ? formFactorOverride === 3
        : Plasmoid.formFactor === PlasmaCore.Types.Vertical
    readonly property int cellHeight: 20

    Layout.minimumWidth: compact.vertical
        ? Kirigami.Units.gridUnit * 1.55
        : horizontalRow.implicitWidth + Kirigami.Units.smallSpacing * 2
    Layout.preferredWidth: Layout.minimumWidth
    // Fixed from startup: Plasma caches the panel item's size hint, so a
    // height that only grows after the first fetch would stay clipped.
    Layout.minimumHeight: compact.vertical
        ? Math.max(Kirigami.Units.iconSizes.smallMedium,
            Math.max(1, compact.panelSlots) * compact.cellHeight + Kirigami.Units.smallSpacing)
        : Kirigami.Units.iconSizes.smallMedium
    Layout.preferredHeight: Layout.minimumHeight
    implicitWidth: Layout.minimumWidth
    implicitHeight: Layout.minimumHeight

    acceptedButtons: Qt.LeftButton | Qt.MiddleButton
    activeFocusOnTab: true
    hoverEnabled: true

    onClicked: function(mouse) {
        if (mouse.button === Qt.MiddleButton) {
            compact.refreshRequested()
            return
        }
        compact.activated()
    }

    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
            compact.activated()
            event.accepted = true
        } else if (event.key === Qt.Key_R) {
            compact.refreshRequested()
            event.accepted = true
        }
    }

    Accessible.role: Accessible.Button
    Accessible.name: "AI subscription limits"
    Accessible.onPressAction: compact.activated()

    function severity(provider) {
        if (!provider || provider.status !== "ok") {
            return Kirigami.Theme.disabledTextColor
        }
        var peak = F.peak(provider)
        if (peak >= compact.criticalThreshold) {
            return Kirigami.Theme.negativeTextColor
        }
        if (peak >= compact.warnThreshold) {
            return Kirigami.Theme.neutralTextColor
        }
        return Kirigami.Theme.textColor
    }

    function windowTone(window) {
        if (!window) {
            return Kirigami.Theme.textColor
        }
        if (window.resets_at && F.toMs(window.resets_at) <= compact.nowMs) {
            return Kirigami.Theme.positiveTextColor
        }
        if (window.used >= compact.criticalThreshold) {
            return Kirigami.Theme.negativeTextColor
        }
        if (window.used >= compact.warnThreshold) {
            return Kirigami.Theme.neutralTextColor
        }
        return Kirigami.Theme.textColor
    }

    function resetLabel(window) {
        var at = F.resetMs(window)
        if (!at) {
            return ""
        }
        if (at <= compact.nowMs) {
            return "✓"
        }
        var left = F.countdownShort(at, compact.nowMs)
        return left.length > 0 ? "↑" + left : "✓"
    }

    Rectangle {
        anchors.fill: parent
        visible: compact.activeFocus
        color: "transparent"
        border.color: Kirigami.Theme.focusColor
        border.width: 1
        radius: Kirigami.Units.cornerRadius
    }

    // Horizontal panel: one line per provider, e.g. "Cx 100/17 ↑2h".
    RowLayout {
        id: horizontalRow
        visible: !compact.vertical
        anchors.centerIn: parent
        spacing: Kirigami.Units.smallSpacing

        Repeater {
            model: compact.vertical ? [] : compact.providers

            delegate: RowLayout {
                id: hcell
                spacing: 3

                readonly property var windows: F.periodSummary(modelData)
                readonly property var resetWindow: windows.length > 0 ? windows[0] : null

                Rectangle {
                    visible: index > 0
                    Layout.alignment: Qt.AlignVCenter
                    width: 1
                    height: Math.round(Kirigami.Units.gridUnit * 0.7)
                    color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.18)
                }

                Rectangle {
                    id: badge
                    Layout.alignment: Qt.AlignVCenter
                    width: Kirigami.Units.iconSizes.small
                    height: width
                    radius: Math.round(width * 0.3)
                    color: Qt.rgba(accent.r, accent.g, accent.b, 0.16)

                    readonly property color accent: modelData.status === "ok"
                        ? F.meta(modelData.id).color
                        : Kirigami.Theme.disabledTextColor

                    Text {
                        anchors.centerIn: parent
                        text: F.meta(modelData.id).short
                        color: badge.accent
                        font.pixelSize: Math.max(7, Math.round(badge.width * 0.46))
                        font.bold: true
                    }
                }

                Text {
                    visible: compact.showNames
                    Layout.alignment: Qt.AlignVCenter
                    text: F.meta(modelData.id).name
                    color: compact.severity(modelData)
                    font.family: Kirigami.Theme.smallFont.family
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                }

                Text {
                    Layout.alignment: Qt.AlignVCenter
                    visible: modelData.status === "ok"
                    text: F.headline(modelData)
                    color: compact.severity(modelData)
                    font.family: Kirigami.Theme.smallFont.family
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    font.bold: true
                }

                Text {
                    Layout.alignment: Qt.AlignVCenter
                    visible: modelData.status !== "ok"
                    text: modelData.status === "not_configured" ? "—" : "!"
                    color: modelData.status === "not_configured"
                        ? Kirigami.Theme.disabledTextColor
                        : Kirigami.Theme.negativeTextColor
                    font.family: Kirigami.Theme.smallFont.family
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    font.bold: true
                }

                Text {
                    Layout.alignment: Qt.AlignVCenter
                    visible: compact.showCountdown && modelData.status === "ok"
                    text: compact.resetLabel(hcell.resetWindow)
                    color: F.windowResetAvailable(hcell.resetWindow, compact.nowMs)
                        ? Kirigami.Theme.positiveTextColor
                        : Kirigami.Theme.disabledTextColor
                    font.family: Kirigami.Theme.smallFont.family
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                }
            }
        }

        RowLayout {
            visible: compact.providers.length === 0
            spacing: 3
            Text {
                text: compact.failed ? "AI!" : (compact.busy ? "AI…" : "AI")
                color: compact.failed
                    ? Kirigami.Theme.negativeTextColor
                    : Kirigami.Theme.disabledTextColor
                font.family: Kirigami.Theme.smallFont.family
                font.pointSize: Kirigami.Theme.smallFont.pointSize
                font.bold: true
            }
        }
    }

    // Vertical panel (e.g. a 46px left panel): fixed-cell stack, one card per
    // provider. Manually positioned because positioners do not update their
    // implicit size reliably inside a panel's compact representation.
    Item {
        id: verticalStack
        visible: compact.vertical
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        width: compact.width
        height: Math.max(Kirigami.Units.iconSizes.smallMedium,
            Math.min(compact.providers.length, Math.max(1, compact.panelSlots)) * compact.cellHeight)

        Repeater {
            model: compact.vertical
                ? compact.providers.slice(0, Math.max(1, compact.panelSlots))
                : []

            delegate: RowLayout {
                id: cell

                readonly property var windows: F.periodSummary(modelData)
                readonly property var first: windows.length > 0 ? windows[0] : null
                readonly property var second: windows.length > 1 ? windows[1] : null
                readonly property var resetWindow: (first && F.resetMs(first) > 0) ? first : second
                readonly property color accent: modelData.status === "ok"
                    ? F.meta(modelData.id).color
                    : Kirigami.Theme.disabledTextColor

                y: index * compact.cellHeight
                width: verticalStack.width
                height: compact.cellHeight
                spacing: 2

                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    width: 2
                    height: compact.cellHeight - 5
                    radius: 1
                    color: cell.accent
                    opacity: 0.75
                }

                ColumnLayout {
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 0

                    Text {
                        text: F.meta(modelData.id).short + (modelData.status === "ok"
                            ? (cell.first ? F.round(cell.first.used) : F.round(F.peak(modelData)))
                            : (modelData.status === "not_configured" ? "—" : "!"))
                            + (modelData.status === "ok" && F.isIdle(cell.first) ? "✓" : "")
                        color: modelData.status === "ok"
                            ? compact.windowTone(cell.first)
                            : (modelData.status === "not_configured"
                                ? Kirigami.Theme.disabledTextColor
                                : Kirigami.Theme.negativeTextColor)
                        font.pixelSize: Math.max(6, Math.round(Kirigami.Units.gridUnit * 0.38))
                        font.bold: true
                    }

                    Text {
                        visible: modelData.status === "ok"
                        text: {
                            var parts = []
                            if (cell.second !== null) {
                                parts.push("W" + F.round(cell.second.used))
                            }
                            if (compact.showCountdown) {
                                var reset = compact.resetLabel(cell.resetWindow)
                                if (reset.length > 0) {
                                    parts.push(reset)
                                }
                            }
                            return parts.join("")
                        }
                        color: Kirigami.Theme.disabledTextColor
                        font.pixelSize: Math.max(5, Math.round(Kirigami.Units.gridUnit * 0.28))
                    }
                }
            }
        }

        Text {
            id: emptyVertical
            visible: compact.providers.length === 0
            anchors.centerIn: parent
            text: compact.failed ? "AI!" : (compact.busy ? "AI…" : "AI")
            color: compact.failed
                ? Kirigami.Theme.negativeTextColor
                : Kirigami.Theme.disabledTextColor
            font.pixelSize: Math.max(7, Math.round(Kirigami.Units.gridUnit * 0.45))
            font.bold: true
        }
    }
}
