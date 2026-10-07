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
    // The Home screen supersedes the test pattern for a normal run; the pattern
    // is still what `--self-test` and a plain `--desktop` preview use.
    readonly property bool homeMode: Runtime.home && !waterfallMode

    visible: true
    color: "#0d0d0d"
    title: homeMode ? "iTuner SDR — Qt runtime (Home)"
                    : waterfallMode ? "iTuner SDR — Qt runtime (waterfall)"
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

        HomeScreen {
            visible: root.homeMode
            anchors.fill: parent
        }

        TestPattern {
            visible: !root.waterfallMode && !root.homeMode
            anchors.fill: parent
        }

        // Inside the rotated canvas so the diagnostics read upright to the
        // operator on the mounted panel. It belongs to the Task 0 test pattern
        // and is what a frame-rate measurement is read from, so it is not drawn
        // over a product screen: an operator does not want a diagnostic box on
        // the Home screen, and it would sit on top of the rail.
        FpsOverlay {
            anchors.fill: parent
            visible: !root.waterfallMode && !root.homeMode
            target: Runtime.fpsTarget
            runtimeSummary: Runtime.summary
            desktop: root.desktop
        }
    }
}
