// Parity tests for the KiwiSDR transport protocol helpers.
//
// Every expectation comes from `qt/tests/golden/kiwi_transport_expected.json`,
// produced by `qt/tests/parity/capture_kiwi_transport.py` calling the real
// `parse_endpoint`, `websocket_redirect_endpoint`, `swap_s16_bytes`,
// `next_kiwi_session_timestamp` and `stereo_s16le_to_mono` in the Python
// renderer, or restating the renderer's inline SND decode. The Python sources
// stay the specification, so nothing here is hand-written against a reading of
// them.

#include <QByteArray>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QtTest>
#include <QVariantMap>

#include <algorithm>
#include <cmath>
#include <functional>
#include <memory>
#include <stdexcept>

#include <kiwi_transport.h>
#include <live_state.h>

using ituner::transport::ByteSource;
using ituner::transport::KiwiEndpoint;
using ituner::transport::KiwiSessionClock;
using ituner::transport::SendFrameFn;
using ituner::transport::SndPacket;

namespace {

QJsonObject golden() {
    const QString path = QString::fromLatin1(ITUNER_QT_SOURCE_ROOT)
                         + QStringLiteral("/tests/golden/kiwi_transport_expected.json");
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        return {};
    }
    return QJsonDocument::fromJson(file.readAll()).object();
}

QByteArray fromHex(const QString &hex) {
    return QByteArray::fromHex(hex.toLatin1());
}

bool closeEnough(double produced, double expected) {
    const double scale = std::max(1.0, std::max(std::abs(produced), std::abs(expected)));
    return std::abs(produced - expected) <= 1e-9 * scale;
}

/// One scripted read: either bytes (an empty array is a closed socket) or a
/// timeout.
struct ScriptEvent {
    bool timeout = false;
    QByteArray data;
};

/// Replays a scripted byte sequence, matching `UI/test_kiwi_transport.py`'s
/// `ScriptedSocket`.
class ScriptedSource : public ByteSource {
public:
    explicit ScriptedSource(QList<ScriptEvent> events) : m_events(std::move(events)) {}

    QByteArray read(int) override {
        if (m_events.isEmpty()) {
            throw std::runtime_error("scripted source exhausted");
        }
        const ScriptEvent event = m_events.takeFirst();
        if (event.timeout) {
            throw ituner::transport::RecvTimeout();
        }
        return event.data;
    }

private:
    QList<ScriptEvent> m_events;
};

/// A clock that walks a fixed list, then holds its last value.
ituner::transport::ClockFn scriptedClock(QList<double> values) {
    auto state = std::make_shared<std::pair<QList<double>, int>>(std::move(values), 0);
    return [state]() -> double {
        const int last = static_cast<int>(state->first.size()) - 1;
        const double value = state->first.at(std::min(state->second, last));
        ++state->second;
        return value;
    };
}

struct Outcome {
    QString type;  // empty on success
    QString message;
    QByteArray value;
    int capacity = 0;
    bool hasCapacity = false;
};

/// Run a call and describe its result or the exact exception type it threw.
Outcome capture(const std::function<QByteArray()> &call) {
    Outcome out;
    try {
        out.value = call();
    } catch (const ituner::transport::KiwiExternalApiDisabledError &error) {
        out.type = QStringLiteral("KiwiExternalApiDisabledError");
        out.message = QString::fromStdString(error.what());
        out.capacity = error.capacity();
        out.hasCapacity = true;
    } catch (const ituner::transport::KiwiServerBusyError &error) {
        out.type = QStringLiteral("KiwiServerBusyError");
        out.message = QString::fromStdString(error.what());
        out.capacity = error.capacity();
        out.hasCapacity = true;
    } catch (const ituner::transport::PartialFrameTimeoutError &error) {
        out.type = QStringLiteral("PartialFrameTimeoutError");
        out.message = QString::fromStdString(error.what());
    } catch (const ituner::transport::WebSocketFrameTooLargeError &error) {
        out.type = QStringLiteral("WebSocketFrameTooLargeError");
        out.message = QString::fromStdString(error.what());
    } catch (const ituner::transport::WebSocketClosedError &error) {
        out.type = QStringLiteral("WebSocketClosedError");
        out.message = QString::fromStdString(error.what());
    } catch (const ituner::transport::RecvTimeout &error) {
        out.type = QStringLiteral("RecvTimeout");
        out.message = QString::fromStdString(error.what());
    } catch (const std::exception &error) {
        // A harness or unexpected error: report it rather than aborting the run.
        out.type = QStringLiteral("std::exception");
        out.message = QString::fromStdString(error.what());
    }
    return out;
}

