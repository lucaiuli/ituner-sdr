#include "state_store.h"

#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QJsonValue>
#include <QSaveFile>
#include <QUrl>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>

#include "receiver_catalog.h"
#include "tuning.h"
#include "waterfall_model.h"

namespace ituner::core {
namespace {

constexpr const char *kVersion = "version";
constexpr int kStateVersion = 4;

const QStringList &receiverTypes() {
    static const QStringList types{QStringLiteral("kiwi"), QStringLiteral("openwebrx"),
                                   QStringLiteral("local"), QStringLiteral("fmdx")};
    return types;
}

bool schemeAndHostAcceptable(const QUrl &url) {
    const QString scheme = url.scheme().toLower();
    return (scheme == QStringLiteral("http") || scheme == QStringLiteral("https"))
           && !url.host().isEmpty();
}

/// Python `round(value, 3)`.
///
/// A correctly-rounded decimal conversion is required: `nearbyint(value *
/// 1000.0) / 1000.0` differs from CPython on values whose exact binary form
/// sits a hair above a half, because multiplying by 1000 introduces its own
/// rounding (`round(7075.0005, 3)` is 7075.001, whereas the multiply-then-round
/// form answers 7075.0). `snprintf("%.3f")`, like CPython's `_Py_dg_dtoa`,
/// rounds the exact value to three decimals, so the two agree.
double roundToThousandths(double value) {
    if (!std::isfinite(value)) {
        return value;
    }
    char buffer[64];
    const int written = std::snprintf(buffer, sizeof buffer, "%.3f", value);
    if (written < 0 || written >= static_cast<int>(sizeof buffer)) {
        // Only reachable for magnitudes far outside any radio frequency; the
        // multiply-and-round form is good enough there and never truncates.
        return std::nearbyint(value * 1000.0) / 1000.0;
    }
    return std::strtod(buffer, nullptr);
}

/// `str(x or "")` for a JSON member: a missing/null member answers the empty
/// string, everything else its scalar text.
QString scalarString(const QJsonValue &value) {
    if (value.isUndefined() || value.isNull()) {
        return {};
    }
    if (value.isString()) {
        return value.toString();
    }
    if (value.isBool()) {
        return value.toBool() ? QStringLiteral("True") : QStringLiteral("False");
    }
    return value.toVariant().toString();
}

}  // namespace

QString rememberedViewPath() {
    return QDir::homePath() + QStringLiteral("/.local/state/kiwi-gl-display-receiver.json");
}

bool isRememberedEndpoint(const QString &server) {
    if (server.isEmpty()) {
        return false;
    }
    return schemeAndHostAcceptable(QUrl(server));
}

std::optional<QVariantMap> loadRememberedView(const QString &path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return std::nullopt;
    }

    QJsonParseError error{};
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        return std::nullopt;
    }
    const QJsonObject saved = document.object();

    const QJsonValue serverValue = saved.value(QStringLiteral("server"));
    if (!serverValue.isString()) {
        // Python feeds the raw value to `urlparse`, which raises for a non-string
        // and is swallowed by the enclosing `except`.
        return std::nullopt;
    }
    const QString server = serverValue.toString();
    if (!isRememberedEndpoint(server)) {
        return std::nullopt;
    }

    QVariantMap view;
    view.insert(QStringLiteral("server"), server);

    const QString receiverType =
        scalarString(saved.value(QStringLiteral("receiver_type"))).toLower();
    if (receiverType == QStringLiteral("kiwi") || receiverType == QStringLiteral("fmdx")) {
        view.insert(QStringLiteral("receiver_type"), receiverType);
    }

    const QJsonValue freqValue = saved.value(QStringLiteral("freq_khz"));
    if (freqValue.isDouble() || freqValue.isBool()) {
        const double freq = freqValue.isBool() ? (freqValue.toBool() ? 1.0 : 0.0)
                                               : freqValue.toDouble();
        const double maximum =
            receiverType == QStringLiteral("fmdx") ? fmdxMaxKhz() : tuningMaxKhz();
        if (freq >= 0.0 && freq <= maximum) {
            view.insert(QStringLiteral("freq_khz"), freq);
        }
    }

    const QJsonValue zoomValue = saved.value(QStringLiteral("zoom"));
    if (zoomValue.isBool()) {
        view.insert(QStringLiteral("zoom"), zoomValue.toBool() ? 1 : 0);
    } else if (zoomValue.isDouble()) {
        const double zoom = zoomValue.toDouble();
        if (std::floor(zoom) == zoom && zoom >= 0.0 && zoom <= displayMaxZoom()) {
            view.insert(QStringLiteral("zoom"), static_cast<int>(zoom));
        }
    }

    const QJsonValue modeValue = saved.value(QStringLiteral("radio_mode"));
    if (modeValue.isString()) {
        const QString mode = modeValue.toString().toUpper();
        if (isKiwiRadioMode(mode)) {
            view.insert(QStringLiteral("radio_mode"), mode);
        }
    }

    const QJsonValue preferencesValue = saved.value(QStringLiteral("preferences"));
    if (preferencesValue.isObject()) {
        view.insert(QStringLiteral("preferences"), preferencesValue.toObject().toVariantMap());
    }

    const QVariantMap migrated = migrateRememberedView(saved.toVariantMap());
    view.insert(QStringLiteral("receiver_id"),
                migrated.value(QStringLiteral("receiver_id")).toString());
    view.insert(QStringLiteral("protocol"),
                migrated.value(QStringLiteral("protocol")).toString());
    // Shared FM-DX control is never restored; the session starts read-only.
    view.insert(QStringLiteral("shared_control"), false);
    return view;
}

bool saveRememberedView(const QString &path, const QString &server, double freqKhz, int zoom,
                        const std::optional<QString> &radioMode, bool manualRadioMode,
                        const QVariantMap &preferences,
                        const std::optional<QString> &receiverType) {
    if (!isRememberedEndpoint(server)) {
        return false;
    }

    QJsonObject saved;
    saved.insert(QString::fromLatin1(kVersion), kStateVersion);
    saved.insert(QStringLiteral("freq_khz"), roundToThousandths(freqKhz));
    saved.insert(QStringLiteral("server"), server);
    saved.insert(QStringLiteral("zoom"), std::clamp(zoom, 0, displayMaxZoom()));

    const QString selectedType = receiverType ? receiverType->toLower() : QString();
    if (receiverTypes().contains(selectedType)) {
        saved.insert(QStringLiteral("receiver_type"), selectedType);
        saved.insert(QStringLiteral("protocol"), selectedType);
        QString trimmed = server;
        while (trimmed.endsWith(QLatin1Char('/'))) {
            trimmed.chop(1);
        }
        saved.insert(QStringLiteral("receiver_id"),
                     QStringLiteral("%1:%2").arg(selectedType, trimmed));
    }

    if (manualRadioMode && radioMode.has_value()) {
        const QString mode = radioMode->toUpper();
        if (isKiwiRadioMode(mode)) {
            saved.insert(QStringLiteral("radio_mode"), mode);
        }
    }

    if (!preferences.isEmpty()) {
        saved.insert(QStringLiteral("preferences"), QJsonObject::fromVariantMap(preferences));
    }

    const QByteArray payload = QJsonDocument(saved).toJson(QJsonDocument::Compact) + '\n';

    QSaveFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        return false;
    }
    if (file.write(payload) != payload.size()) {
        file.cancelWriting();
        return false;
    }
    return file.commit();
}

}  // namespace ituner::core
