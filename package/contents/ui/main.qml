import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as Plasma5Support
import "../code/format.js" as F

PlasmoidItem {
    id: root

    property var payload: ({ "providers": [] })
    property bool loading: false
    property string lastError: ""
    property double lastSuccess: 0
    property double nowMs: Date.now()
    property var notifiedLimits: ({})
    property bool limitNotificationBaselineDone: false
    property var intelReport: null
    property bool intelLoading: false
    property string intelError: ""
    property bool accountBusy: false
    property string accountError: ""
    property string accountNotice: ""
    property string pendingAccountAction: ""
    property bool refreshQueued: false

    readonly property var providers: payload && payload.providers ? payload.providers : []

    Plasmoid.icon: Qt.resolvedUrl("../icons/ai-limits.svg").toString()
    toolTipMainText: "Oasis"
    toolTipSubText: root.tooltipText()

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: "Quit Oasis"
            icon.name: "application-exit"
            onTriggered: root.quit()
        }
    ]

    compactRepresentation: CompactRepresentation {
        providers: root.panelProviders()
        nowMs: root.nowMs
        showNames: Plasmoid.configuration.showNames
        showCountdown: Plasmoid.configuration.showCountdown
        panelSlots: Plasmoid.configuration.panelSlots
        warnThreshold: Plasmoid.configuration.warnThreshold
        criticalThreshold: Plasmoid.configuration.criticalThreshold
        busy: root.loading
        failed: root.lastError.length > 0
        onActivated: root.expanded = !root.expanded
        onRefreshRequested: root.refresh()
    }

    fullRepresentation: FullRepresentation {
        providers: root.providers
        nowMs: root.nowMs
        busy: root.loading
        errorText: root.lastError
        lastSuccess: root.lastSuccess
        warnThreshold: Plasmoid.configuration.warnThreshold
        criticalThreshold: Plasmoid.configuration.criticalThreshold
        intelReport: root.intelReport
        intelBusy: root.intelLoading
        intelError: root.intelError
        accountBusy: root.accountBusy
        accountError: root.accountError
        accountNotice: root.accountNotice
        onRefreshRequested: root.refresh()
        onQuitRequested: root.quit()
        onCloseRequested: root.expanded = false
        onIntelLoadRequested: root.loadIntel()
        onIntelScanRequested: root.scanIntel()
        onSwitchAccountRequested: function(p, a) { root.switchAccount(p, a) }
        onSnapshotAccountRequested: function(p, l) { root.snapshotAccount(p, l) }
        onLaunchLoginRequested: function(p, l) { root.launchAccountLogin(p, l) }
        onAddApiKeyRequested: function(p, l, k, r) { root.addApiKeyAccount(p, l, k, r) }
        onRenameAccountRequested: function(p, a, l) { root.renameAccount(p, a, l) }
        onRemoveAccountRequested: function(p, a) { root.removeAccount(p, a) }
        onClearAccountMessagesRequested: {
            root.accountError = ""
            root.accountNotice = ""
        }
    }

    Plasma5Support.DataSource {
        id: executable
        engine: "executable"
        onNewData: function(sourceName, data) {
            if (executable.connectedSources.indexOf(sourceName) === -1) {
                return
            }
            executable.disconnectSource(sourceName)
            root.loading = false

            var output = data.stdout || ""
            if (output.length === 0) {
                var code = data["exit code"]
                var detail = (data.stderr || "").trim()
                root.lastError = code
                    ? "helper failed (exit " + code + ")" + (detail ? ": " + detail : "")
                    : "helper returned no data"
                if (root.refreshQueued) {
                    root.refreshQueued = false
                    root.refresh()
                }
                return
            }
            try {
                var document = JSON.parse(output)
                if (!document || !document.providers) {
                    throw new Error("missing providers")
                }
                var now = Date.now()
                root.payload = document
                root.lastSuccess = now
                root.nowMs = now
                root.lastError = ""
                root.checkLimitNotifications(document.providers || [])
            } catch (error) {
                root.lastError = "invalid helper output"
            }
            if (root.refreshQueued) {
                root.refreshQueued = false
                root.refresh()
            }
        }
    }

    // Local desktop notification transport. The widget has no daemon of its
    // own; libnotify's notify-send exits immediately after handing the
    // notification to Plasma.
    Plasma5Support.DataSource {
        id: notifier
        engine: "executable"
        onNewData: function(sourceName, data) {
            if (notifier.connectedSources.indexOf(sourceName) === -1) {
                return
            }
            notifier.disconnectSource(sourceName)
        }
    }

    // Used by the Quit action to remove this applet from the panel. The
    // launcher entry runs the same helper with "launch" to add it back.
    Plasma5Support.DataSource {
        id: control
        engine: "executable"
        onNewData: function(sourceName, data) {
            if (control.connectedSources.indexOf(sourceName) === -1) {
                return
            }
            control.disconnectSource(sourceName)
            if (data["exit code"]) {
                var detail = (data.stderr || "").trim()
                root.lastError = detail.length > 0
                    ? "could not quit: " + detail
                    : "could not quit"
            }
        }
    }

    // Integrated AI Intel runner (cached read + on-demand pi + Tavily web scan).
    Plasma5Support.DataSource {
        id: intelSource
        engine: "executable"
        onNewData: function(sourceName, data) {
            if (intelSource.connectedSources.indexOf(sourceName) === -1) {
                return
            }
            intelSource.disconnectSource(sourceName)
            root.intelLoading = false
            var output = data.stdout || ""
            if (output.length === 0) {
                var detail = (data.stderr || "").trim()
                root.intelError = detail.length > 0 ? detail : "intel helper returned no data"
                return
            }
            try {
                var doc = JSON.parse(output)
                root.intelReport = doc
                root.intelError = doc && doc.error ? String(doc.error) : ""
                root.nowMs = Date.now()
            } catch (error) {
                root.intelError = "invalid intel output"
            }
        }
    }

    // Multi-account manager runner (switch, snapshot, add-key, launch-login, rename, remove).
    Plasma5Support.DataSource {
        id: accountSource
        engine: "executable"
        onNewData: function(sourceName, data) {
            if (accountSource.connectedSources.indexOf(sourceName) === -1) {
                return
            }
            accountSource.disconnectSource(sourceName)
            var action = root.pendingAccountAction
            root.pendingAccountAction = ""
            root.accountBusy = false

            var output = (data.stdout || "").trim()
            var detail = (data.stderr || "").trim()
            if (output.length === 0) {
                root.accountError = detail.length > 0 ? detail : "account helper returned no data"
                return
            }
            try {
                var doc = JSON.parse(output)
                if (!doc || doc.ok === false) {
                    root.accountError = doc && doc.error ? String(doc.error) : "account action failed"
                    return
                }
                root.accountError = ""
                var pName = doc.provider ? F.meta(doc.provider).name : ""
                var lbl = doc.label ? String(doc.label) : ""
                if (action === "switch") {
                    root.accountNotice = "Switched " + pName + " → " + lbl + " (CLI updated)"
                    root.clearLimitStateForProvider(doc.provider)
                    root.refresh()
                } else if (action === "add-key") {
                    root.accountNotice = "Added & activated " + pName + " → " + lbl
                    root.clearLimitStateForProvider(doc.provider)
                    root.refresh()
                } else if (action === "snapshot") {
                    root.accountNotice = "Saved " + pName + " account: " + lbl
                    root.refresh()
                } else if (action === "launch-login") {
                    root.accountNotice = doc.message
                        ? String(doc.message) + " Refresh once finished."
                        : "Opened terminal for login. Refresh once finished."
                } else if (action === "rename") {
                    root.accountNotice = "Renamed account to " + lbl
                    root.refresh()
                } else if (action === "remove") {
                    root.accountNotice = "Removed saved " + pName + " account"
                    root.refresh()
                } else {
                    root.refresh()
                }
                noticeTimer.restart()
            } catch (error) {
                root.accountError = detail.length > 0 ? detail : "invalid account helper output"
            }
        }
    }

    Timer {
        interval: 20000
        running: root.accountBusy
        onTriggered: {
            accountSource.connectedSources = []
            root.accountBusy = false
            root.pendingAccountAction = ""
            root.accountError = "account action timed out"
        }
    }

    Timer {
        id: noticeTimer
        interval: 5500
        repeat: false
        onTriggered: root.accountNotice = ""
    }

    Timer {
        interval: 320000
        running: root.intelLoading
        onTriggered: {
            intelSource.connectedSources = []
            root.intelLoading = false
            root.intelError = "intel scan timed out"
        }
    }

    Timer {
        interval: Math.max(30, Plasmoid.configuration.refreshSeconds || 300) * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    // Keeps countdowns live without refetching.
    Timer {
        interval: 60000
        running: true
        repeat: true
        onTriggered: root.nowMs = Date.now()
    }

    // A stuck helper must not leave `loading` true forever (which would also
    // stop all future refreshes and leak one DataSource entry per attempt).
    Timer {
        interval: 30000
        running: root.loading
        onTriggered: {
            executable.connectedSources = []
            root.loading = false
            root.lastError = "helper timed out"
        }
    }

    function localFilePath(url) {
        url = String(url)
        if (url.indexOf("file://") === 0) {
            url = url.substring(7)
        }
        try {
            return decodeURIComponent(url)
        } catch (error) {
            return url
        }
    }

    function helperPath() {
        var configured = String(Plasmoid.configuration.helperPath || "").trim()
        if (configured.length > 0) {
            return configured
        }
        return localFilePath(Qt.resolvedUrl("../code/ai_limits.py"))
    }

    function controlPath() {
        return localFilePath(Qt.resolvedUrl("../code/oasisctl.py"))
    }

    function intelPath() {
        return localFilePath(Qt.resolvedUrl("../code/oasis_intel.py"))
    }

    function accountsPath() {
        return localFilePath(Qt.resolvedUrl("../code/oasis_accounts.py"))
    }

    function clearLimitStateForProvider(providerId) {
        if (!providerId) {
            return
        }
        var next = {}
        var prefix = String(providerId) + ":"
        for (var key in root.notifiedLimits) {
            if (key.indexOf(prefix) !== 0) {
                next[key] = root.notifiedLimits[key]
            }
        }
        root.notifiedLimits = next
    }

    function runAccountCommand(action, commandLine) {
        if (root.accountBusy) {
            return
        }
        root.accountBusy = true
        root.accountError = ""
        root.accountNotice = ""
        root.pendingAccountAction = action
        accountSource.connectedSources = []
        accountSource.connectSource(commandLine)
    }

    function switchAccount(providerId, accountId) {
        var safePath = root.accountsPath().replace(/['\n\r]/g, "")
        runAccountCommand(
            "switch",
            "python3 '" + safePath + "' switch --provider "
                + shellQuote(providerId) + " --account " + shellQuote(accountId))
    }

    function snapshotAccount(providerId, label) {
        var safePath = root.accountsPath().replace(/['\n\r]/g, "")
        var cmd = "python3 '" + safePath + "' snapshot --provider " + shellQuote(providerId)
        if (label && label.length > 0) {
            cmd += " --label " + shellQuote(label)
        }
        runAccountCommand("snapshot", cmd)
    }

    function launchAccountLogin(providerId, label) {
        var safePath = root.accountsPath().replace(/['\n\r]/g, "")
        var cmd = "python3 '" + safePath + "' launch-login --provider " + shellQuote(providerId)
        if (label && label.length > 0) {
            cmd += " --label " + shellQuote(label)
        }
        runAccountCommand("launch-login", cmd)
    }

    function addApiKeyAccount(providerId, label, apiKey, region) {
        var safePath = root.accountsPath().replace(/['\n\r]/g, "")
        var b64 = Qt.btoa(unescape(encodeURIComponent(String(apiKey || ""))))
        var cmd = "printf '%s' " + shellQuote(b64) + " | base64 -d | python3 '" + safePath
            + "' add-key --provider " + shellQuote(providerId)
        if (label && label.length > 0) {
            cmd += " --label " + shellQuote(label)
        }
        if (region && region.length > 0) {
            cmd += " --region " + shellQuote(region)
        }
        runAccountCommand("add-key", cmd)
    }

    function renameAccount(providerId, accountId, newLabel) {
        var safePath = root.accountsPath().replace(/['\n\r]/g, "")
        runAccountCommand(
            "rename",
            "python3 '" + safePath + "' rename --provider "
                + shellQuote(providerId) + " --account " + shellQuote(accountId)
                + " --label " + shellQuote(newLabel))
    }

    function removeAccount(providerId, accountId) {
        var safePath = root.accountsPath().replace(/['\n\r]/g, "")
        runAccountCommand(
            "remove",
            "python3 '" + safePath + "' remove --provider "
                + shellQuote(providerId) + " --account " + shellQuote(accountId))
    }

    function loadIntel() {
        if (root.intelLoading) {
            return
        }
        var safePath = root.intelPath().replace(/['\n\r]/g, "")
        intelSource.connectedSources = []
        intelSource.connectSource("python3 '" + safePath + "' read")
    }

    function scanIntel() {
        if (root.intelLoading) {
            return
        }
        root.intelLoading = true
        root.intelError = ""
        var safePath = root.intelPath().replace(/['\n\r]/g, "")
        intelSource.connectedSources = []
        intelSource.connectSource("python3 '" + safePath + "' scan")
    }

    function quit() {
        var safePath = root.controlPath().replace(/['\n\r]/g, "")
        control.connectedSources = []
        control.connectSource("python3 '" + safePath + "' quit")
    }

    function shellQuote(value) {
        return "'" + String(value).replace(/'/g, "'\\''") + "'"
    }

    function sendLimitNotification(lines) {
        if (Plasmoid.configuration.notifyOnLimit === false || lines.length === 0) {
            return
        }
        var title = "Heads up"
        var body = lines.join("; ")
        notifier.connectedSources = []
        notifier.connectSource(
            "notify-send -a " + shellQuote("Oasis")
            + " -i " + shellQuote("dialog-warning")
            + " -u normal -t 15000 "
            + shellQuote(title) + " " + shellQuote(body))
    }

    function checkLimitNotifications(providers) {
        providers = providers || root.providers
        var result = F.limitNotificationLines(
            providers, root.notifiedLimits, root.nowMs)
        var baseline = !root.limitNotificationBaselineDone
        root.limitNotificationBaselineDone = true
        root.notifiedLimits = result.next
        if (!baseline) {
            root.sendLimitNotification(result.lines)
        }
    }

    function refresh() {
        if (root.loading) {
            root.refreshQueued = true
            return
        }
        root.loading = true
        root.lastError = ""
        executable.connectedSources = []
        // Single quotes keep the path inert if the engine uses a shell.
        var safePath = root.helperPath().replace(/['\n\r]/g, "")
        executable.connectSource("python3 '" + safePath + "' fetch")
    }

    function panelVisible(provider) {
        if (!provider) {
            return false
        }
        if (provider.status === "not_configured") {
            return Plasmoid.configuration.hideUnconfigured === false
        }
        // An active login with no quota windows (Muse while the 5h window is
        // idle) would render as a meaningless 0 in the panel.
        if (provider.status === "ok" && (!provider.windows || provider.windows.length === 0)) {
            return false
        }
        return true
    }

    function panelProviders() {
        var result = []
        for (var i = 0; i < root.providers.length; i++) {
            if (root.panelVisible(root.providers[i])) {
                result.push(root.providers[i])
            }
        }
        return result
    }

    function tooltipText() {
        if (root.lastError.length > 0) {
            return root.lastError
        }
        if (root.providers.length === 0) {
            return "No providers configured"
        }
        var lines = []
        for (var i = 0; i < root.providers.length; i++) {
            var provider = root.providers[i]
            var name = F.meta(provider.id).name
            if (provider.account && provider.accounts && provider.accounts.length > 1) {
                name += " (" + F.shortAccountLabel(provider.account) + ")"
            }
            if (provider.status !== "ok") {
                lines.push(name + " · " + F.statusText(provider))
                continue
            }
            var windows = F.primaryWindows(provider)
            var parts = []
            for (var j = 0; j < windows.length; j++) {
                var window = windows[j]
                var text = window.short + " " + F.round(window.used) + "%"
                var reset = F.resetText(window, root.nowMs)
                if (reset.length > 0) {
                    text += " · " + reset
                }
                parts.push(text)
            }
            var subText = F.subscriptionText(provider, root.nowMs)
            if (subText.length > 0) {
                parts.push("sub " + subText)
            }
            var resetBadge = F.resetBadgeText(provider, root.nowMs)
            if (resetBadge.length > 0) {
                parts.push(resetBadge)
            }
            lines.push(name + "  " + parts.join("   "))
        }
        return lines.join("\n")
    }
}
