"""FM-DX Webserver directory and transport helpers.

The FM-DX ecosystem is not a KiwiSDR protocol variant.  Its public directory
is JSON and each receiver exposes independent ``/text``, ``/rds`` and
``/audio`` WebSockets.  Keeping that boundary in this module prevents the
OpenGL renderer from teaching its Kiwi client about an unrelated protocol.
"""

import base64
import hashlib
import json
import math
import os
import re
import socket
import ssl
import struct
import threading
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DIRECTORY_URL = "https://servers.fmdx.org/api/"
DEFAULT_MIN_KHZ = 64_000.0
DEFAULT_MAX_KHZ = 108_000.0
DEFAULT_FREQUENCY_KHZ = 100_000.0
AVAILABLE_STATUS = 1
TUNE_INTERVAL_SECONDS = 0.125
RDS_DISCOVERY_DWELL_SECONDS = 1.4
RDS_DISCOVERY_MIN_LOCK_SECONDS = 0.35
RDS_DISCOVERY_MAX_PRESETS = 32
RDS_SCAN_STEP_KHZ = 100.0
MODE_LABEL = "FM-FMDX"
AUDIO_SAMPLE_RATE = 48_000
AUDIO_WATERFALL_SPAN_HZ = 20_000
FMDX_MARKER_COLOR = (255, 154, 61, 245)

_receivers_by_url = {}


def normalize_server_url(value):
    """Return a stable HTTP(S) receiver URL or ``None`` for unsafe input."""
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    path = parsed.path.rstrip("/")
    return parsed._replace(path=path, params="", query="", fragment="").geturl()


def parse_frequency_bounds(value):
    """Parse the directory's human-readable MHz limit into a kHz pair."""
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))]
    if len(numbers) < 2:
        return DEFAULT_MIN_KHZ, DEFAULT_MAX_KHZ
    low, high = sorted(numbers[:2])
    low_khz, high_khz = low * 1000.0, high * 1000.0
    if low_khz < 0.0 or high_khz <= low_khz or high_khz > 2_000_000.0:
        return DEFAULT_MIN_KHZ, DEFAULT_MAX_KHZ
    return low_khz, high_khz


