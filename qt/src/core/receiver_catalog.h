// Protocol-neutral receiver records and the one shared receiver catalog.
//
// A direct port of the catalog half of `UI/receiver_catalog.py`. A record keeps
// the transport (`protocol`) separate from the browser segment (`sourceGroup`),
// so a LAN Kiwi is `protocol=kiwi` with `sourceGroup=local` and never enters the
// USB local-device worker.
//
// Raw input rows arrive as `QVariantMap`, the Qt equivalent of the Python
// module's `Mapping`, so a directory row, a cache row and a JSON object all take
// the same path.

#pragma once

#include <QList>
#include <QString>
#include <QStringList>
#include <QVariantList>
#include <QVariantMap>
#include <QVector>

#include <optional>

#include "receiver_capabilities.h"

namespace ituner::core {

/// One receiver, independent of how it is rendered or probed.
struct ReceiverRecord {
    QString id;
    QString protocol;
    QString sourceGroup;
    QString endpoint;
    QString name;
    QString location;
    std::optional<double> latitude;
    std::optional<double> longitude;
    ReceiverCapabilities capabilities;
    std::optional<int> listenersUsed;
    std::optional<int> listenersTotal;
    bool favorite = false;

    /// The legacy station tuple shape, preserved during migration.
    QVariantList legacyRow() const;
};

/// Cheap, dependency-free endpoint canonicalization for stable ids.
QString canonicalEndpoint(const QString &endpoint);

/// Pick a transport for a record that does not declare one.
QString inferProtocol(const QString &endpoint,
                      const std::optional<QString> &declared = std::nullopt);

/// Identity overload: an already-normalized record is returned unchanged.
ReceiverRecord normalizeReceiver(const ReceiverRecord &record);

/// Coerce a raw mapping (directory row, cache row, static source) to a record.
ReceiverRecord normalizeReceiver(const QVariantMap &record);

/// Adapt legacy Kiwi directory tuples into records.
QVector<ReceiverRecord> recordsFromKiwiDirectory(const QVector<QVariantList> &rows);

/// Adapt the FM-DX directory's receiver dicts into records.
QVector<ReceiverRecord> recordsFromFmdxDirectory(const QVector<QVariantMap> &receivers);

/// Load the built-in OpenWebRX/local metadata schema.
///
/// `path` is required: the Python module resolves `receiver_sources.json`
/// relative to itself, and this port deliberately takes the location from the
/// caller so the Qt runtime and the tests can point at the real file rather than
/// guessing an install layout. Pass `ITUNER_SDR_STATIC_SOURCES` to
/// `staticSourcesPath()` to share one default between them.
QVector<ReceiverRecord> loadStaticSources(const QString &path);

/// The environment variable that names the static source file, if set.
QString staticSourcesPath();

/// Stable priority order: LAN, Kiwi, OpenWebRX, FM-DX.
QVector<ReceiverRecord> sortReceivers(const QVector<ReceiverRecord> &records);

/// Return one browser segment's records, always priority-sorted. An unknown
/// segment falls back to `all`.
QVector<ReceiverRecord> filterReceivers(const QVector<ReceiverRecord> &records,
                                       const QString &source = QStringLiteral("all"));

/// Merge every input into one deduplicated, priority-sorted catalog.
QVector<ReceiverRecord> mergeCatalogs(const QList<QVector<ReceiverRecord>> &groups);

/// Build the one shared receiver catalog from every input adapter.
QVector<ReceiverRecord> loadReceiverCatalog(const QVector<QVariantList> &kiwiRows = {},
                                            const QVector<QVariantList> &openwebRxRows = {},
                                            const QVector<ReceiverRecord> &localRows = {},
                                            const QVector<QVariantMap> &fmdxReceivers = {},
                                            const QString &staticPath = {});

/// Upgrade a remembered receiver view to the stable-id schema.
///
/// Legacy `receiver_type` keeps loading for one migration cycle. FM-DX
/// shared-control acknowledgement is never restored: the migrated view always
/// starts read-only.
QVariantMap migrateRememberedView(const QVariantMap &payload);

}  // namespace ituner::core
