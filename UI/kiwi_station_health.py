#!/usr/bin/env python3
"""Gentle rolling KiwiSDR waterfall availability checker.
Checks one cached public station at a time and stores local health state.
"""
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent))
import kiwi_live_display_fb as kiwi
import fmdx

CACHE = Path.home() / ".local/state/kiwi-gl-public-directory.json"
FMDX_CACHE = Path.home() / ".local/state/ituner-fmdx-directory.json"
HEALTH = Path.home() / ".local/state/kiwi-gl-station-health.json"
# Start one directory entry at a time across this whole period. The scan is
# deliberately paced, not batched; a slow probe only makes the pass longer.
FULL_SCAN_PERIOD_SECONDS = 12 * 60 * 60
IDLE_INTERVAL_SECONDS = 30.0
AUDIO_PROBE_SECONDS = 2.5
# A waterfall is healthy only when a W/F data line arrives by this deadline.
# Current live probes: good receivers delivered in 1.06s and 1.22s, while a
# no-frame receiver elapsed 3.40s; four seconds avoids a false positive.
WATERFALL_FRAME_TIMEOUT_SECONDS = 4.0
STATUS_TIMEOUT_SECONDS = 4.0


def load_json(path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return fallback


def scan_interval_seconds(station_count):
    """Even start-to-start spacing for one full directory pass in twelve hours."""
    return FULL_SCAN_PERIOD_SECONDS / station_count if station_count > 0 else IDLE_INTERVAL_SECONDS


def probe_audio(server):
    ws = None
    try:
        ws = kiwi.KiwiWebSocket.connect(server, "SND", timeout=4)
        kiwi.send_kiwi_setup(ws, "kiwi", "availability-check")
        configured = False
        deadline = time.monotonic() + AUDIO_PROBE_SECONDS
        while time.monotonic() < deadline:
            try:
                message = ws.recv()
            except TimeoutError:
                continue
            if message[:3] == b"MSG":
                params = kiwi.parse_msg_params(message)
                if "audio_rate" in params:
                    ws.send_text("SET AR OK in=%s out=44100" % int(float(params["audio_rate"])))
                if "sample_rate" in params and not configured:
                    kiwi.send_snd_setup(ws, 7076.5, "usb", 300, 2700)
                    configured = True
            elif message[:3] == b"SND" and len(message) > 10:
                return True
        return False
    except Exception:
        return False
    finally:
        if ws is not None:
            ws.send_close()


def probe_waterfall(server):
    ws = None
    try:
        ws = kiwi.KiwiWebSocket.connect(server, "W/F", timeout=4)
        kiwi.send_kiwi_setup(ws, "kiwi", "availability-check")
        kiwi.send_wf_setup(ws, 7076.5, 12, 1)
        deadline = time.monotonic() + WATERFALL_FRAME_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                message = ws.recv()
            except TimeoutError:
                continue
            if message.startswith(b"W/F") and len(message) > 16:
                # The socket handshake alone is not evidence of a waterfall.
                # Persist availability only after receiving real frame bytes.
                return "ok"
        return "no_data"
    except Exception as exc:
        return type(exc).__name__.lower()
    finally:
        if ws is not None:
            ws.send_close()


def probe_fmdx_audio(server):
    """Confirm that an FM-DX receiver is supplying its MP3 fallback feed."""
    ws = None
    try:
        ws = fmdx.WebSocket.connect(fmdx.websocket_url(server, "audio"), timeout=4)
        ws.send_text(json.dumps({"type": "fallback", "data": "mp3"}, separators=(",", ":")))
        deadline = time.monotonic() + AUDIO_PROBE_SECONDS
        while time.monotonic() < deadline:
            try:
                message = ws.recv()
            except TimeoutError:
                continue
            if message and not message.startswith(b"{"):
                return True
        return False
    except Exception:
        return False
    finally:
        if ws is not None:
            ws.close()


def probe_time_limit(server):
    """Return whether the receiver advertises any admin-configured limits.

    KiwiSDR's public status intentionally omits the numeric timeout. Its
    hardware banner adds the hourglass/"Limits" marker when the owner enables
    an inactivity or per-IP limit. The exact timeout is learned later from the
    server's ``MSG inactivity_timeout=N`` event in the live client.
    """
    try:
        parsed = urlparse(server if "://" in server else "http://" + server)
        endpoint = parsed._replace(path="/status", params="", query="", fragment="").geturl()
        request = Request(endpoint, headers={"User-Agent": "iTuner-SDR-health/1.0"})
        with urlopen(request, timeout=STATUS_TIMEOUT_SECONDS) as response:
            status = response.read(32768).decode("utf-8", "replace")
        return "⏳ Limits" in status or "Limits" in status.split("sdr_hw=", 1)[-1].split("\n", 1)[0]
    except Exception:
        return None


def save_health(health):
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HEALTH.with_suffix(".tmp")
    temporary.write_text(json.dumps(health, separators=(",", ":")))
    temporary.replace(HEALTH)


def station_is_at_capacity(station):
    """Whether valid directory capacity says no listener slot is free."""
    if not isinstance(station, (list, tuple)) or len(station) < 5:
        return False
    try:
        used = int(station[3])
        total = int(station[4])
    except (TypeError, ValueError):
        return False
    return total > 0 and used >= total


def refresh_station_health(
    health, station, audio_probe=probe_audio, waterfall_probe=probe_waterfall,
    limit_probe=probe_time_limit, fmdx_audio_probe=probe_fmdx_audio,
):
    """Probe a station unless the directory reports every listener slot full.

    A capacity skip intentionally makes no edit to the station record, so its
    last verified audio/waterfall booleans and checked timestamp remain intact.
    """
    if station_is_at_capacity(station):
        return False
    server = station[2]
    receiver_type = station[7] if len(station) > 7 else "kiwi"
    if receiver_type == "fmdx":
        audio = fmdx_audio_probe(server)
        health.setdefault("stations", {})[server] = {
            "status": "ok" if audio else "failed",
            "waterfall": False,
            "audio": audio,
            "receiver_type": "fmdx",
            "checked": int(time.time()),
        }
        return True
    waterfall = waterfall_probe(server) == "ok"
    audio = audio_probe(server)
    previous = health.setdefault("stations", {}).get(server, {})
    entry = {
        "status": "ok" if waterfall or audio else "failed",
        "waterfall": waterfall,
        "audio": audio,
        "checked": int(time.time()),
    }
    advertised = limit_probe(server)
    if advertised is not None:
        entry["time_limit_advertised"] = advertised
    elif previous.get("time_limit_advertised"):
        entry["time_limit_advertised"] = True
    if previous.get("timeout_seconds"):
        entry["timeout_seconds"] = previous["timeout_seconds"]
        entry["timeout_observed"] = previous.get("timeout_observed", previous.get("checked", 0))
    health["stations"][server] = entry
    return True


def main():
    while True:
        started = time.monotonic()
        stations = load_json(CACHE, [])
        try:
            stations += fmdx.stations_from_receivers(
                fmdx.normalize_directory(load_json(FMDX_CACHE, {}))
            )
        except (TypeError, ValueError):
            pass
        health = load_json(HEALTH, {"cursor": 0, "stations": {}})
        interval = scan_interval_seconds(len(stations))
        if stations:
            cursor = int(health.get("cursor", 0)) % len(stations)
            station = stations[cursor]
            if not isinstance(station, (list, tuple)) or len(station) < 3:
                health["cursor"] = (cursor + 1) % len(stations)
                save_health(health)
                time.sleep(max(0.5, interval - (time.monotonic() - started)))
                continue
            probed = refresh_station_health(health, station)
            if not probed:
                print(f"health skip full capacity: {station[2]}", flush=True)
            health["cursor"] = (cursor + 1) % len(stations)
            save_health(health)
        time.sleep(max(0.5, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
