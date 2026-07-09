"""
timeline.py — historical reconstruction + on-demand catch-up recap (WYWA v2, Phase 0).

No new capture: the `predictions` table already logs every car's ACTUAL position /
stops / laps roughly every 60s during a race (predictor.py), and race_control /
caution_periods / driver_changes are append-only and timestamped. So any past race
state is reconstructable straight from the DB — this module is a read-only query
layer, not a capture feature. Like catchup.py it is PURE: no PyQt, no writes, no
dependence on live dashboard state, so it works identically against a live race.db
mid-race or an archived replay DB long after the fact.

    reconstruct(conn, oid, ts_ms) -> catchup.Snapshot   # field state as of ts_ms
    rc_between(conn, oid, t1_ms, t2_ms) -> rows          # race_control lines in (t1, t2]
    recap(conn, oid, t1_ms, t2_ms, cap=None, cls=None)   # one-call reconstruct+diff
    budget_by_class(events, n)                           # per-class event cap
    hour_marks(conn, oid, every_s=3600)                  # lookback checkpoints

Reconstructed snapshots carry no `net` (projected/net position) — only fields with
a real historical record. That's deliberate, not an oversight: EPIC9_DIRECTION.md
calls for "retrospective only, no projections for long lookback windows," and this
holds by construction rather than by a separate check.
"""

import argparse
import re
import sqlite3
import sys
from typing import Optional

import catchup
import penalties

# how far ts_ms may sit past the last known predictions row before a reconstructed
# Snapshot is flagged sparse (~5x the predictor's own logging cadence) — tune in
# Phase 1 against how stale São Paulo data actually looks.
STALE_MS = 5 * 60 * 1000


# ── reconstruction ──────────────────────────────────────────────────────────────
def _entries(conn: sqlite3.Connection, oid: str) -> dict:
    """car_number -> (name, team) from session_entry, the driver/team fallback for
    cars with no driver_changes history (mirrors calculator.py's `e.name AS driver`)."""
    return {r["car_number"]: (r["name"] or "", r["team"] or "")
            for r in conn.execute(
                "SELECT car_number, name, team FROM session_entry WHERE session_oid=?",
                (oid,))}


def _drivers_asof(conn: sqlite3.Connection, oid: str, rows) -> dict:
    """car_number -> the driver in the seat at that car's own reconstructed
    session_lap (latest driver_changes row with session_lap <= the car's cutoff)."""
    by_car: dict = {}
    for r in conn.execute(
            """SELECT car_number, driver, session_lap FROM driver_changes
                 WHERE session_oid=? ORDER BY car_number, seq""", (oid,)):
        by_car.setdefault(r["car_number"], []).append(r)

    out = {}
    for r in rows:
        num, cutoff = r["car_number"], r["session_lap"]
        best = None
        for dc in by_car.get(num, ()):
            dl = dc["session_lap"]
            if dl is None or cutoff is None or dl <= cutoff:
                best = dc["driver"]
        out[num] = best
    return out


def _cautions_asof(conn: sqlite3.Connection, oid: str, lap: int) -> tuple:
    """Cautions opened by `lap` (list of (start_lap, end_lap, dur_s), matching
    Snapshot.cautions), plus the cause of whichever one is still open at `lap` (the
    header flag — "" if none, i.e. as-good-as-green by the only record we have)."""
    rows = conn.execute(
        """SELECT start_lap, end_lap, duration_s, cause FROM caution_periods
             WHERE session_oid=? AND start_lap<=? ORDER BY start_lap""",
        (oid, lap)).fetchall()
    cautions = [(r["start_lap"], r["end_lap"], r["duration_s"]) for r in rows]
    flag = ""
    for r in rows:
        if r["end_lap"] is None or r["end_lap"] >= lap:
            flag = r["cause"] or ""
    return cautions, flag


def _penalties_asof(conn: sqlite3.Connection, oid: str, ts_ms: int) -> dict:
    """{car: (pending_s, post_race_s, note, dq)} replayed through penalties.aggregate,
    the same classifier the live rail/net-position math uses (calculator._load_penalties).
    Rows with an unknown ts (0 / NULL, logged when the feed gave none) are dropped
    rather than guessed at — the "degrade to 0/False when ambiguous" rule in practice."""
    rc_rows = conn.execute(
        """SELECT ts, message FROM race_control
             WHERE session_oid=? AND ts IS NOT NULL AND ts>0 AND ts<=?""",
        (oid, ts_ms)).fetchall()
    pit_rows = conn.execute(
        """SELECT car_number, pit_entry_hour_ms FROM pit_events
             WHERE session_oid=? AND pit_entry_hour_ms IS NOT NULL
               AND pit_entry_hour_ms<=?""",
        (oid, ts_ms)).fetchall()
    pit_entries: dict = {}
    for r in pit_rows:
        pit_entries.setdefault(r["car_number"], []).append(r["pit_entry_hour_ms"])
    return penalties.aggregate(((r["ts"], r["message"]) for r in rc_rows), pit_entries)


