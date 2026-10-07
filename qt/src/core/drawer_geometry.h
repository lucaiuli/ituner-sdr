// The right-rail drawers' geometry and hit-testing.
//
// Ported from the LCD geometry helpers in `UI/kiwi_gl_display.py`:
// `frequency_entry_layout`, `frequency_drawer_boxes`,
// `frequency_drawer_step_boxes`, `frequency_drawer_action_at`,
// `format_frequency_digits`, `format_tune_step`, `frequency_step_target`,
// `configured_tune_step_hz`, `compact_frequency_touch_box`,
// `is_frequency_readout_touch`, `lcd_filter_drawer_boxes`,
// `lcd_filter_drawer_action_at`, `receiver_home_drawer_boxes`,
// `fan_curve_drawer_boxes`, `compact_font_review_boxes`,
// `compact_font_review_action_at`, `tests_option_at`, `radio_mode_layout`,
// `radio_step_options`, `radio_wspr_box`, `radio_option_at`,
// `lcd_radio_drawer_reveal_y` and the three drawer close boxes.
//
// The rule this module exists to keep is the plan's own: drawing and hit-testing
// must be derived from **one box definition per control**, never two layouts that
// drift apart. Every function here returns the boxes the renderer draws and the
// hit test consults, so a control that is drawn is always touchable and a control
// that is not drawn is never touchable.
//
// The FM-DX branch of `radio_option_at` is deliberately absent: FM-DX is a later
// phase in the plan, so this port covers the Kiwi/OpenWebRX RF-receiver path and
// reports no option for FM-DX rather than pretending to own a shared tuner.

#pragma once

#include <QPair>
#include <QString>
#include <QStringList>
#include <QVector>

#include <optional>

#include <waterfall_controls.h>

