"""
build_f1_team_table.py — script data/f1_<year>.json (car#→TLA/team/colour) off a
FastF1 session's own results, rather than hand-typing a roster that drifts every
time a team repaints or a driver is swapped mid-season.

    python src/build_f1_team_table.py 2026 [gp] [session]

Defaults to the most recently COMPLETED round of the given season (so a bare
"python src/build_f1_team_table.py 2026" always gets the current grid without
having to know what round we're on). Pass gp/session explicitly to pin a
specific event instead (e.g. a season-opener before round 2 has run).
"""

import argparse
import json
import os
import sys
from datetime import datetime

try:
    import fastf1
except ImportError:
    sys.exit("fastf1 not installed — pip install fastf1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _latest_completed_round(year: int) -> int:
    sched = fastf1.get_event_schedule(year, include_testing=False)
    now = datetime.now()
    done = sched[sched["EventDate"] < now]
    if done.empty:
        sys.exit(f"no completed {year} rounds yet — pass gp/session explicitly")
    return int(done.iloc[-1]["RoundNumber"])


def build(year: int, gp, session_type: str) -> dict:
    session = fastf1.get_session(year, gp, session_type)
    session.load(telemetry=False, weather=False, laps=False)
    results = session.results

    table = {}
    for drv_num, row in results.iterrows():
        car = str(drv_num)
        table[car] = {
            "tla": row.get("Abbreviation", car),
            "team": row.get("TeamName", ""),
            "color": "#" + str(row.get("TeamColor", "808080")).lstrip("#").upper(),
        }
    return {
        "season": year,
        "as_of": f"{session.event.EventName} — {session.name}",
        "generated": datetime.now().isoformat(timespec="seconds"),
        "drivers": table,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("year", type=int)
    ap.add_argument("gp", nargs="?", default=None,
                    help="Round number or GP name (default: latest completed round)")
    ap.add_argument("session", nargs="?", default="R",
                    help="Session type (default R)")
    ap.add_argument("--cache", default=os.path.join(ROOT, "data", "fastf1_cache"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    fastf1.Cache.enable_cache(args.cache)

    gp = args.gp
    if gp is None:
        gp = _latest_completed_round(args.year)
    else:
        try:
            gp = int(gp)
        except ValueError:
            pass

    print(f"Loading FastF1: {args.year} {gp} {args.session}…")
    doc = build(args.year, gp, args.session)
    print(f"  {doc['as_of']}  ({len(doc['drivers'])} drivers)")

    out_path = args.out or os.path.join(ROOT, "data", f"f1_{args.year}.json")
    with open(out_path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    print(f"  written → {out_path}")


if __name__ == "__main__":
    main()
