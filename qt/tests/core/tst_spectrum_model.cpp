// Parity tests for the spectrum scope, the waterfall control grouping and the
// passband overlay.
//
// Every expectation comes from `qt/tests/golden/spectrum_model_expected.json` and
// `passband_overlay_expected.json`, produced by the captures in
// `qt/tests/parity/` calling the real Python renderer. The binning, the 56/44
// blend and the ten-second max-hold window are compared value by value; the
// overlay geometry is compared against the recorded `draw_logical_*` call
// sequence, so a wrong alpha or a shifted tick fails with the call index named.

#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>

#include <cstring>

#include <passband_overlay.h>
#include <spectrum_model.h>
#include <waterfall_controls.h>

#include "golden_draw_list.h"

using ituner::core::DrawList;
using ituner::core::PassbandOverlayInput;
using ituner::core::SpectrumFrameInput;
using ituner::core::SpectrumState;
using ituner::core::WaterfallControl;
using ituner::core::WaterfallControlGeometry;

namespace {

QJsonObject spectrumGolden() {
    return ituner::test::loadGolden(QStringLiteral("spectrum_model_expected.json"));
}

QJsonObject overlayGolden() {
    return ituner::test::loadGolden(QStringLiteral("passband_overlay_expected.json"));
}

QList<int> intList(const QJsonArray &values) {
    QList<int> result;
    result.reserve(values.size());
    for (const QJsonValue &value : values) {
        result.append(value.toInt());
    }
    return result;
}

QList<double> doubleList(const QJsonArray &values) {
    QList<double> result;
    result.reserve(values.size());
    for (const QJsonValue &value : values) {
        result.append(value.toDouble());
    }
    return result;
}

/// The exact bit pattern, which is what makes a near-miss diagnosable.
QString bits(double value) {
    quint64 raw = 0;
    std::memcpy(&raw, &value, sizeof(raw));
    return QStringLiteral("0x") + QString::number(raw, 16);
}

/// Element-wise exact comparison. A tolerance would hide the fact that the
/// Python and C++ arithmetic are the same operations in the same order.
QString compareExactly(const QList<double> &produced, const QList<double> &expected,
                       const QString &label) {
    if (produced.size() != expected.size()) {
        return QStringLiteral("%1: length expected %2, produced %3")
            .arg(label)
            .arg(expected.size())
            .arg(produced.size());
    }
    for (int index = 0; index < expected.size(); ++index) {
        if (produced.at(index) != expected.at(index)) {
            return QStringLiteral("%1[%2]: expected %3 (%4), produced %5 (%6)")
                .arg(label)
                .arg(index)
                .arg(expected.at(index), 0, 'g', 17)
                .arg(bits(expected.at(index)))
                .arg(produced.at(index), 0, 'g', 17)
                .arg(bits(produced.at(index)));
        }
    }
    return {};
}

}  // namespace

class SpectrumModelTest : public QObject {
    Q_OBJECT

private slots:
    void constantsMatchPython();
    void sampleBinningMatchesPython();
    void zoomResampleMatchesPython();
    void stateSequenceMatchesPython();
    void spectrumDrawListMatchesPython();
    void controlGroupingMatchesPython();
    void passbandOverlayMatchesPython();
    void filterGeometryMatchesPython();
    void filterEditsMatchPython();
};

void SpectrumModelTest::constantsMatchPython() {
    const QJsonObject constants = spectrumGolden().value(QStringLiteral("constants")).toObject();
    QVERIFY2(!constants.isEmpty(), "the spectrum goldens are missing");
    QCOMPARE(ituner::core::spectrumBins(), constants.value(QStringLiteral("bins")).toInt());
    QCOMPARE(ituner::core::spectrumPeakHoldSeconds(),
             constants.value(QStringLiteral("peak_hold_seconds")).toDouble());
    QCOMPARE(ituner::core::spectrumBlendOld(),
             constants.value(QStringLiteral("blend_old")).toDouble());
    QCOMPARE(ituner::core::spectrumBlendNew(),
             constants.value(QStringLiteral("blend_new")).toDouble());
    QCOMPARE(ituner::core::spectrumFieldAlpha(false), 236);
    QCOMPARE(ituner::core::spectrumFieldAlpha(true), 156);
}

