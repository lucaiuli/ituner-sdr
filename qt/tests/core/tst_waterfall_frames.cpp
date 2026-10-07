// The whole-frame waterfall test: palette, level tracking and row ordering
// together.
//
// The per-row palette suite pins one row at a time with a fixed floor and
// ceiling. This suite replays a captured row set through the real
// `WaterfallLeveler` and the real palette, builds the frame the renderer would
// upload, and compares the whole-frame checksum. A palette regression, a
// level-tracking regression or a row-order regression all break the frame hash,
// and the per-row hashes name the row that went wrong.
//
// The expectations come from `qt/tests/golden/waterfall_frames_expected.json`,
// captured by `qt/tests/parity/capture_waterfall_frames.py` calling the Python
// `WaterfallLeveler` and `waterfall_line()` at `width == len(samples)`, where
// PIL's resize is the identity. That last part is what makes an exact comparison
// possible: this port does not reproduce PIL's resampling kernel, so a scaled row
// is deliberately not bit-identical.

#include <QCryptographicHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <waterfall_model.h>
#include <waterfall_palette.h>

#include "golden_draw_list.h"

using ituner::core::WaterfallLeveler;
using ituner::core::WaterfallPalette;
using ituner::core::WaterfallQueue;

namespace {

QList<int> intList(const QJsonArray &values) {
    QList<int> result;
    result.reserve(values.size());
    for (const QJsonValue &value : values) {
        result.append(value.toInt());
    }
    return result;
}

/// Strip the alpha channel: the Python rows are RGB, the port emits RGBA.
QByteArray rgbOnly(const QByteArray &rgba) {
    QByteArray rgb;
    rgb.reserve(rgba.size() / 4 * 3);
    for (int index = 0; index + 3 < rgba.size(); index += 4) {
        rgb.append(rgba.mid(index, 3));
    }
    return rgb;
}

QString sha256(const QByteArray &bytes) {
    return QString::fromLatin1(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex());
}

}  // namespace

class WaterfallFramesTest : public QObject {
    Q_OBJECT

private slots:
    void capturedRowSetIsPresent();
    void streamedFrameMatchesPython();
    void emptyRowMatchesPython();
};

void WaterfallFramesTest::capturedRowSetIsPresent() {
    const QJsonObject golden =
        ituner::test::loadGolden(QStringLiteral("waterfall_frames_expected.json"));
    QVERIFY2(!golden.isEmpty(), "the waterfall frame goldens are missing");
    const QJsonArray rows = golden.value(QStringLiteral("rows")).toArray();
    QVERIFY(!rows.isEmpty());
    QCOMPARE(rows.size(), 12);
    for (const QJsonValue &row : rows) {
        QCOMPARE(row.toArray().size(), golden.value(QStringLiteral("row_samples")).toInt());
    }
    QVERIFY(!golden.value(QStringLiteral("streams")).toArray().isEmpty());
}

