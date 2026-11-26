import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/format.js" as F

ColumnLayout {
    id: panel

    property var proxyState: null
    property var intelPreview: null
    property bool busy: false
    property string errorText: ""
    property string noticeText: ""
    property bool showStats: true

    signal backRequested()
    signal refreshRequested()
    signal quitRequested()
    signal modeRequested(string mode)
    signal startRequested()
    signal stopRequested()
    signal restartRequested()
    signal copyEndpointRequested()
    signal copyClientKeyRequested()
    signal rotateKeyRequested()
    signal routingChanged(var patch)
    signal pinAccountRequested(string providerId, string accountId)
    signal unpinAccountRequested(string providerId)
    signal toggleAccountRequested(string accountId, bool disabled)
    signal resetCooldownRequested(string accountId)
    signal launchProxyLoginRequested(string providerId)
    signal providerDefaultChanged(string providerId, string model, string thinking)
    signal setTierRequested(string tier, string model, string thinking, bool clear)
    signal copyTierEndpointRequested(string tier)
    signal importIntelPreviewRequested()
    signal importIntelApplyRequested()
    signal clearIntelPreviewRequested()

    property string selectedProviderId: "codex"
    property bool tiersExpanded: false
    property bool routingExpanded: false
    property bool statsExpanded: false
    property bool securityExpanded: false
    property bool confirmRotate: false
    property string editingTier: ""
    property string tierDraftModel: ""
    property string tierDraftThinking: "auto"
    property string tierProviderFilter: "all"
    property string providerModelFilter: ""

    onSelectedProviderIdChanged: panel.providerModelFilter = ""

    Keys.onEscapePressed: panel.backRequested()

    onVisibleChanged: {
        if (!visible) {
            panel.confirmRotate = false
            panel.editingTier = ""
            panel.clearIntelPreviewRequested()
        }
    }

    onProxyStateChanged: {
        if (proxyState && proxyState.policy && proxyState.policy.selected_provider) {
            var sp = String(proxyState.policy.selected_provider)
            if (providerOrder.indexOf(sp) !== -1) {
                selectedProviderId = sp
            }
        }
    }

    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.70)
    readonly property color cardBg: Qt.rgba(Kirigami.Theme.textColor.r,
                                            Kirigami.Theme.textColor.g,
                                            Kirigami.Theme.textColor.b, 0.05)
    readonly property color cardBorder: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.12)

    readonly property var backendInfo: proxyState && proxyState.backend
        ? proxyState.backend
        : ({ installed: false, compatible: false, version: "", min_version: "0.3.0", error: "" })
    readonly property var healthInfo: proxyState && proxyState.health
        ? proxyState.health
        : ({ running: false, ready: false, auth_ok: false, error: "" })
    readonly property bool isRunning: !!healthInfo.ready
    readonly property bool isActiveProcess: isRunning
        || !!healthInfo.running
        || !!(proxyState && proxyState.systemd_active)
    readonly property string managedByLabel: {
        var m = proxyState && proxyState.managed_by ? String(proxyState.managed_by) : ""
        return (m.length > 0 && m !== "none") ? ("Running (" + m + ")") : "Running"
    }
    readonly property string currentMode: proxyState && proxyState.mode ? String(proxyState.mode) : "direct"
    readonly property var routingInfo: proxyState && proxyState.routing ? proxyState.routing : ({})
    readonly property var policyInfo: proxyState && proxyState.policy ? proxyState.policy : ({})
    readonly property var totalsInfo: proxyState && proxyState.totals
        ? proxyState.totals
        : ({ requests: 0, ok: 0, failed: 0, input_tokens: 0, output_tokens: 0, cache_tokens: 0 })
    readonly property var allAccounts: proxyState && proxyState.accounts ? proxyState.accounts : []
    readonly property var allModels: proxyState && proxyState.models ? proxyState.models : []
    readonly property var catalogModels: {
        if (proxyState && proxyState.catalog_models && proxyState.catalog_models.length > 0) {
            return proxyState.catalog_models
        }
        return allModels
    }
    readonly property var clientsList: proxyState && proxyState.connected_clients ? proxyState.connected_clients : []
    readonly property var recentRequests: proxyState && proxyState.recent_requests ? proxyState.recent_requests : []
    readonly property var capsMap: proxyState && proxyState.capabilities ? proxyState.capabilities : ({})
    readonly property var providerOrder: ["codex", "claude", "antigravity", "opencodego", "qwen", "muse", "kimi", "grok", "cursor"]
    readonly property var providerDefaults: proxyState && proxyState.provider_defaults ? proxyState.provider_defaults : ({})
    readonly property var currentProviderDefault: providerDefaults[selectedProviderId] || ({ model: "", thinking: "auto" })
    readonly property var tiersMap: proxyState && proxyState.tiers ? proxyState.tiers : ({})
    readonly property int configuredTiersCount: {
        var c = 0
        for (var i = 0; i < tierOrder.length; ++i) {
            var info = tiersMap[tierOrder[i]]
            if (info && info.targets && info.targets.length > 0) c++
        }
        return c
    }
    readonly property bool intelAvailable: !!(proxyState && proxyState.intel_available)
    readonly property var tierOrder: ["diamond", "gold", "silver", "subagent"]
    readonly property var thinkingOptions: [
        { id: "auto", label: "Auto" },
        { id: "none", label: "Off" },
        { id: "low", label: "Low" },
        { id: "medium", label: "Med" },
        { id: "high", label: "High" },
        { id: "max", label: "Max" }
    ]

    readonly property var currentCap: capsMap[selectedProviderId] || ({
        name: F.meta(selectedProviderId).name,
        proxy_support: "unknown",
        proxy_support_label: "Unknown",
        ownership_note: "",
        protocols: [],
        unsupported_protocols: []
    })

    readonly property var providerAccounts: {
        var out = []
        for (var i = 0; i < allAccounts.length; ++i) {
            if (allAccounts[i] && allAccounts[i].provider === selectedProviderId) {
                out.push(allAccounts[i])
            }
        }
        return out
    }

    readonly property var providerModels: {
        var out = []
        var q = String(panel.providerModelFilter || "").trim().toLowerCase()
        for (var i = 0; i < catalogModels.length; ++i) {
            var m = catalogModels[i]
            if (m && m.provider === selectedProviderId) {
                if (q.length === 0 || String(m.id || "").toLowerCase().indexOf(q) !== -1) {
                    out.push(m)
                }
            }
        }
        return out
    }

    readonly property var providerAllModelsCount: {
        var c = 0
        for (var i = 0; i < catalogModels.length; ++i) {
            if (catalogModels[i] && catalogModels[i].provider === selectedProviderId) c++
        }
        return c
    }

    readonly property var selectableModels: {
        var out = []
        var pf = String(panel.tierProviderFilter || "all")
        var q = String(panel.tierDraftModel || "").trim().toLowerCase()
        var exactHit = false
        for (var i = 0; i < catalogModels.length; ++i) {
            var m = catalogModels[i]
            if (!m || !m.id || String(m.id).indexOf("oasis/") === 0) continue
            if (pf !== "all" && String(m.provider || "") !== pf) continue
            if (String(m.id).toLowerCase() === q) exactHit = true
        }
        for (var j = 0; j < catalogModels.length; ++j) {
            var item = catalogModels[j]
            if (!item || !item.id || String(item.id).indexOf("oasis/") === 0) continue
            if (pf !== "all" && String(item.provider || "") !== pf) continue
            // If user clicked/matched an exact model ID, keep showing the full provider list
            // so switching to another model stays 1-click! Otherwise filter live as they type.
            if (!exactHit && q.length > 0 && String(item.id).toLowerCase().indexOf(q) === -1 && String(item.provider || "").toLowerCase().indexOf(q) === -1) {
                continue
            }
            out.push(item)
        }
        return out
    }

    readonly property var tierFilterProviders: ["all", "codex", "claude", "antigravity", "opencodego", "qwen", "muse", "kimi", "grok"]

    function tierMeta(tierId) {
        if (tierId === "diamond") return { label: "Diamond", color: "#38BDF8" }
        if (tierId === "gold") return { label: "Gold", color: "#F59E0B" }
        if (tierId === "silver") return { label: "Silver", color: "#94A3B8" }
        return { label: "Subagent", color: "#A855F7" }
    }

    function thinkingLabel(mode) {
        var m = String(mode || "auto").toLowerCase()
        if (m === "none" || m === "off") return "Off"
        if (m === "medium") return "Med"
        if (m === "low") return "Low"
        if (m === "high") return "High"
        if (m === "xhigh") return "XHigh"
        if (m === "max") return "Max"
        return "Auto"
    }

    function openTierEditor(tierId) {
        if (panel.editingTier === tierId) {
            panel.editingTier = ""
            return
        }
        var info = panel.tiersMap[tierId]
        var targets = info && info.targets ? info.targets : []
        panel.tierDraftModel = targets.length > 0 ? String(targets[0].model || "") : ""
        panel.tierDraftThinking = targets.length > 0 ? String(targets[0].thinking || "auto") : "auto"
        panel.tierProviderFilter = "all"
        panel.editingTier = tierId
    }

    function supportTone(support) {
        if (support === "native") return Kirigami.Theme.positiveTextColor
        if (support === "partial_chat_only" || support === "partial_api_key_only") return Kirigami.Theme.neutralTextColor
        return mutedColor
    }

    function fmtTokens(n) {
        var v = Number(n) || 0
        if (v >= 1000000) return (v / 1000000).toFixed(1) + "M"
        if (v >= 1000) return (v / 1000).toFixed(1) + "k"
        return String(Math.round(v))
    }

    spacing: 6

    // Top bar
    RowLayout {
        Layout.fillWidth: true
        spacing: 6

        QQC2.ToolButton {
            icon.name: "go-previous"
            implicitWidth: 22
            implicitHeight: 22
            padding: 2
            onClicked: panel.backRequested()
            Accessible.name: "Back to usage"
            QQC2.ToolTip.text: "Back to usage"
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: 400
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0

            RowLayout {
                spacing: 5
                Text {
                    text: "Oasis Proxy"
                    color: Kirigami.Theme.textColor
                    font.pixelSize: 13
                    font.bold: true
                }
                Rectangle {
                    implicitWidth: 7
                    implicitHeight: 7
                    radius: 4
                    color: panel.isRunning
                        ? Kirigami.Theme.positiveTextColor
                        : (panel.isActiveProcess ? Kirigami.Theme.neutralTextColor : panel.mutedColor)
                }
            }

            Text {
                text: panel.isRunning
                    ? "Running on " + (panel.proxyState ? String(panel.proxyState.endpoint || "http://127.0.0.1:8317") : "http://127.0.0.1:8317")
                    : (panel.isActiveProcess
                        ? "Starting or degraded — toggle off or restart"
                        : "Off — zero background RAM")
                color: panel.mutedColor
                font.pixelSize: 9
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        QQC2.ToolButton {
            icon.name: "view-refresh"
            enabled: !panel.busy
            implicitWidth: 22
            implicitHeight: 22
            padding: 2
            onClicked: panel.refreshRequested()
            Accessible.name: "Refresh Oasis Proxy status"
            QQC2.ToolTip.text: "Refresh Oasis Proxy status"
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: 400
        }

        QQC2.ToolButton {
            id: proxyQuitBtn
            Layout.rightMargin: -3
            text: "×"
            display: QQC2.AbstractButton.TextOnly
            flat: true
            implicitWidth: 16
            implicitHeight: 16
            padding: 0
            font.pixelSize: 13
            onClicked: panel.quitRequested()
            Accessible.name: "Quit Oasis"

            contentItem: Text {
                text: proxyQuitBtn.text
                font: proxyQuitBtn.font
                color: proxyQuitBtn.hovered ? Kirigami.Theme.textColor : panel.mutedColor
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            background: Rectangle {
                radius: 3
                color: proxyQuitBtn.down
                    ? Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.16)
                    : proxyQuitBtn.hovered
                        ? Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.10)
                        : "transparent"
            }

            QQC2.ToolTip.text: "Quit Oasis (remove from the system tray)"
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: 500
        }
    }

    // Error / notice banners
    Rectangle {
        Layout.fillWidth: true
        visible: panel.errorText.length > 0
        radius: 4
        implicitHeight: pErrTxt.implicitHeight + 8
        color: Qt.rgba(Kirigami.Theme.negativeTextColor.r, Kirigami.Theme.negativeTextColor.g, Kirigami.Theme.negativeTextColor.b, 0.14)

        Text {
            id: pErrTxt
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 4
            text: panel.errorText
            color: Kirigami.Theme.negativeTextColor
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }
    }

    Rectangle {
        Layout.fillWidth: true
        visible: panel.noticeText.length > 0 && panel.errorText.length === 0
        radius: 4
        implicitHeight: pNoteTxt.implicitHeight + 8
        color: Qt.rgba(Kirigami.Theme.positiveTextColor.r, Kirigami.Theme.positiveTextColor.g, Kirigami.Theme.positiveTextColor.b, 0.14)

        Text {
            id: pNoteTxt
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 4
            text: panel.noticeText
            color: Kirigami.Theme.positiveTextColor
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }
    }

    QQC2.ScrollView {
        id: proxyScroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

        ColumnLayout {
            width: proxyScroll.availableWidth
            spacing: 7

            // Integrated Oasis Proxy status & lifecycle card
            Rectangle {
                Layout.fillWidth: true
                radius: 6
                implicitHeight: statusCol.implicitHeight + 14
                color: panel.cardBg
                border.width: 1
                border.color: panel.cardBorder

                ColumnLayout {
                    id: statusCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 7
                    spacing: 5

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            text: "Local Proxy Service"
                            color: Kirigami.Theme.textColor
                            font.pixelSize: 11
                            font.bold: true
                        }

                        Rectangle {
                            radius: 3
                            implicitWidth: stLbl.implicitWidth + 8
                            implicitHeight: 15
                            color: panel.isRunning
                                ? Qt.rgba(Kirigami.Theme.positiveTextColor.r, Kirigami.Theme.positiveTextColor.g, Kirigami.Theme.positiveTextColor.b, 0.18)
                                : (panel.isActiveProcess
                                    ? Qt.rgba(Kirigami.Theme.neutralTextColor.r, Kirigami.Theme.neutralTextColor.g, Kirigami.Theme.neutralTextColor.b, 0.18)
                                    : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.10))

                            Text {
                                id: stLbl
                                anchors.centerIn: parent
                                text: panel.isRunning
                                    ? "On"
                                    : (panel.isActiveProcess
                                        ? "Degraded"
                                        : (panel.backendInfo.compatible ? "Off" : "Unavailable"))
                                color: panel.isRunning
                                    ? Kirigami.Theme.positiveTextColor
                                    : (panel.isActiveProcess ? Kirigami.Theme.neutralTextColor : panel.mutedColor)
                                font.pixelSize: 8
                                font.bold: true
                            }
                        }

                        Item { Layout.fillWidth: true }

                        Text {
                            visible: panel.isRunning
                            text: "Active: " + (panel.proxyState ? Number(panel.proxyState.active_requests || 0) : 0)
                            color: panel.mutedColor
                            font.pixelSize: 9
                        }

                        QQC2.Switch {
                            id: proxyToggle
                            checked: panel.isActiveProcess
                            enabled: !panel.busy && (panel.isActiveProcess || !!panel.backendInfo.compatible)
                            Accessible.name: "Enable Oasis Proxy"
                            onToggled: {
                                if (checked) {
                                    panel.startRequested()
                                } else {
                                    panel.stopRequested()
                                }
                            }
                        }
                    }

                    // Quick actions: Copy URL, Copy Key, Restart (only when active)
                    Flow {
                        Layout.fillWidth: true
                        spacing: 5

                        QQC2.ToolButton {
                            enabled: !panel.busy
                            text: "Copy URL"
                            font.pixelSize: 9
                            Accessible.name: "Copy Oasis Proxy endpoint URL"
                            onClicked: panel.copyEndpointRequested()
                            QQC2.ToolTip.text: (panel.proxyState ? panel.proxyState.openai_base_url : "http://127.0.0.1:8317/v1")
                            QQC2.ToolTip.visible: hovered
                            QQC2.ToolTip.delay: 400
                        }

                        QQC2.ToolButton {
                            enabled: !panel.busy
                            text: "Copy Key"
                            font.pixelSize: 9
                            Accessible.name: "Copy Oasis Proxy client API key"
                            onClicked: panel.copyClientKeyRequested()
                            QQC2.ToolTip.text: "Copies client API key directly to clipboard (never shown in UI)"
                            QQC2.ToolTip.visible: hovered
                            QQC2.ToolTip.delay: 400
                        }

                        QQC2.ToolButton {
                            visible: panel.isActiveProcess
                            enabled: !panel.busy && !!panel.backendInfo.compatible
                            text: "Restart"
                            font.pixelSize: 9
                            Accessible.name: "Restart Oasis Proxy"
                            onClicked: panel.restartRequested()
                        }
                    }

                    Text {
                        visible: !panel.isRunning && panel.healthInfo.error && String(panel.healthInfo.error).length > 0 && panel.isActiveProcess
                        Layout.fillWidth: true
                        text: "Health check warning: " + String(panel.healthInfo.error || "")
                        color: Kirigami.Theme.neutralTextColor
                        font.pixelSize: 9
                        wrapMode: Text.WordWrap
                    }

                    Text {
                        visible: !panel.backendInfo.installed || !panel.backendInfo.compatible
                        Layout.fillWidth: true
                        text: panel.backendInfo.error
                            ? String(panel.backendInfo.error)
                            : "Oasis Proxy engine is not bundled in this checkout — run ./install.sh to build and bundle it."
                        color: Kirigami.Theme.neutralTextColor
                        font.pixelSize: 9
                        wrapMode: Text.WordWrap
                    }

                    // Compact live summary bar
                    RowLayout {
                        visible: panel.isRunning && panel.showStats
                        Layout.fillWidth: true
                        spacing: 10

                        Text {
                            text: "Req: " + (panel.totalsInfo.requests || 0) + " (" + (panel.totalsInfo.failed || 0) + " err)"
                            color: (panel.totalsInfo.failed || 0) > 0 ? Kirigami.Theme.neutralTextColor : panel.mutedColor
                            font.pixelSize: 9
                        }

                        Text {
                            text: "Tokens: " + panel.fmtTokens(panel.totalsInfo.input_tokens) + " in / " + panel.fmtTokens(panel.totalsInfo.output_tokens) + " out"
                            color: panel.mutedColor
                            font.pixelSize: 9
                        }

                        Text {
                            text: "Clients: " + panel.clientsList.length
                            color: panel.mutedColor
                            font.pixelSize: 9
                        }
                    }
                }
            }

            // Provider capabilities, model selection, thinking mode & proxy account pinning
            Rectangle {
                Layout.fillWidth: true
                radius: 6
                implicitHeight: provCol.implicitHeight + 14
                color: panel.cardBg
                border.width: 1
                border.color: panel.cardBorder

                ColumnLayout {
                    id: provCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 7
                    spacing: 6

                    Text {
                        text: "Provider Routing & Account Pinning"
                        color: Kirigami.Theme.textColor
                        font.pixelSize: 11
                        font.bold: true
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: 4

                        Repeater {
                            model: panel.providerOrder

                            delegate: Rectangle {
                                required property string modelData
                                readonly property var pMeta: F.meta(modelData)
                                readonly property color pColor: pMeta.color
                                readonly property bool selected: panel.selectedProviderId === modelData
                                implicitWidth: pPillRow.implicitWidth + 10
                                implicitHeight: 20
                                radius: 4
                                activeFocusOnTab: true
                                Accessible.role: Accessible.Button
                                Accessible.name: pMeta.name
                                color: selected
                                    ? Qt.rgba(pColor.r, pColor.g, pColor.b, 0.22)
                                    : "transparent"
                                border.width: activeFocus ? 2 : 1
                                border.color: activeFocus
                                    ? Kirigami.Theme.highlightColor
                                    : (selected ? Qt.rgba(pColor.r, pColor.g, pColor.b, 0.55) : panel.cardBorder)

                                Keys.onReturnPressed: if (!panel.busy) panel.selectedProviderId = modelData
                                Keys.onSpacePressed: if (!panel.busy) panel.selectedProviderId = modelData

                                RowLayout {
                                    id: pPillRow
                                    anchors.centerIn: parent
                                    spacing: 3
                                    Text {
                                        text: pMeta.short
                                        color: selected ? pColor : panel.mutedColor
                                        font.pixelSize: 8
                                        font.bold: true
                                    }
                                    Text {
                                        text: pMeta.name
                                        color: selected ? Kirigami.Theme.textColor : panel.mutedColor
                                        font.pixelSize: 9
                                        font.bold: selected
                                    }
                                }

                                TapHandler {
                                    enabled: !panel.busy
                                    onTapped: panel.selectedProviderId = modelData
                                }
                            }
                        }
                    }

                    // Honest provider capability & ownership summary
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Rectangle {
                            radius: 3
                            implicitWidth: capTag.implicitWidth + 8
                            implicitHeight: 15
                            color: Qt.rgba(panel.supportTone(panel.currentCap.proxy_support).r,
                                           panel.supportTone(panel.currentCap.proxy_support).g,
                                           panel.supportTone(panel.currentCap.proxy_support).b, 0.16)

                            Text {
                                id: capTag
                                anchors.centerIn: parent
                                text: String(panel.currentCap.proxy_support_label || "Unknown")
                                color: panel.supportTone(panel.currentCap.proxy_support)
                                font.pixelSize: 8
                                font.bold: true
                            }
                        }

                        Text {
                            text: panel.currentCap.protocols && panel.currentCap.protocols.length > 0
                                ? panel.currentCap.protocols.join(" · ")
                                : "No proxy inference protocol"
                            color: panel.mutedColor
                            font.pixelSize: 9
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        text: String(panel.currentCap.ownership_note || "")
                        color: panel.mutedColor
                        font.pixelSize: 9
                        wrapMode: Text.WordWrap
                    }

                    // All models for selected provider (live connected + full catalog, zero cutoff)
                    ColumnLayout {
                        visible: panel.providerAllModelsCount > 0
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Text {
                                text: "Models (" + panel.providerModels.length + ")"
                                color: panel.mutedColor
                                font.pixelSize: 8
                                font.bold: true
                            }

                            Item { Layout.fillWidth: true }

                            Text {
                                visible: panel.providerAccounts.length === 0 && panel.currentCap.proxy_support !== "monitoring_only"
                                text: "catalog — add account below to route"
                                color: Kirigami.Theme.neutralTextColor
                                font.pixelSize: 8
                            }
                        }

                        QQC2.TextField {
                            visible: panel.providerAllModelsCount > 8
                            Layout.fillWidth: true
                            text: panel.providerModelFilter
                            placeholderText: "Filter " + panel.currentCap.name + " models…"
                            font.pixelSize: 8
                            onTextEdited: panel.providerModelFilter = text
                        }

                        Flow {
                            Layout.fillWidth: true
                            spacing: 4

                            Repeater {
                                model: panel.providerModels

                                delegate: Rectangle {
                                    required property var modelData
                                    readonly property string activeDefModel: String(panel.currentProviderDefault.model || panel.policyInfo.selected_model || "")
                                    readonly property bool isSel: activeDefModel === String(modelData.id)
                                    readonly property bool isLive: !!modelData.connected
                                    implicitWidth: mPillRow.implicitWidth + 10
                                    implicitHeight: 18
                                    radius: 3
                                    activeFocusOnTab: true
                                    Accessible.role: Accessible.Button
                                    Accessible.name: "Select default model " + String(modelData.id)
                                    color: isSel
                                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                                        : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.06)
                                    border.width: (isSel || activeFocus) ? 1 : 0
                                    border.color: Kirigami.Theme.highlightColor

                                    Keys.onReturnPressed: if (!panel.busy) panel.providerDefaultChanged(panel.selectedProviderId, String(modelData.id), String(panel.currentProviderDefault.thinking || "auto"))
                                    Keys.onSpacePressed: if (!panel.busy) panel.providerDefaultChanged(panel.selectedProviderId, String(modelData.id), String(panel.currentProviderDefault.thinking || "auto"))

                                    RowLayout {
                                        id: mPillRow
                                        anchors.centerIn: parent
                                        spacing: 4

                                        Rectangle {
                                            visible: isLive
                                            implicitWidth: 5
                                            implicitHeight: 5
                                            radius: 3
                                            color: Kirigami.Theme.positiveTextColor
                                        }

                                        Text {
                                            text: String(modelData.id)
                                            color: isSel ? Kirigami.Theme.textColor : (isLive ? Kirigami.Theme.textColor : panel.mutedColor)
                                            font.pixelSize: 8
                                            font.bold: isSel
                                        }
                                    }

                                    TapHandler {
                                        enabled: !panel.busy
                                        onTapped: panel.providerDefaultChanged(panel.selectedProviderId, String(modelData.id), String(panel.currentProviderDefault.thinking || "auto"))
                                    }
                                }
                            }
                        }
                    }

                    // Per-provider default Thinking / Reasoning effort mode
                    RowLayout {
                        visible: panel.currentCap.proxy_support !== "unsupported"
                        Layout.fillWidth: true
                        spacing: 4

                        Text {
                            text: "Thinking:"
                            color: panel.mutedColor
                            font.pixelSize: 8
                        }

                        Flow {
                            Layout.fillWidth: true
                            spacing: 3

                            Repeater {
                                model: panel.thinkingOptions
                                delegate: Rectangle {
                                    required property var modelData
                                    readonly property bool actThink: String(panel.currentProviderDefault.thinking || "auto") === modelData.id
                                    implicitWidth: ptLbl.implicitWidth + 8
                                    implicitHeight: 16
                                    radius: 3
                                    activeFocusOnTab: true
                                    Accessible.role: Accessible.RadioButton
                                    Accessible.name: "Thinking mode " + modelData.label
                                    Accessible.checked: actThink
                                    color: actThink
                                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                                        : "transparent"
                                    border.width: activeFocus ? 2 : 1
                                    border.color: (actThink || activeFocus) ? Kirigami.Theme.highlightColor : panel.cardBorder

                                    Keys.onReturnPressed: if (!panel.busy && !actThink) panel.providerDefaultChanged(panel.selectedProviderId, String(panel.currentProviderDefault.model || ""), modelData.id)
                                    Keys.onSpacePressed: if (!panel.busy && !actThink) panel.providerDefaultChanged(panel.selectedProviderId, String(panel.currentProviderDefault.model || ""), modelData.id)

                                    Text {
                                        id: ptLbl
                                        anchors.centerIn: parent
                                        text: modelData.label
                                        color: actThink ? Kirigami.Theme.textColor : panel.mutedColor
                                        font.pixelSize: 8
                                        font.bold: actThink
                                    }

                                    TapHandler {
                                        enabled: !panel.busy && !actThink
                                        onTapped: panel.providerDefaultChanged(panel.selectedProviderId, String(panel.currentProviderDefault.model || ""), modelData.id)
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        visible: String(panel.currentProviderDefault.model || "").length > 0
                        Layout.fillWidth: true
                        text: "Virtual model: oasis/" + panel.selectedProviderId
                            + " → " + String(panel.currentProviderDefault.model || "")
                            + " (" + panel.thinkingLabel(panel.currentProviderDefault.thinking) + ")"
                        color: panel.mutedColor
                        font.pixelSize: 8
                        elide: Text.ElideRight
                    }

                    // Isolated proxy accounts for selected provider + Pin/Unpin + Cooldown Reset
                    Repeater {
                        model: panel.providerAccounts

                        delegate: Rectangle {
                            required property var modelData
                            readonly property bool isPinned: !!modelData.pinned
                            readonly property bool isDisabled: !!modelData.disabled
                            readonly property bool inCooldown: !!(modelData.cooldown_until && String(modelData.cooldown_until).length > 0)
                            readonly property string lastErr: modelData.last_error ? String(modelData.last_error) : ""
                            Layout.fillWidth: true
                            implicitHeight: pAccCol.implicitHeight + 8
                            radius: 4
                            color: isPinned
                                ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.14)
                                : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.04)
                            border.width: isPinned ? 1 : 0
                            border.color: Kirigami.Theme.highlightColor

                            ColumnLayout {
                                id: pAccCol
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 4
                                spacing: 3

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 5

                                    Rectangle {
                                        implicitWidth: 6
                                        implicitHeight: 6
                                        radius: 3
                                        color: isDisabled
                                            ? panel.mutedColor
                                            : (inCooldown
                                                ? Kirigami.Theme.neutralTextColor
                                                : (isPinned ? Kirigami.Theme.highlightColor : Kirigami.Theme.positiveTextColor))
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 0
                                        Text {
                                            text: String(modelData.label || modelData.id)
                                            color: isDisabled ? panel.mutedColor : Kirigami.Theme.textColor
                                            font.pixelSize: 10
                                            font.bold: isPinned
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                        Text {
                                            text: (modelData.ownership === "synced_api_key" ? "API key" : "Isolated OAuth")
                                                + " · " + String(modelData.billing || "subscription")
                                                + (inCooldown ? " · cooldown" : "")
                                                + (isDisabled ? " · disabled" : "")
                                            color: inCooldown ? Kirigami.Theme.neutralTextColor : panel.mutedColor
                                            font.pixelSize: 8
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }

                                    QQC2.ToolButton {
                                        visible: inCooldown || lastErr.length > 0
                                        enabled: !panel.busy
                                        text: "Reset"
                                        font.pixelSize: 9
                                        Accessible.name: "Reset cooldown for " + String(modelData.label || modelData.id)
                                        onClicked: panel.resetCooldownRequested(String(modelData.id))
                                    }

                                    QQC2.ToolButton {
                                        enabled: !panel.busy
                                        text: isPinned ? "Unpin" : "Pin"
                                        font.pixelSize: 9
                                        Accessible.name: (isPinned ? "Unpin " : "Pin ") + String(modelData.label || modelData.id)
                                        onClicked: {
                                            if (isPinned) {
                                                panel.unpinAccountRequested(panel.selectedProviderId)
                                            } else {
                                                panel.pinAccountRequested(panel.selectedProviderId, String(modelData.id))
                                            }
                                        }
                                    }

                                    QQC2.ToolButton {
                                        enabled: !panel.busy
                                        text: isDisabled ? "Enable" : "Disable"
                                        font.pixelSize: 9
                                        Accessible.name: (isDisabled ? "Enable " : "Disable ") + String(modelData.label || modelData.id)
                                        onClicked: panel.toggleAccountRequested(String(modelData.id), !isDisabled)
                                    }
                                }

                                Text {
                                    visible: lastErr.length > 0
                                    Layout.fillWidth: true
                                    Layout.leftMargin: 11
                                    text: lastErr
                                    color: Kirigami.Theme.negativeTextColor
                                    font.pixelSize: 8
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: panel.currentCap.proxy_support === "native" && !!panel.backendInfo.compatible
                        spacing: 6

                        QQC2.Button {
                            enabled: !panel.busy
                            text: "+ Add isolated " + panel.currentCap.name + " proxy account"
                            font.pixelSize: 9
                            Accessible.name: "Add isolated " + panel.currentCap.name + " proxy account"
                            onClicked: panel.launchProxyLoginRequested(panel.selectedProviderId)
                        }
                    }
                }
            }

            // Progressively disclosed Single-Daemon Tier Routing (Diamond / Gold / Silver / Subagent + Import AI Intel)
            Rectangle {
                Layout.fillWidth: true
                radius: 6
                implicitHeight: tierCol.implicitHeight + 12
                color: panel.cardBg
                border.width: 1
                border.color: panel.cardBorder

                ColumnLayout {
                    id: tierCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 6
                    spacing: 5

                    Item {
                        Layout.fillWidth: true
                        implicitHeight: tierHdrRow.implicitHeight
                        activeFocusOnTab: true
                        Accessible.role: Accessible.Button
                        Accessible.name: "Toggle Tier Routing"
                        Keys.onReturnPressed: panel.tiersExpanded = !panel.tiersExpanded
                        Keys.onSpacePressed: panel.tiersExpanded = !panel.tiersExpanded

                        RowLayout {
                            id: tierHdrRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            Text {
                                text: (panel.tiersExpanded ? "▾ " : "▸ ") + "Tier Routing"
                                color: Kirigami.Theme.textColor
                                font.pixelSize: 10
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Text {
                                text: panel.configuredTiersCount > 0
                                    ? (panel.configuredTiersCount + "/4 active")
                                    : "optional"
                                color: panel.configuredTiersCount > 0 ? Kirigami.Theme.positiveTextColor : panel.mutedColor
                                font.pixelSize: 9
                            }
                        }
                        TapHandler { onTapped: panel.tiersExpanded = !panel.tiersExpanded }
                    }

                    ColumnLayout {
                        visible: panel.tiersExpanded
                        Layout.fillWidth: true
                        spacing: 5

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Text {
                                Layout.fillWidth: true
                                text: "One daemon serves model IDs (oasis/diamond) and tier URLs (/tier/diamond/v1) with shared OAuth pools."
                                color: panel.mutedColor
                                font.pixelSize: 8
                                wrapMode: Text.WordWrap
                            }

                            QQC2.ToolButton {
                                enabled: !panel.busy && panel.intelAvailable
                                text: "Import AI Intel"
                                font.pixelSize: 9
                                Accessible.name: "Preview and import tier routing from AI Intel"
                                onClicked: panel.importIntelPreviewRequested()
                                QQC2.ToolTip.text: panel.intelAvailable
                                    ? "Preview mapping cached AI Intel tiers to your connected proxy models"
                                    : "Open Intel and run Scan first to cache tier rankings"
                                QQC2.ToolTip.visible: hovered
                                QQC2.ToolTip.delay: 400
                            }
                        }

                        // Inline preview before applying AI Intel import
                        Rectangle {
                            visible: panel.intelPreview !== null
                            Layout.fillWidth: true
                            radius: 4
                            implicitHeight: prevCol.implicitHeight + 10
                            color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.12)
                            border.width: 1
                            border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.45)

                            ColumnLayout {
                                id: prevCol
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 5
                                spacing: 4

                                Text {
                                    Layout.fillWidth: true
                                    text: panel.intelPreview && panel.intelPreview.matched_live_models
                                        ? "AI Intel Import Preview (matched to connected models):"
                                        : "AI Intel Import Preview (draft — start proxy to filter by connected accounts):"
                                    color: Kirigami.Theme.textColor
                                    font.pixelSize: 9
                                    font.bold: true
                                    wrapMode: Text.WordWrap
                                }

                                Repeater {
                                    model: panel.intelPreview && panel.intelPreview.preview ? panel.intelPreview.preview : []
                                    delegate: RowLayout {
                                        required property var modelData
                                        readonly property var tMeta: panel.tierMeta(String(modelData.tier || ""))
                                        readonly property var tList: modelData.targets || []
                                        Layout.fillWidth: true
                                        spacing: 5

                                        Text {
                                            text: tMeta.label + ":"
                                            color: tMeta.color
                                            font.pixelSize: 8
                                            font.bold: true
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: tList.length > 0
                                                ? String(tList[0].model) + " · " + panel.thinkingLabel(tList[0].thinking)
                                                  + (tList.length > 1 ? " (+" + (tList.length - 1) + ")" : "")
                                                : "no connected match"
                                            color: tList.length > 0 ? Kirigami.Theme.textColor : panel.mutedColor
                                            font.pixelSize: 8
                                            elide: Text.ElideRight
                                        }
                                    }
                                }

                                Text {
                                    visible: !!(panel.intelPreview && panel.intelPreview.skipped && panel.intelPreview.skipped.length > 0)
                                    Layout.fillWidth: true
                                    text: String(panel.intelPreview && panel.intelPreview.skipped ? panel.intelPreview.skipped.length : 0)
                                        + " Intel model(s) skipped (not connected on this proxy)."
                                    color: panel.mutedColor
                                    font.pixelSize: 8
                                    wrapMode: Text.WordWrap
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 5

                                    QQC2.Button {
                                        enabled: !panel.busy
                                        text: "Apply Tiers"
                                        font.pixelSize: 9
                                        onClicked: panel.importIntelApplyRequested()
                                    }

                                    QQC2.ToolButton {
                                        enabled: !panel.busy
                                        text: "Cancel"
                                        font.pixelSize: 9
                                        onClicked: panel.clearIntelPreviewRequested()
                                    }
                                }
                            }
                        }

                        // 4 Tier rows: Diamond, Gold, Silver, Subagent
                        Repeater {
                            model: panel.tierOrder

                            delegate: Rectangle {
                                required property string modelData
                                readonly property string tierId: modelData
                                readonly property var tMeta: panel.tierMeta(tierId)
                                readonly property color tColor: tMeta.color
                                readonly property var tInfo: panel.tiersMap[tierId] || ({
                                    id: tierId,
                                    model_id: "oasis/" + tierId,
                                    openai_base_url: "http://127.0.0.1:8317/tier/" + tierId + "/v1",
                                    targets: []
                                })
                                readonly property var tTargets: tInfo.targets || []
                                readonly property bool isEditing: panel.editingTier === tierId

                                Layout.fillWidth: true
                                radius: 4
                                implicitHeight: tierRowCol.implicitHeight + 8
                                color: isEditing
                                    ? Qt.rgba(tColor.r, tColor.g, tColor.b, 0.12)
                                    : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.04)
                                border.width: isEditing ? 1 : 0
                                border.color: Qt.rgba(tColor.r, tColor.g, tColor.b, 0.55)

                                ColumnLayout {
                                    id: tierRowCol
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: 4
                                    spacing: 4

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 5

                                        Rectangle {
                                            radius: 3
                                            implicitWidth: tBadgeTxt.implicitWidth + 8
                                            implicitHeight: 16
                                            color: Qt.rgba(tColor.r, tColor.g, tColor.b, 0.20)
                                            Text {
                                                id: tBadgeTxt
                                                anchors.centerIn: parent
                                                text: tMeta.label
                                                color: tColor
                                                font.pixelSize: 8
                                                font.bold: true
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: tTargets.length > 0
                                                ? String(tTargets[0].model)
                                                  + " · " + panel.thinkingLabel(tTargets[0].thinking)
                                                  + (tTargets.length > 1 ? " +" + (tTargets.length - 1) : "")
                                                : "Not configured"
                                            color: tTargets.length > 0 ? Kirigami.Theme.textColor : panel.mutedColor
                                            font.pixelSize: 9
                                            elide: Text.ElideRight
                                        }

                                        QQC2.ToolButton {
                                            enabled: !panel.busy
                                            text: "Copy URL"
                                            font.pixelSize: 8
                                            Accessible.name: "Copy " + tMeta.label + " tier endpoint URL"
                                            onClicked: panel.copyTierEndpointRequested(tierId)
                                            QQC2.ToolTip.text: String(tInfo.openai_base_url || ("http://127.0.0.1:8317/tier/" + tierId + "/v1"))
                                            QQC2.ToolTip.visible: hovered
                                            QQC2.ToolTip.delay: 400
                                        }

                                        QQC2.ToolButton {
                                            enabled: !panel.busy
                                            text: isEditing ? "Close" : "Edit"
                                            font.pixelSize: 8
                                            Accessible.name: (isEditing ? "Close " : "Edit ") + tMeta.label + " tier"
                                            onClicked: panel.openTierEditor(tierId)
                                        }
                                    }

                                    // Inline editor for this tier (full searchable & provider-filtered model picker)
                                    ColumnLayout {
                                        visible: isEditing
                                        Layout.fillWidth: true
                                        spacing: 4

                                        // Provider filter pills
                                        Flow {
                                            Layout.fillWidth: true
                                            spacing: 3

                                            Repeater {
                                                model: panel.tierFilterProviders
                                                delegate: Rectangle {
                                                    required property string modelData
                                                    readonly property bool actPf: panel.tierProviderFilter === modelData
                                                    implicitWidth: pfLbl.implicitWidth + 8
                                                    implicitHeight: 16
                                                    radius: 3
                                                    color: actPf
                                                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.24)
                                                        : "transparent"
                                                    border.width: 1
                                                    border.color: actPf ? Kirigami.Theme.highlightColor : panel.cardBorder

                                                    Text {
                                                        id: pfLbl
                                                        anchors.centerIn: parent
                                                        text: modelData === "all" ? "All" : F.meta(modelData).name
                                                        color: actPf ? Kirigami.Theme.textColor : panel.mutedColor
                                                        font.pixelSize: 8
                                                        font.bold: actPf
                                                    }

                                                    TapHandler {
                                                        onTapped: panel.tierProviderFilter = modelData
                                                    }
                                                }
                                            }
                                        }

                                        QQC2.TextField {
                                            Layout.fillWidth: true
                                            text: panel.tierDraftModel
                                            placeholderText: "Filter or pick a model below (or type custom ID)…"
                                            font.pixelSize: 9
                                            onTextEdited: panel.tierDraftModel = text
                                        }

                                        // Scrollable full model list (all live + catalog models, zero 8-item cutoff)
                                        Rectangle {
                                            visible: panel.selectableModels.length > 0
                                            Layout.fillWidth: true
                                            implicitHeight: Math.min(tierModelList.contentHeight + 6, 132)
                                            radius: 4
                                            color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.03)
                                            border.width: 1
                                            border.color: panel.cardBorder
                                            clip: true

                                            ListView {
                                                id: tierModelList
                                                anchors.fill: parent
                                                anchors.margins: 3
                                                clip: true
                                                spacing: 2
                                                boundsBehavior: Flickable.StopAtBounds
                                                model: panel.selectableModels
                                                QQC2.ScrollBar.vertical: QQC2.ScrollBar {
                                                    policy: tierModelList.contentHeight > tierModelList.height
                                                        ? QQC2.ScrollBar.AsNeeded
                                                        : QQC2.ScrollBar.AlwaysOff
                                                }

                                                delegate: Rectangle {
                                                    required property var modelData
                                                    readonly property bool picked: panel.tierDraftModel === String(modelData.id)
                                                    readonly property bool isLive: !!modelData.connected
                                                    width: tierModelList.width
                                                    height: 20
                                                    radius: 3
                                                    color: picked
                                                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.25)
                                                        : "transparent"
                                                    border.width: picked ? 1 : 0
                                                    border.color: Kirigami.Theme.highlightColor

                                                    RowLayout {
                                                        anchors.fill: parent
                                                        anchors.leftMargin: 5
                                                        anchors.rightMargin: 5
                                                        spacing: 5

                                                        Rectangle {
                                                            implicitWidth: 5
                                                            implicitHeight: 5
                                                            radius: 3
                                                            color: isLive ? Kirigami.Theme.positiveTextColor : panel.mutedColor
                                                        }

                                                        Text {
                                                            Layout.fillWidth: true
                                                            text: String(modelData.id)
                                                            color: picked ? Kirigami.Theme.textColor : (isLive ? Kirigami.Theme.textColor : panel.mutedColor)
                                                            font.pixelSize: 8
                                                            font.bold: picked || isLive
                                                            elide: Text.ElideRight
                                                        }

                                                        Text {
                                                            text: F.meta(String(modelData.provider || "")).short + (isLive ? " · live" : "")
                                                            color: isLive ? Kirigami.Theme.positiveTextColor : panel.mutedColor
                                                            font.pixelSize: 7
                                                        }
                                                    }

                                                    TapHandler {
                                                        onTapped: panel.tierDraftModel = String(modelData.id)
                                                    }
                                                }
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 3

                                            Text {
                                                text: "Thinking:"
                                                color: panel.mutedColor
                                                font.pixelSize: 8
                                            }

                                            Flow {
                                                Layout.fillWidth: true
                                                spacing: 3
                                                Repeater {
                                                    model: panel.thinkingOptions
                                                    delegate: Rectangle {
                                                        required property var modelData
                                                        readonly property bool act: panel.tierDraftThinking === modelData.id
                                                        implicitWidth: ttLbl.implicitWidth + 8
                                                        implicitHeight: 16
                                                        radius: 3
                                                        color: act
                                                            ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.24)
                                                            : "transparent"
                                                        border.width: 1
                                                        border.color: act ? Kirigami.Theme.highlightColor : panel.cardBorder

                                                        Text {
                                                            id: ttLbl
                                                            anchors.centerIn: parent
                                                            text: modelData.label
                                                            color: act ? Kirigami.Theme.textColor : panel.mutedColor
                                                            font.pixelSize: 8
                                                            font.bold: act
                                                        }

                                                        TapHandler {
                                                            onTapped: panel.tierDraftThinking = modelData.id
                                                        }
                                                    }
                                                }
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 4

                                            QQC2.Button {
                                                enabled: !panel.busy && panel.tierDraftModel.trim().length > 0
                                                text: "Save"
                                                font.pixelSize: 8
                                                onClicked: {
                                                    panel.setTierRequested(tierId, panel.tierDraftModel.trim(), panel.tierDraftThinking, false)
                                                    panel.editingTier = ""
                                                }
                                            }

                                            QQC2.ToolButton {
                                                visible: tTargets.length > 0
                                                enabled: !panel.busy
                                                text: "Clear"
                                                font.pixelSize: 8
                                                onClicked: {
                                                    panel.setTierRequested(tierId, "", "auto", true)
                                                    panel.editingTier = ""
                                                }
                                            }

                                            Item { Layout.fillWidth: true }

                                            Text {
                                                text: "model: " + String(tInfo.model_id || ("oasis/" + tierId))
                                                color: panel.mutedColor
                                                font.pixelSize: 8
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Progressively disclosed Advanced Routing Settings
            Rectangle {
                Layout.fillWidth: true
                radius: 6
                implicitHeight: routeCol.implicitHeight + 12
                color: panel.cardBg
                border.width: 1
                border.color: panel.cardBorder

                ColumnLayout {
                    id: routeCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 6
                    spacing: 5

                    Item {
                        Layout.fillWidth: true
                        implicitHeight: routeHdrRow.implicitHeight
                        activeFocusOnTab: true
                        Accessible.role: Accessible.Button
                        Accessible.name: "Toggle Routing and Session Affinity"
                        Keys.onReturnPressed: panel.routingExpanded = !panel.routingExpanded
                        Keys.onSpacePressed: panel.routingExpanded = !panel.routingExpanded

                        RowLayout {
                            id: routeHdrRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            Text {
                                text: (panel.routingExpanded ? "▾ " : "▸ ") + "Routing & Session Affinity"
                                color: Kirigami.Theme.textColor
                                font.pixelSize: 10
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Text {
                                text: String(panel.routingInfo.strategy || "smart-quota")
                                color: panel.mutedColor
                                font.pixelSize: 9
                            }
                        }
                        TapHandler { onTapped: panel.routingExpanded = !panel.routingExpanded }
                    }

                    ColumnLayout {
                        visible: panel.routingExpanded
                        Layout.fillWidth: true
                        spacing: 5

                        Flow {
                            Layout.fillWidth: true
                            spacing: 4
                            Repeater {
                                model: ["smart-quota", "least-used", "round-robin", "fill-first"]
                                delegate: Rectangle {
                                    required property string modelData
                                    readonly property bool activeStrat: String(panel.routingInfo.strategy || "smart-quota") === modelData
                                    implicitWidth: sTxt.implicitWidth + 10
                                    implicitHeight: 20
                                    radius: 4
                                    activeFocusOnTab: true
                                    Accessible.role: Accessible.RadioButton
                                    Accessible.name: "Routing strategy " + modelData
                                    Accessible.checked: activeStrat
                                    color: activeStrat
                                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                                        : "transparent"
                                    border.width: activeFocus ? 2 : 1
                                    border.color: (activeStrat || activeFocus) ? Kirigami.Theme.highlightColor : panel.cardBorder

                                    Keys.onReturnPressed: if (!panel.busy && !activeStrat) panel.routingChanged({ strategy: modelData })
                                    Keys.onSpacePressed: if (!panel.busy && !activeStrat) panel.routingChanged({ strategy: modelData })

                                    Text {
                                        id: sTxt
                                        anchors.centerIn: parent
                                        text: modelData
                                        color: activeStrat ? Kirigami.Theme.textColor : panel.mutedColor
                                        font.pixelSize: 9
                                        font.bold: activeStrat
                                    }
                                    TapHandler {
                                        enabled: !panel.busy && !activeStrat
                                        onTapped: panel.routingChanged({ strategy: modelData })
                                    }
                                }
                            }
                        }

                        QQC2.CheckBox {
                            Layout.fillWidth: true
                            checked: panel.routingInfo.session_affinity !== false
                            enabled: !panel.busy
                            text: "Keep coding sessions pinned (preserves prompt cache)"
                            font.pixelSize: 9
                            onToggled: panel.routingChanged({ session_affinity: checked })
                        }

                        QQC2.CheckBox {
                            Layout.fillWidth: true
                            checked: panel.routingInfo.codex_websockets !== false
                            enabled: !panel.busy
                            text: "Native Codex WebSocket relay"
                            font.pixelSize: 9
                            onToggled: panel.routingChanged({ codex_websockets: checked })
                        }

                        QQC2.CheckBox {
                            Layout.fillWidth: true
                            checked: !!panel.routingInfo.force_model_prefix
                            enabled: !panel.busy
                            text: "Require explicit provider/model prefix (no implicit fallback)"
                            font.pixelSize: 9
                            onToggled: panel.routingChanged({ force_model_prefix: checked })
                        }

                        QQC2.CheckBox {
                            Layout.fillWidth: true
                            checked: !!panel.policyInfo.allow_metered_fallback
                            enabled: !panel.busy
                            text: "Allow fallback to metered API keys"
                            font.pixelSize: 9
                            onToggled: panel.routingChanged({ allow_metered_fallback: checked })
                        }
                    }
                }
            }

            // Progressively disclosed Recent Requests & Clients
            Rectangle {
                visible: panel.showStats
                Layout.fillWidth: true
                radius: 6
                implicitHeight: statsCol.implicitHeight + 12
                color: panel.cardBg
                border.width: 1
                border.color: panel.cardBorder

                ColumnLayout {
                    id: statsCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 6
                    spacing: 4

                    Item {
                        Layout.fillWidth: true
                        implicitHeight: statsHdrRow.implicitHeight
                        activeFocusOnTab: true
                        Accessible.role: Accessible.Button
                        Accessible.name: "Toggle Requests and Connected Clients"
                        Keys.onReturnPressed: panel.statsExpanded = !panel.statsExpanded
                        Keys.onSpacePressed: panel.statsExpanded = !panel.statsExpanded

                        RowLayout {
                            id: statsHdrRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            Text {
                                text: (panel.statsExpanded ? "▾ " : "▸ ") + "Requests & Connected Clients"
                                color: Kirigami.Theme.textColor
                                font.pixelSize: 10
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Text {
                                text: panel.recentRequests.length + " recent · " + panel.clientsList.length + " clients"
                                color: panel.mutedColor
                                font.pixelSize: 9
                            }
                        }
                        TapHandler { onTapped: panel.statsExpanded = !panel.statsExpanded }
                    }

                    ColumnLayout {
                        visible: panel.statsExpanded
                        Layout.fillWidth: true
                        spacing: 3

                        // Connected clients list
                        Flow {
                            visible: panel.clientsList.length > 0
                            Layout.fillWidth: true
                            spacing: 4

                            Repeater {
                                model: panel.clientsList.slice(0, 6)
                                delegate: Rectangle {
                                    required property var modelData
                                    implicitWidth: cliLbl.implicitWidth + 8
                                    implicitHeight: 16
                                    radius: 3
                                    color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.14)
                                    Text {
                                        id: cliLbl
                                        anchors.centerIn: parent
                                        text: String(modelData.name || modelData.id || "client") + " (" + Number(modelData.requests || 0) + ")"
                                        color: Kirigami.Theme.textColor
                                        font.pixelSize: 8
                                    }
                                }
                            }
                        }

                        Text {
                            visible: panel.recentRequests.length === 0
                            text: "No proxy requests recorded yet."
                            color: panel.mutedColor
                            font.pixelSize: 9
                        }

                        Repeater {
                            model: panel.recentRequests.slice(0, 8)
                            delegate: RowLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 5
                                Text {
                                    text: String(modelData.status || "?")
                                    color: Number(modelData.status || 500) < 400
                                        ? Kirigami.Theme.positiveTextColor
                                        : Kirigami.Theme.negativeTextColor
                                    font.pixelSize: 9
                                    font.bold: true
                                }
                                Text {
                                    text: String(modelData.model || "") + " → " + String(modelData.provider || "")
                                    color: Kirigami.Theme.textColor
                                    font.pixelSize: 9
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: (modelData.latency_ms || 0) + "ms"
                                    color: panel.mutedColor
                                    font.pixelSize: 8
                                }
                            }
                        }
                    }
                }
            }

            // Progressively disclosed Security & Access Key Rotation
            Rectangle {
                Layout.fillWidth: true
                radius: 6
                implicitHeight: secCol.implicitHeight + 12
                color: panel.cardBg
                border.width: 1
                border.color: panel.cardBorder

                ColumnLayout {
                    id: secCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 6
                    spacing: 5

                    Item {
                        Layout.fillWidth: true
                        implicitHeight: secHdrRow.implicitHeight
                        activeFocusOnTab: true
                        Accessible.role: Accessible.Button
                        Accessible.name: "Toggle Security and Access Keys"
                        Keys.onReturnPressed: panel.securityExpanded = !panel.securityExpanded
                        Keys.onSpacePressed: panel.securityExpanded = !panel.securityExpanded

                        RowLayout {
                            id: secHdrRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            Text {
                                text: (panel.securityExpanded ? "▾ " : "▸ ") + "Security & Access Keys"
                                color: Kirigami.Theme.textColor
                                font.pixelSize: 10
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Text {
                                text: "127.0.0.1 only"
                                color: panel.mutedColor
                                font.pixelSize: 9
                            }
                        }
                        TapHandler { onTapped: panel.securityExpanded = !panel.securityExpanded }
                    }

                    ColumnLayout {
                        visible: panel.securityExpanded
                        Layout.fillWidth: true
                        spacing: 5

                        Text {
                            Layout.fillWidth: true
                            text: "Oasis Proxy listens only on localhost with separate client and management keys. Proxy OAuth tokens stay isolated from your native CLI logins."
                            color: panel.mutedColor
                            font.pixelSize: 9
                            wrapMode: Text.WordWrap
                        }

                        Flow {
                            Layout.fillWidth: true
                            spacing: 5

                            QQC2.Button {
                                enabled: !panel.busy
                                text: panel.confirmRotate ? "Confirm rotate key?" : "Rotate client key"
                                font.pixelSize: 9
                                Accessible.name: "Rotate Oasis Proxy client API key"
                                onClicked: {
                                    if (!panel.confirmRotate) {
                                        panel.confirmRotate = true
                                    } else {
                                        panel.confirmRotate = false
                                        panel.rotateKeyRequested()
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
