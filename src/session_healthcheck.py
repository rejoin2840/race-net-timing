"""
session_healthcheck.py — post-session sanity pass for practice/quali sessions.

Prediction accuracy (predictor/evaluator) only means anything for RACE
sessions — practice/quali don't have a "final classification" to grade
against. So for those, this does a cheaper but still useful check: did the
scraper actually stay connected and keep polling, did a plausible number of
cars show up, do the pit stops it recorded look sane. Meant to be run once,
after a session's window has closed, pointed at the conductor's own captured
scraper log + the DB.

Because the conductor only invokes this after a *scheduled* session window,
cars are expected to have run: a PASS requires actual captured data
(lap_history rows, standings with real positions, fresh updated_at), not
just the entry/standings skeleton that connecting and bootstrapping creates
on its own. An empty-but-connected capture is a FAIL, never a PASS.

Run:  venv/bin/python src/session_healthcheck.py --db data/race.db --series imsa \
          --scraper-log logs/conductor_imsa_fp1_20260710.log
Add --idle-ok for ad-hoc runs outside a session window, where an empty
capture is expected and shouldn't fail.
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calculator
from db import DEFAULT_DB_PATH

# both scrapers print/log one timestamped line per snapshot; this matches
# either's bracketed "[HH:MM:SS]" prefix regardless of what follows.
_TS_LINE = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
MAX_SANE_GAP_S = 30       # a snapshot line should show up at least this often
MIN_SANE_STOP_MS = 8_000  # pit stops shorter than this are almost certainly noise
MAX_SANE_STOP_MS = 300_000  # longer than this is probably a red-flag/DNF, not a stop
# the check runs right after the window closes; the newest standings write
# should be no older than this (sessions can end well before the padded
# window does, so stale is a WARN, not a FAIL)
STALE_STANDINGS_S = 30 * 60


def _parse_snapshot_gaps(log_path: str) -> "list[float]":
    times = []
    try:
        text = Path(log_path).read_text(errors="replace")
    except OSError as e:
        print(f"  ⚠ couldn't read scraper log {log_path}: {e}", file=sys.stderr)
        return []
    for m in _TS_LINE.finditer(text):
        h, mi, s = (int(g) for g in m.groups())
        times.append(h * 3600 + mi * 60 + s)
    gaps = []
    for a, b in zip(times, times[1:]):
        d = b - a
        if d < 0:
            d += 24 * 3600  # midnight rollover
        gaps.append(d)
    return gaps


def _parse_db_ts(ts: "str | None") -> "datetime | None":
    # db.py writes utcnow().isoformat() + "Z"
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.rstrip("Z"))
    except ValueError:
        return None


def check(db_path: str, series: str, scraper_log: "str | None",
          expect_activity: bool = True) -> str:
    lines = []
    conn = sqlite3.connect(db_path)
    oid = calculator.latest_session(conn, series=series)
    if not oid:
        conn.close()
        return f"  ⚠ FAIL: no {series} session found in {db_path}"

    lines.append(f"  session: {oid}")

    entries = conn.execute(
        "SELECT COUNT(*) FROM session_entry WHERE session_oid=?", (oid,)
    ).fetchone()[0]
    standings, positioned, newest_upd = conn.execute(
        "SELECT COUNT(*), COUNT(overall_position), MAX(updated_at) "
        "FROM standings_current WHERE session_oid=?", (oid,)
    ).fetchone()
    laps_rows = conn.execute(
        "SELECT COUNT(*) FROM lap_history WHERE session_oid=?", (oid,)
    ).fetchone()[0]
    status = conn.execute(
        "SELECT current_lap, is_finished FROM session_status WHERE session_oid=?",
        (oid,)
    ).fetchone()
    pit_rows = conn.execute(
        "SELECT stop_duration_ms FROM pit_events WHERE session_oid=? "
        "AND stop_duration_ms IS NOT NULL", (oid,)
    ).fetchall()

    ok = True
    warned = False
    lines.append(f"  cars: {entries} entries, {standings} standings rows "
                 f"({positioned} with a position), {laps_rows} lap_history rows")
    if standings == 0:
        lines.append("  ✗ FAIL: no cars recorded at all — scraper likely never connected")
        ok = False
    elif entries == 0:
        lines.append("  ⚠ WARN: standings present but no named entries "
                     "(driver/team data missing)")
        warned = True

    # Entries/standings rows alone prove nothing: connecting and bootstrapping
    # creates them even when the feed never delivers a single live frame (WEC
    # FP3 / São Paulo quali 2026-07-11). If cars ran — which they did if this
    # was a scheduled window (expect_activity), or if the feed itself said so
    # (current_lap / is_finished) — demand actual captured data.
    current_lap, is_finished = status if status else (None, None)
    cars_ran = expect_activity or bool(current_lap) or bool(is_finished)
    if cars_ran:
        if laps_rows == 0:
            lines.append("  ✗ FAIL: cars ran but zero lap_history rows — "
                         "capture recorded nothing beyond bootstrap")
            ok = False
        if standings > 0 and positioned == 0:
            lines.append("  ✗ FAIL: no standings row has an overall_position — "
                         "feed never delivered live standings")
            ok = False
        elif standings > 0:
            newest = _parse_db_ts(newest_upd)
            age = (datetime.utcnow() - newest).total_seconds() if newest else None
            if age is None:
                lines.append("  ⚠ WARN: standings rows carry no parseable "
                             "updated_at — freshness unknown")
                warned = True
            elif age > STALE_STANDINGS_S:
                lines.append(f"  ⚠ WARN: newest standings update is "
                             f"{age/60:.0f} min old — capture may have died "
                             f"mid-session")
                warned = True

    durs = [d for (d,) in pit_rows]
    outliers = [d for d in durs if d < MIN_SANE_STOP_MS or d > MAX_SANE_STOP_MS]
    lines.append(f"  pit_events: {len(durs)} with a measured duration, "
                 f"{len(outliers)} outside [{MIN_SANE_STOP_MS/1000:.0f}s, "
                 f"{MAX_SANE_STOP_MS/1000:.0f}s]")
    if outliers and len(outliers) > max(1, len(durs) // 4):
        lines.append("  ⚠ WARN: a large fraction of pit stops look implausible — "
                     "check for double-scraper duplication or debounce issues")
        warned = True

    if scraper_log:
        gaps = _parse_snapshot_gaps(scraper_log)
        if gaps:
            worst = max(gaps)
            lines.append(f"  polling: {len(gaps)+1} snapshots, worst gap {worst:.0f}s")
            if worst > MAX_SANE_GAP_S:
                lines.append(f"  ⚠ WARN: a {worst:.0f}s gap in polling — "
                             f"possible reconnect or stall")
                warned = True
        else:
            lines.append("  ⚠ WARN: no snapshot lines found in scraper log "
                         "(log missing or empty)")
            warned = True

    verdict = "FAIL" if not ok else ("WARN" if warned else "PASS")
    lines.insert(0, f"  [{verdict}] {series} health check — {oid}")
    conn.close()
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--series", required=True, choices=["imsa", "wec"])
    ap.add_argument("--scraper-log", default=None,
                    help="path to the redirected stdout/log of the scraper process")
    ap.add_argument("--idle-ok", action="store_true",
                    help="don't require captured lap/standings data — for ad-hoc "
                         "runs outside a scheduled session window")
    args = ap.parse_args()
    print(check(args.db, args.series, args.scraper_log,
                expect_activity=not args.idle_ok))


if __name__ == "__main__":
    main()
