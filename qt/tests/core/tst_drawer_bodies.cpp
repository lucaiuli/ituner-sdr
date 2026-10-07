// The Audio and Display drawer bodies, replayed from the Python renderer.
//
// Every expectation comes from `qt/tests/parity/capture_drawer_bodies.py`, which
// runs the real `draw_lcd_audio_drawer` and `draw_display_setup_panel` with their
// tile primitives wrapped, so the recorded rows are the arguments the Python
// drawer actually passed -- the operator-visible strings, in draw order, with
// the box each one is drawn in.
//
// The suite also pins the reasoning the drawers depend on: the control box table
// is one definition used by both the hit test and the tile, and the audio
// Denoise row is a drag instrument rather than a tap control, which is why its
// centre answers nothing.

#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <audio_controls.h>
#include <drawer_bodies.h>
#include <drawer_geometry.h>
#include <navigation.h>
#include <waterfall_model.h>

#include "golden_draw_list.h"

using ituner::core::ControlBox;
using ituner::test::loadGolden;

namespace {

QString compareBox(const QJsonArray &recorded, const ControlBox &produced) {
    if (recorded.size() != 4) {
        return QStringLiteral("the recorded box is malformed");
    }
    const double values[4] = {recorded.at(0).toDouble(), recorded.at(1).toDouble(),
                              recorded.at(2).toDouble(), recorded.at(3).toDouble()};
    const double producedValues[4] = {produced.x0, produced.y0, produced.x1, produced.y1};
    static const char *const names[4] = {"x0", "y0", "x1", "y1"};
    for (int index = 0; index < 4; ++index) {
        if (values[index] != producedValues[index]) {
            return QStringLiteral("%1: expected %2, produced %3")
                .arg(QLatin1String(names[index]), ituner::test::describeDouble(values[index]),
                     ituner::test::describeDouble(producedValues[index]));
        }
    }
    return {};
}

/// A recorded `None` is the empty string here: "nothing here" is a value, not an
/// absence, and the whole point of the port is that an empty answer is a real
/// answer the screen acts on.
QString compareText(const QJsonValue &recorded, const QString &produced) {
    const QString expected = recorded.isNull() ? QString() : recorded.toString();
    if (expected != produced) {
        return QStringLiteral("expected %1, produced %2")
            .arg(expected.isEmpty() ? QStringLiteral("<none>") : expected,
                 produced.isEmpty() ? QStringLiteral("<none>") : produced);
    }
    return {};
}

QStringList stringsFromJson(const QJsonArray &recorded) {
    QStringList list;
    for (const QJsonValue &value : recorded) {
        list.append(value.toString());
    }
    return list;
}

ituner::core::AudioControls audioControlsFromJson(const QJsonObject &recorded) {
    ituner::core::AudioControls controls;
    controls.mute = recorded.value(QStringLiteral("mute")).toBool();
    controls.voiceCleanLevel = recorded.value(QStringLiteral("voice_clean_level")).toInt();
    controls.hfEnhanceLevel = recorded.value(QStringLiteral("hf_enhance_level")).toInt();
    controls.toneProfile = recorded.value(QStringLiteral("tone_profile")).toInt();
    controls.squelchLevel = recorded.value(QStringLiteral("squelch_level")).toInt();
    controls.agc = recorded.value(QStringLiteral("agc")).toBool();
    controls.agcHang = recorded.value(QStringLiteral("agc_hang")).toBool();
    controls.nbAlgo = recorded.value(QStringLiteral("nb_algo")).toInt();
    controls.autonotch = recorded.value(QStringLiteral("autonotch")).toBool();
    controls.deemphasis = recorded.value(QStringLiteral("deemphasis")).toInt();
    controls.denoiseLevel = recorded.value(QStringLiteral("denoise_level")).toInt();
    return controls;
}

ituner::core::DisplayState displayStateFromJson(const QJsonObject &recorded) {
    ituner::core::DisplayState state;
    state.floor = recorded.value(QStringLiteral("floor")).toDouble();
    state.ceiling = recorded.value(QStringLiteral("ceiling")).toDouble();
    state.speed = recorded.value(QStringLiteral("speed")).toInt();
    state.autoScale = recorded.value(QStringLiteral("auto")).toBool();
    state.palette = recorded.value(QStringLiteral("palette")).toString();
    state.spectrumEnabled = recorded.value(QStringLiteral("spectrum_enabled")).toBool();
    state.instrumentLayout = recorded.value(QStringLiteral("instrument_layout")).toString();
    return state;
}

}  // namespace

