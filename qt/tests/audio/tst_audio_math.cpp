// Parity tests for the audio PCM resampler and the S-meter display mapping.
//
// Every expectation comes from `qt/tests/golden/audio_math_expected.json`,
// produced by `qt/tests/parity/capture_audio_math.py` calling the real
// `resample_mono_s16le` in `UI/fmdx.py` and the S-meter maps in
// `UI/kiwi_gl_display.py`. The Python sources stay the specification, so nothing
// here is hand-written against a reading of them.

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <algorithm>
#include <cmath>

#include <audio_math.h>

namespace {

QJsonObject golden() {
    const QString path = QString::fromLatin1(ITUNER_QT_SOURCE_ROOT)
                         + QStringLiteral("/tests/golden/audio_math_expected.json");
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

}  // namespace

class AudioMathTest : public QObject {
    Q_OBJECT

private slots:
    void resampleMatchesPython();
    void smeterPositionMatchesPython();
    void smeterInverseMatchesPython();
    void constantsMatchPython();
};

void AudioMathTest::resampleMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("resample")).toArray();
    QVERIFY2(!rows.isEmpty(), "the audio goldens are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const int sourceRate = row.value(QStringLiteral("source_rate")).toInt();
        const int targetRate = row.value(QStringLiteral("target_rate")).toInt();
        const QByteArray input = QByteArray::fromHex(row.value(QStringLiteral("in_hex")).toString().toLatin1());
        const QByteArray produced = ituner::audio::resampleMonoS16le(input, sourceRate, targetRate);
        const QByteArray expected = QByteArray::fromHex(row.value(QStringLiteral("out_hex")).toString().toLatin1());
        QVERIFY2(produced == expected,
                 qPrintable(QStringLiteral("resample %1 -> %2:\n  expected %3\n  produced %4")
                                .arg(sourceRate)
                                .arg(targetRate)
                                .arg(QString::fromLatin1(expected.toHex()),
                                     QString::fromLatin1(produced.toHex()))));
    }
}

void AudioMathTest::smeterPositionMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("smeter_position")).toArray();
    QVERIFY2(!rows.isEmpty(), "the S-meter position rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double dbm = row.value(QStringLiteral("dbm")).toDouble();
        const double produced = ituner::audio::smeterSegmentPosition(dbm);
        const double expected = row.value(QStringLiteral("position")).toDouble();
        QVERIFY2(closeEnough(produced, expected),
                 qPrintable(QStringLiteral("smeterSegmentPosition(%1): %2")
                                .arg(dbm, 0, 'g', 17)
                                .arg(describe(produced, expected))));
    }
}

void AudioMathTest::smeterInverseMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("smeter_dbm")).toArray();
    QVERIFY2(!rows.isEmpty(), "the S-meter dBm rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double position = row.value(QStringLiteral("position")).toDouble();
        const double produced = ituner::audio::smeterDbmAtSegment(position);
        const double expected = row.value(QStringLiteral("dbm")).toDouble();
        QVERIFY2(closeEnough(produced, expected),
                 qPrintable(QStringLiteral("smeterDbmAtSegment(%1): %2")
                                .arg(position, 0, 'g', 17)
                                .arg(describe(produced, expected))));
    }
}

void AudioMathTest::constantsMatchPython() {
    const QJsonObject constants = golden().value(QStringLiteral("constants")).toObject();
    auto expect = [&](const char *key, double produced) {
        const double wanted = constants.value(QLatin1String(key)).toDouble();
        QVERIFY2(produced == wanted,
                 qPrintable(QStringLiteral("%1: expected %2, produced %3")
                                .arg(QString::fromLatin1(key))
                                .arg(wanted)
                                .arg(produced)));
    };
    expect("floor_dbm", ituner::audio::smeterFloorDbm());
    expect("s9_dbm", ituner::audio::smeterS9Dbm());
    expect("plus20_dbm", ituner::audio::smeterPlus20Dbm());
    expect("ceiling_dbm", ituner::audio::smeterCeilingDbm());
    expect("s1_s9_segments", ituner::audio::smeterS1ToS9Segments());
    expect("s9_plus20_segments", ituner::audio::smeterS9ToPlus20Segments());
    expect("plus20_plus40_segments", ituner::audio::smeterPlus20ToPlus40Segments());
    QCOMPARE(ituner::audio::smeterTotalSegments(), 36);
}

QTEST_MAIN(AudioMathTest)
#include "tst_audio_math.moc"
