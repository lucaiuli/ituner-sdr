// The swipe/tuning gesture model, independent of any renderer.
//
// The Python original is split between a handful of module-level helpers and a
// large inline input loop in `UI/kiwi_gl_display.py`. This port lifts the
// separable behaviour into pure functions over touch samples: the sensitivity
// ramp, the velocity tracking and its travel boost, the consecutive-swipe repeat
// machine, the automatic zoom-out it triggers, the deliberate-drag gate, the
// drag tap and detent tuning math, and the inertia decay. The QML input layer
// owns only time stamps and coordinates.
//
// Everything here reads a `SwipeConfig`, whose defaults are the argument
// defaults in the Python parser, and `normalizeSwipeConfig` reproduces the
// startup coercion block that keeps the values in range.

#pragma once

#include <QString>

#include <optional>

namespace ituner::core {

/// Gesture tuning, seeded with the Python parser defaults.
struct SwipeConfig {
    int tapPx = 12;
    int swipeStartPx = 4;
    double fineSensitivity = 0.12;
    double finePxS = 130.0;
    double slowSensitivity = 1.15;
    double fastSensitivity = 2.4;
    double fastPxS = 420.0;
    double fastZoomPxS = 1400.0;
    int fastZoomDistancePx = 180;
    int fastZoomOut = 5;
    int fastZoomMin = 4;
    int autoZoomBudget = 5;
    double repeatWindowS = 1.4;
    double repeatBoost = 0.65;
    int repeatMax = 3;
    int repeatZoomOut = 1;
    int repeatZoomThreshold = 2;
    int repeatZoomMin = 11;
    double inertiaMinPxS = 520.0;
    double inertiaStrength = 0.0;
    double inertiaTau = 0.30;
    int maxZoom = 16;
    int stationZoom = 13;
    int tuneStepHz = 100;
    bool fingerTunePositional = true;
    bool invertTune = false;

    /// The suppressed `--swipe-sensitivity` alias. When set it replaces the slow
    /// sensitivity before coercion, exactly as the Python startup block does.
    std::optional<double> swipeSensitivity;
};

/// Clamp and cross-limit the configuration exactly as the Python startup block
/// does. Returns the coerced copy; the input is not modified.
SwipeConfig normalizeSwipeConfig(SwipeConfig config);

/// `swipe_effective_sensitivity`: a constant slow rate below the fast
/// threshold, then a progressive ramp to the fast rate at 1.8x the threshold.
double swipeEffectiveSensitivity(double speedPxS, const SwipeConfig &config);

/// The exponential velocity filter used while dragging. A decelerating sample
/// takes precedence (blend 0.72) over an accelerating one (0.46).
double updateSwipeVelocity(double instantVelocityPxS, double currentVelocityPxS);

/// The live travel boost, which fades to exactly 1x during fine motion.
double swipeLiveBoost(double activeBoost, double velocityPxS, const SwipeConfig &config);

/// The consecutive-swipe repeat machine (`begin_swipe`).
struct SwipeRepeatState {
    int direction = 0;
    double lastTime = 0.0;
    int count = 0;
    double activeBoost = 1.0;
};

/// Record the start of a gesture at `now`. `movingRight` is `x > start_x`. A
/// same-direction gesture inside the repeat window widens the boost.
void beginSwipeRepeat(SwipeRepeatState &state, bool movingRight, double now,
                      const SwipeConfig &config);

/// The automatic zoom-out state shared by the repeat-swipe and fast-sweep
/// branches. Kept mutable because the two branches are ordered and each reads
/// what the other wrote.
struct SwipeZoomState {
    bool repeatZoomApplied = false;
    bool repeatZoomChanged = false;
    bool fastSweepZoomApplied = false;
    int autoZoomLevelsUsed = 0;
};

struct SwipeZoomOutcome {
    int zoom = 0;
    int appliedLevels = 0;
};

/// Apply the repeat-swipe and fast-sweep auto zoom-out for one drag sample.
/// `travelPx` is `abs(x - start_x)` and `repeatCount` comes from
/// `SwipeRepeatState::count`. The returned zoom is the caller's new zoom, or the
/// input when nothing changed.
SwipeZoomOutcome applySwipeAutoZoom(SwipeZoomState &state, int zoom, double velocityPxS,
                                    double travelPx, int repeatCount,
                                    const SwipeConfig &config);

/// A deliberate horizontal waterfall-tuning drag, not an accidental touch.
bool isDeliberateWaterfallDrag(double startX, double startY, double x, double y,
                               const SwipeConfig &config);

/// `retune_delta_from_drag`: the frequency change for a pixel delta.
double retuneDeltaFromDrag(double deltaPx, double spanKhz, double canvasWidthPx,
                           bool invertTune, double sensitivity);

/// `retune_from_drag`: the frequency for an absolute finger position.
double retuneFromDrag(double startFreqKhz, double startX, double x, double spanKhz,
                      double canvasWidthPx, bool invertTune, double sensitivity);

/// `retune_from_tap`.
double retuneFromTap(double x, double freqKhz, double spanKhz, double canvasWidthPx);

/// `snap_frequency_khz`: align a frequency to the visible tuning step, using
/// Python's half-to-even `round`.
double snapFrequencyKhz(double freqKhz, int stepHz);

/// `receiver_drag_span`. FM-DX drags use a fixed step-based span.
double receiverDragSpan(double spanKhz, const QString &receiverType, double fmStepHz,
                        double canvasWidthPx);

/// `finger_tune_step_hz`: the finest familiar 1-2-5 detent that stays no coarser
/// than one horizontal display pixel.
int fingerTuneStepHz(int zoom, int baseStepHz, double canvasWidthPx);

/// `receiver_tune_step_hz`. The canvas width only matters for the Kiwi path,
/// which picks a detent from the visible span.
int receiverTuneStepHz(int zoom, int kiwiStepHz, const QString &receiverType,
                       double canvasWidthPx, double fmStepHz = 100000.0);

/// The inertia velocity imparted at the end of a fast gesture, in kHz/s.
/// `start_span` is the span at gesture start; `sensitivity` is computed from the
/// velocity internally, matching the Python release handler.
double swipeInertiaVelocityKhzS(double velocityPxS, double startSpanKhz,
                                double canvasWidthPx, bool invertTune,
                                const SwipeConfig &config);

/// One decay step for the inertia velocity.
double decayInertiaVelocity(double velocityKhzS, double dt, double tau);

/// The `WATERFALL_DRAG_START_PX` and `WATERFALL_HORIZONTAL_DRAG_RATIO`
/// constants, exposed because the drag gate and the QML hit test both need them.
int waterfallDragStartPx();
double waterfallHorizontalDragRatio();

}  // namespace ituner::core