def reconstruct(conn: sqlite3.Connection, oid: str, ts_ms: int) -> catchup.Snapshot:
    """Rebuild the field state as it stood at ts_ms, purely from historical tables.
    Per car: the latest predictions row with ts <= ts_ms (a correlated subquery —
    py3.9/sqlite-safe, no window functions needed for one row per car)."""
    rows = conn.execute(
        """SELECT p.* FROM predictions p
             WHERE p.session_oid=? AND p.ts<=?
               AND p.ts = (SELECT MAX(p2.ts) FROM predictions p2
                            WHERE p2.session_oid=p.session_oid
                              AND p2.car_number=p.car_number AND p2.ts<=?)""",
        (oid, ts_ms, ts_ms)).fetchall()

    max_ts = conn.execute(
        "SELECT MAX(ts) FROM predictions WHERE session_oid=? AND ts<=?",
        (oid, ts_ms)).fetchone()[0]
    sparse = max_ts is None or (ts_ms - max_ts) > STALE_MS

    lap = conn.execute(
        "SELECT MAX(session_lap) FROM predictions WHERE session_oid=? AND ts<=?",
        (oid, ts_ms)).fetchone()[0] or 0

    leader_laps: dict = {}
    for r in rows:
        if r["laps"] is not None:
            cls = r["car_class"] or ""
            leader_laps[cls] = max(leader_laps.get(cls, 0), r["laps"])

    drivers = _drivers_asof(conn, oid, rows)
    entries = _entries(conn, oid)
    pen = _penalties_asof(conn, oid, ts_ms)
    cautions, flag = _cautions_asof(conn, oid, lap)

    cars = {}
    for r in rows:
        num, cls = r["car_number"], (r["car_class"] or "")
        car_laps = r["laps"] or 0
        entry_name, entry_team = entries.get(num, ("", ""))
        pend_s, _post_s, _note, dq = pen.get(num, (0.0, 0.0, "", False))
        cars[num] = catchup.CarState(
            car=num, cls=cls,
            driver=drivers.get(num) or entry_name,
            team=entry_team,
            pos=r["pos_in_class"], overall=r["track_position"],
            stops=int(r["stops"] or 0),
            laps_down=max(0, leader_laps.get(cls, car_laps) - car_laps),
            penalty_s=pend_s, dq=dq,
        )

    return catchup.Snapshot(
        ts=ts_ms / 1000.0, lap=lap, flag=flag,
        caution_count=len(cautions), cautions=cautions,
        cars=cars, sparse=sparse,
    )


def rc_between(conn: sqlite3.Connection, oid: str, t1_ms: int, t2_ms: int):
    """race_control rows strictly after t1 up to and including t2, newest-first
    (matches the ORDER BY convention dashboard.py/dashboard_calm.py use for the rail)."""
    return conn.execute(
        """SELECT ts, message FROM race_control
             WHERE session_oid=? AND ts>? AND ts<=?
             ORDER BY ts DESC, rowid DESC""",
        (oid, t1_ms, t2_ms)).fetchall()


# ── recap ────────────────────────────────────────────────────────────────────
def budget_by_class(events: list, n: int) -> list:
    """Cap `events` (already rank-sorted by summarize) to at most n per class, so one
    deep class doesn't crowd out a quieter one. Field-wide events (cls is None, e.g.
    a caution) are always kept — satisfies EPIC9's per-class budget without summarize
    itself needing to know about classes."""
    out, per_cls = [], {}
    for ev in events:
        if ev.cls is None:
            out.append(ev)
            continue
        seen = per_cls.get(ev.cls, 0)
        if seen < n:
            out.append(ev)
            per_cls[ev.cls] = seen + 1
    return out


