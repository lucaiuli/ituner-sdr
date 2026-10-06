// Unit and parity tests for the receiver catalog port.
//
// The unit cases mirror `UI/test_receiver_catalog.py`, which is the executable
// specification. On top of that, the parity case replays the shared fixtures in
// `qt/tests/golden/receiver_catalog_inputs.json` through this port and compares
// the result with `receiver_catalog_expected.json`, which is produced by running
// the real Python module over the same fixtures
// (`qt/tests/parity/capture_receiver_catalog.py`). Hand-written expectations can
// drift; that comparison cannot.

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QSet>
#include <QStringList>
#include <QtTest>

#include <receiver_catalog.h>

using ituner::core::ReceiverCapabilities;
using ituner::core::ReceiverRecord;

namespace {

QString sourceRoot() {
    return QString::fromLatin1(ITUNER_QT_SOURCE_ROOT);
}

QJsonObject loadObject(const QString &path) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

QJsonValue numberOrNull(const std::optional<double> &value) {
    return value.has_value() ? QJsonValue(*value) : QJsonValue(QJsonValue::Null);
}

QJsonValue numberOrNull(const std::optional<int> &value) {
    return value.has_value() ? QJsonValue(*value) : QJsonValue(QJsonValue::Null);
}

QJsonArray stringArray(const QStringList &values) {
    QJsonArray array;
    for (const QString &value : values) {
        array.append(value);
    }
    return array;
}

QString compact(const QJsonValue &value) {
    if (value.isNull()) {
        return QStringLiteral("null");
    }
    if (value.isUndefined()) {
        return QStringLiteral("undefined");
    }
    if (value.isBool()) {
        return value.toBool() ? QStringLiteral("true") : QStringLiteral("false");
    }
    if (value.isString()) {
        return QLatin1Char('"') + value.toString() + QLatin1Char('"');
    }
    if (value.isDouble()) {
        return QString::number(value.toDouble());
    }
    return QStringLiteral("(composite)");
}

/// The first structural difference between two JSON values, for a useful failure
/// message instead of a wall of text.
QString describeDifference(const QJsonValue &expected, const QJsonValue &actual,
                           const QString &path) {
    if (expected == actual) {
        return {};
    }
    if (expected.isObject() && actual.isObject()) {
        const QJsonObject expectedObject = expected.toObject();
        const QJsonObject actualObject = actual.toObject();
        for (const QString &key : expectedObject.keys()) {
            const QString difference =
                describeDifference(expectedObject.value(key), actualObject.value(key),
                                   path + QLatin1Char('.') + key);
            if (!difference.isEmpty()) {
                return difference;
            }
        }
        for (const QString &key : actualObject.keys()) {
            if (!expectedObject.contains(key)) {
                return QStringLiteral("%1.%2: unexpected key").arg(path, key);
            }
        }
        return {};
    }
    if (expected.isArray() && actual.isArray()) {
        const QJsonArray expectedArray = expected.toArray();
        const QJsonArray actualArray = actual.toArray();
        if (expectedArray.size() != actualArray.size()) {
            return QStringLiteral("%1: %2 entries expected, %3 produced")
                .arg(path)
                .arg(expectedArray.size())
                .arg(actualArray.size());
        }
        for (int index = 0; index < expectedArray.size(); ++index) {
            const QString difference =
                describeDifference(expectedArray.at(index), actualArray.at(index),
                                   QStringLiteral("%1[%2]").arg(path).arg(index));
            if (!difference.isEmpty()) {
                return difference;
            }
        }
        return {};
    }
    return QStringLiteral("%1: expected %2, produced %3").arg(path, compact(expected), compact(actual));
}

QJsonArray legacyRowArray(const ReceiverRecord &record) {
    QJsonArray array;
    for (const QVariant &item : record.legacyRow()) {
        array.append(item.isValid() && !item.isNull() ? QJsonValue::fromVariant(item)
                                                      : QJsonValue(QJsonValue::Null));
    }
    return array;
}

QStringList sortedControls(const QSet<QString> &controls) {
    QStringList result(controls.cbegin(), controls.cend());
    result.sort();
    return result;
}

QJsonArray decisionArray(const ReceiverRecord &record, const QJsonArray &specs) {
    QJsonArray decisions;
    for (const QJsonValue &spec : specs) {
        const QJsonObject entry = spec.toObject();
        const QString control = entry.value(QStringLiteral("control")).toString();
        const bool acknowledged = entry.value(QStringLiteral("shared_acknowledged")).toBool(false);
        const ituner::core::ControlDecision decision =
            record.capabilities.decide(control, acknowledged);
        QJsonObject result;
        result.insert(QStringLiteral("control"), control);
        result.insert(QStringLiteral("shared_acknowledged"), acknowledged);
        result.insert(QStringLiteral("allowed"), decision.allowed);
        result.insert(QStringLiteral("message"), decision.message);
        decisions.append(result);
    }
    return decisions;
}

QJsonArray supportArray(const ReceiverRecord &record, const QJsonArray &frequencies) {
    QJsonArray supports;
    for (const QJsonValue &frequency : frequencies) {
        QJsonObject result;
        result.insert(QStringLiteral("khz"), frequency.toDouble());
        result.insert(QStringLiteral("supported"),
                      record.capabilities.supportsFrequency(frequency.toDouble()));
        supports.append(result);
    }
    return supports;
}

/// The same view the Python capture script records.
QJsonObject recordView(const ReceiverRecord &record, const QJsonArray &decisions,
                       const QJsonArray &supports) {
    const ReceiverCapabilities &caps = record.capabilities;
    QJsonObject view;
    view.insert(QStringLiteral("id"), record.id);
    view.insert(QStringLiteral("protocol"), record.protocol);
    view.insert(QStringLiteral("source_group"), record.sourceGroup);
    view.insert(QStringLiteral("endpoint"), record.endpoint);
    view.insert(QStringLiteral("name"), record.name);
    view.insert(QStringLiteral("location"), record.location);
    view.insert(QStringLiteral("latitude"), numberOrNull(record.latitude));
    view.insert(QStringLiteral("longitude"), numberOrNull(record.longitude));
    view.insert(QStringLiteral("listeners_used"), numberOrNull(record.listenersUsed));
    view.insert(QStringLiteral("listeners_total"), numberOrNull(record.listenersTotal));
    view.insert(QStringLiteral("favorite"), record.favorite);
    view.insert(QStringLiteral("label"), caps.label);
    view.insert(QStringLiteral("waterfall_kind"), caps.waterfallKind);
    view.insert(QStringLiteral("control_scope"), caps.controlScope);
    view.insert(QStringLiteral("modes"), stringArray(caps.modes));
    view.insert(QStringLiteral("controls"), stringArray(sortedControls(caps.controls)));

    QJsonObject fixed;
    for (auto entry = caps.fixedControls.constBegin(); entry != caps.fixedControls.constEnd();
         ++entry) {
        fixed.insert(entry.key(), entry.value());
    }
    view.insert(QStringLiteral("fixed_controls"), fixed);

    QJsonArray ranges;
    for (const QPair<double, double> &range : caps.frequencyRangesKhz) {
        ranges.append(QJsonArray{range.first, range.second});
    }
    view.insert(QStringLiteral("frequency_ranges_khz"), ranges);
    view.insert(QStringLiteral("source_span_khz"), caps.sourceSpanKhz);
    view.insert(QStringLiteral("legacy_row"), legacyRowArray(record));
    view.insert(QStringLiteral("decisions"), decisions);
    view.insert(QStringLiteral("supports_frequency"), supports);

    const std::optional<QPair<double, double>> bounds = caps.tuningBounds();
    view.insert(QStringLiteral("tuning_bounds"),
                bounds.has_value() ? QJsonValue(QJsonArray{bounds->first, bounds->second})
                                   : QJsonValue(QJsonValue::Null));
    return view;
}

QStringList idsOf(const QVector<ReceiverRecord> &records) {
    QStringList ids;
    ids.reserve(records.size());
    for (const ReceiverRecord &record : records) {
        ids.append(record.id);
    }
    return ids;
}

QVector<QVariantList> rowsFromJson(const QJsonArray &rows) {
    QVector<QVariantList> result;
    result.reserve(rows.size());
    for (const QJsonValue &row : rows) {
        result.append(row.toArray().toVariantList());
    }
    return result;
}

QVector<QVariantMap> mapsFromJson(const QJsonArray &rows) {
    QVector<QVariantMap> result;
    result.reserve(rows.size());
    for (const QJsonValue &row : rows) {
        result.append(row.toObject().toVariantMap());
    }
    return result;
}

QStringList sorted(const QSet<QString> &values) {
    QStringList result(values.cbegin(), values.cend());
    result.sort();
    return result;
}

}  // namespace