class DrawerBodiesTest : public QObject {
    Q_OBJECT

private slots:
    void initTestCase();
    void audioGeometryMatches();
    void audioHitTestingMatches();
    void displayGeometryMatches();
    void displayHitTestingMatches();
    void audioTilesMatch();
    void displayTilesMatch();
    void audioSliderMapsMatch();
    void displaySliderMapsMatch();
    void tablesAndLabelsMatch();
    void audioActionsAreReversibleCycles();

private:
    QJsonObject m_golden;
};

void DrawerBodiesTest::initTestCase() {
    m_golden = loadGolden(QStringLiteral("drawer_bodies_expected.json"));
    QVERIFY2(!m_golden.isEmpty(), "drawer_bodies_expected.json is missing or empty");
}

void DrawerBodiesTest::audioGeometryMatches() {
    const ituner::core::AudioDrawerBoxes boxes = ituner::core::audioDrawerBoxes();
    const QJsonObject recorded = m_golden.value(QStringLiteral("audio_boxes")).toObject();
    const struct {
        const char *name;
        ControlBox box;
    } expected[] = {
        {"panel", boxes.panel},         {"volume", boxes.volume},
        {"mute", boxes.mute},           {"voice_clean", boxes.voiceClean},
        {"hf_enhance", boxes.hfEnhance}, {"squelch", boxes.squelch},
        {"agc", boxes.agc},             {"blanker", boxes.blanker},
        {"denoise", boxes.denoise},     {"notch", boxes.notch},
        {"deemphasis", boxes.deemphasis}, {"filter", boxes.filter},
        {"reset", boxes.reset},         {"tone", boxes.tone},
        {"backend", boxes.backend},
    };
    for (const auto &entry : expected) {
        const QString error =
            compareBox(recorded.value(QLatin1String(entry.name)).toArray(), entry.box);
        QVERIFY2(error.isEmpty(), qPrintable(QLatin1String(entry.name) + QStringLiteral(": ") + error));
    }

    // The drawer's close control is the one shared Back target, not a second
    // definition that could drift away from it.
    const QString closeError = compareBox(m_golden.value(QStringLiteral("audio_close")).toArray(),
                                          boxes.close);
    QVERIFY2(closeError.isEmpty(), qPrintable(closeError));
    QCOMPARE(boxes.close.x0, ituner::core::lcdDrawerBackBox().x0);
    QCOMPARE(boxes.close.y1, ituner::core::lcdDrawerBackBox().y1);

    // Every *tile* sits inside the panel: the tile grid is the drawer's face.
    const ControlBox *const tiles[] = {
        &boxes.mute,   &boxes.voiceClean, &boxes.hfEnhance, &boxes.agc,   &boxes.blanker,
        &boxes.notch,  &boxes.deemphasis, &boxes.tone,      &boxes.filter, &boxes.reset,
        &boxes.backend,
    };
    for (const ControlBox *tile : tiles) {
        QVERIFY(tile->x0 >= boxes.panel.x0);
        QVERIFY(tile->x1 <= boxes.panel.x1);
        QVERIFY(tile->y0 >= boxes.panel.y0);
        QVERIFY(tile->y1 <= boxes.panel.y1);
    }
    // The three continuous instruments deliberately span the whole logical
    // width, wider than the annunciator-aligned panel, so the live waterfall
    // stays visible beside them.
    const ControlBox *const sliders[] = {&boxes.volume, &boxes.squelch, &boxes.denoise};
    for (const ControlBox *slider : sliders) {
        QCOMPARE(slider->x0, 1024.0);   // the rail origin, not the annunciator
        QCOMPARE(slider->x1, 1280.0);   // the logical width
        QVERIFY(slider->y0 >= boxes.panel.y0);
        QVERIFY(slider->y1 <= boxes.panel.y1);
    }
    // Nothing in the drawer reaches the Back button: a tap on a control can
    // never be ambiguous with a tap on Back.
    for (const ControlBox *control : tiles) {
        QVERIFY(control->y1 < boxes.close.y0);
    }
    for (const ControlBox *slider : sliders) {
        QVERIFY(slider->y1 < boxes.close.y0);
    }
}

