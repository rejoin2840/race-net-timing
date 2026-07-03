"""
replay.py — drive a Timing71 replay through our own DB + calculator.

Two modes:

  BATCH (default) — processes all frames at once into a dedicated DB, then
  runs the evaluator. Good for accuracy validation.

    python src/replay.py <replay.zip> [--db data/replay.db] [--cadence 60]

  STREAM — feeds frames into data/race.db at a controlled speed while the
  dashboard runs normally.  Start the dashboard first, then run this; it
  simulates a live race so every visual behaviour (box timers, blue flash,
  scroll-lock, strategy updates) can be exercised end-to-end.  Walk away;
  the evaluator runs automatically when the last frame is written.

    python src/replay.py <replay.zip> --stream [--speed 60] [--cadence 60]
      --speed N  : replay N× faster than real-time (default 60;
                   6h race ≈ 6 min, 24h race ≈ 24 min)

After a stream run the evaluator report lands in logs/stream_<name>_<date>.txt.
Run  python src/evaluator.py --db data/race.db --session stream --force
at any time to re-score from the logged predictions.

Field mapping notes
  • Gap (to overall leader) → synthesised elapsed_ms; the calculator only diffs
    elapsed WITHIN a class, so leader-relative seconds reconstruct same-lap gaps.
    Lapped cars get a lap-scaled elapsed so class order stays right.
  • Pit stops come from the message log (authoritative durations).
  • Clock: start_time_s is set to (wall_now − frame elapsed) each cycle so the
    calculator's wall-clock remaining math lands on the replay's remaining time.
"""

import argparse
import bisect
import os
import re
import subprocess
import sys
import sqlite3
import time
from datetime import datetime
from typing import Optional

import calculator
import predictor
import timing71
from db import RaceDB, _now

GLOBAL_AVG_LAP_MS = 100_000
RACE_LENGTH_S     = 6 * 3600     # fallback only; real length derived from feed

_LAPS_DOWN = re.compile(r"(\d+)\s+laps?")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _detect_series(replay: timing71.Replay) -> str:
    """Map the archive's manifest.name to our series key. IndyCar archives have
    no Class/PIC/VFT columns (single class, no fuel telemetry) — everything
    downstream (series_profiles, dashboard rendering) branches on this key."""
    return "indycar" if "indycar" in replay.series_name.lower() else "imsa"


# ── shared helpers ──────────────────────────────────────────────────────────

def _gap_ms(gap) -> int:
    """Gap to the (overall) leader, from the feed's own Gap cell — the source of
    truth. A numeric cell is a REAL time gap (the car is on the same lead lap as the
    leader); an 'N laps' string means genuinely lapped (no time available → synthetic
    N×avg-lap). We do NOT infer lapped-ness from integer lap-count subtraction: that
    flickers ±1 at S/F crossings during pit cycles and was masking the real gap."""
    if gap in (None, "", "-"):
        return 0
    if isinstance(gap, (int, float)):
        return int(float(gap) * 1000)
    m = _LAPS_DOWN.search(str(gap))
    if m:
        return int(m.group(1)) * GLOBAL_AVG_LAP_MS
    try:
        return int(float(gap) * 1000)
    except ValueError:
        return 0


def _laps_behind(gap) -> int:
    """Laps behind the leader, per the feed cell: N from an 'N laps' string, else 0
    (a numeric gap = same lead lap). Not derived from integer lap counts."""
    if isinstance(gap, str):
        m = _LAPS_DOWN.search(gap)
        if m:
            return int(m.group(1))
    return 0


def _laptime_ms(v) -> Optional[int]:
    v = timing71._cell(v)
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        return int(float(v) * 1000)
    s = str(v)
    if ":" in s:
        mm, ss = s.split(":", 1)
        try:
            return int((int(mm) * 60 + float(ss)) * 1000)
        except ValueError:
            return None
    try:
        return int(float(s) * 1000)
    except ValueError:
        return None


# ── DB setup (shared between batch and stream) ──────────────────────────────

