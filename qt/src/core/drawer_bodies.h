// What the Audio and Display drawers actually show.
//
// The Python renderer builds each drawer's tiles inline in
// `draw_lcd_audio_drawer` and `draw_display_setup_panel`, calling one shared
// tile primitive with a title and a detail string. Those two functions are the
// last place where a *string* is decided in the renderer, so this module owns
// that decision instead: given the state, it returns the tiles in draw order,
// each with its box, so the QML screen draws exactly what the state implies and
// a golden captured from the real Python drawer pins every string.
//
// The signal-to-noise rule is the same one the Python drawer follows: a control
// that is doing something is *active* (and drawn in the accent colour) and one
// that is not is still visible and still touchable. Nothing is hidden.

#pragma once

#include <QString>
#include <QVector>

#include <audio_controls.h>
#include <waterfall_controls.h>

namespace ituner::core {

/// One drawer tile: the two lines it shows, whether it is drawn active, and the
/// box it occupies -- the same box the hit test consults.
struct DrawerTile {
    QString name;
    QString title;
    QString detail;
    bool active = false;
    ControlBox box;
    /// The level and range a slider tile's fill is drawn from, exactly as the
    /// Python drawer passes them to its slider primitive. Both stay 0 for a
    /// plain two-line tile, which is what `isSlider()` distinguishes.
    double value = 0.0;
    double maximum = 0.0;

    bool isSlider() const { return maximum > 0.0; }
    /// The drawn fill, clamped to 0..1. Python divides by `max(1, maximum)`, so
    /// the answer is still defined for a degenerate range.
    double fraction() const;
};

/// The Audio drawer's tiles, in draw order.
///
/// The volume row is deliberately **not** here: it is a continuous slider the
/// drawer draws itself, so its label comes from `mainVolumeLabel` and its box
/// from `audioDrawerBoxes().volume`.
///
/// `radioMode` is an input because the squelch scale depends on it: NBFM and
/// NNFM read 0-99 while every other mode reads 0-40 dB.
QVector<DrawerTile> audioDrawerTiles(const AudioControls &controls, double lowCutHz,
                                     double highCutHz, const QString &radioMode,
                                     const QString &audioBackend = QStringLiteral("pipewire"),
                                     double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                     double railX0 = 1024.0);

/// The waterfall setup the Display drawer edits.
struct DisplayState {
    double floor = 0.0;
    double ceiling = 0.0;
    int speed = 0;
    bool autoScale = false;
    QString palette;
    bool spectrumEnabled = true;
    /// `compact` shows the readouts in the rail; `expanded` leaves that room to
    /// the waterfall and shows the frequency in the canvas instrument layer
    /// instead. The Python preference defaults to `expanded`; the port starts
    /// `compact` because the canvas layer is not ported yet, and this is what
    /// the drawer's LAYOUT tile reports.
    QString instrumentLayout = QStringLiteral("compact");
};

/// The ported defaults: floor 142, ceiling 245, speed 4, palette `kiwi`,
/// spectrum on, expanded instruments.
DisplayState defaultDisplayState();

/// One of the Display drawer's plain choice controls (a waterfall speed or a
/// palette), which the drawer draws as a labelled box rather than a tile.
struct DisplayControl {
    QString name;
    QString label;
    bool active = false;
    ControlBox box;
};

/// The Display drawer's tiles, in draw order: reset, layout, scope, spectrum,
/// auto, then the floor and ceiling rows.
QVector<DrawerTile> displayDrawerTiles(const DisplayState &state,
                                       double logicalHeight = 800.0,
                                       double logicalWidth = 1280.0, double railX0 = 1024.0);

/// The Display drawer's speed and palette choices, in draw order.
QVector<DisplayControl> displayDrawerControls(const DisplayState &state,
                                              double logicalHeight = 800.0,
                                              double logicalWidth = 1280.0, double railX0 = 1024.0);

}  // namespace ituner::core