void DrawerBodiesTest::audioHitTestingMatches() {
    const QJsonArray recorded = m_golden.value(QStringLiteral("audio_hits")).toArray();
    QVERIFY(!recorded.isEmpty());
    for (const QJsonValue &value : recorded) {
        const QJsonArray row = value.toArray();
        const QString name = row.at(0).toString();
        const double x = row.at(1).toDouble();
        const double y = row.at(2).toDouble();
        const QString error = compareText(row.at(3), ituner::core::audioOptionAt(x, y));
        QVERIFY2(error.isEmpty(), qPrintable(name + QStringLiteral(": ") + error));
    }

    // The Denoise row is a slider: the Python option function does not name it,
    // so a tap on it is not a control action and only the drag edits the level.
    const ControlBox denoise = ituner::core::audioDrawerBoxes().denoise;
    QVERIFY(ituner::core::audioOptionAt((denoise.x0 + denoise.x1) / 2.0,
                                        (denoise.y0 + denoise.y1) / 2.0)
                .isEmpty());
}

void DrawerBodiesTest::displayGeometryMatches() {
    const ituner::core::DisplayDrawerBoxes boxes = ituner::core::displayDrawerBoxes();
    const QJsonObject recorded = m_golden.value(QStringLiteral("display_boxes")).toObject();
    const struct {
        const char *name;
        ControlBox box;
    } expected[] = {
        {"panel", boxes.panel},       {"spectrum", boxes.spectrum},
        {"auto", boxes.autoScale},    {"floor", boxes.floor},
        {"ceiling", boxes.ceiling},   {"reset", boxes.reset},
        {"instruments", boxes.instruments}, {"scope", boxes.scope},
    };
    for (const auto &entry : expected) {
        const QString error =
            compareBox(recorded.value(QLatin1String(entry.name)).toArray(), entry.box);
        QVERIFY2(error.isEmpty(), qPrintable(QLatin1String(entry.name) + QStringLiteral(": ") + error));
    }

    const QString closeError = compareBox(m_golden.value(QStringLiteral("display_close")).toArray(),
                                          boxes.close);
    QVERIFY2(closeError.isEmpty(), qPrintable(closeError));

    const QJsonArray rates = m_golden.value(QStringLiteral("display_rate_boxes")).toArray();
    QCOMPARE(boxes.rates.size(), rates.size());
    for (int index = 0; index < rates.size(); ++index) {
        const QJsonArray row = rates.at(index).toArray();
        QCOMPARE(boxes.rates.at(index).rate, row.at(0).toInt());
        QCOMPARE(boxes.rates.at(index).label, row.at(1).toString());
        const QString error = compareBox(row.at(2).toArray(), boxes.rates.at(index).box);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("rate %1: %2").arg(index).arg(error)));
    }

    const QJsonArray palettes = m_golden.value(QStringLiteral("display_palette_boxes")).toArray();
    QCOMPARE(boxes.palettes.size(), palettes.size());
    for (int index = 0; index < palettes.size(); ++index) {
        const QJsonArray row = palettes.at(index).toArray();
        QCOMPARE(boxes.palettes.at(index).name, row.at(0).toString());
        QCOMPARE(boxes.palettes.at(index).label, row.at(1).toString());
        const QString error = compareBox(row.at(2).toArray(), boxes.palettes.at(index).box);
        QVERIFY2(error.isEmpty(), qPrintable(QStringLiteral("palette %1: %2").arg(index).arg(error)));
    }
}