class ReceiverCatalogTest : public QObject {
    Q_OBJECT

private slots:
    void sourceOrderMatchesReceiverSidebar();
    void fixedPassbandReturnsAnExplanation();
    void fmdxFrequencyIsSharedAndReadOnlyByDefault();
    void fmdxControlScopeAndWaterfallKindAreTruthful();
    void sharedFrequencyUnlocksOnlyAfterAcknowledgement();
    void unknownControlNamesItselfInTheMessage();
    void localDeviceNamesTheOwnerInItsReasons();
    void filteringAllKeepsPriorityOrder();
    void sourceGroupSelectsADifferentSegmentThanProtocol();
    void unknownSourceFilterFallsBackToAll();
    void emptyControlsListMeansNoControlsForFmdx();
    void emptyControlsListMeansDefaultsForKiwi();
    void staticSourcesSeedOpenwebrxAndLocal();
    void parityWithThePythonImplementation();
};

void ReceiverCatalogTest::sourceOrderMatchesReceiverSidebar() {
    QCOMPARE(ituner::core::sourceFilters(),
             QStringList({QStringLiteral("local"), QStringLiteral("kiwi"),
                          QStringLiteral("openwebrx"), QStringLiteral("fmdx"),
                          QStringLiteral("all")}));
    QCOMPARE(ituner::core::sourcePriority(QStringLiteral("fmdx")), 3);
    QCOMPARE(ituner::core::sourcePriority(QStringLiteral("all")), -1);
}