void SpectrumModelTest::sampleBinningMatchesPython() {
    const QJsonArray rows = spectrumGolden().value(QStringLiteral("binning")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QList<int> samples = intList(row.value(QStringLiteral("samples")).toArray());
        const double floor = row.value(QStringLiteral("floor")).toDouble();
        const double ceiling = row.value(QStringLiteral("ceiling")).toDouble();
        const QList<double> expected = doubleList(row.value(QStringLiteral("bins")).toArray());
        const QList<double> produced =
            ituner::core::spectrumBinsFromSamples(samples, floor, ceiling);
        const QString difference = compareExactly(
            produced, expected,
            QStringLiteral("samples=%1 floor=%2 ceiling=%3").arg(samples.size()).arg(floor).arg(
                ceiling));
        QVERIFY2(difference.isEmpty(), qPrintable(difference));
    }
}

void SpectrumModelTest::zoomResampleMatchesPython() {
    const QJsonArray rows = spectrumGolden().value(QStringLiteral("zoom")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QList<double> values = doubleList(row.value(QStringLiteral("values")).toArray());
        const double source = row.value(QStringLiteral("source_span_khz")).toDouble();
        const double visible = row.value(QStringLiteral("visible_span_khz")).toDouble();
        const QList<double> expected = doubleList(row.value(QStringLiteral("result")).toArray());
        const QList<double> produced =
            ituner::core::zoomedSpectrumValues(values, source, visible);
        const QString difference = compareExactly(
            produced, expected,
            QStringLiteral("values=%1 source=%2 visible=%3").arg(values.size()).arg(source).arg(
                visible));
        QVERIFY2(difference.isEmpty(), qPrintable(difference));
    }
}

void SpectrumModelTest::stateSequenceMatchesPython() {
    const QJsonObject golden = spectrumGolden();
    const QJsonArray steps = golden.value(QStringLiteral("state")).toArray();
    const QJsonObject inputs = golden.value(QStringLiteral("state_inputs")).toObject();
    QVERIFY2(!steps.isEmpty() && !inputs.isEmpty(), "the spectrum state goldens are missing");

    const QList<int> rowA = intList(inputs.value(QStringLiteral("row_a")).toArray());
    const QList<int> rowB = intList(inputs.value(QStringLiteral("row_b")).toArray());
    const QList<double> values240 = doubleList(inputs.value(QStringLiteral("values_240")).toArray());
    const QList<double> values1024 =
        doubleList(inputs.value(QStringLiteral("values_1024")).toArray());

    // `SharedState` is constructed with `spectrum_enabled=True` in the capture.
    SpectrumState state;
    state.setEnabled(true);
    int cursor = 0;
    // Returns a description of the first difference, not a QVERIFY: a QCOMPARE
    // inside a lambda returns from the lambda, which would let the whole
    // sequence drift instead of failing on the step that broke.
    const auto check = [&](const QString &name) -> QString {
        if (cursor >= steps.size()) {
            return QStringLiteral("the golden has no step named %1").arg(name);
        }
        const QJsonObject step = steps.at(cursor).toObject();
        ++cursor;
        if (step.value(QStringLiteral("step")).toString() != name) {
            return QStringLiteral("step %1: expected %2")
                .arg(cursor)
                .arg(step.value(QStringLiteral("step")).toString());
        }
        const QString valuesError = compareExactly(
            state.values(), doubleList(step.value(QStringLiteral("values")).toArray()),
            QStringLiteral("%1 values").arg(name));
        if (!valuesError.isEmpty()) {
            return valuesError;
        }
        const QString peaksError = compareExactly(
            state.peakValues(), doubleList(step.value(QStringLiteral("peak_values")).toArray()),
            QStringLiteral("%1 peaks").arg(name));
        if (!peaksError.isEmpty()) {
            return peaksError;
        }
        if (state.peakHistoryFrames() != step.value(QStringLiteral("history")).toInt()) {
            return QStringLiteral("%1 history: expected %2, produced %3")
                .arg(name)
                .arg(step.value(QStringLiteral("history")).toInt())
                .arg(state.peakHistoryFrames());
        }
        if (state.enabled() != step.value(QStringLiteral("enabled")).toBool()) {
            return QStringLiteral("%1 enabled: expected %2, produced %3")
                .arg(name)
                .arg(step.value(QStringLiteral("enabled")).toBool())
                .arg(state.enabled());
        }
        return {};
    };
    const auto step = [&](const QString &name) {
        const QString error = check(name);
        QVERIFY2(error.isEmpty(), qPrintable(error));
    };

    // The sequence mirrors `capture_spectrum_model.py::state_rows` exactly,
    // including which clock value each update saw.
    step(QStringLiteral("initial"));
    state.setEnabled(true);
    step(QStringLiteral("set_enabled_true"));

    state.updateFromSamples(rowA, 142.0, 245.0, 100.0);
    step(QStringLiteral("update_samples_first"));
    state.updateFromSamples(rowB, 142.0, 245.0, 101.0);
    step(QStringLiteral("update_samples_blend"));

    state.updateFromValues(values240, 101.0);
    step(QStringLiteral("update_values_same_width"));

    // A local RTL FFT is 1024 bins: a different width must reset the hold window
    // rather than mix frames.
    state.updateFromValues(values1024, 103.0);
    step(QStringLiteral("update_values_width_change"));

    state.updateFromSamples({}, 142.0, 245.0, 103.0);
    state.updateFromValues({}, 103.0);
    step(QStringLiteral("empty_inputs_ignored"));

    state.updateFromSamples(rowA, 142.0, 245.0, 105.0);
    step(QStringLiteral("update_samples_width_change_back"));
    state.updateFromSamples(rowA, 142.0, 245.0, 106.0);
    step(QStringLiteral("update_samples_hold_window"));
    state.updateFromSamples(rowA, 142.0, 245.0, 117.0);
    step(QStringLiteral("update_samples_window_evicts"));

    state.resetForReceiver();
    step(QStringLiteral("set_server_reset"));

    state.updateFromSamples(rowA, 142.0, 245.0, 119.0);
    step(QStringLiteral("update_after_reset"));
    state.setEnabled(false);
    step(QStringLiteral("set_enabled_false"));

    QCOMPARE(cursor, steps.size());
}