void DrawerBodiesTest::displayHitTestingMatches() {
    const QJsonArray recorded = m_golden.value(QStringLiteral("display_hits")).toArray();
    QVERIFY(!recorded.isEmpty());
    for (const QJsonValue &value : recorded) {
        const QJsonArray row = value.toArray();
        const QString name = row.at(0).toString();
        const double x = row.at(1).toDouble();
        const double y = row.at(2).toDouble();
        const ituner::core::DisplayOption option = ituner::core::displayOptionAt(x, y);
        QString error = compareText(row.at(3), option.action);
        QVERIFY2(error.isEmpty(), qPrintable(name + QStringLiteral(": ") + error));
        if (row.at(4).isNull()) {
            QCOMPARE(option.rate, 0);
            QVERIFY(option.palette.isEmpty());
        } else if (row.at(4).isString()) {
            error = compareText(row.at(4), option.palette);
        } else {
            QCOMPARE(option.rate, row.at(4).toInt());
        }
        QVERIFY2(error.isEmpty(), qPrintable(name + QStringLiteral(": ") + error));
    }

    // The layout and the SCOPE launcher share one row, so neither may answer for
    // the other's box.
    const ituner::core::DisplayDrawerBoxes boxes = ituner::core::displayDrawerBoxes();
    QCOMPARE(ituner::core::displayOptionAt(boxes.instruments.x0 + 4.0, boxes.instruments.y0 + 4.0).action,
             QStringLiteral("instruments"));
    QCOMPARE(ituner::core::displayOptionAt(boxes.scope.x0 + 4.0, boxes.scope.y0 + 4.0).action,
             QStringLiteral("scope"));
}

void DrawerBodiesTest::audioTilesMatch() {
    const QJsonArray recorded = m_golden.value(QStringLiteral("audio_tiles")).toArray();
    QVERIFY(!recorded.isEmpty());
    for (const QJsonValue &value : recorded) {
        const QJsonArray row = value.toArray();
        const QString name = row.at(0).toString();
        const QString mode = row.at(1).toString();
        const QString backend = row.at(2).toString();
        const double lowCut = row.at(3).toDouble();
        const double highCut = row.at(4).toDouble();
        const ituner::core::AudioControls controls =
            audioControlsFromJson(row.at(5).toObject());
        const QVector<ituner::core::DrawerTile> tiles =
            ituner::core::audioDrawerTiles(controls, lowCut, highCut, mode, backend);
        const QJsonArray expected = row.at(6).toArray();
        QCOMPARE(tiles.size(), expected.size());
        for (int index = 0; index < expected.size(); ++index) {
            const QJsonArray tile = expected.at(index).toArray();
            const QString prefix =
                QStringLiteral("%1 tile %2 (%3): ").arg(name).arg(index).arg(tile.at(0).toString());
            QCOMPARE(tiles.at(index).title, tile.at(0).toString());
            QCOMPARE(tiles.at(index).detail, tile.at(1).toString());
            QCOMPARE(tiles.at(index).active, tile.at(2).toBool());
            const QString error = compareBox(tile.at(3).toArray(), tiles.at(index).box);
            QVERIFY2(error.isEmpty(), qPrintable(prefix + error));
            // The fill a slider tile draws, and nothing for a plain tile.
            if (tile.at(4).isNull()) {
                QVERIFY(!tiles.at(index).isSlider());
                QCOMPARE(tiles.at(index).maximum, 0.0);
            } else {
                QCOMPARE(tiles.at(index).value, tile.at(4).toDouble());
                QCOMPARE(tiles.at(index).maximum, tile.at(5).toDouble());
                QVERIFY(tiles.at(index).fraction() >= 0.0);
                QVERIFY(tiles.at(index).fraction() <= 1.0);
            }
        }
    }

    // Every tile is the box its own hit test answers for, which is the property
    // that keeps drawing and touching from drifting apart. The volume row and
    // the Denoise row are the two continuous sliders, so they are named
    // separately rather than consulted through the option function.
    const ituner::core::AudioControls controls = ituner::core::defaultAudioControls();
    const QVector<ituner::core::DrawerTile> tiles =
        ituner::core::audioDrawerTiles(controls, 300.0, 2700.0, QStringLiteral("USB"));
    for (const ituner::core::DrawerTile &tile : tiles) {
        const double centreX = (tile.box.x0 + tile.box.x1) / 2.0;
        const double centreY = (tile.box.y0 + tile.box.y1) / 2.0;
        if (tile.name == QStringLiteral("denoise")) {
            continue;  // a drag instrument: it is deliberately not a tap control
        }
        QCOMPARE(ituner::core::audioOptionAt(centreX, centreY), tile.name);
    }
}