def recap(conn: sqlite3.Connection, oid: str, t1_ms: int, t2_ms: int,
          cap: Optional[int] = None, cls: Optional[str] = None) -> list:
    """One-call catch-up: reconstruct both ends of the window, diff them, optionally
    filter to one class and/or apply a per-class budget."""
    old = reconstruct(conn, oid, t1_ms)
    new = reconstruct(conn, oid, t2_ms)
    rc = rc_between(conn, oid, t1_ms, t2_ms)
    events = catchup.summarize(old, new, rc)
    if cls:
        events = [e for e in events if e.cls == cls or e.cls is None]
    if cap:
        events = budget_by_class(events, cap)
    return events


def hour_marks(conn: sqlite3.Connection, oid: str, every_s: int = 3600) -> list:
    """Timestamps (epoch ms) every `every_s` seconds from the session's first logged
    prediction through its last — the lookback checkpoints ("H1", "H2", ...). Marks
    are just timestamps: persisting snapshot rows would add a second DB writer next
    to the scraper/predictor, which the single-writer design (db.py) rules out."""
    row = conn.execute(
        "SELECT MIN(ts), MAX(ts) FROM predictions WHERE session_oid=?", (oid,)).fetchone()
    start, end = row[0], row[1]
    if start is None or end is None:
        return []
    step_ms = every_s * 1000
    marks, t = [], start + step_ms
    while t <= end:
        marks.append(t)
        t += step_ms
    return marks


# ── CLI ──────────────────────────────────────────────────────────────────────
_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([hms]?)$", re.I)
_DUR_MULT = {"h": 3600.0, "m": 60.0, "s": 1.0, "": 1.0}


def _parse_duration_s(spec: str) -> float:
    m = _DUR_RE.match(spec.strip())
    if not m:
        raise ValueError(
            f"bad duration {spec!r} — expected e.g. 2h, 90m, 45s, or a bare number of seconds")
    return float(m.group(1)) * _DUR_MULT[m.group(2).lower()]


def _default_session(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT session_oid FROM sessions ORDER BY last_seen DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _session_start_ms(conn: sqlite3.Connection, oid: str) -> Optional[int]:
    """First logged prediction ts — the session-start reference for --from/--to.
    (session_status.start_time_s is the LIVE wall clock, stamped by whichever
    machine most recently wrote it; for a replayed archive that's the replay
    capture time, not the original race's start, so it can't be trusted here.)"""
    return conn.execute(
        "SELECT MIN(ts) FROM predictions WHERE session_oid=?", (oid,)).fetchone()[0]


def format_recap(events: list, old: catchup.Snapshot, new: catchup.Snapshot) -> str:
    lines = [f"=== Recap: lap {old.lap} -> lap {new.lap} ==="]
    if new.sparse:
        lines.append("(sparse: little or no reconstructed data near this window)")
    if not events:
        lines.append("(no notable changes)")
    for ev in events:
        prefix = f"#{ev.car} " if ev.car else ""
        sub = f" — {ev.sub}" if ev.sub else ""
        lines.append(f"[{ev.tone.upper():9}] {prefix}{ev.text}{sub}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="On-demand catch-up recap reconstructed from historical race data "
                    "(no live state, no new capture).")
    ap.add_argument("db", help="Path to a race/replay sqlite DB")
    ap.add_argument("--session", default=None,
                    help="Session OID (default: most recently seen session in the DB)")
    ap.add_argument("--from", dest="from_", required=True,
                    help="Window start since session start, e.g. 2h / 90m / 45s")
    ap.add_argument("--to", dest="to_", required=True,
                    help="Window end since session start, same format as --from")
    ap.add_argument("--class", dest="cls", default=None, help="Filter to one car class")
    ap.add_argument("--cap", type=int, default=None, help="Per-class event budget")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    oid = args.session or _default_session(conn)
    if not oid:
        print("no sessions found in this DB", file=sys.stderr)
        raise SystemExit(1)

    t0 = _session_start_ms(conn, oid)
    if t0 is None:
        print(f"no prediction data for session {oid!r}", file=sys.stderr)
        raise SystemExit(1)

    t1 = t0 + int(_parse_duration_s(args.from_) * 1000)
    t2 = t0 + int(_parse_duration_s(args.to_) * 1000)

    old = reconstruct(conn, oid, t1)
    new = reconstruct(conn, oid, t2)
    rc = rc_between(conn, oid, t1, t2)
    events = catchup.summarize(old, new, rc)
    if args.cls:
        events = [e for e in events if e.cls == args.cls or e.cls is None]
    if args.cap:
        events = budget_by_class(events, args.cap)

    print(format_recap(events, old, new))


if __name__ == "__main__":
    main()
