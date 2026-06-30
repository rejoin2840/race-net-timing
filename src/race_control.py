"""
race_control.py — classify free-text race-control messages for the rail.

The feed logs ~500 messages a race, mostly procedural admin (pits open, wave-by,
"under N minutes to command"), routine track-limit warnings, and resolved incident
chatter. A strategist glancing back wants only: served penalties, DQs, retirements,
things under review, the checkered, and the incidents that explain a yellow. This
module classifies each line into an importance tier so both dashboards can filter
the rail down to signal.

It is also the event source the catch-up / "while you were away" summary will
consume — hence a reusable module, not inline display logic.

Penalty detection reuses penalties.parse(): it already distinguishes consequential
penalties (drive-through / stop-go / time / DQ) from ignorable warnings (returns []),
so the rail and the scoring path stay consistent.
"""

import penalties

# importance tiers
SUPPRESS = 0   # procedural admin / routine warning / resolved chatter → hide
CONTEXT  = 1   # early signal or yellow-cause → show dim
ALERT    = 2   # served penalty / DQ / retirement / checkered → show loud

# incidents that may bring out (or explain) a caution — worth a glance
_YELLOW_CAUSE = (
    "stopped on course", "stopped off course", "off course",
    "behind the wall", "in the runoff", "track services working",
)


def classify(message) -> tuple:
    """Return (tier, kind) for one race-control line. tier == SUPPRESS means hide.

    kind is a short label driving colour: 'dq' / 'penalty' / 'rescinded' / 'retired'
    / 'flag' (ALERT) or 'review' / 'warning' / 'incident' (CONTEXT) or '' (SUPPRESS).
    """
    if not message:
        return (SUPPRESS, "")
    low = " ".join(message.split()).lower()

    # a rescinded penalty is BIG news — a car you'd written off for 22s suddenly isn't.
    # Surface it loudly with its own kind so the UI reads it as a reversal, not a fresh
    # penalty. (Amended penalties keep their drive/stop tokens → caught as ALERT below.)
    if "rescinded" in low:
        return (ALERT, "rescinded")

    # reviews resolve to CONTEXT before the penalty parser runs — "...REVIEWED
    # POST-RACE" would otherwise trip parse()'s post-race branch and look like a
    # served penalty when it's only a notice that stewards are looking at it.
    # ("REVIEWED, NO ACTION" lands here too — the closed review is worth a dim glance.)
    if "review" in low or "reviewed" in low:
        return (CONTEXT, "review")

    # consequential penalties: parse() first (scoring-consistent), then a rail-level
    # catch for the stationary / lap penalties parse() doesn't score (Stop + N, lap
    # time deleted) but a strategist still needs to see.
    pens = penalties.parse(message)
    if pens:
        return (ALERT, "dq" if any(p.timing == "dq" for p in pens) else "penalty")
    if "penalt" in low and any(t in low for t in (
            "drive through", "stop +", "stop and hold", "stop/go", "stop go",
            "lap time deleted")):
        return (ALERT, "penalty")

    if "retired" in low:
        return (ALERT, "retired")
    if "checkered" in low or "chequered" in low:
        return (ALERT, "flag")

    # resolutions ("car returned / continued / ...CONT") are not news → drop
    if "returned" in low or "continued" in low or low.endswith(" cont"):
        return (SUPPRESS, "")

    # early signal
    if "final warning" in low:
        return (CONTEXT, "warning")
    # yellow-cause incidents (Paul: keep so a glance tells you WHY a caution is out)
    if any(k in low for k in _YELLOW_CAUSE):
        return (CONTEXT, "incident")

    # everything else = procedural admin / routine warning / resolved chatter
    return (SUPPRESS, "")


def feed(rows, limit: int = 6) -> list:
    """Filter + dedupe race_control rows into the meaningful ones to display.

    rows = newest-first sqlite rows of (ts, message) (sqlite3.Row or tuple). The feed
    double-logs each line (current + history), so we dedupe on normalised text.
    Returns up to `limit` (message, tier, kind), most-recent-first.
    """
    out, seen = [], set()
    for row in rows:
        raw = (row["message"] if hasattr(row, "keys") else row[1]) or ""
        msg = " ".join(raw.split())          # collapse whitespace/newlines
        key = msg.lower()
        if not msg or key in seen:
            continue
        seen.add(key)
        tier, kind = classify(msg)
        if tier == SUPPRESS:
            continue
        out.append((msg, tier, kind))
        if len(out) >= limit:
            break
    return out