void SpectrumModelTest::spectrumDrawListMatchesPython() {
    const QJsonObject golden = spectrumGolden();
    const QJsonArray cases = golden.value(QStringLiteral("draw")).toArray();
    QVERIFY(!cases.isEmpty());
    for (const QJsonValue &entry : cases) {
        const QJsonObject item = entry.toObject();
        SpectrumFrameInput input;
        input.y0 = item.value(QStringLiteral("y0")).toDouble();
        input.y1 = item.value(QStringLiteral("y1")).toDouble();
        input.values = doubleList(item.value(QStringLiteral("values")).toArray());
        input.peakValues = doubleList(item.value(QStringLiteral("peak_values")).toArray());
        input.hasTextCache = item.value(QStringLiteral("text_cache")).toBool();
        input.foreground = item.value(QStringLiteral("foreground")).toBool();
        input.canvasWidth = item.value(QStringLiteral("canvas_width")).toDouble();
        const QJsonValue source = item.value(QStringLiteral("source_span_khz"));
        const QJsonValue visible = item.value(QStringLiteral("visible_span_khz"));
        if (!source.isNull()) {
            input.sourceSpanKhz = source.toDouble();
        }
        if (!visible.isNull()) {
            input.visibleSpanKhz = visible.toDouble();
        }

        const DrawList produced = ituner::core::spectrumDrawList(input);
        const QString difference =
            ituner::test::compareDrawList(produced, item.value(QStringLiteral("calls")).toArray());
        QVERIFY2(difference.isEmpty(),
                 qPrintable(QStringLiteral("draw case %1: %2")
                                .arg(item.value(QStringLiteral("name")).toString(), difference)));
    }
}

