// The Home screen's wiring: one shared Back target, drawers inside the rail, and
// controls that never cover a Home instrument.
//
// These are the assertions the plan names for Task 4, driven through the same
// `HomeView` the QML screen uses rather than through the core directly. That
// matters: the core is already golden-verified, so the question this suite
// answers is whether the screen hands the operator the boxes and the rules the
// core describes.

#include <QtTest>

#include <audio_controls.h>
#include <drawer_bodies.h>
#include <drawer_geometry.h>
#include <navigation.h>
#include <receiver_capabilities.h>
#include <ui_style.h>
#include <waterfall_model.h>

#include <home_view.h>

using ituner::core::ControlBox;
using ituner::ui::HomeView;

namespace {

/// A box handed over as `{x, y, x1, y1, width, height}` directly.
ControlBox boxFromDirectMap(const QVariantMap &box) {
    return {box.value(QStringLiteral("x")).toDouble(), box.value(QStringLiteral("y")).toDouble(),
            box.value(QStringLiteral("x1")).toDouble(), box.value(QStringLiteral("y1")).toDouble()};
}

/// A control entry, which carries its box under `box`.
ControlBox boxFromMap(const QVariantMap &map) {
    return boxFromDirectMap(map.value(QStringLiteral("box")).toMap());
}

bool overlaps(const ControlBox &a, const ControlBox &b) {
    return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}

QString describe(const ControlBox &box) {
    return QStringLiteral("(%1, %2, %3, %4)").arg(box.x0).arg(box.y0).arg(box.x1).arg(box.y1);
}

}  // namespace

class HomeViewTest : public QObject {
    Q_OBJECT

private slots:
    void railTilesMatchTheCoreGeometry();
    void everyDrawerSharesOneBackTarget();
    void drawerControlsStayInsideTheRail();
    void homeInstrumentsDoNotCoverEachOther();
    void nothingReachesIntoTheRfCanvas();
    void navigationReturnsToTheSurfaceThatOpenedIt();
    void capabilityDisabledControlsExplainThemselves();
    void modeSelectionFollowsTheReceiverContract();
    void audioDrawerShowsThePortsOwnTiles();
    void audioDrawerActsOnItsOwnState();
    void displayDrawerEditsTheWaterfall();
    void drawerBodiesAreNoLongerPlaceholders();
    void themeIsThePortedStyleTokens();
    void controlsCarryTheirResolvedPaint();
    void disabledControlsArePaintedFromTheUntestedTokens();

private:
    /// Open a leaf so its drawer controls are available. Uses the rail when the
    /// surface is a rail destination and the instruments otherwise.
    static void openSurface(HomeView &view, const QString &surface);
};

void HomeViewTest::openSurface(HomeView &view, const QString &surface) {
    // The rail kinds that map to a named surface are exercised through the rail in
    // `navigationReturnsToTheSurfaceThatOpenedIt`; here the surface is opened
    // directly so each drawer's contents can be inspected.
    view.openSurface(surface);
}

void HomeViewTest::railTilesMatchTheCoreGeometry() {
    HomeView view;
    const QVariantList tiles = view.railTiles();
    const auto items = ituner::core::homeRailItems();
    QCOMPARE(tiles.size(), items.size());
    for (int index = 0; index < tiles.size(); ++index) {
        const QVariantMap tile = tiles.at(index).toMap();
        QCOMPARE(tile.value(QStringLiteral("kind")).toString(), items.at(index).kind);
        QCOMPARE(tile.value(QStringLiteral("label")).toString(), items.at(index).label);
        const ControlBox expected = ituner::core::lcdNavBox(index, items.size(), false);
        const ControlBox produced = boxFromMap(tile);
        QVERIFY2(produced == expected,
                 qPrintable(QStringLiteral("tile %1: expected %2, produced %3")
                                .arg(index)
                                .arg(describe(expected), describe(produced))));
    }

    // The Settings rail is the same rail with a Back control, and its last tile
    // is the shared target rather than an 114x114 square.
    view.openRail(5);
    const QVariantList settingsTiles = view.railTiles();
    const auto settingsItems = ituner::core::settingsRailItems();
    QCOMPARE(settingsTiles.size(), settingsItems.size());
    const QVariantMap back = settingsTiles.last().toMap();
    QVERIFY(back.value(QStringLiteral("isBack")).toBool());
    QVERIFY(boxFromMap(back) == ituner::core::lcdDrawerBackBox());
}

