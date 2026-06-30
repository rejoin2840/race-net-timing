"""
predictor.py — prediction logging foundation.

Captures what the calculator predicted at each point in a race so the evaluator
can later score it against what actually happened. Compute-light by design:
the caller throttles to one snapshot per car per PREDICT_EVERY_S (default 60s),
and logging only runs during races. Storage ≈ cars × race_minutes rows.

This module only WRITES the predictions table. Scoring lives in evaluator.py.
"""

import sqlite3
from typing import Optional

PREDICT_EVERY_S = 60          # min seconds between logged snapshots (per the caller)

_CREATE = """
CREATE TABLE IF NOT EXISTS predictions (
    session_oid TEXT, ts INTEGER, session_lap INTEGER, car_number TEXT,
    car_class TEXT, track_position INTEGER, pos_in_class INTEGER, laps INTEGER,
    stops INTEGER, net_position INTEGER, net_gap_ms REAL, est_stops_left INTEGER,
    next_stop_ms REAL, next_stop_std_ms REAL, owes_dc INTEGER, catching TEXT,
    catch_in_laps REAL, projected_finish INTEGER,
    PRIMARY KEY (session_oid, ts, car_number)
);
"""


def ensure(conn: sqlite3.Connection) -> None:
    """Create the predictions table if the DB predates it (standalone safety)."""
    conn.execute(_CREATE)
    conn.commit()


def log_cycle(conn: sqlite3.Connection, oid: str, ctx, cars, ts_ms: int) -> int:
    """Write one prediction row per car for this cycle. Returns rows written."""
    rows = [
        (oid, ts_ms, ctx.current_lap, c.car_number, c.car_class,
         c.track_position, c.pos_in_class, c.laps, c.stops,
         c.net_position, c.net_gap_ms, c.est_stops_left,
         c.next_stop_ms, c.next_stop_std_ms,
         1 if c.owes_driver_change else 0, c.catching, c.catch_in_laps,
         c.projected_finish)
        for c in cars
    ]
    conn.executemany(
        """INSERT OR IGNORE INTO predictions
             (session_oid, ts, session_lap, car_number, car_class,
              track_position, pos_in_class, laps, stops,
              net_position, net_gap_ms, est_stops_left,
              next_stop_ms, next_stop_std_ms, owes_dc, catching,
              catch_in_laps, projected_finish)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    return len(rows)
