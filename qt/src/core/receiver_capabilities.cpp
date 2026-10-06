#include "receiver_capabilities.h"

namespace ituner::core {
namespace {

const QStringList kSourceFilters{
    QStringLiteral("local"), QStringLiteral("kiwi"), QStringLiteral("openwebrx"),
    QStringLiteral("fmdx"), QStringLiteral("all"),
};

const QStringList kProtocols{
    QStringLiteral("kiwi"), QStringLiteral("openwebrx"), QStringLiteral("local"),
    QStringLiteral("fmdx"),
};

const QStringList kKiwiModes{
    QStringLiteral("AM"), QStringLiteral("SAM"), QStringLiteral("LSB"), QStringLiteral("USB"),
    QStringLiteral("CW"), QStringLiteral("NBFM"), QStringLiteral("NFM"), QStringLiteral("WFM"),
    QStringLiteral("IQ"),
};

const QStringList kOpenWebRxModes{
    QStringLiteral("AM"), QStringLiteral("SAM"), QStringLiteral("LSB"), QStringLiteral("USB"),
    QStringLiteral("CW"), QStringLiteral("NBFM"), QStringLiteral("NFM"), QStringLiteral("WFM"),
};

const QStringList kLocalModes{QStringLiteral("AM"), QStringLiteral("NFM"), QStringLiteral("WFM"),
                              QStringLiteral("USB"), QStringLiteral("LSB")};

const QStringList kFmdxModes{QStringLiteral("FM")};

const QStringList kKiwiControls{
    QStringLiteral("frequency"),      QStringLiteral("mode"),
    QStringLiteral("passband"),       QStringLiteral("waterfall_pan"),
    QStringLiteral("waterfall_zoom"), QStringLiteral("agc"),
    QStringLiteral("squelch"),        QStringLiteral("volume"),
};

const QStringList kOpenWebRxControls{
    QStringLiteral("frequency"),      QStringLiteral("mode"),
    QStringLiteral("passband"),       QStringLiteral("waterfall_pan"),
    QStringLiteral("waterfall_zoom"), QStringLiteral("squelch"),
    QStringLiteral("volume"),
};

const QStringList kLocalControls{
    QStringLiteral("frequency"),      QStringLiteral("mode"),
    QStringLiteral("volume"),         QStringLiteral("squelch"),
    QStringLiteral("waterfall_zoom"),
};

/// `controls or default` in Python: an empty list means the default set.
QSet<QString> resolvedControls(const QStringList &controls, const QStringList &fallback) {
    const QStringList &source = controls.isEmpty() ? fallback : controls;
    return QSet<QString>(source.cbegin(), source.cend());
}

QStringList resolvedModes(const QStringList &modes, const QStringList &fallback) {
    return modes.isEmpty() ? fallback : modes;
}

}  // namespace

const QString kFmdxSharedFrequencyMessage =
    QStringLiteral("Shared tuner: changing frequency affects every listener");
const QString kFmdxFixedPassbandMessage = QStringLiteral("Fixed passband on this receiver");
const QString kFmdxFixedModeMessage = QStringLiteral("FM mode is controlled by the shared receiver");
const QString kFmdxFixedSpectrumMessage = QStringLiteral("Audio spectrum is fixed to 20 kHz");

QStringList sourceFilters() {
    return kSourceFilters;
}

QStringList protocolNames() {
    return kProtocols;
}

QString defaultSourceGroup(const QString &protocol) {
    const QString key = protocol.toLower();
    if (kProtocols.contains(key)) {
        return key;
    }
    return protocol;
}

int sourcePriority(const QString &sourceGroup) {
    const int index = kSourceFilters.indexOf(sourceGroup);
    // `all` is the combined view, not a priority bucket.
    if (index < 0 || index == kSourceFilters.size() - 1) {
        return -1;
    }
    return index;
}

QStringList modesForProtocol(const QString &protocol) {
    const QString key = protocol.toLower();
    if (key == QStringLiteral("openwebrx")) {
        return kOpenWebRxModes;
    }
    if (key == QStringLiteral("local")) {
        return kLocalModes;
    }
    if (key == QStringLiteral("fmdx")) {
        return kFmdxModes;
    }
    return kKiwiModes;
}

QString humanizeControl(const QString &control) {
    // `str(control).replace("_", " ").title()`: only underscores become
    // spaces, and a letter after any separator is upper-cased.
    const QString spaced = QString(control).replace(QLatin1Char('_'), QLatin1Char(' '));
    QString result;
    result.reserve(spaced.size());
    bool previousWasLetter = false;
    for (const QChar character : spaced) {
        const bool isLetter = character.isLetter();
        result += isLetter ? (previousWasLetter ? character.toLower() : character.toUpper())
                           : character;
        previousWasLetter = isLetter;
    }
    return result;
}

ControlDecision ReceiverCapabilities::decide(const QString &control,
                                            bool sharedControlAcknowledged) const {
    if (controls.contains(control)) {
        return ControlDecision{true, QString()};
    }
    if (control == QStringLiteral("frequency")
        && controlScope == QStringLiteral("shared_server") && sharedControlAcknowledged) {
        return ControlDecision{true, QString()};
    }

    // A fixed control with an empty reason still wins over the generic message,
    // exactly as the Python `fixed_controls.get()` lookup does.
    const auto fixed = fixedControls.constFind(control);
    if (fixed != fixedControls.constEnd()) {
        return ControlDecision{false, fixed.value()};
    }
    return ControlDecision{
        false,
        QStringLiteral("%1 is unavailable on this receiver").arg(humanizeControl(control))};
}

bool ReceiverCapabilities::supportsFrequency(double frequencyKhz) const {
    if (frequencyRangesKhz.isEmpty()) {
        return true;
    }
    for (const QPair<double, double> &range : frequencyRangesKhz) {
        if (range.first <= frequencyKhz && frequencyKhz <= range.second) {
            return true;
        }
    }
    return false;
}

std::optional<QPair<double, double>> ReceiverCapabilities::tuningBounds() const {
    if (frequencyRangesKhz.isEmpty()) {
        return std::nullopt;
    }
    return QPair<double, double>{frequencyRangesKhz.first().first,
                                 frequencyRangesKhz.last().second};
}

ReceiverCapabilities ReceiverCapabilities::kiwi(const QStringList &controls,
                                                const QStringList &modes) {
    ReceiverCapabilities caps;
    caps.protocol = QStringLiteral("kiwi");
    caps.label = QStringLiteral("KIWI");
    caps.controls = resolvedControls(controls, kKiwiControls);
    caps.modes = resolvedModes(modes, kKiwiModes);
    caps.waterfallKind = QStringLiteral("rf");
    caps.controlScope = QStringLiteral("per_session");
    return caps;
}

ReceiverCapabilities ReceiverCapabilities::openwebrx(
    const QStringList &controls, const QStringList &modes,
    const QList<QPair<double, double>> &frequencyRangesKhz, double sourceSpanKhz) {
    ReceiverCapabilities caps;
    caps.protocol = QStringLiteral("openwebrx");
    caps.label = QStringLiteral("OPENWEBRX");
    caps.controls = resolvedControls(controls, kOpenWebRxControls);
    caps.modes = resolvedModes(modes, kOpenWebRxModes);
    caps.waterfallKind = QStringLiteral("rf");
    caps.controlScope = QStringLiteral("per_session");
    caps.frequencyRangesKhz = frequencyRangesKhz;
    caps.sourceSpanKhz = sourceSpanKhz;
    return caps;
}

ReceiverCapabilities ReceiverCapabilities::localDevice(
    const QString &label, const QStringList &controls, const QStringList &modes,
    const QHash<QString, QString> &fixedControls, const QString &waterfallKind,
    const QList<QPair<double, double>> &frequencyRangesKhz, double sourceSpanKhz) {
    QHash<QString, QString> reasons{
        {QStringLiteral("passband"), QStringLiteral("Passband is set by %1").arg(label)},
        {QStringLiteral("agc"), QStringLiteral("AGC is set by %1").arg(label)},
    };
    for (auto it = fixedControls.constBegin(); it != fixedControls.constEnd(); ++it) {
        reasons.insert(it.key(), it.value());
    }

    ReceiverCapabilities caps;
    caps.protocol = QStringLiteral("local");
    caps.label = label;
    caps.controls = resolvedControls(controls, kLocalControls);
    caps.fixedControls = reasons;
    caps.modes = resolvedModes(modes, kLocalModes);
    caps.waterfallKind = waterfallKind;
    caps.controlScope = QStringLiteral("per_session");
    caps.frequencyRangesKhz = frequencyRangesKhz;
    caps.sourceSpanKhz = sourceSpanKhz;
    return caps;
}

ReceiverCapabilities ReceiverCapabilities::fixedAudio(const QString &protocol, const QString &label,
                                                      const std::optional<QStringList> &controls,
                                                      const QStringList &modes,
                                                      const QString &controlScope) {
    ReceiverCapabilities caps;
    caps.protocol = protocol;
    caps.label = label;
    // `controls if controls is not None else ("volume",)`: an explicitly empty
    // list means this receiver exposes no controls at all.
    const QStringList resolved = controls.has_value() ? *controls : QStringList{QStringLiteral("volume")};
    caps.controls = QSet<QString>(resolved.cbegin(), resolved.cend());
    caps.fixedControls = {
        {QStringLiteral("frequency"), kFmdxSharedFrequencyMessage},
        {QStringLiteral("mode"), kFmdxFixedModeMessage},
        {QStringLiteral("passband"), kFmdxFixedPassbandMessage},
        {QStringLiteral("waterfall_pan"), kFmdxFixedSpectrumMessage},
        {QStringLiteral("waterfall_zoom"), kFmdxFixedSpectrumMessage},
        {QStringLiteral("agc"), QStringLiteral("Gain is controlled at the shared receiver")},
        {QStringLiteral("squelch"), QStringLiteral("Squelch is controlled at the shared receiver")},
    };
    caps.modes = modes.isEmpty() ? kFmdxModes : modes;
    caps.waterfallKind = QStringLiteral("audio_spectrum");
    caps.controlScope = controlScope;
    return caps;
}

ReceiverCapabilities capabilitiesFor(const QString &protocol,
                                     const CapabilityOverrides &overrides) {
    const QString key = protocol.toLower();

    ReceiverCapabilities caps;
    if (key == QStringLiteral("fmdx")) {
        caps = ReceiverCapabilities::fixedAudio(
            QStringLiteral("fmdx"), overrides.label.value_or(QStringLiteral("FM-DX")),
            overrides.controls, overrides.modes.value_or(QStringList()),
            overrides.controlScope.value_or(QStringLiteral("shared_server")));
    } else if (key == QStringLiteral("openwebrx")) {
        caps = ReceiverCapabilities::openwebrx(overrides.controls.value_or(QStringList()),
                                               overrides.modes.value_or(QStringList()),
                                               overrides.frequencyRangesKhz,
                                               overrides.sourceSpanKhz);
    } else if (key == QStringLiteral("local")) {
        caps = ReceiverCapabilities::localDevice(
            overrides.label.value_or(QStringLiteral("LOCAL")),
            overrides.controls.value_or(QStringList()), overrides.modes.value_or(QStringList()), {},
            overrides.waterfallKind.value_or(QStringLiteral("rf")), overrides.frequencyRangesKhz,
            overrides.sourceSpanKhz);
    } else {
        caps = ReceiverCapabilities::kiwi(overrides.controls.value_or(QStringList()),
                                          overrides.modes.value_or(QStringList()));
    }

    // FM-DX keeps the label it was constructed with; every other transport
    // accepts a record-level label.
    if (overrides.label.has_value() && key != QStringLiteral("fmdx")) {
        caps.label = *overrides.label;
    }
    if (overrides.controlScope.has_value()) {
        caps.controlScope = *overrides.controlScope;
    }
    if (overrides.waterfallKind.has_value()) {
        caps.waterfallKind = *overrides.waterfallKind;
    }
    return caps;
}

}  // namespace ituner::core