void HomeViewTest::everyDrawerSharesOneBackTarget() {
    const ControlBox shared = ituner::core::lcdDrawerBackBox();
    QCOMPARE(boxFromDirectMap(HomeView().backBox()), shared);

    for (const char *surface : {"frequency", "filter", "modes", "info", "apps", "audio",
                                "display", "receivers"}) {
        HomeView view;
        openSurface(view, QLatin1String(surface));
        bool sawBack = false;
        for (const QVariant &value : view.drawerControls()) {
            const QVariantMap control = value.toMap();
            if (control.value(QStringLiteral("name")).toString() == QStringLiteral("back")) {
                sawBack = true;
                const ControlBox box = boxFromMap(control);
                QVERIFY2(box == shared,
                         qPrintable(QStringLiteral("%1 Back: expected %2, produced %3")
                                        .arg(QLatin1String(surface), describe(shared),
                                             describe(box))));
            }
        }
        QVERIFY2(sawBack, qPrintable(QStringLiteral("%1 has no shared Back control").arg(QLatin1String(surface))));
    }
}

void HomeViewTest::drawerControlsStayInsideTheRail() {
    for (const char *surface : {"frequency", "filter", "modes", "info", "apps"}) {
        HomeView view;
        openSurface(view, QLatin1String(surface));
        for (const QVariant &value : view.drawerControls()) {
            const QVariantMap control = value.toMap();
            const ControlBox box = boxFromMap(control);
            const QString name = control.value(QStringLiteral("name")).toString();
            QVERIFY2(box.x0 >= 1024.0 && box.x1 <= 1280.0,
                     qPrintable(QStringLiteral("%1 %2 x range %3")
                                    .arg(QLatin1String(surface), name, describe(box))));
            QVERIFY2(box.y0 >= 0.0 && box.y1 <= 800.0,
                     qPrintable(QStringLiteral("%1 %2 y range %3")
                                    .arg(QLatin1String(surface), name, describe(box))));
        }
    }
}

void HomeViewTest::homeInstrumentsDoNotCoverEachOther() {
    HomeView view;
    const QVariantMap instruments = view.instruments();

    // The stack: mode grid, then passband, then volume, then the S-meter.
    const ControlBox passband = boxFromMap(instruments.value(QStringLiteral("passband")).toMap());
    const ControlBox volume = boxFromMap(instruments.value(QStringLiteral("volume")).toMap());
    const ControlBox mute = boxFromMap(instruments.value(QStringLiteral("volume_mute")).toMap());
    const ControlBox track = boxFromMap(instruments.value(QStringLiteral("volume_track")).toMap());
    const ControlBox smeter = boxFromMap(instruments.value(QStringLiteral("smeter")).toMap());
    const ControlBox readout = boxFromMap(instruments.value(QStringLiteral("frequency")).toMap());

    QVERIFY(volume.y0 > passband.y1);
    QVERIFY(smeter.y0 >= volume.y1);
    // The speaker toggle and the slider travel never share a pixel.
    QVERIFY(track.x0 > mute.x1);
    QVERIFY(mute.y0 >= volume.y0 && mute.y1 <= volume.y1);

    // The mode annunciators stop before the passband: the passband starts at the
    // mode grid's bottom edge by construction.
    for (const QVariant &value : view.homeModes()) {
        const ControlBox box = boxFromMap(value.toMap());
        QVERIFY(box.y1 <= passband.y0);
    }

    // Every Home instrument is clear of the tile grid except the S-meter, which
    // the Python `max()` deliberately lets run past it. Asserting that exception
    // is the point: it is behaviour, and it should be recorded rather than hidden.
    const double navTop = ituner::core::lcdNavTop(6, false);
    QVERIFY(passband.y1 <= navTop);
    QVERIFY(volume.y1 <= navTop);
    QVERIFY2(smeter.y1 > navTop,
             "the compact S-meter is expected to pass the tile grid, as the Python layout does");
    QVERIFY(readout.y1 <= passband.y0);
}

