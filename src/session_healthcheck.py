"""
session_healthcheck.py — post-session sanity pass for practice/quali sessions.

Prediction accuracy (predictor/evaluator) only means anything for RACE
sessions — practice/quali don't have a "final classification" to grade
against. So for those, this does a cheaper but still useful check: did the
scraper actually stay connected and keep polling, did a plausible number of
cars show up, do the pit stops it recorded look sane. Meant to be run once,
after a session's window has closed, pointed at the conductor's own captured
scraper log + the DB.

Run:  venv/bin/python src/session_healthcheck.py --db data/race.db --series imsa \
          --scraper-log logs/conductor_imsa_fp1_20260710.log
"""

import argparse
import re
import sqlite3
import sys
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


def check(db_path: str, series: str, scraper_log: "str | None") -> str:
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
    standings = conn.execute(
        "SELECT COUNT(*) FROM standings_current WHERE session_oid=?", (oid,)
    ).fetchone()[0]
    laps_rows = conn.execute(
        "SELECT COUNT(*) FROM lap_history WHERE session_oid=?", (oid,)
    ).fetchone()[0]
    pit_rows = conn.execute(
        "SELECT stop_duration_ms FROM pit_events WHERE session_oid=? "
        "AND stop_duration_ms IS NOT NULL", (oid,)
    ).fetchall()

    ok = True
    lines.append(f"  cars: {entries} entries, {standings} standings rows, "
                 f"{laps_rows} lap_history rows")
    if standings == 0:
        lines.append("  ✗ FAIL: no cars recorded at all — scraper likely never connected")
        ok = False
    elif entries == 0:
        lines.append("  ⚠ WARN: standings present but no named entries "
                     "(driver/team data missing)")

    durs = [d for (d,) in pit_rows]
    outliers = [d for d in durs if d < MIN_SANE_STOP_MS or d > MAX_SANE_STOP_MS]
    lines.append(f"  pit_events: {len(durs)} with a measured duration, "
                 f"{len(outliers)} outside [{MIN_SANE_STOP_MS/1000:.0f}s, "
                 f"{MAX_SANE_STOP_MS/1000:.0f}s]")
    if outliers and len(outliers) > max(1, len(durs) // 4):
        lines.append("  ⚠ WARN: a large fraction of pit stops look implausible — "
                     "check for double-scraper duplication or debounce issues")

    if scraper_log:
        gaps = _parse_snapshot_gaps(scraper_log)
        if gaps:
            worst = max(gaps)
            lines.append(f"  polling: {len(gaps)+1} snapshots, worst gap {worst:.0f}s")
            if worst > MAX_SANE_GAP_S:
                lines.append(f"  ⚠ WARN: a {worst:.0f}s gap in polling — "
                             f"possible reconnect or stall")
        else:
            lines.append("  ⚠ WARN: no snapshot lines found in scraper log "
                         "(log missing or empty)")

    verdict = "PASS" if ok else "FAIL"
    lines.insert(0, f"  [{verdict}] {series} health check — {oid}")
    conn.close()
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--series", required=True, choices=["imsa", "wec"])
    ap.add_argument("--scraper-log", default=None,
                    help="path to the redirected stdout/log of the scraper process")
    args = ap.parse_args()
    print(check(args.db, args.series, args.scraper_log))


if __name__ == "__main__":
    main()
