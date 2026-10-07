#!/usr/bin/env python3
"""Capture KiwiSDR transport expectations from the Python implementation.

Runs with the application runtime, like the other renderer captures:

    UI/.venv/bin/python3 qt/tests/parity/capture_kiwi_transport.py

Every expectation is the real output of a Python function:
`parse_endpoint`, `websocket_redirect_endpoint`, `swap_s16_bytes` and
`KiwiWebSocket`'s accept check live in `UI/kiwi_live_display_fb.py`; the pairing
timestamp and the stereo downmix live in `UI/kiwi_gl_display.py`. The wall clock
the pairing timestamp reads is replaced with a controlled sequence so the
strictly-monotonic behaviour is reproducible, and the recorded millisecond value
is what the C++ test feeds straight to the clock.

The SND decode is inline in the renderer's worker loop rather than a module
function, so `parse_snd` below restates that seven-byte-header rule and the
capture records its output, the same way the swipe capture handles the inline
gesture rules.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import socket
import struct
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "UI"))

try:
    kiwi_gl_display = importlib.import_module("kiwi_gl_display")
except ImportError as error:  # pragma: no cover - environment guidance
    raise SystemExit(
        "this capture needs the application runtime; run it with "
        f"UI/.venv/bin/python3 ({error})"
    ) from error

kiwi = kiwi_gl_display.kiwi
EXPECTED = REPO_ROOT / "qt/tests/golden/kiwi_transport_expected.json"

ENDPOINTS = [
    "http://kiwi.test:8073",
    "https://kiwi.test:8073",
    "wss://kiwi.test",
    "ws://kiwi.test:8073/",
    "kiwi.test:8073",
    "kiwi.test",
    "http://KX4AZ.PROXY.KIWISDR.COM:8073",
    "http://kiwi.test:8074/extra/path?x=1",
    "https://kiwi.test",
    "http://",
    "",
    "http://kiwi.test:not-a-port",
    "http://kiwi.test:99999",
]

REDIRECTS = [
    ("307_absolute", b"HTTP/1.1 307 Temporary Redirect\r\nLocation: http://new.kiwi.test:8073\r\n\r\n"),
    ("301_relative_scheme_ok", b"HTTP/1.1 301 Moved Permanently\r\nLocation: https://new.kiwi.test:8073\r\n\r\n"),
    ("302_wss", b"HTTP/1.1 302 Found\r\nlocation: wss://secure.kiwi.test\r\n\r\n"),
    ("200_not_redirect", b"HTTP/1.1 200 OK\r\nLocation: http://new.kiwi.test:8073\r\n\r\n"),
    ("307_no_location", b"HTTP/1.1 307 Temporary Redirect\r\nServer: kiwisdr\r\n\r\n"),
    ("307_bad_scheme", b"HTTP/1.1 307 Temporary Redirect\r\nLocation: ftp://new.kiwi.test\r\n\r\n"),
    ("307_no_host", b"HTTP/1.1 307 Temporary Redirect\r\nLocation: http://\r\n\r\n"),
    ("308_first_location_wins", b"HTTP/1.1 308 Permanent Redirect\r\nLocation: http://a.kiwi.test:8073\r\nLocation: http://b.kiwi.test:8073\r\n\r\n"),
]

TIMESTAMP_SEQUENCE_MS = [1_000_000, 1_000_000, 1_000_400, 1_000_900, 1_002_000, 999_000]

ACCEPT_KEYS = [
    "dGhlIHNhbXBsZSBub25jZQ==",
    "x3JJHMbDL1EzLkh9GBhXDw==",
    "AAAAAAAAAAAAAAAAAAAAAA==",
]

SND_PACKETS = [
    ("normal_le", 0x80, 7, 200, bytes(range(20))),
    ("stereo_squelch", 0x08 | 0x40 | 0x80, 4_000_000_000, 0, bytes(range(32))),
    ("compressed_big_endian", 0x10, 1, 65_535, bytes(range(16))),
    ("no_pcm", 0x00, 0, 300, b""),
]

PLAYABLE_CASES = [
    (0x80, "usb", 1),
    (0x80, "lsb", 2),
    (0xC0, "am", 1),
    (0x00, "usb", 1),
    (0x88, "sas", 1),
    (0x88, "usb", 1),
    (0x80, "iq", 1),
    (0x80, "drm", 1),
    (0x90, "usb", 1),
    (0x90, "sas", 2),
]


def endpoint_rows():
    rows = []
    for endpoint in ENDPOINTS:
        try:
            scheme, host, port = kiwi.parse_endpoint(endpoint)
        except Exception:  # noqa: BLE001 - record the real raise, whatever it is
            rows.append({"input": endpoint, "ok": False})
            continue
        rows.append({"input": endpoint, "ok": True, "scheme": scheme, "host": host, "port": port})
    return rows


def redirect_rows():
    rows = []
    for name, response in REDIRECTS:
        redirect = kiwi.websocket_redirect_endpoint(response)
        rows.append(
            {"name": name, "response": response.decode("latin1"), "redirect": redirect}
        )
    return rows


def timestamp_rows():
    # Reset the module state, then drive the clock through a controlled sequence.
    # The recorded `now_ms` is exactly what `int(time.time() * 1000)` produced, so
    # the C++ test feeds that value straight to its clock and compares results.
    original_time = kiwi_gl_display.time
    rows = []
    try:
        kiwi_gl_display._last_kiwi_session_timestamp = 0
        for milliseconds in TIMESTAMP_SEQUENCE_MS:
            seconds = milliseconds / 1000.0
            # One frozen reading per call, so the recorded `now_ms` is exactly the
            # reading the function saw.
            kiwi_gl_display.time = types.SimpleNamespace(time=lambda value=seconds: value)
            now_ms = int(kiwi_gl_display.time.time() * 1000)
            result = kiwi_gl_display.next_kiwi_session_timestamp()
            rows.append({"now_ms": now_ms, "result": result})
    finally:
        kiwi_gl_display.time = original_time
    return rows


def accept_rows():
    rows = []
    for key in ACCEPT_KEYS:
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        rows.append({"key": key, "accept": expected})
    return rows


def parse_snd(message):
    """Mirror the renderer's inline seven-byte SND header decode."""
    if len(message) < 10 or message[:3] != b"SND":
        return None
    body = message[3:]
    flags, sequence = struct.unpack("<BI", body[:5])
    smeter, = struct.unpack(">H", body[5:7])
    return {
        "flags": flags,
        "sequence": sequence,
        "smeter_dbm": 0.1 * smeter - 127.0,
        "pcm_hex": body[7:].hex(),
    }


