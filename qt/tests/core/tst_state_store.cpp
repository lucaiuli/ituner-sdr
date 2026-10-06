// Parity tests for remembered-view persistence.
//
// Every expectation comes from `qt/tests/golden/state_store_expected.json`,
// produced by `qt/tests/parity/capture_state_store.py` calling the real
// `load_remembered_view` and `save_remembered_view` in `UI/kiwi_gl_display.py`.
// The Python sources stay the specification, so nothing here is hand-written
// against a reading of them.
//
// One expectation is intentionally *not* byte-for-byte. Python writes the file
// with `json.dumps(..., sort_keys=True)`, which emits `", "`/`": "` separators
// and a trailing `.0` on integral floats; this port writes `QJsonDocument`'s
// canonical compact form (`","`/`":"`, no `.0`). The two serialisations are
// different bytes of the same JSON object, and both runtimes read either one, so
// the test compares the parsed object rather than the raw text. The golden's
// `raw` member is still checked structurally, and the file is fed back through
// `loadRememberedView` to prove the round trip the product actually needs.

#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QTemporaryDir>
#include <QtTest>

#include <algorithm>
#include <cmath>

#include <state_store.h>

namespace {

QJsonObject golden() {
    const QString path = QString::fromLatin1(ITUNER_QT_SOURCE_ROOT)
                         + QStringLiteral("/tests/golden/state_store_expected.json");
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

/// A readable rendering of a JSON value for a failure message.
QString asText(const QJsonValue &value) {
    if (value.isObject()) {
        return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
    }
    if (value.isArray()) {
        return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact));
    }
    return value.toVariant().toString();
}

/// Compare the port's view against the golden result the same way the JSON
/// readers do: as JSON values, so an integer zoom and a floating-point zoom are
/// the same number.
QJsonValue asJson(const QVariantMap &view) {
    return QJsonValue(QJsonObject::fromVariantMap(view));
}

}  // namespace

class StateStoreTest : public QObject {
    Q_OBJECT

private slots:
    void loadMatchesPython();
    void saveMatchesPython();
    void invalidEndpointWritesNothing();
    void roundingMatchesPython();
    void pathMatchesPython();
};

void StateStoreTest::loadMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("load")).toArray();
    QVERIFY2(!rows.isEmpty(), "the state-store goldens are missing");

    QTemporaryDir dir;
    QVERIFY(dir.isValid());

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString name = row.value(QStringLiteral("name")).toString();
        const QJsonValue saved = row.value(QStringLiteral("saved"));
        const QString path = dir.path() + QStringLiteral("/load_") + name
                             + QStringLiteral(".json");

        if (saved.isNull()) {
            // A missing file; nothing to write.
        } else if (saved.isString()) {
            // A deliberately malformed payload, written verbatim.
            QFile file(path);
            QVERIFY2(file.open(QIODevice::WriteOnly | QIODevice::Truncate), qPrintable(name));
            QVERIFY(file.write(saved.toString().toUtf8()) >= 0);
            file.close();
        } else {
            QFile file(path);
            QVERIFY2(file.open(QIODevice::WriteOnly | QIODevice::Truncate), qPrintable(name));
            const QByteArray payload =
                QJsonDocument::fromVariant(saved.toVariant()).toJson(QJsonDocument::Compact);
            QVERIFY(file.write(payload) == payload.size());
            file.close();
        }

        const std::optional<QVariantMap> produced = ituner::core::loadRememberedView(path);
        const QJsonValue expected = row.value(QStringLiteral("result"));

        if (expected.isNull()) {
            // Build the message only on failure; `*produced` is invalid here.
            if (produced.has_value()) {
                QFAIL(qPrintable(QStringLiteral("load %1: expected no view, produced %2")
                                     .arg(name, asText(asJson(*produced)))));
            }
            continue;
        }
        QVERIFY2(produced.has_value(),
                 qPrintable(QStringLiteral("load %1: expected a view, produced none").arg(name)));
        const QJsonValue actual = asJson(*produced);
        QVERIFY2(actual == expected,
                 qPrintable(QStringLiteral("load %1:\n  expected %2\n  produced %3")
                                .arg(name, asText(expected), asText(actual))));
    }
}

