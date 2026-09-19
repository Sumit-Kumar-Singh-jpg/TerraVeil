from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
import app as application


class Stage3BaselineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            database,
            "DB_NAME",
            str(Path(self.directory.name) / "stage3.db"),
        )
        self.db_patch.start()

        database.init_db()
        database.set_mode("REAL")
        self.client = application.app.test_client()

        application.alert_state.update(
            status="NORMAL",
            sirens_active=False,
            alert_kind=None,
            demo_armed=False,
            collapse_latched=False,
            geophone_event=None,
        )
        for siren in application.alert_state["siren_zones"]:
            siren["status"] = "OFF"

    def tearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    def send_ug(self, sequence, roll, vibration=0):
        response = self.client.post(
            "/api/telemetry",
            json=dict(
                host_id="HOST-01",
                zone_id="ZONE-A",
                node_id="UG-01",
                sequence=sequence,
                roll=roll,
                pitch=0.0,
                vibration=vibration,
                soil=2000,
                rssi=-40,
                snr=8.0,
            ),
        )
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_set_zero_relative_baseline_and_red_latch(self):
        result = self.client.post("/api/demo/baseline")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(application.alert_state["demo_armed"])

        # Arbitrary resting angle becomes the neutral reference.
        for sequence in range(1, 6):
            self.send_ug(sequence, roll=25.0)

        latest = database.get_latest_readings("REAL")[0]
        self.assertEqual(latest["baseline_ready"], 1)
        self.assertEqual(latest["risk_level"], "LOW")

        self.send_ug(6, roll=25.1)
        latest = database.get_latest_readings("REAL")[0]
        self.assertLess(abs(latest["tilt_x"]), 0.5)
        self.assertEqual(latest["anomaly"], 0)

        # Severe movement needs persistence.
        self.send_ug(7, roll=31.0)
        self.assertEqual(
            database.get_latest_readings("REAL")[0]["risk_level"],
            "MEDIUM",
        )

        self.send_ug(8, roll=31.0)
        latest = database.get_latest_readings("REAL")[0]
        self.assertEqual(latest["risk_level"], "CRITICAL")
        self.assertEqual(application.alert_state["status"], "RED_ALERT")

        # ACK preserves the session/baseline and disarms this completed cycle.
        session_before = {
            n["node_id"]: n["session_id"]
            for n in database.get_nodes("REAL")
        }

        ack = self.client.post("/api/alert/reset")
        self.assertEqual(ack.status_code, 200)
        self.assertFalse(application.alert_state["demo_armed"])

        session_after = {
            n["node_id"]: n["session_id"]
            for n in database.get_nodes("REAL")
        }
        self.assertEqual(session_before, session_after)

        # Remaining tilted cannot retrigger until a fresh SET ZERO.
        self.send_ug(9, roll=31.0)
        self.assertEqual(application.alert_state["status"], "NORMAL")

        history_before = len(
            database.get_history("UG-01", source="REAL")
        )

        # New cycle: the current 31-degree position is now allowed to become zero.
        self.client.post("/api/demo/baseline")
        for sequence in range(10, 15):
            self.send_ug(sequence, roll=31.0)

        latest = database.get_latest_readings("REAL")[0]
        self.assertEqual(latest["baseline_ready"], 1)
        self.assertEqual(latest["risk_level"], "LOW")

        # Historical rows were not deleted.
        self.assertGreater(
            len(database.get_history("UG-01", source="REAL")),
            history_before,
        )


if __name__ == "__main__":
    unittest.main()
