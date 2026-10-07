// The waterfall screen's display state and touch handling.
//
// This object is the seam between the QML scene and the tested core. It owns no
// geometry and no tuning math of its own: the levels come from
// `WaterfallLeveler`, the scope from `SpectrumState`, the overlay draw lists from
// `spectrumDrawList` and `passbandOverlayDrawList`, the control boxes from
// `lcdWaterfallControlGeometry`, the tuning from the `swipe_gesture` model, and
// the passband edits from `applyFilterCuts`. Everything it does is a call into
// one of those, which is what makes the screen testable without a panel.
//
// The Python original is one very large inline input loop. This is a faithful
// reduction of the parts the waterfall owns: the control taps, the deliberate
// drag gate, the positional retune, the tap retune, the release inertia, and the
// passband edge drag.

#pragma once

#include <QElapsedTimer>
#include <QObject>
#include <QString>

#include <passband_overlay.h>
#include <spectrum_model.h>
#include <swipe_gesture.h>
#include <tuning.h>
#include <waterfall_controls.h>

#include "overlay_item.h"
#include "waterfall_item.h"

namespace ituner::ui {

class WaterfallView : public QObject {
    Q_OBJECT
    Q_PROPERTY(ui::WaterfallItem *waterfall READ waterfall WRITE setWaterfall NOTIFY waterfallChanged)
    Q_PROPERTY(ui::OverlayItem *spectrumOverlay READ spectrumOverlay WRITE setSpectrumOverlay
                   NOTIFY spectrumOverlayChanged)
    Q_PROPERTY(ui::OverlayItem *passbandOverlay READ passbandOverlay WRITE setPassbandOverlay
                   NOTIFY passbandOverlayChanged)
    Q_PROPERTY(bool spectrumEnabled READ spectrumEnabled NOTIFY viewChanged)
    Q_PROPERTY(double frequencyKhz READ frequencyKhz NOTIFY viewChanged)
    Q_PROPERTY(int zoom READ zoom NOTIFY viewChanged)
    Q_PROPERTY(double spanKhz READ spanKhz NOTIFY viewChanged)
    Q_PROPERTY(double lowCutHz READ lowCutHz NOTIFY filterChanged)
    Q_PROPERTY(double highCutHz READ highCutHz NOTIFY filterChanged)
    Q_PROPERTY(bool passbandVisible READ passbandVisible NOTIFY viewChanged)
    Q_PROPERTY(bool filterSheetOpen READ filterSheetOpen WRITE setFilterSheetOpen NOTIFY
                   filterSheetOpenChanged)
    Q_PROPERTY(QString lastGesture READ lastGesture NOTIFY gesturesChanged)

public:
    explicit WaterfallView(QObject *parent = nullptr);

    WaterfallItem *waterfall() const { return m_waterfall; }
    void setWaterfall(WaterfallItem *item);

    OverlayItem *spectrumOverlay() const { return m_spectrumOverlay; }
    void setSpectrumOverlay(OverlayItem *item);

    OverlayItem *passbandOverlay() const { return m_passbandOverlay; }
    void setPassbandOverlay(OverlayItem *item);

    bool spectrumEnabled() const { return m_spectrum.enabled(); }
    double frequencyKhz() const { return m_frequencyKhz; }
    int zoom() const { return m_zoom; }
    double spanKhz() const { return m_spanKhz; }
    double lowCutHz() const { return m_filter.lowHz; }
    double highCutHz() const { return m_filter.highHz; }
    bool passbandVisible() const { return m_passbandAlpha > 0.01; }
    /// Whether the filter sheet owns the passband handles. In the LCD layout the
    /// rail drawer owns them, so the waterfall's own edges are not draggable
    /// until the sheet is open -- the same gate `filter_panel_open` applies.
    bool filterSheetOpen() const { return m_filterSheetOpen; }
    void setFilterSheetOpen(bool open);
    QString lastGesture() const { return m_lastGesture; }

