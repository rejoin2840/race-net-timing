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
import json
import hashlib
import math
import subprocess
from pathlib import Path

import config
import evaluator
import replay
import timing71

# default regression set — edit as the library of complete archives grows.
# Only COMPLETE (run-to-chequered) archives belong here; truncated ones skew
# the numbers (final pit cycles unresolved). Verified complete 2026-06-28.
_REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
# relative ARCHIVE_DIR resolves against the repo root, not the cwd, so any clone works
DL = os.path.expanduser(config.CONFIG.ARCHIVE_DIR)
if not os.path.isabs(DL):
    DL = os.path.join(_REPO_ROOT, DL)
WEC_DIR = os.path.join(_REPO_ROOT, "wec-archives")

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
    if not r.complete:
        raise ValueError("archive has no terminal chequered flag")
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
    if res["dispatch_errors"] or not res["complete"]:
        raise ValueError(f"capture incomplete or dispatch errors: {res}")
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
    if not net or net.get("proj_mae") is None or not stop:
        raise ValueError("required finish/stop metrics are absent")
    row = {"input": os.path.basename(path), "sha256": file_hash(path),
           "label": _short(path), "hrs": hrs, "complete": True,
           "stop": stop, "net": net, "catch": catch}
    validate_numbers(row)
    return row


BASELINE = os.path.join(_REPO_ROOT, "data", "validation_baseline.json")
TOLERANCES = {"net.proj_mae": 0.10, "net.net_mae": 0.10,
              "stop.mae_ms": 1000.0, "catch.hit_rate": 0.05}


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_numbers(value):
    if isinstance(value, dict):
        for v in value.values():
            validate_numbers(v)
    elif isinstance(value, list):
        for v in value:
            validate_numbers(v)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite evaluation metric")


def compare_baseline(rows, baseline):
    errors = []
    reference = {r["input"]: r for r in baseline["races"]}
    for row in rows:
        old = reference.get(row["input"])
        if old is None or old["sha256"] != row["sha256"]:
            errors.append(f"{row['input']}: missing baseline or input identity changed")
            continue
        for key, tolerance in baseline["tolerances"].items():
            group, metric = key.split(".")
            before = (old.get(group) or {}).get(metric)
            after = (row.get(group) or {}).get(metric)
            if before is None:
                continue
            if after is None or (before - after if metric == "hit_rate" else after - before) > tolerance:
                errors.append(f"{row['input']}: {key} {before} -> {after}, tolerance {tolerance}")
        if old.get("catch") and (row.get("catch") or {}).get("n", 0) < old["catch"]["n"] * 0.5:
            errors.append(f"{row['input']}: catch sample coverage fell below half the baseline")
    return errors


def provenance():
    cfg = config.CONFIG.as_dict()
    digest = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
    sources = {p.name: file_hash(p) for p in Path(__file__).parent.glob("*.py")}
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    return {"config": cfg, "config_sha256": digest, "commit": commit,
            "source_sha256": sources}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zips", nargs="*", help="replay zips (default: built-in IMSA RACES)")
    ap.add_argument("--keep-dbs", action="store_true", help="leave temp DBs in place")
    ap.add_argument("--wec", action="store_true", help="run WEC regression set instead of IMSA")
    ap.add_argument("--all", action="store_true", help="run both IMSA and WEC sets")
    ap.add_argument("--json", dest="json_path", help="write reproducible results JSON")
    ap.add_argument("--baseline", default=BASELINE, help="approved baseline JSON")
    ap.add_argument("--write-baseline", metavar="NEW_FILE", help="explicitly establish a new baseline (must not exist)")
    args = ap.parse_args()
    if args.write_baseline and os.path.exists(args.write_baseline):
        ap.error("baseline exists; choose a new file and review it before replacing the approved baseline")
    if config.CONFIG.error:
        ap.error(f"invalid configuration: {config.CONFIG.error}")
    identity = provenance()
    if args.zips:
        zips = args.zips
    elif args.all:
        zips = RACES + WEC_RACES + WEC_CAPTURES
    elif args.wec:
        zips = WEC_RACES + WEC_CAPTURES
    else:
        zips = RACES

    rows = []
    errors = []
    for z in zips:
        if not os.path.exists(z):
            print(f"  ⚠ missing, skipped: {os.path.basename(z)}", file=sys.stderr)
            errors.append(f"missing required input: {os.path.basename(z)}")
            continue
        print(f"  running {_short(z)} …", file=sys.stderr)
        try:
            rows.append(run_one(z, args.keep_dbs))
        except Exception as e:
            print(f"  ⚠ failed {_short(z)}: {e}", file=sys.stderr)
            errors.append(f"{os.path.basename(z)}: {e}")

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
    config.CONFIG.reload_if_changed()
    if provenance()["config_sha256"] != identity["config_sha256"]:
        errors.append("configuration changed during evaluation")
    if not args.write_baseline:
        try:
            baseline = json.loads(Path(args.baseline).read_text())
            if baseline["provenance"]["config_sha256"] != identity["config_sha256"]:
                errors.append("configuration differs from baseline; evaluate and explicitly review a new baseline")
            errors.extend(compare_baseline(rows, baseline))
        except (OSError, KeyError, ValueError) as exc:
            errors.append(f"cannot load baseline: {exc}")
    result = {"format": 1, "method": "causal replay; terminal classification", "provenance": identity,
              "tolerances": TOLERANCES, "races": rows, "errors": errors}
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    if args.write_baseline and not errors:
        with open(args.write_baseline, "x") as f:
            json.dump(result, f, indent=2, allow_nan=False)
            f.write("\n")
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    return 1 if errors or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