/// Map a Python exception name (plus message) onto the port's exception type.
QString translatedType(const QString &pythonName, const QString &message) {
    if (pythonName == QStringLiteral("TimeoutError")) {
        return message.isEmpty() ? QStringLiteral("RecvTimeout")
                                 : QStringLiteral("PartialFrameTimeoutError");
    }
    if (pythonName == QStringLiteral("EOFError")) {
        return QStringLiteral("WebSocketClosedError");
    }
    if (pythonName == QStringLiteral("ValueError")) {
        return QStringLiteral("WebSocketFrameTooLargeError");
    }
    return pythonName;
}

QString jsonText(const QJsonValue &value) {
    if (value.isArray()) {
        return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact));
    }
    if (value.isObject()) {
        return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
    }
    if (value.isNull()) {
        return QStringLiteral("null");
    }
    return value.toVariant().toString();
}

QList<ScriptEvent> eventsFromJson(const QJsonArray &events) {
    QList<ScriptEvent> result;
    for (const QJsonValue &entry : events) {
        ScriptEvent event;
        if (entry.isObject()) {
            const QJsonObject object = entry.toObject();
            event.timeout = object.value(QStringLiteral("timeout")).toBool();
            event.data = fromHex(object.value(QStringLiteral("hex")).toString());
        } else {
            event.data = fromHex(entry.toString());
        }
        result.append(event);
    }
    return result;
}

}  // namespace

class KiwiTransportTest : public QObject {
    Q_OBJECT

private slots:
    void endpointParsingMatchesPython();
    void redirectCaptureMatchesPython();
    void sessionPathMatchesPython();
    void acceptKeyMatchesPython();
    void pairingTimestampMatchesPython();
    void sndDecodeMatchesPython();
    void swapBytesMatchesPython();
    void stereoDownmixMatchesPython();
    void playabilityMatchesPython();
    void constantsMatchPython();
    void serverBusyMessagesMatchPython();
    void recvExactMatchesPython();
    void frameReaderMatchesPython();
    void liveStateCommitProtocolMatchesPython();
};

void KiwiTransportTest::endpointParsingMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("endpoints")).toArray();
    QVERIFY2(!rows.isEmpty(), "the transport goldens are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString input = row.value(QStringLiteral("input")).toString();
        const std::optional<KiwiEndpoint> produced =
            ituner::transport::parseEndpoint(input);

        if (!row.value(QStringLiteral("ok")).toBool()) {
            QVERIFY2(!produced.has_value(),
                     qPrintable(QStringLiteral("parseEndpoint(%1) should have failed").arg(input)));
            continue;
        }
        QVERIFY2(produced.has_value(),
                 qPrintable(QStringLiteral("parseEndpoint(%1) should have succeeded").arg(input)));
        QCOMPARE(produced->scheme, row.value(QStringLiteral("scheme")).toString());
        QCOMPARE(produced->host, row.value(QStringLiteral("host")).toString());
        QCOMPARE(produced->port, row.value(QStringLiteral("port")).toInt());
    }
}

void KiwiTransportTest::redirectCaptureMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("redirects")).toArray();
    QVERIFY2(!rows.isEmpty(), "the redirect rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString name = row.value(QStringLiteral("name")).toString();
        const QByteArray response = row.value(QStringLiteral("response")).toString().toLatin1();
        const std::optional<QString> produced =
            ituner::transport::websocketRedirectEndpoint(response);
        const QJsonValue expected = row.value(QStringLiteral("redirect"));

        if (expected.isNull()) {
            QVERIFY2(!produced.has_value(),
                     qPrintable(QStringLiteral("redirect %1: expected none, produced %2")
                                    .arg(name, produced.value_or(QStringLiteral("(none)")))));
            continue;
        }
        QVERIFY2(produced.has_value(),
                 qPrintable(QStringLiteral("redirect %1: expected %2, produced none")
                                .arg(name, expected.toString())));
        QCOMPARE(*produced, expected.toString());
    }
}

