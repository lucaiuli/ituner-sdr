// Parity tests for the swipe/tuning gesture model.
//
// Every expectation comes from `qt/tests/golden/swipe_gesture_expected.json`,
// produced by `qt/tests/parity/capture_swipe_gesture.py`. Where the Python
// renderer exposes a real module-level function the golden is that function's
// output; where the behaviour is inline in the renderer's input loop the capture
// restates the rules read from the source, and this port carries the same rules
// as testable functions. The capture is the specification either way, so nothing
// here is hand-written against a reading of the Python.

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <algorithm>
#include <cmath>

#include <swipe_gesture.h>

using ituner::core::SwipeConfig;
using ituner::core::SwipeRepeatState;
using ituner::core::SwipeZoomState;

namespace {

/// `rf_canvas_width()` for the installed LCD mode: `DESKTOP_1280_MAIN_W` with
/// `LCD_800_MODE` on. The inertia rows in the capture read it unpatched.
constexpr double kCanvasWidthPx = 1024.0;

QJsonObject golden() {
    const QString path = QString::fromLatin1(ITUNER_QT_SOURCE_ROOT)
                         + QStringLiteral("/tests/golden/swipe_gesture_expected.json");
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

bool closeEnough(double produced, double expected) {
    const double scale = std::max(1.0, std::max(std::abs(produced), std::abs(expected)));
    return std::abs(produced - expected) <= 1e-9 * scale;
}

QString describe(double produced, double expected) {
    return QStringLiteral("expected %1, produced %2")
        .arg(expected, 0, 'g', 17)
        .arg(produced, 0, 'g', 17);
}

void requireClose(double produced, double expected, const QString &context) {
    QVERIFY2(closeEnough(produced, expected),
             qPrintable(QStringLiteral("%1: %2").arg(context, describe(produced, expected))));
}

/// Overlay the keys present in `object` onto `config`.
void applyJson(SwipeConfig &config, const QJsonObject &object) {
    auto d = [&](const char *key, double fallback) {
        return object.contains(QLatin1String(key))
                   ? object.value(QLatin1String(key)).toDouble()
                   : fallback;
    };
    auto i = [&](const char *key, int fallback) {
        return object.contains(QLatin1String(key))
                   ? object.value(QLatin1String(key)).toInt()
                   : fallback;
    };
    config.tapPx = i("tap_px", config.tapPx);
    config.swipeStartPx = i("swipe_start_px", config.swipeStartPx);
    config.fineSensitivity = d("fine_sensitivity", config.fineSensitivity);
    config.finePxS = d("fine_px_s", config.finePxS);
    config.slowSensitivity = d("slow_sensitivity", config.slowSensitivity);
    config.fastSensitivity = d("fast_sensitivity", config.fastSensitivity);
    config.fastPxS = d("fast_px_s", config.fastPxS);
    config.fastZoomPxS = d("fast_zoom_px_s", config.fastZoomPxS);
    config.fastZoomDistancePx = i("fast_zoom_distance_px", config.fastZoomDistancePx);
    config.fastZoomOut = i("fast_zoom_out", config.fastZoomOut);
    config.fastZoomMin = i("fast_zoom_min", config.fastZoomMin);
    config.autoZoomBudget = i("auto_zoom_budget", config.autoZoomBudget);
    config.repeatWindowS = d("repeat_window_s", config.repeatWindowS);
    config.repeatBoost = d("repeat_boost", config.repeatBoost);
    config.repeatMax = i("repeat_max", config.repeatMax);
    config.repeatZoomOut = i("repeat_zoom_out", config.repeatZoomOut);
    config.repeatZoomThreshold = i("repeat_zoom_threshold", config.repeatZoomThreshold);
    config.repeatZoomMin = i("repeat_zoom_min", config.repeatZoomMin);
    config.inertiaMinPxS = d("inertia_min_px_s", config.inertiaMinPxS);
    config.inertiaStrength = d("inertia_strength", config.inertiaStrength);
    config.inertiaTau = d("inertia_tau", config.inertiaTau);
    config.maxZoom = i("max_zoom", config.maxZoom);
    config.stationZoom = i("station_zoom", config.stationZoom);
    config.tuneStepHz = i("tune_step_hz", config.tuneStepHz);
    if (object.contains(QStringLiteral("finger_tune_positional"))) {
        config.fingerTunePositional =
            object.value(QStringLiteral("finger_tune_positional")).toBool();
    }
    if (object.contains(QStringLiteral("invert_tune"))) {
        config.invertTune = object.value(QStringLiteral("invert_tune")).toBool();
    }
    if (object.contains(QStringLiteral("swipe_sensitivity"))) {
        const QJsonValue value = object.value(QStringLiteral("swipe_sensitivity"));
        config.swipeSensitivity = value.isNull() ? std::optional<double>{}
                                                 : std::optional<double>{value.toDouble()};
    }
}

/// The Python parser defaults, overlaid with a variant's overrides, then the
/// startup coercion block.
SwipeConfig variantConfig(const QJsonObject &root, const QString &variant) {
    SwipeConfig config;
    applyJson(config, root.value(QStringLiteral("base")).toObject());
    const QJsonObject overrides =
        root.value(QStringLiteral("variant_inputs")).toObject().value(variant).toObject();
    applyJson(config, overrides);
    return ituner::core::normalizeSwipeConfig(config);
}

void compareConfig(const SwipeConfig &config, const QJsonObject &expected, const QString &context) {
    auto di = [&](const char *key, int produced, const QString &field) {
        const int wanted = expected.value(QLatin1String(key)).toInt();
        QVERIFY2(produced == wanted,
                 qPrintable(QStringLiteral("%1.%2: expected %3, produced %4")
                                .arg(context, field)
                                .arg(wanted)
                                .arg(produced)));
    };
    auto dd = [&](const char *key, double produced, const QString &field) {
        requireClose(produced, expected.value(QLatin1String(key)).toDouble(),
                     QStringLiteral("%1.%2").arg(context, field));
    };
    dd("tap_px", config.tapPx, QStringLiteral("tap_px"));
    di("swipe_start_px", config.swipeStartPx, QStringLiteral("swipe_start_px"));
    dd("fine_sensitivity", config.fineSensitivity, QStringLiteral("fine_sensitivity"));
    dd("fine_px_s", config.finePxS, QStringLiteral("fine_px_s"));
    dd("slow_sensitivity", config.slowSensitivity, QStringLiteral("slow_sensitivity"));
    dd("fast_sensitivity", config.fastSensitivity, QStringLiteral("fast_sensitivity"));
    dd("fast_px_s", config.fastPxS, QStringLiteral("fast_px_s"));
    dd("fast_zoom_px_s", config.fastZoomPxS, QStringLiteral("fast_zoom_px_s"));
    di("fast_zoom_distance_px", config.fastZoomDistancePx,
       QStringLiteral("fast_zoom_distance_px"));
    di("fast_zoom_out", config.fastZoomOut, QStringLiteral("fast_zoom_out"));
    di("fast_zoom_min", config.fastZoomMin, QStringLiteral("fast_zoom_min"));
    di("auto_zoom_budget", config.autoZoomBudget, QStringLiteral("auto_zoom_budget"));
    dd("repeat_window_s", config.repeatWindowS, QStringLiteral("repeat_window_s"));
    dd("repeat_boost", config.repeatBoost, QStringLiteral("repeat_boost"));
    di("repeat_max", config.repeatMax, QStringLiteral("repeat_max"));
    di("repeat_zoom_out", config.repeatZoomOut, QStringLiteral("repeat_zoom_out"));
    di("repeat_zoom_threshold", config.repeatZoomThreshold,
       QStringLiteral("repeat_zoom_threshold"));
    di("repeat_zoom_min", config.repeatZoomMin, QStringLiteral("repeat_zoom_min"));
    dd("inertia_min_px_s", config.inertiaMinPxS, QStringLiteral("inertia_min_px_s"));
    dd("inertia_strength", config.inertiaStrength, QStringLiteral("inertia_strength"));
    dd("inertia_tau", config.inertiaTau, QStringLiteral("inertia_tau"));
    di("max_zoom", config.maxZoom, QStringLiteral("max_zoom"));
    di("station_zoom", config.stationZoom, QStringLiteral("station_zoom"));
    di("tune_step_hz", config.tuneStepHz, QStringLiteral("tune_step_hz"));
    const bool positional =
        expected.value(QStringLiteral("finger_tune_positional")).toBool();
    QVERIFY2(config.fingerTunePositional == positional,
             qPrintable(QStringLiteral("%1.finger_tune_positional").arg(context)));
    const bool invert = expected.value(QStringLiteral("invert_tune")).toBool();
    QVERIFY2(config.invertTune == invert,
             qPrintable(QStringLiteral("%1.invert_tune").arg(context)));
}

}  // namespace

class SwipeGestureTest : public QObject {
    Q_OBJECT

private slots:
    void normalizationMatchesPython();
    void sensitivityMatchesPython();
    void tuningMatchesPython();
    void velocityMatchesPython();
    void repeatMatchesPython();
    void boostMatchesPython();
    void autoZoomMatchesPython();
    void inertiaMatchesPython();
    void constantsMatchPython();
};

void SwipeGestureTest::normalizationMatchesPython() {
    const QJsonObject root = golden();
    const QJsonObject normalized = root.value(QStringLiteral("normalized")).toObject();
    QVERIFY2(!normalized.isEmpty(), "the swipe goldens are missing");

    for (auto it = normalized.constBegin(); it != normalized.constEnd(); ++it) {
        compareConfig(variantConfig(root, it.key()), it.value().toObject(), it.key());
    }
}

void SwipeGestureTest::sensitivityMatchesPython() {
    const QJsonObject root = golden();
    const QJsonArray rows = root.value(QStringLiteral("sensitivity")).toArray();
    QVERIFY2(!rows.isEmpty(), "the sensitivity sweep is missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString variant = row.value(QStringLiteral("variant")).toString();
        const double speed = row.value(QStringLiteral("speed")).toDouble();
        const double expected = row.value(QStringLiteral("sensitivity")).toDouble();
        const SwipeConfig config = variantConfig(root, variant);
        requireClose(ituner::core::swipeEffectiveSensitivity(speed, config), expected,
                     QStringLiteral("sensitivity[%1 @ %2]").arg(variant).arg(speed));
    }
}

void SwipeGestureTest::tuningMatchesPython() {
    const QJsonObject root = golden();
    const QJsonObject tuning = root.value(QStringLiteral("tuning")).toObject();
    QVERIFY2(!tuning.isEmpty(), "the tuning rows are missing");

    SwipeConfig config = ituner::core::normalizeSwipeConfig(SwipeConfig{});

    for (const QJsonValue &entry : tuning.value(QStringLiteral("delta")).toArray()) {
        const QJsonObject row = entry.toObject();
        const double canvas = row.value(QStringLiteral("canvas")).toDouble();
        const double produced = ituner::core::retuneDeltaFromDrag(
            row.value(QStringLiteral("delta_px")).toDouble(),
            row.value(QStringLiteral("span_khz")).toDouble(), canvas,
            row.value(QStringLiteral("invert")).toBool(),
            row.value(QStringLiteral("sensitivity")).toDouble());
        requireClose(produced, row.value(QStringLiteral("khz")).toDouble(),
                     QStringLiteral("delta %1px canvas %2")
                         .arg(row.value(QStringLiteral("delta_px")).toDouble())
                         .arg(canvas));
    }

    for (const QJsonValue &entry : tuning.value(QStringLiteral("from_drag")).toArray()) {
        const QJsonObject row = entry.toObject();
        const double canvas = row.value(QStringLiteral("canvas")).toDouble();
        const double produced = ituner::core::retuneFromDrag(
            row.value(QStringLiteral("start_freq")).toDouble(),
            row.value(QStringLiteral("start_x")).toDouble(),
            row.value(QStringLiteral("x")).toDouble(),
            row.value(QStringLiteral("span_khz")).toDouble(), canvas,
            row.value(QStringLiteral("invert")).toBool(),
            row.value(QStringLiteral("sensitivity")).toDouble());
        requireClose(produced, row.value(QStringLiteral("khz")).toDouble(),
                     QStringLiteral("from_drag x=%1 canvas %2")
                         .arg(row.value(QStringLiteral("x")).toDouble())
                         .arg(canvas));
    }

    for (const QJsonValue &entry : tuning.value(QStringLiteral("from_tap")).toArray()) {
        const QJsonObject row = entry.toObject();
        const double canvas = row.value(QStringLiteral("canvas")).toDouble();
        const double produced = ituner::core::retuneFromTap(
            row.value(QStringLiteral("x")).toDouble(),
            row.value(QStringLiteral("freq")).toDouble(),
            row.value(QStringLiteral("span_khz")).toDouble(), canvas);
        requireClose(produced, row.value(QStringLiteral("khz")).toDouble(),
                     QStringLiteral("from_tap x=%1 canvas %2")
                         .arg(row.value(QStringLiteral("x")).toDouble())
                         .arg(canvas));
    }

    for (const QJsonValue &entry : tuning.value(QStringLiteral("snap")).toArray()) {
        const QJsonObject row = entry.toObject();
        const double produced = ituner::core::snapFrequencyKhz(
            row.value(QStringLiteral("freq")).toDouble(),
            row.value(QStringLiteral("step_hz")).toInt());
        requireClose(produced, row.value(QStringLiteral("khz")).toDouble(),
                     QStringLiteral("snap freq=%1 step=%2")
                         .arg(row.value(QStringLiteral("freq")).toDouble())
                         .arg(row.value(QStringLiteral("step_hz")).toInt()));
    }

    for (const QJsonValue &entry : tuning.value(QStringLiteral("drag_span")).toArray()) {
        const QJsonObject row = entry.toObject();
        const double produced = ituner::core::receiverDragSpan(
            row.value(QStringLiteral("span_khz")).toDouble(),
            row.value(QStringLiteral("receiver_type")).toString(),
            row.value(QStringLiteral("fm_step_hz")).toDouble(),
            row.value(QStringLiteral("canvas")).toDouble());
        requireClose(produced, row.value(QStringLiteral("khz")).toDouble(),
                     QStringLiteral("drag_span %1 fm=%2 canvas %3")
                         .arg(row.value(QStringLiteral("receiver_type")).toString())
                         .arg(row.value(QStringLiteral("fm_step_hz")).toInt())
                         .arg(row.value(QStringLiteral("canvas")).toInt()));
    }

    for (const QJsonValue &entry : tuning.value(QStringLiteral("finger_step")).toArray()) {
        const QJsonObject row = entry.toObject();
        const int produced = ituner::core::fingerTuneStepHz(
            row.value(QStringLiteral("zoom")).toInt(),
            row.value(QStringLiteral("base_step")).toInt(),
            row.value(QStringLiteral("canvas")).toDouble());
        QCOMPARE(produced, row.value(QStringLiteral("hz")).toInt());
    }

    for (const QJsonValue &entry : tuning.value(QStringLiteral("receiver_step")).toArray()) {
        const QJsonObject row = entry.toObject();
        const int produced = ituner::core::receiverTuneStepHz(
            row.value(QStringLiteral("zoom")).toInt(),
            row.value(QStringLiteral("kiwi_step")).toInt(),
            row.value(QStringLiteral("receiver_type")).toString(),
            row.value(QStringLiteral("canvas")).toDouble(),
            row.value(QStringLiteral("fm_step_hz")).toDouble());
        QCOMPARE(produced, row.value(QStringLiteral("hz")).toInt());
    }

    for (const QJsonValue &entry : tuning.value(QStringLiteral("deliberate")).toArray()) {
        const QJsonObject row = entry.toObject();
        const QJsonArray start = row.value(QStringLiteral("start")).toArray();
        const bool produced = ituner::core::isDeliberateWaterfallDrag(
            start.at(0).toDouble(), start.at(1).toDouble(),
            row.value(QStringLiteral("x")).toDouble(),
            row.value(QStringLiteral("y")).toDouble(), config);
        QCOMPARE(produced, row.value(QStringLiteral("deliberate")).toBool());
    }
}

void SwipeGestureTest::velocityMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("velocity")).toArray();
    QVERIFY2(!rows.isEmpty(), "the velocity rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double produced = ituner::core::updateSwipeVelocity(
            row.value(QStringLiteral("instant")).toDouble(),
            row.value(QStringLiteral("current")).toDouble());
        requireClose(produced, row.value(QStringLiteral("velocity")).toDouble(),
                     QStringLiteral("velocity instant=%1")
                         .arg(row.value(QStringLiteral("instant")).toDouble()));
    }
}

