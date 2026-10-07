// The waterfall screen's touch handling and display wiring, without a panel.
//
// This is the test that makes Task 3's gesture requirements checkable on a
// development host: no touchscreen is involved, the screen's own entry points are
// driven with the same coordinates a finger would produce and the resulting
// frequency, zoom and passband are asserted.
//
// The expected values are computed with the same core models the screen uses.
// That is deliberate rather than circular: those models are pinned against
// captured Python goldens by `tst_swipe_gesture` and `tst_spectrum_model`, so the
// question this suite answers is the one the goldens cannot -- whether the screen
// calls them with the right arguments, in the right order, with the right gates.

#include <QQuickItem>
#include <QQuickWindow>
#include <QtTest>

#include <cmath>
#include <limits>

#include <passband_overlay.h>
#include <swipe_gesture.h>
#include <tuning.h>
#include <waterfall_controls.h>

#include <waterfall_item.h>
#include <waterfall_view.h>

using ituner::core::SwipeConfig;
using ituner::core::WaterfallControl;
using ituner::ui::WaterfallItem;
using ituner::ui::WaterfallView;

namespace {

/// A swipe configuration with live inertia, so the release path is exercised
/// instead of being a no-op.
SwipeConfig inertiaConfig() {
    SwipeConfig config;
    config.inertiaStrength = 0.35;
    return ituner::core::normalizeSwipeConfig(config);
}

}  // namespace

class WaterfallViewTest : public QObject {
    Q_OBJECT

private slots:
    void controlsMatchTheLcdLayout();
    void zoomButtonsStepTheZoom();
    void viewButtonsSwallowTheTouchInsteadOfTuning();
    void tapTunesToTheTouchedFrequency();
    void shortOrVerticalDragsDoNotTune();
    void dragTunesPositionallyAndSnaps();
    void releaseCarriesInertiaThatDecays();
    void passbandEdgeDragMovesOnlyThatEdge();
    void spectrumIsFedFromTheSameSamplesAsTheWaterfall();
    void eachReceivedRowCostsOneTextureUpload();
};

void WaterfallViewTest::controlsMatchTheLcdLayout() {
    WaterfallView view;
    const ituner::core::WaterfallControlGeometry &geometry = view.controls();
    QVERIFY(ituner::core::passbandControlClearsCentreBand(geometry));
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 50.0, 673.0),
             WaterfallControl::ZoomMinus);
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 130.0, 673.0),
             WaterfallControl::ZoomPlus);
    // The middle of the waterfall is the tuning surface, not a control.
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 512.0, 200.0), WaterfallControl::None);
    QVERIFY(ituner::core::isWaterfallTuneTouch(geometry, 512.0, 200.0));
}

void WaterfallViewTest::zoomButtonsStepTheZoom() {
    WaterfallView view;
    view.setView(7075.0, 9);
    const QString minus = view.beginTouch(50.0, 673.0);
    QCOMPARE(minus, QStringLiteral("zoom_minus"));
    QCOMPARE(view.zoom(), 8);
    view.endTouch(50.0, 673.0, 0.0);

    const QString plus = view.beginTouch(130.0, 673.0);
    QCOMPARE(plus, QStringLiteral("zoom_plus"));
    QCOMPARE(view.zoom(), 9);
    view.endTouch(130.0, 673.0, 0.0);
}

void WaterfallViewTest::viewButtonsSwallowTheTouchInsteadOfTuning() {
    WaterfallView view;
    view.setView(7075.0, 9);
    const double before = view.frequencyKhz();
    // The drawn Spectrum button belongs to the Home rail in the LCD layout, so
    // the waterfall neither acts on it nor tunes through it: the touch is
    // swallowed rather than silently mistuning the receiver.
    const QString gesture = view.beginTouch(940.0, 673.0);
    QCOMPARE(gesture, QStringLiteral("swallowed"));
    view.moveTouch(600.0, 673.0, 900.0);
    view.endTouch(600.0, 673.0, 900.0);
    QCOMPARE(view.frequencyKhz(), before);
    QCOMPARE(view.zoom(), 9);
}

void WaterfallViewTest::tapTunesToTheTouchedFrequency() {
    WaterfallView view;
    view.setView(7075.0, 13);
    const double span = ituner::core::zoomToSpanKhz(13);
    view.beginTouch(600.0, 200.0);
    view.endTouch(600.0, 200.0, 0.0);
    const int step = ituner::core::receiverTuneStepHz(13, 100, QStringLiteral("kiwi"), 1024.0);
    const double expected = ituner::core::snapFrequencyKhz(
        ituner::core::retuneFromTap(600.0, 7075.0, span, 1024.0), step);
    QCOMPARE(view.frequencyKhz(), expected);
    QVERIFY(view.frequencyKhz() != 7075.0);
}