void KiwiTransportTest::sessionPathMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("session_paths")).toArray();
    QVERIFY2(!rows.isEmpty(), "the session paths are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        QCOMPARE(ituner::transport::kiwiSessionPath(
                     static_cast<qint64>(row.value(QStringLiteral("timestamp")).toDouble()),
                     row.value(QStringLiteral("stream")).toString()),
                 row.value(QStringLiteral("path")).toString());
    }

    QCOMPARE(ituner::transport::kiwiSndStreamName(), QStringLiteral("SND"));
    QCOMPARE(ituner::transport::kiwiWaterfallStreamName(), QStringLiteral("W/F"));
    // `wss` for https/wss, `ws` otherwise.
    QCOMPARE(ituner::transport::websocketScheme(QStringLiteral("https")), QStringLiteral("wss"));
    QCOMPARE(ituner::transport::websocketScheme(QStringLiteral("wss")), QStringLiteral("wss"));
    QCOMPARE(ituner::transport::websocketScheme(QStringLiteral("http")), QStringLiteral("ws"));
    QCOMPARE(ituner::transport::websocketScheme(QStringLiteral("ws")), QStringLiteral("ws"));
}

void KiwiTransportTest::acceptKeyMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("accept_keys")).toArray();
    QVERIFY2(!rows.isEmpty(), "the accept keys are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        QCOMPARE(ituner::transport::websocketAcceptForKey(
                     row.value(QStringLiteral("key")).toString()),
                 row.value(QStringLiteral("accept")).toString());
    }
}

void KiwiTransportTest::pairingTimestampMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("timestamps")).toArray();
    QVERIFY2(!rows.isEmpty(), "the timestamp sequence is missing");

    KiwiSessionClock clock;
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const qint64 now = static_cast<qint64>(row.value(QStringLiteral("now_ms")).toDouble());
        const qint64 expected =
            static_cast<qint64>(row.value(QStringLiteral("result")).toDouble());
        const qint64 produced = clock.next(now);
        QVERIFY2(produced == expected,
                 qPrintable(QStringLiteral("clock(%1): expected %2, produced %3")
                                .arg(now)
                                .arg(expected)
                                .arg(produced)));
    }
}

void KiwiTransportTest::sndDecodeMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("snd")).toArray();
    QVERIFY2(!rows.isEmpty(), "the SND rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString name = row.value(QStringLiteral("name")).toString();
        const QByteArray message = fromHex(row.value(QStringLiteral("message_hex")).toString());
        const std::optional<SndPacket> produced = ituner::transport::parseSndPacket(message);

        if (!row.value(QStringLiteral("ok")).toBool()) {
            QVERIFY2(!produced.has_value(),
                     qPrintable(QStringLiteral("SND %1 should have been rejected").arg(name)));
            continue;
        }
        QVERIFY2(produced.has_value(),
                 qPrintable(QStringLiteral("SND %1 should have decoded").arg(name)));
        QCOMPARE(produced->flags, row.value(QStringLiteral("flags")).toInt());
        QCOMPARE(produced->sequence,
                 static_cast<quint32>(row.value(QStringLiteral("sequence")).toDouble()));
        QVERIFY2(closeEnough(produced->smeterDbm,
                             row.value(QStringLiteral("smeter_dbm")).toDouble()),
                 qPrintable(QStringLiteral("SND %1 smeter: expected %2, produced %3")
                                .arg(name)
                                .arg(row.value(QStringLiteral("smeter_dbm")).toDouble())
                                .arg(produced->smeterDbm)));
        QCOMPARE(produced->pcm, fromHex(row.value(QStringLiteral("pcm_hex")).toString()));
    }
}

void KiwiTransportTest::swapBytesMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("swap")).toArray();
    QVERIFY2(!rows.isEmpty(), "the swap rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        QCOMPARE(ituner::transport::swapS16Bytes(
                     fromHex(row.value(QStringLiteral("in_hex")).toString())),
                 fromHex(row.value(QStringLiteral("out_hex")).toString()));
    }
}

void KiwiTransportTest::stereoDownmixMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("stereo_mono")).toArray();
    QVERIFY2(!rows.isEmpty(), "the stereo downmix rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        QCOMPARE(ituner::transport::stereoS16leToMono(
                     fromHex(row.value(QStringLiteral("in_hex")).toString())),
                 fromHex(row.value(QStringLiteral("out_hex")).toString()));
    }
}

void KiwiTransportTest::playabilityMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("playable")).toArray();
    QVERIFY2(!rows.isEmpty(), "the playability rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const int flags = row.value(QStringLiteral("flags")).toInt();
        const QString mode = row.value(QStringLiteral("radio_mode")).toString();
        const int channels = row.value(QStringLiteral("channels")).toInt();
        const bool produced = ituner::transport::sndPacketPlayable(flags, mode, channels);
        QVERIFY2(produced == row.value(QStringLiteral("playable")).toBool(),
                 qPrintable(QStringLiteral("playable(flags=%1, mode=%2, ch=%3): expected %4")
                                .arg(flags)
                                .arg(mode)
                                .arg(channels)
                                .arg(row.value(QStringLiteral("playable")).toBool())));
    }
}

