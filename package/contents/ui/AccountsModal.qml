import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/format.js" as F

ColumnLayout {
    id: modal

    property var providers: []
    property string selectedProviderId: "codex"
    property double nowMs: 0
    property int warnThreshold: 80
    property int criticalThreshold: 95
    property bool busy: false
    property string errorText: ""
    property string noticeText: ""

    signal backRequested()
    signal refreshRequested()
    signal quitRequested()
    signal switchAccountRequested(string providerId, string accountId)
    signal snapshotAccountRequested(string providerId, string label)
    signal launchLoginRequested(string providerId, string label)
    signal addApiKeyPromptRequested(string providerId, string label, string region)
    signal renameAccountRequested(string providerId, string accountId, string newLabel)
    signal removeAccountRequested(string providerId, string accountId)

    property string editingAccountId: ""
    property string confirmRemoveId: ""
    property string selectedRegion: ""

    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.70)
    readonly property color cardBg: Qt.rgba(Kirigami.Theme.textColor.r,
                                            Kirigami.Theme.textColor.g,
                                            Kirigami.Theme.textColor.b, 0.05)
    readonly property color cardBorder: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.12)

    readonly property var supportedProviders: {
        var result = [];
        for (var i = 0; i < providers.length; ++i) {
            if (providers[i] && providers[i].supports_accounts) {
                result.push(providers[i]);
            }
        }
        return result;
    }

    readonly property var currentProvider: {
        for (var i = 0; i < supportedProviders.length; ++i) {
            if (supportedProviders[i].id === selectedProviderId) {
                return supportedProviders[i];
            }
        }
        return supportedProviders.length > 0 ? supportedProviders[0] : null;
    }

    readonly property var currentMeta: F.meta(currentProvider ? currentProvider.id : selectedProviderId)
    readonly property var currentAccounts: currentProvider && currentProvider.accounts ? currentProvider.accounts : []
    readonly property var currentCaps: currentProvider && currentProvider.account_capabilities
        ? currentProvider.account_capabilities
        : ({ supports_login: true, supports_api_key: false, api_key_placeholder: "", regions: [], login_hint: "" })

    onSelectedProviderIdChanged: {
        editingAccountId = ""
        confirmRemoveId = ""
        labelInput.text = ""
        var regions = currentCaps && currentCaps.regions ? currentCaps.regions : []
        selectedRegion = regions.length > 0 ? String(regions[0]) : ""
    }

    onCurrentProviderChanged: {
        var regions = currentCaps && currentCaps.regions ? currentCaps.regions : []
        if (regions.length > 0 && regions.indexOf(selectedRegion) === -1) {
            selectedRegion = String(regions[0])
        } else if (regions.length === 0) {
            selectedRegion = ""
        }
    }

    spacing: 6

    function chipColor(chip) {
        if (!chip) return mutedColor
        if (chip.ready) return Kirigami.Theme.positiveTextColor
        if (chip.used >= criticalThreshold) return Kirigami.Theme.negativeTextColor
        if (chip.used >= warnThreshold) return Kirigami.Theme.neutralTextColor
        return Kirigami.Theme.highlightColor
    }

    // Top bar
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
                text: "Accounts"
                color: Kirigami.Theme.textColor
                font.pixelSize: 13
                font.bold: true
            }

            Text {
                text: "Switch live CLI credentials on the fly"
                color: modal.mutedColor
                font.pixelSize: 9
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        QQC2.ToolButton {
            icon.name: "view-refresh"
            enabled: !modal.busy
            implicitWidth: 22
            implicitHeight: 22
            padding: 2
            onClicked: modal.refreshRequested()
            QQC2.ToolTip.text: "Refresh accounts & usage"
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
        }
    }

    // Provider selector pills
    Flow {
        Layout.fillWidth: true
        spacing: 4

        Repeater {
            model: modal.supportedProviders

            delegate: Rectangle {
                required property var modelData
                readonly property var pMeta: F.meta(modelData.id)
                readonly property bool selected: modal.currentProvider && modal.currentProvider.id === modelData.id
                readonly property int accCount: modelData.accounts ? modelData.accounts.length : 0

                implicitWidth: pillRow.implicitWidth + 12
                implicitHeight: 22
                radius: 4
                color: selected
                    ? Qt.rgba(pMeta.color.r, pMeta.color.g, pMeta.color.b, 0.22)
                    : (pillHover.hovered ? modal.cardBg : "transparent")
                border.width: 1
                border.color: selected
                    ? Qt.rgba(pMeta.color.r, pMeta.color.g, pMeta.color.b, 0.55)
                    : modal.cardBorder

                RowLayout {
                    id: pillRow
                    anchors.centerIn: parent
                    spacing: 4

                    Text {
                        text: pMeta.short
                        color: selected ? pMeta.color : modal.mutedColor
                        font.pixelSize: 9
                        font.bold: true
                    }

                    Text {
                        text: pMeta.name
                        color: selected ? Kirigami.Theme.textColor : modal.mutedColor
                        font.pixelSize: 9
                        font.bold: selected
                    }

                    Rectangle {
                        visible: accCount > 0
                        radius: 6
                        implicitWidth: Math.max(13, cntLbl.implicitWidth + 6)
                        implicitHeight: 13
                        color: Qt.rgba(pMeta.color.r, pMeta.color.g, pMeta.color.b, selected ? 0.30 : 0.14)

                        Text {
                            id: cntLbl
                            anchors.centerIn: parent
                            text: String(accCount)
                            color: selected ? Kirigami.Theme.textColor : pMeta.color
                            font.pixelSize: 8
                            font.bold: true
                        }
                    }
                }

                HoverHandler { id: pillHover }
                TapHandler { onTapped: modal.selectedProviderId = modelData.id }
            }
        }
    }

    // Status / Error banners
    Rectangle {
        Layout.fillWidth: true
        visible: modal.errorText.length > 0
        radius: 4
        implicitHeight: errText.implicitHeight + 8
        color: Qt.rgba(Kirigami.Theme.negativeTextColor.r, Kirigami.Theme.negativeTextColor.g, Kirigami.Theme.negativeTextColor.b, 0.14)

        Text {
            id: errText
            anchors.fill: parent
            anchors.margins: 4
            text: modal.errorText
            color: Kirigami.Theme.negativeTextColor
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }
    }

    Rectangle {
        Layout.fillWidth: true
        visible: modal.noticeText.length > 0 && modal.errorText.length === 0
        radius: 4
        implicitHeight: noteText.implicitHeight + 8
        color: Qt.rgba(Kirigami.Theme.positiveTextColor.r, Kirigami.Theme.positiveTextColor.g, Kirigami.Theme.positiveTextColor.b, 0.14)

        Text {
            id: noteText
            anchors.fill: parent
            anchors.margins: 4
            text: modal.noticeText
            color: Kirigami.Theme.positiveTextColor
            font.pixelSize: 10
            wrapMode: Text.WordWrap
        }
    }

    // Scrollable provider accounts + add section
    QQC2.ScrollView {
        id: scroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 8

            // Saved accounts header
            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Text {
                    text: modal.currentMeta.name + " accounts (" + modal.currentAccounts.length + ")"
                    color: Kirigami.Theme.textColor
                    font.pixelSize: 11
                    font.bold: true
                }

                Item { Layout.fillWidth: true }

                Text {
                    visible: modal.busy
                    text: "updating…"
                    color: modal.mutedColor
                    font.pixelSize: 9
                }
            }

            // Empty state
            Rectangle {
                Layout.fillWidth: true
                visible: modal.currentAccounts.length === 0
                radius: 6
                implicitHeight: emptyLabel.implicitHeight + 16
                color: modal.cardBg
                border.width: 1
                border.color: modal.cardBorder

                Text {
                    id: emptyLabel
                    anchors.fill: parent
                    anchors.margins: 8
                    text: "No " + modal.currentMeta.name + " account signed in yet. Add one below to sign in or store an API key."
                    color: modal.mutedColor
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }
            }

            // Account cards
            Repeater {
                model: modal.currentAccounts

                delegate: Rectangle {
                    id: accCard
                    required property var modelData
                    readonly property bool isActive: !!modelData.active
                    readonly property bool isLiveOnly: modelData.source === "live"
                    readonly property bool isEditing: modal.editingAccountId === modelData.id
                    readonly property bool isConfirmingRemove: modal.confirmRemoveId === modelData.id
                    readonly property var chips: F.accountPreviewChips(modelData, modal.nowMs)
                    readonly property string statusBadge: F.accountStatusBadge(modelData)

                    Layout.fillWidth: true
                    radius: 6
                    implicitHeight: cardCol.implicitHeight + 14
                    color: isActive
                        ? Qt.rgba(modal.currentMeta.color.r, modal.currentMeta.color.g, modal.currentMeta.color.b, 0.10)
                        : modal.cardBg
                    border.width: 1
                    border.color: isActive
                        ? Qt.rgba(modal.currentMeta.color.r, modal.currentMeta.color.g, modal.currentMeta.color.b, 0.45)
                        : modal.cardBorder

                    ColumnLayout {
                        id: cardCol
                        anchors.fill: parent
                        anchors.margins: 7
                        spacing: 4

                        // Top row: dot + label + badges + action buttons
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 5

                            Rectangle {
                                implicitWidth: 7
                                implicitHeight: 7
                                radius: 4
                                color: accCard.isActive
                                    ? Kirigami.Theme.positiveTextColor
                                    : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.25)
                            }

                            Text {
                                visible: !accCard.isEditing
                                text: String(modelData.label || modal.currentMeta.name)
                                color: Kirigami.Theme.textColor
                                font.pixelSize: 11
                                font.bold: accCard.isActive
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }

                            QQC2.TextField {
                                id: renameField
                                visible: accCard.isEditing
                                Layout.fillWidth: true
                                implicitHeight: 24
                                font.pixelSize: 10
                                text: String(modelData.label || "")
                                onAccepted: {
                                    if (text.trim().length > 0 && modal.currentProvider) {
                                        modal.renameAccountRequested(modal.currentProvider.id, modelData.id, text.trim())
                                        modal.editingAccountId = ""
                                    }
                                }
                            }

                            QQC2.ToolButton {
                                visible: accCard.isEditing
                                text: "Save"
                                implicitHeight: 22
                                font.pixelSize: 9
                                enabled: !modal.busy && renameField.text.trim().length > 0
                                onClicked: {
                                    if (modal.currentProvider) {
                                        modal.renameAccountRequested(modal.currentProvider.id, modelData.id, renameField.text.trim())
                                        modal.editingAccountId = ""
                                    }
                                }
                            }

                            QQC2.ToolButton {
                                visible: accCard.isEditing
                                text: "Cancel"
                                implicitHeight: 22
                                font.pixelSize: 9
                                onClicked: modal.editingAccountId = ""
                            }

                            Rectangle {
                                visible: !accCard.isEditing && accCard.isActive
                                radius: 3
                                implicitWidth: activeTag.implicitWidth + 8
                                implicitHeight: 15
                                color: Qt.rgba(Kirigami.Theme.positiveTextColor.r, Kirigami.Theme.positiveTextColor.g, Kirigami.Theme.positiveTextColor.b, 0.18)

                                Text {
                                    id: activeTag
                                    anchors.centerIn: parent
                                    text: "Active"
                                    color: Kirigami.Theme.positiveTextColor
                                    font.pixelSize: 8
                                    font.bold: true
                                }
                            }

                            QQC2.ToolButton {
                                id: switchBtn
                                visible: !accCard.isEditing && !accCard.isActive
                                enabled: !modal.busy
                                implicitHeight: 20
                                leftPadding: 7
                                rightPadding: 7
                                topPadding: 1
                                bottomPadding: 1
                                onClicked: {
                                    if (modal.currentProvider) {
                                        modal.switchAccountRequested(modal.currentProvider.id, modelData.id)
                                    }
                                }

                                contentItem: Text {
                                    text: "Switch"
                                    color: Kirigami.Theme.highlightColor
                                    font.pixelSize: 9
                                    font.bold: true
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    radius: 4
                                    color: switchBtn.hovered
                                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                                        : Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.12)
                                }
                            }

                            QQC2.ToolButton {
                                id: saveVaultBtn
                                visible: !accCard.isEditing && accCard.isLiveOnly
                                enabled: !modal.busy
                                implicitHeight: 20
                                leftPadding: 6
                                rightPadding: 6
                                topPadding: 1
                                bottomPadding: 1
                                onClicked: {
                                    if (modal.currentProvider) {
                                        modal.snapshotAccountRequested(modal.currentProvider.id, "")
                                    }
                                }

                                contentItem: Text {
                                    text: "Save"
                                    color: Kirigami.Theme.textColor
                                    font.pixelSize: 9
                                    font.bold: true
                                    verticalAlignment: Text.AlignVCenter
                                }

                                background: Rectangle {
                                    radius: 4
                                    color: saveVaultBtn.hovered
                                        ? Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.16)
                                        : Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g, Kirigami.Theme.textColor.b, 0.08)
                                }

                                QQC2.ToolTip.text: "Save active CLI login into Oasis vault"
                                QQC2.ToolTip.visible: hovered
                                QQC2.ToolTip.delay: 400
                            }

                            QQC2.ToolButton {
                                visible: !accCard.isEditing
                                enabled: !modal.busy
                                icon.name: "edit-rename"
                                implicitWidth: 20
                                implicitHeight: 20
                                padding: 2
                                onClicked: {
                                    modal.confirmRemoveId = ""
                                    renameField.text = String(modelData.label || "")
                                    modal.editingAccountId = modelData.id
                                    renameField.forceActiveFocus()
                                }
                                QQC2.ToolTip.text: "Rename account"
                                QQC2.ToolTip.visible: hovered
                                QQC2.ToolTip.delay: 400
                            }

                            QQC2.ToolButton {
                                visible: !accCard.isEditing && !accCard.isLiveOnly && (!accCard.isActive || modelData.source === "vault")
                                enabled: !modal.busy
                                icon.name: accCard.isConfirmingRemove ? "" : "edit-delete"
                                text: accCard.isConfirmingRemove ? "Remove?" : ""
                                display: accCard.isConfirmingRemove ? QQC2.AbstractButton.TextOnly : QQC2.AbstractButton.IconOnly
                                implicitWidth: accCard.isConfirmingRemove ? 52 : 20
                                implicitHeight: 20
                                padding: 2
                                font.pixelSize: 9
                                onClicked: {
                                    if (!accCard.isConfirmingRemove) {
                                        modal.confirmRemoveId = modelData.id
                                    } else if (modal.currentProvider) {
                                        modal.confirmRemoveId = ""
                                        modal.removeAccountRequested(modal.currentProvider.id, modelData.id)
                                    }
                                }
                                QQC2.ToolTip.text: accCard.isConfirmingRemove
                                    ? "Click again to remove from Oasis"
                                    : "Remove saved account"
                                QQC2.ToolTip.visible: hovered
                                QQC2.ToolTip.delay: 400
                            }
                        }

                        // Metadata + live usage preview chips
                        Flow {
                            Layout.fillWidth: true
                            Layout.leftMargin: 12
                            spacing: 5

                            Text {
                                visible: modelData.plan ? String(modelData.plan).length > 0 : false
                                text: {
                                    var p = String(modelData.plan || "")
                                    return p.length > 0 ? p.charAt(0).toUpperCase() + p.slice(1) : ""
                                }
                                color: modal.mutedColor
                                font.pixelSize: 9
                            }

                            Text {
                                visible: modelData.kind === "api_key"
                                text: modelData.region ? "API key · " + modelData.region : "API key"
                                color: modal.mutedColor
                                font.pixelSize: 9
                            }

                            Text {
                                visible: accCard.isLiveOnly
                                text: "unsaved live session"
                                color: Kirigami.Theme.neutralTextColor
                                font.pixelSize: 9
                            }

                            Repeater {
                                model: accCard.chips

                                delegate: Rectangle {
                                    required property var modelData
                                    readonly property color tone: modal.chipColor(modelData)
                                    radius: 3
                                    implicitWidth: chipTxt.implicitWidth + 8
                                    implicitHeight: 15
                                    color: Qt.rgba(tone.r, tone.g, tone.b, 0.15)

                                    Text {
                                        id: chipTxt
                                        anchors.centerIn: parent
                                        text: modelData.reset && modelData.reset !== "ready"
                                            ? modelData.text + " " + modelData.reset
                                            : modelData.text
                                        color: tone
                                        font.pixelSize: 9
                                        font.bold: true
                                    }
                                }
                            }

                            Text {
                                visible: accCard.chips.length === 0 && accCard.statusBadge.length > 0
                                text: accCard.statusBadge
                                color: modelData.status === "error"
                                    ? Kirigami.Theme.negativeTextColor
                                    : modal.mutedColor
                                font.pixelSize: 9
                            }
                        }
                    }
                }
            }

            // Add new account card
            Rectangle {
                Layout.fillWidth: true
                visible: modal.currentProvider !== null
                radius: 6
                implicitHeight: addCol.implicitHeight + 16
                color: modal.cardBg
                border.width: 1
                border.color: modal.cardBorder

                ColumnLayout {
                    id: addCol
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6

                    Text {
                        text: "Add " + modal.currentMeta.name + " account"
                        color: Kirigami.Theme.textColor
                        font.pixelSize: 11
                        font.bold: true
                    }

                    QQC2.TextField {
                        id: labelInput
                        Layout.fillWidth: true
                        implicitHeight: 26
                        font.pixelSize: 10
                        placeholderText: "Label (optional — auto-detects email if blank)"
                    }

                    // Native CLI login + Snapshot current buttons
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        QQC2.Button {
                            visible: !!modal.currentCaps.supports_login
                            enabled: !modal.busy
                            Layout.fillWidth: true
                            implicitHeight: 26
                            text: "Sign in (" + (modal.currentCaps.login_hint || "CLI") + ")"
                            font.pixelSize: 10
                            onClicked: {
                                if (modal.currentProvider) {
                                    modal.launchLoginRequested(modal.currentProvider.id, labelInput.text.trim())
                                    labelInput.text = ""
                                }
                            }
                        }

                        QQC2.Button {
                            visible: modal.currentProvider && modal.currentProvider.id !== "opencodego" && modal.currentProvider.status !== "not_configured"
                            enabled: !modal.busy
                            implicitHeight: 26
                            text: "Save active"
                            font.pixelSize: 10
                            onClicked: {
                                if (modal.currentProvider) {
                                    modal.snapshotAccountRequested(modal.currentProvider.id, labelInput.text.trim())
                                    labelInput.text = ""
                                }
                            }
                            QQC2.ToolTip.text: "Snapshot the currently signed-in CLI account into Oasis"
                            QQC2.ToolTip.visible: hovered
                            QQC2.ToolTip.delay: 400
                        }
                    }

                    Text {
                        visible: !!modal.currentCaps.supports_login
                        Layout.fillWidth: true
                        text: "Saves your current account first, opens a terminal for native CLI login, and switches automatically."
                        color: modal.mutedColor
                        font.pixelSize: 9
                        wrapMode: Text.WordWrap
                    }

                    // API key section (for OpenCode Go, Qwen, Kimi)
                    Rectangle {
                        visible: !!modal.currentCaps.supports_api_key
                        Layout.fillWidth: true
                        implicitHeight: 1
                        color: modal.cardBorder
                    }

                    ColumnLayout {
                        visible: !!modal.currentCaps.supports_api_key
                        Layout.fillWidth: true
                        spacing: 5

                        Text {
                            text: "Or add API key securely (never passed via shell argv)"
                            color: modal.mutedColor
                            font.pixelSize: 9
                            font.bold: true
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            RowLayout {
                                visible: modal.currentCaps.regions && modal.currentCaps.regions.length > 0
                                spacing: 4

                                Repeater {
                                    model: modal.currentCaps.regions || []

                                    delegate: Rectangle {
                                        required property string modelData
                                        readonly property bool activeReg: modal.selectedRegion === modelData
                                        implicitWidth: regTxt.implicitWidth + 10
                                        implicitHeight: 20
                                        radius: 4
                                        color: activeReg
                                            ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                                            : "transparent"
                                        border.width: 1
                                        border.color: activeReg ? Kirigami.Theme.highlightColor : modal.cardBorder

                                        Text {
                                            id: regTxt
                                            anchors.centerIn: parent
                                            text: modelData
                                            color: activeReg ? Kirigami.Theme.textColor : modal.mutedColor
                                            font.pixelSize: 9
                                            font.bold: activeReg
                                        }

                                        TapHandler { onTapped: modal.selectedRegion = modelData }
                                    }
                                }
                            }

                            Item { Layout.fillWidth: true }

                            QQC2.Button {
                                enabled: !modal.busy
                                text: "Enter API Key…"
                                font.pixelSize: 10
                                Accessible.name: "Enter API key securely via password dialog"
                                onClicked: {
                                    if (modal.currentProvider) {
                                        modal.addApiKeyPromptRequested(
                                            modal.currentProvider.id,
                                            labelInput.text.trim(),
                                            modal.selectedRegion
                                        )
                                        labelInput.text = ""
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