def normalize_directory(payload, available_only=True):
    """Validate an FM-DX API response and return renderer-ready receivers."""
    dataset = payload.get("dataset") if isinstance(payload, dict) else None
    if not isinstance(dataset, list):
        raise ValueError("FM-DX directory response has no dataset")
    receivers = []
    seen = set()
    for item in dataset:
        if not isinstance(item, dict):
            continue
        server = normalize_server_url(item.get("url"))
        if not server or server in seen:
            continue
        try:
            status = int(item.get("status", 0))
        except (TypeError, ValueError):
            status = 0
        if available_only and status != AVAILABLE_STATUS:
            continue
        coords = item.get("coords")
        try:
            lat, lon = float(coords[0]), float(coords[1])
        except (IndexError, TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        minimum_khz, maximum_khz = parse_frequency_bounds(item.get("bwLimit"))
        name = str(item.get("name") or "FM-DX Webserver").strip()
        country = str(item.get("countryName") or item.get("country") or "").strip()
        city = str(item.get("city") or "").strip()
        location = ", ".join(part for part in (city, country) if part) or "Public FM-DX receiver"
        receiver = {
            "name": name,
            "location": location,
            "server": server,
            "lat": lat,
            "lon": lon,
            "used": 0,
            "total": 0,
            "receiver_type": "fmdx",
            "status": status,
            "tuner": str(item.get("tuner") or "").strip(),
            "version": str(item.get("version") or "").strip(),
            "audio_quality": str(item.get("audioQuality") or "").strip(),
            "audio_channels": _positive_int(item.get("audioChannels"), 2),
            "minimum_khz": minimum_khz,
            "maximum_khz": maximum_khz,
        }
        receivers.append(receiver)
        seen.add(server)
    return receivers


def _positive_int(value, fallback):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def register_receivers(receivers):
    """Publish receiver metadata for the long-lived transport workers."""
    _receivers_by_url.clear()
    for receiver in receivers:
        server = normalize_server_url(receiver.get("server"))
        if server:
            _receivers_by_url[server] = receiver


def ensure_receiver(server, receiver_type="fmdx"):
    """Retain a remembered FM-DX endpoint even without a directory cache."""
    server = normalize_server_url(server)
    if receiver_type != "fmdx" or not server or server in _receivers_by_url:
        return
    _receivers_by_url[server] = {
        "name": "Remembered FM-DX receiver",
        "location": "Previously selected receiver",
        "server": server,
        "receiver_type": "fmdx",
        "minimum_khz": DEFAULT_MIN_KHZ,
        "maximum_khz": DEFAULT_MAX_KHZ,
    }


def receiver_metadata(server):
    return _receivers_by_url.get(normalize_server_url(server))


def is_fmdx_server(server):
    return receiver_metadata(server) is not None


def receiver_mode(server, fallback):
    """Expose FM-DX as its own mode without leaking a Kiwi demodulator."""
    return MODE_LABEL if is_fmdx_server(server) else str(fallback).upper()


def audio_waterfall_span_khz(zoom, maximum_zoom=16):
    """Map the shared zoom control onto the fixed 20 kHz audio source."""
    zoom = max(0, min(int(maximum_zoom), int(zoom)))
    return (AUDIO_WATERFALL_SPAN_HZ / 1000.0) * (2.0 ** (-zoom / 4.0))


def audio_waterfall_drag_center_khz(start_frequency_khz, target_frequency_khz, visible_span_khz):
    """Return a safe temporary pan for carrier-centred FM-DX drag feedback."""
    source_span = AUDIO_WATERFALL_SPAN_HZ / 1000.0
    visible_span = max(0.001, min(source_span, float(visible_span_khz)))
    maximum_pan = max(0.0, (source_span - visible_span) / 2.0)
    delta = float(target_frequency_khz) - float(start_frequency_khz)
    return max(-maximum_pan, min(maximum_pan, delta))


def http_endpoint_url(server, endpoint):
    server = normalize_server_url(server)
    if not server:
        raise ValueError("invalid FM-DX receiver URL")
    parsed = urlparse(server)
    path = parsed.path.rstrip("/") + "/" + str(endpoint).strip("/")
    return parsed._replace(path=path, params="", query="", fragment="").geturl()


def normalize_station_presets(payload):
    """Normalize an FM-DX ``/static_data`` preset list."""
    values = payload.get("presets") if isinstance(payload, dict) else payload
    if not isinstance(values, (list, tuple)):
        return ()
    stations = []
    for value in values:
        item = value if isinstance(value, dict) else {"frequency": value}
        raw_frequency = item.get("frequency_khz", item.get("frequency", item.get("freq")))
        try:
            frequency = float(raw_frequency)
        except (TypeError, ValueError):
            continue
        frequency_khz = frequency * 1000.0 if frequency < 1000.0 else frequency
        if not (DEFAULT_MIN_KHZ <= frequency_khz <= DEFAULT_MAX_KHZ):
            continue
        name = str(item.get("name") or item.get("ps") or "").strip()
        pi = str(item.get("pi") or "").strip().upper()
        stations.append({
            "frequency_khz": round(frequency_khz, 1),
            "name": name,
            "pi": pi,
        })
    return tuple(stations)


def merge_station_presets(*groups):
    """Merge station rows by frequency, preferring named RDS observations."""
    merged = {}
    for group in groups:
        for station in group or ():
            try:
                frequency_khz = round(float(station["frequency_khz"]), 1)
            except (KeyError, TypeError, ValueError):
                continue
            previous = merged.get(frequency_khz, {})
            name = str(station.get("name") or "").strip()
            pi = str(station.get("pi") or "").strip().upper()
            merged[frequency_khz] = {
                "frequency_khz": frequency_khz,
                "name": name or previous.get("name", ""),
                "pi": pi or previous.get("pi", ""),
            }
    return tuple(merged[key] for key in sorted(merged))


def nearest_station_frequency(stations, frequency_khz):
    """Choose the nearest known RDS station, falling back to a server preset."""
    normalized = normalize_station_presets(stations)
    if not normalized:
        return None
    rds_stations = tuple(
        station for station in normalized
        if station.get("name") or station.get("pi")
    )
    candidates = rds_stations or normalized
    target = float(frequency_khz)
    return min(
        candidates,
        key=lambda station: (
            abs(float(station["frequency_khz"]) - target),
            float(station["frequency_khz"]),
        ),
    )["frequency_khz"]


def rds_discovery_frequencies(stations, frequency_khz, limit=RDS_DISCOVERY_MAX_PRESETS):
    """Order unnamed server presets nearest-first for a courteous RDS pass."""
    normalized = normalize_station_presets(stations)
    target = float(frequency_khz)
    unnamed = (
        station for station in normalized
        if not station.get("name") and not station.get("pi")
    )
    ordered = sorted(
        unnamed,
        key=lambda station: (
            abs(float(station["frequency_khz"]) - target),
            float(station["frequency_khz"]),
        ),
    )
    return tuple(float(station["frequency_khz"]) for station in ordered[:max(0, int(limit))])


def band_scan_frequencies(minimum_khz, maximum_khz, step_khz=RDS_SCAN_STEP_KHZ):
    """Return every complete FM channel step inside a receiver's band limits."""
    step_khz = max(1.0, float(step_khz))
    minimum_khz, maximum_khz = sorted((float(minimum_khz), float(maximum_khz)))
    first = math.ceil(minimum_khz / step_khz) * step_khz
    count = max(0, int(math.floor((maximum_khz - first) / step_khz)) + 1)
    return tuple(round(first + index * step_khz, 1) for index in range(count))


def playback_pcm(pcm, muted=False, scan_active=False):
    """Silence only an explicit mute or an operator-started band scan."""
    return bytes(len(pcm)) if muted or scan_active else pcm


def status_frequency_khz(payload):
    """Read the tuner-reported frequency from an FM-DX status message."""
    if not isinstance(payload, dict):
        return None
    try:
        frequency = float(payload.get("freq"))
    except (TypeError, ValueError):
        return None
    frequency_khz = frequency * 1000.0 if frequency < 1000.0 else frequency
    return frequency_khz if DEFAULT_MIN_KHZ <= frequency_khz <= DEFAULT_MAX_KHZ else None


def status_matches_frequency(payload, expected_frequency_khz, tolerance_khz=25.0):
    frequency_khz = status_frequency_khz(payload)
    return (
        frequency_khz is not None
        and abs(frequency_khz - float(expected_frequency_khz)) <= float(tolerance_khz)
    )


def station_from_status(payload, fallback_frequency_khz):
    """Build one learned preset from a valid live RDS programme-service name."""
    if not isinstance(payload, dict):
        return None
    name = " ".join(str(payload.get("ps") or "").split()).strip(" -_")
    if not name:
        return None
    try:
        frequency = float(payload.get("freq", fallback_frequency_khz))
    except (TypeError, ValueError):
        frequency = float(fallback_frequency_khz)
    frequency_khz = frequency * 1000.0 if frequency < 1000.0 else frequency
    if not (DEFAULT_MIN_KHZ <= frequency_khz <= DEFAULT_MAX_KHZ):
        frequency_khz = float(fallback_frequency_khz)
    return {
        "frequency_khz": round(frequency_khz, 1),
        "name": name,
        "pi": str(payload.get("pi") or "").strip().upper(),
    }


def fetch_station_presets(server, timeout=5.0):
    """Fetch the public, owner-configured station presets for one receiver."""
    request = Request(http_endpoint_url(server, "static_data"), headers={"User-Agent": "iTuner-SDR/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    return normalize_station_presets(payload)


def load_station_cache(path):
    try:
        payload = json.loads(Path(path).read_text())
    except (OSError, TypeError, ValueError):
        return {}
    servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {
        server: normalize_station_presets(stations)
        for raw_server, stations in servers.items()
        if (server := normalize_server_url(raw_server))
    }


def save_station_cache(path, cache):
    path = Path(path)
    payload = {"version": 1, "servers": cache}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
    os.replace(temporary, path)


def audio_scope_samples(mono_pcm, bins=256):
    """Downsample signed 16-bit mono PCM into a normalized scope trace."""
    sample_count = len(mono_pcm) // 2
    bins = max(1, int(bins))
    if sample_count <= 0:
        return ()
    samples = struct.unpack(f"<{sample_count}h", mono_pcm[:sample_count * 2])
    values = []
    bin_count = min(bins, sample_count)
    for index in range(bin_count):
        start = index * sample_count // bin_count
        end = max(start + 1, (index + 1) * sample_count // bin_count)
        values.append(sum(samples[start:end]) / (len(samples[start:end]) * 32768.0))
    return tuple(values)


def resample_mono_s16le(mono_pcm, source_rate, target_rate):
    """Nearest-neighbour PCM conversion for the low-rate speech-analysis copy."""
    source_rate = max(1, int(source_rate))
    target_rate = max(1, int(target_rate))
    sample_count = len(mono_pcm) // 2
    if sample_count <= 0 or source_rate == target_rate:
        return mono_pcm[:sample_count * 2]
    samples = struct.unpack(f"<{sample_count}h", mono_pcm[:sample_count * 2])
    output_count = max(1, sample_count * target_rate // source_rate)
    output = (
        samples[min(sample_count - 1, index * source_rate // target_rate)]
        for index in range(output_count)
    )
    return struct.pack(f"<{output_count}h", *output)


class AudioWaterfallAnalyzer:
    """Turn programme audio into carrier-centred rolling FFT rows."""

    def __init__(
        self,
        sample_rate=AUDIO_SAMPLE_RATE,
        span_hz=AUDIO_WATERFALL_SPAN_HZ,
        fft_size=2048,
        bins=256,
        floor_db=-90.0,
        ceiling_db=-20.0,
    ):
        self.sample_rate = max(1, int(sample_rate))
        self.span_hz = min(float(span_hz), float(self.sample_rate))
        self.maximum_hz = self.span_hz / 2.0
        self.fft_size = max(64, 1 << (int(fft_size) - 1).bit_length())
        self.bins = max(1, int(bins))
        self.floor_db = float(floor_db)
        self.ceiling_db = max(self.floor_db + 1.0, float(ceiling_db))
        self.samples = []
        self.window = tuple(
            0.5 - 0.5 * math.cos(2.0 * math.pi * index / (self.fft_size - 1))
            for index in range(self.fft_size)
        )

    def reset(self):
        """Drop partial PCM so a retune cannot blend two stations in one row."""
        self.samples.clear()

    def feed(self, mono_pcm):
        sample_count = len(mono_pcm) // 2
        if sample_count <= 0:
            return ()
        self.samples.extend(struct.unpack(f"<{sample_count}h", mono_pcm[:sample_count * 2]))
        rows = []
        while len(self.samples) >= self.fft_size:
            frame = self.samples[:self.fft_size]
            del self.samples[:self.fft_size]
            rows.append(self._row(frame))
        return tuple(rows)

    def _row(self, frame):
        spectrum = [complex(sample * self.window[index], 0.0) for index, sample in enumerate(frame)]
        self._fft(spectrum)
        maximum_bin = min(
            self.fft_size // 2,
            max(1, int(self.maximum_hz * self.fft_size / self.sample_rate)),
        )
        magnitudes = [abs(value) / (self.fft_size * 32768.0) for value in spectrum[:maximum_bin + 1]]
        positive_bins = self.bins // 2 if self.bins % 2 == 0 else self.bins // 2 + 1
        positive = []
        scale = 255.0 / (self.ceiling_db - self.floor_db)
        for index in range(positive_bins):
            start = index * len(magnitudes) // positive_bins
            end = max(start + 1, (index + 1) * len(magnitudes) // positive_bins)
            magnitude = max(magnitudes[start:end])
            db = 20.0 * math.log10(max(1e-9, magnitude))
            positive.append(max(0, min(255, round((db - self.floor_db) * scale))))
        if self.bins % 2:
            centered = list(reversed(positive[1:])) + positive
        else:
            centered = list(reversed(positive)) + positive
        return bytes(centered)

    @staticmethod
    def _fft(values):
        count = len(values)
        target = 0
        for index in range(1, count):
            bit = count >> 1
            while target & bit:
                target ^= bit
                bit >>= 1
            target ^= bit
            if index < target:
                values[index], values[target] = values[target], values[index]
        length = 2
        while length <= count:
            angle = -2.0 * math.pi / length
            root = complex(math.cos(angle), math.sin(angle))
            half = length // 2
            for offset in range(0, count, length):
                twiddle = 1.0 + 0.0j
                for index in range(half):
                    even = values[offset + index]
                    odd = values[offset + index + half] * twiddle
                    values[offset + index] = even + odd
                    values[offset + index + half] = even - odd
                    twiddle *= root
            length *= 2


def receiver_bounds(server):
    """Return the receiver's advertised tuning limits in kHz."""
    metadata = receiver_metadata(server)
    if not metadata:
        return None
    try:
        low = float(metadata.get("minimum_khz", DEFAULT_MIN_KHZ))
        high = float(metadata.get("maximum_khz", DEFAULT_MAX_KHZ))
    except (TypeError, ValueError):
        return DEFAULT_MIN_KHZ, DEFAULT_MAX_KHZ
    if low < 0.0 or high <= low:
        return DEFAULT_MIN_KHZ, DEFAULT_MAX_KHZ
    return low, high


def clamp_receiver_frequency(server, freq_khz):
    """Clamp a live tuning request to an FM-DX receiver's advertised band."""
    low, high = receiver_bounds(server) or (DEFAULT_MIN_KHZ, DEFAULT_MAX_KHZ)
    try:
        freq_khz = float(freq_khz)
    except (TypeError, ValueError):
        freq_khz = DEFAULT_FREQUENCY_KHZ
    return min(high, max(low, freq_khz))


def receiver_frequency(server, current_khz):
    """Keep an in-band tune or choose a safe FM broadcast default."""
    low, high = receiver_bounds(server) or (DEFAULT_MIN_KHZ, DEFAULT_MAX_KHZ)
    try:
        current_khz = float(current_khz)
    except (TypeError, ValueError):
        current_khz = DEFAULT_FREQUENCY_KHZ
    if low <= current_khz <= high:
        return current_khz
    return min(high, max(low, DEFAULT_FREQUENCY_KHZ))


def stations_from_receivers(receivers):
    return [
        (
            receiver["name"], receiver["location"], receiver["server"],
            receiver.get("used", 0), receiver.get("total", 0),
            receiver["lat"], receiver["lon"], "fmdx",
        )
        for receiver in receivers
    ]


def merge_receivers(*groups):
    """Merge directory sources without changing either source's order."""
    merged = []
    seen = set()
    for group in groups:
        for receiver in group or ():
            original_server = str(receiver.get("server") or "").strip()
            canonical_server = normalize_server_url(original_server)
            if not canonical_server or canonical_server in seen:
                continue
            copy = dict(receiver)
            copy.setdefault("receiver_type", "kiwi")
            copy["server"] = (
                canonical_server if copy["receiver_type"] == "fmdx" else original_server
            )
            merged.append(copy)
            seen.add(canonical_server)
    return merged


def load_directory(cache_path, timeout=15, minimum_entries=20):
    """Load a cached directory, refresh it, and atomically retain good data."""
    cache_path = Path(cache_path)
    cached = []
    try:
        cached = normalize_directory(json.loads(cache_path.read_text()))
    except (OSError, ValueError, TypeError):
        pass
    try:
        request = Request(DIRECTORY_URL, headers={"User-Agent": "iTuner-SDR/1.0"})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        receivers = normalize_directory(payload)
        if len(receivers) >= minimum_entries:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, separators=(",", ":")))
            os.replace(temporary, cache_path)
            register_receivers(receivers)
            return receivers
    except (OSError, ValueError, TypeError):
        pass
    register_receivers(cached)
    return cached


def websocket_url(server, endpoint):
    """Build an FM-DX WebSocket URL while retaining reverse-proxy subpaths."""
    server = normalize_server_url(server)
    if not server:
        raise ValueError("invalid FM-DX receiver URL")
    parsed = urlparse(server)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    suffix = "/" + str(endpoint).strip("/")
    path = (parsed.path.rstrip("/") + suffix) or suffix
    return parsed._replace(scheme=scheme, path=path, params="", query="", fragment="").geturl()


class WebSocket:
    """Small RFC 6455 client sufficient for FM-DX's text and binary feeds."""

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    @classmethod
    def connect(cls, url, timeout=8.0):
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            raise ValueError("invalid WebSocket URL")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw = socket.create_connection((parsed.hostname, port), timeout=timeout)
        try:
            raw.settimeout(timeout)
            if parsed.scheme == "wss":
                raw = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            host = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
            origin_scheme = "https" if parsed.scheme == "wss" else "http"
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Origin: {origin_scheme}://{host}\r\n"
                "User-Agent: iTuner-SDR/1.0\r\n\r\n"
            ).encode("ascii")
            raw.sendall(request)
            response = _read_http_header(raw)
            status = response.split(b"\r\n", 1)[0]
            expected = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest())
            if b" 101 " not in status or expected not in response:
                raise RuntimeError(f"FM-DX WebSocket handshake failed: {status.decode('latin1', 'replace')}")
            raw.settimeout(1.0)
            return cls(raw)
        except Exception:
            raw.close()
            raise

    def send_text(self, text):
        self._send_frame(0x1, str(text).encode("utf-8"))

    def recv(self):
        while True:
            header = _recv_exact(self.sock, 2)
            b0, b1 = header
            opcode = b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack(">H", _recv_exact(self.sock, 2))[0]
            elif length == 127:
                length = struct.unpack(">Q", _recv_exact(self.sock, 8))[0]
            mask = _recv_exact(self.sock, 4) if b1 & 0x80 else None
            payload = _recv_exact(self.sock, length) if length else b""
            if mask:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 0x8:
                raise EOFError("FM-DX WebSocket closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            return payload

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        with self.lock:
            self.sock.sendall(header + mask + masked)


def parse_text_message(raw):
    try:
        value = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def signal_dbm(payload):
    """Convert the Webserver's dBf signal value to the app's dBm scale."""
    if not isinstance(payload, dict):
        return None
    try:
        value = float(payload["sig"])
    except (KeyError, TypeError, ValueError):
        return None
    # FM-DX reports dBf (dB relative to one femtowatt). Some plugins already
    # expose a negative dBm value; retain those rather than converting twice.
    return value - 120.0 if value >= 0.0 else value


def tune_command(freq_khz):
    return f"T{int(round(float(freq_khz)))}"


def _read_http_header(sock):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1)
        if not chunk:
            raise EOFError("socket closed during WebSocket handshake")
        data += chunk
        if len(data) > 16384:
            raise RuntimeError("WebSocket header too large")
    return bytes(data)


def _recv_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise EOFError("WebSocket closed")
        data += chunk
    return bytes(data)
