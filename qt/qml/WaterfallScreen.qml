import QtQuick
import ItunerSdr.Ui 1.0

// The live waterfall surface with its scope and passband overlays.
//
// The canvas is the full logical 1280x800 landscape space. The RF surface is the
// left 1024 pixels (Runtime.rfCanvasWidth), never the whole width: the final 256
// pixels are the permanent Home/drawer rail and must not scale the RF image or
// consume waterfall samples.
//
// Nothing about the display is decided here. The colours, levels, cadence and
// control boxes come from the tested core through WaterfallView, and this file
// only places the items and forwards touches.
Item {
    id: screen

    // A captured row set, for the offscreen render check. Empty in normal use.
    property string rowSetPath: ""
    property int rowSetStream: 0
    property bool capturedMode: rowSetPath !== ""

    // Live waterfall band, from the LCD layout: the ruler above, then the RF
    // surface down to the operating band.
    property double bandY: 40
    property double bandHeight: 252
    readonly property double rfCanvasWidth: Runtime.rfCanvasWidth

    WaterfallView {
        id: view
        waterfall: waterfall
        // The overlays sit over the live surface, which is the point of them. In
        // captured mode they are detached: the frame check compares the waterfall
        // itself with the Python render, and their rasterization is Qt's, not
        // Python's, so overlaying them would only obscure the comparison.
        spectrumOverlay: screen.capturedMode ? null : spectrum
        passbandOverlay: screen.capturedMode ? null : passband

        Component.onCompleted: {
            // Fresh-install display defaults: the LCD preferences' floor, ceiling,
            // speed 4 and the Kiwi palette, with auto-levelling on.
            setDisplayLevels(142, 245, true, 4, "kiwi")
            setView(Runtime.startFrequencyKhz, 13)
            setSpectrumEnabled(true)
        }
    }

    WaterfallItem {
        id: waterfall
        x: 0
        y: screen.capturedMode ? 0 : screen.bandY
        width: screen.capturedMode ? capturedRowWidth : screen.rfCanvasWidth
        height: screen.capturedMode ? capturedRowCount : screen.bandHeight

        Component.onCompleted: {
            if (screen.capturedMode) {
                // Render exactly the recorded row set, one screen row per line, so
                // the frame can be compared with the Python output pixel for pixel.
                loadCapturedRowSet(screen.rowSetPath, screen.rowSetStream)
            } else {
                rowWidth = screen.rfCanvasWidth
            }
        }
    }

    // The scope is drawn over the waterfall surface, which is what the LCD layout
    // does: its dBm ruler keeps the trace readable without a second panel.
    OverlayItem {
        id: spectrum
        anchors.fill: waterfall
        visible: false
        z: 1
    }

    OverlayItem {
        id: passband
        anchors.fill: waterfall
        visible: false
        z: 2
    }

    // The waterfall's own touches. The control group is not handled here: in the
    // LCD layout the zoom buttons sit inside the same band and the view consults
    // its own control geometry first, so a tap on a button never becomes a tune.
    MouseArea {
        id: touchSurface
        anchors.fill: waterfall
        enabled: !screen.capturedMode
        acceptedButtons: Qt.LeftButton

        // The release velocity drives the inertia decay, and the drag sensitivity
        // ramp reads it while moving, so it is tracked from real samples rather
        // than guessed.
        property double lastX: 0
        property double lastT: 0
        property double velocity: 0

        onPressed: (mouse) => {
            lastX = mouse.x
            lastT = Date.now()
            velocity = 0
            view.beginTouch(mouse.x, mouse.y)
        }
        onPositionChanged: (mouse) => {
            const now = Date.now()
            const dt = Math.max(1, now - lastT) / 1000
            velocity = (mouse.x - lastX) / dt
            lastX = mouse.x
            lastT = now
            view.moveTouch(mouse.x, mouse.y, velocity)
        }
        onReleased: (mouse) => view.endTouch(mouse.x, mouse.y, velocity)
    }

    // The inertia decay tick. The view only moves the frequency while a release
    // momentum is outstanding, and the default strength of 0 makes this a no-op.
    Timer {
        interval: 16
        running: !screen.capturedMode
        repeat: true
        onTriggered: view.advance(0.016)
    }
}