void ReceiverCatalogTest::fixedPassbandReturnsAnExplanation() {
    const ReceiverCapabilities caps =
        ReceiverCapabilities::fixedAudio(QStringLiteral("fmdx"), QStringLiteral("FM-DX"));
    const ituner::core::ControlDecision decision = caps.decide(QStringLiteral("passband"));
    QVERIFY(!decision.allowed);
    QCOMPARE(decision.message, QStringLiteral("Fixed passband on this receiver"));
}

void ReceiverCatalogTest::fmdxFrequencyIsSharedAndReadOnlyByDefault() {
    const ReceiverRecord record = ituner::core::normalizeReceiver(QVariantMap{
        {QStringLiteral("id"), QStringLiteral("fm:test")},
        {QStringLiteral("protocol"), QStringLiteral("fmdx")},
        {QStringLiteral("endpoint"), QStringLiteral("https://fm.test")},
        {QStringLiteral("name"), QStringLiteral("FM test")},
        {QStringLiteral("control_scope"), QStringLiteral("shared_server")},
    });
    QCOMPARE(record.capabilities.decide(QStringLiteral("frequency")).message,
             QStringLiteral("Shared tuner: changing frequency affects every listener"));
}

void ReceiverCatalogTest::fmdxControlScopeAndWaterfallKindAreTruthful() {
    const ReceiverRecord record = ituner::core::normalizeReceiver(QVariantMap{
        {QStringLiteral("id"), QStringLiteral("fm:test")},
        {QStringLiteral("protocol"), QStringLiteral("fmdx")},
        {QStringLiteral("endpoint"), QStringLiteral("https://fm.test")},
    });
    QCOMPARE(record.capabilities.controlScope, QStringLiteral("shared_server"));
    QCOMPARE(record.capabilities.waterfallKind, QStringLiteral("audio_spectrum"));
    QCOMPARE(record.capabilities.decide(QStringLiteral("mode")).message,
             QStringLiteral("FM mode is controlled by the shared receiver"));
    QCOMPARE(record.capabilities.decide(QStringLiteral("waterfall_pan")).message,
             QStringLiteral("Audio spectrum is fixed to 20 kHz"));
    QVERIFY(record.capabilities.decide(QStringLiteral("volume")).allowed);
}

