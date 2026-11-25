import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami

Kirigami.FormLayout {
    id: page

    property alias cfg_refreshSeconds: refresh.value
    property alias cfg_showNames: showNames.checked
    property alias cfg_showCountdown: showCountdown.checked
    property alias cfg_hideUnconfigured: hideUnconfigured.checked
    property alias cfg_notifyOnLimit: notifyOnLimit.checked
    property alias cfg_panelSlots: panelSlots.value
    property alias cfg_warnThreshold: warn.value
    property alias cfg_criticalThreshold: critical.value
    property alias cfg_helperPath: helperPath.text

    QQC2.SpinBox {
        id: refresh
        from: 30
        to: 3600
        stepSize: 30
        editable: true
        Kirigami.FormData.label: "Refresh interval (seconds):"
    }

    Item {
        Kirigami.FormData.isSection: true
    }

    QQC2.CheckBox {
        id: showNames
        text: "Show provider names in the panel"
        Kirigami.FormData.label: "Panel:"
    }

    QQC2.CheckBox {
        id: showCountdown
        text: "Show reset countdown (↑2h 13m)"
    }

    QQC2.CheckBox {
        id: hideUnconfigured
        text: "Hide providers that are not signed in"
    }

    QQC2.SpinBox {
        id: panelSlots
        from: 1
        to: 9
        editable: true
        Kirigami.FormData.label: "Vertical panel slots:"
    }

    Item {
        Kirigami.FormData.isSection: true
    }

    QQC2.CheckBox {
        id: notifyOnLimit
        text: "Notify when a usage window reaches 100%"
        Kirigami.FormData.label: "Notifications:"
    }

    QQC2.Label {
        Layout.fillWidth: true
        Layout.maximumWidth: Kirigami.Units.gridUnit * 26
        text: "One desktop notification per limit. It re-arms after that window resets. The first fetch after the widget starts is a silent baseline, so restarting Plasma does not repeat old alerts."
        wrapMode: Text.WordWrap
        font: Kirigami.Theme.smallFont
        opacity: 0.7
    }

    Item {
        Kirigami.FormData.isSection: true
    }

    QQC2.SpinBox {
        id: warn
        from: 50
        to: 99
        editable: true
        Kirigami.FormData.label: "Warning at (% used):"
    }

    QQC2.SpinBox {
        id: critical
        from: 51
        to: 100
        editable: true
        Kirigami.FormData.label: "Critical at (% used):"
    }

    Item {
        Kirigami.FormData.isSection: true
    }

    QQC2.TextField {
        id: helperPath
        Layout.fillWidth: true
        placeholderText: "bundled helper"
        Kirigami.FormData.label: "Helper path:"
    }

    QQC2.Label {
        Layout.fillWidth: true
        Layout.maximumWidth: Kirigami.Units.gridUnit * 26
        text: "Leave blank to use the helper shipped inside the widget. The helper only reads local sign-in files and calls each provider's first-party usage endpoint."
        wrapMode: Text.WordWrap
        font: Kirigami.Theme.smallFont
        opacity: 0.7
    }
}
