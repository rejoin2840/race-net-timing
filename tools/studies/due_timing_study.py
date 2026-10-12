#!/usr/bin/env python3
"""Causal DUE audit. Current status/findings: BACKLOG.md (Epics 2 and 11).

python tools/studies/due_timing_study.py --all --output data/due_timing_audit.json
Observers do not change calculator inputs or decisions. Scoring sees pit truth
only after replay finishes. Pit entry is a visit, not proof of fuel necessity.
"""
import argparse
import bisect
import contextlib
import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import calculator
import config
import replay
import timing71
import validate_races as vr

CADENCE_S = 5
METHOD = "episode active at last sampled pre-entry observation, within previous exit -> entry"


def config_stamp():
    return hashlib.sha256(json.dumps(config.CONFIG.as_dict(), sort_keys=True).encode()).hexdigest()


def flag_bucket(flag):
    return "green" if flag == "GF" else "caution" if calculator._is_caution(flag) else "other"


def legacy_due(car, series):
    stint = car.pit_window_open and car.fuel_laps_left is not None and car.fuel_laps_left <= 1
    vet = (series == "wec" and car.fuel_pct is not None
           and car.fuel_pct <= config.CONFIG.as_dict(series)["VET_DUE_PCT"]
           and (car.est_stops_left or 0) > 0)
    return bool(stint or vet)


class Observer:
    def __init__(self):
        self.samples = defaultdict(list)
        self.classes = {}
        self.series = None
        self.pits = defaultdict(list)
        self.open_pits = {}
        self.sample_times = []
        self.decision_end_ms = None
        self.config_sha256 = config_stamp()

    def sample(self, conn, oid, ts, ctx, cars):
        if config.CONFIG.error or config_stamp() != self.config_sha256:
            raise ValueError("configuration changed or became invalid during replay")
        if ctx.is_race and ctx.is_finished and self.decision_end_ms is None:
            self.decision_end_ms = ts
        if not ctx.is_race or ctx.is_finished:
            return
        self.series = ctx.profile.key
        self.sample_times.append(ts)
        for car in cars:
            self.classes[car.car_number] = car.car_class
            if car.laps is None:
                continue
            # Both policies receive the same chronologically learned state/VET.
            self.samples[car.car_number].append((ts, car.laps, car.fuel_due == "due",
                                                 legacy_due(car, self.series), flag_bucket(ctx.flag)))

    def event(self, client, channel, data, ts):
        if channel not in ("pit-in", "pit-out") or not isinstance(data, dict) or ts is None:
            return
        car = client._resolve_car(data)
        if not car:
            raise ValueError("unresolved observed pit event")
        if channel == "pit-in":
            if car in self.open_pits:
                raise ValueError("repeated pit entry without observed exit")
            row = {"entry_ms": ts, "exit_ms": None,
                   "pit_lap": client.state.car_laps.get(car, {}).get("lap"),
                   "flag": client.state.current_flag}
            self.pits[car].append(row)
            self.open_pits[car] = row
        elif car in self.open_pits:
            self.open_pits.pop(car)["exit_ms"] = ts


def zip_pits(archive):
    timelines = defaultdict(list)
    for ts, frame in archive.full_frames:
        for row in frame.get("cars", []):
            lap = timing71._num(row[archive.col["Laps"]])
            if lap is not None:
                timelines[str(row[archive.col["Num"]])].append((int(ts * 1000), int(lap)))
    flags = archive.flag_timeline()
    flag_ts = [t for t, _ in flags]
    result = defaultdict(list)
    for car, stops in archive.pit_stops().items():
        seq = timelines[str(car)]
        times = [t for t, _ in seq]
        for stop in stops:
            if stop.in_ts is None:
                continue
            i = bisect.bisect_right(times, stop.in_ts) - 1
            fi = bisect.bisect_right(flag_ts, stop.in_ts) - 1
            raw = flags[fi][1].lower() if fi >= 0 else "unknown"
            flag = "GF" if raw == "green" else "FCY" if any(x in raw for x in ("yellow", "fcy", "caution")) else {"safetycar": "SC", "vsc": "VSC", "red": "RF"}.get(raw, "?")
            result[str(car)].append({"entry_ms": stop.in_ts, "exit_ms": stop.out_ts,
                                     "pit_lap": seq[i][1] if i >= 0 else None, "flag": flag})
    return result


def episodes(samples, index):
    result = []
    active = None
    for sample in samples:
        if sample[index]:
            if active is None:
                active = {"start_ms": sample[0], "start_lap": sample[1], "flag_bucket": sample[4]}
                result.append(active)
            active["last_ms"] = sample[0]
        else:
            active = None
    return result