void ReceiverCatalogTest::sharedFrequencyUnlocksOnlyAfterAcknowledgement() {
    const ReceiverCapabilities caps =
        ReceiverCapabilities::fixedAudio(QStringLiteral("fmdx"), QStringLiteral("FM-DX"));
    QVERIFY(!caps.decide(QStringLiteral("frequency")).allowed);
    QVERIFY(caps.decide(QStringLiteral("frequency"), true).allowed);
    // Per-session receivers never depend on the acknowledgement flag.
    QVERIFY(ReceiverCapabilities::kiwi().decide(QStringLiteral("frequency")).allowed);
}

void ReceiverCatalogTest::unknownControlNamesItselfInTheMessage() {
    const ituner::core::ControlDecision decision =
        ReceiverCapabilities::kiwi().decide(QStringLiteral("spectrum_tilt"));
    QVERIFY(!decision.allowed);
    QCOMPARE(decision.message, QStringLiteral("Spectrum Tilt is unavailable on this receiver"));
}

void ReceiverCatalogTest::localDeviceNamesTheOwnerInItsReasons() {
    const ReceiverRecord record = ituner::core::normalizeReceiver(QVariantMap{
        {QStringLiteral("endpoint"), QStringLiteral("local://rtlsdr")},
        {QStringLiteral("name"), QStringLiteral("Local RTL-SDR")},
        {QStringLiteral("label"), QStringLiteral("RTL-SDR")},
    });
    // The record itself carries no label; the override lands on the contract.
    QCOMPARE(record.capabilities.label, QStringLiteral("RTL-SDR"));
    QCOMPARE(record.capabilities.decide(QStringLiteral("passband")).message,
             QStringLiteral("Passband is set by RTL-SDR"));
    QCOMPARE(record.capabilities.decide(QStringLiteral("agc")).message,
             QStringLiteral("AGC is set by RTL-SDR"));
}

void ReceiverCatalogTest::filteringAllKeepsPriorityOrder() {
    const QVector<ReceiverRecord> records{
        ituner::core::normalizeReceiver(QVariantMap{{QStringLiteral("id"), QStringLiteral("f")},
                                                    {QStringLiteral("protocol"), QStringLiteral("fmdx")},
                                                    {QStringLiteral("endpoint"), QStringLiteral("https://fm")}}),
        ituner::core::normalizeReceiver(QVariantMap{{QStringLiteral("id"), QStringLiteral("o")},
                                                    {QStringLiteral("protocol"), QStringLiteral("openwebrx")},
                                                    {QStringLiteral("endpoint"), QStringLiteral("owrxs://owrx")}}),
        ituner::core::normalizeReceiver(QVariantMap{{QStringLiteral("id"), QStringLiteral("k")},
                                                    {QStringLiteral("protocol"), QStringLiteral("kiwi")},
                                                    {QStringLiteral("endpoint"), QStringLiteral("https://kiwi")}}),
    };
    QStringList protocols;
    for (const ReceiverRecord &record : ituner::core::filterReceivers(records, QStringLiteral("all"))) {
        protocols.append(record.protocol);
    }
    QCOMPARE(protocols, QStringList({QStringLiteral("kiwi"), QStringLiteral("openwebrx"),
                                     QStringLiteral("fmdx")}));
}

void ReceiverCatalogTest::sourceGroupSelectsADifferentSegmentThanProtocol() {
    const ReceiverRecord lanKiwi = ituner::core::normalizeReceiver(QVariantMap{
        {QStringLiteral("id"), QStringLiteral("local:kiwisdr")},
        {QStringLiteral("protocol"), QStringLiteral("kiwi")},
        {QStringLiteral("source_group"), QStringLiteral("local")},
        {QStringLiteral("endpoint"), QStringLiteral("http://kiwisdr.local:8073")},
        {QStringLiteral("name"), QStringLiteral("Local KiwiSDR")},
    });
    QCOMPARE(lanKiwi.protocol, QStringLiteral("kiwi"));
    QCOMPARE(lanKiwi.sourceGroup, QStringLiteral("local"));
    QCOMPARE(ituner::core::filterReceivers({lanKiwi}, QStringLiteral("local")).size(), 1);
    QCOMPARE(ituner::core::filterReceivers({lanKiwi}, QStringLiteral("kiwi")).size(), 0);
    QCOMPARE(ituner::core::filterReceivers({lanKiwi}, QStringLiteral("all")).size(), 1);

    // A segment that is not a browser segment falls back to the protocol default.
    const ReceiverRecord bogus = ituner::core::normalizeReceiver(QVariantMap{
        {QStringLiteral("protocol"), QStringLiteral("kiwi")},
        {QStringLiteral("source_group"), QStringLiteral("bogus")},
        {QStringLiteral("endpoint"), QStringLiteral("https://kiwi-bogus")},
    });
    QCOMPARE(bogus.sourceGroup, QStringLiteral("kiwi"));
}