void KiwiTransportTest::constantsMatchPython() {
    const QJsonObject constants = golden().value(QStringLiteral("constants")).toObject();
    QCOMPARE(ituner::transport::kiwiRawAudioQuantumFrames(),
             constants.value(QStringLiteral("quantum_frames")).toInt());
    QCOMPARE(ituner::transport::kiwiSndKeepaliveSeconds(),
             constants.value(QStringLiteral("keepalive_seconds")).toDouble());
    QCOMPARE(ituner::transport::websocketMaxFrameBytes(), 16 * 1024 * 1024);
    QCOMPARE(ituner::transport::websocketPartialFrameTimeoutSeconds(), 8.0);
}

void KiwiTransportTest::serverBusyMessagesMatchPython() {
    const QJsonArray rows = golden().value(QStringLiteral("busy")).toArray();
    QVERIFY2(!rows.isEmpty(), "the server-busy rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QVariantMap params = row.value(QStringLiteral("params")).toObject().toVariantMap();
        const Outcome produced = capture([&params]() {
            ituner::transport::raiseForKiwiServerMessage(params);
            return QByteArray();
        });
        const QJsonValue expectedError = row.value(QStringLiteral("error"));

        if (expectedError.isUndefined()) {
            QVERIFY2(produced.type.isEmpty(),
                     qPrintable(QStringLiteral("expected no error, got %1").arg(produced.type)));
            continue;
        }
        QCOMPARE(produced.type, expectedError.toString());
        QCOMPARE(produced.message, row.value(QStringLiteral("message")).toString());
        if (row.contains(QStringLiteral("capacity"))) {
            QVERIFY(produced.hasCapacity);
            QCOMPARE(produced.capacity, row.value(QStringLiteral("capacity")).toInt());
        }
    }
}

void KiwiTransportTest::recvExactMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("recv_exact")).toArray();
    QVERIFY2(!rows.isEmpty(), "the recv_exact rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString name = row.value(QStringLiteral("name")).toString();
        ScriptedSource source(eventsFromJson(row.value(QStringLiteral("events")).toArray()));
        QList<double> clockValues;
        for (const QJsonValue &tick : row.value(QStringLiteral("clock")).toArray()) {
            clockValues.append(tick.toDouble());
        }
        const auto clock = scriptedClock(clockValues);
        const int count = row.value(QStringLiteral("count")).toInt();

        const Outcome produced =
            capture([&source, &clock, count]() { return ituner::transport::recvExact(source, count, clock); });

        if (row.contains(QStringLiteral("ok_hex"))) {
            QVERIFY2(produced.type.isEmpty(),
                     qPrintable(QStringLiteral("recv_exact %1: unexpected %2 (%3)")
                                    .arg(name, produced.type, produced.message)));
            QCOMPARE(produced.value, fromHex(row.value(QStringLiteral("ok_hex")).toString()));
            continue;
        }
        const QString expectedType = translatedType(
            row.value(QStringLiteral("error")).toString(),
            row.value(QStringLiteral("message")).toString());
        QVERIFY2(produced.type == expectedType,
                 qPrintable(QStringLiteral("recv_exact %1: expected %2, got %3")
                                .arg(name, expectedType, produced.type)));
        // `RecvTimeout` is a bare poll signal; the Python message is empty.
        if (expectedType != QStringLiteral("RecvTimeout")) {
            QCOMPARE(produced.message, row.value(QStringLiteral("message")).toString());
        }
    }
}

void KiwiTransportTest::frameReaderMatchesPython() {
    const QJsonArray rows = golden().value(QStringLiteral("frames")).toArray();
    QVERIFY2(!rows.isEmpty(), "the frame rows are missing");

    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString name = row.value(QStringLiteral("name")).toString();
        ScriptedSource source(eventsFromJson(row.value(QStringLiteral("events")).toArray()));
        const auto clock = scriptedClock({0.0});
        int sentCount = 0;
        const SendFrameFn send = [&sentCount](int, const QByteArray &) { ++sentCount; };

        const Outcome produced = capture([&source, &clock, &send]() {
            const auto frame = ituner::transport::recvWebSocketFrame(source, clock, send);
            return frame ? frame->payload : QByteArray();
        });

        if (row.contains(QStringLiteral("ok_hex"))) {
            QVERIFY2(produced.type.isEmpty(),
                     qPrintable(QStringLiteral("frame %1: unexpected %2").arg(name, produced.type)));
            QCOMPARE(produced.value, fromHex(row.value(QStringLiteral("ok_hex")).toString()));
        } else {
            const QString expectedType = translatedType(
                row.value(QStringLiteral("error")).toString(),
                row.value(QStringLiteral("message")).toString());
            QVERIFY2(produced.type == expectedType,
                     qPrintable(QStringLiteral("frame %1: expected %2, got %3")
                                    .arg(name, expectedType, produced.type)));
            QCOMPARE(produced.message, row.value(QStringLiteral("message")).toString());
        }
        QCOMPARE(sentCount, row.value(QStringLiteral("sent_count")).toInt());
    }
}

