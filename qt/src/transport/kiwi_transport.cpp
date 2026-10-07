#include "kiwi_transport.h"

#include <QCryptographicHash>
#include <QList>
#include <QtEndian>
#include <QUrl>
#include <QVariant>

#include <algorithm>
#include <mutex>
#include <string>

namespace ituner::transport {
namespace {

constexpr const char *kWebSocketGuid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

}  // namespace

std::optional<KiwiEndpoint> parseEndpoint(const QString &endpoint) {
    QString candidate = endpoint;
    // Python prepends `http://` whenever the endpoint carries no scheme at all.
    if (!candidate.contains(QStringLiteral("://"))) {
        candidate = QStringLiteral("http://") + candidate;
    }

    const QUrl url(candidate, QUrl::StrictMode);
    if (!url.isValid()) {
        return std::nullopt;
    }

    QString scheme = url.scheme();
    if (scheme.isEmpty()) {
        scheme = QStringLiteral("http");
    }
    const QString host = url.host();
    if (host.isEmpty()) {
        return std::nullopt;
    }

    int port = url.port(-1);
    if (port == -1) {
        port = (scheme == QStringLiteral("https") || scheme == QStringLiteral("wss")) ? 443 : 8073;
    }
    if (port < 1 || port > 65535) {
        return std::nullopt;
    }
    return KiwiEndpoint{scheme, host, port};
}

QString websocketScheme(const QString &scheme) {
    return (scheme == QStringLiteral("https") || scheme == QStringLiteral("wss"))
               ? QStringLiteral("wss")
               : QStringLiteral("ws");
}

QString kiwiSessionPath(qint64 timestampMillis, const QString &streamName) {
    return QStringLiteral("/ws/kiwi/%1/%2").arg(timestampMillis).arg(streamName);
}

QString websocketAcceptForKey(const QString &key) {
    const QByteArray digest =
        QCryptographicHash::hash((key + QString::fromLatin1(kWebSocketGuid)).toUtf8(),
                                 QCryptographicHash::Sha1);
    return QString::fromLatin1(digest.toBase64());
}

std::optional<QString> websocketRedirectEndpoint(const QByteArray &responseHeader) {
    const int firstBreak = responseHeader.indexOf("\r\n");
    const QByteArray status =
        firstBreak < 0 ? responseHeader : responseHeader.left(firstBreak);
    const bool redirectStatus = status.contains(" 301 ") || status.contains(" 302 ")
                                || status.contains(" 307 ") || status.contains(" 308 ");
    if (!redirectStatus) {
        return std::nullopt;
    }

    int lineStart = firstBreak < 0 ? responseHeader.size() : firstBreak + 2;
    while (lineStart < responseHeader.size()) {
        int lineEnd = responseHeader.indexOf("\r\n", lineStart);
        if (lineEnd < 0) {
            lineEnd = responseHeader.size();
        }
        const QByteArray line = responseHeader.mid(lineStart, lineEnd - lineStart);
        lineStart = lineEnd + 2;

        if (line.toLower().startsWith("location:")) {
            const int colon = line.indexOf(':');
            const QByteArray value = line.mid(colon + 1).trimmed();
            const QString candidate = QString::fromLatin1(value);
            const QUrl url(candidate);
            const QString scheme = url.scheme().toLower();
            const bool knownScheme = scheme == QStringLiteral("http")
                                     || scheme == QStringLiteral("https")
                                     || scheme == QStringLiteral("ws")
                                     || scheme == QStringLiteral("wss");
            if (knownScheme && !url.host().isEmpty()) {
                return candidate;
            }
        }
    }
    return std::nullopt;
}

qint64 KiwiSessionClock::next(qint64 nowMillis) {
    m_last = std::max(nowMillis, m_last + 1);
    return m_last;
}

qint64 nextKiwiSessionTimestamp(qint64 nowMillis) {
    static KiwiSessionClock clock;
    static std::mutex mutex;
    const std::lock_guard<std::mutex> lock(mutex);
    return clock.next(nowMillis);
}

QString kiwiSndStreamName() {
    return QStringLiteral("SND");
}

QString kiwiWaterfallStreamName() {
    return QStringLiteral("W/F");
}

int kiwiRawAudioQuantumFrames() {
    return 512;
}

double kiwiSndKeepaliveSeconds() {
    return 1.0;
}

std::optional<SndPacket> parseSndPacket(const QByteArray &message) {
    if (message.size() < 10 || !message.startsWith("SND")) {
        return std::nullopt;
    }
    const QByteArray body = message.mid(3);
    const auto *raw = reinterpret_cast<const uchar *>(body.constData());

    SndPacket packet;
    packet.flags = static_cast<int>(raw[0]);
    packet.sequence = qFromLittleEndian<quint32>(raw + 1);
    packet.smeterDbm = 0.1 * static_cast<double>(qFromBigEndian<quint16>(raw + 5)) - 127.0;
    packet.pcm = body.mid(7);
    return packet;
}

bool isKiwiStereoAudioMode(const QString &mode) {
    return mode == QStringLiteral("sas") || mode == QStringLiteral("qam");
}

bool isKiwiNonAudioMode(const QString &mode) {
    return mode == QStringLiteral("iq") || mode == QStringLiteral("drm");
}

bool sndPacketPlayable(int flags, const QString &radioMode, int desiredChannels) {
    if (flags & SndFlagCompressed) {
        return false;
    }
    const bool stereo = (flags & SndFlagStereo) != 0;
    if (stereo) {
        return isKiwiStereoAudioMode(radioMode);
    }
    return !isKiwiNonAudioMode(radioMode) && desiredChannels == 1;
}

QByteArray swapS16Bytes(const QByteArray &data) {
    // Python assigns `out[0::2] = data[1::2]` and vice versa, which needs an
    // even length; SND PCM always is. An odd trailing byte is carried through
    // unchanged rather than dropped.
    QByteArray out = data;
    const int pairs = data.size() / 2;
    char *destination = out.data();
    const char *source = data.constData();
    for (int index = 0; index < pairs; ++index) {
        destination[index * 2] = source[index * 2 + 1];
        destination[index * 2 + 1] = source[index * 2];
    }
    return out;
}

KiwiServerBusyError::KiwiServerBusyError(int capacity)
    : std::runtime_error("receiver busy (external app capacity " + std::to_string(capacity) + ")"),
      m_capacity(capacity) {}

KiwiServerBusyError::KiwiServerBusyError(int capacity, const char *message)
    : std::runtime_error(message), m_capacity(capacity) {}

KiwiExternalApiDisabledError::KiwiExternalApiDisabledError()
    : KiwiServerBusyError(0, "external app access disabled by receiver") {}

RecvTimeout::RecvTimeout()
    : std::runtime_error("socket timeout") {}

void raiseForKiwiServerMessage(const QVariantMap &params) {
    if (!params.contains(QStringLiteral("too_busy"))) {
        return;
    }
    bool converted = false;
    const int parsed = params.value(QStringLiteral("too_busy")).toInt(&converted);
    // Python answers -1 when `int()` rejects the value, then still raises busy.
    const int capacity = converted ? parsed : -1;
    if (capacity == 0) {
        throw KiwiExternalApiDisabledError();
    }
    throw KiwiServerBusyError(capacity);
}

int websocketMaxFrameBytes() {
    return 16 * 1024 * 1024;
}

double websocketPartialFrameTimeoutSeconds() {
    return 8.0;
}

QByteArray recvExact(ByteSource &source, int count, const ClockFn &clock) {
    QByteArray data;
    const double startedAt = clock();
    while (data.size() < count) {
        try {
            const QByteArray chunk = source.read(count - data.size());
            if (chunk.isEmpty()) {
                throw WebSocketClosedError("socket closed");
            }
            data += chunk;
        } catch (const RecvTimeout &) {
            // At a frame boundary the worker may safely poll again. Once any
            // bytes have arrived, returning would lose framing permanently: the
            // next read would mistake payload bytes for a new header.
            if (data.isEmpty()) {
                throw;
            }
            if (clock() - startedAt >= websocketPartialFrameTimeoutSeconds()) {
                throw PartialFrameTimeoutError("websocket partial frame timed out");
            }
        }
    }
    return data;
}

std::optional<WebSocketFrame> recvWebSocketFrame(ByteSource &source, const ClockFn &clock,
                                                 const SendFrameFn &send) {
    while (true) {
        const QByteArray header = recvExact(source, 2, clock);
        if (header.isEmpty()) {
            throw WebSocketClosedError("websocket closed");
        }
        const auto first = static_cast<quint8>(header.at(0));
        const auto second = static_cast<quint8>(header.at(1));
        const int opcode = first & 0x0F;
        const bool masked = (second & 0x80) != 0;
        quint64 length = second & 0x7F;
        if (length == 126) {
            const QByteArray extended = recvExact(source, 2, clock);
            length = qFromBigEndian<quint16>(reinterpret_cast<const uchar *>(extended.constData()));
        } else if (length == 127) {
            const QByteArray extended = recvExact(source, 8, clock);
            length = qFromBigEndian<quint64>(reinterpret_cast<const uchar *>(extended.constData()));
        }
        // Refuse an oversized frame before reading its mask or payload.
        if (length > static_cast<quint64>(websocketMaxFrameBytes())) {
            throw WebSocketFrameTooLargeError("websocket frame too large: " + std::to_string(length)
                                              + " bytes");
        }

        QByteArray mask;
        if (masked) {
            mask = recvExact(source, 4, clock);
        }
        QByteArray payload;
        if (length > 0) {
            payload = recvExact(source, static_cast<int>(length), clock);
        }
        if (masked) {
            for (int index = 0; index < payload.size(); ++index) {
                payload[index] = static_cast<char>(static_cast<quint8>(payload.at(index))
                                                   ^ static_cast<quint8>(mask.at(index % 4)));
            }
        }

        if (opcode == 0x8) {
            // Keep the peer's close information; a public Kiwi's client/slot
            // policy otherwise looks identical to a generic transport failure.
            int closeCode = -1;
            QString closeReason;
            if (payload.size() >= 2) {
                closeCode = qFromBigEndian<quint16>(reinterpret_cast<const uchar *>(payload.constData()));
                closeReason = QString::fromUtf8(payload.mid(2)).trimmed();
            }
            QString detail = QStringLiteral("websocket closed");
            if (closeCode >= 0) {
                detail += QStringLiteral(" code=%1").arg(closeCode);
            }
            if (!closeReason.isEmpty()) {
                detail += QStringLiteral(" reason=%1").arg(closeReason.left(120));
            }
            throw WebSocketClosedError(detail.toStdString());
        }
        if (opcode == 0x9) {
            send(0xA, payload);
            continue;
        }
        if (opcode == 0xA) {
            continue;
        }
        return WebSocketFrame{opcode, payload};
    }
}

QByteArray stereoS16leToMono(const QByteArray &data) {
    const int frames = data.size() / 4;
    if (frames <= 0) {
        return {};
    }

    QByteArray out;
    out.resize(frames * 2);
    const auto *source = reinterpret_cast<const uchar *>(data.constData());
    auto *destination = reinterpret_cast<uchar *>(out.data());
    for (int index = 0; index < frames; ++index) {
        const int left = static_cast<qint16>(qFromLittleEndian<qint16>(source + index * 4));
        const int right = static_cast<qint16>(qFromLittleEndian<qint16>(source + index * 4 + 2));
        // Python's `//` floors toward negative infinity; an arithmetic shift
        // does the same, where integer division would truncate toward zero.
        const auto mono = static_cast<qint16>((left + right) >> 1);
        qToLittleEndian<qint16>(mono, destination + index * 2);
    }
    return out;
}

}  // namespace ituner::transport