void SpectrumModelTest::controlGroupingMatchesPython() {
    const QJsonObject controls = overlayGolden().value(QStringLiteral("controls")).toObject();
    QVERIFY2(!controls.isEmpty(), "the control goldens are missing");

    const QJsonObject recorded = controls.value(QStringLiteral("geometry")).toObject();
    const WaterfallControlGeometry geometry = ituner::core::lcdWaterfallControlGeometry(
        controls.value(QStringLiteral("logical_height")).toDouble(),
        controls.value(QStringLiteral("rail_x0")).toDouble(),
        ituner::core::ControlBox{
            controls.value(QStringLiteral("home_box")).toArray().at(0).toDouble(),
            controls.value(QStringLiteral("home_box")).toArray().at(1).toDouble(),
            controls.value(QStringLiteral("home_box")).toArray().at(2).toDouble(),
            controls.value(QStringLiteral("home_box")).toArray().at(3).toDouble()});

    const auto compareBox = [&](const QString &name, const ituner::core::ControlBox &box) {
        const QJsonArray expected = recorded.value(name).toArray();
        QCOMPARE(expected.size(), 4);
        const QList<double> coordinates = {box.x0, box.y0, box.x1, box.y1};
        for (int index = 0; index < 4; ++index) {
            QVERIFY2(coordinates.at(index) == expected.at(index).toDouble(),
                     qPrintable(QStringLiteral("%1[%2]: expected %3, produced %4")
                                    .arg(name)
                                    .arg(index)
                                    .arg(expected.at(index).toDouble())
                                    .arg(coordinates.at(index))));
        }
    };
    compareBox(QStringLiteral("ZOOM_GROUP_BOX"), geometry.zoomGroup);
    compareBox(QStringLiteral("ZOOM_MINUS_BOX"), geometry.zoomMinus);
    compareBox(QStringLiteral("ZOOM_PLUS_BOX"), geometry.zoomPlus);
    compareBox(QStringLiteral("VIEW_GROUP_BOX"), geometry.viewGroup);
    compareBox(QStringLiteral("FILTER_TOGGLE_BOX"), geometry.filterToggle);
    compareBox(QStringLiteral("SPECTRUM_TOGGLE_BOX"), geometry.spectrumToggle);
    compareBox(QStringLiteral("ASR_TOGGLE_BOX"), geometry.asrToggle);

    QCOMPARE(geometry.canvasWidth, controls.value(QStringLiteral("canvas_width")).toDouble());
    QCOMPARE(ituner::core::controlTouchGuardPx(),
             controls.value(QStringLiteral("guard_px")).toDouble());

    const QJsonArray bounds = controls.value(QStringLiteral("touch_bounds")).toArray();
    const ituner::core::ControlBox touchBounds = ituner::core::waterfallTouchBounds(
        controls.value(QStringLiteral("logical_height")).toDouble());
    const QList<double> producedBounds = {touchBounds.x0, touchBounds.y0, touchBounds.x1,
                                         touchBounds.y1};
    QCOMPARE(producedBounds, doubleList(bounds));

    // The interface rule: the Spectrum/Passband group stays off the centre so a
    // tuning drag can start anywhere across the middle of the waterfall.
    QVERIFY(ituner::core::passbandControlClearsCentreBand(geometry));
    // And the passband button is the right-edge group, not a centre control.
    QVERIFY(geometry.spectrumToggle.x0 >= geometry.canvasWidth / 2.0);
    QVERIFY(geometry.viewGroup.x1 <= geometry.railX0);

    const QJsonArray samples = controls.value(QStringLiteral("samples")).toArray();
    QVERIFY(!samples.isEmpty());
    for (const QJsonValue &entry : samples) {
        const QJsonObject sample = entry.toObject();
        const double x = sample.value(QStringLiteral("x")).toDouble();
        const double y = sample.value(QStringLiteral("y")).toDouble();
        QCOMPARE(ituner::core::isWaterfallTuneTouch(geometry, x, y,
                                                    controls.value(QStringLiteral("logical_height"))
                                                        .toDouble()),
                 sample.value(QStringLiteral("tune")).toBool());
        QCOMPARE(touchBounds.contains(x, y), sample.value(QStringLiteral("in_bounds")).toBool());
    }

    // The LCD presentation parks the waterfall's own passband button off-canvas
    // and leaves the drawn Spectrum button to the Home rail.
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 50.0, 673.0),
             WaterfallControl::ZoomMinus);
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 130.0, 673.0),
             WaterfallControl::ZoomPlus);
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 940.0, 673.0), WaterfallControl::None);
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 500.0, 200.0), WaterfallControl::None);
    QCOMPARE(ituner::core::waterfallControlAt(geometry, 900.0, 750.0),
             WaterfallControl::AsrToggle);
}

