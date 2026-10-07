import QtQuick
import ItunerSdr.Ui 1.0

// The Home screen: the live RF canvas on the left, the permanent 256 px rail on
// the right, and a single touch surface that routes every gesture through the
// tested C++ hit test.
//
// Nothing here decides a box or a navigation rule. HomeView hands over the rail
// tiles, the instruments and the open drawer's controls, each already carrying
// its box, its enabled state and the reason it is disabled. This file draws what
// it is given and forwards touches; that is what keeps the drawn control and the
// touchable control the same control.
Item {
    id: screen

    readonly property double rfCanvasWidth: Runtime.rfCanvasWidth
    readonly property double railX0: 1024

    HomeView {
        id: home
        receiverProtocol: "kiwi"

        // `--surface <name>` opens one drawer straight away so it can be
        // rendered and checked offscreen. The parent is Home either way, so its
        // Back control still returns there.
        Component.onCompleted: {
            if (Runtime.startSurface !== "") {
                openSurface(Runtime.startSurface)
            }
        }
    }

    // --- the live RF surface -------------------------------------------------
    WaterfallView {
        id: waterfallView
        waterfall: waterfall
        spectrumOverlay: spectrum
        passbandOverlay: passband

        Component.onCompleted: {
            screen.applyDisplayState()
            setView(Runtime.startFrequencyKhz, 13)
        }
    }

    WaterfallItem {
        id: waterfall
        x: 0
        y: 40
        width: screen.rfCanvasWidth
        height: 252

        Component.onCompleted: rowWidth = screen.rfCanvasWidth
    }

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

    // --- the permanent rail --------------------------------------------------
    Rectangle {
        x: screen.railX0
        y: 0
        width: Runtime.logicalWidth - screen.railX0
        height: Runtime.logicalHeight
        color: "#050d13"

        // Drawer heading, for the faces that have one.
        Rectangle {
            visible: home.drawerOpen
            x: 0
            y: 0
            width: parent.width
            height: 64
            color: "#060d13"
            Text {
                anchors.centerIn: parent
                text: home.drawerTitle
                color: "#d7ebef"
                font.pixelSize: 15
                font.bold: true
            }
        }

        // Live instrument stack, drawn from HomeView.instruments.
        Repeater {
            model: home.drawerOpen ? [] : ["frequency", "passband", "volume_mute", "volume", "smeter"]
            delegate: Item {
                readonly property var entry: home.instruments[modelData]
                // Named `controlEnabled` rather than `enabled`: `Item` already has
                // an `enabled`, and shadowing it would be a real behaviour change,
                // not just a warning.
                readonly property bool controlEnabled: entry ? entry.enabled : true

                x: entry ? entry.box.x - screen.railX0 : 0
                y: entry ? entry.box.y : 0
                width: entry ? entry.box.width : 0
                height: entry ? entry.box.height : 0
                visible: !!entry

                Rectangle {
                    anchors.fill: parent
                    color: modelData === "frequency" || modelData === "smeter" ? "#0b1a22" : "transparent"
                    border.color: "#20353d"
                    border.width: 1
                }

                Text {
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: modelData === "frequency" ? -8 : 0
                    text: {
                        if (!entry) return ""
                        if (modelData === "frequency") return home.frequencyText
                        if (modelData === "smeter") return "S " + Math.round(home.smeterFraction * 100) + "%"
                        if (modelData === "volume") return "VOL " + Math.round(home.volume * 100) + "%"
                        if (modelData === "volume_mute") return home.muted ? "MUTED" : "SPKR"
                        if (modelData === "passband") return "PASSBAND"
                        return ""
                    }
                    color: controlEnabled ? "#bfd9de" : "#5d6f75"
                    font.pixelSize: 14
                    font.bold: true
                }

                // The active mode and the selected tuning grid, read from the
                // same HomeView state the drawer commits into.
                Text {
                    visible: modelData === "frequency"
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 6
                    text: home.mode + "   " + home.tuneStepText
                    color: "#7fa3ab"
                    font.pixelSize: 11
                    font.bold: true
                }
            }
        }

        // Mode annunciators.
        Repeater {
            model: home.drawerOpen ? [] : home.homeModes
            delegate: Rectangle {
                x: modelData.box.x - screen.railX0
                y: modelData.box.y
                width: modelData.box.width
                height: modelData.box.height
                color: modelData.active ? "#2b7951" : "#0e2129"
                border.color: modelData.enabled ? "#39535c" : "#2a3a40"
                border.width: 1
                Text {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: modelData.enabled ? "#e6f2f4" : "#63757b"
                    font.pixelSize: 11
                    font.bold: true
                }
            }
        }

        // Rail tiles (and the shared Back control of a rail view).
        //
        // An open drawer *is* the rail: it replaces the tiles beneath it, which
        // is the Python rule (`radio_panel_box`: "the open drawer replaces the
        // annunciator block and the Home rail beneath it"). Drawing the tiles
        // behind the drawer would let their labels show through the gaps between
        // the drawer's own tiles, which is exactly the kind of defect only the
        // rendered frame can see.
        Repeater {
            model: home.drawerOpen ? [] : home.railTiles
            delegate: Rectangle {
                x: modelData.box.x - screen.railX0
                y: modelData.box.y
                width: modelData.box.width
                height: modelData.box.height
                color: modelData.isBack ? "#12262e" : "#0d1b22"
                border.color: "#243940"
                border.width: 1

                // The icon directory is supplied by the launcher, so a build with
                // no installed artwork shows the label rather than a broken image.
                // `Runtime.menuIconDir` is already an absolute `file:` URL: a
                // relative path here would resolve against this component's own
                // `qrc:` URL and quietly load nothing.
                Image {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 12
                    width: 44
                    height: 44
                    visible: !modelData.isBack && Runtime.menuIconDir !== "" && modelData.icon !== ""
                    source: visible ? Runtime.menuIconDir + "/" + modelData.icon : ""
                    fillMode: Image.PreserveAspectFit
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: modelData.isBack ? 0 : 8
                    anchors.verticalCenter: modelData.isBack ? parent.verticalCenter : undefined
                    text: modelData.label
                    color: "#d3e6ea"
                    font.pixelSize: modelData.isBack ? 14 : 12
                    font.bold: true
                }
            }
        }

        // The open drawer's own controls, including the shared Back control.
        //
        // A control carries its own `kind`. A plain `label` control is one line,
        // which is what the frequency, passband, modes and Apps drawers need; a
        // `tile` shows a title and a value; a `slider` additionally fills its
        // track from the level HomeView reports; a `choice` is a labelled box
        // that reads active when it is the selected option. The strings, the
        // boxes and the fill all come from HomeView, so nothing here can drift
        // from the geometry the golden pins.
        Repeater {
            model: home.drawerControls
            delegate: Rectangle {
                id: drawerControl
                x: modelData.box.x - screen.railX0
                y: modelData.box.y
                width: modelData.box.width
                height: modelData.box.height
                color: modelData.active ? "#1d4a38"
                                       : (modelData.enabled ? "#12262e" : "#0b1418")
                border.color: modelData.active ? "#3f9c72"
                                               : (modelData.enabled ? "#2c464f" : "#233034")
                border.width: 1

                readonly property string kind: modelData.kind === undefined ? "label" : modelData.kind
                readonly property var textColor: modelData.enabled
                    ? (modelData.active ? "#d8f4e7" : "#dcecef")
                    : "#5d6f75"

                // A continuous instrument: its box is the touch target, so the
                // track is drawn inset the way the Python drawer draws it.
                Rectangle {
                    visible: drawerControl.kind === "slider"
                    x: 10
                    y: parent.height - 20
                    width: Math.max(0, parent.width - 20)
                    height: 6
                    color: "#1b2d34"
                    Rectangle {
                        width: (modelData.fraction === undefined ? 0 : modelData.fraction) * parent.width
                        height: parent.height
                        color: "#50e2a4"
                    }
                }

                Text {
                    visible: drawerControl.kind === "tile" || drawerControl.kind === "slider"
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                    anchors.topMargin: 8
                    text: modelData.title === undefined ? "" : modelData.title
                    color: drawerControl.textColor
                    font.pixelSize: 13
                    font.bold: true
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: drawerControl.kind === "label" ? 0 : 8
                    anchors.verticalCenter: drawerControl.kind === "label" ? parent.verticalCenter : undefined
                    text: drawerControl.kind === "label"
                        ? modelData.label
                        : (modelData.detail === undefined ? modelData.label : modelData.detail)
                    color: drawerControl.kind === "label" ? drawerControl.textColor : "#9dc0c7"
                    font.pixelSize: drawerControl.kind === "label" ? 13 : 12
                    font.bold: true
                }
            }
        }

        // A disabled control explains itself rather than swallowing the touch.
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 84
            width: parent.width - 24
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            visible: home.lastMessage !== ""
            text: home.lastMessage
            color: "#f0c07a"
            font.pixelSize: 12
            font.bold: true
        }
    }

    // The Display drawer owns the waterfall's own levels, so a change there is
    // pushed into the live view. It is a dedicated signal rather than a
    // re-application on every state change, so the waterfall's own controls
    // cannot be overwritten by an unrelated Home update.
    Connections {
        target: home
        function onDisplayChanged() { screen.applyDisplayState() }
    }

    function applyDisplayState() {
        var levels = home.displayLevels
        waterfallView.setDisplayLevels(levels[0], levels[1], levels[2], levels[3], levels[4])
        waterfallView.setSpectrumEnabled(home.spectrumEnabled)
    }

    // --- one touch surface ---------------------------------------------------
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onReleased: (mouse) => home.touch(mouse.x, mouse.y)
    }
}
