"""
replay_f1.py — drive a FastF1 historical session through our DB + calculator.

Two modes (mirrors replay.py):

  BATCH (default) — processes all laps into a dedicated DB, then runs the
  evaluator.  Good for accuracy validation.

    python src/replay_f1.py 2023 Bahrain R [--db data/f1_replay.db] [--cadence 60]

  STREAM — feeds laps into data/race.db at a controlled speed while the
  dashboard runs normally.

    python src/replay_f1.py 2023 Bahrain R --stream [--speed 60]

Field mapping (FastF1 → db.ingest_car):
  Position       → overall_position + pos_in_class (single class)
  LapTime        → lastLapTime (ms)
  Sector1/2/3    → lastSectors (Al Kamel format)
  Compound       → tireCompound
  TyreLife       → tireAge
  Time           → elapsedTime (ms from session start)
  Stint boundary → pit detection
  TrackStatus    → canonical flag codes (GF/SC/VSC/RF)
"""

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time as time_mod
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

try:
    import fastf1
except ImportError:
    sys.exit("fastf1 not installed — pip install fastf1")

import calculator
import predictor
from db import RaceDB, _now

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# F1 track-status codes → canonical flags used by calculator/dashboard
_STATUS_MAP = {
    "1": "GF",    # AllClear
    "2": "YF",    # Yellow (local sector)
    "4": "SC",    # Safety Car
    "5": "RF",    # Red Flag
    "6": "VSC",   # Virtual Safety Car
    "7": "GF",    # VSC Ending → green transition
}


# ── helpers ────────────────────────────────────────────────────────────────

def _td_ms(td) -> Optional[int]:
    """Pandas Timedelta → integer milliseconds, or None for NaT."""
    if pd.isna(td):
        return None
    if isinstance(td, timedelta):
        return int(td.total_seconds() * 1000)
    return int(td / pd.Timedelta(milliseconds=1))


def _sectors_str(s1, s2, s3) -> Optional[str]:
    """Build the Al-Kamel-style sector string: '1;ms;0;0;0;0;2;ms;…'."""
    parts = []
    for i, s in enumerate((s1, s2, s3), 1):
        ms = _td_ms(s)
        if ms is None:
            return None
        parts.append(f"{i};{ms};0;0;0;0")
    return ";".join(parts)


def _make_oid(year, gp, session_type) -> str:
    safe_gp = re.sub(r"[^\w]+", "_", str(gp)).lower().strip("_")
    return f"f1_{year}_{safe_gp}_{session_type.lower()}"


# ── flag timeline ──────────────────────────────────────────────────────────

def _build_flag_timeline(session) -> list:
    """[(elapsed_ms, canonical_flag)] from session.track_status."""
    ts = session.track_status
    if ts is None or ts.empty:
        return [(0, "GF")]
    timeline = []
    for _, row in ts.iterrows():
        ms = _td_ms(row["Time"])
        flag = _STATUS_MAP.get(str(row["Status"]), "GF")
        if ms is not None:
            timeline.append((ms, flag))
    return timeline or [(0, "GF")]


def _flag_at(timeline, elapsed_ms) -> str:
    flag = "GF"
    for ts, f in timeline:
        if ts > elapsed_ms:
            break
        flag = f
    return flag


# ── session loading ────────────────────────────────────────────────────────

def load_session(year, gp, session_type):
    return fastf1.get_session(int(year), gp, session_type)


# ── DB bootstrap ───────────────────────────────────────────────────────────