void DrawerBodiesTest::displayTilesMatch() {
    const QJsonArray recorded = m_golden.value(QStringLiteral("display_tiles")).toArray();
    QVERIFY(!recorded.isEmpty());
    for (const QJsonValue &value : recorded) {
        const QJsonArray row = value.toArray();
        const QString name = row.at(0).toString();
        const ituner::core::DisplayState state = displayStateFromJson(row.at(1).toObject());
        const QVector<ituner::core::DrawerTile> tiles = ituner::core::displayDrawerTiles(state);
        const QJsonArray expected = row.at(2).toArray();
        QCOMPARE(tiles.size(), expected.size());
        for (int index = 0; index < expected.size(); ++index) {
            const QJsonArray tile = expected.at(index).toArray();
            const QString prefix =
                QStringLiteral("%1 tile %2 (%3): ").arg(name).arg(index).arg(tile.at(0).toString());
            QCOMPARE(tiles.at(index).title, tile.at(0).toString());
            QCOMPARE(tiles.at(index).detail, tile.at(1).toString());
            QCOMPARE(tiles.at(index).active, tile.at(2).toBool());
            const QString error = compareBox(tile.at(3).toArray(), tiles.at(index).box);
            QVERIFY2(error.isEmpty(), qPrintable(prefix + error));
            if (tile.at(4).isNull()) {
                QVERIFY(!tiles.at(index).isSlider());
            } else {
                QCOMPARE(tiles.at(index).value, tile.at(4).toDouble());
                QCOMPARE(tiles.at(index).maximum, tile.at(5).toDouble());
            }
        }
    }

    // The floor and ceiling rows are the two sliders the option function
    // declines to name, so they are the two entries a tap cannot reach.
    const ituner::core::DisplayState state = ituner::core::defaultDisplayState();
    for (const ituner::core::DrawerTile &tile : ituner::core::displayDrawerTiles(state)) {
        const double centreX = (tile.box.x0 + tile.box.x1) / 2.0;
        const double centreY = (tile.box.y0 + tile.box.y1) / 2.0;
        if (tile.name == QStringLiteral("floor") || tile.name == QStringLiteral("ceiling")) {
            QVERIFY(ituner::core::displayOptionAt(centreX, centreY).action.isEmpty());
            continue;
        }
        QCOMPARE(ituner::core::displayOptionAt(centreX, centreY).action, tile.name);
    }

    // A speed or palette is active exactly when it is the selected one, and the
    // labels are the drawer's own.
    const QVector<ituner::core::DisplayControl> controls =
        ituner::core::displayDrawerControls(state);
    QCOMPARE(controls.size(), 5);
    int activeCount = 0;
    for (const ituner::core::DisplayControl &control : controls) {
        activeCount += control.active ? 1 : 0;
    }
    QCOMPARE(activeCount, 2);
}