void WaterfallViewTest::shortOrVerticalDragsDoNotTune() {
    WaterfallView view;
    view.setView(7075.0, 13);
    view.beginTouch(400.0, 200.0);
    // Five pixels is under the deliberate-drag threshold.
    view.moveTouch(405.0, 200.0, 100.0);
    QCOMPARE(view.frequencyKhz(), 7075.0);

    // A mostly vertical move is not a tuning gesture either.
    view.moveTouch(500.0, 400.0, 400.0);
    QCOMPARE(view.frequencyKhz(), 7075.0);
    view.endTouch(500.0, 400.0, 400.0);
}

void WaterfallViewTest::dragTunesPositionallyAndSnaps() {
    WaterfallView view;
    view.setView(7075.0, 13);
    const double span = ituner::core::zoomToSpanKhz(13);
    const double sensitivity = ituner::core::swipeEffectiveSensitivity(0.0, view.swipeConfig());
    const int step = ituner::core::receiverTuneStepHz(13, 100, QStringLiteral("kiwi"), 1024.0);
    const double expected = ituner::core::snapFrequencyKhz(
        ituner::core::retuneFromDrag(7075.0, 400.0, 600.0, span, 1024.0, false, sensitivity), step);

    view.beginTouch(400.0, 200.0);
    view.moveTouch(600.0, 200.0, 0.0);
    QCOMPARE(view.frequencyKhz(), expected);
    const double afterFirst = view.frequencyKhz();
    view.endTouch(600.0, 200.0, 0.0);

    // Positional, not cumulative: a second gesture with the same travel moves the
    // same distance from wherever the receiver now is, rather than compounding
    // onto the previous move.
    const double firstDelta = afterFirst - 7075.0;
    view.beginTouch(400.0, 200.0);
    view.moveTouch(600.0, 200.0, 0.0);
    QCOMPARE(view.frequencyKhz() - afterFirst, firstDelta);
    view.endTouch(600.0, 200.0, 0.0);
}

void WaterfallViewTest::releaseCarriesInertiaThatDecays() {
    WaterfallView view;
    view.setSwipeConfig(inertiaConfig());
    view.setView(7075.0, 13);
    view.beginTouch(400.0, 200.0);
    view.moveTouch(900.0, 200.0, 1500.0);
    const double atRelease = view.frequencyKhz();
    view.endTouch(900.0, 200.0, 1500.0);
    QVERIFY(atRelease != 7075.0);

    view.advance(0.016);
    const double afterOneFrame = view.frequencyKhz();
    QVERIFY(afterOneFrame != atRelease);

    // The decay is exponential, so each step moves less than the last, and the
    // velocity eventually stops being worth applying.
    view.advance(0.016);
    const double firstStep = std::abs(afterOneFrame - atRelease);
    const double secondStep = std::abs(view.frequencyKhz() - afterOneFrame);
    QVERIFY2(secondStep < firstStep, "the inertia did not decay");

    for (int index = 0; index < 400; ++index) {
        view.advance(0.05);
    }
    const double settled = view.frequencyKhz();
    view.advance(0.05);
    QCOMPARE(view.frequencyKhz(), settled);
}

void WaterfallViewTest::passbandEdgeDragMovesOnlyThatEdge() {
    WaterfallView view;
    view.setFilterCuts(-2400.0, 2400.0);
    QCOMPARE(view.lowCutHz(), -2400.0);
    QCOMPARE(view.highCutHz(), 2400.0);

    // The filter sheet owns the handles; with it closed the waterfall's edges are
    // not draggable, exactly as `filter_panel_open` gates them.
    QCOMPARE(view.beginTouch(42.0, 150.0), QStringLiteral("swallowed"));
    view.moveTouch(300.0, 150.0, 0.0);
    QCOMPARE(view.lowCutHz(), -2400.0);
    QCOMPARE(view.highCutHz(), 2400.0);
    view.endTouch(300.0, 150.0, 0.0);

    view.setFilterSheetOpen(true);
    const double limit = ituner::core::filterEditLimit(-2400.0, 2400.0);
    QCOMPARE(limit, 3000.0);
    const double lowX = ituner::core::filterX(-2400.0, 42.0, 918.0, limit, 0.0);
    QCOMPARE(view.beginTouch(lowX, 150.0), QStringLiteral("passband_edge"));

    // Dragging the low handle moves only the low edge.
    view.moveTouch(700.0, 150.0, 0.0);
    QCOMPARE(view.highCutHz(), 2400.0);
    QCOMPARE(view.lowCutHz(), ituner::core::filterCutAtX(700.0, 42.0, 918.0, limit, 0.0));
    QCOMPARE(view.lowCutHz(), 1500.0);
    view.endTouch(700.0, 150.0, 0.0);

    // A drag well past the high edge stops one detent below it rather than
    // crossing it, which is `set_filter`'s ordering rule.
    const double limit2 = ituner::core::filterEditLimit(view.lowCutHz(), view.highCutHz());
    const double lowX2 = ituner::core::filterX(view.lowCutHz(), 42.0, 918.0, limit2, 0.0);
    QCOMPARE(view.beginTouch(lowX2, 150.0), QStringLiteral("passband_edge"));
    view.moveTouch(918.0, 150.0, 0.0);
    QCOMPARE(view.highCutHz(), 2400.0);
    QCOMPARE(view.lowCutHz(), 2350.0);
    view.endTouch(918.0, 150.0, 0.0);
}

