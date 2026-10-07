#include "drawer_geometry.h"

#include <algorithm>
#include <cmath>

#include <navigation.h>
#include <waterfall_model.h>

namespace ituner::core {

namespace {

/// `LCD_NAV_X0`, `LCD_DRAWER_HEADER_H`, `LCD_ANNUNCIATOR_BOX` and the step
/// metrics the frequency drawer reads.
constexpr double kNavX0 = 1024.0;
constexpr double kDrawerHeaderH = 64.0;
constexpr double kAnnunciatorX0 = 1031.0;
constexpr double kAnnunciatorX1 = 1273.0;
constexpr double kFrequencyStepGap = 8.0;
constexpr double kFrequencyStepH = 52.0;

/// `DENOISE_SLIDER_POSITIONS`: six evenly spaced, discrete Denoise settings.
const QVector<double> kDenoiseSliderPositions = {0.00, 0.20, 0.40, 0.60, 0.80, 1.00};

}  // namespace

/// See the header: neither `QString::number` nor `snprintf` will do, and the reason is
/// not cosmetic. Both round an exact decimal half *away from zero*, while Python
/// rounds it to the nearest **even** digit: `f"{500.5:.0f}"` is `500`, and
/// `f"{1250/1000:.1f}"` is `1.2`. A width readout that printed one hertz or one
/// tenth high would be a visible disagreement with the app the operator knows.
///
/// So the fixed-precision rendering is done here: expand the exact binary value
/// to enough decimal places to see whether the value sits exactly on a boundary,
/// then round the decimal string by hand with a half-to-even tie. Seventeen
/// guard digits beyond the requested precision are enough to separate any value
/// from an exact half, while a value like `2.45` -- whose nearest double is just
/// *above* the decimal literal -- still rounds up, as Python does.
QString formatDecimals(double value, int decimals) {
    if (decimals <= 0) {
        return QString::number(pythonRoundToInt(value));
    }
    const QString text = QString::asprintf("%.*f", decimals + 17, value);
    const int point = text.indexOf(QLatin1Char('.'));
    if (point < 0) {
        return text;
    }
    const QString fraction = text.mid(point + 1);
    QString kept = fraction.left(decimals);
    if (kept.size() < decimals) {
        kept = kept.leftJustified(decimals, QLatin1Char('0'));
    }
    const QString discarded = fraction.mid(decimals);
    bool roundUp = false;
    if (!discarded.isEmpty()) {
        const QChar first = discarded.at(0);
        bool beyond = false;
        for (int index = 1; index < discarded.size() && !beyond; ++index) {
            beyond = discarded.at(index) != QLatin1Char('0');
        }
        if (first > QLatin1Char('5') || (first == QLatin1Char('5') && beyond)) {
            roundUp = true;
        } else if (first == QLatin1Char('5')) {
            const int last = kept.isEmpty() ? 0 : kept.at(kept.size() - 1).digitValue();
            roundUp = last % 2 != 0;
        }
    }
    if (!roundUp) {
        return text.left(point) + QLatin1Char('.') + kept;
    }
    int index = kept.size() - 1;
    while (index >= 0 && kept.at(index) == QLatin1Char('9')) {
        kept[index] = QLatin1Char('0');
        --index;
    }
    if (index >= 0) {
        kept[index] = QChar(kept.at(index).unicode() + 1);
        return text.left(point) + QLatin1Char('.') + kept;
    }
    // Every kept digit carried, so the integer part grows.
    QString integer = text.left(point);
    for (int position = integer.size() - 1; position >= 0; --position) {
        if (integer.at(position) == QLatin1Char('9')) {
            integer[position] = QLatin1Char('0');
        } else {
            integer[position] = QChar(integer.at(position).unicode() + 1);
            return integer + QLatin1Char('.') + kept;
        }
    }
    integer.prepend(QLatin1Char('1'));
    return integer + QLatin1Char('.') + kept;
}

namespace {

/// The Apps drawer's action boxes, in the Python precedence order.
///
/// These are the **LCD** boxes, which `configure_output` reassigns from the
/// desktop ones: in the LCD presentation every Apps action is a rail tile, and
/// Back is the shared drawer target rather than its own corner. Reading the
/// desktop values here would have been a silently wrong port, which is exactly
/// what the golden caught.
struct TestBox {
    const char *action;
    ControlBox box;
};

const TestBox kTestBoxes[] = {
    {"back", {1034.0, 722.0, 1270.0, 790.0}},
    {"globe", {1034.0, 112.0, 1145.0, 216.0}},
    {"dj", {1159.0, 112.0, 1270.0, 216.0}},
    {"rtl", {1034.0, 228.0, 1145.0, 332.0}},
    {"pattern", {1159.0, 228.0, 1270.0, 332.0}},
    {"font_lab", {1034.0, 344.0, 1145.0, 448.0}},
    {"openwebrx", {1159.0, 344.0, 1270.0, 448.0}},
    {"run", {1034.0, 460.0, 1145.0, 538.0}},
    {"dual", {1159.0, 460.0, 1270.0, 538.0}},
};

/// `FILTER_WIDTH_PRESETS`.
const QVector<QPair<QString, qint64>> &filterWidthPresets() {
    static const QVector<QPair<QString, qint64>> presets = {
        {QStringLiteral("CW"), 500},
        {QStringLiteral("VOICE NARROW"), 1200},
        {QStringLiteral("VOICE"), 2400},
        {QStringLiteral("VOICE WIDE"), 3000},
        {QStringLiteral("WIDE 6k"), 6000},
        {QStringLiteral("WIDE 9/12k"), 9000},
    };
    return presets;
}

/// The FM-DX fallback step, for when the saved step is not one of the three the
/// FM-DX tuner offers.
constexpr qint64 kFmdxDefaultStep = 100000;

/// `KIWI_MODE_FAMILIES`.
const QVector<QPair<QString, QStringList>> &kiwiFamilies() {
    static const QVector<QPair<QString, QStringList>> families = {
        {QStringLiteral("AM"), {QStringLiteral("AM"), QStringLiteral("AMN"), QStringLiteral("AMW")}},
        {QStringLiteral("SYNC AM"),
         {QStringLiteral("SAM"), QStringLiteral("SAU"), QStringLiteral("SAL"),
          QStringLiteral("SAS"), QStringLiteral("QAM")}},
        {QStringLiteral("USB"), {QStringLiteral("USB"), QStringLiteral("USN")}},
        {QStringLiteral("LSB"), {QStringLiteral("LSB"), QStringLiteral("LSN")}},
        {QStringLiteral("CW"), {QStringLiteral("CW"), QStringLiteral("CWN")}},
        {QStringLiteral("FM"), {QStringLiteral("NBFM"), QStringLiteral("NNFM")}},
        {QStringLiteral("I/Q"), {QStringLiteral("IQ")}},
        {QStringLiteral("DRM"), {QStringLiteral("DRM")}},
    };
    return families;
}

/// The Python `clamp(value, low, high)`: `max(low, min(high, value))`, which is
/// not `std::clamp` when `low > high`.
double pythonClamp(double value, double low, double high) {
    return std::max(low, std::min(high, value));
}

/// The two tuning arrows are square: their height follows the two-column width
/// instead of stretching into a tall rectangle.
void fillFrequencyArrows(FrequencyDrawerBoxes *boxes, double left, double right) {
    const double arrowGap = 10.0;
    const double arrow = (right - left - arrowGap) / 2.0;
    const double arrowTop = 184.0;
    const double arrowBottom = arrowTop + arrow;
    boxes->down = {left, arrowTop, left + arrow, arrowBottom};
    boxes->up = {right - arrow, arrowTop, right, arrowBottom};
}

}  // namespace

// ---------------------------------------------------------------------------
// Frequency readout and manual entry
// ---------------------------------------------------------------------------

FrequencyEntryLayout frequencyEntryLayout(double logicalHeight, double logicalWidth, double railX0) {
    FrequencyEntryLayout layout;
    layout.panel = {railX0, 0.0, logicalWidth, lcdRailBottom(logicalHeight)};
    layout.entry = {railX0 + 10.0, 66.0, logicalWidth - 10.0, 142.0};
    layout.commands = {
        {QStringLiteral("BACK"), {railX0 + 10.0, 508.0, railX0 + 124.0, 580.0}},
        {QStringLiteral("CLEAR"), {railX0 + 132.0, 508.0, logicalWidth - 10.0, 580.0}},
        {QStringLiteral("CANCEL"), lcdDrawerBackBox(logicalHeight, logicalWidth, railX0)},
    };
    const QStringList rows[4] = {
        {QStringLiteral("1"), QStringLiteral("2"), QStringLiteral("3")},
        {QStringLiteral("4"), QStringLiteral("5"), QStringLiteral("6")},
        {QStringLiteral("7"), QStringLiteral("8"), QStringLiteral("9")},
        {QStringLiteral("."), QStringLiteral("0"), QStringLiteral("ENTER")},
    };
    for (int row = 0; row < 4; ++row) {
        const double y0 = 158.0 + row * 88.0;
        for (int column = 0; column < 3; ++column) {
            const double x0 = railX0 + 10.0 + column * 82.0;
            layout.keys.append({rows[row].at(column), {x0, y0, x0 + 70.0, y0 + 70.0}});
        }
    }
    return layout;
}

QString frequencyEntryActionAt(double x, double y, double logicalHeight, double logicalWidth,
                               double railX0) {
    const FrequencyEntryLayout layout =
        frequencyEntryLayout(logicalHeight, logicalWidth, railX0);
    for (const LabelledBox &command : layout.commands) {
        if (command.second.contains(x, y)) {
            return command.first;
        }
    }
    for (const LabelledBox &key : layout.keys) {
        if (key.second.contains(x, y)) {
            return key.first;
        }
    }
    return QString();
}

FrequencyDrawerBoxes frequencyDrawerBoxes(double logicalHeight, double logicalWidth, double railX0) {
    FrequencyDrawerBoxes boxes;
    const double left = railX0 + 10.0;
    const double right = logicalWidth - 10.0;
    boxes.panel = {railX0, 0.0, logicalWidth, lcdRailBottom(logicalHeight)};
    boxes.readout = {left, 76.0, right, 160.0};
    fillFrequencyArrows(&boxes, left, right);
    boxes.manual = {left, 352.0, right, 424.0};
    boxes.stepHeading = {left, 434.0, right, 458.0};
    boxes.close = lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    return boxes;
}

QVector<qint64> frequencyTuneSteps(const QString &receiverType) {
    if (receiverType.trimmed().toLower() == QStringLiteral("fmdx")) {
        return {50000, 100000, 200000};
    }
    return {10, 100, 1000, 5000};
}

QVector<StepBox> frequencyDrawerStepBoxes(const QString &receiverType, double logicalHeight,
                                          double logicalWidth, double railX0) {
    const QVector<qint64> steps = frequencyTuneSteps(receiverType);
    const double left = railX0 + 10.0;
    const double right = logicalWidth - 10.0;
    const double width = (right - left - kFrequencyStepGap) / 2.0;
    const double y0 = frequencyDrawerBoxes(logicalHeight, logicalWidth, railX0).stepHeading.y1 + 6.0;
    QVector<StepBox> boxes;
    for (int index = 0; index < steps.size(); ++index) {
        const int column = index % 2;
        const int row = index / 2;
        const double x = left + column * (width + kFrequencyStepGap);
        const double top = y0 + row * (kFrequencyStepH + kFrequencyStepGap);
        boxes.append({steps.at(index), {x, top, x + width, top + kFrequencyStepH}});
    }
    return boxes;
}

QString frequencyDrawerActionAt(double x, double y, const QString &receiverType,
                                double logicalHeight, double logicalWidth, double railX0) {
    const FrequencyDrawerBoxes boxes = frequencyDrawerBoxes(logicalHeight, logicalWidth, railX0);
    if (boxes.down.contains(x, y)) {
        return QStringLiteral("down");
    }
    if (boxes.up.contains(x, y)) {
        return QStringLiteral("up");
    }
    if (boxes.manual.contains(x, y)) {
        return QStringLiteral("manual");
    }
    if (boxes.close.contains(x, y)) {
        return QStringLiteral("close");
    }
    for (const StepBox &step :
         frequencyDrawerStepBoxes(receiverType, logicalHeight, logicalWidth, railX0)) {
        if (step.second.contains(x, y)) {
            return QStringLiteral("step_") + QString::number(step.first);
        }
    }
    return QString();
}

QString formatFrequencyDigits(double freqKhz) {
    // Python formats the rounded Hz value as a minimum-nine-digit decimal and
    // then keeps the last nine characters, so a value past 999.999.999 Hz keeps
    // its low digits rather than overflowing the readout.
    const qint64 hertz = std::max<qint64>(0, pythonRoundToInt(std::fmax(0.0, freqKhz) * 1000.0));
    QString digits = QString::number(hertz);
    while (digits.size() < 9) {
        digits.prepend(QLatin1Char('0'));
    }
    digits = digits.right(9);
    return digits.left(3) + QLatin1Char('.') + digits.mid(3, 3) + QLatin1Char('.') +
           digits.mid(6, 3);
}

QString formatTuneStep(qint64 stepHz) {
    const qint64 step = std::max<qint64>(1, stepHz);
    if (step >= 1000000 && step % 1000000 == 0) {
        return QString::number(step / 1000000) + QStringLiteral(" MHz");
    }
    if (step >= 1000 && step % 1000 == 0) {
        return QString::number(step / 1000) + QStringLiteral(" kHz");
    }
    return QString::number(step) + QStringLiteral(" Hz");
}

double frequencyStepTarget(double freqKhz, int direction, qint64 stepHz, double lowKhz,
                           double highKhz) {
    const qint64 step = std::max<qint64>(1, stepHz);
    const double currentHz = freqKhz * 1000.0;
    const double quotient = currentHz / static_cast<double>(step);
    // The epsilon keeps a value that sits exactly on a grid point from moving
    // twice: it is the difference between "the next detent" and "two detents".
    double targetHz = 0.0;
    if (direction > 0) {
        targetHz = (std::floor(quotient + 1e-9) + 1.0) * static_cast<double>(step);
    } else {
        targetHz = (std::ceil(quotient - 1e-9) - 1.0) * static_cast<double>(step);
    }
    return pythonClamp(targetHz / 1000.0, lowKhz, highKhz);
}

std::optional<double> parseFrequencyEntryMhz(const QString &value, double maxFrequencyKhz) {
    bool ok = false;
    const double numeric = value.trimmed().toDouble(&ok);
    if (!ok) {
        return std::nullopt;
    }
    // MHz first, a pasted kHz value tolerated: the threshold is the largest
    // number that can still be a MHz reading, so `29.999` is 29999 kHz while
    // `30` is already kHz.
    const double frequencyKhz =
        numeric <= maxFrequencyKhz / 1000.0 ? numeric * 1000.0 : numeric;
    if (frequencyKhz < 0.0 || frequencyKhz > maxFrequencyKhz) {
        return std::nullopt;
    }
    return frequencyKhz;
}

QString formatFilterWidth(double widthHz) {
    if (widthHz < 1000.0) {
        return formatDecimals(widthHz, 0) + QStringLiteral(" Hz");
    }
    return formatDecimals(widthHz / 1000.0, 1) + QStringLiteral(" k");
}

qint64 configuredTuneStepHz(const QString &receiverType, qint64 kiwiStepHz, qint64 fmdxStepHz) {
    if (receiverType == QStringLiteral("fmdx")) {
        const QVector<qint64> steps = frequencyTuneSteps(QStringLiteral("fmdx"));
        return steps.contains(fmdxStepHz) ? fmdxStepHz : kFmdxDefaultStep;
    }
    return std::max<qint64>(1, kiwiStepHz);
}

ControlBox compactFrequencyTouchBox() { return {1035.0, 12.0, 1269.0, 83.0}; }

bool isFrequencyReadoutTouch(double x, double y, const ControlBox &measuredBox, bool compact) {
    if (measuredBox.contains(x, y)) {
        return true;
    }
    return compact && compactFrequencyTouchBox().contains(x, y);
}

// ---------------------------------------------------------------------------
// Filter (passband) drawer
// ---------------------------------------------------------------------------

FilterDrawerBoxes lcdFilterDrawerBoxes(double logicalHeight, double logicalWidth, double railX0) {
    FilterDrawerBoxes boxes;
    const double innerX0 = railX0 + 10.0;
    const double innerX1 = logicalWidth - 10.0;
    boxes.panel = {railX0, kDrawerHeaderH, logicalWidth, lcdRailBottom(logicalHeight)};
    boxes.close = lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    boxes.visual = {innerX0, 108.0, innerX1, 211.0};
    boxes.shift = {innerX0, 217.0, innerX1, 329.0};
    boxes.width = {innerX0, 341.0, innerX1, 441.0};
    // Choice buttons are bottom-aligned just above Back, which leaves the two
    // continuous instruments generous finger travel.
    const double presetTop = 493.0;
    const double presetHeight = 50.0;
    const double presetGap = 8.0;
    const double presetWidth = (innerX1 - innerX0 - 8.0) / 2.0;
    const QVector<QPair<QString, qint64>> &presets = filterWidthPresets();
    for (int index = 0; index < presets.size(); ++index) {
        const int column = index % 2;
        const int row = index / 2;
        const double left = innerX0 + column * (presetWidth + 8.0);
        const double top = presetTop + row * (presetHeight + presetGap);
        boxes.presets.append({presets.at(index).first, presets.at(index).second,
                              {left, top, left + presetWidth, top + presetHeight}});
    }
    return boxes;
}

QString lcdFilterDrawerActionAt(double x, double y, double logicalHeight, double logicalWidth,
                                double railX0) {
    const FilterDrawerBoxes boxes = lcdFilterDrawerBoxes(logicalHeight, logicalWidth, railX0);
    if (!boxes.panel.contains(x, y)) {
        return QString();
    }
    if (boxes.shift.contains(x, y)) {
        return QStringLiteral("shift");
    }
    if (boxes.width.contains(x, y)) {
        return QStringLiteral("width");
    }
    return QStringLiteral("drawer");
}

// ---------------------------------------------------------------------------
// Audio drawer
// ---------------------------------------------------------------------------

AudioDrawerBoxes audioDrawerBoxes(double logicalHeight, double logicalWidth, double railX0) {
    AudioDrawerBoxes boxes;
    boxes.panel = {kAnnunciatorX0, kDrawerHeaderH, kAnnunciatorX1, lcdRailBottom(logicalHeight)};
    boxes.close = lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    boxes.mute = {kAnnunciatorX0 + 10.0, 80.0, kAnnunciatorX1 - 10.0, 142.0};
    // The two continuous sliders span the whole logical width so the live
    // waterfall stays visible beside them.
    boxes.volume = {railX0, 160.0, logicalWidth, 222.0};
    boxes.squelch = {railX0, 230.0, logicalWidth, 294.0};
    boxes.denoise = {railX0, 302.0, logicalWidth, 366.0};
    const double left = kAnnunciatorX0 + 10.0;
    const double leftEnd = kAnnunciatorX0 + 117.0;
    const double right = kAnnunciatorX0 + 124.0;
    const double rightEnd = kAnnunciatorX1 - 10.0;
    constexpr double kTileH = 60.0;
    constexpr double kTileGap = 8.0;
    // Five compact rows, so the listening EQ fits without making the drawer
    // scroll.
    const double rows[5] = {382.0, 450.0, 518.0, 586.0, 654.0};
    boxes.voiceClean = {left, rows[0], leftEnd, rows[0] + kTileH};
    boxes.hfEnhance = {right, rows[0], rightEnd, rows[0] + kTileH};
    boxes.agc = {left, rows[1], leftEnd, rows[1] + kTileH};
    boxes.blanker = {right, rows[1], rightEnd, rows[1] + kTileH};
    boxes.notch = {left, rows[2], leftEnd, rows[2] + kTileH};
    boxes.deemphasis = {right, rows[2], rightEnd, rows[2] + kTileH};
    boxes.tone = {left, rows[3], leftEnd, rows[3] + kTileH};
    boxes.filter = {right, rows[3], rightEnd, rows[3] + kTileH};
    boxes.reset = {left, rows[4], leftEnd, rows[4] + kTileH};
    boxes.backend = {right, rows[4], rightEnd, rows[4] + kTileH};
    return boxes;
}

QString audioOptionAt(double x, double y, double logicalHeight, double logicalWidth,
                      double railX0) {
    const AudioDrawerBoxes boxes = audioDrawerBoxes(logicalHeight, logicalWidth, railX0);
    const QVector<LabelledBox> ordered = {
        {QStringLiteral("close"), boxes.close},
        {QStringLiteral("mute"), boxes.mute},
        {QStringLiteral("voice_clean"), boxes.voiceClean},
        {QStringLiteral("hf_enhance"), boxes.hfEnhance},
        {QStringLiteral("squelch"), boxes.squelch},
        {QStringLiteral("agc"), boxes.agc},
        {QStringLiteral("blanker"), boxes.blanker},
        {QStringLiteral("notch"), boxes.notch},
        {QStringLiteral("deemphasis"), boxes.deemphasis},
        {QStringLiteral("tone"), boxes.tone},
        {QStringLiteral("filter"), boxes.filter},
        {QStringLiteral("reset"), boxes.reset},
        {QStringLiteral("backend"), boxes.backend},
    };
    for (const LabelledBox &entry : ordered) {
        if (entry.second.contains(x, y)) {
            return entry.first;
        }
    }
    return QString();
}

double audioVolumeAtX(double x, double logicalHeight, double logicalWidth, double railX0) {
    const ControlBox box = audioDrawerBoxes(logicalHeight, logicalWidth, railX0).volume;
    return pythonClamp((x - box.x0) / std::max(1.0, box.x1 - box.x0), 0.0, 1.0);
}

int squelchMaximum(const QString &radioMode) {
    const QString mode = radioMode.trimmed().toLower();
    return (mode == QStringLiteral("nbfm") || mode == QStringLiteral("nnfm")) ? 99 : 40;
}

int audioSquelchAtX(double x, int maximum, double logicalHeight, double logicalWidth,
                    double railX0) {
    const ControlBox box = audioDrawerBoxes(logicalHeight, logicalWidth, railX0).squelch;
    const double fraction = pythonClamp((x - box.x0) / std::max(1.0, box.x1 - box.x0), 0.0, 1.0);
    return pythonRoundToInt(fraction * static_cast<double>(maximum));
}

int audioDenoiseLevelAtX(double x, double logicalHeight, double logicalWidth, double railX0) {
    const ControlBox box = audioDrawerBoxes(logicalHeight, logicalWidth, railX0).denoise;
    const double fraction =
        pythonClamp((x - (box.x0 + 14.0)) / std::max(1.0, (box.x1 - 14.0) - (box.x0 + 14.0)), 0.0,
                    1.0);
    // Python keeps the first detent on a tie (`min` is stable), so an exact
    // midpoint rounds down rather than away.
    int nearest = 0;
    double best = -1.0;
    for (int index = 0; index < kDenoiseSliderPositions.size(); ++index) {
        const double distance = std::abs(kDenoiseSliderPositions.at(index) - fraction);
        if (best < 0.0 || distance < best) {
            best = distance;
            nearest = index;
        }
    }
    return nearest;
}

// ---------------------------------------------------------------------------
// Display (waterfall setup) drawer
// ---------------------------------------------------------------------------

DisplayDrawerBoxes displayDrawerBoxes(double logicalHeight, double logicalWidth, double railX0) {
    DisplayDrawerBoxes boxes;
    boxes.panel = {kAnnunciatorX0, kDrawerHeaderH, kAnnunciatorX1, lcdRailBottom(logicalHeight)};
    boxes.close = lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    const double innerX0 = kAnnunciatorX0 + 10.0;
    const double innerX1 = kAnnunciatorX1 - 10.0;
    constexpr double kColumnGap = 7.0;
    constexpr double kTileH = 64.0;
    constexpr double kAdjustH = 56.0;
    constexpr double kGap = 14.0;
    // Every LCD drawer is top-justified from the header and leaves a quiet lane
    // above the bottom Back button.
    boxes.reset = {innerX0, 84.0, innerX1, 142.0};
    const double toggleY0 = 158.0;
    const double floorY0 = toggleY0 + kTileH + kGap;
    const double ceilingY0 = floorY0 + kAdjustH + kGap;
    const double rateY0 = ceilingY0 + kAdjustH + kGap;
    const double paletteY0 = rateY0 + kTileH + kGap;
    const double instrumentsY0 = paletteY0 + kTileH + kGap;
    const double halfWidth = (innerX1 - innerX0 - kColumnGap) / 2.0;
    boxes.spectrum = {innerX0, toggleY0, innerX0 + halfWidth, toggleY0 + kTileH};
    boxes.autoScale = {innerX0 + halfWidth + kColumnGap, toggleY0, innerX1, toggleY0 + kTileH};
    // The floor and ceiling rows are continuous instruments on the LCD, so the
    // legacy `+` boxes stay empty and the drag owns the whole row.
    boxes.floor = {innerX0, floorY0, innerX1, floorY0 + kAdjustH};
    boxes.ceiling = {innerX0, ceilingY0, innerX1, ceilingY0 + kAdjustH};
    const double rateWidth = (innerX1 - innerX0 - 2.0 * kColumnGap) / 3.0;
    const QVector<QPair<int, QString>> rates = {
        {1, QStringLiteral("SLOW")},
        {2, QStringLiteral("MED")},
        {4, QStringLiteral("FAST")},
    };
    for (int index = 0; index < rates.size(); ++index) {
        const double left = innerX0 + index * (rateWidth + kColumnGap);
        boxes.rates.append({rates.at(index).first, rates.at(index).second,
                            {left, rateY0, left + rateWidth, rateY0 + kTileH}});
    }
    const double paletteWidth = (innerX1 - innerX0 - kColumnGap) / 2.0;
    const QVector<QPair<QString, QString>> palettes = {
        {QStringLiteral("classic"), QStringLiteral("CLASSIC")},
        {QStringLiteral("kiwi"), QStringLiteral("KIWI")},
    };
    for (int index = 0; index < palettes.size(); ++index) {
        const double left = innerX0 + index * (paletteWidth + kColumnGap);
        boxes.palettes.append({palettes.at(index).first, palettes.at(index).second,
                               {left, paletteY0, left + paletteWidth, paletteY0 + kTileH}});
    }
    boxes.instruments = {innerX0, instrumentsY0, innerX0 + halfWidth, instrumentsY0 + kTileH};
    boxes.scope = {innerX0 + halfWidth + kColumnGap, instrumentsY0, innerX1,
                   instrumentsY0 + kTileH};
    return boxes;
}

DisplayOption displayOptionAt(double x, double y, double logicalHeight, double logicalWidth,
                              double railX0) {
    const DisplayDrawerBoxes boxes = displayDrawerBoxes(logicalHeight, logicalWidth, railX0);
    DisplayOption option;
    if (boxes.close.contains(x, y)) {
        option.action = QStringLiteral("close");
        return option;
    }
    if (boxes.reset.contains(x, y)) {
        option.action = QStringLiteral("reset");
        return option;
    }
    if (boxes.instruments.contains(x, y)) {
        option.action = QStringLiteral("instruments");
        return option;
    }
    if (boxes.scope.contains(x, y)) {
        option.action = QStringLiteral("scope");
        return option;
    }
    if (boxes.spectrum.contains(x, y)) {
        option.action = QStringLiteral("spectrum");
        return option;
    }
    if (boxes.autoScale.contains(x, y)) {
        option.action = QStringLiteral("auto");
        return option;
    }
    for (const DisplayRateBox &rate : boxes.rates) {
        if (rate.box.contains(x, y)) {
            option.action = QStringLiteral("rate");
            option.rate = rate.rate;
            return option;
        }
    }
    for (const DisplayPaletteBox &palette : boxes.palettes) {
        if (palette.box.contains(x, y)) {
            option.action = QStringLiteral("palette");
            option.palette = palette.name;
            return option;
        }
    }
    return option;
}

// ---------------------------------------------------------------------------
// Info / fan-curve / font-review drawers
// ---------------------------------------------------------------------------

ThreeTileDrawerBoxes receiverHomeDrawerBoxes(double logicalHeight, double logicalWidth,
                                             double railX0) {
    ThreeTileDrawerBoxes boxes;
    boxes.panel = {railX0, kDrawerHeaderH, logicalWidth, lcdRailBottom(logicalHeight)};
    boxes.close = lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    boxes.first = {railX0 + 10.0, 258.0, logicalWidth - 10.0, 326.0};
    boxes.second = {railX0 + 10.0, 346.0, logicalWidth - 10.0, 414.0};
    boxes.third = {railX0 + 10.0, 426.0, logicalWidth - 10.0, 494.0};
    return boxes;
}

ThreeTileDrawerBoxes fanCurveDrawerBoxes(double logicalHeight, double logicalWidth, double railX0) {
    ThreeTileDrawerBoxes boxes;
    boxes.panel = {railX0, kDrawerHeaderH, logicalWidth, lcdRailBottom(logicalHeight)};
    boxes.close = lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    boxes.first = {railX0 + 10.0, 248.0, logicalWidth - 10.0, 318.0};
    boxes.second = {railX0 + 10.0, 346.0, logicalWidth - 10.0, 416.0};
    boxes.third = {railX0 + 10.0, 444.0, logicalWidth - 10.0, 514.0};
    return boxes;
}

CompactFontReviewBoxes compactFontReviewBoxes(double logicalHeight, double logicalWidth,
                                              double railX0) {
    CompactFontReviewBoxes boxes;
    const double x0 = railX0 + 8.0;
    const double x1 = logicalWidth - 8.0;
    const double gap = 8.0;
    const double half = (x1 - x0 - gap) / 2.0;
    boxes.panel = {railX0, 0.0, logicalWidth, lcdRailBottom(logicalHeight)};
    boxes.preview = {x0, 116.0, x1, 198.0};
    boxes.previous = {x0, 218.0, x0 + half, 286.0};
    boxes.next = {x0 + half + gap, 218.0, x1, 286.0};
    boxes.like = {x0, 304.0, x0 + half, 372.0};
    boxes.deleteButton = {x0 + half + gap, 304.0, x1, 372.0};
    boxes.use = {x0, 390.0, x1, 458.0};
    boxes.exit = lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
    return boxes;
}

QString compactFontReviewActionAt(double x, double y, double logicalHeight, double logicalWidth,
                                  double railX0) {
    const CompactFontReviewBoxes boxes =
        compactFontReviewBoxes(logicalHeight, logicalWidth, railX0);
    const QVector<QPair<QString, ControlBox>> ordered = {
        {QStringLiteral("previous"), boxes.previous},
        {QStringLiteral("next"), boxes.next},
        {QStringLiteral("like"), boxes.like},
        {QStringLiteral("delete"), boxes.deleteButton},
        {QStringLiteral("use"), boxes.use},
        {QStringLiteral("exit"), boxes.exit},
    };
    for (const QPair<QString, ControlBox> &entry : ordered) {
        if (entry.second.contains(x, y)) {
            return entry.first;
        }
    }
    return QString();
}

QString testsOptionAt(double x, double y) {
    for (const TestBox &entry : kTestBoxes) {
        if (entry.box.contains(x, y)) {
            return QString::fromLatin1(entry.action);
        }
    }
    return QString();
}

QVector<LabelledBox> testsActionBoxes() {
    QVector<LabelledBox> boxes;
    for (const TestBox &entry : kTestBoxes) {
        boxes.append({QString::fromLatin1(entry.action), entry.box});
    }
    return boxes;
}

ControlBox lcdRadioDrawerCloseBox(double logicalHeight, double logicalWidth, double railX0) {
    return lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
}

ControlBox lcdDisplayDrawerCloseBox(double logicalHeight, double logicalWidth, double railX0) {
    return lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
}

ControlBox lcdAudioDrawerCloseBox(double logicalHeight, double logicalWidth, double railX0) {
    return lcdDrawerBackBox(logicalHeight, logicalWidth, railX0);
}

// ---------------------------------------------------------------------------
// Modes drawer
// ---------------------------------------------------------------------------

ControlBox radioPanelBox(double logicalHeight) {
    return {kAnnunciatorX0, kDrawerHeaderH, kAnnunciatorX1, lcdRailBottom(logicalHeight)};
}

double lcdRadioModeGridY0() { return 88.0; }

double lcdRadioStepY0(int modeFamilyCount) {
    const int rows = std::max(1, static_cast<int>(std::ceil(modeFamilyCount / 2.0)));
    const double gridHeight = rows * 62.0 + (rows - 1) * 7.0;
    return lcdRadioModeGridY0() + gridHeight + 30.0;
}

QVector<QPair<QString, QStringList>> kiwiModeFamilies() { return kiwiFamilies(); }

QString nextRadioModeVariant(const QString &currentMode, const QStringList &modes) {
    if (modes.isEmpty()) {
        return QString();
    }
    const QString upper = currentMode.toUpper();
    const int index = modes.indexOf(upper);
    if (index < 0) {
        return modes.first();
    }
    return modes.at((index + 1) % modes.size());
}

QVector<ModeFamilyBox> radioModeLayout(double logicalHeight) {
    const ControlBox panel = radioPanelBox(logicalHeight);
    const double gridX0 = panel.x0 + 10.0;
    const double gridX1 = panel.x1 - 10.0;
    const double gridY0 = lcdRadioModeGridY0();
    const double gap = 7.0;
    const double buttonHeight = 62.0;
    const double available = gridX1 - gridX0;
    const double buttonWidth = (available - gap) / 2.0;
    QVector<ModeFamilyBox> boxes;
    for (int index = 0; index < kiwiModeFamilies().size(); ++index) {
        const int column = index % 2;
        const int row = index / 2;
        const double x0 = gridX0 + column * (buttonWidth + gap);
        const double y0 = gridY0 + row * (buttonHeight + gap);
        boxes.append({kiwiModeFamilies().at(index).first, kiwiModeFamilies().at(index).second,
                      {x0, y0, x0 + buttonWidth, y0 + buttonHeight}});
    }
    return boxes;
}

QVector<StepBox> radioStepOptions(const QString &receiverType, double logicalHeight) {
    QVector<StepBox> boxes;
    if (receiverType == QStringLiteral("fmdx")) {
        const QVector<qint64> steps = frequencyTuneSteps(QStringLiteral("fmdx"));
        for (int index = 0; index < steps.size(); ++index) {
            const double y = 364.0 + index * 74.0;
            boxes.append({steps.at(index), {kNavX0 + 10.0, y, 1280.0 - 10.0, y + 62.0}});
        }
        return boxes;
    }
    const ControlBox panel = radioPanelBox(logicalHeight);
    const double gap = 7.0;
    const double stepY0 = lcdRadioStepY0(kiwiModeFamilies().size());
    const double stepHeight = 52.0;
    const double buttonWidth = (panel.x1 - panel.x0 - 20.0 - gap) / 2.0;
    const QVector<qint64> steps = frequencyTuneSteps(QStringLiteral("kiwi"));
    for (int index = 0; index < steps.size(); ++index) {
        const int column = index % 2;
        const int row = index / 2;
        const double left = panel.x0 + 10.0 + column * (buttonWidth + gap);
        const double top = stepY0 + row * (stepHeight + gap);
        boxes.append({steps.at(index), {left, top, left + buttonWidth, top + stepHeight}});
    }
    return boxes;
}

ControlBox radioWsprBox(double logicalWidth, double railX0) {
    return {railX0 + 10.0, 522.0, logicalWidth - 10.0, 584.0};
}

double lcdRadioDrawerRevealY(double progress, double logicalHeight) {
    const ControlBox panel = radioPanelBox(logicalHeight);
    return panel.y0 + (panel.y1 - panel.y0) * progress;
}

RadioOption radioOptionAt(double x, double y, const QString &receiverType, double drawerProgress,
                          int familyCount, double logicalHeight, double logicalWidth) {
    Q_UNUSED(familyCount);
    RadioOption option;
    // A partly revealed drawer only accepts touches on the part that is showing,
    // which is what keeps the still-covered controls from taking a stray tap.
    if (y > lcdRadioDrawerRevealY(drawerProgress, logicalHeight)) {
        return option;
    }
    if (lcdRadioDrawerCloseBox(logicalHeight, logicalWidth).contains(x, y)) {
        option.action = QStringLiteral("close");
        return option;
    }
    if (receiverType != QStringLiteral("fmdx")) {
        // Kiwi and OpenWebRX are RF receivers with real mode controls. FM-DX's
        // tuner is server-controlled and is a later phase.
        for (const ModeFamilyBox &family : radioModeLayout(logicalHeight)) {
            if (family.box.contains(x, y)) {
                option.action = QStringLiteral("mode_cycle");
                option.modes = family.modes;
                return option;
            }
        }
        if (radioWsprBox(logicalWidth).contains(x, y)) {
            option.action = QStringLiteral("workspace");
            option.detail = QStringLiteral("wspr");
            return option;
        }
    } else {
        return option;
    }
    for (const StepBox &step : radioStepOptions(receiverType, logicalHeight)) {
        if (step.second.contains(x, y)) {
            option.action = QStringLiteral("step");
            option.stepHz = step.first;
            return option;
        }
    }
    return option;
}

}  // namespace ituner::core