def _init_db(session, db_path, oid):
    """Create DB, write session/entries/pit-events/RC.

    Returns (db, flag_timeline, retired_set, total_laps).
    """
    db = RaceDB(db_path)

    for tbl in ("standings_current", "session_status", "session_entry",
                "pit_events", "predictions", "race_control", "driver_changes",
                "lap_history", "caution_periods"):
        try:
            db.conn.execute(f"DELETE FROM {tbl} WHERE session_oid=?", (oid,))
        except sqlite3.OperationalError:
            pass
    db.conn.commit()

    event_name = (session.event.EventName
                  if hasattr(session.event, "EventName") else str(session.event))
    year = session.event.year if hasattr(session.event, "year") else ""
    db.set_session(oid, {
        "name": session.name,
        "type": "RACE" if "Race" in session.name else session.name.upper(),
        "eventName": f"{year} {event_name}",
        "champName": "Formula 1",
    }, series="f1")

    # ── entries ─────────────────────────────────────────────────────────────
    results = session.results
    retired_set = set()
    retired_after = {}           # car → last lap before retirement
    for drv_num, row in results.iterrows():
        car = str(drv_num)
        full_name = row.get("FullName", row.get("Abbreviation", car))
        db.upsert_entry(car, {
            "class": "F1",
            "team": row.get("TeamName"),
            "vehicle": None,
            "name": full_name,
            "drivers": [full_name],
        })
        status = str(row.get("Status", ""))
        if "Retire" in status:
            retired_set.add(car)
            retired_after[car] = int(row["Laps"]) if pd.notna(row.get("Laps")) else 999
    db.commit()

    # ── race control messages ───────────────────────────────────────────────
    # RC Time is an absolute Timestamp; convert to elapsed ms from session start.
    rc = session.race_control_messages
    if rc is not None and not rc.empty:
        rc_epoch = rc["Time"].min() if not rc["Time"].isna().all() else None
        msgs = []
        for _, row in rc.iterrows():
            t = row.get("Time")
            if pd.notna(t) and rc_epoch is not None:
                ms = int((t - rc_epoch).total_seconds() * 1000)
            else:
                ms = 0
            text = row.get("Message", "")
            if text:
                msgs.append((ms, text))
        db.record_race_control(msgs)
        db.commit()

    # ── flag timeline ───────────────────────────────────────────────────────
    flag_timeline = _build_flag_timeline(session)

    # ── pit events from stint boundaries ────────────────────────────────────
    laps = session.laps.sort_values(["DriverNumber", "LapNumber"])
    for drv_num in laps["DriverNumber"].unique():
        drv_laps = laps[laps["DriverNumber"] == drv_num].sort_values("LapNumber")
        stop_num = 0
        prev_stint = None
        prev_row = None
        for _, row in drv_laps.iterrows():
            stint = row.get("Stint")
            if pd.notna(stint) and prev_stint is not None and stint != prev_stint:
                stop_num += 1
                car = str(drv_num)
                pit_lap = (int(row["LapNumber"]) - 1
                           if pd.notna(row["LapNumber"]) else None)
                elapsed_ms = _td_ms(row.get("Time")) or 0
                flag = _flag_at(flag_timeline, elapsed_ms)

                pit_entry_ms = _td_ms(prev_row.get("PitInTime")) if prev_row is not None else None
                pit_out_ms = _td_ms(row.get("PitOutTime"))
                dur_ms = None
                if pit_entry_ms is not None and pit_out_ms is not None:
                    dur_ms = pit_out_ms - pit_entry_ms

                db._pit_count[car] = stop_num
                db._last_pit_lap[car] = pit_lap
                db.conn.execute(
                    """INSERT OR IGNORE INTO pit_events
                         (session_oid, car_number, stop_number, pit_lap, session_lap,
                          flag, pit_entry_hour_ms, stop_duration_ms, total_pit_ms,
                          detected_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (oid, car, stop_num, pit_lap, pit_lap, flag,
                     pit_entry_ms, dur_ms, None, _now()),
                )
            prev_stint = stint
            prev_row = row
    db.commit()

    total_laps = int(session.total_laps) if session.total_laps else 57
    return db, flag_timeline, retired_set, retired_after, total_laps


# ── per-lap-number ingestion ───────────────────────────────────────────────

def _ingest_lap(db, oid, lap_num, lap_rows, flag_timeline,
                driver_state, best_times, retired_set, retired_after,
                total_laps):
    """Snapshot the field after all drivers complete `lap_num`. Returns elapsed_s."""
    # Update state for drivers who completed this lap
    for _, row in lap_rows.iterrows():
        driver_state[str(row["DriverNumber"])] = row

    active = dict(driver_state)
    if not active:
        return None

    # Leader = most laps, earliest time
    leader_laps = max(
        int(r["LapNumber"]) for r in active.values() if pd.notna(r["LapNumber"]))
    leader_time = None
    for row in active.values():
        if int(row["LapNumber"]) == leader_laps:
            t = _td_ms(row["Time"])
            if t is not None and (leader_time is None or t < leader_time):
                leader_time = t
    if leader_time is None:
        return None

    elapsed_s = leader_time / 1000.0
    avg_lap_ms = leader_time / leader_laps if leader_laps > 0 else 90_000

    flag = _flag_at(flag_timeline, leader_time)

    db.update_status({
        "currentFlag": flag,
        "currentLap": leader_laps,
        "isSessionRunning": True,
        "isFinished": lap_num >= total_laps,
        "finalType": "BY_LAPS",
        "finalLaps": total_laps,
        "finalTime": int(avg_lap_ms * total_laps / 1000),
        "startTime": time_mod.time() - elapsed_s,
        "stoppedSeconds": 0,
    })

    # Sort field by (-laps, cumulative_time) for overall position
    sorted_field = sorted(
        active.items(),
        key=lambda x: (
            -int(x[1]["LapNumber"]) if pd.notna(x[1]["LapNumber"]) else 0,
            _td_ms(x[1]["Time"]) or float("inf"),
        ),
    )
    overall_pos = {car: i + 1 for i, (car, _) in enumerate(sorted_field)}

    for car, row in active.items():
        car_laps = int(row["LapNumber"]) if pd.notna(row["LapNumber"]) else 0
        laps_behind = leader_laps - car_laps

        car_time = _td_ms(row["Time"])
        if laps_behind == 0 and car_time is not None:
            gap_ms = car_time - leader_time
        elif laps_behind > 0:
            gap_ms = laps_behind * int(avg_lap_ms)
        else:
            gap_ms = 0

        last_lap_ms = _td_ms(row["LapTime"])

        # Best-lap tracking
        is_pb = False
        if last_lap_ms is not None:
            prev = best_times.get(car)
            if prev is None or last_lap_ms < prev[0]:
                best_times[car] = (last_lap_ms, car_laps)
                is_pb = True
        best_lap_ms, best_lap_num = best_times.get(car, (None, None))
        all_bests = [v[0] for v in best_times.values() if v[0] is not None]
        is_overall = (last_lap_ms is not None and all_bests
                      and last_lap_ms <= min(all_bests))

        sectors = _sectors_str(
            row.get("Sector1Time"), row.get("Sector2Time"),
            row.get("Sector3Time"))

        # Track status
        is_retired = (car in retired_set
                      and car_laps >= retired_after.get(car, 999))
        if is_retired:
            track_status = "STOPPED"
        elif pd.notna(row.get("PitOutTime")):
            track_status = "OUT_LAP"
        else:
            track_status = "TRACK"

        compound = (row["Compound"]
                    if pd.notna(row.get("Compound")) else None)
        tyre_life = (int(row["TyreLife"])
                     if pd.notna(row.get("TyreLife")) else None)

        d = {
            "overall_position": overall_pos.get(car),
            "car_number": car,
            "pos_in_class": overall_pos.get(car),
            "laps": car_laps,
            "laps_behind": laps_behind,
            "gap_ms": gap_ms,
            "track_status": track_status,
        }
        standing = {
            "class": "F1",
            "isRunning": not is_retired,
            "lastLapTime": last_lap_ms,
            "bestLapTime": best_lap_ms,
            "bestLapNumber": best_lap_num,
            "lastSectors": sectors,
            "isLastLapBestPersonal": is_pb,
            "isLastLapBestOverall": is_overall,
            "elapsedTime": car_time,
            "fuelPct": None,
            "fuelFlag": None,
            "tireCompound": compound,
            "tireAge": tyre_life,
            "overrideState": None,
        }
        db.ingest_car(car, d, standing, leader_laps, flag, raw_data=None)

    return elapsed_s


# ── batch mode ─────────────────────────────────────────────────────────────

def build(session, db_path, oid, cadence_s=60):
    db, flag_tl, retired, retired_after, total_laps = _init_db(
        session, db_path, oid)
    predictor.ensure(db.conn)

    laps = session.laps.sort_values("LapNumber")
    lap_nums = sorted(laps["LapNumber"].dropna().unique())

    driver_state = {}
    best_times = {}
    last_log = -1e9
    n_logged = 0

    for lap_num in lap_nums:
        ln = int(lap_num)
        group = laps[laps["LapNumber"] == lap_num]
        elapsed_s = _ingest_lap(
            db, oid, ln, group, flag_tl,
            driver_state, best_times, retired, retired_after, total_laps)
        db.conn.commit()

        if elapsed_s is not None and elapsed_s - last_log >= cadence_s:
            ctx, cars = calculator.analyse(db.conn, oid)
            n_logged += predictor.log_cycle(
                db.conn, oid, ctx, cars,
                int(elapsed_s * 1000))
            db.conn.commit()
            last_log = elapsed_s

    db.close()
    print(f"F1 replay built → {db_path}")
    print(f"  laps: {len(lap_nums)}  predictions logged: {n_logged}")


# ── stream mode ────────────────────────────────────────────────────────────

def stream(session, db_path, oid, cadence_s=60, speed=60.0):
    db, flag_tl, retired, retired_after, total_laps = _init_db(
        session, db_path, oid)
    predictor.ensure(db.conn)

    laps = session.laps.sort_values("LapNumber")
    lap_nums = sorted(laps["LapNumber"].dropna().unique())

    # Estimate race duration from the last lap's Time
    all_times = laps["Time"].dropna()
    race_s = _td_ms(all_times.max()) / 1000.0 if not all_times.empty else 5400.0

    print(f"\n{'─'*60}")
    print(f"  STREAM  F1 {session.event.EventName} — {session.name}")
    print(f"  {len(lap_nums)} race laps  ·  {total_laps} scheduled  ·  "
          f"{race_s/3600:.1f}h  ·  {speed:.0f}× speed")
    print(f"  Estimated runtime: {race_s / speed / 60:.1f} min")
    print(f"  DB: {db_path}   session: {oid}")
    print(f"{'─'*60}\n")

    driver_state = {}
    best_times = {}
    last_log = -1e9
    n_logged = 0
    wall_start = time_mod.time()
    interrupted = False

    # Build a time estimate per lap-group (leader's elapsed for pacing)
    lap_elapsed = {}
    tmp_state = {}
    for lap_num in lap_nums:
        group = laps[laps["LapNumber"] == lap_num]
        for _, row in group.iterrows():
            tmp_state[str(row["DriverNumber"])] = row
        leader_laps_n = max(
            int(r["LapNumber"]) for r in tmp_state.values()
            if pd.notna(r["LapNumber"]))
        for row in tmp_state.values():
            if int(row["LapNumber"]) == leader_laps_n:
                t = _td_ms(row["Time"])
                if t is not None:
                    lap_elapsed[int(lap_num)] = t / 1000.0
                    break

    try:
        for i, lap_num in enumerate(lap_nums):
            ln = int(lap_num)
            replay_elapsed = lap_elapsed.get(ln, 0)
            wall_target = wall_start + replay_elapsed / speed
            sleep_s = wall_target - time_mod.time()
            if sleep_s > 0:
                time_mod.sleep(sleep_s)

            group = laps[laps["LapNumber"] == lap_num]
            elapsed_s = _ingest_lap(
                db, oid, ln, group, flag_tl,
                driver_state, best_times, retired, retired_after, total_laps)
            db.conn.commit()

            if elapsed_s is not None and elapsed_s - last_log >= cadence_s:
                ctx, cars = calculator.analyse(db.conn, oid)
                n_logged += predictor.log_cycle(
                    db.conn, oid, ctx, cars,
                    int(elapsed_s * 1000))
                db.conn.commit()
                last_log = elapsed_s

            if elapsed_s is not None:
                h = int(elapsed_s) // 3600
                m = (int(elapsed_s) % 3600) // 60
                pct = (i + 1) / len(lap_nums) * 100
                wall_el = time_mod.time() - wall_start
                eta = (wall_el / (i + 1)) * (len(lap_nums) - i - 1)
                print(f"\r  [{h:d}:{m:02d} elapsed]  lap {ln}/{total_laps}"
                      f"  {pct:.1f}%  preds {n_logged}"
                      f"  ETA {int(eta//60)}m{int(eta%60):02d}s  ",
                      end="", flush=True)

    except KeyboardInterrupt:
        interrupted = True
        print("\n\n  [interrupted — flushing DB]")

    db.close()
    print(f"\n\n{'─'*60}")
    print(f"  Stream {'interrupted' if interrupted else 'complete'}.")
    print(f"  laps written: {len(lap_nums)}  predictions logged: {n_logged}")
    print(f"{'─'*60}\n")

    # ── auto-evaluator ──────────────────────────────────────────────────────
    safe_name = re.sub(r"[^\w]+", "_",
                       session.event.EventName or "f1_replay")[:40]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = os.path.join(ROOT, "logs", f"stream_f1_{safe_name}_{stamp}.txt")

    print("  Running evaluator…")
    ev_script = os.path.join(ROOT, "src", "evaluator.py")
    python = os.path.join(ROOT, "venv", "bin", "python")
    try:
        result = subprocess.run(
            [python, ev_script, "--db", db_path, "--session", oid, "--force"],
            capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            f.write(f"stream: F1 {session.event.EventName} {session.name}\n")
            f.write(f"run:    {datetime.now().isoformat()}\n")
            f.write(f"db:     {db_path}   session: {oid}\n")
            f.write(f"speed:  {speed}×   cadence: {cadence_s}s\n")
            f.write(f"laps:   {len(lap_nums)}   predictions: {n_logged}\n")
            f.write("─" * 60 + "\n\n")
            f.write(output)
        print(f"  Report saved → {report_path}\n")
        for line in reversed(output.splitlines()):
            if line.strip():
                print(f"  {line.strip()}\n")
                break
    except Exception as e:
        print(f"  Evaluator error: {e}\n")
        print(f"  Re-run manually:\n"
              f"    python src/evaluator.py --db {db_path} "
              f"--session {oid} --force\n")


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Drive a FastF1 historical session through the strategy engine.")
    ap.add_argument("year", type=int, help="Season year (e.g. 2023)")
    ap.add_argument("gp", help="Grand Prix name or round number (e.g. Bahrain, 1)")
    ap.add_argument("session", nargs="?", default="R",
                    help="Session type: R=Race, Q=Quali, S=Sprint (default R)")
    ap.add_argument("--stream", action="store_true",
                    help="Stream laps into race.db (dashboard mode)")
    ap.add_argument("--speed", type=float, default=60.0,
                    help="Replay speed multiplier for --stream (default 60×)")
    ap.add_argument("--db", default=None,
                    help="DB path (default: data/f1_replay.db / data/race.db)")
    ap.add_argument("--cadence", type=int, default=60,
                    help="Prediction-logging interval in seconds (default 60)")
    ap.add_argument("--oid", default=None, help="Session OID override")
    ap.add_argument("--cache", default=None,
                    help="FastF1 cache directory (default: system cache)")
    args = ap.parse_args()

    if args.cache:
        fastf1.Cache.enable_cache(args.cache)

    # Try to parse gp as a round number
    try:
        gp = int(args.gp)
    except ValueError:
        gp = args.gp

    print(f"Loading FastF1: {args.year} {gp} {args.session}…")
    ff_session = load_session(args.year, gp, args.session)
    ff_session.load(telemetry=False, weather=False)
    print(f"  {ff_session.event.EventName} — {ff_session.name}")
    print(f"  {len(ff_session.laps)} lap records, "
          f"{len(ff_session.results)} drivers")

    oid = args.oid or _make_oid(args.year, gp, args.session)

    if args.stream:
        db_path = args.db or os.path.join(ROOT, "data", "race.db")
        stream(ff_session, db_path, oid,
               cadence_s=args.cadence, speed=args.speed)
    else:
        db_path = args.db or os.path.join(ROOT, "data", "f1_replay.db")
        if os.path.exists(db_path):
            os.remove(db_path)
        build(ff_session, db_path, oid, cadence_s=args.cadence)


if __name__ == "__main__":
    main()
