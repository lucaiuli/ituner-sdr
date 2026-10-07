// The deterministic core of the KiwiSDR WebSocket transport.
//
// Ported from `KiwiWebSocket`, `parse_endpoint`, `websocket_redirect_endpoint`
// and `swap_s16_bytes` in `UI/kiwi_live_display_fb.py`, plus
// `next_kiwi_session_timestamp` and `stereo_s16le_to_mono` in
// `UI/kiwi_gl_display.py`. This header deliberately holds only the parts that do
// not touch a socket, a clock, or a device, so they can be verified headlessly
// against the Python originals; the live `QWebSocket` session that drives them
// is a later slice. Where a Python helper reads the wall clock or module state,
// the time or state is a parameter here.
//
// Two protocol facts are load-bearing and easy to lose:
//   * SND and W/F are a *single* Kiwi listener. Both WebSockets must carry the
//     same client-side millisecond pairing timestamp, or a receiver at capacity
//     treats the second socket as another listener and drops the waterfall.
//   * A public proxy may answer the upgrade with an HTTP 307 rather than a
//     WebSocket close, so at most two trusted absolute redirects are followed.
//     The transport must never follow a redirect loop.

#pragma once

#include <QByteArray>
#include <QString>
#include <QVariantMap>

#include <functional>
#include <optional>
#include <stdexcept>

namespace ituner::transport {

/// A parsed KiwiSDR endpoint.
struct KiwiEndpoint {
    QString scheme;
    QString host;
    int port = 0;
};

/// `parse_endpoint`: ensure a scheme, then take host and port. The default port
/// is 8073, or 443 for `https`/`wss`. Returns `nullopt` where Python raises
/// `ValueError` (no hostname, or a non-numeric port).
std::optional<KiwiEndpoint> parseEndpoint(const QString &endpoint);

/// The WebSocket scheme for an endpoint scheme: `wss` for `https`/`wss`, else
/// `ws`.
QString websocketScheme(const QString &scheme);

/// The request path for a stream: `/ws/kiwi/{timestamp}/{stream}`.
QString kiwiSessionPath(qint64 timestampMillis, const QString &streamName);

/// The `Sec-WebSocket-Accept` value the server must return for a given key:
/// base64(sha1(key + RFC 6455 GUID)).
QString websocketAcceptForKey(const QString &key);

/// `websocket_redirect_endpoint`: the trusted absolute redirect advertised by a
/// Kiwi proxy, or `nullopt`. Only 301/302/307/308 statuses are accepted, and
/// only a `Location` whose scheme is http/https/ws/wss with a hostname.
std::optional<QString> websocketRedirectEndpoint(const QByteArray &responseHeader);

/// `next_kiwi_session_timestamp`: a strictly monotonic millisecond clock.
///
/// A plain `time.time() * 1000` collides whenever several workers start in the
/// same millisecond, which makes the receiver cross-pair or drop healthy
/// streams. Each call returns `max(now, last + 1)`.
class KiwiSessionClock {
public:
    qint64 next(qint64 nowMillis);

