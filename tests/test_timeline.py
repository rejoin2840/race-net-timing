"""Unit + integration tests for src/timeline.py — historical reconstruction and
on-demand catch-up recap (WYWA v2, Phase 0).

Two layers:
  • a synthetic in-memory sqlite fixture (3 cars scripted through a lead change,
    a pit stop, a caution, and an RC penalty line + a 4th car appearing mid-race)
    exercises reconstruct()/recap()/budget_by_class() deterministically
  • an integration check against data/replay_rolex.db (real archived race data)
    proves reconstruction holds up against a real predictions table

Run (no pytest dependency — matches the other tests in this dir):
  ./venv/bin/python tests/test_timeline.py
"""
import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import catchup  # noqa: E402
import timeline  # noqa: E402

OID = "T"
T0 = 1_000_000          # ms — arbitrary session-start epoch for the fixture
T1 = T0                  # first mark: lap 5
T2 = T0 + 60_000         # second mark: lap 6 (predictor.PREDICT_EVERY_S cadence)


# ── fixture ──────────────────────────────────────────────────────────────────
def _build_db() -> sqlite3.Connection:
    """3 cars in class GTP scripted through one event each (car 1: lead change,
    car 2: RC drive-through penalty, car 3: pit stop + a lap down) plus a caution
    that opens between T1 and T2, and a 4th car with no data until T2 (mid-race
    arrival). Minimal ad-hoc schema (mirrors db.py's real column set for just the
    tables timeline.py reads) rather than db.RaceDB, whose ingest methods expect
    live Al Kamel-shaped dicts, not synthetic rows."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE predictions (
            session_oid TEXT, ts INTEGER, session_lap INTEGER, car_number TEXT,
            car_class TEXT, track_position INTEGER, pos_in_class INTEGER,
            laps INTEGER, stops INTEGER);
        CREATE TABLE race_control (
            session_oid TEXT, ts INTEGER, message TEXT, detected_at TEXT);
        CREATE TABLE caution_periods (
            session_oid TEXT, period_num INTEGER, start_lap INTEGER, start_ts TEXT,
            end_lap INTEGER, end_ts TEXT, duration_s INTEGER, cause TEXT);
        CREATE TABLE driver_changes (
            session_oid TEXT, car_number TEXT, seq INTEGER, driver TEXT,
            session_lap INTEGER);
        CREATE TABLE session_entry (
            session_oid TEXT, car_number TEXT, name TEXT, team TEXT);
        CREATE TABLE pit_events (
            session_oid TEXT, car_number TEXT, pit_entry_hour_ms INTEGER);
        CREATE TABLE sessions (session_oid TEXT, last_seen TEXT);
    """)

    def pred(ts, car, cls, pos, laps, stops):
        conn.execute(
            """INSERT INTO predictions
                 (session_oid, ts, session_lap, car_number, car_class,
                  track_position, pos_in_class, laps, stops)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (OID, ts, laps, car, cls, pos, pos, laps, stops))

    # T1 (lap 5): car1 P2, car2 P1 (leader), car3 P3
    pred(T1, "1", "GTP", 2, 5, 0)
    pred(T1, "2", "GTP", 1, 5, 0)
    pred(T1, "3", "GTP", 3, 5, 0)

    # T2 (lap 6): car1 takes the lead (P1), car2 drops to P2 (1-spot swap, below
    # MIN_POS_MOVE so no spurious GAIN/LOSS), car3 pits and is a lap down (laps
    # stayed at 5 while the class leader reached 6)
    pred(T2, "1", "GTP", 1, 6, 0)
    pred(T2, "2", "GTP", 2, 6, 0)
    pred(T2, "3", "GTP", 3, 5, 1)
    # car 4 has no data at T1 — arrives mid-race
    pred(T2, "4", "GTP", 4, 6, 0)

    # RC penalty on car 2, logged between T1 and T2 (excluded from the T1 replay,
    # included by T2) — outranks the harmless 1-spot swap, so car2's event is PENALTY
    conn.execute(
        """INSERT INTO race_control (session_oid, ts, message, detected_at)
             VALUES (?,?,?,?)""",
        (OID, T0 + 30_000, "CAR 2 - PENALTY - DRIVE THROUGH - PIT LANE SPEEDING", ""))

    # caution opens at lap 6 (after T1's lap 5, by T2's lap 6) and is still running
    conn.execute(
        """INSERT INTO caution_periods
             (session_oid, period_num, start_lap, start_ts, end_lap, end_ts,
              duration_s, cause)
           VALUES (?,?,?,?,?,?,?,?)""",
        (OID, 1, 6, "", None, None, None, "FCY"))

    # car1: driver change exactly at lap 6 — visible at T2, not yet at T1
    conn.execute(
        """INSERT INTO driver_changes (session_oid, car_number, seq, driver, session_lap)
             VALUES (?,?,?,?,?)""", (OID, "1", 1, "A. Baseline", 0))
    conn.execute(
        """INSERT INTO driver_changes (session_oid, car_number, seq, driver, session_lap)
             VALUES (?,?,?,?,?)""", (OID, "1", 2, "A. Replacement", 6))
    # car2/car3 have no driver_changes rows — fall back to session_entry.name
    conn.execute(
        """INSERT INTO session_entry (session_oid, car_number, name, team)
             VALUES (?,?,?,?)""", (OID, "2", "B. Driver", "Team B"))
    conn.execute(
        """INSERT INTO session_entry (session_oid, car_number, name, team)
             VALUES (?,?,?,?)""", (OID, "3", "C. Driver", "Team C"))

    conn.commit()
    return conn


# ── reconstruct() ────────────────────────────────────────────────────────────
def test_reconstruct_before_first_data_is_sparse_and_empty():
    conn = _build_db()
    snap = timeline.reconstruct(conn, OID, T0 - 10_000_000)
    assert snap.sparse is True
    assert snap.cars == {}
    assert snap.lap == 0


def test_reconstruct_picks_latest_row_per_car():
    conn = _build_db()
    old = timeline.reconstruct(conn, OID, T1)
    new = timeline.reconstruct(conn, OID, T2)
    assert old.lap == 5 and new.lap == 6
    assert old.cars["2"].pos == 1        # car2 leads at T1
    assert new.cars["1"].pos == 1        # car1 leads at T2
    assert not old.sparse and not new.sparse


def test_reconstruct_car_appearing_mid_race():
    conn = _build_db()
    old = timeline.reconstruct(conn, OID, T1)
    new = timeline.reconstruct(conn, OID, T2)
    assert "4" not in old.cars
    assert "4" in new.cars


def test_reconstruct_laps_down_from_class_leader():
    conn = _build_db()
    new = timeline.reconstruct(conn, OID, T2)
    assert new.cars["3"].laps_down == 1   # leader (car1/car2) on lap 6, car3 on lap 5
    assert new.cars["1"].laps_down == 0


def test_reconstruct_driver_resolution_over_time():
    conn = _build_db()
    old = timeline.reconstruct(conn, OID, T1)
    new = timeline.reconstruct(conn, OID, T2)
    assert old.cars["1"].driver == "A. Baseline"       # change at lap 6 not yet in effect
    assert new.cars["1"].driver == "A. Replacement"
    assert new.cars["2"].driver == "B. Driver"          # fallback: session_entry.name
    assert new.cars["3"].driver == "C. Driver"


def test_reconstruct_caution_open_at_t():
    conn = _build_db()
    old = timeline.reconstruct(conn, OID, T1)
    new = timeline.reconstruct(conn, OID, T2)
    assert old.caution_count == 0                       # opens after lap 5
    assert new.caution_count == 1
    assert new.cautions == [(6, None, None)]
    assert new.flag == "FCY"                            # still open at lap 6


def test_reconstruct_penalty_replayed_from_race_control():
    conn = _build_db()
    old = timeline.reconstruct(conn, OID, T1)
    new = timeline.reconstruct(conn, OID, T2)
    assert old.cars["2"].penalty_s == 0.0                # RC line logged after T1
    assert new.cars["2"].penalty_s == 22.0               # DRIVE_THROUGH_S
    assert new.cars["2"].dq is False


def test_reconstruct_is_deterministic():
    conn = _build_db()
    a = timeline.reconstruct(conn, OID, T2)
    b = timeline.reconstruct(conn, OID, T2)
    assert a == b


# ── recap() ──────────────────────────────────────────────────────────────────
def test_recap_surfaces_lead_change_pit_caution_and_penalty():
    conn = _build_db()
    events = timeline.recap(conn, OID, T1, T2)
    by_car = {e.car: e for e in events if e.car}
    assert by_car["1"].tone == catchup.LEAD
    assert by_car["2"].tone == catchup.PENALTY          # outranks the 1-spot swap
    assert by_car["3"].tone == catchup.PIT
    cautions = [e for e in events if e.tone == catchup.CAUTION]
    assert len(cautions) == 1 and "L6" in cautions[0].text


def test_recap_class_filter_keeps_only_matching_and_field_wide():
    conn = _build_db()
    events = timeline.recap(conn, OID, T1, T2, cls="GTP")
    assert all(e.cls == "GTP" or e.cls is None for e in events)
    events_other = timeline.recap(conn, OID, T1, T2, cls="LMP2")
    assert all(e.cls is None for e in events_other)      # only the caution survives


def test_recap_is_deterministic():
    conn = _build_db()
    a = timeline.recap(conn, OID, T1, T2)
    b = timeline.recap(conn, OID, T1, T2)
    assert a == b


# ── budget_by_class() ────────────────────────────────────────────────────────
def test_budget_by_class_caps_per_class_keeps_field_wide():
    events = [
        catchup.Event(tone=catchup.CAUTION, text="caution", car=None, cls=None, rank=70),
        catchup.Event(tone=catchup.GAIN, text="a", car="1", cls="GTP", rank=50),
        catchup.Event(tone=catchup.GAIN, text="b", car="2", cls="GTP", rank=49),
        catchup.Event(tone=catchup.GAIN, text="c", car="3", cls="GTP", rank=48),
        catchup.Event(tone=catchup.GAIN, text="d", car="4", cls="LMP2", rank=47),
    ]
    out = timeline.budget_by_class(events, 2)
    assert out[0].tone == catchup.CAUTION               # field-wide always kept
    gtp = [e for e in out if e.cls == "GTP"]
    assert len(gtp) == 2 and {e.car for e in gtp} == {"1", "2"}   # highest-ranked 2
    assert any(e.cls == "LMP2" for e in out)            # under its own budget, kept


# ── hour_marks() ─────────────────────────────────────────────────────────────
def test_hour_marks_empty_when_no_data():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE predictions (session_oid TEXT, ts INTEGER)")
    assert timeline.hour_marks(conn, "nope") == []


def test_hour_marks_span_start_to_end():
    conn = _build_db()
    marks = timeline.hour_marks(conn, OID, every_s=30)   # 30s "hours" over a 60s fixture
    assert marks == [T0 + 30_000, T0 + 60_000]


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_duration_parsing():
    assert timeline._parse_duration_s("2h") == 7200.0
    assert timeline._parse_duration_s("90m") == 5400.0
    assert timeline._parse_duration_s("45s") == 45.0
    assert timeline._parse_duration_s("30") == 30.0


# ── real-data integration (skips cleanly if the archive isn't present) ──────
def test_reconstruct_and_recap_against_real_replay_db():
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "replay_rolex.db")
    if not os.path.exists(db_path):
        return  # environment-dependent, not a failure
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    oid = timeline._default_session(conn)
    assert oid
    t0 = timeline._session_start_ms(conn, oid)
    assert t0 is not None

    snap = timeline.reconstruct(conn, oid, t0 + 3600 * 1000)
    assert len(snap.cars) > 5                            # plausible car count
    positions = [c.pos for c in snap.cars.values() if c.pos is not None]
    assert positions and all(p > 0 for p in positions)   # plausible in-class positions

    events = timeline.recap(conn, oid, t0 + 3600 * 1000, t0 + 2 * 3600 * 1000)
    assert len(events) > 0                               # a real race hour has changes


def test_cli_smoke_against_real_replay_db():
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "replay_rolex.db")
    if not os.path.exists(db_path):
        return
    script = os.path.join(os.path.dirname(__file__), "..", "src", "timeline.py")
    result = subprocess.run(
        [sys.executable, script, db_path, "--from", "1h", "--to", "2h"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Recap: lap" in result.stdout


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
