import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/format.js" as F

Item {
    id: full

    property var providers: []
    property double nowMs: 0
    property bool busy: false
    property string errorText: ""
    property double lastSuccess: 0
    property int warnThreshold: 80
    property int criticalThreshold: 95
    property var intelReport: null
    property bool intelBusy: false
    property string intelError: ""
    property bool intelOpen: false

    signal refreshRequested()
    signal quitRequested()
    signal closeRequested()
    signal intelLoadRequested()
    signal intelScanRequested()

    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.70)
    readonly property int popupMargin: 10
    readonly property int maxPopupHeight: Kirigami.Units.gridUnit * 36

    readonly property var activeProviders: {
        var result = [];
        for (var i = 0; i < providers.length; ++i) {
            var provider = providers[i];
            if (provider.status === "ok"
                    && provider.windows
                    && provider.windows.length > 0) {
                result.push(provider);
            }
        }
        return result;
    }

    readonly property var alertProviders: {
        var result = [];
        for (var i = 0; i < providers.length; ++i) {
            var provider = providers[i];
            var noWindows = !provider.windows || provider.windows.length === 0;
            if (provider.status === "expired"
                    || provider.status === "error"
                    || (provider.status === "ok" && noWindows)) {
                result.push(provider);
            }
        }
        return result;
    }

    readonly property var signedOutProviders: {
        var result = [];
        for (var i = 0; i < providers.length; ++i) {
            if (providers[i].status === "not_configured") result.push(providers[i]);
        }
        return result;
    }

    readonly property var mainProviders: activeProviders.concat(alertProviders)

    Layout.minimumWidth: full.intelOpen
        ? Kirigami.Units.gridUnit * 28
        : Kirigami.Units.gridUnit * 24
    Layout.preferredWidth: full.intelOpen
        ? Kirigami.Units.gridUnit * 31
        : Kirigami.Units.gridUnit * 26
    Layout.minimumHeight: Math.min(Layout.preferredHeight, maxPopupHeight)
    Layout.preferredHeight: full.intelOpen
        ? Kirigami.Units.gridUnit * 30
        : Math.min(
            Math.max(content.implicitHeight + popupMargin * 2, Kirigami.Units.gridUnit * 6),
            maxPopupHeight)
    Layout.maximumHeight: Layout.preferredHeight

    IntelModal {
        id: intelView
        anchors.fill: parent
        anchors.margins: full.popupMargin
        visible: full.intelOpen
        report: full.intelReport
        nowMs: full.nowMs
        scanning: full.intelBusy
        errorText: full.intelError
        onBackRequested: full.intelOpen = false
        onScanRequested: full.intelScanRequested()
        onQuitRequested: full.quitRequested()
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: full.popupMargin
        visible: !full.intelOpen
        spacing: 4

        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            Text {
                text: "Oasis"
                color: Kirigami.Theme.textColor
                font.pixelSize: 13
                font.bold: true
            }

            Item { Layout.fillWidth: true }

            Text {
                text: full.busy
                    ? "updating…"
                    : "updated " + F.updatedText(full.lastSuccess, full.nowMs)
                color: full.mutedColor
                font.pixelSize: 10
            }

            QQC2.ToolButton {
                id: intelButton
                implicitHeight: 22
                leftPadding: 6
                rightPadding: 6
                topPadding: 2
                bottomPadding: 2
                onClicked: {
                    full.intelOpen = true
                    full.intelLoadRequested()
                }

                contentItem: Text {
                    text: full.intelBusy ? "✦ …" : "✦ Intel"
                    color: intelButton.hovered
                        ? Kirigami.Theme.highlightColor
                        : Kirigami.Theme.textColor
                    font.pixelSize: 10
                    font.bold: true
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    radius: 4
                    color: intelButton.down
                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.24)
                        : intelButton.hovered
                            ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.16)
                            : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
                }

                QQC2.ToolTip.text: "AI Intel modal (deals, model tiers, projections via pi + Tavily)"
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.delay: 500
            }

            QQC2.ToolButton {
                icon.name: "view-refresh"
                enabled: !full.busy
                implicitWidth: 22
                implicitHeight: 22
                padding: 2
                onClicked: full.refreshRequested()
                QQC2.ToolTip.text: "Refresh now"
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.delay: 500
            }

            QQC2.ToolButton {
                id: quitButton
                Layout.rightMargin: -3
                text: "×"
                display: QQC2.AbstractButton.TextOnly
                flat: true
                implicitWidth: 16
                implicitHeight: 16
                padding: 0
                font.pixelSize: 13
                onClicked: full.quitRequested()
                Accessible.name: "Quit Oasis"

                contentItem: Text {
                    text: quitButton.text
                    font: quitButton.font
                    color: quitButton.hovered
                        ? Kirigami.Theme.textColor
                        : full.mutedColor
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    radius: 3
                    color: quitButton.down
                        ? Qt.rgba(Kirigami.Theme.textColor.r,
                                  Kirigami.Theme.textColor.g,
                                  Kirigami.Theme.textColor.b, 0.16)
                        : quitButton.hovered
                            ? Qt.rgba(Kirigami.Theme.textColor.r,
                                      Kirigami.Theme.textColor.g,
                                      Kirigami.Theme.textColor.b, 0.10)
                            : "transparent"
                }

                QQC2.ToolTip.text: "Quit Oasis (remove from the system tray)"
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.delay: 500
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Qt.rgba(Kirigami.Theme.textColor.r,
                           Kirigami.Theme.textColor.g,
                           Kirigami.Theme.textColor.b, 0.14)
        }

        Text {
            Layout.fillWidth: true
            visible: full.errorText.length > 0
            text: full.errorText
            color: Kirigami.Theme.negativeTextColor
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }

        ListView {
            id: mainList
            Layout.fillWidth: true
            Layout.fillHeight: false
            Layout.preferredHeight: Math.min(contentHeight, Kirigami.Units.gridUnit * 28)
            Layout.maximumHeight: Layout.preferredHeight
            Layout.alignment: Qt.AlignTop
            visible: full.mainProviders.length > 0
                || full.signedOutProviders.length > 0
            clip: true
            spacing: 10
            model: full.mainProviders
            boundsBehavior: Flickable.StopAtBounds

            QQC2.ScrollBar.vertical: QQC2.ScrollBar {
                policy: mainList.contentHeight > mainList.height
                    ? QQC2.ScrollBar.AsNeeded
                    : QQC2.ScrollBar.AlwaysOff
            }

            onCountChanged: forceLayout()
            onModelChanged: forceLayout()

            delegate: ProviderRow {
                width: mainList.width
                provider: modelData
                nowMs: full.nowMs
                warnThreshold: full.warnThreshold
                criticalThreshold: full.criticalThreshold
            }

            footer: ColumnLayout {
                width: mainList.width
                spacing: 5

                Text {
                    Layout.fillWidth: true
                    visible: full.mainProviders.length === 0
                    text: full.busy
                        ? "Reading local sign-ins…"
                        : "No active sign-ins."
                    color: full.mutedColor
                    font.pixelSize: 10
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: 1
                    visible: full.signedOutProviders.length > 0
                    implicitHeight: 1
                    color: Qt.rgba(Kirigami.Theme.textColor.r,
                                   Kirigami.Theme.textColor.g,
                                   Kirigami.Theme.textColor.b, 0.10)
                }

                Flow {
                    Layout.fillWidth: true
                    Layout.preferredHeight: implicitHeight
                    visible: full.signedOutProviders.length > 0
                    spacing: 7

                    Text {
                        text: "Not signed in:"
                        color: full.mutedColor
                        font.pixelSize: 10
                    }

                    Repeater {
                        model: full.signedOutProviders

                        delegate: RowLayout {
                            spacing: 3

                            Rectangle {
                                width: 12
                                height: 12
                                radius: 3
                                color: Qt.rgba(Kirigami.Theme.textColor.r,
                                               Kirigami.Theme.textColor.g,
                                               Kirigami.Theme.textColor.b, 0.10)

                                Text {
                                    anchors.centerIn: parent
                                    text: F.meta(modelData.id).short
                                    color: full.mutedColor
                                    font.pixelSize: 6
                                    font.bold: true
                                }
                            }

                            Text {
                                text: F.meta(modelData.id).name
                                color: full.mutedColor
                                font.pixelSize: 10
                            }

                            HoverHandler {
                                id: signedOutHover
                            }

                            QQC2.ToolTip.visible: signedOutHover.hovered
                            QQC2.ToolTip.delay: 600
                            QQC2.ToolTip.text: modelData.hint || "Not signed in"
                        }
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: full.mainProviders.length === 0
                && full.signedOutProviders.length === 0
            text: full.busy
                ? "Reading local sign-ins…"
                : "No providers detected."
            color: full.mutedColor
            font.pixelSize: 10
        }
    }
}
