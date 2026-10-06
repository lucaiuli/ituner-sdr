import QtQuick

// Diagnostics for the Task 0 measurement.
//
// This item lives inside the rotated canvas so it reads upright to the operator
// on the mounted panel; if the labels are sideways or mirrored, the rotation
// transform is wrong. The measured rate is a one-second window of real frame
// pulses, not the requested target.
Item {
    id: overlay

    property real target: 24
    property string runtimeSummary: ""
    property bool desktop: false

    readonly property real measuredFps: fpsCounter.value

    Item {
        id: fpsCounter

        property real value: 0
        property int frames: 0
        property double windowStart: Date.now()

        FrameAnimation {
            running: true

            onTriggered: {
                fpsCounter.frames += 1;
                const now = Date.now();
                const elapsed = now - fpsCounter.windowStart;
                if (elapsed >= 1000) {
                    fpsCounter.value = fpsCounter.frames * 1000 / elapsed;
                    fpsCounter.frames = 0;
                    fpsCounter.windowStart = now;
                }
            }
        }
    }

    Rectangle {
        id: readout

        x: 24
        y: 150
        width: 420
        height: 150
        radius: 8
        color: "#000000cc"
        border.color: "#4a4a4a"
        border.width: 2

        Column {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 4

            Text {
                text: "measured " + overlay.measuredFps.toFixed(1) + " fps"
                color: overlay.measuredFps >= overlay.target * 0.9 ? "#00e676" : "#ffb300"
                font.pixelSize: 30
                font.bold: true
            }

            Text {
                text: "target " + overlay.target.toFixed(0) + " fps"
                color: "#ffffff"
                font.pixelSize: 22
            }

            Text {
                text: overlay.desktop ? "desktop preview" : "framebuffer target"
                color: "#a0a0a0"
                font.pixelSize: 22
            }

            Text {
                width: readout.width - 24
                text: overlay.runtimeSummary
                color: "#a0a0a0"
                font.pixelSize: 20
                wrapMode: Text.Wrap
            }
        }
    }

    // A moving bar and a per-frame blink: a stall or a dropped frame is visible
    // even when the one-second average still looks healthy.
    Rectangle {
        id: sweeper

        y: 360
        width: 160
        height: 12
        radius: 6
        color: "#00e5ff"

        NumberAnimation on x {
            from: 24
            to: overlay.width - sweeper.width - 24
            duration: 2000
            loops: Animation.Infinite
        }
    }

    Rectangle {
        id: frameBlink

        x: overlay.width - 84
        y: 24
        width: 60
        height: 60
        color: "#ffb300"

        FrameAnimation {
            running: true

            onTriggered: {
                frameBlink.opacity = frameBlink.opacity > 0.5 ? 0.15 : 1.0;
            }
        }
    }
}
