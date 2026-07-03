"""Tests for race_control.classify() — Epic 3 step 4: unparsed_penalty kind.

Run (no pytest needed):
    ./venv/bin/python tests/test_race_control.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import race_control as rc


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "rc_messages_imsa.txt")


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ── unparsed_penalty catches INVALIDATED lines ────────────────────────────────

def test_invalidated_is_unparsed_penalty():
    msg = "CAR 10: PENALTY - SHORT CUT - TURN 7 - LAP TIME INVALIDATED - LAP 40"
    tier, kind = rc.classify(msg)
    ok(tier == rc.CONTEXT, f"expected CONTEXT tier, got {tier}")
    ok(kind == "unparsed_penalty", f"expected unparsed_penalty, got {kind!r}")


def test_another_invalidated_variant():
    msg = "CAR 55: PENALTY - TRACK LIMITS - TURN 14 - LAP TIME INVALIDATED - LAP 12"
    tier, kind = rc.classify(msg)
    ok(kind == "unparsed_penalty", f"expected unparsed_penalty, got {kind!r}")


# ── WARNING lines are still suppressed ───────────────────────────────────────

def test_penalty_warning_stays_suppressed():
    msg = "CAR 4: PENALTY WARNING - EXCEEDING TRACK LIMITS"
    tier, kind = rc.classify(msg)
    ok(tier == rc.SUPPRESS, f"penalty+warning should be SUPPRESS, got tier={tier} kind={kind!r}")


def test_warning_in_message_stays_suppressed():
    msg = "CAR 99: PENALTY - FINAL WARNING - TRACK LIMITS"
    tier, kind = rc.classify(msg)
    ok(tier != rc.CONTEXT or kind != "unparsed_penalty",
       "penalty with 'warning' must not become unparsed_penalty")


# ── scored penalties still return 'penalty' / 'dq' ───────────────────────────

def test_drive_through_is_still_alert_penalty():
    msg = "CAR 10: PENALTY - DRIVE THROUGH - AVOIDABLE CONTACT - LAP 22"
    tier, kind = rc.classify(msg)
    ok(tier == rc.ALERT, f"drive-through should be ALERT, got {tier}")
    ok(kind == "penalty", f"drive-through kind should be penalty, got {kind!r}")


def test_stop_and_hold_is_still_alert_penalty():
    msg = "CAR 77: PENALTY - STOP AND HOLD 60 SEC - AVOIDABLE CONTACT"
    tier, kind = rc.classify(msg)
    ok(tier == rc.ALERT and kind == "penalty",
       f"stop-and-hold should be ALERT/penalty, got {tier}/{kind!r}")


def test_dq_is_still_alert_dq():
    msg = "CAR 23: DISQUALIFIED - TECHNICAL INFRACTION - POST RACE"
    tier, kind = rc.classify(msg)
    ok(tier == rc.ALERT and kind == "dq",
       f"DQ should be ALERT/dq, got {tier}/{kind!r}")


# ── corpus canary — pinned count guards against regressions ──────────────────

def test_corpus_unparsed_penalty_count():
    msgs = open(FIXTURES).read().splitlines()
    count = sum(1 for m in msgs if rc.classify(m) == (rc.CONTEXT, "unparsed_penalty"))
    ok(count == 54,
       f"expected exactly 54 unparsed_penalty hits in corpus, got {count}")


def test_corpus_penalty_warning_still_suppressed():
    msgs = open(FIXTURES).read().splitlines()
    leaked = [m for m in msgs
              if "penalt" in m.lower() and "warning" in m.lower()
              and rc.classify(m)[1] == "unparsed_penalty"]
    ok(not leaked,
       f"penalty+warning messages must stay suppressed, leaked: {leaked[:3]}")


# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(failed)