void DrawerBodiesTest::audioSliderMapsMatch() {
    const ituner::core::AudioDrawerBoxes boxes = ituner::core::audioDrawerBoxes();

    const QJsonArray volumes = m_golden.value(QStringLiteral("volume_map")).toArray();
    for (const QJsonValue &value : volumes) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::audioVolumeAtX(row.at(0).toDouble()), row.at(1).toDouble());
    }
    // The endpoints are exact and the level is clamped outside the track, so a
    // swipe that leaves the rail cannot drive the volume past its ends.
    QCOMPARE(ituner::core::audioVolumeAtX(boxes.volume.x0), 0.0);
    QCOMPARE(ituner::core::audioVolumeAtX(boxes.volume.x1), 1.0);

    const QJsonArray squelch = m_golden.value(QStringLiteral("squelch_map")).toArray();
    for (const QJsonValue &value : squelch) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::audioSquelchAtX(row.at(0).toDouble(), row.at(1).toInt()),
                 row.at(2).toInt());
    }

    const QJsonArray denoise = m_golden.value(QStringLiteral("denoise_map")).toArray();
    for (const QJsonValue &value : denoise) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::audioDenoiseLevelAtX(row.at(0).toDouble()), row.at(1).toInt());
    }
    // The detents are discrete, so the same position twice is the same level and
    // the level never exceeds the table.
    QCOMPARE(ituner::core::audioDenoiseLevelAtX(boxes.denoise.x0),
             ituner::core::audioDenoiseLevelAtX(boxes.denoise.x0));
    QVERIFY(ituner::core::audioDenoiseLevelAtX(boxes.denoise.x1) < ituner::core::denoisePresetNames().size());
}

void DrawerBodiesTest::displaySliderMapsMatch() {
    const ituner::core::DisplayDrawerBoxes boxes = ituner::core::displayDrawerBoxes();

    const QJsonArray floors = m_golden.value(QStringLiteral("floor_map")).toArray();
    for (const QJsonValue &value : floors) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::waterfallFloorAtX(row.at(0).toDouble(), boxes.floor.x0,
                                                 boxes.floor.x1, row.at(1).toDouble()),
                 row.at(2).toInt());
    }

    const QJsonArray ceilings = m_golden.value(QStringLiteral("ceiling_map")).toArray();
    for (const QJsonValue &value : ceilings) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::waterfallCeilingAtX(row.at(0).toDouble(), boxes.ceiling.x0,
                                                   boxes.ceiling.x1, row.at(1).toDouble()),
                 row.at(2).toInt());
    }
}

