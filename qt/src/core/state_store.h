// Remembered-view persistence.
//
// Ported from `load_remembered_view` and `save_remembered_view` in
// `UI/kiwi_gl_display.py`. The file path, the version-4 field names, the
// accept/reject rules per field, the atomic `.tmp` + `os.replace` write and the
// "shared FM-DX control is never restored" rule are all preserved so a CM5 can
// switch between the Python and Qt runtimes without losing its remembered
// receiver.
//
// One deliberate deviation is recorded here because it is invisible in the JSON
// shape: `QJsonDocument` stores every JSON number as a double, so this port
// cannot tell `"zoom": 13` from `"zoom": 13.0`. Python accepts the former and
// rejects the latter (`isinstance(zoom, int)`). The port accepts any integral
// zoom value instead. The value is still a valid zoom; the difference only
// shows for a hand-edited fractional zoom, and the file the application itself
// writes always uses an integer.

#pragma once

#include <QString>
#include <QVariantMap>

#include <optional>

namespace ituner::core {

/// `Path.home() / ".local/state/kiwi-gl-display-receiver.json"`.
QString rememberedViewPath();

/// Read the remembered view. Returns `nullopt` when the file is missing,
/// unreadable, malformed, or its `server` is not an http/https URL with a
/// hostname, exactly as the Python function answers `None`.
///
/// Keys when present: `server`, `receiver_type`, `freq_khz`, `zoom`,
/// `radio_mode`, `preferences`, and always `receiver_id`, `protocol`,
/// `shared_control` (false).
std::optional<QVariantMap> loadRememberedView(const QString &path);

/// Write the remembered view atomically. The server must be an http/https URL
/// with a hostname or the call is a no-op that returns false, matching the
/// Python guard. `zoom` is truncated toward zero and clamped to the display
/// maximum; `freq_khz` is rounded to three decimals.
bool saveRememberedView(const QString &path, const QString &server, double freqKhz,
                        int zoom, const std::optional<QString> &radioMode = std::nullopt,
                        bool manualRadioMode = false, const QVariantMap &preferences = {},
                        const std::optional<QString> &receiverType = std::nullopt);

/// True when the endpoint is `http`/`https` with a hostname, the same predicate
/// both functions use to accept or reject a server.
bool isRememberedEndpoint(const QString &server);

}  // namespace ituner::core
