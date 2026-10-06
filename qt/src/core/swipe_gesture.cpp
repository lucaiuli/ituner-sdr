#include "swipe_gesture.h"

#include <algorithm>
#include <cmath>

#include "tuning.h"
#include "waterfall_model.h"

namespace ituner::core {
namespace {

constexpr int kWaterfallDragStartPx = 14;
constexpr double kWaterfallHorizontalDragRatio = 1.5;

const double *fmdxTuneSteps() {
    static const double steps[]{50000.0, 100000.0, 200000.0};
    return steps;
}

constexpr int kFmdxTuneStepCount = 3;

/// Python's `clamp(value, low, high)` is `max(low, min(high, value))`, not
/// `std::clamp`. The two differ when `low > high`.
double pythonClamp(double value, double low, double high) {
    return std::max(low, std::min(high, value));
}

}  // namespace

double swipeEffectiveSensitivity(double speedPxS, const SwipeConfig &config) {
    const double speed = std::abs(speedPxS);
    if (speed <= config.fastPxS) {
        return config.slowSensitivity;
    }
    // Keep acceleration progressive after normal travel speed instead of
    // jumping from the direct mapping to a fast multiplier at one pixel.
    const double fastRampEnd = config.fastPxS * 1.8;
    const double blend = (speed - config.fastPxS) / (fastRampEnd - config.fastPxS);
    return config.slowSensitivity
           + (config.fastSensitivity - config.slowSensitivity)
                 * pythonClamp(blend, 0.0, 1.0);
}

double updateSwipeVelocity(double instantVelocityPxS, double currentVelocityPxS) {
    // Decelerating must feel immediate: a slow finger movement takes precedence
    // over the preceding quick swipe within the next samples.
    const double velocityBlend =
        std::abs(instantVelocityPxS) < std::abs(currentVelocityPxS) ? 0.72 : 0.46;
    return (1.0 - velocityBlend) * currentVelocityPxS + velocityBlend * instantVelocityPxS;
}

double swipeLiveBoost(double activeBoost, double velocityPxS, const SwipeConfig &config) {
    // Travel boost is tied to live velocity, not merely to the fact that recent
    // swipes were fast. It fades to exactly 1x during fine motion.
    double travelT = pythonClamp((std::abs(velocityPxS) - config.fastPxS) / config.fastPxS,
                                 0.0, 1.0);
    travelT = travelT * travelT * (3.0 - 2.0 * travelT);
    return 1.0 + (activeBoost - 1.0) * travelT;
}

void beginSwipeRepeat(SwipeRepeatState &state, bool movingRight, double now,
                      const SwipeConfig &config) {
    const int direction = movingRight ? 1 : -1;
    if (direction == state.direction && now - state.lastTime <= config.repeatWindowS) {
        state.count = std::min(config.repeatMax, state.count + 1);
    } else {
        state.count = 0;
    }
    state.direction = direction;
    state.lastTime = now;
    state.activeBoost = 1.0 + state.count * config.repeatBoost;
}

SwipeZoomOutcome applySwipeAutoZoom(SwipeZoomState &state, int zoom, double velocityPxS,
                                    double travelPx, int repeatCount,
                                    const SwipeConfig &config) {
    SwipeZoomOutcome outcome;
    const int startZoom = zoom;
    const double speed = std::abs(velocityPxS);

    // Consecutive gestures only widen the view once this gesture itself is
    // moving decisively. That leaves a deliberate slow follow-up drag as fine
    // tuning, even directly after travelling quickly.
    if (!config.fingerTunePositional && !state.repeatZoomApplied
        && repeatCount >= config.repeatZoomThreshold && speed >= config.fastPxS
        && travelPx >= config.fastZoomDistancePx && config.repeatZoomOut) {
        int newZoom = zoom;
        if (zoom > config.repeatZoomMin) {
            const int spare = std::max(0, config.autoZoomBudget - state.autoZoomLevelsUsed);
            newZoom = std::max(config.repeatZoomMin,
                               zoom - std::min(config.repeatZoomOut, spare));
        }
        const int appliedLevels = zoom - newZoom;
        if (appliedLevels > 0) {
            zoom = newZoom;
            state.autoZoomLevelsUsed += appliedLevels;
            state.repeatZoomChanged = true;
        }
        state.repeatZoomApplied = true;
    }

    if (!config.fingerTunePositional && !state.fastSweepZoomApplied
        && state.repeatZoomApplied && speed >= config.fastZoomPxS
        && travelPx >= config.fastZoomDistancePx && config.fastZoomOut) {
        if (zoom > config.fastZoomMin) {
            const int remaining =
                std::max(0, config.fastZoomOut - (state.repeatZoomChanged ? 1 : 0));
            const int allowedLevels =
                std::min(remaining, std::max(0, config.autoZoomBudget - state.autoZoomLevelsUsed));
            const int newZoom = std::max(config.fastZoomMin, zoom - allowedLevels);
            const int appliedLevels = zoom - newZoom;
            if (appliedLevels > 0) {
                zoom = newZoom;
                state.autoZoomLevelsUsed += appliedLevels;
            }
        }
        state.fastSweepZoomApplied = true;
    }

    outcome.zoom = zoom;
    outcome.appliedLevels = startZoom - zoom;
    return outcome;
}

bool isDeliberateWaterfallDrag(double startX, double startY, double x, double y,
                               const SwipeConfig &config) {
    const double dx = std::abs(x - startX);
    const double dy = std::abs(y - startY);
    return dx >= std::max(static_cast<double>(config.swipeStartPx),
                          static_cast<double>(kWaterfallDragStartPx))
           && dx >= dy * kWaterfallHorizontalDragRatio;
}

double retuneDeltaFromDrag(double deltaPx, double spanKhz, double canvasWidthPx,
                           bool invertTune, double sensitivity) {
    const double hzPerPx = spanKhz * 1000.0 / canvasWidthPx;
    const double direction = invertTune ? 1.0 : -1.0;
    return direction * deltaPx * hzPerPx * sensitivity / 1000.0;
}

double retuneFromDrag(double startFreqKhz, double startX, double x, double spanKhz,
                      double canvasWidthPx, bool invertTune, double sensitivity) {
    return startFreqKhz
           + retuneDeltaFromDrag(x - startX, spanKhz, canvasWidthPx, invertTune, sensitivity);
}

double retuneFromTap(double x, double freqKhz, double spanKhz, double canvasWidthPx) {
    const double hzPerPx = spanKhz * 1000.0 / canvasWidthPx;
    return freqKhz + (x - canvasWidthPx / 2.0) * hzPerPx / 1000.0;
}

double snapFrequencyKhz(double freqKhz, int stepHz) {
    const int step = std::max(1, stepHz);
    return pythonRoundToInt(freqKhz * 1000.0 / step) * step / 1000.0;
}

double receiverDragSpan(double spanKhz, const QString &receiverType, double fmStepHz,
                        double canvasWidthPx) {
    if (receiverType == QStringLiteral("fmdx")) {
        return fmStepHz / 1000.0 * canvasWidthPx / 40.0;
    }
    return spanKhz;
}

int fingerTuneStepHz(int zoom, int baseStepHz, double canvasWidthPx) {
    const int base = std::max(1, baseStepHz);
    const double spanHz = std::max(1.0, zoomToSpanKhz(zoom) * 1000.0);
    const double targetStepHz = spanHz / std::max(1.0, canvasWidthPx);
    const double allowedStepHz = std::min(static_cast<double>(base), targetStepHz);
    // Familiar receiver increments, ordered so the chosen detent is never larger
    // than the on-screen resolution it is meant to control.
    static const int preferredSteps[]{1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000};
    if (allowedStepHz < 1.0) {
        return 1;
    }
    int best = 1;
    for (const int step : preferredSteps) {
        if (step <= allowedStepHz) {
            best = step;
        }
    }
    return best;
}

int receiverTuneStepHz(int zoom, int kiwiStepHz, const QString &receiverType,
                       double canvasWidthPx, double fmStepHz) {
    if (receiverType == QStringLiteral("fmdx")) {
        for (int index = 0; index < kFmdxTuneStepCount; ++index) {
            if (fmStepHz == fmdxTuneSteps()[index]) {
                return static_cast<int>(fmStepHz);
            }
        }
        return 100000;
    }
    return fingerTuneStepHz(zoom, kiwiStepHz, canvasWidthPx);
}

double swipeInertiaVelocityKhzS(double velocityPxS, double startSpanKhz,
                                double canvasWidthPx, bool invertTune,
                                const SwipeConfig &config) {
    const double sensitivity = swipeEffectiveSensitivity(velocityPxS, config);
    return retuneDeltaFromDrag(velocityPxS, startSpanKhz, canvasWidthPx, invertTune,
                               sensitivity)
           * config.inertiaStrength;
}

double decayInertiaVelocity(double velocityKhzS, double dt, double tau) {
    return velocityKhzS * std::exp(-dt / tau);
}

int waterfallDragStartPx() {
    return kWaterfallDragStartPx;
}

double waterfallHorizontalDragRatio() {
    return kWaterfallHorizontalDragRatio;
}

SwipeConfig normalizeSwipeConfig(SwipeConfig config) {
    config.maxZoom = static_cast<int>(pythonClamp(config.maxZoom, 0.0, displayMaxZoom()));
    config.stationZoom =
        static_cast<int>(pythonClamp(config.stationZoom, 0.0, displayMaxZoom()));

    if (config.swipeSensitivity.has_value()) {
        config.slowSensitivity = *config.swipeSensitivity;
    }
    config.slowSensitivity = std::max(0.1, config.slowSensitivity);
    config.fineSensitivity =
        pythonClamp(config.fineSensitivity, 0.02, config.slowSensitivity);
    config.finePxS = pythonClamp(config.finePxS, 10.0,
                                 std::max(11.0, config.fastPxS - 1.0));
    config.fastSensitivity = std::max(config.slowSensitivity, config.fastSensitivity);
    config.fastPxS = std::max(50.0, config.fastPxS);
    config.fastZoomPxS = std::max(config.fastPxS, config.fastZoomPxS);
    config.fastZoomDistancePx = std::max(config.swipeStartPx, config.fastZoomDistancePx);
    config.fastZoomOut = std::max(0, config.fastZoomOut);
    config.fastZoomMin =
        static_cast<int>(pythonClamp(config.fastZoomMin, 0.0, config.maxZoom));
    config.autoZoomBudget = std::max(0, config.autoZoomBudget);
    config.repeatWindowS = std::max(0.2, config.repeatWindowS);
    config.repeatBoost = std::max(0.0, config.repeatBoost);
    config.repeatMax = std::max(0, config.repeatMax);
    config.repeatZoomOut = std::max(0, config.repeatZoomOut);
    config.repeatZoomThreshold = std::max(1, config.repeatZoomThreshold);
    config.repeatZoomMin =
        static_cast<int>(pythonClamp(config.repeatZoomMin, 0.0, config.maxZoom));
    config.tuneStepHz = std::max(1, config.tuneStepHz);
    config.inertiaMinPxS = std::max(0.0, config.inertiaMinPxS);
    config.inertiaStrength = std::max(0.0, config.inertiaStrength);
    config.inertiaTau = std::max(0.05, config.inertiaTau);
    return config;
}

}  // namespace ituner::core