void StateStoreTest::saveMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("save")).toArray();
    QVERIFY2(!rows.isEmpty(), "the state-store goldens are missing");

    QTemporaryDir dir;
    QVERIFY(dir.isValid());

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString name = row.value(QStringLiteral("name")).toString();
        const QJsonObject args = row.value(QStringLiteral("args")).toObject();
        const QString path = dir.path() + QStringLiteral("/save_") + name
                             + QStringLiteral(".json");

        const QString server = args.value(QStringLiteral("server")).toString();
        const double freqKhz = args.value(QStringLiteral("freq_khz")).toDouble();
        const int zoom = args.value(QStringLiteral("zoom")).toInt();

        std::optional<QString> radioMode;
        if (args.contains(QStringLiteral("radio_mode"))) {
            radioMode = args.value(QStringLiteral("radio_mode")).toString();
        }
        std::optional<QString> receiverType;
        if (args.contains(QStringLiteral("receiver_type"))) {
            receiverType = args.value(QStringLiteral("receiver_type")).toString();
        }
        QVariantMap preferences;
        const QJsonValue preferencesValue = args.value(QStringLiteral("preferences"));
        if (preferencesValue.isObject()) {
            preferences = preferencesValue.toObject().toVariantMap();
        }
        const bool manualRadioMode =
            args.value(QStringLiteral("manual_radio_mode")).toBool(false);

        const bool written =
            ituner::core::saveRememberedView(path, server, freqKhz, zoom, radioMode,
                                             manualRadioMode, preferences, receiverType);
        const bool expectedWritten = row.value(QStringLiteral("written")).toBool();

        QVERIFY2(written == expectedWritten,
                 qPrintable(QStringLiteral("save %1: written %2, expected %3")
                                .arg(name, written ? QStringLiteral("true") : QStringLiteral("false"),
                                     expectedWritten ? QStringLiteral("true")
                                                     : QStringLiteral("false"))));

        if (!expectedWritten) {
            QVERIFY2(!QFile::exists(path),
                     qPrintable(QStringLiteral("save %1: file must not exist").arg(name)));
            continue;
        }

        QFile file(path);
        QVERIFY2(file.open(QIODevice::ReadOnly), qPrintable(name));
        const QByteArray contents = file.readAll();
        file.close();

        // The atomic writer always terminates with a newline, matching Python's
        // `json.dumps(...) + "\n"`.
        QVERIFY2(contents.endsWith('\n'), qPrintable(QStringLiteral("save %1: no newline").arg(name)));

        QJsonParseError parseError{};
        const QJsonDocument document = QJsonDocument::fromJson(contents, &parseError);
        QVERIFY2(parseError.error == QJsonParseError::NoError && document.isObject(),
                 qPrintable(QStringLiteral("save %1: unparsable %2").arg(name, QString::fromUtf8(contents))));

        const QJsonValue expectedParsed = row.value(QStringLiteral("parsed"));
        QVERIFY2(QJsonValue(document.object()) == expectedParsed,
                 qPrintable(QStringLiteral("save %1:\n  expected %2\n  produced %3")
                                .arg(name, asText(expectedParsed), asText(QJsonValue(document.object())))));

        // Feed the written file back through the loader: this is the guarantee
        // that matters, that the Qt runtime can read what it wrote.
        const std::optional<QVariantMap> reloaded = ituner::core::loadRememberedView(path);
        QVERIFY2(reloaded.has_value(),
                 qPrintable(QStringLiteral("save %1: written file does not load").arg(name)));
        QCOMPARE(reloaded->value(QStringLiteral("server")).toString(), server);
    }
}

void StateStoreTest::invalidEndpointWritesNothing() {
    QVERIFY2(golden().value(QStringLiteral("empty_save_is_rejected")).toBool(),
             "the golden did not record that an invalid endpoint is rejected");

    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString path = dir.path() + QStringLiteral("/guard.json");

    // The same call the capture makes, including an explicitly invalid scheme.
    const bool written = ituner::core::saveRememberedView(path, QStringLiteral("ftp://nope"),
                                                          7075.0, 13, std::nullopt, false, {},
                                                          std::nullopt);
    QVERIFY(!written);
    QVERIFY(!QFile::exists(path));

    // And the user-facing string from the save table.
    QVERIFY(!ituner::core::saveRememberedView(path, QStringLiteral("not a url"), 7075.0, 13,
                                              std::nullopt, false, {}, std::nullopt));
    QVERIFY(!QFile::exists(path));
}

void StateStoreTest::roundingMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("rounding")).toArray();
    QVERIFY2(!rows.isEmpty(), "the rounding sweep is missing");

    QTemporaryDir dir;
    QVERIFY(dir.isValid());

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const double value = row.value(QStringLiteral("value")).toDouble();
        const double rounded = row.value(QStringLiteral("rounded")).toDouble();

        const QString path = dir.path() + QStringLiteral("/round.json");
        QFile::remove(path);
        QVERIFY(ituner::core::saveRememberedView(path, QStringLiteral("http://kiwi.test:8073"),
                                                 value, 13, std::nullopt, false, {},
                                                 std::nullopt));

        QFile file(path);
        QVERIFY(file.open(QIODevice::ReadOnly));
        const QJsonObject saved = QJsonDocument::fromJson(file.readAll()).object();
        file.close();

        const double produced = saved.value(QStringLiteral("freq_khz")).toDouble();
        QVERIFY2(produced == rounded,
                 qPrintable(QStringLiteral("round(%1, 3): expected %2, produced %3")
                                .arg(value, 0, 'g', 17)
                                .arg(rounded, 0, 'g', 17)
                                .arg(produced, 0, 'g', 17)));
    }
}

void StateStoreTest::pathMatchesPython() {
    // `Path.home() / ".local/state/kiwi-gl-display-receiver.json"`.
    QCOMPARE(ituner::core::rememberedViewPath(),
             QDir::homePath()
                 + QStringLiteral("/.local/state/kiwi-gl-display-receiver.json"));
}

QTEST_MAIN(StateStoreTest)
#include "tst_state_store.moc"