def score(observer):
    stops_out, episodes_out = [], []
    policies = [("current", 2)] + ([("legacy_stint_same_vet", 3)] if observer.series == "wec" else [])
    for car in sorted(set(observer.samples) | set(observer.pits)):
        samples = observer.samples[car]
        times = [s[0] for s in samples]
        pits = sorted(observer.pits[car], key=lambda p: p["entry_ms"])
        lower = -float("inf")
        for ordinal, pit in enumerate(pits, 1):
            a = bisect.bisect_left(times, lower)
            b = bisect.bisect_left(times, pit["entry_ms"])
            interval = samples[a:b]
            for policy, index in policies:
                eps = episodes(interval, index)
                matched = eps[-1] if interval and interval[-1][index] else None
                lead = pit["pit_lap"] - matched["start_lap"] if matched and pit["pit_lap"] is not None else None
                outside = observer.decision_end_ms is not None and pit["entry_ms"] >= observer.decision_end_ms
                eligible = not outside and bool(interval) and pit["pit_lap"] is not None and (lead is None or lead >= 0)
                record = {"car": car, "class": observer.classes.get(car, "unknown"),
                          "series": observer.series, "policy": policy, "stop_number": ordinal,
                          "stop_group": "first" if ordinal == 1 else "subsequent",
                          "flag_bucket": flag_bucket(pit["flag"]), **pit,
                          "interval_start_ms": None if ordinal == 1 else lower,
                          "eligible": eligible, "warning_ms": matched["start_ms"] if matched else None,
                          "lead_laps": lead if eligible else None,
                          "lead_seconds": (pit["entry_ms"] - matched["start_ms"]) / 1000 if matched else None,
                          "missed": eligible and matched is None,
                          "exclusion": None if eligible else "after calculator finish" if outside else "missing pre-entry observations/lap or inconsistent lap counter"}
                stops_out.append(record)
                for ep in eps:
                    episodes_out.append({"car": car, "class": record["class"], "series": observer.series,
                                         "policy": policy, "stop_group": record["stop_group"], **ep,
                                         "next_entry_ms": pit["entry_ms"],
                                         "outcome": "entry_outside_decision_window" if outside else "matched" if ep is matched else "withdrawn_before_entry"})
            # An exit is the reset boundary; unknown exit makes the tail unscorable.
            lower = pit["exit_ms"] if pit["exit_ms"] is not None else float("inf")
        for policy, index in policies:
            for ep in episodes(samples[bisect.bisect_left(times, lower):], index):
                episodes_out.append({"car": car, "class": observer.classes.get(car, "unknown"),
                                     "series": observer.series, "policy": policy,
                                     "stop_group": "subsequent" if pits else "first", **ep,
                                     "next_entry_ms": None, "outcome": "no_subsequent_entry_observed"})
    return stops_out, episodes_out


def summarize(stops, eps):
    groups = defaultdict(lambda: {"stops": [], "episodes": []})
    for kind, records in (("stops", stops), ("episodes", eps)):
        for r in records:
            for dimensions in ((), ("series",), ("series", "class"), ("series", "stop_group"),
                               ("series", "flag_bucket"), ("series", "class", "stop_group", "flag_bucket")):
                key = (r["policy"],) + tuple(f"{d}={r[d]}" for d in dimensions)
                groups[key][kind].append(r)
    result = []
    for key, group in sorted(groups.items()):
        eligible = [s for s in group["stops"] if s["eligible"]]
        leads = [s["lead_laps"] for s in eligible if s["lead_laps"] is not None]
        within = sum(0 <= lead <= 2 for lead in leads)
        result.append({"group": " / ".join(key), "stops": len(group["stops"]),
                       "eligible": len(eligible), "warned": len(leads), "missed": sum(s["missed"] for s in eligible),
                       "mean_lead_laps": statistics.mean(leads) if leads else None,
                       "median_lead_laps": statistics.median(leads) if leads else None,
                       "early_pct_of_warnings": 100 * sum(x > 2 for x in leads) / len(leads) if leads else None,
                       "within_pct_of_warnings": 100 * within / len(leads) if leads else None,
                       "within_pct_of_eligible": 100 * within / len(eligible) if eligible else None,
                       "withdrawn_episodes": sum(e["outcome"] == "withdrawn_before_entry" for e in group["episodes"]),
                       "episodes_without_subsequent_stop": sum(e["next_entry_ms"] is None for e in group["episodes"])})
    return result