void KiwiTransportTest::liveStateCommitProtocolMatchesPython() {
    const QJsonObject root = golden();
    const QJsonArray rows = root.value(QStringLiteral("live_state")).toArray();
    QVERIFY2(!rows.isEmpty(), "the live-state rows are missing");
    QCOMPARE(ituner::transport::LiveState::maxZoom(),
             root.value(QStringLiteral("live_state_max_zoom")).toInt());

    ituner::transport::LiveState state(QStringLiteral("http://init.test:8073"), 7000.0, 13,
                                       -110.0);
    for (const QJsonValue &entry : rows) {
        const QJsonObject row = entry.toObject();
        const QString op = row.value(QStringLiteral("op")).toString();
        const QJsonObject args = row.value(QStringLiteral("args")).toObject();

        QJsonValue observed;
        if (op == QStringLiteral("set_freq")) {
            observed = state.setFrequency(args.value(QStringLiteral("freq")).toDouble());
        } else if (op == QStringLiteral("preview_freq")) {
            state.previewFrequency(args.value(QStringLiteral("freq")).toDouble());
            observed = QJsonValue();
        } else if (op == QStringLiteral("get_tune")) {
            const auto value = state.tune(args.value(QStringLiteral("seen")).toInt());
            QJsonArray result;
            result.append(state.tuneGeneration());
            result.append(value ? QJsonValue(*value) : QJsonValue());
            observed = result;
        } else if (op == QStringLiteral("set_zoom")) {
            observed = state.setZoom(args.value(QStringLiteral("zoom")).toInt());
        } else if (op == QStringLiteral("set_freq_zoom")) {
            observed = state.setFrequencyZoom(args.value(QStringLiteral("freq")).toDouble(),
                                              args.value(QStringLiteral("zoom")).toInt());
        } else if (op == QStringLiteral("get_view")) {
            const auto value = state.view(args.value(QStringLiteral("seen")).toInt());
            QJsonArray result;
            result.append(state.viewGeneration());
            if (value) {
                QJsonArray pair;
                pair.append(value->first);
                pair.append(value->second);
                result.append(pair);
            } else {
                result.append(QJsonValue());
            }
            observed = result;
        } else if (op == QStringLiteral("set_server")) {
            std::optional<int> zoom;
            if (args.contains(QStringLiteral("zoom"))) {
                zoom = args.value(QStringLiteral("zoom")).toInt();
            }
            observed = state.setServer(args.value(QStringLiteral("server")).toString(), zoom);
        } else if (op == QStringLiteral("get_server")) {
            const auto value = state.server(args.value(QStringLiteral("seen")).toInt());
            QJsonArray result;
            result.append(state.serverGeneration());
            result.append(value ? QJsonValue(*value) : QJsonValue());
            observed = result;
        } else if (op == QStringLiteral("get_zoom")) {
            observed = state.zoom();
        } else if (op == QStringLiteral("get_span")) {
            observed = state.spanKhz();
        } else if (op == QStringLiteral("current_server")) {
            observed = state.currentServer();
        } else {
            QFAIL(qPrintable(QStringLiteral("unknown op %1").arg(op)));
        }

        const QJsonValue expected = row.value(QStringLiteral("result"));
        if (observed != expected) {
            QFAIL(qPrintable(QStringLiteral("live_state %1(%2): expected %3, produced %4")
                                 .arg(op, jsonText(args), jsonText(expected), jsonText(observed))));
        }
        QCOMPARE(state.tuneGeneration(), row.value(QStringLiteral("tune_gen")).toInt());
        QCOMPARE(state.viewGeneration(), row.value(QStringLiteral("view_gen")).toInt());
        QCOMPARE(state.serverGeneration(), row.value(QStringLiteral("server_gen")).toInt());
    }
}

QTEST_MAIN(KiwiTransportTest)
#include "tst_kiwi_transport.moc"
