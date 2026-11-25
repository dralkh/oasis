import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "../code/format.js" as F

Rectangle {
    id: graph

    property var mainModels: []
    property var subModels: []
    property string filterMode: "all" // "all" | "main" | "sub"

    readonly property var chart: F.buildTierChartData(mainModels, subModels, filterMode)
    readonly property color mutedColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                                Kirigami.Theme.textColor.g,
                                                Kirigami.Theme.textColor.b, 0.65)
    readonly property color gridColor: Qt.rgba(Kirigami.Theme.textColor.r,
                                               Kirigami.Theme.textColor.g,
                                               Kirigami.Theme.textColor.b, 0.12)
    readonly property color frontierColor: Qt.rgba(Kirigami.Theme.highlightColor.r,
                                                   Kirigami.Theme.highlightColor.g,
                                                   Kirigami.Theme.highlightColor.b, 0.55)

    readonly property int padLeft: 26
    readonly property int padRight: 12
    readonly property int padTop: 12
    readonly property int padBottom: 20

    visible: chart.points && chart.points.length > 0
    Layout.fillWidth: true
    implicitHeight: bodyCol.implicitHeight + 14
    radius: 6
    color: Qt.rgba(Kirigami.Theme.textColor.r,
                   Kirigami.Theme.textColor.g,
                   Kirigami.Theme.textColor.b, 0.04)
    border.width: 1
    border.color: Qt.rgba(Kirigami.Theme.textColor.r,
                          Kirigami.Theme.textColor.g,
                          Kirigami.Theme.textColor.b, 0.11)

    onChartChanged: canvas.requestPaint()

    ColumnLayout {
        id: bodyCol
        anchors.fill: parent
        anchors.margins: 7
        spacing: 4

        // Header + All / Main / Sub filter
        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                text: "CAPABILITY vs. COST / TASK"
                color: Kirigami.Theme.textColor
                font.pixelSize: 9
                font.bold: true
                Layout.fillWidth: true
            }

            Repeater {
                model: [
                    { id: "all", label: "All" },
                    { id: "main", label: "Main" },
                    { id: "sub", label: "Sub" }
                ]

                delegate: Rectangle {
                    required property var modelData
                    readonly property bool active: graph.filterMode === modelData.id
                    implicitWidth: flbl.implicitWidth + 10
                    implicitHeight: 16
                    radius: 3
                    color: active
                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.22)
                        : "transparent"
                    border.width: 1
                    border.color: active
                        ? Qt.rgba(Kirigami.Theme.highlightColor.r, Kirigami.Theme.highlightColor.g, Kirigami.Theme.highlightColor.b, 0.45)
                        : graph.gridColor

                    Text {
                        id: flbl
                        anchors.centerIn: parent
                        text: modelData.label
                        color: active ? Kirigami.Theme.highlightColor : graph.mutedColor
                        font.pixelSize: 8
                        font.bold: active
                    }

                    TapHandler { onTapped: graph.filterMode = modelData.id }
                }
            }
        }

        // Plot Area
        Item {
            id: plotArea
            Layout.fillWidth: true
            implicitHeight: 162

            readonly property real plotW: Math.max(40, width - graph.padLeft - graph.padRight)
            readonly property real plotH: Math.max(40, height - graph.padTop - graph.padBottom)

            onWidthChanged: canvas.requestPaint()
            onHeightChanged: canvas.requestPaint()

            Canvas {
                id: canvas
                anchors.fill: parent

                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)

                    var left = graph.padLeft
                    var top = graph.padTop
                    var pw = plotArea.plotW
                    var ph = plotArea.plotH
                    var bottom = top + ph
                    var right = left + pw

                    // Horizontal gridlines
                    ctx.strokeStyle = graph.gridColor
                    ctx.lineWidth = 1
                    var yTicks = graph.chart.yTicks || []
                    for (var i = 0; i < yTicks.length; i++) {
                        var py = Math.round(bottom - yTicks[i].yNorm * ph) + 0.5
                        ctx.beginPath()
                        ctx.moveTo(left, py)
                        ctx.lineTo(right, py)
                        ctx.stroke()
                    }

                    // Vertical log-cost gridlines
                    var xTicks = graph.chart.xTicks || []
                    for (var j = 0; j < xTicks.length; j++) {
                        var px = Math.round(left + xTicks[j].xNorm * pw) + 0.5
                        ctx.beginPath()
                        ctx.moveTo(px, top)
                        ctx.lineTo(px, bottom)
                        ctx.stroke()
                    }

                    // Subtle axes
                    ctx.strokeStyle = Qt.rgba(Kirigami.Theme.textColor.r,
                                              Kirigami.Theme.textColor.g,
                                              Kirigami.Theme.textColor.b, 0.22)
                    ctx.beginPath()
                    ctx.moveTo(left + 0.5, top)
                    ctx.lineTo(left + 0.5, bottom + 0.5)
                    ctx.lineTo(right, bottom + 0.5)
                    ctx.stroke()

                    // Pareto frontier dashed line
                    var frontier = graph.chart.frontier || []
                    if (frontier.length >= 2) {
                        ctx.save()
                        ctx.strokeStyle = graph.frontierColor
                        ctx.lineWidth = 1.5
                        if (ctx.setLineDash) ctx.setLineDash([4, 3])
                        ctx.beginPath()
                        for (var k = 0; k < frontier.length; k++) {
                            var fx = left + frontier[k].xNorm * pw
                            var fy = bottom - frontier[k].yNorm * ph
                            if (k === 0) ctx.moveTo(fx, fy)
                            else ctx.lineTo(fx, fy)
                        }
                        ctx.stroke()
                        ctx.restore()
                    }
                }
            }

            // Sweet-spot corner cue (top-left = high score, low cost)
            Text {
                x: graph.padLeft + 4
                y: graph.padTop + 2
                text: "↖ best value"
                color: Qt.rgba(Kirigami.Theme.positiveTextColor.r,
                               Kirigami.Theme.positiveTextColor.g,
                               Kirigami.Theme.positiveTextColor.b, 0.70)
                font.pixelSize: 8
                font.italic: true
            }

            // Y-axis tick labels
            Repeater {
                model: graph.chart.yTicks || []
                delegate: Text {
                    required property var modelData
                    x: 0
                    width: graph.padLeft - 4
                    y: graph.padTop + plotArea.plotH - modelData.yNorm * plotArea.plotH - height / 2
                    text: modelData.label
                    color: graph.mutedColor
                    font.pixelSize: 8
                    horizontalAlignment: Text.AlignRight
                }
            }

            // X-axis tick labels
            Repeater {
                model: graph.chart.xTicks || []
                delegate: Text {
                    required property var modelData
                    x: graph.padLeft + modelData.xNorm * plotArea.plotW - implicitWidth / 2
                    y: graph.padTop + plotArea.plotH + 3
                    text: modelData.label
                    color: graph.mutedColor
                    font.pixelSize: 8
                }
            }

            // Axis title bottom-right
            Text {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                text: "std $/task (log) →"
                color: graph.mutedColor
                font.pixelSize: 7
                opacity: 0.8
            }

            // Plotted model points + labels
            Repeater {
                model: graph.chart.points || []

                delegate: Item {
                    id: pt
                    required property var modelData
                    readonly property color dotColor: F.tierColor(modelData.tier)
                    readonly property real cx: graph.padLeft + modelData.xNorm * plotArea.plotW
                    readonly property real cy: graph.padTop + plotArea.plotH - modelData.yNorm * plotArea.plotH
                    readonly property bool isSub: modelData.role === "sub"

                    x: cx - 7
                    y: cy - 7
                    width: 14
                    height: 14
                    z: ptHover.hovered ? 20 : (modelData.onFrontier ? 5 : 2)

                    // Subtle hover halo
                    Rectangle {
                        anchors.centerIn: parent
                        width: 16
                        height: 16
                        radius: 8
                        visible: ptHover.hovered
                        color: Qt.rgba(pt.dotColor.r, pt.dotColor.g, pt.dotColor.b, 0.25)
                    }

                    // Marker: Circle for Main / Both, Diamond for Subagent
                    Rectangle {
                        anchors.centerIn: parent
                        width: ptHover.hovered ? 10 : 8
                        height: ptHover.hovered ? 10 : 8
                        radius: pt.isSub ? 1 : 5
                        rotation: pt.isSub ? 45 : 0
                        color: pt.isSub ? Kirigami.Theme.backgroundColor : pt.dotColor
                        border.width: pt.isSub ? 2 : (modelData.role === "both" ? 1.5 : 0)
                        border.color: pt.isSub ? pt.dotColor : Kirigami.Theme.textColor
                    }

                    // Non-overlapping short label
                    Text {
                        text: modelData.shortName
                        x: modelData.labelSide === "left" ? -implicitWidth - 1 : parent.width + 1
                        y: (parent.height - implicitHeight) / 2 + (modelData.labelDy || 0)
                        color: ptHover.hovered ? Kirigami.Theme.textColor : graph.mutedColor
                        font.pixelSize: 8
                        font.bold: ptHover.hovered || modelData.tier.toLowerCase() === "diamond"
                    }

                    HoverHandler { id: ptHover }

                    QQC2.ToolTip.visible: ptHover.hovered
                    QQC2.ToolTip.delay: 150
                    QQC2.ToolTip.text: {
                        var roleText = modelData.role === "both"
                            ? "Main & Subagent"
                            : (modelData.role === "main" ? "Main Agent" : "Subagent")
                        var lines = [
                            F.tierGlyph(modelData.tier) + " " + modelData.tier + " · " + roleText,
                            modelData.model + (modelData.provider ? " (" + modelData.provider + ")" : ""),
                            "Std task: ~" + modelData.costLabel + "  (" + modelData.costRaw + ")",
                            "Score: " + modelData.score + "  ·  " + modelData.benchmark
                        ]
                        return lines.join("\n")
                    }
                }
            }
        }

        // Compact legend
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                text: "● Main   ◇ Sub"
                color: graph.mutedColor
                font.pixelSize: 8
            }

            Item { Layout.fillWidth: true }

            Text {
                text: "◆ Diamond"
                color: F.tierColor("Diamond")
                font.pixelSize: 8
                font.bold: true
            }
            Text {
                text: "★ Gold"
                color: F.tierColor("Gold")
                font.pixelSize: 8
                font.bold: true
            }
            Text {
                text: "● Silver"
                color: F.tierColor("Silver")
                font.pixelSize: 8
                font.bold: true
            }
            Text {
                text: "--- Frontier"
                color: Kirigami.Theme.highlightColor
                font.pixelSize: 8
            }
        }
    }
}
