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
    property string intelAgent: "auto"
    property bool accountBusy: false
    property string accountError: ""
    property string accountNotice: ""
    property bool accountsOpen: false
    property string accountsProviderId: "codex"
    property bool proxyOpen: false
    property var proxyState: null
    property var proxyIntelPreview: null
    property bool proxyBusy: false
    property string proxyError: ""
    property string proxyNotice: ""
    property bool showProxyStats: true

    signal refreshRequested()
    signal quitRequested()
    signal closeRequested()
    signal intelLoadRequested()
    signal intelScanRequested(string agentId)
    signal intelAgentSelected(string agentId)
    signal switchAccountRequested(string providerId, string accountId)
    signal snapshotAccountRequested(string providerId, string label)
    signal launchLoginRequested(string providerId, string label)
    signal addApiKeyPromptRequested(string providerId, string label, string region)
    signal renameAccountRequested(string providerId, string accountId, string newLabel)
    signal removeAccountRequested(string providerId, string accountId)
    signal clearAccountMessagesRequested()
    signal proxyStatusRequested()
    signal proxyModeRequested(string mode)
    signal proxyStartRequested()
    signal proxyStopRequested()
    signal proxyRestartRequested()
    signal proxyCopyEndpointRequested()
    signal proxyCopyClientKeyRequested()
    signal proxyRotateKeyRequested()
    signal proxyRoutingChanged(var patch)
    signal proxyPinAccountRequested(string providerId, string accountId)
    signal proxyUnpinAccountRequested(string providerId)
    signal proxyToggleAccountRequested(string accountId, bool disabled)
    signal proxyResetCooldownRequested(string accountId)
    signal proxyLaunchLoginRequested(string providerId)
    signal proxyProviderDefaultRequested(string providerId, string model, string thinking)
    signal proxySetTierRequested(string tier, string model, string thinking, bool clear)
    signal proxyCopyTierEndpointRequested(string tier)
    signal proxyImportIntelPreviewRequested()
    signal proxyImportIntelApplyRequested()
    signal proxyClearIntelPreviewRequested()

    function openAccounts(providerId) {
        if (providerId && providerId.length > 0) {
            full.accountsProviderId = providerId
        }
        full.intelOpen = false
        full.proxyOpen = false
        full.accountsOpen = true
        full.clearAccountMessagesRequested()
    }

    function openProxy() {
        full.intelOpen = false
        full.accountsOpen = false
        full.proxyOpen = true
        full.proxyStatusRequested()
    }

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
        : ((full.accountsOpen || full.proxyOpen) ? Kirigami.Units.gridUnit * 26 : Kirigami.Units.gridUnit * 24)
    Layout.preferredWidth: full.intelOpen
        ? Kirigami.Units.gridUnit * 31
        : ((full.accountsOpen || full.proxyOpen) ? Kirigami.Units.gridUnit * 28 : Kirigami.Units.gridUnit * 26)
    Layout.minimumHeight: (full.intelOpen || full.accountsOpen || full.proxyOpen)
        ? Kirigami.Units.gridUnit * 18
        : Math.min(Layout.preferredHeight, maxPopupHeight)
    Layout.preferredHeight: full.intelOpen
        ? Kirigami.Units.gridUnit * 30
        : ((full.accountsOpen || full.proxyOpen)
            ? Kirigami.Units.gridUnit * 30
            : Math.min(
                Math.max(content.implicitHeight + popupMargin * 2, Kirigami.Units.gridUnit * 6),
                maxPopupHeight))
    Layout.maximumHeight: Layout.preferredHeight

    ProxyPanel {
        id: proxyView
        anchors.fill: parent
        anchors.margins: full.popupMargin
        visible: full.proxyOpen
        proxyState: full.proxyState
        intelPreview: full.proxyIntelPreview
        busy: full.proxyBusy
        errorText: full.proxyError
        noticeText: full.proxyNotice
        showStats: full.showProxyStats
        onBackRequested: full.proxyOpen = false
        onRefreshRequested: full.proxyStatusRequested()
        onQuitRequested: full.quitRequested()
        onModeRequested: function(m) { full.proxyModeRequested(m) }
        onStartRequested: full.proxyStartRequested()
        onStopRequested: full.proxyStopRequested()
        onRestartRequested: full.proxyRestartRequested()
        onCopyEndpointRequested: full.proxyCopyEndpointRequested()
        onCopyClientKeyRequested: full.proxyCopyClientKeyRequested()
        onRotateKeyRequested: full.proxyRotateKeyRequested()
        onRoutingChanged: function(p) { full.proxyRoutingChanged(p) }
        onPinAccountRequested: function(p, a) { full.proxyPinAccountRequested(p, a) }
        onUnpinAccountRequested: function(p) { full.proxyUnpinAccountRequested(p) }
        onToggleAccountRequested: function(a, d) { full.proxyToggleAccountRequested(a, d) }
        onResetCooldownRequested: function(a) { full.proxyResetCooldownRequested(a) }
        onLaunchProxyLoginRequested: function(p) { full.proxyLaunchLoginRequested(p) }
        onProviderDefaultChanged: function(p, m, t) { full.proxyProviderDefaultRequested(p, m, t) }
        onSetTierRequested: function(t, m, th, c) { full.proxySetTierRequested(t, m, th, c) }
        onCopyTierEndpointRequested: function(t) { full.proxyCopyTierEndpointRequested(t) }
        onImportIntelPreviewRequested: full.proxyImportIntelPreviewRequested()
        onImportIntelApplyRequested: full.proxyImportIntelApplyRequested()
        onClearIntelPreviewRequested: full.proxyClearIntelPreviewRequested()
    }

    IntelModal {
        id: intelView
        anchors.fill: parent
        anchors.margins: full.popupMargin
        visible: full.intelOpen
        report: full.intelReport
        nowMs: full.nowMs
        scanning: full.intelBusy
        errorText: full.intelError
        selectedAgent: full.intelAgent
        onBackRequested: full.intelOpen = false
        onScanRequested: function(a) { full.intelScanRequested(a) }
        onAgentSelected: function(a) { full.intelAgentSelected(a) }
        onQuitRequested: full.quitRequested()
    }

    AccountsModal {
        id: accountsView
        anchors.fill: parent
        anchors.margins: full.popupMargin
        visible: full.accountsOpen
        providers: full.providers
        selectedProviderId: full.accountsProviderId
        nowMs: full.nowMs
        warnThreshold: full.warnThreshold
        criticalThreshold: full.criticalThreshold
        busy: full.accountBusy || full.busy
        errorText: full.accountError
        noticeText: full.accountNotice
        onSelectedProviderIdChanged: full.accountsProviderId = selectedProviderId
        onBackRequested: full.accountsOpen = false
        onRefreshRequested: full.refreshRequested()
        onQuitRequested: full.quitRequested()
        onSwitchAccountRequested: function(p, a) { full.switchAccountRequested(p, a) }
        onSnapshotAccountRequested: function(p, l) { full.snapshotAccountRequested(p, l) }
        onLaunchLoginRequested: function(p, l) { full.launchLoginRequested(p, l) }
        onAddApiKeyPromptRequested: function(p, l, r) { full.addApiKeyPromptRequested(p, l, r) }
        onRenameAccountRequested: function(p, a, l) { full.renameAccountRequested(p, a, l) }
        onRemoveAccountRequested: function(p, a) { full.removeAccountRequested(p, a) }
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: full.popupMargin
        visible: !full.intelOpen && !full.accountsOpen && !full.proxyOpen
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
                elide: Text.ElideRight
                Layout.maximumWidth: Kirigami.Units.gridUnit * 8
            }

            QQC2.ToolButton {
                id: accountsButton
                implicitHeight: 22
                leftPadding: 6
                rightPadding: 6
                topPadding: 2
                bottomPadding: 2
                onClicked: {
                    var defaultPid = full.mainProviders.length > 0 && full.mainProviders[0].supports_accounts
                        ? full.mainProviders[0].id
                        : "codex"
                    full.openAccounts(defaultPid)
                }

                contentItem: Text {
                    text: full.accountBusy ? "⇄ …" : "⇄ Accounts"
                    color: accountsButton.hovered
                        ? Kirigami.Theme.highlightColor
                        : Kirigami.Theme.textColor
                    font.pixelSize: 10
                    font.bold: true
                    verticalAlignment: Text.AlignVCenter
                }

                background: Rectangle {
                    radius: 4
                    color: accountsButton.down
                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.24)
                        : accountsButton.hovered
                            ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.16)
                            : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
                }

                QQC2.ToolTip.text: "Add & switch CLI accounts on the fly"
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.delay: 500
            }

            QQC2.ToolButton {
                id: proxyButton
                readonly property bool proxyUp: !!(full.proxyState && full.proxyState.health && full.proxyState.health.ready)
                implicitHeight: 22
                leftPadding: 6
                rightPadding: 6
                topPadding: 2
                bottomPadding: 2
                Accessible.name: "Local inference proxy"
                onClicked: full.openProxy()

                contentItem: RowLayout {
                    spacing: 4
                    Rectangle {
                        visible: proxyButton.proxyUp
                        implicitWidth: 6
                        implicitHeight: 6
                        radius: 3
                        color: Kirigami.Theme.positiveTextColor
                    }
                    Kirigami.Icon {
                        source: "network-server-symbolic"
                        implicitWidth: 12
                        implicitHeight: 12
                        isMask: true
                        color: proxyButton.hovered
                            ? Kirigami.Theme.highlightColor
                            : Kirigami.Theme.textColor
                    }
                    Text {
                        text: full.proxyBusy ? "…" : "Proxy"
                        color: proxyButton.hovered
                            ? Kirigami.Theme.highlightColor
                            : Kirigami.Theme.textColor
                        font.pixelSize: 10
                        font.bold: true
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                background: Rectangle {
                    radius: 4
                    color: proxyButton.down
                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.24)
                        : proxyButton.hovered
                            ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.16)
                            : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
                }

                QQC2.ToolTip.text: proxyButton.proxyUp
                    ? "Local CLI inference proxy (running on 127.0.0.1)"
                    : "Optional local CLI inference proxy (Direct CLI mode when off)"
                QQC2.ToolTip.visible: hovered
                QQC2.ToolTip.delay: 500
            }

            QQC2.ToolButton {
                id: intelButton
                implicitHeight: 22
                leftPadding: 6
                rightPadding: 6
                topPadding: 2
                bottomPadding: 2
                onClicked: {
                    full.accountsOpen = false
                    full.proxyOpen = false
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

                QQC2.ToolTip.text: "AI Intel modal (deals, model tiers, projections via any installed CLI agent)"
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
            visible: full.errorText.length > 0 || full.accountError.length > 0
            text: full.accountError.length > 0 ? full.accountError : full.errorText
            color: Kirigami.Theme.negativeTextColor
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }

        Rectangle {
            Layout.fillWidth: true
            visible: full.accountNotice.length > 0 && full.accountError.length === 0
            radius: 4
            implicitHeight: mainNoticeText.implicitHeight + 6
            color: Qt.rgba(Kirigami.Theme.positiveTextColor.r, Kirigami.Theme.positiveTextColor.g, Kirigami.Theme.positiveTextColor.b, 0.14)

            Text {
                id: mainNoticeText
                anchors.fill: parent
                anchors.margins: 3
                text: full.accountNotice
                color: Kirigami.Theme.positiveTextColor
                font.pixelSize: 10
                wrapMode: Text.WordWrap
            }
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
                accountBusy: full.accountBusy
                onSwitchAccountRequested: function(p, a) { full.switchAccountRequested(p, a) }
                onManageAccountsRequested: function(p) { full.openAccounts(p) }
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
                                color: signedOutHover.hovered && modelData.supports_accounts
                                    ? Kirigami.Theme.highlightColor
                                    : full.mutedColor
                                font.pixelSize: 10
                            }

                            HoverHandler {
                                id: signedOutHover
                            }

                            TapHandler {
                                enabled: !!modelData.supports_accounts
                                onTapped: full.openAccounts(modelData.id)
                            }

                            QQC2.ToolTip.visible: signedOutHover.hovered
                            QQC2.ToolTip.delay: 600
                            QQC2.ToolTip.text: modelData.supports_accounts
                                ? (modelData.hint ? modelData.hint + " (click to sign in / add)" : "Click to sign in or add account")
                                : (modelData.hint || "Not signed in")
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
