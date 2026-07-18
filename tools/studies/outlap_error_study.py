#!/usr/bin/env python3
"""Out-lap error study — is NET prediction error concentrated around pit cycles?

Buckets every prediction sample by the car's position in its own pit cycle
(laps since last stop / laps until next stop) and reports, per bucket:

  table 1 (MAE):  net/track/projected MAE vs final classification, plus the
                  net-minus-track delta — where does NET under/over-perform
                  the naive running-order baseline?
  table 2 (bias): SIGNED mean error (prediction - final; positive = predicted
                  worse than the car finished) — separates "model is noisy
                  here" from "model is systematically pessimistic/optimistic
                  here", which points at the mechanism.
  stops-lag:      fraction of samples where the feed's pit counter trails the
                  stops pit_events has already detected — measures whether
                  post-stop state lag (not car pace) explains a bucket.

Grading convention matches evaluator.eval_net_position (final = car's last
observed pos_in_class). Findings from the 2026-07-18 run: BACKLOG.md
decisions log ("out-lap coefficient" entry).

Usage:
  ./venv/bin/python tools/studies/outlap_error_study.py [imsa|wec|all] [dbcache_dir]

With dbcache_dir, replay DBs are built once and reused on later runs (a full
set rebuild takes ~2 min per race).
"""
import contextlib
import hashlib
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src"))
os.chdir(_REPO)

import validate_races as vr  # reuse race lists + builders

SINCE_BUCKETS = [(0, "0 (out-lap)"), (1, "1 (1st flying)"), (2, "2"), (3, "3"),
                 (6, "4-6"), (12, "7-12"), (10**9, "13+")]
UNTIL_BUCKETS = [(1, "-1 (in-lap next)"), (2, "-2"), (3, "-3"), (10**9, "-4 or more")]
ORDER = (["pre-first-stop"] + [n for _, n in SINCE_BUCKETS]
         + [n for _, n in UNTIL_BUCKETS])


def bucket_of(v, table):
    for hi, name in table:
        if v <= hi:
            return name
    return table[-1][1]


def analyze(db, oid, agg):
    conn = sqlite3.connect(db)
    pits = defaultdict(list)
    for car, pl in conn.execute(
            "SELECT car_number, pit_lap FROM pit_events "
            "WHERE session_oid=? AND pit_lap IS NOT NULL", (oid,)):
        pits[car].append(pl)
    for car in pits:
        pits[car].sort()

    rows = conn.execute(
        """SELECT car_number, laps, net_position, pos_in_class,
                  projected_finish, stops
             FROM predictions
             WHERE session_oid=? AND net_position IS NOT NULL
               AND pos_in_class IS NOT NULL AND laps IS NOT NULL
             ORDER BY car_number, ts""", (oid,)).fetchall()
    conn.close()

    by_car = defaultdict(list)
    for r in rows:
        by_car[r[0]].append(r[1:])

    for car, seq in by_car.items():
        final = seq[-1][2]
        pl = pits.get(car, [])
        for laps, net, pic, proj, stops in seq:
            prev = nxt = None
            for p in pl:
                if p <= laps:
                    prev = p
                elif nxt is None:
                    nxt = p
            detected = sum(1 for p in pl if p <= laps)
            keys = [("since", bucket_of(laps - prev, SINCE_BUCKETS))
                    if prev is not None else ("since", "pre-first-stop")]
            if nxt is not None:
                keys.append(("until", bucket_of(nxt - laps, UNTIL_BUCKETS)))
            for kind, b in keys:
                a = agg[(kind, b)]
                a["n"] += 1
                a["net"] += abs(net - final);  a["snet"] += net - final
                a["trk"] += abs(pic - final);  a["strk"] += pic - final
                if proj is not None:
                    a["proj"] += abs(proj - final); a["sproj"] += proj - final
                    a["pn"] += 1
                if stops is not None and stops < detected:
                    a["lag"] += 1


def _cached_build(path, cache_dir):
    if not cache_dir:
        fd, db = tempfile.mkstemp(suffix=".db", prefix="outlap_")
        os.close(fd); os.remove(db)
        keep = False
    else:
        os.makedirs(cache_dir, exist_ok=True)
        h = hashlib.sha1(path.encode()).hexdigest()[:12]
        db = os.path.join(cache_dir, f"{h}.db")
        keep = True
        if os.path.exists(db):
            return db, "replay", keep   # zips always build under oid="replay"
    if path.endswith(".jsonl.gz"):
        oid, _ = vr._build_from_capture(path, db)
    else:
        oid, _ = vr._build_from_zip(path, db)
    return db, oid, keep


def run_set(paths, label, cache_dir):
    agg = defaultdict(lambda: defaultdict(float))
    for path in paths:
        if not os.path.exists(path):
            print(f"  SKIP (missing): {os.path.basename(path)}", flush=True)
            continue
        db, oid, keep = _cached_build(path, cache_dir)
        try:
            analyze(db, oid, agg)
            print(f"  done: {vr._short(path)}", flush=True)
        finally:
            if not keep:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(db)

    print(f"\n=== {label} — MAE vs final (net-trk >0 means NET worse) ===")
    print(f"{'bucket':<18}{'n':>8}{'net MAE':>9}{'trk MAE':>9}{'net-trk':>9}{'proj MAE':>10}")
    for kind in ("since", "until"):
        for name in ORDER:
            a = agg.get((kind, name))
            if not a or a["n"] == 0:
                continue
            n, pn = a["n"], a["pn"]
            proj = a["proj"] / pn if pn else float("nan")
            print(f"{name:<18}{int(n):>8}{a['net']/n:>9.2f}{a['trk']/n:>9.2f}"
                  f"{(a['net']-a['trk'])/n:>+9.2f}{proj:>10.2f}")
        print()

    print(f"=== {label} — signed bias (pred-final; + = predicted worse) ===")
    print(f"{'bucket':<18}{'n':>8}{'net bias':>9}{'trk bias':>9}{'proj bias':>10}{'stops-lag%':>11}")
    for kind in ("since", "until"):
        for name in ORDER:
            a = agg.get((kind, name))
            if not a or a["n"] == 0:
                continue
            n, pn = a["n"], a["pn"]
            sproj = a["sproj"] / pn if pn else float("nan")
            print(f"{name:<18}{int(n):>8}{a['snet']/n:>+9.2f}{a['strk']/n:>+9.2f}"
                  f"{sproj:>+10.2f}{100*a['lag']/n:>10.1f}%")
        print()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    cache = sys.argv[2] if len(sys.argv) > 2 else None
    if which in ("imsa", "all"):
        print("IMSA set building...", flush=True)
        run_set(vr.RACES, "IMSA (7 races)", cache)
    if which in ("wec", "all"):
        print("WEC set building...", flush=True)
        run_set(vr.WEC_RACES + vr.WEC_CAPTURES, "WEC (8 zips + 1 capture)", cache)
    print("STUDY COMPLETE", flush=True)
