"""
weekend_conductor.py — unattended supervisor for this weekend's live sessions.

Sleeps until each scheduled session, launches the right live scraper (and,
for races, the headless predictor logger) as subprocesses writing to the
SAME production DB the GUI reads from, then stops them and runs either the
evaluator (races) or a lighter data-quality health check (practice/quali)
once the window closes. Everything funnels into one log file.

Design choices (see the plan doc for the "why"):
  - Same DB as the GUI (no --db override) — so opening dashboard_calm.py
    while this is running just shows the live session, no picker action
    needed. Don't click "Launch Live Feed" for a series this is covering;
    the picker will warn if you try (lock file check).
  - One thread per scheduled session (they can overlap, e.g. Sat IndyCar
    FP2 vs F1 Quali) rather than a single sequential loop.
  - Generous start-early / stop-late buffers, because sessions commonly
    run behind the posted time (TV coverage, red flags, etc).
  - Every failure is caught and logged per-session; one session going wrong
    never takes down the rest of the weekend.

Run (leave this in its own Terminal tab, ideally under caffeinate):
  caffeinate -s venv/bin/python src/weekend_conductor.py

Dry run (fires ONE short fake IndyCar session ~1 min from now, ~2 min
window, to prove the whole start/log/stop/healthcheck cycle end-to-end
before trusting it with the real schedule):
  venv/bin/python src/weekend_conductor.py --test-now
"""

import argparse
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
DATA_DIR = ROOT / "data"
PYTHON = str(ROOT / "venv" / "bin" / "python")

sys.path.insert(0, str(SRC))
import calculator          # noqa: E402
import session_healthcheck  # noqa: E402

CONDUCTOR_LOG = LOG_DIR / "weekend_conductor.log"
_log_lock = threading.Lock()

# start this many minutes before the posted time (sessions rarely start
# early; scrapers are harmless/inert before a session exists)
START_BUFFER_MIN = 15

# how long AFTER the posted start to keep the window open, per kind —
# generous, since sessions commonly run behind
WINDOW_MIN = {
    "practice": 90,
    "quali": 90,
    "sprint_race": 120,
    "race": 210,
}

SCRAPER = {
    "f1": "f1_live.py",
    "indycar": "indycar_live.py",
}

# ── schedule (Paul-provided, EDT, 2026-07-02) ────────────────────────────────
def _dt(month, day, hour, minute):
    return datetime(2026, month, day, hour, minute)


SCHEDULE = [
    # series,     label,             kind,          start
    ("f1",       "FP1",              "practice",     _dt(7, 3, 7, 30)),
    ("f1",       "Sprint Quali",     "quali",        _dt(7, 3, 11, 30)),
    ("indycar",  "FP1",              "practice",     _dt(7, 3, 15, 0)),
    ("f1",       "Sprint Race",      "sprint_race",  _dt(7, 4, 7, 0)),
    ("f1",       "GP Quali",         "quali",        _dt(7, 4, 11, 0)),
    ("indycar",  "FP2",              "practice",     _dt(7, 4, 10, 0)),
    ("indycar",  "Qualifying",       "quali",        _dt(7, 4, 14, 30)),
    ("f1",       "Race",             "race",         _dt(7, 5, 10, 0)),
    ("indycar",  "Race",             "race",         _dt(7, 5, 12, 30)),
]


def _log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        print(line, flush=True)
        with open(CONDUCTOR_LOG, "a") as f:
            f.write(line + "\n")


def _lock_path(series: str) -> Path:
    return DATA_DIR / f".{series}_live.lock"


def _write_lock(series: str, pid: int):
    _lock_path(series).write_text(f"{pid} {datetime.now().isoformat()}\n")


def _remove_lock(series: str):
    p = _lock_path(series)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def _stop_proc(proc: "subprocess.Popen | None", name: str):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _log(f"  ⚠ {name} didn't exit on SIGTERM within 10s, killing")
        proc.kill()
        proc.wait(timeout=5)


