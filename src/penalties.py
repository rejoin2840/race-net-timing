"""
penalties.py — parse free-text race-control messages into structured penalties.

Al Kamel exposes no structured penalty data — only race-director text in the
race_control table. This turns that text into something the calculator can carry
into net position / projected finish.

Penalty timing (how it affects the order):
  • pending   — drive-through / stop-go not yet served → time it WILL lose → NET position
  • post_race — "+N seconds post-race" time penalty → projected FINISH only
  • dq        — cease participation / disqualified → drops to back of class
  • warning   — informational (track limits warning, etc.) → ignored

Patterns are built from observed IMSA wording. Drive-through / stop-go phrasing
is race-only, so those branches are best-effort until validated against a real
race — refine here once we've seen the actual strings in race_control.
"""

import re
from dataclasses import dataclass

# approximate time costs where the message doesn't state seconds (refine post-race)
DRIVE_THROUGH_S   = 22.0    # pit-lane transit at the limit, no stop
STOP_GO_TRANSIT_S = 22.0    # transit overhead added on top of the stated stationary time


@dataclass
class Penalty:
    cars:    list      # car numbers the penalty applies to
    kind:    str       # DRIVE_THROUGH / STOP_GO / TIME / DQ / WARNING
    seconds: float     # time cost (0 for DQ/warning)
    timing:  str       # pending / post_race / dq / warning
    raw:     str


_CARS_RE   = re.compile(r"\bcars?\s*#?\s*([0-9]+(?:\s*,\s*#?\s*[0-9]+)*(?:\s*(?:and|&)\s*#?\s*[0-9]+)?)", re.I)
# WEC dash-separated multi-car list: "CAR 009 - 95 - DRIVE THROUGH". The negative
# lookahead keeps a trailing duration out of the list ("CAR 12 - 30 SECONDS STOP
# AND GO" must stay car 12 only — the regex backtracks off "30" and fails, so the
# normal _CARS_RE takes over).
_DASH_LIST_RE = re.compile(r"\bcars?\s*#?\s*([0-9]+(?:\s*-\s*[0-9]+)+\b)(?!\s*sec)", re.I)
_HASH_RE   = re.compile(r"#\s*([0-9]+)")
_SECONDS_RE = re.compile(r"\+?\s*(\d+(?:\.\d+)?)\s*(?:sec(?:ond)?s?)\b", re.I)
# stationary "Stop + N" / "Stop and hold + N" / "Stop plus N" hold penalty.
# Distinct from a stated seconds penalty: the N here is NOT followed by "sec".
# MM:SS variant ("Stop + 3:36") is handled separately to avoid capturing only
# the minutes digit.
_STOP_HOLD_RE = re.compile(r"stop\s*(?:\+|plus|and\s+hold|/?\s*hold)\s*\+?\s*(\d+(?:\.\d+)?)", re.I)
_STOP_MMSS_RE = re.compile(r"stop\s*(?:\+|plus|and\s+hold|/?\s*hold)\s*\+?\s*(\d+):(\d+)", re.I)


def _extract_cars(msg: str) -> list:
    cars = []
    m = _DASH_LIST_RE.search(msg)      # WEC "CAR 009 - 95 - <penalty>" list first
    if not m:
        m = _CARS_RE.search(msg)
    if m:
        cars = re.findall(r"[0-9]+", m.group(1))
    if not cars:
        cars = _HASH_RE.findall(msg)
    # dedupe, preserve order
    seen, out = set(), []
    for c in cars:
        if c not in seen:
            seen.add(c); out.append(c)
    return out


def _seconds(msg: str) -> float:
    m = _SECONDS_RE.search(msg)
    return float(m.group(1)) if m else 0.0