    /// The last value returned, for diagnostics.
    qint64 last() const { return m_last; }

private:
    qint64 m_last = 0;
};

/// The process-wide session clock the workers share.
qint64 nextKiwiSessionTimestamp(qint64 nowMillis);

/// The SND/`W/F` stream names Kiwi expects.
QString kiwiSndStreamName();
QString kiwiWaterfallStreamName();

/// `KIWI_RAW_AUDIO_QUANTUM_FRAMES`: 512 PCM frames per SND quantum.
int kiwiRawAudioQuantumFrames();

/// `KIWI_SND_KEEPALIVE_SECONDS`: the once-per-second keepalive Kiwi requires.
double kiwiSndKeepaliveSeconds();

/// Bit flags in the one-byte SND header.
enum SndFlag : int {
    SndFlagStereo = 0x08,
    SndFlagCompressed = 0x10,
    SndFlagSquelchUi = 0x40,
    SndFlagLittleEndian = 0x80,
};

/// A decoded SND packet.
struct SndPacket {
    int flags = 0;
    quint32 sequence = 0;
    /// `0.1 * smeter - 127.0`, dBm.
    double smeterDbm = 0.0;
    /// The signed PCM payload after the seven-byte header, unswapped.
    QByteArray pcm;
};

/// Decode a SND message. Returns `nullopt` unless the message is at least ten
/// bytes and begins with the ASCII tag `SND`.
std::optional<SndPacket> parseSndPacket(const QByteArray &message);

/// `sndPacketPlayable`: true when the packet is uncompressed PCM this receiver
/// mode can use at the requested channel count.
///
/// `radioMode` must be the lower-case mode the Python state stores (`"iq"`,
/// `"drm"`, `"sas"`, `"qam"`, `"usb"`, ...); the Python worker reads it from a
/// state that lowercases it on the way in.
bool sndPacketPlayable(int flags, const QString &radioMode, int desiredChannels);

/// `swap_s16_bytes`: swap the two bytes of every little-endian 16-bit sample.
/// Only defined for an even length, as the Python slice assignment requires.
QByteArray swapS16Bytes(const QByteArray &data);

/// `stereo_s16le_to_mono`: downmix interleaved little-endian stereo to mono,
/// averaging each pair with Python's floor division.
QByteArray stereoS16leToMono(const QByteArray &data);

/// The radio-mode sets the SND playability predicate uses.
bool isKiwiStereoAudioMode(const QString &mode);
bool isKiwiNonAudioMode(const QString &mode);

// ---------------------------------------------------------------------------
// Transport I/O errors and the frame reader.
//
// `UI/test_kiwi_transport.py` pins these behaviours in Python, and the port keeps
// the same exception taxonomy so a caller can tell a permanent "external app
// access disabled" receiver from one that is merely busy, and can keep framing
// intact across a socket timeout.
// ---------------------------------------------------------------------------

/// The receiver rejected this listener because its allowed slots are full.
class KiwiServerBusyError : public std::runtime_error {
public:
    explicit KiwiServerBusyError(int capacity);

    int capacity() const { return m_capacity; }

protected:
    /// For a subclass that replaces the message, as `KiwiExternalApiDisabledError`
    /// does by reassigning `args`.
    KiwiServerBusyError(int capacity, const char *message);

private:
    int m_capacity;
};

/// The owner has configured this receiver to reject non-browser clients.
class KiwiExternalApiDisabledError : public KiwiServerBusyError {
public:
    KiwiExternalApiDisabledError();
};

/// A socket read timed out at a frame boundary with no bytes buffered yet; the
/// worker may safely poll again.
class RecvTimeout : public std::runtime_error {
public:
    RecvTimeout();
};

/// The peer closed the socket, or sent a WebSocket close frame.
class WebSocketClosedError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/// A frame started but did not finish inside the partial-frame budget.
class PartialFrameTimeoutError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/// A frame declared more bytes than the reader will buffer.
class WebSocketFrameTooLargeError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/// `raise_for_kiwi_server_message`: throw the precise access error a Kiwi `MSG`
/// packet represents, or return when it carries no `too_busy` field.
void raiseForKiwiServerMessage(const QVariantMap &params);

/// `WEBSOCKET_MAX_FRAME_BYTES`: 16 MiB.
int websocketMaxFrameBytes();

/// `WEBSOCKET_PARTIAL_FRAME_TIMEOUT_SECONDS`: 8 s.
double websocketPartialFrameTimeoutSeconds();

/// The byte source `recvExact` and the frame reader pull from. `read` returns an
/// empty array when the peer closed, and throws `RecvTimeout` when the read
/// times out. A real implementation wraps a QTcpSocket/QWebSocket; tests script
/// it.
class ByteSource {
public:
    virtual ~ByteSource() = default;

    virtual QByteArray read(int count) = 0;
};

/// A monotonic clock, injected so the timeout rules are testable.
using ClockFn = std::function<double()>;

/// `recv_exact`: read exactly `count` bytes without losing framing across a
/// socket timeout. A timeout with nothing buffered is re-thrown for the worker
/// to poll again; once bytes have arrived, the read either completes or fails
/// after the partial-frame budget rather than returning a short buffer.
QByteArray recvExact(ByteSource &source, int count, const ClockFn &clock);

/// One decoded data frame.
struct WebSocketFrame {
    int opcode = 0;
    QByteArray payload;
};

/// Send a frame back to the peer (used for the pong a ping requires).
using SendFrameFn = std::function<void(int opcode, const QByteArray &payload)>;

/// `KiwiWebSocket.recv`: read one data frame, transparently answering a ping and
/// skipping a pong. Returns `nullopt` when a control frame was handled and the
/// caller should read again, and throws `WebSocketClosedError` on a close frame
/// (carrying the peer's code and reason) or a dropped socket.
std::optional<WebSocketFrame> recvWebSocketFrame(ByteSource &source, const ClockFn &clock,
                                                 const SendFrameFn &send);

}  // namespace ituner::transport