void HomeViewTest::nothingReachesIntoTheRfCanvas() {
    HomeView view;
    QVector<ControlBox> homeControls;
    for (const QVariant &value : view.railTiles()) {
        homeControls.append(boxFromMap(value.toMap()));
    }
    for (const QVariant &value : view.homeModes()) {
        homeControls.append(boxFromMap(value.toMap()));
    }
    const QVariantMap instruments = view.instruments();
    for (const QString &name : {QStringLiteral("frequency"), QStringLiteral("passband"),
                                QStringLiteral("volume"), QStringLiteral("volume_mute"),
                                QStringLiteral("volume_track"), QStringLiteral("smeter")}) {
        homeControls.append(boxFromMap(instruments.value(name).toMap()));
    }
    for (const ControlBox &box : homeControls) {
        QVERIFY2(box.x0 >= 1024.0,
                 qPrintable(QStringLiteral("a Home control reaches into the RF canvas: %1")
                                .arg(describe(box))));
    }

    // And the controls the operator can actually touch on Home do not overlap
    // each other where they must not: the mode buttons are a grid, the readout and
    // the passband are disjoint, and the volume's parts are disjoint.
    const auto modes = view.homeModes();
    for (int i = 0; i < modes.size(); ++i) {
        for (int j = i + 1; j < modes.size(); ++j) {
            QVERIFY2(!overlaps(boxFromMap(modes.at(i).toMap()), boxFromMap(modes.at(j).toMap())),
                     "two mode annunciators overlap");
        }
    }
}

void HomeViewTest::navigationReturnsToTheSurfaceThatOpenedIt() {
    HomeView view;
    QCOMPARE(view.surface(), QStringLiteral("home"));

    // A leaf opened from Home returns to Home.
    view.touch(1152.0, 47.0);
    QCOMPARE(view.surface(), QStringLiteral("frequency"));
    view.back();
    QCOMPARE(view.surface(), QStringLiteral("home"));

    // A leaf opened from Settings returns to Settings, not Home.
    view.openRail(5);
    QCOMPARE(view.surface(), QStringLiteral("settings"));
    view.openRail(3);  // APPS
    QCOMPARE(view.surface(), QStringLiteral("apps"));
    view.back();
    QCOMPARE(view.surface(), QStringLiteral("settings"));

    // The rail's own Back returns to Home.
    view.openRail(5);
    QCOMPARE(view.surface(), QStringLiteral("home"));

    // An unknown parent is Home rather than a dead end.
    view.touch(1152.0, 47.0);
    view.back();
    QCOMPARE(view.surface(), QStringLiteral("home"));
    view.back();
    QCOMPARE(view.surface(), QStringLiteral("home"));
}

void HomeViewTest::capabilityDisabledControlsExplainThemselves() {
    // A shared FM-DX tuner cannot move its frequency. The instrument must render
    // disabled and explain itself, and the touch must not be silently dropped.
    HomeView view;
    view.setReceiverProtocol(QStringLiteral("fmdx"));

    const QVariantMap readout = view.instruments().value(QStringLiteral("frequency")).toMap();
    QVERIFY2(!readout.value(QStringLiteral("enabled")).toBool(),
             "the FM-DX frequency readout should be disabled");
    QCOMPARE(readout.value(QStringLiteral("message")).toString(),
             ituner::core::kFmdxSharedFrequencyMessage);

    const QString action = view.touch(1152.0, 47.0);
    QCOMPARE(action, QStringLiteral("rejected"));
    QCOMPARE(view.lastMessage(), ituner::core::kFmdxSharedFrequencyMessage);
    // The frequency did not move and no drawer opened.
    QCOMPARE(view.surface(), QStringLiteral("home"));

    // Volume, by contrast, is ours to change on a shared tuner.
    const QVariantMap volume = view.instruments().value(QStringLiteral("volume")).toMap();
    QVERIFY(volume.value(QStringLiteral("enabled")).toBool());

    // Kiwi allows the same control, so the disabled state is the contract's, not
    // a hard-coded UI rule.
    HomeView kiwi;
    QVERIFY(kiwi.instruments()
                .value(QStringLiteral("frequency"))
                .toMap()
                .value(QStringLiteral("enabled"))
                .toBool());
}

