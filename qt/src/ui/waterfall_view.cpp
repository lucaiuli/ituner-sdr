#include "waterfall_view.h"

#include <QFont>
#include <QFontMetricsF>

#include <algorithm>
#include <cmath>

namespace ituner::ui {

using ituner::core::FilterEdge;
using ituner::core::PassbandOverlayInput;
using ituner::core::SpectrumFrameInput;
using ituner::core::WaterfallControl;

namespace {

/// The passband label, measured in the render font so the core can place its
/// background box. Python reads the same measurement from the pygame font.
double measurePassbandLabel(double lowCutHz, double highCutHz) {
    QFont font;
    font.setFamily(QStringLiteral("Cantarell"));
    font.setPixelSize(13);
    font.setBold(true);
    const QFontMetricsF metrics(font);
    return metrics.horizontalAdvance(QLatin1String("BW ") + QString::number(
                                         std::abs(highCutHz - lowCutHz) / 1000.0, 'f', 2)
                                     + QLatin1String(" kHz"));
}

}  // namespace

WaterfallView::WaterfallView(QObject *parent) : QObject(parent) {
    m_swipe = ituner::core::normalizeSwipeConfig(m_swipe);
    m_zoom = std::clamp(m_swipe.stationZoom, 0, ituner::core::displayMaxZoom());
    m_spanKhz = ituner::core::zoomToSpanKhz(m_zoom);
    // Kiwi's native USB default, from `kiwi_mode_filter`.
    m_filter = {300.0, 2700.0};
    m_clock.start();
}

void WaterfallView::setWaterfall(WaterfallItem *item) {
    if (m_waterfall == item) {
        return;
    }
    m_waterfall = item;
    emit waterfallChanged();
    refreshOverlays();
}

void WaterfallView::setSpectrumOverlay(OverlayItem *item) {
    if (m_spectrumOverlay == item) {
        return;
    }
    m_spectrumOverlay = item;
    emit spectrumOverlayChanged();
    refreshOverlays();
}

void WaterfallView::setPassbandOverlay(OverlayItem *item) {
    if (m_passbandOverlay == item) {
        return;
    }
    m_passbandOverlay = item;
    emit passbandOverlayChanged();
    refreshOverlays();
}

void WaterfallView::setFilterSheetOpen(bool open) {
    if (m_filterSheetOpen == open) {
        return;
    }
    m_filterSheetOpen = open;
    emit filterSheetOpenChanged();
}

void WaterfallView::setSpectrumEnabled(bool enabled) {
    m_spectrum.setEnabled(enabled);
    emit viewChanged();
    refreshOverlays();
}

void WaterfallView::toggleSpectrum() {
    setSpectrumEnabled(!m_spectrum.enabled());
}

void WaterfallView::setDisplayLevels(double floor, double ceiling, bool autoLevel, int speed,
                                     const QString &palette) {
    if (m_waterfall != nullptr) {
        m_waterfall->setLevels(floor, ceiling);
        m_waterfall->setAutoLevel(autoLevel);
        m_waterfall->setSpeed(speed);
        m_waterfall->setPaletteName(palette);
    }
}

int WaterfallView::pushWaterfallLine(const QList<int> &samples, int rowPixels, double floor,
                                     double ceiling) {
    // The scope reads the same samples the waterfall does. Python only pays for
    // the ten-second hold while the scope is visible, because that cost lands on
    // every single row.
    if (m_spectrum.enabled()) {
        m_spectrum.updateFromSamples(samples, floor, ceiling, m_clock.elapsed() / 1000.0);
    }
    if (m_waterfall == nullptr) {
        return 0;
    }
    const int rows = m_waterfall->pushSamples(samples, rowPixels, m_centerKhz, m_spanKhz);
    if (m_spectrum.enabled()) {
        refreshOverlays();
    }
    return rows;
}

void WaterfallView::setView(double frequencyKhz, int zoom) {
    m_frequencyKhz = frequencyKhz;
    m_zoom = std::clamp(zoom, 0, ituner::core::displayMaxZoom());
    m_spanKhz = ituner::core::zoomToSpanKhz(m_zoom);
    emit viewChanged();
    refreshOverlays();
}

void WaterfallView::setFilterCuts(double lowCutHz, double highCutHz) {
    const ituner::core::FilterCutsResult result =
        ituner::core::applyFilterCuts(m_filter, std::optional<double>(lowCutHz),
                                      std::optional<double>(highCutHz));
    if (!result.changed) {
        return;
    }
    m_filter = result.cuts;
    emit filterChanged();
    refreshOverlays();
}

void WaterfallView::setPassbandAlpha(double alpha) {
    const double clamped = std::clamp(alpha, 0.0, 1.0);
    if (std::abs(m_passbandAlpha - clamped) < 1e-9) {
        return;
    }
    m_passbandAlpha = clamped;
    emit viewChanged();
    refreshOverlays();
}

void WaterfallView::refreshOverlays() {
    if (m_spectrumOverlay != nullptr) {
        SpectrumFrameInput input;
        if (m_waterfall != nullptr) {
            input.canvasWidth = m_waterfall->rowWidth();
        }
        // In the LCD layout the scope occupies the waterfall band itself, so the
        // trace is drawn over the live surface and the dBm ruler needs a tall
        // field. The overlay item is sized to the band by the screen.
        input.y0 = 0.0;
        input.y1 = m_spectrumOverlay->height() > 0.0 ? m_spectrumOverlay->height() : 240.0;
        input.values = m_spectrum.values();
        input.peakValues = m_spectrum.peakValues();
        input.hasTextCache = true;
        input.foreground = false;
        m_spectrumOverlay->setDrawList(ituner::core::spectrumDrawList(input));
        m_spectrumOverlay->setVisible(m_spectrum.enabled());
    }

    if (m_passbandOverlay != nullptr) {
        PassbandOverlayInput input;
        if (m_waterfall != nullptr) {
            input.canvasWidth = m_waterfall->rowWidth();
        }
        input.spanKhz = m_spanKhz;
        input.lowCutHz = m_filter.lowHz;
        input.highCutHz = m_filter.highHz;
        input.y0 = 0.0;
        input.y1 = m_passbandOverlay->height() > 0.0 ? m_passbandOverlay->height() : 252.0;
        input.alpha = m_passbandAlpha;
        input.tunedOffsetHz = 0.0;
        input.labelWidthPx = measurePassbandLabel(m_filter.lowHz, m_filter.highHz);
        m_passbandOverlay->setDrawList(ituner::core::passbandOverlayDrawList(input));
        m_passbandOverlay->setVisible(m_passbandAlpha > 0.01);
    }
}

void WaterfallView::setLastGesture(const QString &name) {
    if (m_lastGesture == name) {
        return;
    }
    m_lastGesture = name;
    emit gesturesChanged();
}

QString WaterfallView::beginTouch(double x, double y) {
    m_startX = x;
    m_startY = y;
    m_inertiaVelocityKhzS = 0.0;
    m_dragEdge = FilterEdge::None;
    m_gesture = Gesture::None;

    // The control group is consulted before the waterfall, exactly as the Python
    // input chain does, so a tap on a button can never become a tune.
    const WaterfallControl control = ituner::core::waterfallControlAt(m_controls, x, y);
    switch (control) {
    case WaterfallControl::ZoomMinus:
        m_gesture = Gesture::ZoomButton;
        applyZoomStep(-1);
        setLastGesture(QStringLiteral("zoom_minus"));
        return m_lastGesture;
    case WaterfallControl::ZoomPlus:
        m_gesture = Gesture::ZoomButton;
        applyZoomStep(1);
        setLastGesture(QStringLiteral("zoom_plus"));
        return m_lastGesture;
    case WaterfallControl::AsrToggle:
        m_gesture = Gesture::ZoomButton;
        setLastGesture(QStringLiteral("asr_toggle"));
        return m_lastGesture;
    case WaterfallControl::FilterToggle:
    case WaterfallControl::SpectrumToggle:
        // The LCD presentation leaves these to the Home rail. The touch is
        // swallowed rather than silently mistuning the receiver.
        m_gesture = Gesture::ZoomButton;
        setLastGesture(QStringLiteral("view_button"));
        return m_lastGesture;
    case WaterfallControl::None:
    case WaterfallControl::Tune:
        break;
    }

    const ituner::core::FilterCuts view = ituner::core::filterViewOffsets(m_filter.lowHz,
                                                                         m_filter.highHz);
    m_dragEditLimit = ituner::core::filterEditLimit(view.lowHz, view.highHz);
    if (m_filterSheetOpen && m_filterEditBox.contains(x, y)) {
        const FilterEdge edge = ituner::core::filterHandleAt(
            x, view, m_filterEditBox.x0, m_filterEditBox.x1, m_dragEditLimit);
        if (edge != FilterEdge::None) {
            m_dragEdge = edge;
            m_gesture = Gesture::PassbandEdge;
            setLastGesture(QStringLiteral("passband_edge"));
            return m_lastGesture;
        }
    }

    if (!ituner::core::isWaterfallTuneTouch(m_controls, x, y)) {
        m_gesture = Gesture::ZoomButton;
        setLastGesture(QStringLiteral("swallowed"));
        return m_lastGesture;
    }

    m_gesture = Gesture::Tune;
    m_startFrequencyKhz = m_frequencyKhz;
    m_dragFrequencyKhz = m_frequencyKhz;
    setLastGesture(QStringLiteral("tune"));
    return m_lastGesture;
}

void WaterfallView::moveTouch(double x, double y, double velocityPxS) {
    if (m_gesture == Gesture::PassbandEdge) {
        const double cut = ituner::core::filterCutAtX(x, m_filterEditBox.x0, m_filterEditBox.x1,
                                                      m_dragEditLimit, 0.0);
        // Only the grabbed edge moves; the other is bounded by `set_filter`'s
        // ordering rule, so the two edges can never cross.
        const ituner::core::FilterCutsResult result =
            m_dragEdge == FilterEdge::Low
                ? ituner::core::applyFilterCuts(m_filter, std::optional<double>(cut), std::nullopt)
                : ituner::core::applyFilterCuts(m_filter, std::nullopt,
                                                std::optional<double>(cut));
        if (result.changed) {
            m_filter = result.cuts;
            emit filterChanged();
            refreshOverlays();
        }
        return;
    }
    if (m_gesture != Gesture::Tune) {
        return;
    }
    // The drag only becomes a tune once it is a deliberate horizontal gesture;
    // until then the frequency stays where it was.
    if (!ituner::core::isDeliberateWaterfallDrag(m_startX, m_startY, x, y, m_swipe)) {
        return;
    }
    const double sensitivity = ituner::core::swipeEffectiveSensitivity(velocityPxS, m_swipe);
    const double span = ituner::core::receiverDragSpan(m_spanKhz, QStringLiteral("kiwi"), 0.0,
                                                      m_controls.canvasWidth);
    const double frequency = ituner::core::retuneFromDrag(m_startFrequencyKhz, m_startX, x, span,
                                                          m_controls.canvasWidth,
                                                          m_swipe.invertTune, sensitivity);
    const int step = ituner::core::receiverTuneStepHz(m_zoom, m_swipe.tuneStepHz,
                                                      QStringLiteral("kiwi"),
                                                      m_controls.canvasWidth);
    m_dragFrequencyKhz = ituner::core::snapFrequencyKhz(frequency, step);
    m_frequencyKhz = m_dragFrequencyKhz;
    emit viewChanged();
}

void WaterfallView::endTouch(double x, double y, double velocityPxS) {
    if (m_gesture == Gesture::Tune) {
        const double span = ituner::core::receiverDragSpan(m_spanKhz, QStringLiteral("kiwi"), 0.0,
                                                           m_controls.canvasWidth);
        if (!ituner::core::isDeliberateWaterfallDrag(m_startX, m_startY, x, y, m_swipe)) {
            // A tap tunes to the touched frequency, with no direction term.
            const double frequency = ituner::core::retuneFromTap(x, m_frequencyKhz, span,
                                                                 m_controls.canvasWidth);
            const int step = ituner::core::receiverTuneStepHz(m_zoom, m_swipe.tuneStepHz,
                                                              QStringLiteral("kiwi"),
                                                              m_controls.canvasWidth);
            m_frequencyKhz = ituner::core::snapFrequencyKhz(frequency, step);
        } else {
            // A release after real travel carries momentum, which `advance`
            // decays. With the default inertia strength of zero it is a no-op,
            // exactly as in the Python renderer.
            m_inertiaVelocityKhzS = ituner::core::swipeInertiaVelocityKhzS(
                velocityPxS, span, m_controls.canvasWidth, m_swipe.invertTune, m_swipe);
        }
        emit viewChanged();
    }
    m_gesture = Gesture::None;
    m_dragEdge = FilterEdge::None;
}

void WaterfallView::advance(double dt) {
    if (m_inertiaVelocityKhzS == 0.0) {
        return;
    }
    const double clamped = std::clamp(dt, 0.0, 0.05);
    m_frequencyKhz += m_inertiaVelocityKhzS * clamped;
    m_inertiaVelocityKhzS =
        ituner::core::decayInertiaVelocity(m_inertiaVelocityKhzS, clamped, m_swipe.inertiaTau);
    if (std::abs(m_inertiaVelocityKhzS) < 1e-4) {
        m_inertiaVelocityKhzS = 0.0;
    }
    emit viewChanged();
}

void WaterfallView::applyZoomStep(int delta) {
    setView(m_frequencyKhz, m_zoom + delta);
}

}  // namespace ituner::ui
