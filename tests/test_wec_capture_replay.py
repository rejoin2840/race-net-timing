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
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import db as dbmod  # noqa: E402
from wec_live import (WecLiveClient, iter_capture, replay_capture,  # noqa: E402
                      replay_predict, _reanchor_clock)

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

    def test_survives_gzip_container_without_trailer(self):
        """A hard-killed capture never runs GzipFile.close(), so the file has
        no end-of-stream trailer at all (not just a torn last JSON line) —
        the real SP 2026 race capture hit exactly this. Reproduce it: write
        via sync-flush (same as _record_frame) and never close."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl.gz", delete=False) as tf:
            path = tf.name
        try:
            gz = gzip.open(path, "wb")
            frames = [
                {"channel": "ranks", "data": {"carNumber": "1"}, "ts": 1000},
                {"channel": "laps", "data": {"carNumber": "2"}, "ts": 2000},
            ]
            for fr in frames:
                gz.write((json.dumps(fr) + "\n").encode("utf-8"))
                gz.flush()  # Z_SYNC_FLUSH, same as _record_frame — no close()
            del gz  # drop the handle without close(): no gzip trailer written

            out = list(iter_capture(path))
            self.assertEqual(len(out), len(frames))
            self.assertEqual(out[0][0], "ranks")
            self.assertEqual(out[1][0], "laps")
        finally:
            os.unlink(path)

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

    def test_pit_duration_uses_recorded_frame_time_not_wall_clock(self):
        """Regression: _handle_pit_in/_handle_pit_out used to stamp durations
        with time.time(), which is correct live but garbage on replay (a
        155k-frame capture dispatches in ~3s of real time, collapsing every
        stop to single-digit milliseconds). The fixture's pit-in/pit-out
        frames for PIT_CAR are exactly 1000ms apart by recorded ts — replay
        must reproduce that duration, not the near-zero wall-clock gap."""
        row = self._query(
            "SELECT stop_duration_ms FROM pit_events "
            "WHERE car_number=? AND stop_number=1", PIT_CAR)[0]
        self.assertEqual(row["stop_duration_ms"], 1000)

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


def _run_replay_predict(db_path, cadence_s=5):
    """replay_predict the fixture into db_path; returns (client, result)."""
    client = WecLiveClient(db_path="", no_db=True)
    client.db = dbmod.RaceDB(db_path)
    res = replay_predict(client, FIXTURE, cadence_s=cadence_s)
    return client, res


class TestReplayPredict(unittest.TestCase):
    """Phase-A contract: --replay-predict regenerates predictions offline,
    deterministically, with frame-ts timestamps — never against the prod DB."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        # two independent runs of the same capture, for the determinism check
        cls.c1, cls.res1 = _run_replay_predict(
            os.path.join(cls._tmp.name, "a.db"))
        cls.c2, cls.res2 = _run_replay_predict(
            os.path.join(cls._tmp.name, "b.db"))

    @classmethod
    def tearDownClass(cls):
        cls.c1.db.close()
        cls.c2.db.close()
        cls._tmp.cleanup()

    def _rows(self, client):
        # round the REAL columns to whole ms: analyse() calls time.time()
        # microseconds after the re-anchor, so sub-ms float jitter between
        # runs is expected and not a determinism failure
        return client.db.conn.execute(
            "SELECT session_oid, ts, session_lap, car_number, car_class, "
            "track_position, pos_in_class, laps, stops, net_position, "
            "CAST(ROUND(net_gap_ms) AS INTEGER) net_gap_ms, est_stops_left, "
            "CAST(ROUND(next_stop_ms) AS INTEGER) next_stop_ms, "
            "owes_dc, catching, "
            "CAST(ROUND(catch_in_laps*10) AS INTEGER) catch_dlaps, "
            "projected_finish "
            "FROM predictions ORDER BY ts, car_number").fetchall()

    def test_predictions_logged(self):
        self.assertGreater(self.res1["predictions"], 0)
        self.assertEqual(self.res1["frames"], N_FRAMES)
        self.assertEqual(self.res1["dispatch_errors"], 0)
        n = self.c1.db.conn.execute(
            "SELECT COUNT(*) c FROM predictions").fetchone()["c"]
        self.assertEqual(n, self.res1["predictions"])

    def test_one_row_per_car_per_cycle(self):
        for row in self.c1.db.conn.execute(
                "SELECT ts, COUNT(*) c FROM predictions GROUP BY ts"):
            self.assertEqual(row["c"], N_CARS)

    def test_cadence_throttles_cycles(self):
        """5s cadence over the fixture's 16s span → more than one cycle,
        far fewer than one per frame."""
        n_cycles = self.c1.db.conn.execute(
            "SELECT COUNT(DISTINCT ts) c FROM predictions").fetchone()["c"]
        self.assertGreater(n_cycles, 1)
        self.assertLess(n_cycles, N_FRAMES)

    def test_prediction_ts_is_frame_ts(self):
        """Rows are stamped with recorded frame time, not replay wall time."""
        frame_ts = {ts for _, _, ts in iter_capture(FIXTURE) if ts is not None}
        pred_ts = {r["ts"] for r in self.c1.db.conn.execute(
            "SELECT DISTINCT ts FROM predictions")}
        self.assertTrue(pred_ts)
        self.assertLessEqual(pred_ts, frame_ts)

    def test_deterministic_across_runs(self):
        r1, r2 = self._rows(self.c1), self._rows(self.c2)
        self.assertEqual(len(r1), len(r2))
        for a, b in zip(r1, r2):
            self.assertEqual(tuple(a), tuple(b))

    def test_requires_db(self):
        client = WecLiveClient(db_path="", no_db=True)
        with self.assertRaises(ValueError):
            replay_predict(client, FIXTURE)

    def test_cli_refuses_production_db(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        prod = os.path.join(root, "data", "race.db")
        proc = subprocess.run(
            [sys.executable, os.path.join(root, "src", "wec_live.py"),
             "--replay-predict", FIXTURE, "--db", prod],
            capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("refuses the production DB", proc.stderr)


class TestReanchorClock(unittest.TestCase):
    """_reanchor_clock must make calculator's `time.time() - start_time_s
    - stopped_s` reproduce elapsed-at-frame exactly (the replay clock trap)."""

    OID = "wec_test_1"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = dbmod.RaceDB(os.path.join(self._tmp.name, "clock.db"))

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _seed(self, start_time_s, stopped_s=None):
        self.db.conn.execute(
            "INSERT INTO session_status (session_oid, start_time_s, stopped_s)"
            " VALUES (?,?,?)", (self.OID, start_time_s, stopped_s))

    def _elapsed_as_calculator(self):
        st = self.db.conn.execute(
            "SELECT start_time_s, stopped_s FROM session_status "
            "WHERE session_oid=?", (self.OID,)).fetchone()
        return time.time() - st["start_time_s"] - (st["stopped_s"] or 0)

    def test_reanchors_from_recorded_start(self):
        real_start = 1751600000.0
        self._seed(real_start)
        frame_ts_ms = int((real_start + 100) * 1000)      # 100s into the race
        _reanchor_clock(self.db.conn, self.OID, frame_ts_ms, None)
        self.assertAlmostEqual(self._elapsed_as_calculator(), 100.0, delta=1.0)

    def test_red_flag_stopped_seconds_still_subtracted(self):
        """Re-anchoring uses wall elapsed; calculator subtracts stopped_s on
        top, yielding true green-running elapsed — pin that composition."""
        real_start = 1751600000.0
        self._seed(real_start, stopped_s=30)
        frame_ts_ms = int((real_start + 100) * 1000)
        _reanchor_clock(self.db.conn, self.OID, frame_ts_ms, None)
        self.assertAlmostEqual(self._elapsed_as_calculator(), 70.0, delta=1.0)

    def test_falls_back_to_first_frame_ts(self):
        self._seed(None)                                  # no session-clock yet
        fallback = 1751600000.0
        frame_ts_ms = int((fallback + 40) * 1000)
        _reanchor_clock(self.db.conn, self.OID, frame_ts_ms, fallback)
        self.assertAlmostEqual(self._elapsed_as_calculator(), 40.0, delta=1.0)

    def test_never_negative(self):
        self._seed(1751600000.0)
        _reanchor_clock(self.db.conn, self.OID, 1751599000000, None)  # ts < start
        self.assertAlmostEqual(self._elapsed_as_calculator(), 0.0, delta=1.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
