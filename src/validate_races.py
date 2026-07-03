"""
validate_races.py — multi-race regression suite for the prediction engine.

Batch-processes a set of Timing71 replay zips through replay.build() into temp
DBs, computes the core accuracy metrics for each, and prints ONE comparison
table. Use it to (a) confirm a tuning change generalises across tracks/lengths
rather than overfitting one race, and (b) guard against silent regressions when
the algorithms change.

  python src/validate_races.py                    # runs the default (IMSA) RACES set
  python src/validate_races.py --series indycar   # runs the built-in INDYCAR_RACES set
  python src/validate_races.py <zip> <zip> …      # ad-hoc set
  python src/validate_races.py --keep-dbs         # leave the temp DBs for inspection

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
RACES = [
    f"{DL}/2026-01-24 18-37 IMSA WeatherTech SportsCar Championship - Rolex 24 at Daytona - Race.zip",
    f"{DL}/2025-10-11 16-07 IMSA WeatherTech SportsCar Championship - 28th Annual Motul Petit Le Mans - Race.zip",
    f"{DL}/2025-09-21 15-37 IMSA WeatherTech SportsCar Championship - Tire Rack.com Battle On The Bricks - Race.zip",
    f"{DL}/2026-05-03 19-57 IMSA WeatherTech SportsCar Championship - StubHub Monterey SportsCar Championship - Race.zip",
    f"{DL}/2026-04-18 20-02 IMSA WeatherTech SportsCar Championship - Acura Grand Prix of Long Beach - Race.zip",
    f"{DL}/2026-05-30 19-57 IMSA WeatherTech SportsCar Championship - Chevrolet Detroit Sports Car Classic - Race.zip",
]

# IndyCar regression set — 2026 season archives, tagged by track type since
# pit/fuel/caution dynamics differ sharply between them (see weekend_qa.md
# "Do we need more IndyCar archives?"). Deliberately NOT run yet as of
# 2026-07-02 — first validation happens post-weekend against the fresh
# Sunday 07-05 race archive, so the model's performance on genuinely unseen
# data is measured honestly rather than after already having been tuned
# against this same set.
IC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "indycar archives")
INDYCAR_RACES = [
    # ovals
    f"{IC}/2026-05-24 16-42 IndyCar - 110th Running of the Indianapolis 500 - Race.zip",
    f"{IC}/2026-06-08 01-22 IndyCar - 10th Annual Bommarito Automotive Group 500 - Race.zip",
    f"{IC}/2026-03-07 20-17 IndyCar - Good Ranchers 250 - Race.zip",           # Texas Motor Speedway
    # street circuits
    f"{IC}/2026-03-01 17-26 IndyCar - Firestone Grand Prix of St Petersburg - Race.zip",
    f"{IC}/2026-03-15 16-14 IndyCar - Java House Grand Prix of Arlington - Race.zip",
    f"{IC}/2026-04-19 21-54 IndyCar - ACURA Grand Prix of Long Beach - Race.zip",
    f"{IC}/2026-05-31 16-49 IndyCar - Chevrolet Detroit Grand Prix - Race.zip",
    # road courses
    f"{IC}/2026-03-29 17-14 IndyCar - Children's of Alabama Indy Grand Prix - Race.zip",  # Barber Motorsports Park
    f"{IC}/2026-05-09 20-54 IndyCar - SONSIO GRAND PRIX - Race.zip",           # road course, exact circuit unconfirmed — verify if metrics look off
    f"{IC}/2026-06-21 18-24 IndyCar - XPEL Grand Prix at Road America - Race.zip",
]


def _short(name: str) -> str:
    """Pull a readable circuit/event label out of the long archive filename."""
    base = os.path.basename(name)
    # files look like 'YYYY-MM-DD HH-MM IMSA … - <event> - Race.zip'
    parts = base.rsplit(" - ", 2)
    event = parts[-2] if len(parts) >= 2 else base
    for noise in ("28th Annual Motul ", "Tire Rack.com ", "Acura ", "StubHub ",
                  "Chevrolet ", "Grand Prix of "):
        event = event.replace(noise, "")
    return event[:26]


def run_one(zip_path: str, keep: bool):
    r = timing71.load(zip_path)
    fd, db = tempfile.mkstemp(suffix=".db", prefix="valrace_")
    os.close(fd); os.remove(db)
    with open(os.devnull, "w") as null, contextlib.redirect_stdout(null):
        replay.build(r, db, oid="replay", cadence_s=60)
    import sqlite3
    conn = sqlite3.connect(db)
    stop  = evaluator.eval_stop_time(conn, "replay")
    net   = evaluator.eval_net_position(conn, "replay")
    catch = evaluator.eval_catch(conn, "replay")
    conn.close()
    if not keep:
        os.remove(db)
    hrs = (r.full_frames[-1][0] - r.full_frames[0][0]) / 3600
    return {"label": _short(zip_path), "hrs": hrs, "stop": stop, "net": net, "catch": catch}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zips", nargs="*", help="replay zips (default: built-in RACES)")
    ap.add_argument("--series", choices=["imsa", "indycar"], default="imsa",
                     help="which built-in regression set to use when no zips are given")
    ap.add_argument("--keep-dbs", action="store_true", help="leave temp DBs in place")
    args = ap.parse_args()
    default_set = INDYCAR_RACES if args.series == "indycar" else RACES
    zips = args.zips or default_set

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
           f"{'netMAE':>6} {'trkMAE':>6} {'edge':>6} | {'catch%':>6} {'late':>5}")
    print(hdr); print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        s, n, c = r["stop"], r["net"], r["catch"]
        sm = f"{s['mae_ms']/1000:6.1f}s" if s else "    —"
        sb = f"{s['bias_ms']/1000:+5.1f}" if s else "    —"
        nm = f"{n['net_mae']:6.2f}" if n else "     —"
        tm = f"{n['track_mae']:6.2f}" if n else "     —"
        ed = f"{n['improvement_pct']:+5.0f}%" if n else "     —"
        win = "✓" if (n and n['improvement_pct'] > 0) else " "
        cr = f"{c['hit_rate']*100:5.0f}%" if c else "    —"
        cl = f"{c['median_late_laps']:4.1f}" if c and c['median_late_laps'] is not None else "   —"
        print(f"  {r['label']:26} {r['hrs']:4.1f} | {sm} {sb} | "
              f"{nm} {tm} {ed}{win}| {cr} {cl}")
    print()


if __name__ == "__main__":
    main()
