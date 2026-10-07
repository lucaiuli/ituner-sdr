#include "audio_controls.h"

#include <algorithm>
#include <cmath>

#include <waterfall_model.h>

namespace ituner::core {

namespace {

/// The Python `clamp(value, low, high)`, which answers `low` when `low > high`.
double pythonClamp(double value, double low, double high) {
    return std::max(low, std::min(high, value));
}

}  // namespace

QStringList voiceCleanPresets() {
    return {QStringLiteral("OFF"), QStringLiteral("MED"), QStringLiteral("STR")};
}

QStringList hfEnhancePresets() {
    return {QStringLiteral("OFF"), QStringLiteral("EPOCH 1"), QStringLiteral("EPOCH 8")};
}

QStringList tonePresets() {
    return {QStringLiteral("OFF"), QStringLiteral("SW"), QStringLiteral("PRES"),
            QStringLiteral("WARM")};
}

QStringList denoisePresetNames() {
    return {QStringLiteral("OFF"),  QStringLiteral("LIGHT"),   QStringLiteral("NORMAL"),
            QStringLiteral("STRONG"), QStringLiteral("STRONG+"), QStringLiteral("MAX")};
}

QVector<double> denoiseSliderPositions() {
    return {0.00, 0.20, 0.40, 0.60, 0.80, 1.00};
}

int denoiseMakeupGainDb(int level) {
    const QVector<int> table = {0, 2, 4, 6, 9, 12};
    return table.at(std::clamp(level, 0, static_cast<int>(table.size()) - 1));
}

QVector<int> hfEnhanceLevels(bool modelsInstalled) {
    QVector<int> levels = {0};
    if (modelsInstalled) {
        levels.append(1);
        levels.append(2);
    }
    return levels;
}

AudioControls defaultAudioControls() {
    AudioControls controls;
    controls.squelchLevel = 0;
    controls.mute = false;
    controls.agc = true;
    controls.agcHang = false;
    controls.deemphasis = 0;
    controls.nbAlgo = 0;
    controls.denoiseLevel = 0;
    controls.voiceCleanLevel = 0;
    controls.hfEnhanceLevel = 0;
    controls.toneProfile = 0;
    controls.autonotch = false;
    return controls;
}

AudioControls applyAudioAction(const AudioControls &controls, const QString &action,
                               const QVector<int> &hfLevels) {
    AudioControls next = controls;
    if (action == QStringLiteral("mute")) {
        next.mute = !controls.mute;
    } else if (action == QStringLiteral("voice_clean")) {
        next.voiceCleanLevel = (controls.voiceCleanLevel + 1) % voiceCleanPresets().size();
        // The two listener processors never stack, so selecting one clears the
        // other rather than layering.
        next.hfEnhanceLevel = 0;
    } else if (action == QStringLiteral("hf_enhance")) {
        const int index = hfLevels.indexOf(controls.hfEnhanceLevel);
        next.hfEnhanceLevel = hfLevels.at((index + 1) % hfLevels.size());
        next.voiceCleanLevel = 0;
    } else if (action == QStringLiteral("tone")) {
        next.toneProfile = (controls.toneProfile + 1) % tonePresets().size();
    } else if (action == QStringLiteral("agc")) {
        // OFF -> AUTO -> HANG -> OFF, which is the order an operator expects
        // from one button.
        if (!controls.agc) {
            next.agc = true;
            next.agcHang = false;
        } else if (!controls.agcHang) {
            next.agcHang = true;
        } else {
            next.agc = false;
            next.agcHang = false;
        }
    } else if (action == QStringLiteral("blanker")) {
        next.nbAlgo = (controls.nbAlgo + 1) % 3;
    } else if (action == QStringLiteral("notch")) {
        next.autonotch = !controls.autonotch;
    } else if (action == QStringLiteral("deemphasis")) {
        next.deemphasis = (controls.deemphasis + 1) % 3;
    } else if (action == QStringLiteral("reset")) {
        next = defaultAudioControls();
    }
    return next;
}

double mainVolumeMuteThreshold() { return 0.005; }

QString mainVolumeLabel(double level) {
    const double clamped = pythonClamp(level, 0.0, 1.0);
    if (clamped <= mainVolumeMuteThreshold()) {
        return QStringLiteral("MUTE");
    }
    return QString::number(pythonRoundToInt(clamped * 100.0)) + QStringLiteral("%");
}

}  // namespace ituner::core
