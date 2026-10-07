// Parity and unit tests for the waterfall colour ramp, level tracking and ring.
//
// The colour and level expectations come from
// `qt/tests/golden/waterfall_palette_expected.json`, produced by
// `qt/tests/parity/capture_waterfall_palette.py` calling the real Python
// `make_waterfall_mapper()`, `waterfall_line()` and `WaterfallLeveler`. The
// capture passes `width == len(samples)` so PIL's resize is the identity and the
// returned pixels are exactly the normalised levels through the palette; that is
// why an exact comparison is possible even though this port does not reproduce
// PIL's resampling kernel.
//
// The ring cases are source-derived unit tests: `WaterfallTexture.__init__` needs
// a GL context, so the Python class cannot be driven headlessly for goldens.

#include <QCryptographicHash>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <algorithm>
#include <cmath>

#include <waterfall_model.h>
#include <waterfall_palette.h>

using ituner::core::WaterfallLeveler;
using ituner::core::WaterfallPalette;
using ituner::core::WaterfallQueue;
using ituner::core::WaterfallRing;

namespace {

QJsonObject golden() {
    const QString path = QString::fromLatin1(ITUNER_QT_SOURCE_ROOT)
                         + QStringLiteral("/tests/golden/waterfall_palette_expected.json");
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

QList<int> intList(const QJsonArray &values) {
    QList<int> result;
    result.reserve(values.size());
    for (const QJsonValue &value : values) {
        result.append(value.toInt());
    }
    return result;
}

/// Strip the alpha channel: the Python rows are RGB, this port emits RGBA.
QByteArray rgbOnly(const QByteArray &rgba) {
    QByteArray rgb;
    rgb.reserve(rgba.size() / 4 * 3);
    for (int index = 0; index + 3 < rgba.size(); index += 4) {
        rgb.append(rgba.mid(index, 3));
    }
    return rgb;
}

bool closeEnough(double produced, double expected) {
    const double scale = std::max(1.0, std::max(std::abs(produced), std::abs(expected)));
    return std::abs(produced - expected) <= 1e-12 * scale;
}

}  // namespace

class WaterfallPaletteTest : public QObject {
    Q_OBJECT

private slots:
    void paletteTablesMatchPython();
    void emptyRowUsesTheVacuumColour();
    void rowsMatchPython();
    void levelerMatchesPython();
    void ringMovesUpwardAndKeepsMetadata();
    void ringRejectsMalformedRowsWithoutStopping();
    void queueHonoursRowPixels();
};

void WaterfallPaletteTest::paletteTablesMatchPython() {
    const QJsonObject palette = golden().value(QStringLiteral("palette")).toObject();
    QVERIFY2(!palette.isEmpty(), "the waterfall goldens are missing");

    const WaterfallPalette produced = ituner::core::kiwiPalette();
    const QList<int> red = intList(palette.value(QStringLiteral("red")).toArray());
    const QList<int> green = intList(palette.value(QStringLiteral("green")).toArray());
    const QList<int> blue = intList(palette.value(QStringLiteral("blue")).toArray());

    QCOMPARE(produced.size(), palette.value(QStringLiteral("size")).toInt());
    QCOMPARE(produced.red, red);
    QCOMPARE(produced.green, green);
    QCOMPARE(produced.blue, blue);
}

void WaterfallPaletteTest::emptyRowUsesTheVacuumColour() {
    const QJsonArray expected = golden().value(QStringLiteral("empty_row_rgb")).toArray();
    QByteArray expectedBytes;
    for (const QJsonValue &value : expected) {
        expectedBytes.append(static_cast<char>(value.toInt()));
    }
    const QByteArray produced = rgbOnly(ituner::core::emptyRowRgba(expectedBytes.size() / 3));
    QCOMPARE(produced, expectedBytes);
}

void WaterfallPaletteTest::rowsMatchPython() {
    const QJsonArray rows = golden().value(QStringLiteral("rows")).toArray();
    QVERIFY(!rows.isEmpty());
    const WaterfallPalette palette = ituner::core::kiwiPalette();

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QList<int> samples = intList(row.value(QStringLiteral("samples")).toArray());
        const double floor = row.value(QStringLiteral("floor")).toDouble();
        const double ceiling = row.value(QStringLiteral("ceiling")).toDouble();
        const int width = row.value(QStringLiteral("width")).toInt();

        const QByteArray levels = ituner::core::normalizeLevels(samples, floor, ceiling);
        const QByteArray rgb =
            rgbOnly(ituner::core::renderRowRgba(levels, width, palette));

        const QString label = QStringLiteral("samples %1 floor %2 ceiling %3")
                                  .arg(samples.size())
                                  .arg(floor)
                                  .arg(ceiling);
        QCOMPARE(rgb.size(), row.value(QStringLiteral("rgb_length")).toInt());

        const QString expectedHash = row.value(QStringLiteral("sha256")).toString();
        const QString producedHash =
            QString::fromLatin1(QCryptographicHash::hash(rgb, QCryptographicHash::Sha256).toHex());
        QVERIFY2(producedHash == expectedHash,
                 qPrintable(QStringLiteral("%1: row bytes differ (%2)")
                                .arg(label, producedHash.left(16))));

        if (row.contains(QStringLiteral("rgb_hex"))) {
            QCOMPARE(rgb.toHex(), row.value(QStringLiteral("rgb_hex")).toString().toLatin1());
        }
        QByteArray expectedFirst;
        for (const QJsonValue &value : row.value(QStringLiteral("first_bytes")).toArray()) {
            expectedFirst.append(static_cast<char>(value.toInt()));
        }
        QCOMPARE(rgb.left(expectedFirst.size()), expectedFirst);
    }
}