void ReceiverCatalogTest::unknownSourceFilterFallsBackToAll() {
    const QVector<ReceiverRecord> records{ituner::core::normalizeReceiver(
        QVariantMap{{QStringLiteral("id"), QStringLiteral("k")},
                    {QStringLiteral("protocol"), QStringLiteral("kiwi")},
                    {QStringLiteral("endpoint"), QStringLiteral("https://kiwi")}})};
    QCOMPARE(idsOf(ituner::core::filterReceivers(records, QStringLiteral("nope"))),
             idsOf(ituner::core::filterReceivers(records, QStringLiteral("all"))));
    // The browser lower-cases the segment before matching.
    QCOMPARE(ituner::core::filterReceivers(records, QStringLiteral("KIWI")).size(), 1);
}

void ReceiverCatalogTest::emptyControlsListMeansNoControlsForFmdx() {
    const ReceiverRecord record = ituner::core::normalizeReceiver(QVariantMap{
        {QStringLiteral("protocol"), QStringLiteral("fmdx")},
        {QStringLiteral("endpoint"), QStringLiteral("https://fm.test3")},
        {QStringLiteral("controls"), QVariantList{}},
    });
    QVERIFY(record.capabilities.controls.isEmpty());
    QCOMPARE(record.capabilities.decide(QStringLiteral("volume")).message,
             QStringLiteral("Volume is unavailable on this receiver"));
}

void ReceiverCatalogTest::emptyControlsListMeansDefaultsForKiwi() {
    const ReceiverRecord record = ituner::core::normalizeReceiver(QVariantMap{
        {QStringLiteral("protocol"), QStringLiteral("kiwi")},
        {QStringLiteral("endpoint"), QStringLiteral("https://kiwi")},
        {QStringLiteral("controls"), QVariantList{}},
    });
    QVERIFY(record.capabilities.decide(QStringLiteral("frequency")).allowed);
    QVERIFY(record.capabilities.decide(QStringLiteral("volume")).allowed);
}

void ReceiverCatalogTest::staticSourcesSeedOpenwebrxAndLocal() {
    const QVector<ReceiverRecord> records =
        ituner::core::loadStaticSources(sourceRoot() + QStringLiteral("/../UI/receiver_sources.json"));
    QVERIFY(!records.isEmpty());

    QSet<QString> protocols;
    QSet<QString> groups;
    for (const ReceiverRecord &record : records) {
        protocols.insert(record.protocol);
        groups.insert(record.sourceGroup);
    }
    QCOMPARE(sorted(protocols), QStringList({QStringLiteral("kiwi"), QStringLiteral("openwebrx")}));
    QCOMPARE(sorted(groups), QStringList({QStringLiteral("local"), QStringLiteral("openwebrx")}));

    const QVector<ReceiverRecord> loaded = ituner::core::loadReceiverCatalog(
        QVector<QVariantList>{QVariantList{QStringLiteral("Kiwi A"), QStringLiteral("Somewhere"),
                                           QStringLiteral("http://kiwi.test:8073"), 1, 4, 1.0, 2.0,
                                           QStringLiteral("kiwi")}},
        {}, {},
        QVector<QVariantMap>{QVariantMap{{QStringLiteral("name"), QStringLiteral("FM A")},
                                         {QStringLiteral("location"), QStringLiteral("FM land")},
                                         {QStringLiteral("server"), QStringLiteral("https://fm.test")},
                                         {QStringLiteral("lat"), 1},
                                         {QStringLiteral("lon"), 2}}},
        sourceRoot() + QStringLiteral("/../UI/receiver_sources.json"));

    QSet<QString> loadedProtocols;
    QSet<QString> loadedGroups;
    for (const ReceiverRecord &record : loaded) {
        loadedProtocols.insert(record.protocol);
        loadedGroups.insert(record.sourceGroup);
    }
    QCOMPARE(sorted(loadedProtocols),
             QStringList({QStringLiteral("fmdx"), QStringLiteral("kiwi"),
                          QStringLiteral("openwebrx")}));
    QCOMPARE(sorted(loadedGroups),
             QStringList({QStringLiteral("fmdx"), QStringLiteral("kiwi"),
                          QStringLiteral("local"), QStringLiteral("openwebrx")}));
}