void WaterfallFramesTest::streamedFrameMatchesPython() {
    const QJsonObject golden =
        ituner::test::loadGolden(QStringLiteral("waterfall_frames_expected.json"));
    QVERIFY2(!golden.isEmpty(), "the waterfall frame goldens are missing");

    const QList<QList<int>> rows = [&] {
        QList<QList<int>> samples;
        for (const QJsonValue &row : golden.value(QStringLiteral("rows")).toArray()) {
            samples.append(intList(row.toArray()));
        }
        return samples;
    }();
    const WaterfallPalette palette = ituner::core::kiwiPalette();
    const int usableHeight = 600;

    for (const QJsonValue &entry : golden.value(QStringLiteral("streams")).toArray()) {
        const QJsonObject stream = entry.toObject();
        const bool autoLevel = stream.value(QStringLiteral("auto")).toBool();
        const int rowPixels = stream.value(QStringLiteral("wf_row_pixels")).toInt();
        WaterfallLeveler leveler(stream.value(QStringLiteral("initial_floor")).toDouble(),
                                 stream.value(QStringLiteral("initial_ceiling")).toDouble(),
                                 autoLevel);
        WaterfallQueue queue;
        QByteArray frame;

        const QJsonArray expectedRows = stream.value(QStringLiteral("rows")).toArray();
        for (int index = 0; index < rows.size(); ++index) {
            const QList<int> &samples = rows.at(index);
            const QPair<double, double> levels = leveler.levelsFor(samples);
            const QJsonObject expected = expectedRows.at(index).toObject();
            QVERIFY2(levels.first == expected.value(QStringLiteral("floor")).toDouble(),
                     qPrintable(QStringLiteral("row %1 floor: expected %2, produced %3")
                                    .arg(index)
                                    .arg(expected.value(QStringLiteral("floor")).toDouble(), 0, 'g',
                                         17)
                                    .arg(levels.first, 0, 'g', 17)));
            QVERIFY2(levels.second == expected.value(QStringLiteral("ceiling")).toDouble(),
                     qPrintable(QStringLiteral("row %1 ceiling: expected %2, produced %3")
                                    .arg(index)
                                    .arg(expected.value(QStringLiteral("ceiling")).toDouble(), 0, 'g',
                                         17)
                                    .arg(levels.second, 0, 'g', 17)));

            const QByteArray levels8 =
                ituner::core::normalizeLevels(samples, levels.first, levels.second);
            const QByteArray rgba = ituner::core::renderRowRgba(levels8, samples.size(), palette);
            const QByteArray rgb = rgbOnly(rgba);

            const QString label = QStringLiteral("row %1 (row pixels %2)").arg(index).arg(rowPixels);
            QCOMPARE(rgb.size(), expected.value(QStringLiteral("rgb_length")).toInt());
            QVERIFY2(sha256(rgb) == expected.value(QStringLiteral("sha256")).toString(),
                     qPrintable(QStringLiteral("%1: row bytes differ").arg(label)));
            QByteArray firstBytes;
            for (const QJsonValue &value : expected.value(QStringLiteral("first_bytes")).toArray()) {
                firstBytes.append(static_cast<char>(value.toInt()));
            }
            QCOMPARE(rgb.left(firstBytes.size()), firstBytes);

            // One received line can cover several screen rows; the queue is what
            // turns that into the frame the renderer uploads.
            queue.enqueue(rgb, rowPixels, usableHeight);
            QCOMPARE(queue.pendingCount(), rowPixels);
            QByteArray line;
            while (queue.popNext(&line)) {
                frame.append(line);
            }
        }

        QCOMPARE(frame.size(), stream.value(QStringLiteral("frame_length")).toInt());
        QVERIFY2(sha256(frame) == stream.value(QStringLiteral("frame_sha256")).toString(),
                 qPrintable(QStringLiteral("row pixels %1, auto %2: the whole frame differs")
                                .arg(rowPixels)
                                .arg(autoLevel)));
    }
}

void WaterfallFramesTest::emptyRowMatchesPython() {
    const QJsonObject golden =
        ituner::test::loadGolden(QStringLiteral("waterfall_frames_expected.json"));
    QVERIFY2(!golden.isEmpty(), "the waterfall frame goldens are missing");
    const int width = golden.value(QStringLiteral("row_samples")).toInt();
    const QByteArray rgb = rgbOnly(ituner::core::emptyRowRgba(width));
    QCOMPARE(rgb.size(), width * 3);
    QVERIFY2(sha256(rgb) == golden.value(QStringLiteral("empty_row_sha256")).toString(),
             "the empty row is not the Python vacuum colour");
    // Every pixel is the vacuum colour, which is what makes the check meaningful
    // rather than just true.
    QCOMPARE(rgb.left(3), QByteArray("\x00\x00\x10", 3));
}

QTEST_MAIN(WaterfallFramesTest)
#include "tst_waterfall_frames.moc"