    /// The resolved waterfall control layout, exposed so the screen and the tests
    /// read one definition.
    const ituner::core::WaterfallControlGeometry &controls() const { return m_controls; }

    /// The tuning configuration, seeded with the Python parser defaults.
    const ituner::core::SwipeConfig &swipeConfig() const { return m_swipe; }
    void setSwipeConfig(const ituner::core::SwipeConfig &config) { m_swipe = config; }

    Q_INVOKABLE void setSpectrumEnabled(bool enabled);
    Q_INVOKABLE void toggleSpectrum();

    /// The DISP drawer's waterfall settings.
    Q_INVOKABLE void setDisplayLevels(double floor, double ceiling, bool autoLevel, int speed,
                                      const QString &palette);

    /// Push one W/F line. The scope is fed from the same samples, exactly as the
    /// Python worker does.
    Q_INVOKABLE int pushWaterfallLine(const QList<int> &samples, int rowPixels, double floor,
                                      double ceiling);

    Q_INVOKABLE void setView(double frequencyKhz, int zoom);
    Q_INVOKABLE void setFilterCuts(double lowCutHz, double highCutHz);

    /// The passband overlay's fade, driven by Waterfall Focus.
    Q_INVOKABLE void setPassbandAlpha(double alpha);

    /// Recompute both overlays from the current state.
    Q_INVOKABLE void refreshOverlays();

    /// The live scope trace, for the diagnostics sheet and the tests.
    Q_INVOKABLE QList<double> spectrumValues() const { return m_spectrum.values(); }
    /// The ten-second max-hold envelope.
    Q_INVOKABLE QList<double> spectrumPeakValues() const { return m_spectrum.peakValues(); }

    /// Touch handling. Returns the name of the gesture that started, which the
    /// test asserts on.
    Q_INVOKABLE QString beginTouch(double x, double y);
    Q_INVOKABLE void moveTouch(double x, double y, double velocityPxS);
    Q_INVOKABLE void endTouch(double x, double y, double velocityPxS);
    /// Advance the release inertia by the elapsed seconds.
    Q_INVOKABLE void advance(double dt);

signals:
    void waterfallChanged();
    void spectrumOverlayChanged();
    void passbandOverlayChanged();
    void filterSheetOpenChanged();
    void viewChanged();
    void filterChanged();
    void gesturesChanged();

private:
    enum class Gesture { None, Tune, ZoomButton, PassbandEdge };
    void applyZoomStep(int delta);
    void setLastGesture(const QString &name);

    WaterfallItem *m_waterfall = nullptr;
    OverlayItem *m_spectrumOverlay = nullptr;
    OverlayItem *m_passbandOverlay = nullptr;

    ituner::core::SwipeConfig m_swipe;
    /// `FILTER_EDIT_BOX`, the sheet's direct-manipulation strip.
    ituner::core::ControlBox m_filterEditBox{42.0, 116.0, 918.0, 214.0};
    QElapsedTimer m_clock;
    bool m_filterSheetOpen = false;
    ituner::core::WaterfallControlGeometry m_controls =
        ituner::core::lcdWaterfallControlGeometry();
    ituner::core::SpectrumState m_spectrum;
    ituner::core::FilterCuts m_filter;

    double m_frequencyKhz = 7075.794;
    int m_zoom = 13;
    double m_spanKhz = 0.0;
    double m_passbandAlpha = 0.82;

    Gesture m_gesture = Gesture::None;
    double m_startX = 0.0;
    double m_startY = 0.0;
    double m_startFrequencyKhz = 0.0;
    double m_dragFrequencyKhz = 0.0;
    double m_inertiaVelocityKhzS = 0.0;
    ituner::core::FilterEdge m_dragEdge = ituner::core::FilterEdge::None;
    double m_dragEditLimit = 0.0;
    double m_centerKhz = 7075.794;
    QString m_lastGesture = QStringLiteral("none");
};

}  // namespace ituner::ui
