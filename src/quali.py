"""
quali.py — F1 knockout-qualifying (Q1/Q2/Q3) cut-line math + read model.

Separate from calculator.py on purpose: a knockout quali segment is ranked by
"best lap this segment," not net position / pit strategy / fuel — none of
RaceContext/CarAnalysis's race-shaped fields apply. This module is the quali
analogue: pure functions here have no DB/Qt dependency and are unit-tested
directly (tests/test_quali.py); analyse() is the read-side glue that turns
quali_standings/quali_status rows (written by replay_f1_quali.py, or a future
live adapter) into a sorted, cut-line-annotated list a UI can paint.
"""

import math
import sqlite3
from dataclasses import dataclass
from typing import Optional

SEGMENTS = ("Q1", "Q2", "Q3")

# The one constant across every F1 season regardless of grid size: the top 10
# always contest Q3 for pole. What varies is how the field narrows down to
# that 10 across the two earlier cuts (see advance_counts).
Q3_ADVANCE = 10

# Nominal segment lengths (seconds) — used for the countdown when a live feed
# doesn't carry segment-remaining-time explicitly.
SEGMENT_DURATION_S = {"Q1": 18 * 60, "Q2": 15 * 60, "Q3": 12 * 60}


def advance_counts(entries: int) -> dict:
    """{'Q1': n advancing out of Q1 into Q2, 'Q2': n advancing out of Q2 into Q3}.

    Derived from the field size rather than hardcoded to the classic 20-car
    "top 15 / top 10" split: the 2026 22-car grid actually cuts to 16/10 (FastF1
    data confirmed), so a fixed P16 assumption would silently mis-place the Q1
    cut line the moment the grid size changes again. Formula: total eliminated
    across the two cuts = entries - Q3_ADVANCE, split as evenly as possible with
    the extra (odd) elimination taken in Q1 — matches every 2026 round checked
    (22 → 16 → 10) and the classic 20-car case (20 → 15 → 10) exactly.
    """
    if entries <= Q3_ADVANCE:
        # a field this small never gets cut — everyone effectively fights for pole
        return {"Q1": entries, "Q2": entries}
    total_cut = entries - Q3_ADVANCE
    q1_cut = math.ceil(total_cut / 2)
    q2_cut = total_cut - q1_cut
    q1_advance = entries - q1_cut
    q2_advance = q1_advance - q2_cut
    return {"Q1": q1_advance, "Q2": q2_advance}


def next_segment(segment: str) -> Optional[str]:
    i = SEGMENTS.index(segment)
    return SEGMENTS[i + 1] if i + 1 < len(SEGMENTS) else None


@dataclass
class QualiCarState:
    car_number:  str
    best_lap_ms: Optional[int]
    last_lap_ms: Optional[int]
    laps:        int
    rank:        int             # live rank within the segment (1 = provisional fastest)
    advancing:   bool            # at/above the cut line as things stand right now


@dataclass
class QualiContext:
    session_oid:       str
    event:             str
    segment:           str                 # 'Q1' / 'Q2' / 'Q3'
    entries:           int                 # cars taking part this session
    advance_n:         Optional[int]       # None for Q3 — no cut, just the pole fight
    segment_elapsed_s: int
    segment_total_s:   int
    is_finished:       bool


def _load_context(conn: sqlite3.Connection, oid: str) -> Optional[QualiContext]:
    sess = conn.execute(
        "SELECT event_name FROM sessions WHERE session_oid=?", (oid,)).fetchone()
    st = conn.execute(
        "SELECT * FROM quali_status WHERE session_oid=?", (oid,)).fetchone()
    if st is None:
        return None
    segment = st["segment"] or "Q1"
    entries = st["entries"] or 0
    cuts = advance_counts(entries)
    advance_n = cuts.get(segment)   # None for Q3 (not a key in advance_counts)
    return QualiContext(
        session_oid=oid,
        event=(sess["event_name"] if sess else "?"),
        segment=segment,
        entries=entries,
        advance_n=advance_n,
        segment_elapsed_s=st["segment_elapsed_s"] or 0,
        segment_total_s=st["segment_total_s"] or SEGMENT_DURATION_S.get(segment, 0),
        is_finished=bool(st["is_finished"]),
    )


def analyse(conn: sqlite3.Connection, oid: str) -> tuple[Optional[QualiContext], list[QualiCarState]]:
    ctx = _load_context(conn, oid)
    if ctx is None:
        return None, []

    rows = conn.execute(
        """SELECT car_number, best_lap_ms, last_lap_ms, laps
             FROM quali_standings
            WHERE session_oid=? AND segment=?""",
        (oid, ctx.segment)).fetchall()

    # live rank: cars with a lap time sort by it (fastest first); cars still on
    # an out-lap / yet to set a time sort after, in car-number order (stable,
    # arbitrary — there's nothing to rank them by yet).
    timed = sorted((r for r in rows if r["best_lap_ms"]), key=lambda r: r["best_lap_ms"])
    untimed = sorted((r for r in rows if not r["best_lap_ms"]), key=lambda r: r["car_number"])
    ordered = timed + untimed

    cars = []
    for i, r in enumerate(ordered):
        rank = i + 1
        advancing = ctx.advance_n is None or rank <= ctx.advance_n
        cars.append(QualiCarState(
            car_number=r["car_number"], best_lap_ms=r["best_lap_ms"],
            last_lap_ms=r["last_lap_ms"], laps=r["laps"] or 0,
            rank=rank, advancing=advancing,
        ))
    return ctx, cars
