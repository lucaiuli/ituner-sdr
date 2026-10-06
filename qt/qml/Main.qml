import QtQuick
import ItunerSdr

// Task 0 test pattern.
//
// The window is the native framebuffer (800x1280 portrait on the CM5, 1280x800
// in --desktop preview). The logical canvas is always 1280x800 landscape and is
// placed by OrientationCanvas, which takes its rotation and origin from the
// tested C++ geometry module.
Window {
    id: root

    readonly property bool desktop: Runtime.desktop

    visible: true
    color: "#0d0d0d"
    title: "iTuner SDR — Qt runtime (Task 0 test pattern)"

    width: desktop ? Runtime.logicalWidth : Runtime.panelWidth
    height: desktop ? Runtime.logicalHeight : Runtime.panelHeight

    // eglfs drives a full-screen surface. The desktop twin is an ordinary
    // window so the canvas can be inspected at its native pixel size.
    flags: desktop ? Qt.Window : Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus

    OrientationCanvas {
        TestPattern {
            anchors.fill: parent
        }

        // Inside the rotated canvas so the diagnostics read upright to the
        // operator on the mounted panel.
        FpsOverlay {
            anchors.fill: parent
            target: Runtime.fpsTarget
            runtimeSummary: Runtime.summary
            desktop: root.desktop
        }
    }
}