void DrawerBodiesTest::tablesAndLabelsMatch() {
    QCOMPARE(ituner::core::voiceCleanPresets(),
             stringsFromJson(m_golden.value(QStringLiteral("voice_presets")).toArray()));
    QCOMPARE(ituner::core::hfEnhancePresets(),
             stringsFromJson(m_golden.value(QStringLiteral("hf_presets")).toArray()));
    QCOMPARE(ituner::core::tonePresets(),
             stringsFromJson(m_golden.value(QStringLiteral("tone_presets")).toArray()));
    QCOMPARE(ituner::core::denoisePresetNames(),
             stringsFromJson(m_golden.value(QStringLiteral("denoise_presets")).toArray()));

    const QJsonArray positions = m_golden.value(QStringLiteral("denoise_slider_positions")).toArray();
    const QVector<double> sliderPositions = ituner::core::denoiseSliderPositions();
    QCOMPARE(sliderPositions.size(), positions.size());
    for (int index = 0; index < positions.size(); ++index) {
        QCOMPARE(sliderPositions.at(index), positions.at(index).toDouble());
    }

    const QJsonArray gains = m_golden.value(QStringLiteral("denoise_makeup_gain_db")).toArray();
    for (const QJsonValue &value : gains) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::denoiseMakeupGainDb(row.at(0).toInt()), row.at(1).toInt());
    }

    const QJsonArray volumeLabels = m_golden.value(QStringLiteral("volume_labels")).toArray();
    for (const QJsonValue &value : volumeLabels) {
        const QJsonArray row = value.toArray();
        // The recorded `None` is the same floor level a missing volume takes.
        const double level = row.at(0).isNull() ? 0.0 : row.at(0).toDouble();
        QCOMPARE(ituner::core::mainVolumeLabel(level), row.at(1).toString());
    }

    const QJsonArray widths = m_golden.value(QStringLiteral("filter_widths")).toArray();
    for (const QJsonValue &value : widths) {
        const QJsonArray row = value.toArray();
        QCOMPARE(ituner::core::formatFilterWidth(row.at(0).toDouble()), row.at(1).toString());
    }

    const QJsonArray maxima = m_golden.value(QStringLiteral("squelch_maximum")).toArray();
    for (const QJsonValue &value : maxima) {
        const QJsonArray row = value.toArray();
        const QString mode = row.at(0).isNull() ? QString() : row.at(0).toString();
        QCOMPARE(ituner::core::squelchMaximum(mode), row.at(1).toInt());
    }

    // The HF enhancer's availability is an input, not a guess: with no model
    // installed the drawer offers OFF alone.
    QCOMPARE(ituner::core::hfEnhanceLevels(false), QVector<int>{0});
    QCOMPARE(ituner::core::hfEnhanceLevels(true), (QVector<int>{0, 1, 2}));
}

