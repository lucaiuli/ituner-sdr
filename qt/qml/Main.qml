import QtQuick
import ItunerSdr

// The runtime window.
//
// The window is the native framebuffer (800x1280 portrait on the CM5, 1280x800
// in --desktop preview). The logical canvas is always 1280x800 landscape and is
// placed by OrientationCanvas, which takes its rotation and origin from the
// tested C++ geometry module.
//
// Two surfaces are available: the waterfall screen, which is the ported live
// view, and the Task 0 test pattern. The waterfall screen is shown for a
// captured row set (the offscreen render check) and for the frame-cost bench;
// everything else still shows the pattern while the Home screen is Task 4.
Window {
    id: root

    readonly property bool desktop: Runtime.desktop
    readonly property bool waterfallMode: Runtime.waterfallFramePath !== ""
                                          || Runtime.waterfallBenchSeconds > 0

    visible: true
    color: "#0d0d0d"
    title: waterfallMode ? "iTuner SDR — Qt runtime (waterfall)"
                         : "iTuner SDR — Qt runtime (Task 0 test pattern)"

    width: desktop ? Runtime.logicalWidth : Runtime.panelWidth
    height: desktop ? Runtime.logicalHeight : Runtime.panelHeight

    // eglfs drives a full-screen surface. The desktop twin is an ordinary
    // window so the canvas can be inspected at its native pixel size.
    flags: desktop ? Qt.Window : Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus

    OrientationCanvas {
        WaterfallScreen {
            visible: root.waterfallMode
            anchors.fill: parent
            rowSetPath: Runtime.waterfallFramePath
            rowSetStream: Runtime.waterfallStream
        }

        TestPattern {
            visible: !root.waterfallMode
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
