"""Unit tests for src/penalties.py against the real IMSA race-control corpus.

Run: ./venv/bin/python tests/test_penalties.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import penalties  # noqa: E402

STOP_GO_TRANSIT_S = penalties.STOP_GO_TRANSIT_S

RC_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rc_messages_imsa.txt")

# Coarse heuristic — matches any line that looks penalty-like
_PENALTY_COARSE = re.compile(
    r"penalt|drive.through|stop.(?:\+|plus|and|hold|go)|disqualif|cease|rescind|black.flag|\bDQ\b",
    re.I,
)
# Lap-time-deleted lines are intentionally not scored (best lap / quali impact
# only) — excluded from both parsed and KNOWN_UNPARSED accounting.
_LAP_DELETED = re.compile(r"lap.?time.?(?:del|inv|inval)|invalidated.*lap|lap time", re.I)

# Every corpus line that matches _PENALTY_COARSE but correctly returns [] from
# parse() — these are not "missed" parses, they are deliberate no-ops.
KNOWN_UNPARSED = {
    # rescinded — stewards reversed the call; must NOT affect scoring
    "CAR 023: PENALTY - TIRE WITHOUT CREW - DRIVE THROUGH - RESCINDED",
    # mechanical black flags — car must stop racing, not a time penalty
    "CAR 10 MECHANICAL BLACK FLAG",
    "CAR 11 MECHANICAL BLACK FLAG",
    "CAR 16 MECHANICAL BLACK FLAG - TIRE OPERATIONAL REQUIREMENTS",
    "CAR 19 MECHANICAL BLACK FLAG",
    "CAR 36 MECHANICAL BLACK FLAG",
    "CAR 5 MECHANICAL BLACK FLAG",
    "CAR 6 MECHANICAL BLACK FLAG",
    "CAR 60 MECHANICAL BLACK FLAG",
    "CAR 77 MECHANICAL BLACK FLAG",
    "CAR 9 MECHANICAL BLACK FLAG",
    # stop-and-repair — car goes behind the wall; not a timed standing penalty
    "CAR 24: MECHANICAL BLACK. FAILURE TO ADHERE TO TIRE OPERATIONAL REQUIREMENTS - STOP AND REPAIR",
    "CAR 46: MECHANICAL BLACK. FAILURE TO ADHERE TO TIRE OPERATIONAL REQUIREMENTS - STOP AND REPAIR",
    "CAR 57: MECHANICAL BLACK. FAILURE TO ADHERE TO TIRE OPERATIONAL REQUIREMENTS - STOP AND REPAIR",
    # warnings — informational, no time cost
    "CAR 120: PENALTY - FAILURE TO ADHERE TO THE CONTROLLED POWERTRAIN PARAMETERS - WARNING",
    "CAR 12: PENALTY - PIT LANE PROTOCOL VIOLATION - WARNING",
    "CAR 15: PENALTY - TRACKSIDE VIOLATION - WARNING",
    "CAR 16: PENALTY - INCIDENT RESPONSIBILITY WITH 66 - WARNING",
    "CAR 18: PENALTY - INCIDENT RESPONSIBILITY WITH 52 - WARNING",
    "CAR 19: PENALTY - INCIDENT RESPONSIBILITY WITH 66 - WARNING",
    "CAR 22 PENALTY - TRACK LIMITS - WARNING",
    "CAR 23: PENALTY - BLOCKING - WARNING",
    "CAR 23: PENALTY - FAILURE TO ADHERE TO THE CONTROLLED POWERTRAIN PARAMETERS - WARNING",
    "CAR 23: PENALTY - MOVEMENT UNDER BREAKING - WARNING",
    "CAR 27: PENALTY - FAILURE TO ADHERE TO THE CONTROLLED POWERTRAIN PARAMETERS - WARNING",
    "CAR 40 PENALTY - TRACK LIMITS - WARNING",
    "CAR 44: PENALTY - FAILURE TO ADHERE TO THE CONTROLLED POWERTRAIN PARAMETERS - WARNING",
    "CAR 44: PENALTY - INCIDENT RESPONSIBILITY WITH 27 - WARNING",
    "CAR 52: PENALTY - INCIDENT RESPONSIBILITY WITH 13 - WARNING",
    "CAR 52: PENALTY - INCIDENT RESPONSIBILITY WITH 73 - WARNING",
    "CAR 57: PENALTY - FAILURE TO ADHERE TO THE CONTROLLED POWERTRAIN PARAMETERS - WARNING",
    "CAR 57: PENALTY - INCIDENT RESPONSIBILITY WITH 44 - WARNING",
    "CAR 57: PENALTY - PIT LANE SPEED VIOLATION - (+1) WARNING",
    "CAR 60: PENALTY - EQUIPMENT OUSTIDE OF PIT BOX - WARNING",
    "CAR 66: PENALTY - INCIDENT RESPONSIBILITY WITH 177 - WARNING",
    "CAR 911: PENALTY - BLOCKING - WARNING",
}


def _one(msg):
    pens = penalties.parse(msg)
    assert len(pens) == 1, f"expected 1 penalty, got {pens!r} for {msg!r}"
    return pens[0]


# ── Stop + N stationary hold (real strings) ────────────────────────────────

def test_stop_plus_n_scores_hold_plus_transit():
    p = _one("Car 37: Penalty - Not fulfilling Emergency Service requirements - Stop + 10")
    assert p.cars == ["37"]
    assert p.kind == "STOP_HOLD"
    assert p.timing == "pending"
    assert p.seconds == 10.0 + STOP_GO_TRANSIT_S


def test_stop_plus_n_amended_still_applies():
    p = _one("Car 37: Penalty - More than Emergency Service in closed pit - Stop + 60 *AMENDED")
    assert p.kind == "STOP_HOLD"
    assert p.seconds == 60.0 + STOP_GO_TRANSIT_S


def test_stop_plus_n_carried_into_aggregate():
    acc = penalties.aggregate([
        "Car 37: Penalty - Not fulfilling Emergency Service requirements - Stop + 10",
    ])
    pend, post, note, dq = acc["37"]
    assert pend == 10.0 + STOP_GO_TRANSIT_S
    assert not dq
    assert "stop+hold" in note


# ── Lap time deleted is intentionally unscored ─────────────────────────────

def test_lap_time_deleted_not_scored():
    for msg in (
        "Car 5, 36: Penalty - Short Cut - Turn 5-9 - Lap time deleted",
        "Car 37: Penalty - Short Cut - Turn 5-9 - Lap time deleted - Lap 8",
        "Car 59: Penalty - Short Cut - Turn  - Lap time deleted",
    ):
        assert penalties.parse(msg) == [], f"should not score: {msg!r}"


# ── RESCINDED must NOT score; AMENDED must ─────────────────────────────────

def test_rescinded_drive_through_dropped():
    assert penalties.parse(
        "Car 25: Penalty - Pit Entry Violation - Drive Through *RESCINDED") == []


def test_amended_drive_through_kept():
    p = _one("Car 25: Penalty - Pit Entry Violation - Drive Through *AMENDED")
    assert p.kind == "DRIVE_THROUGH"
    assert p.timing == "pending"


# ── Regression: existing branches unchanged ────────────────────────────────

def test_plain_drive_through_still_scores():
    p = _one("Car 033: Penalty - Multiple Track Limits - Drive Through")
    assert p.kind == "DRIVE_THROUGH"


def test_warning_still_ignored():
    assert penalties.parse("Car 11 Penalty - Track Limits - Warning") == []


# ── Corpus invariant: parse() never raises; every penalty-like line either ──
# ── parses or is explicitly in KNOWN_UNPARSED (nothing silently dropped)  ──

def test_parse_never_raises_on_corpus():
    """parse() must not raise on any line in the real corpus."""
    if not os.path.exists(RC_FIXTURE):
        return  # fixture absent (no ~/Downloads in CI) — skip gracefully
    with open(RC_FIXTURE) as f:
        lines = [l.rstrip() for l in f if l.strip()]
    for line in lines:
        try:
            penalties.parse(line)
        except Exception as exc:
            raise AssertionError(f"parse() raised on {line!r}: {exc}") from exc


def test_known_unparsed_invariant():
    """Every penalty-like corpus line either parses or is in KNOWN_UNPARSED.

    A line that matches the coarse heuristic but returns [] AND is not in
    KNOWN_UNPARSED means the parser silently dropped a real penalty — the
    worst failure mode because net position would be wrong with no visible
    sign. Add it to KNOWN_UNPARSED (deliberate non-parse) or fix the parser.
    """
    if not os.path.exists(RC_FIXTURE):
        return
    with open(RC_FIXTURE) as f:
        lines = [l.rstrip() for l in f if l.strip()]
    unaccounted = []
    for line in lines:
        if _LAP_DELETED.search(line):
            continue
        if not _PENALTY_COARSE.search(line):
            continue
        if not penalties.parse(line) and line not in KNOWN_UNPARSED:
            unaccounted.append(line)
    assert not unaccounted, (
        "Penalty-like corpus lines not parsed and not in KNOWN_UNPARSED "
        "(add to KNOWN_UNPARSED or fix the parser):\n"
        + "\n".join(f"  {l!r}" for l in unaccounted)
    )


# ── Corpus-sourced: parser variations discovered from real archives ─────────

def test_stop_plus_spelled_out_scores():
    """Real corpus: 'STOP PLUS 60' (spelled out) must parse as STOP_HOLD."""
    p = _one("CAR 5: PENALTY - INCIDENT RESPONSIBILITY WITH 23 - STOP PLUS 60")
    assert p.kind == "STOP_HOLD"
    assert p.seconds == 60.0 + STOP_GO_TRANSIT_S
    assert p.timing == "pending"
    assert p.cars == ["5"]


def test_correction_prefix_stop_plus_extracts_correct_car():
    """'CORRECTION CAR N …' prefix must not confuse car extraction."""
    p = _one("CORRECTION CAR 25: PENALTY - INCIDENT RESPONSIBILITY WITH 5 - STOP PLUS 60")
    assert p.kind == "STOP_HOLD"
    assert p.seconds == 60.0 + STOP_GO_TRANSIT_S
    assert p.cars == ["25"]


def test_stop_mmss_converted_to_seconds():
    """Real corpus: 'STOP + 3:36' means 3 min 36 sec = 216 s, not 3 s."""
    p = _one("CAR 912: PENALTY - IMPROPER FINAL WAVE-BY PROCEDURE - STOP + 3:36")
    assert p.kind == "STOP_HOLD"
    assert p.seconds == 3 * 60 + 36 + STOP_GO_TRANSIT_S
    assert p.cars == ["912"]


def test_stop_mmss_multi_car():
    """MM:SS format with a comma-list of cars."""
    result = penalties.parse("CAR 8, 18, 88: PENALTY - IMPROPER FINAL WAVE-BY PROCEDURE - STOP + 2:40")
    assert len(result) == 1
    assert result[0].cars == ["8", "18", "88"]
    assert result[0].seconds == 2 * 60 + 40 + STOP_GO_TRANSIT_S


def test_multi_car_comma_drive_through():
    """Real corpus: comma-list without 'PENALTY' keyword still parses."""
    result = penalties.parse("CARS 85, 48, 24 - DRIVE THROUGH")
    assert len(result) == 1
    assert result[0].cars == ["85", "48", "24"]
    assert result[0].kind == "DRIVE_THROUGH"


def test_multi_car_ampersand_stop_hold():
    """Real corpus: '&' separator between car numbers."""
    result = penalties.parse("CAR 31 & 033 - STOP + 60")
    assert len(result) == 1
    assert result[0].cars == ["31", "033"]
    assert result[0].kind == "STOP_HOLD"
    assert result[0].seconds == 60.0 + STOP_GO_TRANSIT_S


def test_post_race_stop_plus_extracts_seconds():
    """Real corpus: 'STOP + 60 *POST RACE TIME' must give 60 s, not 0."""
    p = _one("CAR 15: PENALTY - RUNNING THE RED LIGHT AT PIT EXIT - STOP + 60 *POST RACE TIME")
    assert p.kind == "TIME"
    assert p.timing == "post_race"
    assert p.seconds == 60.0


# ── WEC-specific formats (from 2024-2026 WEC Timing71 archives) ──────────────

def test_wec_seconds_added_to_pit_stop_5s():
    """WEC corpus: 'N SECONDS ADDED TO THE NEXT PIT STOP' without 'PENALTY'."""
    p = _one("CAR 007 - 5 SECONDS ADDED TO THE NEXT PIT STOP - PIT STOP INFRINGEMENT AT 2107")
    assert p.kind == "TIME"
    assert p.seconds == 5.0
    assert p.timing == "pending"
    assert p.cars == ["007"]


def test_wec_seconds_added_to_pit_stop_10s():
    """WEC corpus: 10-second variant."""
    p = _one("CAR 10 - 10 SECONDS ADDED TO THE NEXT PIT STOP - PIT STOP INFRINGEMENT")
    assert p.kind == "TIME"
    assert p.seconds == 10.0
    assert p.timing == "pending"
    assert p.cars == ["10"]


def test_wec_sec_penalty_added_format():
    """WEC corpus: 'N SEC TIME PENALTY ADDED TO NEXT PIT STOP' (with PENALTY keyword)."""
    p = _one("5 SEC TIME PENALTY ADDED TO NEXT PIT STOP - CAR 22 - UNSAFE RELEASE")
    assert p.kind == "TIME"
    assert p.seconds == 5.0
    assert p.timing == "pending"
    assert p.cars == ["22"]


def test_wec_seconds_added_corpus_no_raises():
    """parse() must not raise on any WEC corpus line."""
    wec_fixture = os.path.join(os.path.dirname(__file__), "fixtures", "rc_messages_wec.txt")
    if not os.path.exists(wec_fixture):
        return
    with open(wec_fixture) as f:
        lines = [l.rstrip() for l in f if l.strip()]
    for line in lines:
        try:
            penalties.parse(line)
        except Exception as exc:
            raise AssertionError(f"parse() raised on {line!r}: {exc}") from exc


if __name__ == "__main__":
    # Runnable without pytest: execute every test_* function in this module.
    import traceback

    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