namespace ituner::core {

/// A labelled control box, for the layouts that are a list of ("LABEL", box).
using LabelledBox = QPair<QString, ControlBox>;

/// A step value paired with the box that selects it.
using StepBox = QPair<qint64, ControlBox>;

// ---------------------------------------------------------------------------
// Frequency readout and manual entry
// ---------------------------------------------------------------------------

/// `frequency_entry_layout`: the manual keypad as a rail face, not a panel that
/// floats over the waterfall beside it.
struct FrequencyEntryLayout {
    ControlBox panel;
    ControlBox entry;
    QVector<LabelledBox> commands;
    QVector<LabelledBox> keys;
};

FrequencyEntryLayout frequencyEntryLayout(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                          double railX0 = 1024.0);

/// `frequency_entry_action_at`: the command or key label under a point, or an
/// empty string. Python returns `None`; an empty string is the same "nothing
/// here" in a language where that is cheap to compare.
QString frequencyEntryActionAt(double x, double y, double logicalHeight = 800.0,
                               double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `frequency_drawer_boxes`: the tuning controls in the otherwise empty rail.
struct FrequencyDrawerBoxes {
    ControlBox panel;
    ControlBox readout;
    ControlBox down;
    ControlBox up;
    ControlBox manual;
    ControlBox stepHeading;
    ControlBox close;
};

FrequencyDrawerBoxes frequencyDrawerBoxes(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                          double railX0 = 1024.0);

/// `frequency_tune_steps`: the selectable steps for the active receiver type.
QVector<qint64> frequencyTuneSteps(const QString &receiverType = QStringLiteral("kiwi"));

/// `frequency_drawer_step_boxes`: the inline step choices, not another screen.
QVector<StepBox> frequencyDrawerStepBoxes(const QString &receiverType = QStringLiteral("kiwi"),
                                          double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                          double railX0 = 1024.0);

/// `frequency_drawer_action_at`: one of `down`, `up`, `manual`, `close`,
/// `step_<hz>`, or an empty string.
QString frequencyDrawerActionAt(double x, double y, const QString &receiverType = QStringLiteral("kiwi"),
                                double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                double railX0 = 1024.0);

/// `format_frequency_digits`: the nine-digit Hz readout, grouped as
/// `007.075.794` for fast touch-screen recognition.
QString formatFrequencyDigits(double freqKhz);

/// `format_tune_step`: `100 kHz`, `1 MHz` or `500 Hz`.
QString formatTuneStep(qint64 stepHz);

/// `frequency_step_target`: the adjacent point on the configured tuning grid,
/// clamped to the receiver's bounds.
double frequencyStepTarget(double freqKhz, int direction, qint64 stepHz, double lowKhz,
                           double highKhz);

/// `configured_tune_step_hz`: the operator-selected grid, independent of zoom.
qint64 configuredTuneStepHz(const QString &receiverType, qint64 kiwiStepHz, qint64 fmdxStepHz);

/// `parse_frequency_entry_mhz`: the entry value is read as MHz first and a
/// pasted kHz value is tolerated, so `14.074` is 14074 kHz while `7075` is
/// 7075 kHz rather than 7.075 GHz. Nothing is returned for text that is not a
/// number or a frequency outside `0..maxFrequencyKhz`.
std::optional<double> parseFrequencyEntryMhz(const QString &value,
                                             double maxFrequencyKhz = 29999.0);

/// `format_filter_width`: `500 Hz` or `2.4 k`.
QString formatFilterWidth(double widthHz);

/// Python's `f"{value:.Nf}"`.
///
/// Exposed because it is a real rule and not a convenience wrapper: Python
/// rounds an exact decimal half to the nearest **even** digit (`500.5` prints as
/// `500`) while both `QString::number` and `snprintf` round it away from zero.
/// A readout that disagreed by one digit would be a visible difference from the
/// app the operator knows, so the rounding is done here once and shared.
QString formatDecimals(double value, int decimals);

/// `compact_frequency_touch_box`: the annunciator readout's finger target.
ControlBox compactFrequencyTouchBox();

/// `is_frequency_readout_touch`: every visible compact-frequency pixel is the
/// tuning target, so the whole readout is tappable rather than only its text.
bool isFrequencyReadoutTouch(double x, double y, const ControlBox &measuredBox, bool compact);

// ---------------------------------------------------------------------------
// Filter (passband) drawer
// ---------------------------------------------------------------------------

/// One width preset: the label, its width in Hz and its box.
struct FilterPreset {
    QString name;
    qint64 widthHz = 0;
    ControlBox box;
};

/// `lcd_filter_drawer_boxes`: the non-modal LCD passband editor.
struct FilterDrawerBoxes {
    ControlBox panel;
    ControlBox close;
    ControlBox visual;
    ControlBox shift;
    ControlBox width;
    QVector<FilterPreset> presets;
};

FilterDrawerBoxes lcdFilterDrawerBoxes(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                       double railX0 = 1024.0);

/// `lcd_filter_drawer_action_at`: `shift`, `width`, `drawer`, or an empty string.
QString lcdFilterDrawerActionAt(double x, double y, double logicalHeight = 800.0,
                                double logicalWidth = 1280.0, double railX0 = 1024.0);

// ---------------------------------------------------------------------------
// Audio drawer
// ---------------------------------------------------------------------------

/// `configure_output`'s LCD branch for the `AUDIO_*` boxes.
///
/// These are **not** the desktop popup constants: in the LCD presentation the
/// drawer is a bottom-anchored right-rail stack, with the two sliders spanning
/// the whole logical width because the live waterfall stays visible beside
/// them. Reading the desktop values here would be a silently wrong port, which
/// is what the golden caught for the Apps drawer and is checked again here.
struct AudioDrawerBoxes {
    ControlBox panel;
    ControlBox close;
    ControlBox mute;
    ControlBox volume;
    ControlBox squelch;
    ControlBox denoise;
    ControlBox voiceClean;
    ControlBox hfEnhance;
    ControlBox agc;
    ControlBox blanker;
    ControlBox notch;
    ControlBox deemphasis;
    ControlBox tone;
    ControlBox filter;
    ControlBox reset;
    ControlBox backend;
};

AudioDrawerBoxes audioDrawerBoxes(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                  double railX0 = 1024.0);

/// `audio_option_at`: one of `close`, `mute`, `voice_clean`, `hf_enhance`,
/// `squelch`, `agc`, `blanker`, `notch`, `deemphasis`, `tone`, `filter`,
/// `reset`, `backend`, or an empty string.
QString audioOptionAt(double x, double y, double logicalHeight = 800.0,
                      double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `volume_at_x(x, AUDIO_VOLUME_BOX)`: 0..1 across the slider, clamped.
double audioVolumeAtX(double x, double logicalHeight = 800.0, double logicalWidth = 1280.0,
                      double railX0 = 1024.0);

/// `audio_squelch_at_x`: the squelch scale, whose ceiling depends on the mode.
int audioSquelchAtX(double x, int maximum = 99, double logicalHeight = 800.0,
                    double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `squelch_maximum`: Kiwi uses a 0-99 scale for NBFM and 0-40 dB otherwise.
int squelchMaximum(const QString &radioMode);

/// `audio_denoise_level_at_x`: the nearest Denoise detent, not a continuous
/// level, because the six presets are evenly spaced on purpose.
int audioDenoiseLevelAtX(double x, double logicalHeight = 800.0, double logicalWidth = 1280.0,
                         double railX0 = 1024.0);

// ---------------------------------------------------------------------------
// Display (waterfall setup) drawer
// ---------------------------------------------------------------------------

/// One waterfall speed choice and the box that selects it.
struct DisplayRateBox {
    int rate = 0;
    QString label;
    ControlBox box;
};

/// One palette choice and the box that selects it.
struct DisplayPaletteBox {
    QString name;
    QString label;
    ControlBox box;
};

/// `configure_output`'s LCD branch for the `DISPLAY_*` boxes.
struct DisplayDrawerBoxes {
    ControlBox panel;
    ControlBox close;
    ControlBox reset;
    ControlBox spectrum;
    ControlBox autoScale;
    /// The floor row is a continuous slider, not a pair of `-`/`+` buttons: the
    /// plus boxes stay empty on the LCD and the drag is what edits the level.
    ControlBox floor;
    ControlBox ceiling;
    QVector<DisplayRateBox> rates;
    QVector<DisplayPaletteBox> palettes;
    ControlBox instruments;
    ControlBox scope;
};

DisplayDrawerBoxes displayDrawerBoxes(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                      double railX0 = 1024.0);

/// The result of a Display-drawer touch, mirroring the Python `(name, value)`
/// tuple with named fields instead of a heterogeneous second element.
struct DisplayOption {
    /// `close`, `reset`, `instruments`, `scope`, `spectrum`, `auto`, `rate`,
    /// `palette`, or empty for nothing.
    QString action;
    /// The new speed, for `rate`.
    int rate = 0;
    /// The palette identifier, for `palette`.
    QString palette;
};

/// `display_option_at`. The desktop-only `floor`/`ceiling` `+/-` branch is
/// deliberately absent: this port is the LCD presentation, where the floor and
/// ceiling rows are dragged, so the Python function answers nothing for them and
/// `waterfallFloorAtX`/`waterfallCeilingAtX` over the same rows is what edits
/// the level.
DisplayOption displayOptionAt(double x, double y, double logicalHeight = 800.0,
                              double logicalWidth = 1280.0, double railX0 = 1024.0);

// ---------------------------------------------------------------------------
// Info / fan-curve / font-review drawers
// ---------------------------------------------------------------------------

/// A drawer whose body is a panel, a shared close control and three tiles.
struct ThreeTileDrawerBoxes {
    ControlBox panel;
    ControlBox close;
    ControlBox first;
    ControlBox second;
    ControlBox third;
};

/// `receiver_home_drawer_boxes`: fan, locate and fallback tiles.
ThreeTileDrawerBoxes receiverHomeDrawerBoxes(double logicalHeight = 800.0,
                                             double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `fan_curve_drawer_boxes`: start, full and minimum tiles.
ThreeTileDrawerBoxes fanCurveDrawerBoxes(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                         double railX0 = 1024.0);

/// `compact_font_review_boxes`: the full-height rail workspace for the VFO face.
struct CompactFontReviewBoxes {
    ControlBox panel;
    ControlBox preview;
    ControlBox previous;
    ControlBox next;
    ControlBox like;
    ControlBox deleteButton;
    ControlBox use;
    ControlBox exit;
};

CompactFontReviewBoxes compactFontReviewBoxes(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                              double railX0 = 1024.0);

/// `compact_font_review_action_at`: one of `previous`, `next`, `like`, `delete`,
/// `use`, `exit`, or an empty string.
QString compactFontReviewActionAt(double x, double y, double logicalHeight = 800.0,
                                  double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `tests_option_at`: the Apps-drawer action under a point, in the Python
/// precedence order (`back`, `globe`, `dj`, `rtl`, `pattern`, `font_lab`,
/// `openwebrx`, `run`, `dual`), or an empty string.
QString testsOptionAt(double x, double y);

/// The Apps-drawer action boxes themselves, in the same order.
///
/// Exposed so a test can compare the boxes, not only the hit test: a box shifted
/// by a pixel still answers the same for its centre, and the geometry is the
/// contract.
QVector<LabelledBox> testsActionBoxes();

/// The drawer close boxes. All three are the shared Back target, and that is the
/// point: `lcd_drawer_back_box()` is the single definition.
ControlBox lcdRadioDrawerCloseBox(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                  double railX0 = 1024.0);
ControlBox lcdDisplayDrawerCloseBox(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                    double railX0 = 1024.0);
ControlBox lcdAudioDrawerCloseBox(double logicalHeight = 800.0, double logicalWidth = 1280.0,
                                  double railX0 = 1024.0);

// ---------------------------------------------------------------------------
// Modes drawer
// ---------------------------------------------------------------------------

/// `radio_panel_box()`: the open drawer replaces the annunciator block and the
/// Home rail beneath it, so mode information is never duplicated on screen.
ControlBox radioPanelBox(double logicalHeight = 800.0);

/// `lcd_radio_mode_grid_y0()` and `lcd_radio_step_y0()`.
double lcdRadioModeGridY0();
double lcdRadioStepY0(int modeFamilyCount = 8);

/// `KIWI_MODE_FAMILIES`: the mode families the LCD matrix shows, as
/// family name to variant list, in order.
QVector<QPair<QString, QStringList>> kiwiModeFamilies();

/// `next_radio_mode_variant`: the next variant in one family, wrapping at the
/// end. A mode that is not in the family answers its first variant, which is
/// what the Python `except ValueError` branch does -- not a no-op.
QString nextRadioModeVariant(const QString &currentMode, const QStringList &modes);

/// One mode family: its name, its variants and the box that cycles them.
struct ModeFamilyBox {
    QString family;
    QStringList modes;
    ControlBox box;
};

/// `radio_mode_layout`: the 2-column LCD mode-family matrix.
QVector<ModeFamilyBox> radioModeLayout(double logicalHeight = 800.0);

/// `radio_step_options`: the tuning-step pair below the mode families.
QVector<StepBox> radioStepOptions(const QString &receiverType = QStringLiteral("kiwi"),
                                  double logicalHeight = 800.0);

/// `radio_wspr_box`: the dedicated WSPR workspace launcher.
ControlBox radioWsprBox(double logicalWidth = 1280.0, double railX0 = 1024.0);

/// `lcd_radio_drawer_reveal_y`: the drawer's lower edge, from its reveal progress.
double lcdRadioDrawerRevealY(double progress, double logicalHeight = 800.0);

/// The result of a Modes-drawer touch, mirroring the Python tuple shape.
struct RadioOption {
    /// `close`, `mode_cycle`, `workspace`, `step`, or empty for nothing.
    QString action;
    /// The variants to cycle, for `mode_cycle`.
    QStringList modes;
    /// The workspace name, for `workspace`.
    QString detail;
    /// The step in Hz, for `step`.
    qint64 stepHz = 0;
};

/// `radio_option_at`, for the Kiwi/OpenWebRX path. FM-DX is a later phase and
/// yields no option here.
RadioOption radioOptionAt(double x, double y, const QString &receiverType = QStringLiteral("kiwi"),
                          double drawerProgress = 1.0, int familyCount = 8,
                          double logicalHeight = 800.0, double logicalWidth = 1280.0);



}  // namespace ituner::core