void WaterfallPaletteTest::levelerMatchesPython() {
    const QJsonArray cases = golden().value(QStringLiteral("levelers")).toArray();
    QVERIFY(!cases.isEmpty());

    for (const QJsonValue &entry : cases) {
        const QJsonObject item = entry.toObject();
        WaterfallLeveler leveler(item.value(QStringLiteral("floor")).toDouble(),
                                 item.value(QStringLiteral("ceiling")).toDouble(),
                                 item.value(QStringLiteral("auto")).toBool());
        const QJsonArray steps = item.value(QStringLiteral("steps")).toArray();
        const QJsonArray expected = item.value(QStringLiteral("expected")).toArray();
        QCOMPARE(steps.size(), expected.size());

        for (int index = 0; index < steps.size(); ++index) {
            const QList<int> samples = intList(steps.at(index).toArray());
            const QPair<double, double> produced = leveler.levelsFor(samples);
            const double expectedFloor = expected.at(index).toArray().at(0).toDouble();
            const double expectedCeiling = expected.at(index).toArray().at(1).toDouble();
            QVERIFY2(closeEnough(produced.first, expectedFloor),
                     qPrintable(QStringLiteral("step %1 floor: expected %2, produced %3")
                                    .arg(index)
                                    .arg(expectedFloor, 0, 'g', 17)
                                    .arg(produced.first, 0, 'g', 17)));
            QVERIFY2(closeEnough(produced.second, expectedCeiling),
                     qPrintable(QStringLiteral("step %1 ceiling: expected %2, produced %3")
                                    .arg(index)
                                    .arg(expectedCeiling, 0, 'g', 17)
                                    .arg(produced.second, 0, 'g', 17)));
        }
    }
}

