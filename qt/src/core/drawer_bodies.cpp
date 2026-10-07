#include "drawer_bodies.h"

#include <algorithm>
#include <cmath>

#include <drawer_geometry.h>
#include <waterfall_model.h>

namespace ituner::core {

namespace {

/// The Python `clamp(value, low, high)`, which answers `low` when `low > high`.
double pythonClamp(double value, double low, double high) {
    return std::max(low, std::min(high, value));
}

/// `int(clamp(value, 0, maximum))`: the form the drawer uses on every level it
/// indexes a preset table with.
int clampLevel(int value, int maximum) {
    return static_cast<int>(pythonClamp(value, 0.0, static_cast<double>(maximum)));
}

}  // namespace

/// `clamp(value / max(1, maximum), 0.0, 1.0)`, the fill the Python slider tile
/// draws.
double DrawerTile::fraction() const {
    return pythonClamp(value / std::max(1.0, maximum), 0.0, 1.0);
}

QVector<DrawerTile> audioDrawerTiles(const AudioControls &controls, double lowCutHz,
                                     double highCutHz, const QString &radioMode,
                                     const QString &audioBackend, double logicalHeight,
                                     double logicalWidth, double railX0) {
    const AudioDrawerBoxes boxes = audioDrawerBoxes(logicalHeight, logicalWidth, railX0);
    const int voiceLevel = clampLevel(controls.voiceCleanLevel, voiceCleanPresets().size() - 1);
    const int hfLevel = clampLevel(controls.hfEnhanceLevel, hfEnhancePresets().size() - 1);
    const int toneProfile = clampLevel(controls.toneProfile, tonePresets().size() - 1);
    const bool hfActive = hfLevel > 0;
    const int squelch = controls.squelchLevel;
    const int squelchCeiling = squelchMaximum(radioMode);
    const int denoiseLevel = clampLevel(controls.denoiseLevel, denoisePresetNames().size() - 1);
    // Denoise and the two listener processors are exclusive on purpose: a
    // processor that is running takes over, so the Denoise tile says BYPASS
    // rather than pretending it is still filtering.
    const bool denoiseActive = denoiseLevel > 0 && !(voiceLevel > 0 || hfActive);

    QVector<DrawerTile> tiles;
    tiles.append({QStringLiteral("mute"), QStringLiteral("MUTE"),
                  controls.mute ? QStringLiteral("ON") : QStringLiteral("OFF"), controls.mute,
                  boxes.mute});
    tiles.append({QStringLiteral("voice_clean"), QStringLiteral("VOICE"),
                  voiceCleanPresets().at(voiceLevel), voiceLevel > 0, boxes.voiceClean});
    tiles.append({QStringLiteral("hf_enhance"), QStringLiteral("HF ENH"),
                  hfEnhancePresets().at(hfLevel), hfActive, boxes.hfEnhance});

    QString squelchDetail;
    if (squelch <= 0) {
        squelchDetail = QStringLiteral("OFF");
    } else if (squelchCeiling == 99) {
        squelchDetail = QString::number(squelch);
    } else {
        squelchDetail = QString::number(squelch) + QStringLiteral(" dB");
    }
    tiles.append({QStringLiteral("squelch"), QStringLiteral("SQUELCH"), squelchDetail, squelch > 0,
                  boxes.squelch, static_cast<double>(squelch),
                  static_cast<double>(squelchCeiling)});

    const QString agcDetail =
        controls.agc && !controls.agcHang
            ? QStringLiteral("AUTO")
            : (controls.agc ? QStringLiteral("HANG") : QStringLiteral("MANUAL"));
    tiles.append({QStringLiteral("agc"), QStringLiteral("AGC"), agcDetail, controls.agc, boxes.agc});
    tiles.append({QStringLiteral("blanker"), QStringLiteral("BLANKER"),
                  QStringList{QStringLiteral("OFF"), QStringLiteral("STANDARD"),
                              QStringLiteral("WILD")}
                      .at(clampLevel(controls.nbAlgo, 2)),
                  controls.nbAlgo > 0, boxes.blanker});
    tiles.append({QStringLiteral("denoise"), QStringLiteral("DENOISE"),
                  voiceLevel > 0 || hfActive ? QStringLiteral("BYPASS")
                                             : denoisePresetNames().at(denoiseLevel),
                  denoiseActive, boxes.denoise, static_cast<double>(denoiseLevel),
                  static_cast<double>(denoiseSliderPositions().size() - 1)});
    tiles.append({QStringLiteral("notch"), QStringLiteral("NOTCH"),
                  controls.autonotch ? QStringLiteral("ON") : QStringLiteral("OFF"),
                  controls.autonotch, boxes.notch});
    tiles.append({QStringLiteral("deemphasis"), QStringLiteral("DE-EMPH"),
                  QStringList{QStringLiteral("OFF"), QStringLiteral("75 uS"),
                              QStringLiteral("50 uS")}
                      .at(clampLevel(controls.deemphasis, 2)),
                  controls.deemphasis > 0, boxes.deemphasis});
    tiles.append({QStringLiteral("tone"), QStringLiteral("TONE"), tonePresets().at(toneProfile),
                  toneProfile > 0, boxes.tone});
    tiles.append({QStringLiteral("filter"), QStringLiteral("PASSBAND"),
                  formatFilterWidth(highCutHz - lowCutHz), false, boxes.filter});
    tiles.append({QStringLiteral("reset"), QStringLiteral("RESET"), QStringLiteral("DEFAULTS"),
                  false, boxes.reset});
    const bool direct = audioBackend == QStringLiteral("alsa");
    tiles.append({QStringLiteral("backend"), QStringLiteral("OUTPUT"),
                  direct ? QStringLiteral("ALSA DIRECT") : QStringLiteral("PIPEWIRE"), direct,
                  boxes.backend});
    return tiles;
}

DisplayState defaultDisplayState() {
    DisplayState state;
    state.floor = static_cast<double>(waterfallDefaultFloor());
    state.ceiling = static_cast<double>(waterfallDefaultCeiling());
    state.speed = waterfallDefaultSpeed();
    state.palette = waterfallDefaultPalette();
    return state;
}

QVector<DrawerTile> displayDrawerTiles(const DisplayState &state, double logicalHeight,
                                       double logicalWidth, double railX0) {
    const DisplayDrawerBoxes boxes = displayDrawerBoxes(logicalHeight, logicalWidth, railX0);
    const bool expanded = state.instrumentLayout == QStringLiteral("expanded");
    QVector<DrawerTile> tiles;
    // Short labels so a half-width tile stays legible: INSTRUMENTS -> LAYOUT.
    tiles.append({QStringLiteral("reset"), QStringLiteral("RESET DISPLAY"),
                  QStringLiteral("DEFAULTS"), false, boxes.reset});
    tiles.append({QStringLiteral("instruments"), QStringLiteral("LAYOUT"),
                  state.instrumentLayout.toUpper(), expanded, boxes.instruments});
    // The waterfall's old DISPLAY button only revealed the scope drag rail; that
    // property now lives in this drawer as its own launcher.
    tiles.append({QStringLiteral("scope"), QStringLiteral("SCOPE"), QStringLiteral("DRAG"), false,
                  boxes.scope});
    tiles.append({QStringLiteral("spectrum"), QStringLiteral("SPECTRUM"),
                  state.spectrumEnabled ? QStringLiteral("ON") : QStringLiteral("OFF"),
                  state.spectrumEnabled, boxes.spectrum});
    tiles.append({QStringLiteral("auto"), QStringLiteral("AUTO"),
                  state.autoScale ? QStringLiteral("ON") : QStringLiteral("OFF"), state.autoScale,
                  boxes.autoScale});
    // The floor and ceiling rows are sliders whose range is derived from the
    // other end of the pair, so the track can never show a fill that the drag
    // could not reach.
    const double floorMaximum = std::min(220.0, state.ceiling - 30.0);
    const double ceilingMinimum = state.floor + 30.0;
    tiles.append({QStringLiteral("floor"), QStringLiteral("FLOOR"), formatDecimals(state.floor, 0),
                  !state.autoScale, boxes.floor, state.floor - 40.0, floorMaximum - 40.0});
    tiles.append({QStringLiteral("ceiling"), QStringLiteral("CEILING"),
                  formatDecimals(state.ceiling, 0), !state.autoScale, boxes.ceiling,
                  state.ceiling - ceilingMinimum, 255.0 - ceilingMinimum});
    return tiles;
}

QVector<DisplayControl> displayDrawerControls(const DisplayState &state, double logicalHeight,
                                             double logicalWidth, double railX0) {
    const DisplayDrawerBoxes boxes = displayDrawerBoxes(logicalHeight, logicalWidth, railX0);
    QVector<DisplayControl> controls;
    for (const DisplayRateBox &rate : boxes.rates) {
        controls.append({QStringLiteral("rate_") + QString::number(rate.rate), rate.label,
                         rate.rate == state.speed, rate.box});
    }
    for (const DisplayPaletteBox &palette : boxes.palettes) {
        controls.append({QStringLiteral("palette_") + palette.name, palette.label,
                         palette.name == state.palette, palette.box});
    }
    return controls;
}

}  // namespace ituner::core
