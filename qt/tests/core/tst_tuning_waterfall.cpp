// Parity tests for the tuning/zoom math and the waterfall model.
//
// Every expected value comes from `qt/tests/golden/tuning_waterfall_expected.json`,
// which `qt/tests/parity/capture_tuning_waterfall.py` produces by calling the real
// Python functions in `UI/kiwi_gl_display.py` and `UI/kiwi_live_display_fb.py`.
// The Python sources stay the specification, so no expectation here is
// hand-written against my reading of them.

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <algorithm>
#include <cmath>

#include <tuning.h>
#include <waterfall_model.h>

namespace {

QJsonObject golden() {
    const QString path = QString::fromLatin1(ITUNER_QT_SOURCE_ROOT)
                         + QStringLiteral("/tests/golden/tuning_waterfall_expected.json");
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

/// The values compared here all come from exact binary arithmetic (halving,
/// powers of two, exact slider fractions), so the tolerance is only there to keep
/// a formatting artefact from reading as a behaviour difference.
bool closeEnough(double produced, double expected) {
    const double scale = std::max(1.0, std::max(std::abs(produced), std::abs(expected)));
    return std::abs(produced - expected) <= 1e-9 * scale;
}

QString describe(double produced, double expected) {
    return QStringLiteral("expected %1, produced %2")
        .arg(expected, 0, 'g', 17)
        .arg(produced, 0, 'g', 17);
}

}  // namespace

class TuningWaterfallTest : public QObject {
    Q_OBJECT

private slots:
    void zoomLadderMatchesPython();
    void spanToZoomMatchesPython();
    void presentationFpsMatchesPython();
    void sliderMappingMatchesPython();
    void pythonRoundMatchesPython();
    void palettesNormalize();
    void defaultsAndLimitsMatchPython();
    void radioModesMatchPython();
    void sliderBoxIsParameterized();
};

void TuningWaterfallTest::zoomLadderMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("zoom")).toArray();
    QVERIFY2(!rows.isEmpty(), "the tuning goldens are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const int zoom = row.value(QStringLiteral("zoom")).toInt();
        const int level = row.value(QStringLiteral("level")).toInt();
        QCOMPARE(ituner::core::kiwiZoomLevel(zoom), level);
        QVERIFY2(closeEnough(ituner::core::zoomSourceSpanKhz(zoom),
                             row.value(QStringLiteral("source_span_khz")).toDouble()),
                 qPrintable(describe(ituner::core::zoomSourceSpanKhz(zoom),
                                     row.value(QStringLiteral("source_span_khz")).toDouble())));
        QVERIFY2(closeEnough(ituner::core::zoomToSpanKhz(zoom),
                             row.value(QStringLiteral("span_khz")).toDouble()),
                 qPrintable(QStringLiteral("zoom %1: %2")
                                .arg(zoom)
                                .arg(describe(ituner::core::zoomToSpanKhz(zoom),
                                              row.value(QStringLiteral("span_khz")).toDouble()))));
        QVERIFY2(closeEnough(ituner::core::digitalZoomFactor(zoom),
                             row.value(QStringLiteral("digital_factor")).toDouble()),
                 qPrintable(QStringLiteral("zoom %1 digital factor").arg(zoom)));
    }
}

void TuningWaterfallTest::spanToZoomMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("span_to_zoom")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double span = row.value(QStringLiteral("span_khz")).toDouble();
        const int expected = row.value(QStringLiteral("zoom")).toInt();
        QVERIFY2(ituner::core::spanToZoom(span) == expected,
                 qPrintable(QStringLiteral("span %1: expected zoom %2, produced %3")
                                .arg(span)
                                .arg(expected)
                                .arg(ituner::core::spanToZoom(span))));
    }
}

