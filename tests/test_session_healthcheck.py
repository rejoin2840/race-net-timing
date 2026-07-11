"""Unit tests for src/session_healthcheck.py — the post-session data-quality gate.

Regression anchor: WEC FP3 / São Paulo quali 2026-07-11 captured ZERO
lap_history rows and all-NULL overall_position, yet PASSed because the check
only looked at entries/standings row existence — which bootstrap alone
creates. An empty-but-connected capture must be FAIL (or WARN), never PASS.

Run (no pytest dependency — matches the other tests in this dir):
  ./venv/bin/python tests/test_session_healthcheck.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import db as db_mod            # noqa: E402
import session_healthcheck     # noqa: E402


def _now_iso(minutes_ago: float = 0.0) -> str:
    return (datetime.utcnow() - timedelta(minutes=minutes_ago)).isoformat() + "Z"


def _make_db(path: str, *, cars: int, positions: bool, laps: int,
             standings_age_min: float = 1.0, current_lap=None,
             skip_standings: bool = False) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(db_mod._SCHEMA)
    oid = "wec#2026#test#fp3"
    conn.execute(
        "INSERT INTO sessions (session_oid, series, first_seen, last_seen) "
        "VALUES (?,?,?,?)", (oid, "wec", _now_iso(120), _now_iso(1)))
    if current_lap is not None:
        conn.execute(
            "INSERT INTO session_status (session_oid, current_lap, is_running, "
            "is_finished, updated_at) VALUES (?,?,1,0,?)",
            (oid, current_lap, _now_iso(1)))
    for i in range(cars):
        car = str(i + 1)
        conn.execute(
            "INSERT INTO session_entry (session_oid, car_number, name, team, "
            "updated_at) VALUES (?,?,?,?,?)",
            (oid, car, f"Driver {car}", f"Team {car}", _now_iso(90)))
        if not skip_standings:
            conn.execute(
                "INSERT INTO standings_current (session_oid, car_number, "
                "overall_position, updated_at) VALUES (?,?,?,?)",
                (oid, car, (i + 1) if positions else None,
                 _now_iso(standings_age_min)))
        for lap in range(1, laps + 1):
            conn.execute(
                "INSERT INTO lap_history (session_oid, car_number, lap_number, "
                "lap_time_ms, recorded_at) VALUES (?,?,?,?,?)",
                (oid, car, lap, 100_000 + lap, _now_iso(standings_age_min)))
    conn.commit()
    conn.close()


def _run(path: str, **kwargs) -> str:
    return session_healthcheck.check(path, "wec", None, **kwargs)


def _verdict(report: str) -> str:
    first = report.splitlines()[0]
    return first.split("[", 1)[1].split("]", 1)[0]


passed = failed = 0


def _case(name: str, cond: bool, detail: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")


def main():
    tmp = tempfile.mkdtemp(prefix="hc_test_")

    # 1. the FP3/quali bug: bootstrap-only capture (entries + standings rows,
    #    NULL positions, zero laps) must FAIL now that activity is expected
    p = os.path.join(tmp, "bootstrap_only.db")
    _make_db(p, cars=30, positions=False, laps=0)
    r = _run(p)
    _case("bootstrap-only capture FAILs", _verdict(r) == "FAIL", r)
    _case("  …names missing lap_history", "lap_history" in r, r)
    _case("  …names missing positions", "overall_position" in r, r)

    # 2. healthy capture: positions, laps, fresh updates → PASS
    p = os.path.join(tmp, "healthy.db")
    _make_db(p, cars=30, positions=True, laps=20, current_lap=20)
    r = _run(p)
    _case("healthy capture PASSes", _verdict(r) == "PASS", r)

    # 3. laps present but every position NULL → FAIL
    p = os.path.join(tmp, "no_positions.db")
    _make_db(p, cars=30, positions=False, laps=20, current_lap=20)
    r = _run(p)
    _case("all-NULL positions FAILs", _verdict(r) == "FAIL", r)

    # 4. good data but standings last touched 2h ago → WARN (capture died
    #    mid-session), not PASS and not FAIL
    p = os.path.join(tmp, "stale.db")
    _make_db(p, cars=30, positions=True, laps=20, standings_age_min=120,
             current_lap=20)
    r = _run(p)
    _case("stale standings WARNs", _verdict(r) == "WARN", r)

    # 5. --idle-ok semantics: ad-hoc run outside a window, feed idle
    #    (no current_lap), bootstrap-only DB is fine
    p = os.path.join(tmp, "idle.db")
    _make_db(p, cars=30, positions=False, laps=0)
    r = _run(p, expect_activity=False)
    _case("idle bootstrap-only PASSes with expect_activity=False",
          _verdict(r) == "PASS", r)

    # 6. …but if the feed says cars ran (current_lap > 0), empty capture
    #    fails even without expected activity
    p = os.path.join(tmp, "idle_but_ran.db")
    _make_db(p, cars=30, positions=False, laps=0, current_lap=15)
    r = _run(p, expect_activity=False)
    _case("current_lap>0 forces FAIL even when idle-ok", _verdict(r) == "FAIL", r)

    # 7. never connected at all (no standings rows) still FAILs
    p = os.path.join(tmp, "never_connected.db")
    _make_db(p, cars=30, positions=False, laps=0, skip_standings=True)
    r = _run(p)
    _case("no standings rows FAILs", _verdict(r) == "FAIL", r)

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
