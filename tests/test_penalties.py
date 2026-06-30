"""Unit tests for src/penalties.py against the real IMSA race-control corpus.

Run: ./venv/bin/python -m pytest tests/test_penalties.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import penalties  # noqa: E402

STOP_GO_TRANSIT_S = penalties.STOP_GO_TRANSIT_S


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