void DrawerBodiesTest::audioActionsAreReversibleCycles() {
    // Each cycle returns to where it started, which is what makes a single
    // button safe to use without a menu: AGC is OFF -> AUTO -> HANG -> OFF.
    const QVector<QString> cycles = {QStringLiteral("mute"), QStringLiteral("voice_clean"),
                                     QStringLiteral("hf_enhance"), QStringLiteral("tone"),
                                     QStringLiteral("agc"), QStringLiteral("blanker"),
                                     QStringLiteral("notch"), QStringLiteral("deemphasis")};
    for (const QString &action : cycles) {
        ituner::core::AudioControls controls = ituner::core::defaultAudioControls();
        const int voiceBefore = controls.voiceCleanLevel;
        const int hfBefore = controls.hfEnhanceLevel;
        const int toneBefore = controls.toneProfile;
        const int blankerBefore = controls.nbAlgo;
        const int deemphasisBefore = controls.deemphasis;
        const bool muteBefore = controls.mute;
        const bool notchBefore = controls.autonotch;
        const bool agcBefore = controls.agc;
        const bool hangBefore = controls.agcHang;
        // Twelve steps is a whole number of turns for every cycle length the
        // drawer uses (2, 3 and 4), so a cycle that never returns is visible.
        for (int step = 0; step < 12; ++step) {
            controls = ituner::core::applyAudioAction(controls, action);
        }
        QCOMPARE(controls.voiceCleanLevel, voiceBefore);
        QCOMPARE(controls.hfEnhanceLevel, hfBefore);
        QCOMPARE(controls.toneProfile, toneBefore);
        QCOMPARE(controls.nbAlgo, blankerBefore);
        QCOMPARE(controls.deemphasis, deemphasisBefore);
        QCOMPARE(controls.mute, muteBefore);
        QCOMPARE(controls.autonotch, notchBefore);
        QCOMPARE(controls.agc, agcBefore);
        QCOMPARE(controls.agcHang, hangBefore);
    }

    // AGC reaches every one of its three faces before repeating.
    ituner::core::AudioControls controls = ituner::core::defaultAudioControls();
    QCOMPARE(controls.agc, true);
    QCOMPARE(controls.agcHang, false);
    controls = ituner::core::applyAudioAction(controls, QStringLiteral("agc"));
    QCOMPARE(controls.agc, true);
    QCOMPARE(controls.agcHang, true);
    controls = ituner::core::applyAudioAction(controls, QStringLiteral("agc"));
    QCOMPARE(controls.agc, false);
    QCOMPARE(controls.agcHang, false);

    // The two listener processors never stack: selecting one clears the other.
    ituner::core::AudioControls both = ituner::core::defaultAudioControls();
    both = ituner::core::applyAudioAction(both, QStringLiteral("voice_clean"));
    QCOMPARE(both.voiceCleanLevel, 1);
    both = ituner::core::applyAudioAction(both, QStringLiteral("hf_enhance"));
    QCOMPARE(both.voiceCleanLevel, 0);
    QCOMPARE(both.hfEnhanceLevel, 0);  // no model installed, so OFF is the only choice
    both = ituner::core::applyAudioAction(both, QStringLiteral("voice_clean"),
                                          ituner::core::hfEnhanceLevels(true));
    QCOMPARE(both.voiceCleanLevel, 1);
    both = ituner::core::applyAudioAction(both, QStringLiteral("hf_enhance"),
                                          ituner::core::hfEnhanceLevels(true));
    QCOMPARE(both.voiceCleanLevel, 0);
    QCOMPARE(both.hfEnhanceLevel, 1);
    both = ituner::core::applyAudioAction(both, QStringLiteral("hf_enhance"),
                                          ituner::core::hfEnhanceLevels(true));
    QCOMPARE(both.hfEnhanceLevel, 2);
    both = ituner::core::applyAudioAction(both, QStringLiteral("hf_enhance"),
                                          ituner::core::hfEnhanceLevels(true));
    QCOMPARE(both.hfEnhanceLevel, 0);

    // Denoise and the processors are exclusive, and the drawer says so.
    ituner::core::AudioControls denoised = ituner::core::defaultAudioControls();
    denoised.denoiseLevel = 3;
    QCOMPARE(ituner::core::audioDrawerTiles(denoised, 300.0, 2700.0, QStringLiteral("USB"))
                 .at(6)
                 .detail,
             QStringLiteral("STRONG"));
    denoised.voiceCleanLevel = 1;
    QCOMPARE(ituner::core::audioDrawerTiles(denoised, 300.0, 2700.0, QStringLiteral("USB"))
                 .at(6)
                 .detail,
             QStringLiteral("BYPASS"));

    // The actions the drawer does not own leave the state alone.
    const ituner::core::AudioControls untouched = ituner::core::defaultAudioControls();
    for (const QString &action :
         {QStringLiteral("close"), QStringLiteral("filter"), QStringLiteral("backend"),
          QStringLiteral("nonsense")}) {
        const ituner::core::AudioControls after =
            ituner::core::applyAudioAction(untouched, action);
        QCOMPARE(after.mute, untouched.mute);
        QCOMPARE(after.voiceCleanLevel, untouched.voiceCleanLevel);
        QCOMPARE(after.agc, untouched.agc);
    }

    // Reset is a real reset, from a state that is nothing like the default.
    ituner::core::AudioControls busy = ituner::core::defaultAudioControls();
    busy.mute = true;
    busy.voiceCleanLevel = 2;
    busy.nbAlgo = 2;
    busy.deemphasis = 2;
    busy.autonotch = true;
    const ituner::core::AudioControls reset =
        ituner::core::applyAudioAction(busy, QStringLiteral("reset"));
    QCOMPARE(reset.mute, false);
    QCOMPARE(reset.voiceCleanLevel, 0);
    QCOMPARE(reset.nbAlgo, 0);
    QCOMPARE(reset.deemphasis, 0);
    QCOMPARE(reset.autonotch, false);
    QCOMPARE(reset.agc, true);
}

QTEST_MAIN(DrawerBodiesTest)
#include "tst_drawer_bodies.moc"
