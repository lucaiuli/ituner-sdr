import QtQuick

// The Task 0 rotation and scaling check.
//
// Everything here is authored in logical canvas coordinates (1280x800). If the
// panel shows the four corner letters in the right corners, all edge labels
// upright, and the magenta diagonal running from the cyan corner to the green
// one, then the rotation and the touch transform agree with the Python
// renderer.
Item {
    id: pattern

    readonly property int gridStep: 10
    readonly property int markerSide: 72
    readonly property color gridColour: "#242424"
    readonly property color majorGridColour: "#3a3a3a"
    readonly property color frameColour: "#00e5ff"

    Rectangle {
        anchors.fill: parent
        color: "#121212"
    }

    // A 10 px grid; every fifth line is brighter so scaling errors are visible.
    Repeater {
        model: Math.ceil(pattern.width / pattern.gridStep)

        delegate: Rectangle {
            required property int index

            x: index * pattern.gridStep
            y: 0
            width: 1
            height: pattern.height
            color: index % 5 === 0 ? pattern.majorGridColour : pattern.gridColour
        }
    }

    Repeater {
        model: Math.ceil(pattern.height / pattern.gridStep)

        delegate: Rectangle {
            required property int index

            x: 0
            y: index * pattern.gridStep
            width: pattern.width
            height: 1
            color: index % 5 === 0 ? pattern.majorGridColour : pattern.gridColour
        }
    }

    // Mirroring check: one diagonal from the top-left to the bottom-right.
    Rectangle {
        x: 0
        y: 0
        width: Math.sqrt(pattern.width * pattern.width + pattern.height * pattern.height)
        height: 2
        color: "#e91e63"
        transformOrigin: Item.Left
        rotation: Math.atan2(pattern.height, pattern.width) * 180 / Math.PI
    }

    // The exact logical canvas bounds.
    Rectangle {
        anchors.fill: parent
        anchors.margins: 3
        color: "transparent"
        border.color: pattern.frameColour
        border.width: 2
    }

    // Centre crosshair and a scaling circle.
    Rectangle {
        x: pattern.width / 2 - 1
        y: 0
        width: 2
        height: pattern.height
        color: "#ffb300"
    }

    Rectangle {
        x: 0
        y: pattern.height / 2 - 1
        width: pattern.width
        height: 2
        color: "#ffb300"
    }

    Rectangle {
        x: pattern.width / 2 - 200
        y: pattern.height / 2 - 200
        width: 400
        height: 400
        radius: 200
        color: "transparent"
        border.color: "#ffb300"
        border.width: 2
    }

    CornerMarker {
        x: 12
        y: 12
        side: pattern.markerSide
        label: "TL"
        markerColour: "#00e5ff"
    }

    CornerMarker {
        x: pattern.width - pattern.markerSide - 12
        y: 12
        side: pattern.markerSide
        label: "TR"
        markerColour: "#e91e63"
    }

    CornerMarker {
        x: 12
        y: pattern.height - pattern.markerSide - 12
        side: pattern.markerSide
        label: "BL"
        markerColour: "#ffb300"
    }

    CornerMarker {
        x: pattern.width - pattern.markerSide - 12
        y: pattern.height - pattern.markerSide - 12
        side: pattern.markerSide
        label: "BR"
        markerColour: "#00e676"
    }

    EdgeLabel {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 20
        text: "TOP  y=0"
    }

    EdgeLabel {
        anchors.horizontalCenter: parent.horizontalCenter
        y: pattern.height - 46
        text: "BOTTOM  y=799"
    }

    EdgeLabel {
        x: 20
        anchors.verticalCenter: parent.verticalCenter
        text: "LEFT  x=0"
    }

    EdgeLabel {
        x: pattern.width - 220
        anchors.verticalCenter: parent.verticalCenter
        text: "RIGHT  x=1279"
    }

    // A tenth-interval ruler: a stretched canvas cannot pass unnoticed.
    Repeater {
        model: 9

        delegate: Rectangle {
            required property int index

            x: (index + 1) * pattern.width / 10 - 1
            y: pattern.height / 2 + 210
            width: 2
            height: 26
            color: "#8a8a8a"
        }
    }
}
