"""
replay_f1_quali.py — drive a FastF1 historical KNOCKOUT QUALIFYING session
(Q1/Q2/Q3) through quali.py's read model, for the cut-line board.

Batch mode only (no --stream yet — quali segments are short, ~15-20 min, and
this is the replay-first MVP; a live adapter is Phase 3 territory alongside
the race SignalR feed).

    python src/replay_f1_quali.py 2026 8 Q [--db data/f1_quali_replay.db]
                                            [--segment Q1] [--progress 0.6]

--segment stops the replay after that segment finishes (default: all three).
--progress replays only that fraction of the target segment's laps, so you
can render a still-in-progress cut line rather than only the final state.

Field mapping (FastF1 → quali_standings), mirrors replay_f1.py's conventions:
  session.session_status Started/Finished  → Q1/Q2/Q3 time windows
  LapTime (per lap, chronological)         → best_lap_ms / last_lap_ms / laps
"""

import argparse
import os
import sqlite3
import sys

try:
    import fastf1
except ImportError:
    sys.exit("fastf1 not installed — pip install fastf1")

import quali
from db import RaceDB
from replay_f1 import _td_ms, _make_oid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _segment_windows(session) -> list:
    """[(label, start_ms, finish_ms)] for Q1/Q2/Q3, sliced off session_status's
    Started→Finished pairs. Falls back to three equal thirds of the session if
    the status log doesn't have the expected clean pairs (e.g. an unusual
    red-flag-heavy session) — good enough for a replay-first MVP."""
    ts = session.session_status
    starts = [_td_ms(r["Time"]) for _, r in ts.iterrows() if r["Status"] == "Started"]
    finishes = [_td_ms(r["Time"]) for _, r in ts.iterrows() if r["Status"] == "Finished"]
    if len(starts) >= 3 and len(finishes) >= 3:
        bounds = list(zip(starts[:3], finishes[:3]))
    else:
        total = _td_ms(ts["Time"].max()) if ts is not None and not ts.empty else 0
        third = total // 3
        bounds = [(0, third), (third, 2 * third), (2 * third, total)]
    return list(zip(quali.SEGMENTS, bounds))


def _ingest_segment(db, label: str, seg_laps, n_entries: int, progress: float) -> None:
    """Replay one segment's laps in chronological order, writing a DB snapshot
    after each lap completes. Segments are short (a few laps per car) so there's
    no need for replay.py's cadence throttle — every completion is written."""
    seg_laps = seg_laps.dropna(subset=["Time"]).sort_values("Time")
    if seg_laps.empty:
        db.write_quali_status(label, n_entries, 0,
                              quali.SEGMENT_DURATION_S[label], is_finished=False)
        return

    full_len = len(seg_laps)
    cutoff = int(full_len * max(0.0, min(1.0, progress)))
    seg_laps = seg_laps.iloc[:max(1, cutoff)]

    seg_start_ms = _td_ms(seg_laps["Time"].min())
    best: dict = {}   # car -> (best_ms, last_ms, lap_count)
    total_s = quali.SEGMENT_DURATION_S[label]

    for _, row in seg_laps.iterrows():
        car = str(row["DriverNumber"])
        lap_ms = _td_ms(row.get("LapTime"))
        b, _last, n = best.get(car, (None, None, 0))
        n += 1
        if lap_ms is not None and (b is None or lap_ms < b):
            b = lap_ms
        best[car] = (b, lap_ms, n)

        elapsed_ms = _td_ms(row["Time"])
        elapsed_s = max(0, (elapsed_ms - seg_start_ms) // 1000) if seg_start_ms is not None else 0
        db.write_quali_status(label, n_entries, min(elapsed_s, total_s), total_s,
                              is_finished=False)
        ranked = sorted(
            ((c, v[0]) for c, v in best.items() if v[0] is not None),
            key=lambda cv: cv[1])
        rank_of = {c: i + 1 for i, (c, _) in enumerate(ranked)}
        for c, (b_ms, l_ms, n_laps) in best.items():
            db.write_quali_row(label, c, b_ms, l_ms, n_laps, rank_of.get(c))
        db.conn.commit()

    is_finished = cutoff >= full_len
    db.write_quali_status(
        label, n_entries, total_s if is_finished else elapsed_s, total_s,
        is_finished=is_finished)
    db.conn.commit()


def build(session, db_path, oid, segment: str, progress: float) -> None:
    db = RaceDB(db_path)
    for tbl in ("quali_standings", "quali_status", "sessions", "session_entry"):
        try:
            db.conn.execute(f"DELETE FROM {tbl} WHERE session_oid=?", (oid,))
        except sqlite3.OperationalError:
            pass
    db.conn.commit()

    event_name = (session.event.EventName
                  if hasattr(session.event, "EventName") else str(session.event))
    year = session.event.year if hasattr(session.event, "year") else ""
    db.set_session(oid, {
        "name": session.name, "type": "QUALIFYING",
        "eventName": f"{year} {event_name}", "champName": "Formula 1",
    }, series="f1")

    results = session.results
    for drv_num, row in results.iterrows():
        car = str(drv_num)
        full_name = row.get("FullName", row.get("Abbreviation", car))
        db.upsert_entry(car, {
            "class": "F1", "team": row.get("TeamName"), "vehicle": None,
            "name": full_name, "drivers": [full_name],
        })
    db.commit()

    n_entries = len(results)
    windows = _segment_windows(session)
    laps = session.laps.sort_values(["DriverNumber", "Time"])

    target_idx = quali.SEGMENTS.index(segment)
    for i, (label, (start_ms, finish_ms)) in enumerate(windows):
        if i > target_idx:
            break
        lap_time = laps["Time"].apply(_td_ms)
        seg_laps = laps[(lap_time >= start_ms) & (lap_time <= finish_ms)]
        seg_progress = progress if i == target_idx else 1.0
        _ingest_segment(db, label, seg_laps, n_entries, seg_progress)
        print(f"  {label}: {len(seg_laps)} lap records"
              f"{' (partial replay)' if i == target_idx and progress < 1.0 else ''}")

    db.close()
    print(f"F1 quali replay built → {db_path}  (session {oid}, through {segment})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("year", type=int)
    ap.add_argument("gp", help="Round number or GP name")
    ap.add_argument("session", nargs="?", default="Q")
    ap.add_argument("--db", default=None)
    ap.add_argument("--segment", choices=quali.SEGMENTS, default="Q3",
                    help="replay through this segment (default: all three)")
    ap.add_argument("--progress", type=float, default=1.0,
                    help="fraction of the target segment's laps to replay (default 1.0)")
    ap.add_argument("--cache", default=os.path.join(ROOT, "data", "fastf1_cache"))
    ap.add_argument("--oid", default=None)
    args = ap.parse_args()

    fastf1.Cache.enable_cache(args.cache)

    try:
        gp = int(args.gp)
    except ValueError:
        gp = args.gp

    print(f"Loading FastF1: {args.year} {gp} {args.session}…")
    session = fastf1.get_session(args.year, gp, args.session)
    session.load(telemetry=False, weather=False)
    print(f"  {session.event.EventName} — {session.name}  "
          f"({len(session.results)} entries, {len(session.laps)} lap records)")

    oid = args.oid or _make_oid(args.year, gp, args.session)
    db_path = args.db or os.path.join(ROOT, "data", "f1_quali_replay.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    build(session, db_path, oid, args.segment, args.progress)


if __name__ == "__main__":
    main()