def snd_rows():
    rows = []
    for name, flags, sequence, smeter, pcm in SND_PACKETS:
        message = (
            b"SND"
            + bytes([flags])
            + struct.pack("<I", sequence)
            + struct.pack(">H", smeter)
            + pcm
        )
        row = {"name": name, "message_hex": message.hex()}
        parsed = parse_snd(message)
        if parsed is None:
            row["ok"] = False
        else:
            row["ok"] = True
            row.update(parsed)
        rows.append(row)
    # Two shapes the decode must reject.
    for name, message in (
        ("short", b"SND\x80\x00"),
        ("bad_tag", b"XYZ\x80" + bytes(20)),
    ):
        row = {"name": name, "message_hex": message.hex()}
        parsed = parse_snd(message)
        row["ok"] = parsed is not None
        if parsed is not None:
            row.update(parsed)
        rows.append(row)
    return rows


def swap_rows():
    inputs = [bytes([0, 1, 2, 3, 4, 5]), b"\x00\x80\xff\x7f", b""]
    return [
        {"in_hex": value.hex(), "out_hex": kiwi.swap_s16_bytes(value).hex()}
        for value in inputs
    ]


def stereo_rows():
    inputs = [
        struct.pack("<8h", 1, 2, -3, -4, 7, -7, -32768, 32767),
        struct.pack("<2h", 100, 200),
        b"",
        struct.pack("<3h", 1, 2, 3),
    ]
    return [
        {"in_hex": value.hex(), "out_hex": kiwi_gl_display.stereo_s16le_to_mono(value).hex()}
        for value in inputs
    ]


def playable_rows():
    rows = []
    for flags, radio_mode, channels in PLAYABLE_CASES:
        packet_is_stereo = bool(flags & kiwi.SND_FLAG_STEREO)
        playable = not (flags & kiwi.SND_FLAG_COMPRESSED) and (
            (packet_is_stereo and radio_mode in kiwi_gl_display.KIWI_STEREO_AUDIO_MODES)
            or (
                not packet_is_stereo
                and radio_mode not in kiwi_gl_display.KIWI_NON_AUDIO_MODES
                and channels == 1
            )
        )
        rows.append(
            {
                "flags": flags,
                "radio_mode": radio_mode,
                "channels": channels,
                "playable": playable,
            }
        )
    return rows