void ReceiverCatalogTest::parityWithThePythonImplementation() {
    const QJsonObject inputs =
        loadObject(sourceRoot() + QStringLiteral("/tests/golden/receiver_catalog_inputs.json"));
    const QJsonObject expected =
        loadObject(sourceRoot() + QStringLiteral("/tests/golden/receiver_catalog_expected.json"));
    QVERIFY2(!inputs.isEmpty(), "the parity fixtures are missing");
    QVERIFY2(!expected.isEmpty(),
             "the parity goldens are missing; run qt/tests/parity/capture_receiver_catalog.py");

    const QJsonArray recordSpecs = inputs.value(QStringLiteral("records")).toArray();
    QVector<ReceiverRecord> records;
    QJsonArray views;
    for (const QJsonValue &spec : recordSpecs) {
        const QJsonObject entry = spec.toObject();
        const ReceiverRecord record =
            ituner::core::normalizeReceiver(entry.value(QStringLiteral("input")).toObject().toVariantMap());
        records.append(record);
        views.append(recordView(record, decisionArray(record, entry.value(QStringLiteral("decisions")).toArray()),
                                supportArray(record, entry.value(QStringLiteral("supports_frequency_khz")).toArray())));
    }

    const QJsonArray expectedViews = expected.value(QStringLiteral("records")).toArray();
    QCOMPARE(views.size(), expectedViews.size());
    for (int index = 0; index < views.size(); ++index) {
        const QString difference = describeDifference(expectedViews.at(index), views.at(index),
                                                      QStringLiteral("records[%1]").arg(index));
        QVERIFY2(difference.isEmpty(), qPrintable(difference));
    }

    // Browser segments, in every filter the fixtures name.
    const QJsonObject expectedFilters = expected.value(QStringLiteral("filters")).toObject();
    const QJsonArray filterNames = inputs.value(QStringLiteral("filters")).toArray();
    for (const QJsonValue &name : filterNames) {
        const QString filter = name.toString();
        QCOMPARE(stringArray(idsOf(ituner::core::filterReceivers(records, filter))),
                 expectedFilters.value(filter).toArray());
    }

    // Directory adapters.
    const QVector<ReceiverRecord> kiwiRecords = ituner::core::recordsFromKiwiDirectory(
        rowsFromJson(inputs.value(QStringLiteral("kiwi_rows")).toArray()));
    QCOMPARE(stringArray(idsOf(kiwiRecords)),
             expected.value(QStringLiteral("kiwi_directory")).toArray());

    const QVector<ReceiverRecord> fmdxRecords = ituner::core::recordsFromFmdxDirectory(
        mapsFromJson(inputs.value(QStringLiteral("fmdx_rows")).toArray()));
    QCOMPARE(stringArray(idsOf(fmdxRecords)),
             expected.value(QStringLiteral("fmdx_directory")).toArray());

    // Merge, including deduplication.
    QCOMPARE(stringArray(idsOf(ituner::core::mergeCatalogs({kiwiRecords, fmdxRecords, records}))),
             expected.value(QStringLiteral("merged")).toArray());

    // Remembered-view migration.
    const QJsonArray expectedMigrated = expected.value(QStringLiteral("migrated")).toArray();
    const QJsonArray migratePayloads = inputs.value(QStringLiteral("migrate_payloads")).toArray();
    QCOMPARE(expectedMigrated.size(), migratePayloads.size());
    for (int index = 0; index < migratePayloads.size(); ++index) {
        const QVariantMap produced =
            ituner::core::migrateRememberedView(migratePayloads.at(index).toObject().toVariantMap());
        const QString difference = describeDifference(expectedMigrated.at(index),
                                                      QJsonObject::fromVariantMap(produced),
                                                      QStringLiteral("migrated[%1]").arg(index));
        QVERIFY2(difference.isEmpty(), qPrintable(difference));
    }
}

QTEST_MAIN(ReceiverCatalogTest)
#include "tst_receiver_catalog.moc"