def run_session(series: str, label: str, kind: str, start: datetime):
    slug = f"{series}_{label}".lower().replace(" ", "_")
    now = datetime.now()
    launch_at = start - timedelta(minutes=START_BUFFER_MIN)
    window_min = WINDOW_MIN[kind]
    stop_at = start + timedelta(minutes=window_min)

    if stop_at <= now:
        _log(f"SKIP {series} {label}: window already passed "
             f"(would have stopped {stop_at:%H:%M})")
        return

    if launch_at > now:
        wait_s = (launch_at - now).total_seconds()
        _log(f"WAIT {series} {label}: sleeping {wait_s/60:.0f} min, "
             f"launch at {launch_at:%H:%M}")
        time.sleep(wait_s)
    else:
        _log(f"{series} {label}: launch time already passed, starting now")

    scraper_log_path = LOG_DIR / f"conductor_{slug}_{start:%Y%m%d}.log"
    _log(f"START {series} {label} — scraper log: {scraper_log_path}")

    _write_lock(series, pid=0)  # placeholder until we have the real pid
    scraper_proc = None
    predictor_proc = None
    try:
        with open(scraper_log_path, "w") as slog:
            scraper_proc = subprocess.Popen(
                [PYTHON, "-u", str(SRC / SCRAPER[series])],
                cwd=str(ROOT), stdout=slog, stderr=subprocess.STDOUT,
            )
        _write_lock(series, scraper_proc.pid)
        _log(f"  {series} scraper pid={scraper_proc.pid}")

        if kind in ("race", "sprint_race"):
            pred_log_path = LOG_DIR / f"conductor_{slug}_predictor_{start:%Y%m%d}.log"
            with open(pred_log_path, "w") as plog:
                predictor_proc = subprocess.Popen(
                    [PYTHON, "-u", str(SRC / "headless_predictor.py"), "--series", series],
                    cwd=str(ROOT), stdout=plog, stderr=subprocess.STDOUT,
                )
            _log(f"  headless predictor pid={predictor_proc.pid} -> {pred_log_path}")

        remaining = (stop_at - datetime.now()).total_seconds()
        _log(f"  running until {stop_at:%H:%M} ({remaining/60:.0f} min)")
        time.sleep(max(0.0, remaining))

    except Exception as e:
        _log(f"  ⚠ ERROR during {series} {label}: {e}")
    finally:
        _log(f"STOP {series} {label}")
        _stop_proc(scraper_proc, f"{series} scraper")
        _stop_proc(predictor_proc, f"{series} predictor")
        _remove_lock(series)

    # post-processing (best-effort; never let this crash the thread)
    try:
        db_path = str(DATA_DIR / "race.db")
        if kind in ("race", "sprint_race"):
            import sqlite3
            conn = sqlite3.connect(db_path)
            oid = calculator.latest_session(conn, series=series)
            conn.close()
            if not oid:
                _log(f"  ⚠ no session found for {series} — evaluator skipped")
            else:
                _log(f"  running evaluator for {oid}")
                result = subprocess.run(
                    [PYTHON, str(SRC / "evaluator.py"), "--db", db_path,
                     "--session", oid, "--force"],
                    cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                )
                for line in (result.stdout + result.stderr).splitlines():
                    _log(f"    eval: {line}")
        else:
            report = session_healthcheck.check(db_path, series, str(scraper_log_path))
            for line in report.splitlines():
                _log(f"    {line}")
    except Exception as e:
        _log(f"  ⚠ post-processing error for {series} {label}: {e}")

    _log(f"DONE {series} {label}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-now", action="store_true",
                    help="ignore SCHEDULE; run one short fake IndyCar session "
                         "starting ~1 min from now to smoke-test the whole cycle")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    if args.test_now:
        start = datetime.now() + timedelta(minutes=1)
        schedule = [("indycar", "DryRun", "practice", start)]
        WINDOW_MIN["practice"] = 2   # shrink the window for a fast test
        global START_BUFFER_MIN
        START_BUFFER_MIN = 1
        _log("TEST MODE: one short IndyCar dry-run session, ~1 min from now")
    else:
        schedule = SCHEDULE

    _log(f"conductor starting, {len(schedule)} session(s) scheduled")
    threads = []
    for series, label, kind, start in schedule:
        t = threading.Thread(target=run_session, args=(series, label, kind, start),
                              name=f"{series}-{label}", daemon=False)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()
    _log("conductor: all sessions handled, exiting")


if __name__ == "__main__":
    main()
