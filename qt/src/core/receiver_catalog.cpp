#include "receiver_catalog.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QMetaType>
#include <QSet>
#include <QVariant>

#include <algorithm>

namespace ituner::core {
namespace {

/// Python truthiness for the `a or b` chains this module is built from.
bool isFalsy(const QVariant &value) {
    if (!value.isValid() || value.isNull()) {
        return true;
    }
    switch (value.typeId()) {
    case QMetaType::QString:
        return value.toString().isEmpty();
    case QMetaType::Bool:
        return !value.toBool();
    case QMetaType::Int:
    case QMetaType::UInt:
    case QMetaType::LongLong:
    case QMetaType::ULongLong:
    case QMetaType::Double:
    case QMetaType::Float:
        return value.toDouble() == 0.0;
    case QMetaType::QVariantList:
        return value.toList().isEmpty();
    case QMetaType::QVariantMap:
        return value.toMap().isEmpty();
    default:
        return false;
    }
}

QVariant firstFalsySkipped(const QVariantMap &map, std::initializer_list<const char *> keys) {
    for (const char *key : keys) {
        const QVariant value = map.value(QLatin1String(key));
        if (!isFalsy(value)) {
            return value;
        }
    }
    return {};
}

/// `dict.get(key, default)`: the key's own value wins even when it is null.
QVariant firstPresent(const QVariantMap &map, std::initializer_list<const char *> keys) {
    for (const char *key : keys) {
        const auto entry = map.constFind(QLatin1String(key));
        if (entry != map.constEnd()) {
            return entry.value();
        }
    }
    return {};
}

std::optional<QString> optionalText(const QVariant &value) {
    if (!value.isValid() || value.isNull()) {
        return std::nullopt;
    }
    return value.toString();
}

std::optional<double> asFloat(const QVariant &value) {
    if (!value.isValid() || value.isNull()) {
        return std::nullopt;
    }
    bool ok = false;
    const double result = value.toDouble(&ok);
    if (!ok) {
        return std::nullopt;
    }
    return result;
}

std::optional<int> asInt(const QVariant &value) {
    if (!value.isValid() || value.isNull()) {
        return std::nullopt;
    }
    bool ok = false;
    const int result = value.toInt(&ok);
    if (!ok) {
        return std::nullopt;
    }
    return result;
}

bool asBool(const QVariant &value) {
    if (!value.isValid() || value.isNull()) {
        return false;
    }
    switch (value.typeId()) {
    case QMetaType::QString:
        // Python `bool("false")` is True: any non-empty string is truthy.
        return !value.toString().isEmpty();
    case QMetaType::QVariantList:
        return !value.toList().isEmpty();
    case QMetaType::QVariantMap:
        return !value.toMap().isEmpty();
    default:
        return !isFalsy(value);
    }
}

QStringList asStringList(const QVariant &value) {
    QStringList result;
    const QVariantList list = value.toList();
    result.reserve(list.size());
    for (const QVariant &item : list) {
        result.append(item.toString());
    }
    return result;
}

QList<QPair<double, double>> asRanges(const QVariant &value) {
    QList<QPair<double, double>> ranges;
    const QVariantList list = value.toList();
    for (const QVariant &item : list) {
        const QVariantList pair = item.toList();
        if (pair.size() < 2) {
            continue;
        }
        const std::optional<double> low = asFloat(pair.at(0));
        const std::optional<double> high = asFloat(pair.at(1));
        if (!low.has_value() || !high.has_value()) {
            continue;
        }
        ranges.append(QPair<double, double>{*low, *high});
    }
    return ranges;
}

int receiverSortKey(const ReceiverRecord &record) {
    int priority = sourcePriority(record.sourceGroup);
    if (priority < 0) {
        priority = sourcePriority(record.protocol);
    }
    return priority < 0 ? sourceFilters().size() : priority;
}

std::optional<QString> declaredProtocol(const QString &value) {
    return value.isEmpty() ? std::nullopt : std::optional<QString>(value);
}

QVariant optionalVariant(const std::optional<double> &value) {
    return value.has_value() ? QVariant(*value) : QVariant();
}

}  // namespace

QString canonicalEndpoint(const QString &endpoint) {
    QString text = endpoint.trimmed();
    while (text.endsWith(QLatin1Char('/'))) {
        text.chop(1);
    }
    return text;
}

QString inferProtocol(const QString &endpoint, const std::optional<QString> &declared) {
    if (declared.has_value() && !declared->isEmpty()) {
        return declared->toLower();
    }
    const QString value = endpoint.toLower();
    if (value.startsWith(QLatin1String("owrx://")) || value.startsWith(QLatin1String("owrxs://"))) {
        return QStringLiteral("openwebrx");
    }
    if (value.startsWith(QLatin1String("local://")) || value.startsWith(QLatin1String("usb://"))) {
        return QStringLiteral("local");
    }
    return QStringLiteral("kiwi");
}

QVariantList ReceiverRecord::legacyRow() const {
    return QVariantList{name,
                        location,
                        endpoint,
                        listenersUsed.has_value() ? QVariant(*listenersUsed) : QVariant(),
                        listenersTotal.has_value() ? QVariant(*listenersTotal) : QVariant(),
                        optionalVariant(latitude),
                        optionalVariant(longitude),
                        protocol};
}

ReceiverRecord normalizeReceiver(const ReceiverRecord &record) {
    return record;
}

ReceiverRecord normalizeReceiver(const QVariantMap &record) {
    const QString endpoint = firstFalsySkipped(record, {"endpoint", "server"}).toString().trimmed();
    const QString declared = firstFalsySkipped(record, {"protocol", "receiver_type"}).toString();
    const QString protocol = inferProtocol(endpoint, declaredProtocol(declared));

    QString sourceGroup = firstFalsySkipped(record, {"source_group"}).toString();
    if (sourceGroup.isEmpty()) {
        sourceGroup = defaultSourceGroup(protocol);
    }
    if (sourcePriority(sourceGroup) < 0) {
        sourceGroup = defaultSourceGroup(protocol);
    }

    QString name = firstFalsySkipped(record, {"name"}).toString().trimmed();
    if (name.isEmpty()) {
        name = QStringLiteral("Receiver");
    }
    const QString location = firstFalsySkipped(record, {"location", "loc"}).toString().trimmed();

    CapabilityOverrides overrides;
    overrides.label = optionalText(firstPresent(record, {"label"}));
    overrides.controlScope = optionalText(firstPresent(record, {"control_scope"}));
    overrides.waterfallKind = optionalText(firstPresent(record, {"waterfall_kind"}));
    // `controls` keeps the absent-versus-empty distinction: for FM-DX an empty
    // list means no controls, while an absent key means the protocol default.
    if (const auto entry = record.constFind(QStringLiteral("controls"));
        entry != record.constEnd() && !entry.value().isNull()) {
        overrides.controls = asStringList(entry.value());
    }
    overrides.modes = asStringList(firstFalsySkipped(record, {"modes"}));
    overrides.frequencyRangesKhz = asRanges(firstFalsySkipped(record, {"frequency_ranges_khz"}));
    overrides.sourceSpanKhz = asFloat(firstFalsySkipped(record, {"source_span_khz"})).value_or(0.0);

    ReceiverRecord result;
    const QString providedId = firstFalsySkipped(record, {"id"}).toString();
    result.id = providedId.isEmpty()
                    ? QStringLiteral("%1:%2").arg(protocol, canonicalEndpoint(endpoint))
                    : providedId;
    result.protocol = protocol;
    result.sourceGroup = sourceGroup;
    result.endpoint = endpoint;
    result.name = name;
    result.location = location;
    result.latitude = asFloat(firstPresent(record, {"latitude", "lat"}));
    result.longitude = asFloat(firstPresent(record, {"longitude", "lon"}));
    result.capabilities = capabilitiesFor(protocol, overrides);
    result.listenersUsed = asInt(firstPresent(record, {"listeners_used", "used"}));
    result.listenersTotal = asInt(firstPresent(record, {"listeners_total", "total"}));
    result.favorite = asBool(firstPresent(record, {"favorite"}));
    return result;
}

QVector<ReceiverRecord> recordsFromKiwiDirectory(const QVector<QVariantList> &rows) {
    QVector<ReceiverRecord> records;
    records.reserve(rows.size());
    for (const QVariantList &row : rows) {
        if (row.size() < 3) {
            continue;
        }
        QVariantMap raw;
        raw.insert(QStringLiteral("name"), row.at(0));
        raw.insert(QStringLiteral("location"), row.at(1));
        raw.insert(QStringLiteral("server"), row.at(2));
        if (row.size() > 3) {
            raw.insert(QStringLiteral("used"), row.at(3));
        }
        if (row.size() > 4) {
            raw.insert(QStringLiteral("total"), row.at(4));
        }
        if (row.size() > 5) {
            raw.insert(QStringLiteral("lat"), row.at(5));
        }
        if (row.size() > 6) {
            raw.insert(QStringLiteral("lon"), row.at(6));
        }
        const std::optional<QString> declared =
            row.size() > 7 ? optionalText(row.at(7)) : std::nullopt;
        raw.insert(QStringLiteral("protocol"), inferProtocol(row.at(2).toString(), declared));
        records.append(normalizeReceiver(raw));
    }
    return records;
}

QVector<ReceiverRecord> recordsFromFmdxDirectory(const QVector<QVariantMap> &receivers) {
    QVector<ReceiverRecord> records;
    records.reserve(receivers.size());
    for (const QVariantMap &receiver : receivers) {
        const QVariant endpoint = firstFalsySkipped(receiver, {"server", "endpoint"});
        if (isFalsy(endpoint)) {
            continue;
        }

        QVariantMap raw;
        raw.insert(QStringLiteral("name"), receiver.value(QStringLiteral("name")));
        raw.insert(QStringLiteral("location"), receiver.value(QStringLiteral("location")));
        raw.insert(QStringLiteral("server"), endpoint);
        raw.insert(QStringLiteral("lat"), receiver.value(QStringLiteral("lat")));
        raw.insert(QStringLiteral("lon"), receiver.value(QStringLiteral("lon")));
        raw.insert(QStringLiteral("used"), receiver.value(QStringLiteral("used")));
        raw.insert(QStringLiteral("total"), receiver.value(QStringLiteral("total")));
        raw.insert(QStringLiteral("protocol"), QStringLiteral("fmdx"));
        raw.insert(QStringLiteral("source_group"), QStringLiteral("fmdx"));

        // `receiver.get("minimum_khz", 64_000.0)`. A present but non-numeric
        // bound falls back to the default here; the Python original would carry
        // the value through and fail later during a coverage check.
        const double minimum =
            asFloat(firstPresent(receiver, {"minimum_khz"})).value_or(64000.0);
        const double maximum =
            asFloat(firstPresent(receiver, {"maximum_khz"})).value_or(108000.0);
        raw.insert(QStringLiteral("frequency_ranges_khz"),
                   QVariantList{QVariantList{minimum, maximum}});

        records.append(normalizeReceiver(raw));
    }
    return records;
}

QString staticSourcesPath() {
    return qEnvironmentVariable("ITUNER_SDR_STATIC_SOURCES");
}

QVector<ReceiverRecord> loadStaticSources(const QString &path) {
    if (path.isEmpty()) {
        return {};
    }
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    const QJsonDocument document = QJsonDocument::fromJson(file.readAll());
    if (!document.isObject()) {
        return {};
    }
    const QJsonValue receivers = document.object().value(QStringLiteral("receivers"));
    if (!receivers.isArray()) {
        return {};
    }

    QVector<ReceiverRecord> records;
    const QJsonArray items = receivers.toArray();
    records.reserve(items.size());
    for (const QJsonValue &item : items) {
        if (!item.isObject()) {
            continue;
        }
        const QVariantMap raw = item.toObject().toVariantMap();
        if (isFalsy(raw.value(QStringLiteral("endpoint")))) {
            continue;
        }
        records.append(normalizeReceiver(raw));
    }
    return records;
}

QVector<ReceiverRecord> sortReceivers(const QVector<ReceiverRecord> &records) {
    QVector<ReceiverRecord> sorted = records;
    std::stable_sort(sorted.begin(), sorted.end(),
                     [](const ReceiverRecord &left, const ReceiverRecord &right) {
                         return receiverSortKey(left) < receiverSortKey(right);
                     });
    return sorted;
}

QVector<ReceiverRecord> filterReceivers(const QVector<ReceiverRecord> &records,
                                        const QString &source) {
    const QString requested = source.isEmpty() ? QStringLiteral("all") : source.toLower();
    const QString selected =
        sourceFilters().contains(requested) ? requested : QStringLiteral("all");
    if (selected == QStringLiteral("all")) {
        return sortReceivers(records);
    }
    QVector<ReceiverRecord> filtered;
    for (const ReceiverRecord &record : records) {
        if (record.sourceGroup == selected) {
            filtered.append(record);
        }
    }
    return sortReceivers(filtered);
}

QVector<ReceiverRecord> mergeCatalogs(const QList<QVector<ReceiverRecord>> &groups) {
    QVector<ReceiverRecord> merged;
    QSet<QString> seenIds;
    QSet<QString> seenEndpoints;
    for (const QVector<ReceiverRecord> &group : groups) {
        for (const ReceiverRecord &item : group) {
            const ReceiverRecord record = normalizeReceiver(item);
            const QString endpoint = canonicalEndpoint(record.endpoint);
            if (endpoint.isEmpty() || seenIds.contains(record.id)
                || seenEndpoints.contains(endpoint)) {
                continue;
            }
            seenIds.insert(record.id);
            seenEndpoints.insert(endpoint);
            merged.append(record);
        }
    }
    return sortReceivers(merged);
}

QVector<ReceiverRecord> loadReceiverCatalog(const QVector<QVariantList> &kiwiRows,
                                            const QVector<QVariantList> &openwebRxRows,
                                            const QVector<ReceiverRecord> &localRows,
                                            const QVector<QVariantMap> &fmdxReceivers,
                                            const QString &staticPath) {
    return mergeCatalogs({
        recordsFromKiwiDirectory(kiwiRows),
        recordsFromKiwiDirectory(openwebRxRows),
        loadStaticSources(staticPath),
        localRows,
        recordsFromFmdxDirectory(fmdxReceivers),
    });
}

QVariantMap migrateRememberedView(const QVariantMap &payload) {
    const QString endpoint = firstFalsySkipped(payload, {"server", "endpoint"}).toString().trimmed();
    const QString declared = firstFalsySkipped(payload, {"receiver_type", "protocol"}).toString();
    const QString protocol = inferProtocol(endpoint, declaredProtocol(declared));
    const QString canonical = canonicalEndpoint(endpoint);

    const QString providedId = firstFalsySkipped(payload, {"receiver_id"}).toString();
    const QString receiverId = protocol == QStringLiteral("fmdx")
                                   ? QStringLiteral("fmdx:%1").arg(canonical)
                                   : (providedId.isEmpty()
                                          ? QStringLiteral("%1:%2").arg(protocol, canonical)
                                          : providedId);

    QVariantMap migrated;
    migrated.insert(QStringLiteral("receiver_id"), receiverId);
    migrated.insert(QStringLiteral("protocol"), protocol);
    migrated.insert(QStringLiteral("endpoint"), endpoint);

    const std::optional<double> frequency = asFloat(firstPresent(payload, {"frequency_khz"}));
    if (frequency.has_value()) {
        migrated.insert(QStringLiteral("frequency_khz"), *frequency);
    }
    const QVariant mode = firstPresent(payload, {"mode"});
    if (mode.isValid() && !mode.isNull()) {
        migrated.insert(QStringLiteral("mode"), mode);
    }
    const QVariant zoom = firstPresent(payload, {"zoom"});
    if (zoom.isValid() && !zoom.isNull()) {
        migrated.insert(QStringLiteral("zoom"), zoom);
    }

    // Membership is exact, not case-folded: Python compares the raw value.
    const QString source = payload.value(QStringLiteral("source")).toString();
    migrated.insert(QStringLiteral("source"),
                    sourceFilters().contains(source) ? source : QStringLiteral("kiwi"));
    const QString view = payload.value(QStringLiteral("view")).toString();
    migrated.insert(QStringLiteral("view"),
                    (view == QStringLiteral("list") || view == QStringLiteral("map"))
                        ? view
                        : QStringLiteral("list"));
    migrated.insert(QStringLiteral("shared_control"), false);
    return migrated;
}

}  // namespace ituner::core