def _init_db(replay: timing71.Replay, db_path: str, oid: str):
    """Create/open the DB, write session + entries + pit_events.

    Returns (db, col, lap_at, flag_at, pit_in) ready for frame iteration.
    pit_in: dict[car] → sorted [(in_ts_ms, pit_lap)]
    """
    db = RaceDB(db_path)
    # Wipe any stale rows for this session BEFORE ingesting — a prior stream that
    # was interrupted (or a different race reusing oid='stream') leaves ghost cars
    # in standings_current that incremental frames never delete, polluting leader
    # detection and laps-down. Start every stream from a clean session.
    for tbl in ("standings_current", "session_status", "session_entry",
                "pit_events", "predictions", "race_control", "driver_changes",
                "lap_history", "caution_periods"):
        try:
            db.conn.execute(f"DELETE FROM {tbl} WHERE session_oid=?", (oid,))
        except sqlite3.OperationalError:
            pass   # table may not exist yet on a fresh DB / lacks session_oid
    db.conn.commit()

    series = _detect_series(replay)
    db.set_session(oid, {
        "name": "Race (replay)", "type": "RACE",
        "eventName": replay.name, "champName": "Timing71 replay",
    }, series=series)
    col = replay.col
    # single-class archives (IndyCar) carry no Class column — everything is one
    # class, matching series_profiles.INDYCAR.classes = ("INDYCAR",)
    default_class = "INDYCAR" if series == "indycar" else None

    # ── entries ──────────────────────────────────────────────────────────────
    finals    = replay.final_cars()
    stops_by  = replay.pit_stops()
    lineup: dict[str, set] = {}
    for car, stops in stops_by.items():
        for s in stops:
            for nm in (s.driver_from, s.driver_to):
                if nm:
                    lineup.setdefault(car, set()).add(nm)
    for _ts, fr in (replay.full_frames[:1] + replay.full_frames[-1:]):
        for row in fr.get("cars", []):
            num = row[col["Num"]]; drv = row[col["Driver"]]
            if drv:
                lineup.setdefault(num, set()).add(drv)
    for car, fin in finals.items():
        db.upsert_entry(car, {
            "class": fin.get("class") or default_class, "team": None, "vehicle": None,
            "name": None, "drivers": sorted(lineup.get(car, set())),
        })
    db.commit()

    # ── per-car lap timeline ──────────────────────────────────────────────────
    lap_ts: dict[str, list] = {}
    for ts, fr in replay.full_frames:
        for row in fr.get("cars", []):
            num  = row[col["Num"]]
            laps = timing71._num(row[col["Laps"]])
            if laps is not None:
                lap_ts.setdefault(num, []).append((ts * 1000, int(laps)))

    def lap_at(car: str, ts_ms: int) -> Optional[int]:
        seq = lap_ts.get(car)
        if not seq:
            return None
        i = bisect.bisect_right([t for t, _ in seq], ts_ms) - 1
        return seq[max(0, i)][1]

    # ── flag timeline ─────────────────────────────────────────────────────────
    flags    = replay.flag_timeline()
    flag_ts_ = [t for t, _ in flags]

    def flag_at(ts_ms: int) -> str:
        if not flags:
            return "GF"
        i   = bisect.bisect_right(flag_ts_, ts_ms) - 1
        raw = flags[max(0, i)][1]
        # IMSA's flagState uses "fcy"/"yellow"; IndyCar uses "caution".
        return "FCY" if "fcy" in raw or "yellow" in raw or "caution" in raw else "GF"

    # ── pit events ─────────────────────────────────────────────────────────────
    stop_no: dict[str, int] = {}
    pit_in:  dict[str, list] = {}
    for car, stops in stops_by.items():
        for s in sorted(stops, key=lambda x: x.in_ts or x.out_ts or 0):
            in_ts = s.in_ts or s.out_ts
            if in_ts is None:
                continue
            n = stop_no.get(car, 0) + 1
            stop_no[car] = n
            pit_lap = lap_at(car, in_ts)
            pit_in.setdefault(car, []).append((in_ts, pit_lap))
            dur_ms = int(s.duration_s * 1000) if s.duration_s else None
            db.conn.execute(
                """INSERT OR IGNORE INTO pit_events
                     (session_oid, car_number, stop_number, pit_lap, session_lap,
                      flag, pit_entry_hour_ms, stop_duration_ms, total_pit_ms, detected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (oid, car, n, pit_lap, pit_lap, flag_at(in_ts),
                 in_ts, dur_ms, None, _now()),
            )
            if s.is_driver_change and s.driver_to:
                db.record_driver(car, s.driver_to, pit_lap)
    db.commit()

    return db, col, lap_at, flag_at, pit_in


def _rc_feed(replay: timing71.Replay):
    """Time-gated race-control feed for frame loops.

    Returns feed(ts_ms) → the (ts, text) raceControl tuples newly due since the
    last call (message ts <= ts_ms). Persisting RC incrementally — instead of
    all upfront — keeps the DB time-consistent at every analyse() cycle, so
    calculator._load_penalties() only ever sees penalties already issued,
    matching live-feed behaviour. Loading them upfront charged hour-1
    predictions with hour-20 penalties (netMAE regression, see BACKLOG Epic 3).
    """
    rc = [(m[0], m[2]) for m in replay.messages
          if len(m) > 3 and m[3] == "raceControl"]   # already sorted asc by ts
    cursor = [0]

    def feed(ts_ms: int) -> list:
        i = cursor[0]
        while i < len(rc) and rc[i][0] <= ts_ms:
            i += 1
        due, cursor[0] = rc[cursor[0]:i], i
        return due

    return feed


# ── per-frame ingestion (shared) ────────────────────────────────────────────

def _ingest_frame(db, oid, ts, fr, col, pit_in, flag_at):
    """Write one replay frame into the DB. Returns elapsed_s or None."""
    ts_ms   = ts * 1000
    sess    = fr.get("session") or {}
    elapsed_s = sess.get("timeElapsed")
    remain_s  = sess.get("timeRemain")
    laps_remain = sess.get("lapsRemain")   # IndyCar: lap-limited, no timeRemain

    current_lap = max(
        (timing71._num(r[col["Laps"]]) or 0)
        for r in fr.get("cars", [])
    ) if fr.get("cars") else 0

    if elapsed_s is not None:
        status = {
            "currentFlag": flag_at(ts_ms),
            "currentLap": current_lap,
            "isSessionRunning": True, "isFinished": False,
            "startTime": time.time() - elapsed_s, "stoppedSeconds": 0,
        }
        if remain_s is not None:
            status["finalType"] = "BY_TIME"
            status["finalTime"] = elapsed_s + remain_s
        elif laps_remain is not None:
            status["finalType"] = "BY_LAPS"
            status["finalLaps"] = current_lap + laps_remain
        else:
            status["finalType"] = "BY_TIME"
            status["finalTime"] = RACE_LENGTH_S
        db.update_status(status)

    rows = fr.get("cars", [])
    if not rows:
        return elapsed_s

    leader_laps = max((timing71._num(r[col["Laps"]]) or 0) for r in rows)
    ordered = sorted(rows, key=lambda r: (
        -(timing71._num(r[col["Laps"]]) or 0),
        _gap_ms(timing71._cell(r[col["Gap"]]))))
    overall_pos = {r[col["Num"]]: i + 1 for i, r in enumerate(ordered)}

    # Cumulative Int down the overall running order = real, drift-free gap to the
    # overall leader. The feed's Gap cell lap-quantizes for lower classes (it's gap to
    # the GTP leader), collapsing intra-class gaps to ~0; the Int column (interval to
    # the car directly ahead) sums cleanly. Non-numeric Int ("1 lap") contributes 0.
    # Use _num() (not a bare isinstance check) — IMSA's Int cells are JSON floats,
    # but IndyCar's are numeric strings ("1.7297"); _num() coerces either.
    cum_int: dict[str, float] = {}
    running = 0.0
    for r in ordered:
        iv = timing71._num(r[col["Int"]])
        running += iv if iv is not None else 0.0
        cum_int[r[col["Num"]]] = running

    # Columns that don't exist on every archive (IndyCar has no Class/PIC/VFT —
    # single class, no fuel telemetry — but adds T for tyre compound). Resolved
    # once per frame rather than per row.
    cls_i, pic_i, vft_i, tyre_i = col.get("Class"), col.get("PIC"), col.get("VFT"), col.get("T")
    default_class = "INDYCAR" if cls_i is None else None

    for r in rows:
        car  = r[col["Num"]]
        laps = int(timing71._num(r[col["Laps"]]) or 0)
        cls  = r[cls_i] if cls_i is not None else default_class
        seq  = pit_in.get(car, [])
        done = [pl for (it, pl) in seq if it <= ts_ms]
        db._pit_count[car]    = len(done)
        db._last_pit_lap[car] = next((pl for pl in reversed(done) if pl is not None), None)
        state     = (r[col["State"]] or "").upper()
        gcell     = timing71._cell(r[col["Gap"]])
        gap_ms    = _gap_ms(gcell)            # real time gap (or N×avg-lap if "N laps")
        laps_behind = _laps_behind(gcell)     # from the feed cell, not integer counts
        last_ms   = _laptime_ms(r[col["Last"]])

        # virtual fuel tank telemetry: VFT cell is [percent, flag] where flag is
        # '' / 'yellow' / 'red' (IMSA's own low-fuel warning). Populated for
        # GTP/GTDPRO/GTD; empty (['','']) for LMP2. None for archives with no
        # VFT column at all (IndyCar has no fuel telemetry, same as F1 v1).
        vft = r[vft_i] if vft_i is not None else None
        fuel_pct  = (vft[0] if isinstance(vft, (list, tuple)) and vft
                     and isinstance(vft[0], (int, float)) else None)
        fuel_flag = (vft[1] if isinstance(vft, (list, tuple)) and len(vft) > 1
                     and vft[1] else None)

        # IndyCar tyre compound: T cell is ["P", "tyre-medium"] (Primary/Black)
        # or ["O", "tyre-soft"] (Alternate/Red) — Firestone has no hardness
        # naming, just the two compounds. Age = laps run since the car's last
        # detected pit stop (or since the green flag if it hasn't pitted yet) —
        # db._last_pit_lap was just set above for this car this frame.
        tire_compound = tire_age = None
        if tyre_i is not None:
            tcell = r[tyre_i]
            letter = tcell[0] if isinstance(tcell, (list, tuple)) and tcell else None
            tire_compound = {"P": "PRIMARY", "O": "ALTERNATE"}.get(letter)
            last_pit_lap = db._last_pit_lap[car]
            tire_age = laps - last_pit_lap if last_pit_lap is not None else laps

        # Authoritative pit/out-lap state from the feed (mirrors the live feed's
        # track_status). OUT spans exactly the single out lap (pit exit → S/F).
        track_status = {"PIT": "BOX", "OUT": "OUT_LAP",
                        "STOP": "STOPPED", "RET": "STOPPED"}.get(state, "TRACK")

        d = {
            "overall_position": overall_pos.get(car), "car_number": car,
            "pos_in_class": r[pic_i] if pic_i is not None else overall_pos.get(car),
            "laps": laps,
            "laps_behind": laps_behind,
            "gap_ms": gap_ms,
            "track_status": track_status,
        }
        standing = {
            "class": cls, "isRunning": state in ("RUN", "FIN"),
            "lastLapTime": last_ms,
            "bestLapTime":  _laptime_ms(r[col["Best"]]),
            "elapsedTime":  int(cum_int.get(car, 0.0) * 1000),
            "fuelPct":  fuel_pct,
            "fuelFlag": fuel_flag,
            "tireCompound": tire_compound,
            "tireAge": tire_age,
        }
        db.ingest_car(car, d, standing, laps, flag_at(ts_ms), raw_data=None)

        # keep the entry's current driver fresh per frame (matches the live feed:
        # calculator reads session_entry.name as the in-car driver). The frame's
        # Driver cell is "LAST, First" and changes at driver swaps.
        drv = r[col["Driver"]]
        if drv:
            db.conn.execute(
                "UPDATE session_entry SET name=? WHERE session_oid=? AND car_number=?",
                (drv, oid, car))

    return elapsed_s


# ── batch mode ───────────────────────────────────────────────────────────────

def build(replay: timing71.Replay, db_path: str, oid: str = "replay",
          cadence_s: int = 60) -> None:
    db, col, _lap_at, flag_at, pit_in = _init_db(replay, db_path, oid)
    predictor.ensure(db.conn)
    rc_feed  = _rc_feed(replay)
    last_log = -1e9
    n_logged = 0

    for ts, fr in replay.full_frames:
        db.record_race_control(rc_feed(ts * 1000))
        elapsed_s = _ingest_frame(db, oid, ts, fr, col, pit_in, flag_at)
        db.conn.commit()
        if elapsed_s is not None and elapsed_s - last_log >= cadence_s:
            ctx, cars = calculator.analyse(db.conn, oid)
            n_logged += predictor.log_cycle(db.conn, oid, ctx, cars, int(ts * 1000))
            db.conn.commit()
            last_log = elapsed_s

    db.close()
    stops_by = replay.pit_stops()
    print(f"replay built → {db_path}")
    print(f"  frames: {len(replay.full_frames)}  predictions logged: {n_logged}")
    print(f"  pit_events: {sum(len(v) for v in stops_by.values())}")


# ── stream mode ──────────────────────────────────────────────────────────────

def stream(replay: timing71.Replay, db_path: str, oid: str = "stream",
           cadence_s: int = 60, speed: float = 60.0) -> None:
    """Stream replay frames into the DB at real-time × speed.

    The dashboard polls race.db normally — open it before starting the stream.
    Ctrl-C at any time; predictions already logged are preserved.
    Evaluator runs automatically at the end and the report is saved to logs/.
    """
    db, col, _lap_at, flag_at, pit_in = _init_db(replay, db_path, oid)
    predictor.ensure(db.conn)
    rc_feed = _rc_feed(replay)

    frames    = replay.full_frames
    n_frames  = len(frames)
    stops_by  = replay.pit_stops()
    n_stops   = sum(len(v) for v in stops_by.values())
    first_ts  = frames[0][0] if frames else 0
    last_ts   = frames[-1][0] if frames else 0
    race_h    = (last_ts - first_ts) / 3600

    print(f"\n{'─'*60}")
    print(f"  STREAM  {replay.name}")
    print(f"  {n_frames} frames  ·  {n_stops} pit events  ·  "
          f"{race_h:.1f}h race  ·  {speed:.0f}× speed")
    print(f"  Estimated runtime: {race_h * 3600 / speed / 60:.1f} min")
    print(f"  DB: {db_path}   session: {oid}")
    print(f"{'─'*60}\n")

    wall_start = time.time()
    last_log   = -1e9
    n_logged   = 0
    interrupted = False

    try:
        for i, (ts, fr) in enumerate(frames):
            # pace: sleep until we should be here in wall-clock time
            replay_offset = ts - first_ts
            wall_target   = wall_start + replay_offset / speed
            sleep_s       = wall_target - time.time()
            if sleep_s > 0:
                time.sleep(sleep_s)

            db.record_race_control(rc_feed(ts * 1000))
            elapsed_s = _ingest_frame(db, oid, ts, fr, col, pit_in, flag_at)
            db.conn.commit()

            if elapsed_s is not None and elapsed_s - last_log >= cadence_s:
                ctx, cars = calculator.analyse(db.conn, oid)
                n_logged += predictor.log_cycle(
                    db.conn, oid, ctx, cars, int(ts * 1000))
                db.conn.commit()
                last_log = elapsed_s

            # progress line (overwrite in place)
            if elapsed_s is not None:
                h = int(elapsed_s) // 3600
                m = (int(elapsed_s) % 3600) // 60
                pct = (i + 1) / n_frames * 100
                wall_elapsed = time.time() - wall_start
                eta_s = (wall_elapsed / (i + 1)) * (n_frames - i - 1)
                print(f"\r  [{h:d}:{m:02d} elapsed]  frame {i+1}/{n_frames}"
                      f"  {pct:.1f}%  preds {n_logged}"
                      f"  ETA {int(eta_s//60)}m{int(eta_s%60):02d}s  ",
                      end="", flush=True)

    except KeyboardInterrupt:
        interrupted = True
        print("\n\n  [interrupted — flushing DB]")

    db.close()
    print(f"\n\n{'─'*60}")
    print(f"  Stream {'interrupted' if interrupted else 'complete'}.")
    print(f"  frames written: {n_frames}  predictions logged: {n_logged}")
    print(f"{'─'*60}\n")

    # ── auto-evaluator ────────────────────────────────────────────────────────
    safe_name = re.sub(r"[^\w]+", "_", replay.name or "replay")[:40]
    stamp     = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = os.path.join(ROOT, "logs", f"stream_{safe_name}_{stamp}.txt")

    print("  Running evaluator…")
    ev_script = os.path.join(ROOT, "src", "evaluator.py")
    python    = os.path.join(ROOT, "venv", "bin", "python")
    try:
        result = subprocess.run(
            [python, ev_script, "--db", db_path, "--session", oid, "--force"],
            capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        with open(report_path, "w") as f:
            f.write(f"stream: {replay.name}\n")
            f.write(f"run:    {datetime.now().isoformat()}\n")
            f.write(f"db:     {db_path}   session: {oid}\n")
            f.write(f"speed:  {speed}×   cadence: {cadence_s}s\n")
            f.write(f"frames: {n_frames}   predictions: {n_logged}\n")
            f.write("─" * 60 + "\n\n")
            f.write(output)
        print(f"  Report saved → {report_path}\n")
        # echo the one-liner to terminal
        for line in reversed(output.splitlines()):
            if line.strip():
                print(f"  {line.strip()}\n")
                break
    except Exception as e:
        print(f"  Evaluator error: {e}\n")
        print(f"  Re-run manually:\n"
              f"    python src/evaluator.py --db {db_path} "
              f"--session {oid} --force\n")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Drive a Timing71 replay through the strategy engine.")
    ap.add_argument("zip", help="Path to the Timing71 replay .zip")
    ap.add_argument("--stream", action="store_true",
                    help="Stream frames in real-time into race.db (dashboard mode)")
    ap.add_argument("--speed", type=float, default=60.0,
                    help="Replay speed multiplier for --stream (default 60×)")
    ap.add_argument("--db", default=None,
                    help="DB path (default: data/replay.db for batch, "
                         "data/race.db for stream)")
    ap.add_argument("--cadence", type=int, default=60,
                    help="Prediction-logging interval in seconds (default 60)")
    ap.add_argument("--oid", default=None,
                    help="Session OID (default: 'replay' for batch, 'stream' for stream)")
    args = ap.parse_args()

    if args.stream:
        db_path = args.db or os.path.join(ROOT, "data", "race.db")
        oid     = args.oid or "stream"
        # clear the previous stream session from the DB so the dashboard shows
        # fresh data, but leave other sessions (live race history) intact.
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("DELETE FROM standings_current WHERE session_oid=?", (oid,))
                conn.execute("DELETE FROM session_status   WHERE session_oid=?", (oid,))
                conn.execute("DELETE FROM sessions         WHERE session_oid=?",  (oid,))
                conn.execute("DELETE FROM pit_events       WHERE session_oid=?", (oid,))
                conn.execute("DELETE FROM predictions      WHERE session_oid=?", (oid,))
                conn.execute("DELETE FROM entries          WHERE session_oid=?", (oid,))
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()
        r = timing71.load(args.zip)
        stream(r, db_path, oid=oid, cadence_s=args.cadence, speed=args.speed)
    else:
        db_path = args.db or os.path.join(ROOT, "data", "replay.db")
        oid     = args.oid or "replay"
        if os.path.exists(db_path):
            os.remove(db_path)
        r = timing71.load(args.zip)
        build(r, db_path, oid=oid, cadence_s=args.cadence)


if __name__ == "__main__":
    main()