void HomeViewTest::modeSelectionFollowsTheReceiverContract() {
    HomeView view;
    QCOMPARE(view.mode(), QStringLiteral("USB"));

    // The mode drawer cycles one family's variants and wraps.
    openSurface(view, QStringLiteral("modes"));
    QCOMPARE(view.surface(), QStringLiteral("modes"));
    const auto family = ituner::core::radioModeLayout().at(0);  // AM, whose variants are AM/AMN/AMW
    const ControlBox box = family.box;
    const double cx = (box.x0 + box.x1) / 2.0;
    const double cy = (box.y0 + box.y1) / 2.0;
    // USB is not in the AM family, so the first tap enters at its first variant
    // rather than at a neighbour: that is the Python `except ValueError` branch.
    const QStringList expected = {QStringLiteral("AM"), QStringLiteral("AMN"),
                                 QStringLiteral("AMW"), QStringLiteral("AM")};
    for (const QString &next : expected) {
        QCOMPARE(view.touch(cx, cy), QStringLiteral("mode_cycle"));
        QCOMPARE(view.mode(), next);
    }

    // A direct annunciator tap commits that mode.
    HomeView direct;
    const auto buttons = ituner::core::homeInstruments(true).modeButtons;
    const ControlBox usb = buttons.at(4).box;
    QCOMPARE(usb.contains((usb.x0 + usb.x1) / 2.0, (usb.y0 + usb.y1) / 2.0), true);
    direct.touch((usb.x0 + usb.x1) / 2.0, (usb.y0 + usb.y1) / 2.0);
    QCOMPARE(direct.mode(), QStringLiteral("USB"));
}

void HomeViewTest::audioDrawerShowsThePortsOwnTiles() {
    HomeView view;
    view.openSurface(QStringLiteral("audio"));
    QCOMPARE(view.surface(), QStringLiteral("audio"));

    const QVariantList controls = view.drawerControls();
    // Back, the volume row, then the thirteen tiles the drawer is defined by.
    const QVector<ituner::core::DrawerTile> expected = ituner::core::audioDrawerTiles(
        view.audioControls(), view.lowCutHz(), view.highCutHz(), view.mode(), view.audioBackend());
    QCOMPARE(controls.size(), expected.size() + 2);

    const QVariantMap back = controls.at(0).toMap();
    QCOMPARE(back.value(QStringLiteral("name")).toString(), QStringLiteral("back"));
    QVERIFY(boxFromMap(back) == ituner::core::lcdDrawerBackBox());

    const QVariantMap volume = controls.at(1).toMap();
    QCOMPARE(volume.value(QStringLiteral("kind")).toString(), QStringLiteral("slider"));
    QCOMPARE(volume.value(QStringLiteral("title")).toString(), QStringLiteral("VOLUME"));
    QCOMPARE(volume.value(QStringLiteral("detail")).toString(),
             ituner::core::mainVolumeLabel(view.volume()));
    QVERIFY(boxFromMap(volume) == ituner::core::audioDrawerBoxes().volume);

    for (int index = 0; index < expected.size(); ++index) {
        const QVariantMap entry = controls.at(index + 2).toMap();
        const ituner::core::DrawerTile &tile = expected.at(index);
        QCOMPARE(entry.value(QStringLiteral("name")).toString(), tile.name);
        QCOMPARE(entry.value(QStringLiteral("title")).toString(), tile.title);
        QCOMPARE(entry.value(QStringLiteral("detail")).toString(), tile.detail);
        QCOMPARE(entry.value(QStringLiteral("active")).toBool(), tile.active);
        QCOMPARE(entry.value(QStringLiteral("fraction")).toDouble(), tile.fraction());
        QVERIFY2(boxFromMap(entry) == tile.box, qPrintable(tile.name));
    }

    // Every drawer control is inside the rail and none of them covers the shared
    // Back control, so the drawer is fully operable without a dead touch.
    const ControlBox rail{1024.0, 0.0, 1280.0, 800.0};
    for (const QVariant &value : controls) {
        const ControlBox box = boxFromMap(value.toMap());
        QVERIFY(box.x0 >= rail.x0);
        QVERIFY(box.x1 <= rail.x1);
        if (value.toMap().value(QStringLiteral("name")).toString() == QStringLiteral("back")) {
            continue;
        }
        QVERIFY2(!overlaps(box, ituner::core::lcdDrawerBackBox()),
                 qPrintable(QStringLiteral("a control covers Back: %1").arg(describe(box))));
    }
}

