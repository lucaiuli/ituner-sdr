import tempfile
import unittest
from pathlib import Path
import sys
import queue
import struct
import io
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parent))
import fmdx  # noqa: E402
import kiwi_gl_display as ui  # noqa: E402


class FmdxUiIntegrationTests(unittest.TestCase):
    def tearDown(self):
        fmdx.register_receivers(ui.FMDX_RECEIVERS)

    @staticmethod
    def state(server="http://kiwi.test", frequency=7075.0):
        return ui.SharedState(
            server, frequency, 8, -95.0, -125, -15, 3, "usb", True,
        )

    def register_fmdx(self, server="https://fm.example/radio"):
        fmdx.register_receivers([{
            "server": server,
            "minimum_khz": 87_500.0,
            "maximum_khz": 108_000.0,
        }])
        return server

    def test_typed_station_rows_support_protocol_routes(self):
        server = self.register_fmdx()
        rows = [
            ("Kiwi", "A", "http://kiwi.test", 0, 4, 1, 2, "kiwi"),
            ("FM", "B", server, 0, 0, 3, 4, "fmdx"),
        ]

        self.assertEqual(ui.station_receiver_type(rows[1]), "fmdx")
        self.assertEqual(
            [row[0] for row in ui.filtered_stations(rows, "", "name", "fmdx")],
            ["FM"],
        )
        self.assertEqual(
            [row[0] for row in ui.filtered_stations(rows, "", "name", "kiwi")],
            ["Kiwi"],
        )

    def test_shared_state_switches_protocol_and_clamps_fmdx_frequency(self):
        server = self.register_fmdx()
        state = self.state()
        original_mode = state.radio_snapshot()[0]

        state.set_server(server, receiver_type="fmdx")
        state.set_view(freq_khz=120_000.0)

        self.assertEqual(state.receiver_type_snapshot(), "fmdx")
        self.assertEqual(state.snapshot()[1], 108_000.0)
        self.assertEqual(state.radio_snapshot()[0], original_mode)
        self.assertEqual(ui.effective_receiver_mode("fmdx", original_mode), "FM-FMDX")

    def test_protocol_specific_bounds_and_display_span(self):
        server = self.register_fmdx()

        self.assertEqual(
            ui.receiver_tuning_bounds(server, "fmdx"),
            (87_500.0, 108_000.0),
        )
        self.assertEqual(ui.receiver_tuning_bounds("http://kiwi", "kiwi"), (0.0, ui.TUNING_MAX_KHZ))
        self.assertEqual(ui.receiver_display_span(0, "fmdx"), 20.0)

    def test_remembered_receiver_round_trip_preserves_protocol(self):
        server = self.register_fmdx()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receiver.json"
            ui.save_remembered_view(
                path, server, 101_700.0, 4,
                radio_mode="USB", manual_radio_mode=True,
                receiver_type="fmdx",
            )
            loaded = ui.load_remembered_view(path)

        self.assertEqual(loaded["receiver_type"], "fmdx")
        self.assertEqual(loaded["freq_khz"], 101_700.0)

    def test_constellation_excludes_fmdx_receivers(self):
        center = {"server": "kiwi", "lat": 0, "lon": 0, "receiver_type": "kiwi"}
        fmdx_receiver = {"server": "fm", "lat": 1, "lon": 1, "receiver_type": "fmdx"}

        listeners, scouts = ui.choose_constellation(center, [center, fmdx_receiver], {})

        self.assertEqual([item["server"] for item in listeners], ["kiwi"])
        self.assertEqual(scouts, [])

    def test_worker_dispatch_uses_explicit_receiver_type(self):
        self.assertEqual(ui.receiver_worker_protocol("fmdx"), "fmdx")
        self.assertEqual(ui.receiver_worker_protocol("kiwi"), "kiwi")
        self.assertEqual(ui.receiver_worker_protocol(None), "kiwi")

    def test_fmdx_decoder_command_requests_stereo_pcm(self):
        command = ui.fmdx_decoder_command()

        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("mp3", command)
        self.assertEqual(command[-3:], ["-ac", "2", "pipe:1"])

    def test_fmdx_mode_family_is_server_controlled_but_step_remains_available(self):
        previous_progress = ui.LCD_RADIO_DRAWER_PROGRESS
        ui.LCD_RADIO_DRAWER_PROGRESS = 1.0
        self.addCleanup(setattr, ui, "LCD_RADIO_DRAWER_PROGRESS", previous_progress)
        _family, _modes, mode_box = next(iter(ui.radio_mode_layout()))
        mode_x = (mode_box[0] + mode_box[2]) / 2
        mode_y = (mode_box[1] + mode_box[3]) / 2
        step_hz, step_box = next(iter(ui.radio_step_options()))
        step_x = (step_box[0] + step_box[2]) / 2
        step_y = (step_box[1] + step_box[3]) / 2

        self.assertEqual(
            ui.radio_option_at(mode_x, mode_y, receiver_type="fmdx"),
            ("fmdx_action", "scan_start"),
        )
        self.assertEqual(
            ui.radio_option_at(step_x, step_y, receiver_type="fmdx"),
            ("step", step_hz),
        )

    def test_fmdx_drawer_exposes_bounded_preset_and_scan_actions(self):
        previous_progress = ui.LCD_RADIO_DRAWER_PROGRESS
        ui.LCD_RADIO_DRAWER_PROGRESS = 1.0
        self.addCleanup(setattr, ui, "LCD_RADIO_DRAWER_PROGRESS", previous_progress)
        stations = (
            {"frequency_khz": 99_500.0, "name": "A"},
            {"frequency_khz": 101_700.0, "name": "B"},
        )
        controls = list(ui.fmdx_control_layout(stations, scan_active=False))

        self.assertEqual([control[0] for control in controls], ["preset_previous", "preset_next", "scan_start"])
        for expected, box in controls:
            x = (box[0] + box[2]) / 2
            y = (box[1] + box[3]) / 2
            self.assertEqual(
                ui.radio_option_at(x, y, receiver_type="fmdx", fmdx_stations=stations),
                ("fmdx_action", expected),
            )

    def test_fmdx_scan_request_is_generation_scoped(self):
        server = self.register_fmdx()
        state = self.state()
        state.set_server(server, receiver_type="fmdx")
        generation = state.snapshot()[-1]

        request = state.request_fmdx_scan(True, generation)
        self.assertTrue(request[0])
        self.assertGreater(request[1], 0)
        self.assertEqual(state.fmdx_scan_request_snapshot(), request)
        self.assertIsNone(state.request_fmdx_scan(False, generation - 1))

    def test_fmdx_status_tracks_rds_and_signal_for_current_generation(self):
        server = self.register_fmdx()
        state = self.state()
        state.set_server(server, receiver_type="fmdx")
        generation = state.snapshot()[-1]

        station = state.update_fmdx_status(
            {"freq": 101.7, "ps": "RADIO 1", "pi": "1234", "sig": 65.0},
            generation,
        )

        self.assertEqual(station["frequency_khz"], 101_700.0)
        self.assertEqual(state.fmdx_status_snapshot()["ps"], "RADIO 1")
        self.assertEqual(state.fmdx_stations_snapshot()[0]["name"], "RADIO 1")
        self.assertIsNone(state.update_fmdx_status({"ps": "OLD"}, generation - 1))

    def test_decoded_fmdx_pcm_feeds_audio_analysis_and_waterfall(self):
        server = self.register_fmdx()
        state = self.state()
        state.set_server(server, receiver_type="fmdx")
        generation = state.snapshot()[-1]
        pcm = struct.pack("<8h", 1000, -1000, 2000, 0, 3000, 1000, 4000, 2000)
        player_calls = []

        class Player:
            def submit(self, audio, silence=False):
                player_calls.append((audio, silence))

        class Analyzer:
            def feed(self, mono):
                self.mono = mono
                return (bytes([0, 64, 128, 255]),)

        analyzer = Analyzer()
        line_queue = queue.Queue(maxsize=2)
        callsign_queue = queue.Queue(maxsize=2)
        args = SimpleNamespace(audio_rate=12_000, wf_row_pixels=1)

        accepted = ui.publish_fmdx_pcm(
            pcm, args, state, generation, Player(), analyzer, line_queue,
            transcript_queue=None, callsign_queue=callsign_queue,
        )

        self.assertTrue(accepted)
        self.assertEqual(player_calls, [(pcm, False)])
        self.assertEqual(len(analyzer.mono), 8)
        self.assertEqual(len(callsign_queue.get_nowait()), 2)
        line, center_khz, span_khz = line_queue.get_nowait()
        self.assertEqual(line.size, (ui.WF_TEX_W, 1))
        self.assertEqual(center_khz, state.snapshot()[1])
        self.assertEqual(span_khz, fmdx.AUDIO_WATERFALL_SPAN_HZ / 1000.0)

    def test_fmdx_scan_silences_live_playback(self):
        server = self.register_fmdx()
        state = self.state()
        state.set_server(server, receiver_type="fmdx")
        generation = state.snapshot()[-1]
        state.request_fmdx_scan(True, generation)
        calls = []

        class Player:
            def submit(self, audio, silence=False):
                calls.append((audio, silence))

        class Analyzer:
            def feed(self, mono):
                return ()

        ui.publish_fmdx_pcm(
            b"\x01\x00\x02\x00", SimpleNamespace(audio_rate=12_000, wf_row_pixels=1),
            state, generation, Player(), Analyzer(), queue.Queue(maxsize=1),
        )

        self.assertEqual(calls, [(b"\x00\x00\x00\x00", True)])

    def test_fmdx_decoder_forwards_pcm_and_closes_process(self):
        class Sink(io.BytesIO):
            def close(self):
                self.was_closed = True

        class Process:
            def __init__(self):
                self.stdin = Sink()
                self.stdout = io.BytesIO(b"decoded pcm")
                self.terminated = False

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.terminated = True

        process = Process()
        calls = []
        decoder = ui.FmdxMp3Decoder(calls.append, process_factory=lambda *args, **kwargs: process)
        decoder.feed(b"mp3")
        decoder.close()

        self.assertEqual(calls, [b"decoded pcm"])
        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