void SpectrumModelTest::passbandOverlayMatchesPython() {
    const QJsonArray cases = overlayGolden().value(QStringLiteral("overlay")).toArray();
    QVERIFY(!cases.isEmpty());
    for (const QJsonValue &entry : cases) {
        const QJsonObject item = entry.toObject();
        PassbandOverlayInput input;
        input.spanKhz = item.value(QStringLiteral("span_khz")).toDouble();
        input.lowCutHz = item.value(QStringLiteral("low_cut_hz")).toDouble();
        input.highCutHz = item.value(QStringLiteral("high_cut_hz")).toDouble();
        input.y0 = item.value(QStringLiteral("y0")).toDouble();
        input.y1 = item.value(QStringLiteral("y1")).toDouble();
        input.alpha = item.value(QStringLiteral("alpha")).toDouble();
        input.tunedOffsetHz = item.value(QStringLiteral("tuned_offset_hz")).toDouble();
        input.labelWidthPx = item.value(QStringLiteral("label_width_px")).toDouble();
        input.canvasWidth = item.value(QStringLiteral("canvas_width")).toDouble();

        const DrawList produced = ituner::core::passbandOverlayDrawList(input);
        const QString difference =
            ituner::test::compareDrawList(produced, item.value(QStringLiteral("calls")).toArray());
        QVERIFY2(difference.isEmpty(),
                 qPrintable(QStringLiteral("overlay case %1: %2")
                                .arg(item.value(QStringLiteral("name")).toString(), difference)));
    }

    // Two states the recorded cases pin, asserted directly so the intent is
    // legible rather than only implied by a call sequence.
    PassbandOverlayInput bracket;
    bracket.spanKhz = 300.0;
    bracket.lowCutHz = -1200.0;
    bracket.highCutHz = 1200.0;
    bracket.y0 = 40.0;
    bracket.y1 = 292.0;
    QVERIFY(ituner::core::passbandOverlayGeometry(bracket).bracket);

    PassbandOverlayInput faded = bracket;
    faded.alpha = 0.01;
    QVERIFY(ituner::core::passbandOverlayDrawList(faded).isEmpty());
}

void SpectrumModelTest::filterGeometryMatchesPython() {
    const QJsonObject filter = overlayGolden().value(QStringLiteral("filter")).toObject();
    QVERIFY2(!filter.isEmpty(), "the filter goldens are missing");

    const QJsonObject constants = filter.value(QStringLiteral("constants")).toObject();
    QCOMPARE(ituner::core::filterLimitHz(), constants.value(QStringLiteral("limit_hz")).toDouble());
    QCOMPARE(ituner::core::filterSnapHz(), constants.value(QStringLiteral("snap_hz")).toDouble());
    QCOMPARE(ituner::core::filterHandleTouchPx(),
             constants.value(QStringLiteral("handle_touch_px")).toDouble());

    for (const QJsonValue &entry : filter.value(QStringLiteral("edit_limits")).toArray()) {
        const QJsonObject row = entry.toObject();
        QCOMPARE(ituner::core::filterEditLimit(row.value(QStringLiteral("low_cut")).toDouble(),
                                               row.value(QStringLiteral("high_cut")).toDouble()),
                 row.value(QStringLiteral("limit")).toDouble());
    }
    for (const QJsonValue &entry : filter.value(QStringLiteral("filter_x")).toArray()) {
        const QJsonObject row = entry.toObject();
        QCOMPARE(ituner::core::filterX(row.value(QStringLiteral("cut_hz")).toDouble(), 42.0, 918.0),
                 row.value(QStringLiteral("x")).toDouble());
    }
    for (const QJsonValue &entry : filter.value(QStringLiteral("cut_at_x")).toArray()) {
        const QJsonObject row = entry.toObject();
        QCOMPARE(ituner::core::filterCutAtX(row.value(QStringLiteral("x")).toDouble(), 42.0, 918.0),
                 row.value(QStringLiteral("cut_hz")).toDouble());
    }

    const QJsonArray handles = filter.value(QStringLiteral("handles")).toArray();
    QVERIFY(!handles.isEmpty());
    for (const QJsonValue &entry : handles) {
        const QJsonObject row = entry.toObject();
        const ituner::core::FilterCuts cuts{row.value(QStringLiteral("low_cut")).toDouble(),
                                            row.value(QStringLiteral("high_cut")).toDouble()};
        const double limit = row.value(QStringLiteral("edit_limit")).toDouble();
        const ituner::core::FilterEdge produced =
            ituner::core::filterHandleAt(row.value(QStringLiteral("x")).toDouble(), cuts, 42.0, 918.0,
                                         limit);
        const QJsonValue recorded = row.value(QStringLiteral("grabbed"));
        const ituner::core::FilterEdge expected =
            recorded.isNull() ? ituner::core::FilterEdge::None
            : recorded.toString() == QLatin1String("low") ? ituner::core::FilterEdge::Low
                                                          : ituner::core::FilterEdge::High;
        QCOMPARE(produced, expected);
        // The recorded handle positions are the same projection the port uses.
        QCOMPARE(ituner::core::filterX(cuts.lowHz, 42.0, 918.0, limit, 0.0),
                 row.value(QStringLiteral("low_x")).toDouble());
        QCOMPARE(ituner::core::filterX(cuts.highHz, 42.0, 918.0, limit, 0.0),
                 row.value(QStringLiteral("high_x")).toDouble());
    }
}