def parse(message: str) -> list:
    """Parse one race-control line into zero or more Penalty objects."""
    if not message:
        return []
    m = message.lower()

    # warnings carry no time — bail before anything else matches
    if "warning" in m and "penalt" not in m:
        return []

    # a rescinded penalty is reversed by the stewards — it must NOT be carried
    # into scoring. (race_control.py surfaces it on the rail as a reversal.)
    # "*AMENDED" is the opposite: the penalty still applies (only its terms
    # changed), so it falls through and gets scored normally below.
    if "rescinded" in m:
        return []

    cars = _extract_cars(message)
    if not cars:
        return []

    # disqualification / retirement by the stewards
    if "cease participation" in m or "disqualif" in m or re.search(r"\bdq\b", m):
        return [Penalty(cars, "DQ", 0.0, "dq", message)]

    secs = _seconds(message)

    # post-race time penalty (applied to final classification).
    # IMSA sometimes writes "Stop + 60 *POST RACE TIME" — the seconds suffix
    # ("sec") is absent so _seconds() returns 0; fall back to _STOP_HOLD_RE.
    if "post-race" in m or "post race" in m:
        if secs == 0.0:
            mh = _STOP_HOLD_RE.search(message)
            if mh:
                secs = float(mh.group(1))
        return [Penalty(cars, "TIME", secs, "post_race", message)]

    # WEC format: "N SECONDS ADDED TO THE NEXT PIT STOP" — time added to pit stop
    # dwell time, scored identically to a pending time penalty. No "penalty" keyword.
    if "added to" in m and "pit stop" in m and secs > 0:
        return [Penalty(cars, "TIME", secs, "pending", message)]

    # in-race penalties the car must serve → pending time loss
    if "drive" in m and "through" in m:
        return [Penalty(cars, "DRIVE_THROUGH", DRIVE_THROUGH_S, "pending", message)]
    if "stop" in m and ("go" in m or "/go" in m):
        return [Penalty(cars, "STOP_GO", (secs or 0.0) + STOP_GO_TRANSIT_S, "pending", message)]
    # stationary hold: check MM:SS first so "Stop + 3:36" gives 216s not 3s.
    mmss = _STOP_MMSS_RE.search(message)
    if mmss:
        hold_s = int(mmss.group(1)) * 60 + int(mmss.group(2))
        return [Penalty(cars, "STOP_HOLD", hold_s + STOP_GO_TRANSIT_S, "pending", message)]
    mh = _STOP_HOLD_RE.search(message)
    if mh:
        return [Penalty(cars, "STOP_HOLD", float(mh.group(1)) + STOP_GO_TRANSIT_S, "pending", message)]
    # "Lap time deleted" is intentionally NOT scored: deleting a single lap hurts
    # best-lap / qualifying, not race track position, which is all NET/projected
    # model carries. It still shows on the RACE CONTROL rail via race_control.py.

    # a stated time penalty with no post-race qualifier → treat as pending
    if "penalt" in m and secs > 0:
        return [Penalty(cars, "TIME", secs, "pending", message)]

    return []


def _served(car: str, penalty_ts, pit_entries) -> bool:
    """A pending penalty is served once the car makes a pit-lane visit AFTER the
    penalty was announced: drive-throughs and stop-gos are served in the pit lane,
    and 'N seconds added to the next pit stop' is by definition paid at the next
    stop. The feed never posts a 'penalty served' notice (checked across the full
    IMSA + WEC corpora), so this timestamp test is the only serving signal.
    Both message ts and pit-entry ts are epoch ms from the same feed clock."""
    # epoch-ms sanity: a ts in seconds (or lap numbers, or 0) would compare wrongly
    # against epoch-ms pit entries and clear penalties too eagerly — treat any
    # non-epoch-ms ts as "unknown" and keep the safe carry-forever behaviour.
    if penalty_ts is None or penalty_ts < 1e11 or not pit_entries:
        return False
    for key in (car, car.lstrip("0") or car):     # '007' in RC text vs '7' in pit rows
        if any(e > penalty_ts for e in pit_entries.get(key, ())):
            return True
    return False


def aggregate(messages, pit_entries=None) -> dict:
    """Fold parsed race-control lines into per-car totals.

    messages = iterable of message strings, or (ts_ms, message) tuples (newest
    order doesn't matter). Returns {car: (pending_s, post_race_s, note, dq)}.
    Identical penalties are de-duplicated (the feed logs each line twice —
    current + history).

    pit_entries = optional {car: [pit_entry_epoch_ms, ...]}. When given together
    with timed messages, a pending penalty stops counting once the car has
    visited the pit lane after the announcement (see _served) — otherwise a
    served drive-through double-counts forever: the ~22s it cost is already in
    the car's real gap AND net keeps adding it. Untimed calls keep the old
    carry-forever behaviour (safe fallback when no pit data exists)."""
    seen = set()
    acc: dict = {}
    for item in messages:
        ts, msg = item if isinstance(item, tuple) else (None, item)
        for p in parse(msg):
            for car in p.cars:
                key = (car, p.kind, round(p.seconds, 1), p.timing)
                if key in seen:
                    continue
                seen.add(key)
                pend, post, note, dq = acc.get(car, (0.0, 0.0, "", False))
                if p.timing == "dq":
                    dq = True
                    note = "DQ"
                elif p.timing == "post_race":
                    post += p.seconds
                    note = (note + " · " if note else "") + f"+{p.seconds:.0f}s post"
                elif p.timing == "pending":
                    if _served(car, ts, pit_entries):
                        acc[car] = (pend, post, note, dq)   # paid on track — real gap has it
                        continue
                    pend += p.seconds
                    label = {"DRIVE_THROUGH": "drive-thru", "STOP_GO": "stop/go",
                             "STOP_HOLD": "stop+hold"}.get(
                        p.kind, f"+{p.seconds:.0f}s")
                    note = (note + " · " if note else "") + label
                acc[car] = (pend, post, note, dq)
    return acc
