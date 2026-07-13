"""
validate_races.py — multi-race regression suite for the prediction engine.

Batch-processes a set of Timing71 replay zips through replay.build() into temp
DBs, computes the core accuracy metrics for each, and prints ONE comparison
table. Use it to (a) confirm a tuning change generalises across tracks/lengths
rather than overfitting one race, and (b) guard against silent regressions when
the algorithms change.

  python src/validate_races.py                # runs the default IMSA RACES set
  python src/validate_races.py <zip> <zip> …  # ad-hoc set
  python src/validate_races.py --keep-dbs    # leave the temp DBs for inspection

Reads the live config.json, so it reflects whatever tuning is currently active.
"""

import argparse
import contextlib
import os
import sys
import tempfile

import config
import evaluator
import replay
import timing71

# default regression set — edit as the library of complete archives grows.
# Only COMPLETE (run-to-chequered) archives belong here; truncated ones skew
# the numbers (final pit cycles unresolved). Verified complete 2026-06-28.
DL = os.path.expanduser(config.CONFIG.ARCHIVE_DIR)
WEC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "wec-archives")

RACES = [
    f"{DL}/2026-01-24 18-37 IMSA WeatherTech SportsCar Championship - Rolex 24 at Daytona - Race.zip",
    f"{DL}/2025-10-11 16-07 IMSA WeatherTech SportsCar Championship - 28th Annual Motul Petit Le Mans - Race.zip",
    f"{DL}/2025-09-21 15-37 IMSA WeatherTech SportsCar Championship - Tire Rack.com Battle On The Bricks - Race.zip",
    f"{DL}/2026-05-03 19-57 IMSA WeatherTech SportsCar Championship - StubHub Monterey SportsCar Championship - Race.zip",
    f"{DL}/2026-04-18 20-02 IMSA WeatherTech SportsCar Championship - Acura Grand Prix of Long Beach - Race.zip",
    f"{DL}/2026-05-30 19-57 IMSA WeatherTech SportsCar Championship - Chevrolet Detroit Sports Car Classic - Race.zip",
    f"{DL}/2026-07-12 18-02 IMSA WeatherTech SportsCar Championship - Chevrolet Grand Prix - Race.zip",
]

# WEC regression set — complete (run-to-chequered) 6h/8h races only.
# Le Mans 24h excluded: different format, entry structure, and overnight
# dynamics make it a poor regression baseline. Truncated archives excluded.
# Verified complete 2026-07-05.
WEC_RACES = [
    f"{WEC_DIR}/2024-07-14 14-27 FIA WEC - Rolex 6 Hours of SÃO PAULO - Race.zip",
    f"{WEC_DIR}/2025-07-13 14-27 FIA WEC - Race.zip",
    f"{WEC_DIR}/2025-09-07 17-57 FIA WEC - Lone Star Le Mans - Race.zip",
    f"{WEC_DIR}/2025-09-28 01-57 FIA WEC - 6 Hours of Fuji - Race.zip",
    f"{WEC_DIR}/2025-11-08 10-57 FIA WEC - Bapco Energies 8 Hours of Bahrain - Race.zip",
    f"{WEC_DIR}/2026-04-19 10-57 FIA World Endurance Championship - FIA WEC - 6 Hours of Imola - Race.zip",
    f"{WEC_DIR}/2025-02-28 10-57 FIA WEC - Qatar 1812km - Race.zip",
    f"{WEC_DIR}/2026-07-12 14-27 FIA World Endurance Championship - ROLEX 6 Hours of São Paulo - Race.zip",
]

# Griiip raw captures (--record output) — replayed via wec_live.replay_predict
# instead of replay.build. Same complete-races-only rule as the zip sets.
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
WEC_CAPTURES = [
    f"{DATA_DIR}/wec_saopaulo_wec_race_20260712.jsonl.gz",
]



def _short(name: str) -> str:
    """Pull a readable circuit/event label out of the long archive filename."""
    base = os.path.basename(name)
    if base.endswith(".jsonl.gz"):
        return base[:-len(".jsonl.gz")][:26]
    parts = base.rsplit(" - ", 2)
    event = parts[-2] if len(parts) >= 2 else base
    for noise in ("28th Annual Motul ", "Tire Rack.com ", "Acura ", "StubHub ",
                  "Chevrolet ", "Grand Prix of ", "FIA WEC - ", "FIA World Endurance Championship - ",
                  "Rolex ", "Bapco Energies ", "TotalEnergies "):
        event = event.replace(noise, "")
    return event[:26]