class ScriptedSocket:
    """Replays a scripted event list, where an exception is raised and bytes
    are returned, matching `UI/test_kiwi_transport.py`."""

    def __init__(self, events):
        self.events = list(events)
        self.sent = []

    def recv(self, _count):
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    def sendall(self, _data):
        self.sent.append(bytes(_data))


def decode_events(events):
    result = []
    for event in events:
        if event.get("timeout"):
            result.append(socket.timeout())
        else:
            result.append(bytes.fromhex(event["hex"]))
    return result


def outcome(call):
    try:
        value = call()
    except BaseException as error:  # noqa: BLE001 - record the real outcome
        row = {"error": type(error).__name__, "message": str(error)}
        if isinstance(error, kiwi.KiwiServerBusyError):
            row["capacity"] = error.capacity
        return row
    if isinstance(value, (bytes, bytearray)):
        return {"ok_hex": bytes(value).hex()}
    return {"ok": value}


def run_with_clock(clock_values, call):
    original = kiwi.time
    ticks = iter(clock_values)
    last = clock_values[-1] if clock_values else 0.0
    try:
        def monotonic():
            try:
                return next(ticks)
            except StopIteration:
                return last

        kiwi.time = types.SimpleNamespace(monotonic=monotonic)
        return call()
    finally:
        kiwi.time = original


BUSY_CASES = [
    {"too_busy": "0"},
    {"too_busy": "4"},
    {"too_busy": 7},
    {"too_busy": "0"}, 
    {"sample_rate": "12000"},
    {"too_busy": "not-a-number"},
    {},
]

RECV_EXACT_CASES = [
    ("partial_survives_timeout",
     [{"hex": "6162"}, {"timeout": True}, {"hex": "6364"}], 4, [0.0, 0.1]),
    ("idle_timeout_reraises",
     [{"timeout": True}], 2, [0.0, 0.1]),
    ("partial_frame_times_out",
     [{"hex": "61"}, {"timeout": True}], 4, [0.0, 8.5]),
    ("closed_socket",
     [{"hex": ""}], 2, [0.0]),
    ("chunked",
     [{"hex": "6162"}, {"hex": "6364"}, {"hex": "6566"}], 6, [0.0]),
    ("zero_count",
     [], 0, [0.0]),
]

_MASKED_PAYLOAD = b"hello"
_MASKED_MASK = b"\x01\x02\x03\x04"
_MASKED_BYTES = bytes(
    value ^ _MASKED_MASK[index % 4] for index, value in enumerate(_MASKED_PAYLOAD)
)

# Each event is exactly the size of one `recv_exact` call, because the scripted
# socket ignores the requested count, matching `UI/test_kiwi_transport.py`.
FRAME_CASES = [
    ("text", [b"\x81\x05", b"hello"]),
    ("binary", [b"\x82\x03", b"\x00\x01\x02"]),
    ("masked_text", [b"\x81\x85", _MASKED_MASK, _MASKED_BYTES]),
    ("close_code_only", [b"\x88\x02", b"\x03\xe8"]),
    ("close_code_reason", [b"\x88\x06", b"\x03\xe8", b"bye."]),
    ("close_empty", [b"\x88\x00"]),
    ("pong_skipped", [b"\x8a\x00", b"\x81\x02", b"hq"]),
    ("ping_then_text", [b"\x89\x02", b"hi", b"\x81\x01", b"x"]),
    ("extended_16bit", [b"\x81\x7e", b"\x00\x02", b"hi"]),
    ("frame_too_large", [b"\x82\x7f", struct.pack(">Q", kiwi.WEBSOCKET_MAX_FRAME_BYTES + 1)]),
    ("socket_closed", [b""]),
]


def busy_rows():
    rows = []
    for params in BUSY_CASES:
        rows.append({"params": params, **outcome(lambda p=params: kiwi.raise_for_kiwi_server_message(p))})
    return rows


def recv_exact_rows():
    rows = []
    for name, events, count, clock in RECV_EXACT_CASES:
        sock = ScriptedSocket(decode_events(events))
        rows.append(
            {
                "name": name,
                "events": events,
                "count": count,
                "clock": clock,
                **outcome(lambda s=sock, c=count, k=clock: run_with_clock(k, lambda: kiwi.recv_exact(s, c))),
            }
        )
    return rows


def frame_rows():
    rows = []
    for name, events in FRAME_CASES:
        sock = ScriptedSocket(list(events))
        row = {"name": name, "events": [event.hex() for event in events]}
        row.update(outcome(lambda s=sock: run_with_clock([0.0], lambda: kiwi.KiwiWebSocket(s).recv())))
        row["sent_count"] = len(sock.sent)
        rows.append(row)
    return rows