void SwipeGestureTest::repeatMatchesPython() {
    const QJsonObject root = golden();
    const QJsonArray rows = root.value(QStringLiteral("repeat")).toArray();
    QVERIFY2(!rows.isEmpty(), "the repeat rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const SwipeConfig config =
            variantConfig(root, row.value(QStringLiteral("variant")).toString());
        SwipeRepeatState state;
        int index = 0;
        for (const QJsonValue &gesture : row.value(QStringLiteral("gestures")).toArray()) {
            const QJsonArray pair = gesture.toArray();
            ituner::core::beginSwipeRepeat(state, pair.at(0).toInt() != 0,
                                           pair.at(1).toDouble(), config);
            const QJsonObject expected =
                row.value(QStringLiteral("states")).toArray().at(index).toObject();
            QCOMPARE(state.count, expected.value(QStringLiteral("count")).toInt());
            requireClose(state.activeBoost, expected.value(QStringLiteral("boost")).toDouble(),
                         QStringLiteral("repeat %1 step %2")
                             .arg(row.value(QStringLiteral("variant")).toString())
                             .arg(index));
            ++index;
        }
        QCOMPARE(index, row.value(QStringLiteral("states")).toArray().size());
    }
}

void SwipeGestureTest::boostMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("boost")).toArray();
    QVERIFY2(!rows.isEmpty(), "the boost rows are missing");

    const SwipeConfig config = ituner::core::normalizeSwipeConfig(SwipeConfig{});
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double produced = ituner::core::swipeLiveBoost(
            row.value(QStringLiteral("active_boost")).toDouble(),
            row.value(QStringLiteral("velocity")).toDouble(), config);
        requireClose(produced, row.value(QStringLiteral("boost")).toDouble(),
                     QStringLiteral("boost active=%1 v=%2")
                         .arg(row.value(QStringLiteral("active_boost")).toDouble())
                         .arg(row.value(QStringLiteral("velocity")).toDouble()));
    }
}

