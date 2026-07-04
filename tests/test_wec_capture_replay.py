"""Capture-replay harness for the WEC live pipeline (Epic 8).

Feeds tests/fixtures/wec_capture_sample.jsonl.gz — the REAL Griiip bootstrap
captured 2026-07-03 plus live-style frames built from its real rows — through
wec_live's full parse/dispatch path into a real RaceDB, then asserts on what
landed in the database.

This is also the race-week Commit-5 workflow: record FP1 with --record, replay
the file with `wec_live.py --replay F.jsonl.gz --db scratch.db`, and iterate
field mappings offline. Once a real WEC capture exists, promote its frames
into the fixture (regenerate; the assertions below stay the contract).

Run (no pytest dependency):
  ./venv/bin/python tests/test_wec_capture_replay.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import db as dbmod  # noqa: E402
from wec_live import WecLiveClient, iter_capture, replay_capture  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "wec_capture_sample.jsonl.gz")

# ground truth baked into the fixture (see scratch generator / fixture header)
N_FRAMES = 17          # whole frames; the torn trailing line is not one of them
N_CARS = 23            # entries/standings from the real bootstrap
PIT_CAR = "4"
LAP_CAR = "18"
BEST_LAP_MS = 33799    # the valid faster synthesized lap (beats the real
                       # bootstrap best of 34099 by 300ms)
BEST_LAP_NUM = 37      # its lap number (an invalid faster one must NOT win)


class TestCaptureReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.client = WecLiveClient(db_path="", no_db=True)
        cls.client.db = dbmod.RaceDB(os.path.join(cls._tmp.name, "replay.db"))
        cls.n_frames, cls.n_errors = replay_capture(cls.client, FIXTURE)

    @classmethod
    def tearDownClass(cls):
        cls.client.db.close()
        cls._tmp.cleanup()

    def _query(self, sql, *args):
        return self.client.db.conn.execute(sql, args).fetchall()

    # ── transport-level contract ─────────────────────────────────────────
    def test_all_frames_replayed_without_dispatch_errors(self):
        self.assertEqual(self.n_frames, N_FRAMES)
        self.assertEqual(self.n_errors, 0)

    def test_torn_trailing_line_is_skipped(self):
        # iterating the raw file again must also survive the torn line
        self.assertEqual(len(list(iter_capture(FIXTURE))), N_FRAMES)

    # ── what landed in the DB ────────────────────────────────────────────
    def test_session_row_written_as_wec(self):
        rows = self._query("SELECT series FROM sessions")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["series"], "wec")

    def test_all_entries_written(self):
        self.assertEqual(
            self._query("SELECT COUNT(*) c FROM session_entry")[0]["c"], N_CARS)

    def test_all_standings_written(self):
        self.assertEqual(
            self._query("SELECT COUNT(*) c FROM standings_current")[0]["c"],
            N_CARS)

    def test_nested_items_ranks_unwrapped(self):
        row = self._query("SELECT overall_position FROM standings_current "
                          "WHERE car_number='21'")[0]
        self.assertEqual(row["overall_position"], 1)

    def test_lap_history_populated(self):
        n = self._query("SELECT COUNT(*) c FROM lap_history "
                        "WHERE car_number=?", LAP_CAR)[0]["c"]
        self.assertGreater(n, 0)

    def test_live_best_lap_tracked_and_invalid_excluded(self):
        row = self._query("SELECT best_lap_ms, best_lap_num "
                          "FROM standings_current WHERE car_number=?",
                          LAP_CAR)[0]
        self.assertEqual(row["best_lap_ms"], BEST_LAP_MS)
        self.assertEqual(row["best_lap_num"], BEST_LAP_NUM)

    def test_first_pit_stop_recorded(self):
        rows = self._query("SELECT stop_number FROM pit_events "
                           "WHERE car_number=?", PIT_CAR)
        self.assertEqual([r["stop_number"] for r in rows], [1])

    def test_session_status_fields_survive_partial_updates(self):
        """Flag updates after the session-length frame must not null the
        length fields (the status-accumulator contract)."""
        row = self._query("SELECT current_flag, final_type, final_laps, "
                          "start_time_s, is_finished FROM session_status")[0]
        self.assertEqual(row["current_flag"], "CH")
        self.assertEqual(row["final_type"], "BY_LAPS")
        self.assertEqual(row["final_laps"], 40)
        self.assertIsNotNone(row["start_time_s"])
        self.assertEqual(row["is_finished"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
