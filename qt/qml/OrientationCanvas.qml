import QtQuick

// Places the 1280x800 logical canvas inside the native framebuffer.
//
// The rotation and the origin are not computed here: they come from Runtime,
// which is backed by the same C++ geometry module that will invert touch
// coordinates in Task 4. One source of truth means the drawn canvas and the
// touch target cannot drift apart.
Item {
    id: host

    default property alias content: canvas.data

    x: Runtime.desktop ? 0 : Runtime.canvasX
    y: Runtime.desktop ? 0 : Runtime.canvasY
    width: Runtime.logicalWidth
    height: Runtime.logicalHeight
    rotation: Runtime.desktop ? 0 : Runtime.canvasRotation
    transformOrigin: Item.TopLeft

    Item {
        id: canvas

        anchors.fill: parent
    }
}