void TuningWaterfallTest::presentationFpsMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("presentation_fps")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString receiver = row.value(QStringLiteral("receiver")).toString();
        const int speed = row.value(QStringLiteral("speed")).toInt();
        const int rowPixels = row.value(QStringLiteral("row_pixels")).toInt();
        const double expected = row.value(QStringLiteral("fps")).toDouble();
        const double produced =
            ituner::core::waterfallPresentationFps(receiver, speed, rowPixels);
        QVERIFY2(closeEnough(produced, expected),
                 qPrintable(QStringLiteral("%1 speed %2 row pixels %3: %4")
                                .arg(receiver)
                                .arg(speed)
                                .arg(rowPixels)
                                .arg(describe(produced, expected))));
    }
}

void TuningWaterfallTest::sliderMappingMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("sliders")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double x = row.value(QStringLiteral("x")).toDouble();
        const double floorX0 = row.value(QStringLiteral("floor_box")).toArray().at(0).toDouble();
        const double floorX1 = row.value(QStringLiteral("floor_box")).toArray().at(1).toDouble();
        const double ceilingX0 = row.value(QStringLiteral("ceiling_box")).toArray().at(0).toDouble();
        const double ceilingX1 = row.value(QStringLiteral("ceiling_box")).toArray().at(1).toDouble();
        const double ceiling = row.value(QStringLiteral("ceiling")).toDouble();
        const double floor = row.value(QStringLiteral("floor")).toDouble();

        QVERIFY2(closeEnough(ituner::core::waterfallSliderFraction(x, floorX0, floorX1),
                             row.value(QStringLiteral("fraction_floor")).toDouble()),
                 qPrintable(QStringLiteral("x %1 floor fraction").arg(x)));
        QVERIFY2(closeEnough(ituner::core::waterfallSliderFraction(x, ceilingX0, ceilingX1),
                             row.value(QStringLiteral("fraction_ceiling")).toDouble()),
                 qPrintable(QStringLiteral("x %1 ceiling fraction").arg(x)));

        const int producedFloor = ituner::core::waterfallFloorAtX(x, floorX0, floorX1, ceiling);
        const int expectedFloor = row.value(QStringLiteral("floor_at_x")).toInt();
        QVERIFY2(producedFloor == expectedFloor,
                 qPrintable(QStringLiteral("x %1 ceiling %2: floor expected %3, produced %4")
                                .arg(x)
                                .arg(ceiling)
                                .arg(expectedFloor)
                                .arg(producedFloor)));

        const int producedCeiling =
            ituner::core::waterfallCeilingAtX(x, ceilingX0, ceilingX1, floor);
        const int expectedCeiling = row.value(QStringLiteral("ceiling_at_x")).toInt();
        QVERIFY2(producedCeiling == expectedCeiling,
                 qPrintable(QStringLiteral("x %1 floor %2: ceiling expected %3, produced %4")
                                .arg(x)
                                .arg(floor)
                                .arg(expectedCeiling)
                                .arg(producedCeiling)));
    }
}

void TuningWaterfallTest::pythonRoundMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("python_round")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double value = row.value(QStringLiteral("value")).toDouble();
        const int expected = row.value(QStringLiteral("rounded")).toInt();
        QVERIFY2(ituner::core::pythonRoundToInt(value) == expected,
                 qPrintable(QStringLiteral("round(%1): expected %2, produced %3")
                                .arg(value)
                                .arg(expected)
                                .arg(ituner::core::pythonRoundToInt(value))));
    }
}

void TuningWaterfallTest::palettesNormalize() {
    const QJsonArray rows = golden().value(QStringLiteral("palettes")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString requested = row.value(QStringLiteral("requested")).toString();
        QCOMPARE(ituner::core::normalizePalette(requested),
                 row.value(QStringLiteral("normalized")).toString());
    }
    QCOMPARE(ituner::core::waterfallPalettes(),
             QStringList({QStringLiteral("classic"), QStringLiteral("kiwi"),
                          QStringLiteral("ice")}));
}