def run_one(path):
    if not path.is_file():
        raise ValueError(f"missing required input: {path.name}")
    obs = Observer()
    input_sha = vr.file_hash(path)
    with tempfile.TemporaryDirectory(prefix="due-audit-") as tmp:
        db_path = str(Path(tmp) / "race.db")
        if path.name.endswith(".jsonl.gz"):
            import wec_live
            from db import RaceDB
            client = wec_live.WecLiveClient(db_path="", no_db=True)
            client.db = RaceDB(db_path)
            try:
                status = wec_live.replay_predict(client, path, CADENCE_S, on_sample=obs.sample, on_event=obs.event)
                if status["dispatch_errors"] or not status["complete"]:
                    raise ValueError("capture incomplete or processing failed")
            finally:
                client.db.close()
        else:
            archive = timing71.load(str(path))
            if not archive.complete:
                raise ValueError("archive has no terminal flag")
            with contextlib.redirect_stdout(sys.stderr):
                replay.build(archive, db_path, cadence_s=CADENCE_S, on_sample=obs.sample)
            obs.pits = zip_pits(archive)  # scorer only, after every prediction
        if not obs.samples or not obs.pits:
            raise ValueError("required samples/pit evidence absent")
        stops, eps = score(obs)
        if not any(s["eligible"] for s in stops):
            raise ValueError("no measurable pit visits")
        if vr.file_hash(path) != input_sha:
            raise ValueError("input changed during replay")
        gaps = [b-a for a,b in zip(obs.sample_times, obs.sample_times[1:])]
        return {"input": path.name, "sha256": input_sha, "series": obs.series,
                "sample_cycles": len(obs.sample_times), "maximum_sample_gap_s": max(gaps) / 1000 if gaps else None,
                "first_sample_ms": obs.sample_times[0], "last_sample_ms": obs.sample_times[-1],
                "calculator_finish_ms": obs.decision_end_ms,
                "stops": stops, "episodes": eps, "summary": summarize(stops, eps)}


def markdown(result):
    text = ["# DUE timing audit", "", "> Dated evidence; current status: [BACKLOG.md](../BACKLOG.md).", "",
            f"Method: {METHOD}. Target cadence: {CADENCE_S}s; actual gaps are recorded per input.",
            "Pit visits are not fuel-necessity labels. Withdrawn episodes are separate from episodes with no later observed entry.",
            "This audits calculator decisions; renderer freshness and live acceptance remain separate.", "",
            "Visits after the calculator finishes are retained as evidence but excluded from timing scores.", "",
            "Early % uses warned stops; within % uses all eligible stops. Misses are included in that denominator.", "",
            "| Group | Eligible | Warned | Missed | Mean lead | Median lead | >2L early % | 0–2L / eligible % | No later entry episodes |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result["summary"]:
        def fmt(v): return "—" if v is None else f"{v:.2f}"
        text.append(f"| {row['group']} | {row['eligible']} | {row['warned']} | {row['missed']} | {fmt(row['mean_lead_laps'])} | {fmt(row['median_lead_laps'])} | {fmt(row['early_pct_of_warnings'])} | {fmt(row['within_pct_of_eligible'])} | {row['episodes_without_subsequent_stop']} |")
    if result["errors"]:
        text += ["", "Incomplete run: " + "; ".join(result["errors"])]
    return "\n".join(text) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--wec", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.with_suffix(".md").exists():
        parser.error("choose a new output; historical evidence is never overwritten")
    identity = vr.provenance()
    identity.update({"study_sha256": vr.file_hash(__file__), "python": platform.python_version(),
                     "implementation": platform.python_implementation(),
                     "dependencies": {n: importlib.metadata.version(n) for n in ("msgpack", "signalrcore", "websockets")}})
    paths = args.inputs or (vr.RACES + vr.WEC_RACES + vr.WEC_CAPTURES if args.all else
                            vr.WEC_RACES + vr.WEC_CAPTURES if args.wec else vr.RACES)
    races, errors = [], []
    if config.CONFIG.error:
        errors.append("invalid configuration")
    else:
        for path in paths:
            print(f"Auditing {Path(path).name}", file=sys.stderr, flush=True)
            try:
                races.append(run_one(Path(path)))
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
                print(errors[-1], file=sys.stderr, flush=True)
    if vr.provenance()["config_sha256"] != identity["config_sha256"]:
        errors.append("configuration changed during audit")
    if vr.provenance()["source_sha256"] != identity["source_sha256"] or vr.file_hash(__file__) != identity["study_sha256"]:
        errors.append("source files changed during audit")
    all_stops = [s for r in races for s in r["stops"]]
    all_eps = [e for r in races for e in r["episodes"]]
    result = {"format": 1, "method": METHOD, "cadence_s": CADENCE_S, "provenance": identity,
              "races": races, "summary": summarize(all_stops, all_eps), "errors": errors}
    vr.validate_numbers(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    args.output.with_suffix(".md").write_text(markdown(result))
    return 1 if errors or not races else 0


if __name__ == "__main__":
    raise SystemExit(main())