void HomeViewTest::audioDrawerActsOnItsOwnState() {
    HomeView view;
    view.openSurface(QStringLiteral("audio"));
    const ituner::core::AudioDrawerBoxes boxes = ituner::core::audioDrawerBoxes();
    const auto centre = [](const ControlBox &box) {
        return QPointF((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0);
    };
    const auto tileNamed = [&view](const QString &name) {
        for (const QVariant &value : view.drawerControls()) {
            const QVariantMap entry = value.toMap();
            if (entry.value(QStringLiteral("name")).toString() == name) {
                return entry;
            }
        }
        return QVariantMap();
    };

    // MUTE flips, and the tile says so.
    QCOMPARE(view.audioControls().mute, false);
    QCOMPARE(tileNamed(QStringLiteral("mute")).value(QStringLiteral("detail")).toString(),
             QStringLiteral("OFF"));
    QCOMPARE(view.touch(centre(boxes.mute).x(), centre(boxes.mute).y()),
             QStringLiteral("audio_mute"));
    QCOMPARE(view.audioControls().mute, true);
    QCOMPARE(tileNamed(QStringLiteral("mute")).value(QStringLiteral("active")).toBool(), true);

    // The volume row is the same instrument as the Home volume control.
    QCOMPARE(view.touch(boxes.volume.x0 + (boxes.volume.x1 - boxes.volume.x0) / 2.0, 190.0),
             QStringLiteral("audio_volume"));
    QCOMPARE(view.volume(), 0.5);

    // The squelch scale follows the receiver mode, which the drawer asks the
    // contract for rather than assuming.
    QCOMPARE(ituner::core::squelchMaximum(QStringLiteral("USB")), 40);
    view.touch(boxes.squelch.x1, 260.0);
    QCOMPARE(view.audioControls().squelchLevel, 40);
    QCOMPARE(tileNamed(QStringLiteral("squelch")).value(QStringLiteral("detail")).toString(),
             QStringLiteral("40 dB"));
    view.openSurface(QStringLiteral("audio"));
    view.setMode(QStringLiteral("NBFM"));
    view.touch(boxes.squelch.x1, 260.0);
    QCOMPARE(view.audioControls().squelchLevel, 99);
    QCOMPARE(tileNamed(QStringLiteral("squelch")).value(QStringLiteral("detail")).toString(),
             QStringLiteral("99"));

    // The Denoise row is a detent slider: a drag to the right end is MAX.
    view.touch(boxes.denoise.x1, 334.0);
    QCOMPARE(view.audioControls().denoiseLevel, 5);
    QCOMPARE(tileNamed(QStringLiteral("denoise")).value(QStringLiteral("detail")).toString(),
             QStringLiteral("MAX"));

    // A blank rail tap closes the drawer, which is the Python rule for an option
    // function that answers nothing.
    QCOMPARE(view.touch(1200.0, 446.0), QStringLiteral("back"));
    QCOMPARE(view.surface(), QStringLiteral("home"));

    // OUTPUT switches the output device, and RESET returns every control.
    view.openSurface(QStringLiteral("audio"));
    QCOMPARE(view.touch(centre(boxes.backend).x(), centre(boxes.backend).y()),
             QStringLiteral("audio_backend"));
    QCOMPARE(view.audioBackend(), QStringLiteral("alsa"));
    QCOMPARE(tileNamed(QStringLiteral("backend")).value(QStringLiteral("detail")).toString(),
             QStringLiteral("ALSA DIRECT"));
    QCOMPARE(view.touch(centre(boxes.reset).x(), centre(boxes.reset).y()),
             QStringLiteral("audio_reset"));
    QCOMPARE(view.audioControls().mute, false);
    QCOMPARE(view.audioControls().squelchLevel, 0);
    QCOMPARE(view.audioControls().denoiseLevel, 0);

    // PASSBAND opens the passband drawer with the audio drawer as its parent, so
    // Back returns to Audio rather than Home.
    view.openSurface(QStringLiteral("audio"));
    QCOMPARE(view.touch(centre(boxes.filter).x(), centre(boxes.filter).y()),
             QStringLiteral("audio_filter"));
    QCOMPARE(view.surface(), QStringLiteral("filter"));
    view.back();
    QCOMPARE(view.surface(), QStringLiteral("audio"));
}

void HomeViewTest::displayDrawerEditsTheWaterfall() {
    HomeView view;
    view.openSurface(QStringLiteral("display"));
    QCOMPARE(view.surface(), QStringLiteral("display"));

    const ituner::core::DisplayDrawerBoxes boxes = ituner::core::displayDrawerBoxes();
    const auto centre = [](const ControlBox &box) {
        return QPointF((box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0);
    };

    // The drawer's tiles and choices are the ported ones, in draw order.
    const QVector<ituner::core::DrawerTile> tiles =
        ituner::core::displayDrawerTiles(view.displayState());
    const QVector<ituner::core::DisplayControl> choices =
        ituner::core::displayDrawerControls(view.displayState());
    QCOMPARE(view.drawerControls().size(), tiles.size() + choices.size() + 1);
    for (int index = 0; index < tiles.size(); ++index) {
        const QVariantMap entry = view.drawerControls().at(index + 1).toMap();
        QCOMPARE(entry.value(QStringLiteral("name")).toString(), tiles.at(index).name);
        QCOMPARE(entry.value(QStringLiteral("detail")).toString(), tiles.at(index).detail);
        QVERIFY(boxFromMap(entry) == tiles.at(index).box);
    }

    // The speed and palette choices are boxes, not tiles: one line, active when
    // selected.
    const QVariantMap med = view.drawerControls().at(1 + tiles.size() + 1).toMap();
    QCOMPARE(med.value(QStringLiteral("kind")).toString(), QStringLiteral("choice"));
    QCOMPARE(med.value(QStringLiteral("detail")).toString(), QStringLiteral("MED"));
    QCOMPARE(med.value(QStringLiteral("active")).toBool(), false);

    // A speed change reaches the live waterfall, and the default speed is not it.
    QCOMPARE(view.displayLevels().at(3).toInt(), 4);
    QSignalSpy levels(&view, &HomeView::displayChanged);
    QCOMPARE(view.touch(centre(boxes.rates.at(1).box).x(), centre(boxes.rates.at(1).box).y()),
             QStringLiteral("display_rate"));
    QCOMPARE(view.displayLevels().at(3).toInt(), 2);
    QCOMPARE(levels.count(), 1);

    // AUTO also turns the floor/ceiling tiles off, because the leveler owns them.
    QCOMPARE(view.touch(centre(boxes.autoScale).x(), centre(boxes.autoScale).y()),
             QStringLiteral("display_auto"));
    QCOMPARE(view.displayState().autoScale, true);
    for (const ituner::core::DrawerTile &tile : ituner::core::displayDrawerTiles(view.displayState())) {
        if (tile.name == QStringLiteral("floor") || tile.name == QStringLiteral("ceiling")) {
            QVERIFY(!tile.active);
        }
    }

    // A floor drag edits the level and is what disengages auto.
    QCOMPARE(view.displayState().autoScale, true);
    QCOMPARE(view.touch(boxes.floor.x0 + 10.0, centre(boxes.floor).y()),
             QStringLiteral("display_floor"));
    QCOMPARE(view.displayState().autoScale, false);
    QCOMPARE(view.displayLevels().at(0).toInt(),
             ituner::core::waterfallFloorAtX(boxes.floor.x0 + 10.0, boxes.floor.x0, boxes.floor.x1,
                                             view.displayState().ceiling));

    // The spectrum toggle and the SCOPE launcher both end with a trace to watch.
    QCOMPARE(view.spectrumEnabled(), true);
    QCOMPARE(view.touch(centre(boxes.spectrum).x(), centre(boxes.spectrum).y()),
             QStringLiteral("display_spectrum"));
    QCOMPARE(view.spectrumEnabled(), false);
    QCOMPARE(view.touch(centre(boxes.scope).x(), centre(boxes.scope).y()),
             QStringLiteral("display_scope"));
    QCOMPARE(view.spectrumEnabled(), true);

    // The palette is normalized through the ported rule, so an unknown name can
    // never reach the waterfall.
    QCOMPARE(view.touch(centre(boxes.palettes.at(0).box).x(), centre(boxes.palettes.at(0).box).y()),
             QStringLiteral("display_palette"));
    QCOMPARE(view.displayState().palette, QStringLiteral("classic"));

    // LAYOUT changes the presentation, not the waterfall levels, so it must not
    // ask the view to re-apply them.
    QSignalSpy layoutLevels(&view, &HomeView::displayChanged);
    QCOMPARE(view.touch(centre(boxes.instruments).x(), centre(boxes.instruments).y()),
             QStringLiteral("display_instruments"));
    QCOMPARE(view.instrumentLayout(), QStringLiteral("expanded"));
    // Changing the presentation does not touch a single waterfall level, so it
    // must not ask the live view to re-apply any of them.
    QCOMPARE(layoutLevels.count(), 0);
    // The expanded presentation drops the compact readout instead of overlapping
    // it with the mode grid.
    QVERIFY(!view.instruments().contains(QStringLiteral("frequency")));
    QCOMPARE(view.touch(centre(boxes.instruments).x(), centre(boxes.instruments).y()),
             QStringLiteral("display_instruments"));
    QCOMPARE(view.instrumentLayout(), QStringLiteral("compact"));
    QVERIFY(view.instruments().contains(QStringLiteral("frequency")));

    // RESET returns every waterfall default and switches the spectrum back on.
    QCOMPARE(view.touch(centre(boxes.reset).x(), centre(boxes.reset).y()),
             QStringLiteral("display_reset"));
    QCOMPARE(view.displayLevels().at(3).toInt(), ituner::core::waterfallDefaultSpeed());
    QCOMPARE(view.displayLevels().at(0).toInt(), ituner::core::waterfallDefaultFloor());
    QCOMPARE(view.displayLevels().at(1).toInt(), ituner::core::waterfallDefaultCeiling());
    QCOMPARE(view.displayLevels().at(4).toString(), ituner::core::waterfallDefaultPalette());
    QCOMPARE(view.spectrumEnabled(), true);
    QCOMPARE(view.displayState().autoScale, false);

    // A blank rail tap closes it, and Back returns to the surface that opened it.
    view.openSurface(QStringLiteral("display"));
    QCOMPARE(view.touch(1200.0, 600.0), QStringLiteral("back"));
    QCOMPARE(view.surface(), QStringLiteral("home"));
}

void HomeViewTest::drawerBodiesAreNoLongerPlaceholders() {
    // AUDIO and DISPLAY were the two surfaces that showed a development notice
    // instead of a body. Each must now report a real action for its tiles and
    // must not be treated as an unwired screen.
    const struct {
        const char *surface;
        double x;
        double y;
        const char *expected;
    } cases[] = {
        {"audio", 1094.5, 412.0, "audio_voice_clean"},
        {"audio", 1094.5, 684.0, "audio_reset"},
        {"display", 1152.0, 113.0, "display_reset"},
        {"display", 1094.75, 190.0, "display_spectrum"},
    };
    for (const auto &entry : cases) {
        HomeView view;
        view.openSurface(QLatin1String(entry.surface));
        const QString action = view.touch(entry.x, entry.y);
        QCOMPARE(action, QLatin1String(entry.expected));
        QVERIFY(!action.isEmpty());
        QVERIFY(action != QStringLiteral("unwired"));
    }
}

void HomeViewTest::themeIsThePortedStyleTokens() {
    // The screen must not own a colour. Everything it paints a control with comes
    // from the ported style table, so this walks the theme the QML reads and
    // compares it with the style itself.
    HomeView view;
    const QVariantMap theme = view.theme();
    const ituner::core::AppUiStyle &style = ituner::core::appUiStyle();

    const struct {
        const char *property;
        ituner::core::Rgba colour;
    } roles[] = {
        {"background", style.palette.background},
        {"sidebar", style.palette.sidebar},
        {"surface", style.palette.surface},
        {"selectedSurface", style.palette.selectedSurface},
        {"border", style.palette.border},
        {"text", style.palette.text},
        {"secondaryText", style.palette.secondaryText},
        {"focus", style.palette.focus},
        {"focusText", style.palette.focusText},
        {"ready", style.palette.ready},
        {"waiting", style.palette.waiting},
        {"untested", style.palette.untested},
        {"untestedText", style.palette.untestedText},
        {"danger", style.palette.danger},
        {"dangerBorder", style.palette.dangerBorder},
    };
    for (const auto &role : roles) {
        const QString name = QLatin1String(role.property);
        QVERIFY2(theme.contains(name), qPrintable(QStringLiteral("the theme omits %1").arg(name)));
        const QString colour = theme.value(name).toString();
        QCOMPARE(colour, ituner::core::qmlColor(role.colour));
        // A QML colour is `#AARRGGBB`: eight digits plus the hash. A `#RRGGBB`
        // answer would silently draw every token opaque.
        QCOMPARE(colour.size(), 9);
        QVERIFY(colour.startsWith(QLatin1Char('#')));
    }

    QCOMPARE(theme.value(QStringLiteral("buttonRadius")).toInt(), style.button.radius);
    QCOMPARE(theme.value(QStringLiteral("labelSize")).toInt(), style.button.labelSize);
    QCOMPARE(theme.value(QStringLiteral("buttonFont")).toStringList(), style.button.fontFamily);
}

void HomeViewTest::controlsCarryTheirResolvedPaint() {
    HomeView view;
    const ituner::core::AppUiStyle &style = ituner::core::appUiStyle();
    const QString surface = ituner::core::qmlColor(style.palette.surface);
    const QString border = ituner::core::qmlColor(style.palette.border);
    const QString focus = ituner::core::qmlColor(style.palette.focus);
    const QString focusText = ituner::core::qmlColor(style.palette.focusText);

    // Every resting rail tile is the styled surface at border width 1, which is
    // what `draw_styled_button_frame` paints for the Python rail.
    const QVariantList tiles = view.railTiles();
    QVERIFY(!tiles.isEmpty());
    for (const QVariant &value : tiles) {
        const QVariantMap visual = value.toMap().value(QStringLiteral("visual")).toMap();
        QCOMPARE(visual.value(QStringLiteral("fill")).toString(), surface);
        QCOMPARE(visual.value(QStringLiteral("border")).toString(), border);
        QCOMPARE(visual.value(QStringLiteral("borderWidth")).toInt(), 1);
    }

    // A drawer control resolves the same way, and an active one resolves to the
    // focus colour at border width 2 rather than to a colour chosen here.
    view.openSurface(QStringLiteral("audio"));
    const auto controlNamed = [&view](const QString &name) {
        for (const QVariant &value : view.drawerControls()) {
            const QVariantMap entry = value.toMap();
            if (entry.value(QStringLiteral("name")).toString() == name) {
                return entry;
            }
        }
        return QVariantMap{};
    };

    const QVariantMap resting = controlNamed(QStringLiteral("mute"));
    QVERIFY2(!resting.isEmpty(), "the audio drawer should offer MUTE");
    QCOMPARE(resting.value(QStringLiteral("visual")).toMap().value(QStringLiteral("fill")).toString(),
             surface);

    // Activate it the way an operator does. The drawer's MUTE tile follows the
    // Audio controls, which is its own state: the Home rail's speaker toggle is a
    // different control and is deliberately not what this reads.
    const ControlBox mute = ituner::core::audioDrawerBoxes().mute;
    QCOMPARE(view.touch((mute.x0 + mute.x1) / 2.0, (mute.y0 + mute.y1) / 2.0),
             QStringLiteral("audio_mute"));
    const QVariantMap active = controlNamed(QStringLiteral("mute"));
    QVERIFY(active.value(QStringLiteral("active")).toBool());
    const QVariantMap visual = active.value(QStringLiteral("visual")).toMap();
    QCOMPARE(visual.value(QStringLiteral("fill")).toString(), focus);
    QCOMPARE(visual.value(QStringLiteral("text")).toString(), focusText);
    QCOMPARE(visual.value(QStringLiteral("borderWidth")).toInt(), 2);
}

void HomeViewTest::disabledControlsArePaintedFromTheUntestedTokens() {
    // A control the receiver cannot honour is drawn from the palette's `untested`
    // tokens, and it must be resolved *after* the capability verdict: resolving
    // the paint first would leave a disabled instrument looking usable.
    HomeView view;
    view.setReceiverProtocol(QStringLiteral("fmdx"));

    const QVariantMap readout = view.instruments().value(QStringLiteral("frequency")).toMap();
    QVERIFY(!readout.value(QStringLiteral("enabled")).toBool());

    const ituner::core::UiPalette &palette = ituner::core::appUiStyle().palette;
    const QVariantMap visual = readout.value(QStringLiteral("visual")).toMap();
    QCOMPARE(visual.value(QStringLiteral("fill")).toString(), ituner::core::qmlColor(palette.untested));
    QCOMPARE(visual.value(QStringLiteral("text")).toString(),
             ituner::core::qmlColor(palette.untestedText));

    // The same control on a Kiwi receiver is enabled and painted normally, so the
    // grey is the contract's verdict rather than a hard-coded rule.
    HomeView kiwi;
    const QVariantMap enabled = kiwi.instruments().value(QStringLiteral("frequency")).toMap();
    QVERIFY(enabled.value(QStringLiteral("enabled")).toBool());
    QCOMPARE(enabled.value(QStringLiteral("visual")).toMap().value(QStringLiteral("fill")).toString(),
             ituner::core::qmlColor(palette.surface));
}

QTEST_MAIN(HomeViewTest)
#include "tst_home_view.moc"