LIVE_STATE_OPS = [
    ("set_freq", {"freq": 7075.0}),
    ("preview_freq", {"freq": 7100.0}),
    ("get_tune", {"seen": 0}),
    ("get_tune", {"seen": 1}),
    ("get_tune", {"seen": 2}),
    ("set_zoom", {"zoom": 13}),
    ("set_zoom", {"zoom": 10}),
    ("get_view", {"seen": 1}),
    ("get_view", {"seen": 2}),
    ("set_freq_zoom", {"freq": 7106.0, "zoom": 9}),
    ("get_tune", {"seen": 1}),
    ("get_view", {"seen": 2}),
    ("set_server", {"server": "http://a.test:8073"}),
    ("get_server", {"seen": 0}),
    ("get_server", {"seen": 1}),
    ("set_server", {"server": "http://b.test:8073", "zoom": 6}),
    ("get_server", {"seen": 1}),
    ("get_zoom", {}),
    ("get_span", {}),
    ("current_server", {}),
    ("set_zoom", {"zoom": 20}),
    ("get_span", {}),
]


def live_state_rows():
    """Drive the real `LiveState` through a scripted sequence so the commit
    protocol and its generation counters are compared end to end."""
    state = kiwi.LiveState("http://init.test:8073", 7000.0, 13, -110.0)
    rows = []
    for op, args in LIVE_STATE_OPS:
        if op == "set_freq":
            result = state.set_freq(args["freq"])
        elif op == "preview_freq":
            state.preview_freq(args["freq"])
            result = None
        elif op == "get_tune":
            generation, value = state.get_tune(args["seen"])
            result = [generation, value]
        elif op == "set_zoom":
            result = state.set_zoom(args["zoom"])
        elif op == "set_freq_zoom":
            result = state.set_freq_zoom(args["freq"], args["zoom"])
        elif op == "get_view":
            generation, value = state.get_view(args["seen"])
            result = [generation, list(value) if value is not None else None]
        elif op == "set_server":
            result = state.set_server(args["server"], args.get("zoom"))
        elif op == "get_server":
            generation, value = state.get_server(args["seen"])
            result = [generation, value]
        elif op == "get_zoom":
            result = state.get_zoom()
        elif op == "get_span":
            result = state.get_span()
        elif op == "current_server":
            result = state.current_server()
        else:
            raise AssertionError(op)
        rows.append(
            {
                "op": op,
                "args": args,
                "result": result,
                "tune_gen": state.tune_generation,
                "view_gen": state.view_generation,
                "server_gen": state.server_generation,
            }
        )
    return rows


def main() -> int:
    expected = {
        "endpoints": endpoint_rows(),
        "redirects": redirect_rows(),
        "session_paths": [
            {"timestamp": 1_000_000, "stream": "SND", "path": "/ws/kiwi/1000000/SND"},
            {"timestamp": 1_000_000, "stream": "W/F", "path": "/ws/kiwi/1000000/W/F"},
            {"timestamp": 1_786_000_000_123, "stream": "W/F", "path": "/ws/kiwi/1786000000123/W/F"},
        ],
        "accept_keys": accept_rows(),
        "timestamps": timestamp_rows(),
        "snd": snd_rows(),
        "swap": swap_rows(),
        "stereo_mono": stereo_rows(),
        "playable": playable_rows(),
        "busy": busy_rows(),
        "recv_exact": recv_exact_rows(),
        "frames": frame_rows(),
        "live_state": live_state_rows(),
        "live_state_max_zoom": kiwi.KIWI_MAX_ZOOM,
        "constants": {
            "quantum_frames": kiwi_gl_display.KIWI_RAW_AUDIO_QUANTUM_FRAMES,
            "keepalive_seconds": kiwi_gl_display.KIWI_SND_KEEPALIVE_SECONDS,
            "snd_stream": "SND",
            "waterfall_stream": "W/F",
            "stereo_modes": sorted(kiwi_gl_display.KIWI_STEREO_AUDIO_MODES),
            "non_audio_modes": sorted(kiwi_gl_display.KIWI_NON_AUDIO_MODES),
            "guid": "258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
        },
    }
    EXPECTED.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
    print(
        f"wrote {EXPECTED.relative_to(REPO_ROOT)} with {len(expected['endpoints'])} endpoint, "
        f"{len(expected['redirects'])} redirect and {len(expected['snd'])} SND rows"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
