import json
import math
import struct
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fmdx
import kiwi_station_health


class FmdxDirectoryTests(unittest.TestCase):
    def tearDown(self):
        fmdx.register_receivers([])

    def test_normalizes_available_receiver_and_frequency_range(self):
        receivers = fmdx.normalize_directory({"dataset": [{
            "name": "Bucharest FM",
            "url": "https://example.test/radio/#ignored",
            "coords": ["44.42", "26.10"],
            "status": 1,
            "countryName": "Romania",
            "city": "Bucharest",
            "bwLimit": "65 - 108 MHz",
            "audioChannels": 2,
        }]})
        self.assertEqual(len(receivers), 1)
        receiver = receivers[0]
        self.assertEqual(receiver["server"], "https://example.test/radio")
        self.assertEqual(receiver["location"], "Bucharest, Romania")
        self.assertEqual((receiver["minimum_khz"], receiver["maximum_khz"]), (65000.0, 108000.0))
        self.assertEqual(receiver["receiver_type"], "fmdx")

    def test_excludes_locked_unreachable_and_invalid_coordinates(self):
        payload = {"dataset": [
            {"url": "https://locked.test", "coords": [1, 2], "status": 2},
            {"url": "https://down.test", "coords": [1, 2], "status": 0},
            {"url": "https://bad.test", "coords": [91, 2], "status": 1},
        ]}
        self.assertEqual(fmdx.normalize_directory(payload), [])

    def test_registry_selects_an_in_band_default(self):
        receiver = {
            "server": "https://example.test",
            "minimum_khz": 87000.0,
            "maximum_khz": 108000.0,
        }
        fmdx.register_receivers([receiver])
        self.assertTrue(fmdx.is_fmdx_server("https://example.test/"))
        self.assertEqual(fmdx.receiver_frequency("https://example.test", 7075.0), 100000.0)
        self.assertEqual(fmdx.receiver_frequency("https://example.test", 101700.0), 101700.0)

    def test_live_tuning_clamps_to_fmdx_band_instead_of_kiwi_limit(self):
        receiver = {
            "server": "https://example.test",
            "minimum_khz": 87000.0,
            "maximum_khz": 108000.0,
        }
        fmdx.register_receivers([receiver])
        self.assertEqual(fmdx.receiver_bounds("https://example.test"), (87000.0, 108000.0))
        self.assertEqual(fmdx.clamp_receiver_frequency("https://example.test", 101700.0), 101700.0)
        self.assertEqual(fmdx.clamp_receiver_frequency("https://example.test", 29999.0), 87000.0)
        self.assertEqual(fmdx.clamp_receiver_frequency("https://example.test", 120000.0), 108000.0)

    def test_builds_websocket_paths_behind_reverse_proxy(self):
        self.assertEqual(
            fmdx.websocket_url("https://host.test/fmdx/", "audio"),
            "wss://host.test/fmdx/audio",
        )
        self.assertEqual(
            fmdx.websocket_url("http://host.test:8080", "/text"),
            "ws://host.test:8080/text",
        )

    def test_parses_status_and_tuning_commands(self):
        message = fmdx.parse_text_message(json.dumps({"freq": 99.5, "sig": 47.5}).encode())
        self.assertEqual(message["freq"], 99.5)
        self.assertEqual(fmdx.signal_dbm(message), -72.5)
        self.assertEqual(fmdx.tune_command(101700.4), "T101700")

    def test_fmdx_receiver_owns_its_display_mode(self):
        fmdx.register_receivers([{"server": "https://example.test"}])
        self.assertEqual(fmdx.receiver_mode("https://example.test", "USB"), "FM-FMDX")
        self.assertEqual(fmdx.receiver_mode("https://kiwi.test", "USB"), "USB")

    def test_builds_normalized_audio_scope_from_mono_pcm(self):
        pcm = struct.pack("<8h", -32768, -16384, 0, 16384, 32767, 0, -8192, 8192)
        values = fmdx.audio_scope_samples(pcm, bins=4)
        self.assertEqual(len(values), 4)
        self.assertAlmostEqual(values[0], -0.75, places=2)
        self.assertAlmostEqual(values[1], 0.25, places=2)
        self.assertAlmostEqual(values[2], 0.50, places=2)
        self.assertAlmostEqual(values[3], 0.0, places=2)

    def test_builds_carrier_centered_twenty_khz_audio_waterfall_rows(self):
        analyzer = fmdx.AudioWaterfallAnalyzer(
            sample_rate=48000, span_hz=20000, fft_size=1024, bins=128,
        )
        pcm = struct.pack(
            "<1024h",
            *(int(24000 * math.sin(2 * math.pi * 1000 * index / 48000)) for index in range(1024)),
        )
        rows = analyzer.feed(pcm)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 128)
        left_peak = max(range(64), key=rows[0].__getitem__)
        right_peak = max(range(64, 128), key=rows[0].__getitem__)
        self.assertTrue(54 <= left_peak <= 60, left_peak)
        self.assertTrue(67 <= right_peak <= 73, right_peak)
        self.assertLessEqual(abs((127 - right_peak) - left_peak), 1)

    def test_audio_waterfall_reset_drops_partial_audio_from_previous_station(self):
        analyzer = fmdx.AudioWaterfallAnalyzer(
            sample_rate=48000, span_hz=20000, fft_size=64, bins=16,
        )
        half_frame = struct.pack("<32h", *(1000 for _ in range(32)))
        self.assertEqual(analyzer.feed(half_frame), ())
        analyzer.reset()
        self.assertEqual(analyzer.feed(half_frame), ())
        self.assertEqual(len(analyzer.feed(half_frame)), 1)

    def test_downsamples_fmdx_audio_for_existing_asr_rate(self):
        pcm = struct.pack("<8h", *range(8))
        downsampled = fmdx.resample_mono_s16le(pcm, 48000, 12000)
        self.assertEqual(struct.unpack("<2h", downsampled), (0, 4))

    def test_audio_waterfall_zoom_changes_the_visible_span(self):
        spans = [fmdx.audio_waterfall_span_khz(level) for level in range(17)]
        self.assertEqual(spans[0], 20.0)
        self.assertTrue(all(left > right for left, right in zip(spans, spans[1:])))
        self.assertAlmostEqual(spans[-1], 1.25)

    def test_audio_waterfall_drag_pan_is_visible_and_stays_inside_source(self):
        self.assertEqual(
            fmdx.audio_waterfall_drag_center_khz(100_000.0, 99_997.0, 10.0),
            -3.0,
        )
        self.assertEqual(
            fmdx.audio_waterfall_drag_center_khz(100_000.0, 99_000.0, 10.0),
            -5.0,
        )
        self.assertEqual(
            fmdx.audio_waterfall_drag_center_khz(100_000.0, 99_997.0, 20.0),
            0.0,
        )

    def test_normalizes_server_presets_and_merges_learned_rds_names(self):
        presets = fmdx.normalize_station_presets({"presets": ["87.8", "101.5", "bad"]})
        learned = ({"frequency_khz": 101500.0, "name": "RADIO CLUJ", "pi": "E123"},)
        merged = fmdx.merge_station_presets(presets, learned)
        self.assertEqual([item["frequency_khz"] for item in merged], [87800.0, 101500.0])
        self.assertEqual(merged[1]["name"], "RADIO CLUJ")
        self.assertEqual(merged[1]["pi"], "E123")

    def test_nearest_station_prefers_known_rds_then_falls_back_to_preset(self):
        stations = (
            {"frequency_khz": 99_900.0, "name": "", "pi": ""},
            {"frequency_khz": 100_100.0, "name": "RDS ONE", "pi": "E101"},
            {"frequency_khz": 101_700.0, "name": "RDS TWO", "pi": "E102"},
        )
        self.assertEqual(fmdx.nearest_station_frequency(stations, 100_000.0), 100_100.0)
        self.assertEqual(
            fmdx.nearest_station_frequency((stations[0],), 100_000.0),
            99_900.0,
        )
        self.assertIsNone(fmdx.nearest_station_frequency((), 100_000.0))

    def test_rds_discovery_orders_only_unnamed_presets_nearest_first(self):
        stations = (
            {"frequency_khz": 98_100.0, "name": "", "pi": ""},
            {"frequency_khz": 100_100.0, "name": "KNOWN", "pi": "E100"},
            {"frequency_khz": 100_300.0, "name": "", "pi": ""},
            {"frequency_khz": 99_900.0, "name": "", "pi": ""},
        )
        self.assertEqual(
            fmdx.rds_discovery_frequencies(stations, 100_000.0),
            (99_900.0, 100_300.0, 98_100.0),
        )

    def test_rds_discovery_rejects_stale_status_from_previous_frequency(self):
        self.assertTrue(fmdx.status_matches_frequency({"freq": 101.5}, 101_500.0))
        self.assertFalse(fmdx.status_matches_frequency({"freq": 99.9}, 101_500.0))

    def test_manual_band_scan_covers_every_100_khz_channel(self):
        self.assertEqual(
            fmdx.band_scan_frequencies(87_550.0, 87_850.0),
            (87_600.0, 87_700.0, 87_800.0),
        )

    def test_only_an_active_manual_scan_silences_playback(self):
        pcm = b"\x01\x02\x03\x04"
        self.assertEqual(fmdx.playback_pcm(pcm, muted=False, scan_active=False), pcm)
        self.assertEqual(fmdx.playback_pcm(pcm, muted=False, scan_active=True), bytes(4))
        self.assertEqual(fmdx.playback_pcm(pcm, muted=True, scan_active=False), bytes(4))

    def test_remembered_fmdx_server_can_be_registered_without_directory_cache(self):
        fmdx.register_receivers([])
        fmdx.ensure_receiver("https://remembered.example/radio", "fmdx")
        self.assertTrue(fmdx.is_fmdx_server("https://remembered.example/radio/"))
        self.assertEqual(fmdx.receiver_bounds("https://remembered.example/radio"), (64000.0, 108000.0))

    def test_station_cache_round_trip_preserves_rds_names(self):
        cache = {"https://example.test": ({
            "frequency_khz": 101500.0, "name": "RADIO CLUJ", "pi": "E123",
        },)}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stations.json"
            fmdx.save_station_cache(path, cache)
            loaded = fmdx.load_station_cache(path)
        self.assertEqual(loaded["https://example.test"][0]["name"], "RADIO CLUJ")

    def test_merge_preserves_kiwi_url_but_canonicalizes_fmdx(self):
        merged = fmdx.merge_receivers(
            [{"server": "http://kiwi.test:8073/", "receiver_type": "kiwi"}],
            [{"server": "https://fmdx.test/radio/", "receiver_type": "fmdx"}],
        )
        self.assertEqual(merged[0]["server"], "http://kiwi.test:8073/")
        self.assertEqual(merged[1]["server"], "https://fmdx.test/radio")

    def test_health_dispatches_fmdx_without_a_waterfall_probe(self):
        health = {"stations": {}}
        station = ("FM", "Somewhere", "https://fmdx.test", 0, 0, 1, 2, "fmdx")
        called = []
        result = kiwi_station_health.refresh_station_health(
            health,
            station,
            audio_probe=lambda _server: self.fail("Kiwi audio probe used"),
            waterfall_probe=lambda _server: self.fail("Kiwi waterfall probe used"),
            fmdx_audio_probe=lambda server: called.append(server) or True,
        )
        self.assertTrue(result)
        self.assertEqual(called, ["https://fmdx.test"])
        self.assertTrue(health["stations"]["https://fmdx.test"]["audio"])
        self.assertFalse(health["stations"]["https://fmdx.test"]["waterfall"])


if __name__ == "__main__":
    unittest.main()