void SpectrumModelTest::filterEditsMatchPython() {
    const QJsonObject edits = overlayGolden().value(QStringLiteral("filter_edits")).toObject();
    QVERIFY2(!edits.isEmpty(), "the filter edit goldens are missing");
    const QJsonArray initial = edits.value(QStringLiteral("initial")).toArray();
    QCOMPARE(initial.size(), 3);

    // The Python `set_filter` bumps a generation counter on every change, so the
    // recorded counter is the strongest available statement about *which* edits
    // moved the passband and which were clamped back to where they already were.
    ituner::core::FilterCuts cuts{initial.at(0).toDouble(), initial.at(1).toDouble()};
    int generation = initial.at(2).toInt();

    const QJsonArray rows = edits.value(QStringLiteral("rows")).toArray();
    QVERIFY(!rows.isEmpty());
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QJsonValue low = row.value(QStringLiteral("requested_low"));
        const QJsonValue high = row.value(QStringLiteral("requested_high"));
        const std::optional<double> lowCut = low.isNull() ? std::nullopt
                                                          : std::optional<double>(low.toDouble());
        const std::optional<double> highCut =
            high.isNull() ? std::nullopt : std::optional<double>(high.toDouble());

        // A pixel drag is expressed exactly as the handler does it: the canvas x
        // becomes a cut through the same inverse projection the editor uses.
        const QJsonValue boxX0 = row.value(QStringLiteral("box_x0"));
        const bool isDrag = !row.value(QStringLiteral("pixel_x")).isNull() && !boxX0.isNull();
        std::optional<double> draggedLow = lowCut;
        std::optional<double> draggedHigh = highCut;
        if (isDrag && row.value(QStringLiteral("edit_limit")).toDouble() > 0.0) {
            // A drag is a canvas pixel: the port has to project it back to a cut
            // with the same inverse mapping the editor uses before applying it.
            const double cut = ituner::core::filterCutAtX(
                row.value(QStringLiteral("pixel_x")).toDouble(), boxX0.toDouble(),
                row.value(QStringLiteral("box_x1")).toDouble(),
                row.value(QStringLiteral("edit_limit")).toDouble(), 0.0);
            QCOMPARE(cut, row.value(QStringLiteral("cut")).toDouble());
            if (row.value(QStringLiteral("edge")).toString() == QLatin1String("low")) {
                draggedLow = cut;
                draggedHigh = std::nullopt;
            } else {
                draggedHigh = cut;
                draggedLow = std::nullopt;
            }
        }

        const ituner::core::FilterCutsResult produced =
            ituner::core::applyFilterCuts(cuts, draggedLow, draggedHigh);
        const QString label = QStringLiteral("%1 row %2")
                                  .arg(row.value(QStringLiteral("kind")).toString())
                                  .arg(generation);
        QVERIFY2(produced.cuts.lowHz == row.value(QStringLiteral("low_cut")).toDouble(),
                 qPrintable(QStringLiteral("%1 low: expected %2, produced %3")
                                .arg(label)
                                .arg(row.value(QStringLiteral("low_cut")).toDouble())
                                .arg(produced.cuts.lowHz)));
        QVERIFY2(produced.cuts.highHz == row.value(QStringLiteral("high_cut")).toDouble(),
                 qPrintable(QStringLiteral("%1 high: expected %2, produced %3")
                                .arg(label)
                                .arg(row.value(QStringLiteral("high_cut")).toDouble())
                                .arg(produced.cuts.highHz)));
        if (produced.changed) {
            ++generation;
        }
        QCOMPARE(generation, row.value(QStringLiteral("generation")).toInt());
        if (produced.changed) {
            cuts = produced.cuts;
        }
    }
}

QTEST_MAIN(SpectrumModelTest)
#include "tst_spectrum_model.moc"
