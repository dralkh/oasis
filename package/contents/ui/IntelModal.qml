import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/format.js" as F

ColumnLayout {
    id: modal

    property var report: null
    property double nowMs: 0
    property bool scanning: false
    property string errorText: ""
    property int activeTab: 0
    property string selectedAgent: "auto"

    signal backRequested()
    signal scanRequested(string agentId)
    signal agentSelected(string agentId)
    signal quitRequested()

    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.70)
    readonly property color cardBg: Qt.rgba(Kirigami.Theme.textColor.r,
                                            Kirigami.Theme.textColor.g,
                                            Kirigami.Theme.textColor.b, 0.05)
    readonly property color cardBorder: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.11)

    readonly property bool hasScan: !!(report && report.has_scan)
    readonly property var availableAgents: report && report.available_agents ? report.available_agents : []
    readonly property var selectableAgents: {
        var out = []
        for (var i = 0; i < availableAgents.length; ++i) {
            var a = availableAgents[i]
            if (a && (a.id === "auto" || a.installed)) {
                out.push(a)
            }
        }
        return out
    }
    readonly property string activeAgentLabel: {
        for (var i = 0; i < availableAgents.length; ++i) {
            var a = availableAgents[i]
            if (a && String(a.id) === String(modal.selectedAgent || "auto")) {
                return String(a.detail || a.label || a.id)
            }
        }
        return modal.selectedAgent === "auto" ? "Auto" : String(modal.selectedAgent || "Auto")
    }
    readonly property var deals: report && report.deals ? report.deals : []
    readonly property var projections: report && report.limit_projections ? report.limit_projections : []
    readonly property var mainModels: report && report.model_tiers && report.model_tiers.main_agent
        ? report.model_tiers.main_agent : []
    readonly property var subModels: report && report.model_tiers && report.model_tiers.subagent
        ? report.model_tiers.subagent : []
    readonly property var metrics: report && report.crucial_metrics ? report.crucial_metrics : ({})
    readonly property var highlights: metrics && metrics.highlights ? metrics.highlights : []
    readonly property var alerts: metrics && metrics.policy_alerts ? metrics.policy_alerts : []
    readonly property var sources: report && report.sources ? report.sources : []

    spacing: 6

    // Top bar: Back, title + model/age, Scan (pi + Tavily), Quit
    RowLayout {
        Layout.fillWidth: true
        spacing: 6

        QQC2.ToolButton {
            icon.name: "go-previous"
            implicitWidth: 22
            implicitHeight: 22
            padding: 2
            onClicked: modal.backRequested()
            QQC2.ToolTip.text: "Back to usage"
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: 400
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0

            Text {
                text: "AI Intel"
                color: Kirigami.Theme.textColor
                font.pixelSize: 13
                font.bold: true
            }

            Text {
                text: {
                    if (modal.scanning) return "deep worldwide research via " + modal.activeAgentLabel + "…"
                    var m = modal.report && modal.report.model_used ? String(modal.report.model_used) : modal.activeAgentLabel
                    var shortModel = m.indexOf("/") >= 0 ? m.split("/").pop() : m
                    if (modal.hasScan && modal.report.generated_at) {
                        return shortModel + " · Worldwide · " + F.updatedText(F.toMs(modal.report.generated_at), modal.nowMs)
                    }
                    return modal.activeAgentLabel + " · tap Scan for worldwide intel"
                }
                color: modal.mutedColor
                font.pixelSize: 9
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        QQC2.ToolButton {
            id: scanBtn
            enabled: !modal.scanning
            implicitHeight: 22
            leftPadding: 7
            rightPadding: 7
            topPadding: 2
            bottomPadding: 2
            onClicked: modal.scanRequested(modal.selectedAgent)

            contentItem: RowLayout {
                spacing: 4
                Kirigami.Icon {
                    source: modal.scanning ? "process-working" : "view-refresh"
                    implicitWidth: 12
                    implicitHeight: 12
                }
                Text {
                    text: modal.scanning ? "Scanning…" : "Scan"
                    color: scanBtn.enabled ? Kirigami.Theme.textColor : modal.mutedColor
                    font.pixelSize: 10
                    font.bold: true
                }
            }

            background: Rectangle {
                radius: 4
                color: scanBtn.down
                    ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.28)
                    : scanBtn.hovered
                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.20)
                        : Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.13)
            }

            QQC2.ToolTip.text: "Run worldwide AI Intel scan via " + modal.activeAgentLabel
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: 400
        }

        QQC2.ToolButton {
            id: modalQuitBtn
            Layout.rightMargin: -3
            text: "×"
            display: QQC2.AbstractButton.TextOnly
            flat: true
            implicitWidth: 16
            implicitHeight: 16
            padding: 0
            font.pixelSize: 13
            onClicked: modal.quitRequested()
            Accessible.name: "Quit Oasis"

            contentItem: Text {
                text: modalQuitBtn.text
                font: modalQuitBtn.font
                color: modalQuitBtn.hovered ? Kirigami.Theme.textColor : modal.mutedColor
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }

            QQC2.ToolTip.text: "Quit Oasis"
            QQC2.ToolTip.visible: hovered
            QQC2.ToolTip.delay: 500
        }
    }

    // CLI agent selector row (Auto + every installed CLI agent, incl. Pi)
    RowLayout {
        visible: modal.selectableAgents.length > 0
        Layout.fillWidth: true
        spacing: 4

        Text {
            text: "Agent:"
            color: modal.mutedColor
            font.pixelSize: 9
        }

        Flow {
            Layout.fillWidth: true
            spacing: 4

            Repeater {
                model: modal.selectableAgents

                delegate: Rectangle {
                    required property var modelData
                    readonly property string aid: String(modelData.id || "")
                    readonly property bool picked: String(modal.selectedAgent || "auto") === aid
                    implicitWidth: agTxt.implicitWidth + 10
                    implicitHeight: 18
                    radius: 4
                    activeFocusOnTab: true
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: "Use " + String(modelData.detail || modelData.label || aid) + " for AI Intel"
                    Accessible.checked: picked
                    color: picked
                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                        : "transparent"
                    border.width: activeFocus ? 2 : 1
                    border.color: (picked || activeFocus) ? Kirigami.Theme.highlightColor : modal.cardBorder

                    Keys.onReturnPressed: if (!modal.scanning) modal.agentSelected(aid)
                    Keys.onSpacePressed: if (!modal.scanning) modal.agentSelected(aid)

                    Text {
                        id: agTxt
                        anchors.centerIn: parent
                        text: aid === "auto" ? String(modelData.detail || "Auto") : String(modelData.label || aid)
                        color: picked ? Kirigami.Theme.textColor : modal.mutedColor
                        font.pixelSize: 8
                        font.bold: picked
                    }

                    TapHandler {
                        enabled: !modal.scanning
                        onTapped: modal.agentSelected(aid)
                    }
                }
            }
        }
    }

    // Compact 4-tab pill selector
    RowLayout {
        Layout.fillWidth: true
        spacing: 4

        Repeater {
            model: [
                { id: 0, label: "Tiers" },
                { id: 1, label: modal.deals.length > 0 ? "Deals (" + modal.deals.length + ")" : "Deals" },
                { id: 2, label: "Limits" },
                { id: 3, label: "Pulse" }
            ]

            delegate: Rectangle {
                required property var modelData
                readonly property bool selected: modal.activeTab === modelData.id

                Layout.fillWidth: true
                implicitHeight: 22
                radius: 4
                color: selected
                    ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                    : (tabHover.hovered ? modal.cardBg : "transparent")
                border.width: 1
                border.color: selected
                    ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.45)
                    : modal.cardBorder

                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: selected ? Kirigami.Theme.highlightColor : Kirigami.Theme.textColor
                    font.pixelSize: 10
                    font.bold: selected
                }

                HoverHandler { id: tabHover }
                TapHandler { onTapped: modal.activeTab = modelData.id }
            }
        }
    }

    // Error banner
    Rectangle {
        Layout.fillWidth: true
        visible: modal.errorText.length > 0 || (modal.report && modal.report.error)
        radius: 4
        implicitHeight: errLbl.implicitHeight + 8
        color: Qt.rgba(Kirigami.Theme.negativeTextColor.r, Kirigami.Theme.negativeTextColor.g, Kirigami.Theme.negativeTextColor.b, 0.14)

        Text {
            id: errLbl
            anchors.fill: parent
            anchors.margins: 4
            text: modal.errorText.length > 0 ? modal.errorText : String(modal.report ? modal.report.error || "" : "")
            color: Kirigami.Theme.negativeTextColor
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }
    }

    // Scrollable compact content body
    QQC2.ScrollView {
        id: scroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 8

            // Empty-scan callout on web-sourced tabs (Tiers / Deals)
            Rectangle {
                Layout.fillWidth: true
                visible: !modal.hasScan && (modal.activeTab === 0 || modal.activeTab === 1)
                radius: 6
                implicitHeight: emptyCol.implicitHeight + 20
                color: modal.cardBg
                border.width: 1
                border.color: modal.cardBorder

                ColumnLayout {
                    id: emptyCol
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    Text {
                        Layout.fillWidth: true
                        text: modal.scanning
                            ? "Scouring web with " + modal.activeAgentLabel + "…"
                            : "No web intel cached yet"
                        color: Kirigami.Theme.textColor
                        font.pixelSize: 11
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                    }

                    Text {
                        Layout.fillWidth: true
                        text: modal.scanning
                            ? "Checking active discounts, retention offers, Diamond/Gold/Silver model tiers, and provider updates."
                            : "Choose any installed CLI agent above (or Auto) and click Scan for current deals, retention offers, Diamond/Gold/Silver model rankings, and market alerts."
                        color: modal.mutedColor
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }

                    QQC2.Button {
                        Layout.alignment: Qt.AlignHCenter
                        visible: !modal.scanning
                        text: "Scan with " + modal.activeAgentLabel
                        icon.name: "view-refresh"
                        onClicked: modal.scanRequested(modal.selectedAgent)
                    }
                }
            }

            // TAB 0: MODEL TIERS (Diamond / Gold / Silver for Main Agent & Subagent)
            ColumnLayout {
                Layout.fillWidth: true
                visible: modal.activeTab === 0 && modal.hasScan
                spacing: 8

                TierGraph {
                    mainModels: modal.mainModels
                    subModels: modal.subModels
                }

                Repeater {
                    model: [
                        { title: "MAIN AGENT", subtitle: "Planning, architecture & deep reasoning", list: modal.mainModels },
                        { title: "SUBAGENT", subtitle: "Fast, low-cost search & execution workers", list: modal.subModels }
                    ]

                    delegate: ColumnLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Text {
                                text: modelData.title
                                color: Kirigami.Theme.textColor
                                font.pixelSize: 10
                                font.bold: true
                            }
                            Text {
                                text: modelData.subtitle
                                color: modal.mutedColor
                                font.pixelSize: 9
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }

                        Repeater {
                            model: modelData.list

                            delegate: Rectangle {
                                required property var modelData
                                readonly property color tColor: F.tierColor(modelData.tier)

                                Layout.fillWidth: true
                                radius: 5
                                implicitHeight: tierCol.implicitHeight + 10
                                color: modal.cardBg
                                border.width: 1
                                border.color: Qt.rgba(tColor.r, tColor.g, tColor.b, 0.30)

                                ColumnLayout {
                                    id: tierCol
                                    anchors.fill: parent
                                    anchors.margins: 5
                                    spacing: 2

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 5

                                        Rectangle {
                                            radius: 3
                                            implicitWidth: tLbl.implicitWidth + 8
                                            implicitHeight: 15
                                            color: Qt.rgba(tColor.r, tColor.g, tColor.b, 0.18)

                                            Text {
                                                id: tLbl
                                                anchors.centerIn: parent
                                                text: F.tierGlyph(modelData.tier) + " " + String(modelData.tier || "")
                                                color: tColor
                                                font.pixelSize: 9
                                                font.bold: true
                                            }
                                        }

                                        Text {
                                            text: String(modelData.model || "")
                                            color: Kirigami.Theme.textColor
                                            font.pixelSize: 11
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }

                                        Text {
                                            text: modelData.provider ? "· " + String(modelData.provider) : ""
                                            color: modal.mutedColor
                                            font.pixelSize: 9
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }

                                        Text {
                                            visible: !!(modelData.source_url && String(modelData.source_url).indexOf("http") === 0)
                                            text: "↗"
                                            color: Kirigami.Theme.linkColor
                                            font.pixelSize: 10
                                            font.bold: true
                                            TapHandler {
                                                onTapped: Qt.openUrlExternally(String(modelData.source_url))
                                            }
                                        }
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 5
                                        visible: !!(modelData.cost_per_task && String(modelData.cost_per_task).length > 0)

                                        Rectangle {
                                            radius: 3
                                            implicitWidth: Math.min(costLbl.implicitWidth + 8, tierCol.width)
                                            implicitHeight: 15
                                            color: Qt.rgba(Kirigami.Theme.positiveTextColor.r, Kirigami.Theme.positiveTextColor.g, Kirigami.Theme.positiveTextColor.b, 0.14)

                                            Text {
                                                id: costLbl
                                                anchors.centerIn: parent
                                                width: Math.min(implicitWidth, tierCol.width - 8)
                                                text: String(modelData.cost_per_task || "")
                                                color: Kirigami.Theme.positiveTextColor
                                                font.pixelSize: 9
                                                font.bold: true
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        visible: !!(modelData.benchmark && String(modelData.benchmark).length > 0)
                                        text: String(modelData.benchmark || "")
                                        color: tColor
                                        font.pixelSize: 9
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        visible: !!(modelData.why && String(modelData.why).length > 0)
                                        text: String(modelData.why || "")
                                        color: modal.mutedColor
                                        font.pixelSize: 9
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // TAB 1: DEALS (Discounts, free months, retention/cancel offers)
            ColumnLayout {
                Layout.fillWidth: true
                visible: modal.activeTab === 1 && modal.hasScan
                spacing: 5

                Text {
                    Layout.fillWidth: true
                    visible: modal.deals.length === 0
                    text: "No verified active discounts, free months, or retention offers found in the last scan."
                    color: modal.mutedColor
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }

                Repeater {
                    model: modal.deals

                    delegate: Rectangle {
                        required property var modelData
                        readonly property bool isRetention: String(modelData.audience || "").toLowerCase().indexOf("cancel") >= 0
                            || String(modelData.audience || "").toLowerCase().indexOf("retention") >= 0

                        Layout.fillWidth: true
                        radius: 5
                        implicitHeight: dealCol.implicitHeight + 10
                        color: modal.cardBg
                        border.width: 1
                        border.color: modal.cardBorder

                        ColumnLayout {
                            id: dealCol
                            anchors.fill: parent
                            anchors.margins: 5
                            spacing: 3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 5

                                Text {
                                    text: String(modelData.provider || "")
                                    color: Kirigami.Theme.textColor
                                    font.pixelSize: 11
                                    font.bold: true
                                }

                                Text {
                                    text: String(modelData.offer || "")
                                    color: Kirigami.Theme.positiveTextColor
                                    font.pixelSize: 10
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }

                                Text {
                                    visible: !!(modelData.source_url && String(modelData.source_url).indexOf("http") === 0)
                                    text: "↗"
                                    color: Kirigami.Theme.linkColor
                                    font.pixelSize: 11
                                    font.bold: true
                                    TapHandler {
                                        onTapped: Qt.openUrlExternally(String(modelData.source_url))
                                    }
                                }
                            }

                            Flow {
                                Layout.fillWidth: true
                                spacing: 4

                                Rectangle {
                                    visible: !!(modelData.audience && String(modelData.audience).length > 0)
                                    radius: 3
                                    implicitWidth: audLbl.implicitWidth + 8
                                    implicitHeight: 14
                                    color: isRetention
                                        ? Qt.rgba(Kirigami.Theme.neutralTextColor.r, Kirigami.Theme.neutralTextColor.g, Kirigami.Theme.neutralTextColor.b, 0.18)
                                        : Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.16)

                                    Text {
                                        id: audLbl
                                        anchors.centerIn: parent
                                        text: String(modelData.audience || "")
                                        color: isRetention ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.highlightColor
                                        font.pixelSize: 8
                                        font.bold: true
                                    }
                                }

                                Rectangle {
                                    visible: !!(modelData.countries && String(modelData.countries).length > 0)
                                    radius: 3
                                    implicitWidth: ctryLbl.implicitWidth + 8
                                    implicitHeight: 14
                                    color: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.09)

                                    Text {
                                        id: ctryLbl
                                        anchors.centerIn: parent
                                        text: String(modelData.countries || "")
                                        color: modal.mutedColor
                                        font.pixelSize: 8
                                    }
                                }

                                Text {
                                    visible: !!(modelData.verified_date && String(modelData.verified_date).length > 0)
                                    text: String(modelData.verified_date || "")
                                    color: modal.mutedColor
                                    font.pixelSize: 8
                                    height: 14
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                visible: !!(modelData.details && String(modelData.details).length > 0)
                                text: String(modelData.details || "")
                                color: modal.mutedColor
                                font.pixelSize: 9
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            // TAB 2: LIVE LIMIT PROJECTIONS & SUBSCRIPTION COUNTDOWNS
            ColumnLayout {
                Layout.fillWidth: true
                visible: modal.activeTab === 2
                spacing: 5

                Rectangle {
                    Layout.fillWidth: true
                    visible: !!(modal.report && modal.report.limit_advice && String(modal.report.limit_advice).length > 0)
                    radius: 5
                    implicitHeight: adviceLbl.implicitHeight + 10
                    color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.14)

                    Text {
                        id: adviceLbl
                        anchors.fill: parent
                        anchors.margins: 5
                        text: String(modal.report ? modal.report.limit_advice || "" : "")
                        color: Kirigami.Theme.textColor
                        font.pixelSize: 10
                        wrapMode: Text.WordWrap
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: modal.projections.length === 0
                    text: "No active provider usage cached yet."
                    color: modal.mutedColor
                    font.pixelSize: 10
                }

                Repeater {
                    model: modal.projections

                    delegate: Rectangle {
                        required property var modelData
                        readonly property color stColor: {
                            var s = String(modelData.status || "")
                            if (s === "critical") return Kirigami.Theme.negativeTextColor
                            if (s === "warn") return Kirigami.Theme.neutralTextColor
                            if (s === "ready") return Kirigami.Theme.positiveTextColor
                            return Kirigami.Theme.highlightColor
                        }

                        Layout.fillWidth: true
                        radius: 5
                        implicitHeight: projCol.implicitHeight + 10
                        color: modal.cardBg
                        border.width: 1
                        border.color: modal.cardBorder

                        ColumnLayout {
                            id: projCol
                            anchors.fill: parent
                            anchors.margins: 5
                            spacing: 2

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 5

                                Text {
                                    text: String(modelData.provider || "")
                                    color: Kirigami.Theme.textColor
                                    font.pixelSize: 11
                                    font.bold: true
                                }

                                Rectangle {
                                    radius: 3
                                    implicitWidth: winLbl.implicitWidth + 8
                                    implicitHeight: 14
                                    color: Qt.rgba(stColor.r, stColor.g, stColor.b, 0.16)

                                    Text {
                                        id: winLbl
                                        anchors.centerIn: parent
                                        text: String(modelData.window || "") + " " + Math.round(Number(modelData.current_used) || 0) + "%"
                                        color: stColor
                                        font.pixelSize: 9
                                        font.bold: true
                                    }
                                }

                                Item { Layout.fillWidth: true }

                                Rectangle {
                                    visible: Number(modelData.reset_passes) > 0
                                    radius: 3
                                    implicitWidth: rpLbl.implicitWidth + 8
                                    implicitHeight: 14
                                    color: Qt.rgba(Kirigami.Theme.positiveTextColor.r, Kirigami.Theme.positiveTextColor.g, Kirigami.Theme.positiveTextColor.b, 0.16)

                                    Text {
                                        id: rpLbl
                                        anchors.centerIn: parent
                                        text: "↻ " + Number(modelData.reset_passes)
                                        color: Kirigami.Theme.positiveTextColor
                                        font.pixelSize: 9
                                        font.bold: true
                                    }
                                }

                                Text {
                                    visible: !!(modelData.subscription_left && String(modelData.subscription_left).length > 0)
                                    text: String(modelData.subscription_left || "")
                                    color: modal.mutedColor
                                    font.pixelSize: 9
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: String(modelData.projection || "")
                                color: modal.mutedColor
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            // TAB 3: PULSE (Crucial metrics, Best Value Stack, Alerts, Sources)
            ColumnLayout {
                Layout.fillWidth: true
                visible: modal.activeTab === 3
                spacing: 8

                // Compact 4-stat KPI strip
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    visible: modal.highlights.length > 0

                    Repeater {
                        model: modal.highlights

                        delegate: Rectangle {
                            required property var modelData
                            Layout.fillWidth: true
                            radius: 5
                            implicitHeight: kpiCol.implicitHeight + 10
                            color: modal.cardBg
                            border.width: 1
                            border.color: modal.cardBorder

                            ColumnLayout {
                                id: kpiCol
                                anchors.fill: parent
                                anchors.margins: 5
                                spacing: 1

                                Text {
                                    text: String(modelData.label || "")
                                    color: modal.mutedColor
                                    font.pixelSize: 8
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: String(modelData.value || "—")
                                    color: Kirigami.Theme.textColor
                                    font.pixelSize: 12
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: String(modelData.note || "")
                                    color: modal.mutedColor
                                    font.pixelSize: 8
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }

                // Best Value Stack
                Rectangle {
                    Layout.fillWidth: true
                    visible: !!(modal.metrics && modal.metrics.best_value_stack && String(modal.metrics.best_value_stack).length > 0)
                    radius: 5
                    implicitHeight: stackCol.implicitHeight + 10
                    color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.12)
                    border.width: 1
                    border.color: Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.30)

                    ColumnLayout {
                        id: stackCol
                        anchors.fill: parent
                        anchors.margins: 5
                        spacing: 2

                        Text {
                            text: "BEST VALUE STACK"
                            color: Kirigami.Theme.highlightColor
                            font.pixelSize: 9
                            font.bold: true
                        }
                        Text {
                            Layout.fillWidth: true
                            text: String(modal.metrics.best_value_stack || "")
                            color: Kirigami.Theme.textColor
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // Policy & Market Alerts
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: modal.alerts.length > 0
                    spacing: 3

                    Text {
                        text: "POLICY & MARKET ALERTS"
                        color: Kirigami.Theme.textColor
                        font.pixelSize: 9
                        font.bold: true
                    }

                    Repeater {
                        model: modal.alerts

                        delegate: Text {
                            required property var modelData
                            Layout.fillWidth: true
                            text: "• " + String(modelData)
                            color: modal.mutedColor
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // Sources
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: modal.sources.length > 0
                    spacing: 3

                    Text {
                        text: "SOURCES"
                        color: modal.mutedColor
                        font.pixelSize: 8
                        font.bold: true
                    }

                    Flow {
                        Layout.fillWidth: true
                        spacing: 4

                        Repeater {
                            model: modal.sources

                            delegate: Rectangle {
                                required property var modelData
                                radius: 3
                                implicitWidth: Math.min(srcLbl.implicitWidth + 10, 180)
                                implicitHeight: 16
                                color: modal.cardBg
                                border.width: 1
                                border.color: modal.cardBorder

                                Text {
                                    id: srcLbl
                                    anchors.centerIn: parent
                                    width: Math.min(implicitWidth, 170)
                                    text: String(modelData.title || modelData.url || "Source") + " ↗"
                                    color: Kirigami.Theme.linkColor
                                    font.pixelSize: 8
                                    elide: Text.ElideRight
                                }

                                TapHandler {
                                    onTapped: {
                                        if (modelData.url && String(modelData.url).indexOf("http") === 0) {
                                            Qt.openUrlExternally(String(modelData.url))
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
}