void WaterfallViewTest::spectrumIsFedFromTheSameSamplesAsTheWaterfall() {
    WaterfallItem item;
    item.setRowWidth(64);
    WaterfallView view;
    view.setWaterfall(&item);
    view.setSpectrumEnabled(true);

    QList<int> samples;
    samples.reserve(64);
    for (int index = 0; index < 64; ++index) {
        samples.append(index * 3);
    }
    // One line covers one screen row here, and the waterfall history grows by it.
    QCOMPARE(view.pushWaterfallLine(samples, 1, 0.0, 200.0), 1);
    QCOMPARE(item.rowsWritten(), 1);

    // The first frame replaces the trace rather than blending it, because the
    // history starts empty and a width change resets the hold window. The screen
    // must feed the scope the same row it renders.
    const QList<double> bins = ituner::core::spectrumBinsFromSamples(samples, 0.0, 200.0);
    const QList<double> values = view.spectrumValues();
    QCOMPARE(values, bins);

    // Repeating the same frame must leave the trace where it was. The weights sum
    // to one in decimal but not in binary, so a repeated frame may shift the last
    // bit; anything larger would mean the blend is drifting the trace.
    QCOMPARE(view.pushWaterfallLine(samples, 1, 0.0, 200.0), 1);
    QCOMPARE(item.rowsWritten(), 2);
    const QList<double> repeated = view.spectrumValues();
    QCOMPARE(repeated.size(), values.size());
    for (int index = 0; index < repeated.size(); ++index) {
        const double drift = std::abs(repeated.at(index) - values.at(index));
        QVERIFY2(drift <= 2.0 * std::numeric_limits<double>::epsilon(),
                 qPrintable(QStringLiteral("bin %1 drifted by %2").arg(index).arg(drift)));
    }

    // The hold envelope is the max across the window, so it never drops below the
    // live trace.
    const QList<double> peaks = view.spectrumPeakValues();
    QCOMPARE(peaks.size(), values.size());
    for (int index = 0; index < peaks.size(); ++index) {
        QVERIFY(peaks.at(index) >= values.at(index));
    }

    // Two screen rows per line is the row-pixel behaviour: the history grows by
    // two, and the scope still sees one frame.
    WaterfallItem paired;
    paired.setRowWidth(64);
    WaterfallView pairedView;
    pairedView.setWaterfall(&paired);
    pairedView.setSpectrumEnabled(true);
    QCOMPARE(pairedView.pushWaterfallLine(samples, 2, 0.0, 200.0), 2);
    QCOMPARE(paired.rowsWritten(), 2);
    QCOMPARE(pairedView.spectrumValues(), values);

    // With the scope disabled the ten-second window is not maintained at all,
    // which is what keeps a waterfall-first screen cheap.
    WaterfallItem quiet;
    quiet.setRowWidth(64);
    WaterfallView quietView;
    quietView.setWaterfall(&quiet);
    QCOMPARE(quietView.spectrumValues().size(), 0);
    QCOMPARE(quietView.pushWaterfallLine(samples, 1, 0.0, 200.0), 1);
    QCOMPARE(quietView.spectrumValues().size(), 0);
    QCOMPARE(quiet.rowsWritten(), 1);
}

void WaterfallViewTest::eachReceivedRowCostsOneTextureUpload() {
    // The item's whole design rests on one claim: a received line costs one
    // texture upload, however many rows the band shows. That is only true if the
    // scene-graph nodes are reused by texture *slot*. Keying them by age instead
    // passes every single-frame render check -- the pixels are computed from the
    // age either way -- and only shows up once the history scrolls, when a slot
    // that received new pixels is not the slot with the matching age.
    //
    // A one-shot render also cannot catch it, so this drives a real render between
    // pushes and counts the uploads.
    QQuickWindow window;
    window.setColor(Qt::black);
    window.resize(64, 10);

    WaterfallItem item;
    item.setParentItem(window.contentItem());
    item.setWidth(64.0);
    item.setHeight(10.0);
    item.setRowWidth(64);
    item.setCapacity(300);

    window.show();
    if (!QTest::qWaitForWindowExposed(&window)) {
        QSKIP("the platform plugin exposed no window");
    }

    QList<int> samples;
    samples.reserve(64);
    for (int index = 0; index < 64; ++index) {
        samples.append((index * 3) % 256);
    }

    // Render once per received line, with the band showing only part of the
    // history, so the newest slot and the matching age diverge.
    item.resetTextureUploadCount();
    const int pushes = 20;
    for (int index = 0; index < pushes; ++index) {
        item.pushSamples(samples, 1);
        const QImage frame = window.grabWindow();
        QVERIFY2(!frame.isNull(), "the offscreen window produced no frame");
    }

    // One upload per row received: not per visible row, and not per render.
    QCOMPARE(item.textureUploadCount(), pushes);
    QCOMPARE(item.rowsWritten(), pushes);
}

QTEST_MAIN(WaterfallViewTest)
#include "tst_waterfall_view.moc"
