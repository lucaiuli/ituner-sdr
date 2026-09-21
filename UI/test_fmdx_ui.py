import tempfile
import unittest
from pathlib import Path
import sys


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


if __name__ == "__main__":
    unittest.main()
