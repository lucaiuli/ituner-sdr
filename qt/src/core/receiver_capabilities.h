// Truthful control and coverage contract for one receiver type.
//
// A direct port of the capability half of `UI/receiver_catalog.py`. The contract
// is deliberately small: a control is either allowed, or rejected with one
// human-readable reason, and that reason is what the Home screen shows when a
// fixed or unavailable control is touched. No input is ever silently ignored.
//
// The Python module is the specification. Where Python relies on a subtle
// distinction this port keeps it, for example `controls or default` (an empty
// list means the default set) versus `controls if controls is not None`
// (an empty list really means no controls). Both are marked below.

#pragma once

#include <QHash>
#include <QList>
#include <QPair>
#include <QSet>
#include <QString>
#include <QStringList>

#include <optional>

namespace ituner::core {

/// Browser segments in priority order. `all` is the combined view and is always
/// selected last so the five segments stay readable on 1280x800.
QStringList sourceFilters();

/// Transports, independent of the browser segment they appear under.
QStringList protocolNames();

/// The default browser segment for a transport. A record may override it: a LAN
/// Kiwi is `protocol=kiwi` with `source_group=local`.
QString defaultSourceGroup(const QString &protocol);

/// Index of a segment in `sourceFilters()`, or -1 when it is not a segment.
int sourcePriority(const QString &sourceGroup);

/// Default mode list for a transport.
QStringList modesForProtocol(const QString &protocol);

/// `str(control).replace("_", " ").title()` from the Python source.
QString humanizeControl(const QString &control);

/// The exact reasons FM-DX explains when a shared-server control is touched
/// without an explicit, session-only acknowledgement.
extern const QString kFmdxSharedFrequencyMessage;
extern const QString kFmdxFixedPassbandMessage;
extern const QString kFmdxFixedModeMessage;
extern const QString kFmdxFixedSpectrumMessage;

/// Whether one control may change state, and why not when it may not.
struct ControlDecision {
    bool allowed = false;
    QString message;
};

/// Truthful control and coverage contract for one receiver type.
struct ReceiverCapabilities {
    QString protocol;
    QString label;
    QSet<QString> controls;
    QHash<QString, QString> fixedControls;
    QStringList modes;
    QString waterfallKind = QStringLiteral("rf");
    QString controlScope = QStringLiteral("per_session");
    QList<QPair<double, double>> frequencyRangesKhz;
    double sourceSpanKhz = 0.0;

    /// Resolve one control against this contract.
    ///
    /// A shared-server frequency control stays blocked until the operator
    /// explicitly acknowledges it for the current session; every other scope
    /// ignores that flag.
    ControlDecision decide(const QString &control,
                           bool sharedControlAcknowledged = false) const;

    /// True when the contract either declares no range or the frequency falls
    /// inside one of them. The Python version parses text and returns false on
    /// bad input; C++ callers already hold a number, so that branch cannot occur.
    bool supportsFrequency(double frequencyKhz) const;

    /// Whole coverage of the declared ranges, or nothing when none are declared.
    std::optional<QPair<double, double>> tuningBounds() const;

    /// A per-session Kiwi. An empty `controls` list means the default set.
    static ReceiverCapabilities kiwi(const QStringList &controls = {},
                                     const QStringList &modes = {});

    /// A per-session OpenWebRX, which may declare coverage and a source span.
    static ReceiverCapabilities openwebrx(const QStringList &controls = {},
                                          const QStringList &modes = {},
                                          const QList<QPair<double, double>> &frequencyRangesKhz = {},
                                          double sourceSpanKhz = 0.0);

    /// A local device: passband and AGC are set by the device and say so.
    static ReceiverCapabilities localDevice(const QString &label = QStringLiteral("LOCAL"),
                                            const QStringList &controls = {},
                                            const QStringList &modes = {},
                                            const QHash<QString, QString> &fixedControls = {},
                                            const QString &waterfallKind = QStringLiteral("rf"),
                                            const QList<QPair<double, double>> &frequencyRangesKhz = {},
                                            double sourceSpanKhz = 0.0);

    /// A receiver whose audio path is derived and whose tuning is shared.
    ///
    /// FM-DX exposes only volume; frequency, mode, passband and spectrum pan are
    /// fixed or shared and explain themselves when touched. Unlike the other
    /// factories, an empty `controls` list here means no controls at all.
    static ReceiverCapabilities fixedAudio(const QString &protocol = QStringLiteral("fmdx"),
                                           const QString &label = QStringLiteral("FM-DX"),
                                           const std::optional<QStringList> &controls = std::nullopt,
                                           const QStringList &modes = {},
                                           const QString &controlScope =
                                               QStringLiteral("shared_server"));
};

/// Record-level overrides accepted when building a protocol default.
struct CapabilityOverrides {
    std::optional<QString> label;
    std::optional<QString> controlScope;
    std::optional<QString> waterfallKind;
    std::optional<QStringList> controls;
    std::optional<QStringList> modes;
    QList<QPair<double, double>> frequencyRangesKhz;
    double sourceSpanKhz = 0.0;
};

/// Build the default contract for a transport, honouring record overrides.
ReceiverCapabilities capabilitiesFor(const QString &protocol,
                                     const CapabilityOverrides &overrides = {});

}  // namespace ituner::core