void SwipeGestureTest::autoZoomMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("zoom")).toArray();
    QVERIFY2(!rows.isEmpty(), "the zoom rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        SwipeConfig config = ituner::core::normalizeSwipeConfig(SwipeConfig{});
        config.fingerTunePositional = row.value(QStringLiteral("positional")).toBool();

        SwipeZoomState state;
        int zoom = row.value(QStringLiteral("start_zoom")).toInt();
        int used = 0;
        int index = 0;
        for (const QJsonValue &step : row.value(QStringLiteral("steps")).toArray()) {
            const ituner::core::SwipeZoomOutcome outcome = ituner::core::applySwipeAutoZoom(
                state, zoom, row.value(QStringLiteral("velocity")).toDouble(),
                row.value(QStringLiteral("travel")).toDouble(),
                row.value(QStringLiteral("repeat_count")).toInt(), config);
            zoom = outcome.zoom;
            used += outcome.appliedLevels;

            const QJsonObject expected = step.toObject();
            QCOMPARE(zoom, expected.value(QStringLiteral("zoom")).toInt());
            QCOMPARE(used, expected.value(QStringLiteral("used")).toInt());
            ++index;
        }
        QCOMPARE(index, row.value(QStringLiteral("steps")).toArray().size());
    }
}

void SwipeGestureTest::inertiaMatchesPython() {
    const QJsonObject root = golden();
    const QJsonArray rows = root.value(QStringLiteral("inertia")).toArray();
    QVERIFY2(!rows.isEmpty(), "the inertia rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        QJsonObject overrides;
        overrides.insert(QStringLiteral("inertia_strength"),
                         row.value(QStringLiteral("strength")));
        overrides.insert(QStringLiteral("inertia_tau"), row.value(QStringLiteral("tau")));
        SwipeConfig config;
        applyJson(config, root.value(QStringLiteral("base")).toObject());
        applyJson(config, overrides);
        config = ituner::core::normalizeSwipeConfig(config);

        const double velocity = row.value(QStringLiteral("velocity")).toDouble();
        const double span = row.value(QStringLiteral("span_khz")).toDouble();
        const bool invert = row.value(QStringLiteral("invert")).toBool();
        const double tau = config.inertiaTau;

        double value = ituner::core::swipeInertiaVelocityKhzS(
            velocity, span, kCanvasWidthPx, invert, config);
        requireClose(value, row.value(QStringLiteral("khz_s")).toDouble(),
                     QStringLiteral("inertia v=%1").arg(velocity));

        const QJsonArray decays = row.value(QStringLiteral("decays")).toArray();
        for (const QJsonValue &decay : decays) {
            value = ituner::core::decayInertiaVelocity(value, 0.05, tau);
            requireClose(value, decay.toDouble(), QStringLiteral("inertia decay v=%1").arg(velocity));
        }
    }
}

void SwipeGestureTest::constantsMatchPython() {
    const QJsonObject root = golden();
    QCOMPARE(ituner::core::waterfallDragStartPx(),
             root.value(QStringLiteral("drag_start_px")).toInt());
    requireClose(ituner::core::waterfallHorizontalDragRatio(),
                 root.value(QStringLiteral("horizontal_drag_ratio")).toDouble(),
                 QStringLiteral("horizontal_drag_ratio"));

    // The FM-DX accepted steps, and the fallback for anything else: Python
    // `fm_step if fm_step in FMDX_TUNE_STEPS_HZ else 100_000`.
    for (const QJsonValue &step : root.value(QStringLiteral("fmdx_tune_steps_hz")).toArray()) {
        QCOMPARE(ituner::core::receiverTuneStepHz(0, 100, QStringLiteral("fmdx"),
                                                  kCanvasWidthPx, step.toDouble()),
                 static_cast<int>(step.toDouble()));
    }
    QCOMPARE(ituner::core::receiverTuneStepHz(0, 100, QStringLiteral("fmdx"), kCanvasWidthPx,
                                              25000.0),
             100000);
}

QTEST_MAIN(SwipeGestureTest)
#include "tst_swipe_gesture.moc"