void WaterfallPaletteTest::ringMovesUpwardAndKeepsMetadata() {
    WaterfallRing ring(8);
    const QByteArray rowA(4, 'a');
    const QByteArray rowB(4, 'b');

    QVERIFY(ring.pushLine(rowA, 4, 7000.0, 30.0));
    QCOMPARE(ring.newestIndex(), 7);
    QCOMPARE(ring.rowAtAge(0), rowA);
    QVERIFY(ring.rowAtAge(1).isEmpty());
    QVERIFY(ring.pushLine(rowB, 4, 7001.0, 31.0));
    QCOMPARE(ring.newestIndex(), 6);
    QCOMPARE(ring.rowsWritten(), 2);

    // Age 0 is the newest row, and age walks toward older rows.
    QCOMPARE(ring.indexForAge(0), 6);
    QCOMPARE(ring.indexForAge(1), 7);
    // `ageAtIndex` is the inverse the renderer walks with, so a slot can keep its
    // texture while its screen position moves.
    QCOMPARE(ring.ageAtIndex(6), 0);
    QCOMPARE(ring.ageAtIndex(7), 1);
    QCOMPARE(ring.ageAtIndex(ring.indexForAge(3)), 3);
    QCOMPARE(ring.centerKhzAtAge(0).value_or(0.0), 7001.0);
    QCOMPARE(ring.centerKhzAtAge(1).value_or(0.0), 7000.0);
    // Age 1 is the row pushed first, with its own span.
    QCOMPARE(ring.spanKhzAtAge(1).value_or(0.0), 30.0);
    QCOMPARE(ring.spanKhzAtAge(0).value_or(0.0), 31.0);
    // Beyond what was written there is nothing to report.
    QVERIFY(!ring.centerKhzAtAge(2).has_value());

    // The cursor wraps, so nine rows on an eight-row ring reuse one slot.
    for (int index = 0; index < 9; ++index) {
        QVERIFY(ring.pushLine(rowA, 4));
    }
    QCOMPARE(ring.rowsWritten(), 8);
    ring.clear();
    QCOMPARE(ring.rowsWritten(), 0);
    QCOMPARE(ring.newestIndex(), 0);
    QVERIFY(!ring.centerKhzAtAge(0).has_value());
}

void WaterfallPaletteTest::ringRejectsMalformedRowsWithoutStopping() {
    WaterfallRing ring(4);
    const QByteArray good(4, 'g');
    const QByteArray bad(3, 'x');

    QVERIFY(ring.pushLine(good, 4));
    const int before = ring.newestIndex();
    QVERIFY(!ring.pushLine(bad, 4));
    // The cursor and the metadata advance even for a rejected row, exactly as
    // WaterfallTexture.push_line does; only the pixels are dropped.
    QCOMPARE(ring.newestIndex(), (before - 1 + 4) % 4);
    QCOMPARE(ring.rowsWritten(), 1);
    // The rejected row leaves its slot alone, so the renderer shows history
    // rather than a hole where the row would have been.
    QVERIFY(ring.rowAtAge(0).isEmpty());
    // Fill every slot, then reject a row: the slot it claims keeps the older
    // pixels, exactly as the GL texture would.
    for (int index = 0; index < 4; ++index) {
        QVERIFY(ring.pushLine(good, 4));
    }
    QCOMPARE(ring.rowsWritten(), 4);
    const int filledCursor = ring.newestIndex();
    QVERIFY(!ring.pushLine(bad, 4));
    QCOMPARE(ring.newestIndex(), (filledCursor - 1 + 4) % 4);
    QCOMPARE(ring.rowAtAge(0), good);
    // The rejected row carried no metadata, so the slot it claimed is empty even
    // though the cursor moved onto it.
    QVERIFY(!ring.centerKhzAtAge(0).has_value());
    // A display must never die because one producer row was malformed.
    QVERIFY(ring.pushLine(good, 4));
}

void WaterfallPaletteTest::queueHonoursRowPixels() {
    WaterfallQueue queue;
    const QByteArray line(2, 'q');

    queue.enqueue(line, 3, 100);
    QCOMPARE(queue.pendingCount(), 3);
    queue.clear();
    QCOMPARE(queue.pendingCount(), 0);

    // The row count is clamped into 1..usable height, as the Python clamp does.
    queue.enqueue(line, 0, 100);
    QCOMPARE(queue.pendingCount(), 1);
    queue.clear();
    queue.enqueue(line, 500, 12);
    QCOMPARE(queue.pendingCount(), 12);
    queue.clear();
    // A zero-height region still yields one row rather than a negative count.
    queue.enqueue(line, 4, 0);
    QCOMPARE(queue.pendingCount(), 1);

    queue.clear();
    queue.enqueue(QByteArray(2, '1'), 1, 100);
    queue.enqueue(QByteArray(2, '2'), 1, 100);
    QByteArray taken;
    QVERIFY(queue.popNext(&taken));
    QCOMPARE(taken, QByteArray(2, '1'));
    QVERIFY(queue.popNext(&taken));
    QCOMPARE(taken, QByteArray(2, '2'));
    QVERIFY(!queue.popNext(&taken));
}

QTEST_MAIN(WaterfallPaletteTest)
#include "tst_waterfall_palette.moc"
