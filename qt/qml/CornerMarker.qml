import QtQuick

// One labelled corner block of the Task 0 test pattern. Four of these with
// distinct colours and letters make a wrong rotation, a mirrored transform or a
// scaled canvas impossible to miss.
Item {
    id: marker

    property string label: "?"
    property color markerColour: "#00e5ff"
    property real side: 72

    width: side
    height: side

    Rectangle {
        anchors.fill: parent
        radius: 4
        color: marker.markerColour
    }

    Text {
        anchors.centerIn: parent
        text: marker.label
        color: "#0d0d0d"
        font.pixelSize: Math.round(marker.side * 0.47)
        font.bold: true
    }
}
