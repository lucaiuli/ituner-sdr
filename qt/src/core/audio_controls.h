// The Audio drawer's control state, its preset tables and its labels.
//
// Ported from the audio half of `UI/kiwi_gl_display.py`: `VOICE_CLEAN_PRESETS`,
// `HF_ENHANCE_PRESETS`, `TONE_PRESETS`, `DENOISE_SLIDER_POSITIONS`,
// `DENOISE_MAKEUP_GAIN_DB`, the `kiwi.DENOISE_PRESETS` labels,
// `main_volume_label`, `hf_enhance_available` and `LiveState.reset_audio_controls`.
//
// Two things are deliberate here:
//
// 1. Every string an operator reads in this drawer (`AUTO`, `HANG`, `MANUAL`,
//    `BYPASS`, `ALSA DIRECT`, `75 uS`, `MUTE` ...) is computed in C++ from one
//    state, never assembled in QML, so the drawer cannot say one thing and the
//    device another.
// 2. This module is pure state and tables with no layout in it, so the state
//    transitions are testable without a screen. `applyAudioAction` is a
//    restatement rather than a translation: the Python switch lives inline in
//    the renderer's touch loop, so the capture pins the *result* of the
//    transition (the tiles the drawer draws) rather than the shape of the code.

#pragma once

#include <QString>
#include <QStringList>
#include <QVector>

namespace ituner::core {

/// `VOICE_CLEAN_PRESETS`: the two listener-only processors never stack, so each
/// carries an OFF level of its own.
QStringList voiceCleanPresets();

/// `HF_ENHANCE_PRESETS`.
QStringList hfEnhancePresets();

/// `TONE_PRESETS`: the output-only listening equaliser.
QStringList tonePresets();

/// The labels of `kiwi.DENOISE_PRESETS`, which are the DSP preset names rather
/// than a numeric scale.
QStringList denoisePresetNames();

/// `DENOISE_SLIDER_POSITIONS`: six evenly spaced, discrete settings.
QVector<double> denoiseSliderPositions();

/// `denoise_makeup_gain_db`, clamped into the table.
int denoiseMakeupGainDb(int level);

/// `HF_ENHANCE_MODELS` availability: level 0 is always OFF, and each other level
/// exists only when its local model file is installed. The Qt runtime has no HF
/// model sidecar yet, so a caller that has not installed one passes `false` and
/// the drawer honestly offers OFF alone instead of a control that cannot work.
QVector<int> hfEnhanceLevels(bool modelsInstalled);

/// The audio drawer's controls, with the reset defaults.
struct AudioControls {
    bool mute = false;
    int voiceCleanLevel = 0;
    int hfEnhanceLevel = 0;
    int toneProfile = 0;
    int squelchLevel = 0;
    bool agc = true;
    bool agcHang = false;
    int nbAlgo = 0;
    bool autonotch = false;
    int deemphasis = 0;
    int denoiseLevel = 0;
};

/// `reset_audio_controls`.
AudioControls defaultAudioControls();

/// The state the drawer's action leaves behind.
///
/// An action the drawer does not own (`close`, `filter`, `backend`, an unknown
/// name) changes nothing: the drawer only reports those, because closing a
/// drawer or switching the output device is not an audio *control* change.
AudioControls applyAudioAction(const AudioControls &controls, const QString &action,
                               const QVector<int> &hfLevels = hfEnhanceLevels(false));

/// `main_volume_label`: an explicit `MUTE` below the threshold, otherwise the
/// rounded percentage.
QString mainVolumeLabel(double level);

/// The threshold below which the master level reads as `MUTE`.
double mainVolumeMuteThreshold();

}  // namespace ituner::core