void TuningWaterfallTest::defaultsAndLimitsMatchPython() {
    const QJsonObject defaults = golden().value(QStringLiteral("defaults")).toObject();
    QCOMPARE(ituner::core::waterfallDefaultFloor(),
             defaults.value(QStringLiteral("floor")).toInt());
    QCOMPARE(ituner::core::waterfallDefaultCeiling(),
             defaults.value(QStringLiteral("ceiling")).toInt());
    QCOMPARE(ituner::core::waterfallDefaultSpeed(),
             defaults.value(QStringLiteral("speed")).toInt());
    QCOMPARE(ituner::core::waterfallMaxSpeed(),
             defaults.value(QStringLiteral("max_speed")).toInt());
    QCOMPARE(ituner::core::waterfallDefaultPalette(),
             defaults.value(QStringLiteral("palette")).toString());

    const QJsonObject sourceFps = defaults.value(QStringLiteral("source_fps")).toObject();
    for (auto entry = sourceFps.constBegin(); entry != sourceFps.constEnd(); ++entry) {
        const int speed = entry.key().toInt();
        QVERIFY2(closeEnough(ituner::core::waterfallSourceFps(speed), entry.value().toDouble()),
                 qPrintable(QStringLiteral("speed %1 source fps").arg(speed)));
    }

    const QJsonObject root = golden();
    QCOMPARE(ituner::core::displayMaxZoom(), root.value(QStringLiteral("max_zoom")).toInt());
    QCOMPARE(ituner::core::kiwiMaxZoom(), root.value(QStringLiteral("kiwi_max_zoom")).toInt());
    QVERIFY(closeEnough(ituner::core::tuningMaxKhz(),
                        root.value(QStringLiteral("tuning_max_khz")).toDouble()));
    QVERIFY(closeEnough(ituner::core::fmdxMaxKhz(),
                        root.value(QStringLiteral("fmdx_max_khz")).toDouble()));
}

void TuningWaterfallTest::radioModesMatchPython() {
    QStringList modes = ituner::core::kiwiRadioModes();
    modes.sort();
    QJsonArray expected;
    for (const QString &mode : modes) {
        expected.append(mode);
    }
    QCOMPARE(expected, golden().value(QStringLiteral("radio_modes")).toArray());
    QVERIFY(ituner::core::isKiwiRadioMode(QStringLiteral("lsb")));
    QVERIFY(ituner::core::isKiwiRadioMode(QStringLiteral("IQ")));
    QVERIFY(!ituner::core::isKiwiRadioMode(QStringLiteral("FM")));
}

void TuningWaterfallTest::sliderBoxIsParameterized() {
    // The Python helpers read their box from module globals. This port takes the
    // edges as parameters so the QML sliders can supply their own, so the shape
    // of the mapping is checked on a box the goldens never saw.
    constexpr double kX0 = 100.0;
    constexpr double kX1 = 600.0;

    QCOMPARE(ituner::core::waterfallSliderFraction(kX0 - 50.0, kX0, kX1), 0.0);
    QCOMPARE(ituner::core::waterfallSliderFraction(kX1 + 50.0, kX0, kX1), 1.0);
    // The usable track is inset by 10 px at both ends.
    QCOMPARE(ituner::core::waterfallSliderFraction(kX0 + 10.0, kX0, kX1), 0.0);
    QCOMPARE(ituner::core::waterfallSliderFraction(kX1 - 10.0, kX0, kX1), 1.0);

    int previous = ituner::core::waterfallFloorAtX(kX0, kX0, kX1, 245.0);
    for (double x = kX0; x <= kX1; x += 5.0) {
        const int level = ituner::core::waterfallFloorAtX(x, kX0, kX1, 245.0);
        QVERIFY2(level >= previous, qPrintable(QStringLiteral("floor dipped at x %1").arg(x)));
        QVERIFY(level >= 40 && level <= 220);
        previous = level;
    }
    // A lower ceiling narrows the floor's range instead of crossing it.
    QCOMPARE(ituner::core::waterfallFloorAtX(kX1 + 50.0, kX0, kX1, 70.0), 40);
    QCOMPARE(ituner::core::waterfallCeilingAtX(kX0 - 50.0, kX0, kX1, 142.0), 172);
    QCOMPARE(ituner::core::waterfallCeilingAtX(kX1 + 50.0, kX0, kX1, 142.0), 255);
}

QTEST_MAIN(TuningWaterfallTest)
#include "tst_tuning_waterfall.moc"