def _build_from_zip(zip_path: str, db: str):
    """Timing71 archive → replay.build. Returns (oid, race_hours)."""
    r = timing71.load(zip_path)
    with open(os.devnull, "w") as null, contextlib.redirect_stdout(null):
        replay.build(r, db, oid="replay", cadence_s=60)
    return "replay", (r.full_frames[-1][0] - r.full_frames[0][0]) / 3600


def _build_from_capture(path: str, db: str):
    """Griiip --record capture → wec_live.replay_predict. Returns (oid, hrs)."""
    import db as dbmod
    import wec_live
    client = wec_live.WecLiveClient(db_path="", no_db=True)
    client.db = dbmod.RaceDB(db)
    try:
        res = wec_live.replay_predict(client, path, cadence_s=60)
    finally:
        client.db.close()
    first = last = None
    for _, _, ts in wec_live.iter_capture(path):
        if ts is not None:
            last = ts
            if first is None:
                first = ts
    hrs = (last - first) / 3_600_000 if first is not None else 0.0
    return res["session_oid"], hrs


def run_one(path: str, keep: bool):
    fd, db = tempfile.mkstemp(suffix=".db", prefix="valrace_")
    os.close(fd); os.remove(db)
    if path.endswith(".jsonl.gz"):
        oid, hrs = _build_from_capture(path, db)
    else:
        oid, hrs = _build_from_zip(path, db)
    import sqlite3
    conn = sqlite3.connect(db)
    stop  = evaluator.eval_stop_time(conn, oid)
    net   = evaluator.eval_net_position(conn, oid)
    catch = evaluator.eval_catch(conn, oid)
    conn.close()
    if not keep:
        os.remove(db)
    return {"label": _short(path), "hrs": hrs, "stop": stop, "net": net, "catch": catch}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zips", nargs="*", help="replay zips (default: built-in IMSA RACES)")
    ap.add_argument("--keep-dbs", action="store_true", help="leave temp DBs in place")
    ap.add_argument("--wec", action="store_true", help="run WEC regression set instead of IMSA")
    ap.add_argument("--all", action="store_true", help="run both IMSA and WEC sets")
    args = ap.parse_args()
    if args.zips:
        zips = args.zips
    elif args.all:
        zips = RACES + WEC_RACES + WEC_CAPTURES
    elif args.wec:
        zips = WEC_RACES + WEC_CAPTURES
    else:
        zips = RACES

    rows = []
    for z in zips:
        if not os.path.exists(z):
            print(f"  ⚠ missing, skipped: {os.path.basename(z)}", file=sys.stderr)
            continue
        print(f"  running {_short(z)} …", file=sys.stderr)
        try:
            rows.append(run_one(z, args.keep_dbs))
        except Exception as e:
            print(f"  ⚠ failed {_short(z)}: {e}", file=sys.stderr)

    cfg = __import__("config").CONFIG.as_dict()
    print(f"\n  config: STOP_OUTLIER_MAD={cfg.get('STOP_OUTLIER_MAD')}  "
          f"CAUTION_PENALTY_FACTOR={cfg.get('CAUTION_PENALTY_FACTOR')}  "
          f"CATCH_CLOSING_EFFICIENCY={cfg.get('CATCH_CLOSING_EFFICIENCY')}\n")
    hdr = (f"  {'race':26} {'h':>4} | {'stopMAE':>7} {'bias':>6} | "
           f"{'projMAE':>7} {'trkMAE':>6} {'edge':>6} {'netMAE':>6} | "
           f"{'catch%':>6} {'late':>5}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        s, n, c = r["stop"], r["net"], r["catch"]
        # stop columns show the predictable slice (splash/penalty/repair stops
        # are unforecastable noise); falls back to combined on old data
        sp = (s or {}).get("predictable") or s
        sm = f"{sp['mae_ms']/1000:6.1f}s" if sp else "    —"
        sb = f"{sp['bias_ms']/1000:+5.1f}" if sp else "    —"
        pm = (f"{n['proj_mae']:7.2f}" if n and n.get("proj_mae") is not None
              else "      —")
        tm = f"{n['track_mae']:6.2f}" if n else "     —"
        # edge = the shipped forecast (projected_finish) vs naive track position
        pi = n.get("proj_improvement_pct") if n else None
        ed = f"{pi:+5.0f}%" if pi is not None else "     —"
        win = "✓" if (pi is not None and pi > 0) else " "
        nm = f"{n['net_mae']:6.2f}" if n else "     —"
        cr = f"{c['hit_rate']*100:5.0f}%" if c else "    —"
        cl = f"{c['median_late_laps']:4.1f}" if c and c['median_late_laps'] is not None else "   —"
        print(f"  {r['label']:26} {r['hrs']:4.1f} | {sm} {sb} | "
              f"{pm} {tm} {ed}{win} {nm} | {cr} {cl}")
    print()


if __name__ == "__main__":
    main()
