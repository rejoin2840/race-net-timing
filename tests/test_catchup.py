"""Unit tests for src/catchup.py — the "while you were away" diff engine.

Two layers:
  • synthetic snapshots exercise every event type + ranking + dedupe (fast, deterministic)
  • a real snapshot built from data/race.db via dashboard.Poller proves catchup.snapshot
    reads the live CarAnalysis fields correctly, and that real race-control penalty rows
    surface through summarize().

Run (no pytest dependency — matches the other tests in this dir):
  ./venv/bin/python tests/test_catchup.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import catchup  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────
class _Car:
    def __init__(self, num, cls, pos, stops=0, laps_down=0, penalty_s=0.0, dq=False):
        self.car_number = num; self.car_class = cls; self.driver = ""; self.team = ""
        self.effective_pos_in_class = pos; self.pos_in_class = pos
        self.track_position = pos; self.net_position = pos
        self.stops = stops; self.laps_down = laps_down
        self.penalty_s = penalty_s; self.dq = dq


class _Ctx:
    def __init__(self, lap, flag="GF", caution_count=0, cautions=None):
        self.current_lap = lap; self.flag = flag
        self.caution_count = caution_count; self.cautions = cautions or []


class _Row(dict):
    """Stands in for an sqlite3.Row of (ts, message) — supports row['message']."""
    def keys(self):
        return super().keys()


def _rc(msg, ts=1500):
    return _Row(ts=ts, message=msg)


def _kinds(events):
    return {e.tone for e in events}


def _by_car(events):
    return {e.car: e for e in events if e.car}


# ── synthetic scenarios ────────────────────────────────────────────────────────
def _base():
    old = catchup.snapshot(_Ctx(42), [
        _Car("7", "GTP", 2), _Car("6", "GTP", 1), _Car("25", "GTP", 5),
        _Car("31", "GTP", 3), _Car("93", "GTDPRO", 1),
    ], ts=1000)
    new = catchup.snapshot(_Ctx(58, caution_count=1, cautions=[(48, 52, 180)]), [
        _Car("7", "GTP", 1), _Car("6", "GTP", 2), _Car("25", "GTP", 3),
        _Car("31", "GTP", 5, stops=1), _Car("93", "GTDPRO", 1),
    ], ts=2000)
    return old, new


def test_lead_change_is_top_ranked():
    old, new = _base()
    evs = catchup.summarize(old, new, rc_since=[])
    assert evs[0].tone == catchup.LEAD
    assert evs[0].car == "7" and "passed #6" in evs[0].sub


def test_position_gain_and_pit_present():
    old, new = _base()
    by = _by_car(catchup.summarize(old, new, rc_since=[]))
    assert by["25"].tone == catchup.GAIN          # climbed 5 → 3
    assert by["31"].tone == catchup.PIT           # pitted → rejoined P5
    assert "rejoined P5" in by["31"].sub


def test_pit_suppresses_its_own_position_loss():
    """A car that dropped BECAUSE it pitted reports the pit (with rejoin pos), not a bare
    loss — one event per car, and the pit explains the drop."""
    old, new = _base()
    by = _by_car(catchup.summarize(old, new, rc_since=[]))
    assert by["31"].tone == catchup.PIT
    assert by["31"].tone != catchup.LOSS


def test_caution_event_surfaced():
    old, new = _base()
    evs = catchup.summarize(old, new, rc_since=[])
    cautions = [e for e in evs if e.tone == catchup.CAUTION]
    assert len(cautions) == 1
    assert "48" in cautions[0].text and "52" in cautions[0].text


def test_real_penalty_from_rc_outranks_caused_loss():
    """A penalty comes from the authoritative RC text and dedupes per car — it should be
    the event shown for that car, not the snapshot position change."""
    old, new = _base()
    rc = [_rc("Car 93 - Penalty - Drive Through - Pit Lane Speeding")]
    by = _by_car(catchup.summarize(old, new, rc_since=rc))
    assert by["93"].tone == catchup.PENALTY


def test_routine_one_spot_jitter_excluded():
    """A single-position shuffle mid-pack is noise — below MIN_POS_MOVE, and not a lead
    change, so it must not appear. (A stable P1 keeps it out of the lead-change path.)"""
    old = catchup.snapshot(_Ctx(40), [
        _Car("1", "GTP", 1), _Car("7", "GTP", 4), _Car("6", "GTP", 5)], ts=1)
    new = catchup.snapshot(_Ctx(41), [
        _Car("1", "GTP", 1), _Car("7", "GTP", 5), _Car("6", "GTP", 4)], ts=2)
    assert catchup.summarize(old, new, rc_since=[]) == []


def test_no_change_is_silent():
    old, _ = _base()
    assert catchup.summarize(old, copy.deepcopy(old), rc_since=[]) == []


def test_cap_optional():
    # summarize now returns the FULL ranked list by default (the card does its own
    # impact-based headline split); the cap only applies when explicitly requested.
    cars_old = [_Car(str(i), "GTP", i) for i in range(1, 20)]
    cars_new = [_Car(str(i), "GTP", 20 - i) for i in range(1, 20)]   # full reversal
    old = catchup.snapshot(_Ctx(10), cars_old, ts=1)
    new = catchup.snapshot(_Ctx(30), cars_new, ts=2)
    assert len(catchup.summarize(old, new, rc_since=[])) > catchup.MAX_EVENTS
    assert len(catchup.summarize(old, new, rc_since=[], cap=catchup.MAX_EVENTS)) \
        == catchup.MAX_EVENTS


# ── real-data plumbing (skips cleanly if the DB isn't present) ──────────────────
def test_snapshot_reads_real_caranalysis_fields():
    import importlib.util
    db = os.path.join(os.path.dirname(__file__), "..", "data", "race.db")
    if not os.path.exists(db) or importlib.util.find_spec("PyQt6") is None:
        return  # nothing to assert against — environment-dependent, not a failure
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import dashboard as dash
    res = dash.Poller().poll(0)
    if res is None:
        return
    ctx, cars, rc, _age, _trend = res
    snap = catchup.snapshot(ctx, cars)
    assert len(snap.cars) == len(cars) > 0
    one = next(iter(snap.cars.values()))
    assert one.car and one.cls is not None      # fields populated, no AttributeError
    # real RC rows flow through summarize without error (whatever they contain)
    catchup.summarize(snap, snap, rc_since=list(rc or []))


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
