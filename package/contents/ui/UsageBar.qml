import QtQuick
import org.kde.kirigami as Kirigami

Rectangle {
    id: bar

    property real value: 0
    property color tone: Kirigami.Theme.highlightColor
    property bool estimated: false

    implicitWidth: 40
    implicitHeight: 6
    radius: height / 2
    color: Qt.rgba(Kirigami.Theme.textColor.r,
                   Kirigami.Theme.textColor.g,
                   Kirigami.Theme.textColor.b, 0.15)

    Rectangle {
        width: Math.max(0, Math.min(1, bar.value / 100)) * parent.width
        height: parent.height
        radius: parent.radius
        color: bar.tone
        opacity: bar.estimated ? 0.55 : 1
    }
}
