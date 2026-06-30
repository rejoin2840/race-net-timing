"""Unit test for catchup.summarize() returning the FULL ranked, deduped list
(no more hard MAX_EVENTS cap unless `cap=` is explicitly passed).

Runnable without pytest: ./venv/bin/python tests/test_summarize_cap.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import catchup  # noqa: E402


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


def _make_snapshots():
    # 12 cars, all in class GTP, each moving >= MIN_POS_MOVE positions, plus a
    # class-lead change (old leader car "1" -> new leader car "2").
    n = 12
    old_cars = [_Car(str(i), "GTP", i) for i in range(1, n + 1)]
    # reverse the order so every car moves at least MIN_POS_MOVE spots
    new_cars = [_Car(str(i), "GTP", n + 1 - i) for i in range(1, n + 1)]

    old = catchup.snapshot(_Ctx(10), old_cars, ts=1)
    new = catchup.snapshot(_Ctx(30), new_cars, ts=2)
    return old, new


def test_cap_is_gone_full_list_returned():
    old, new = _make_snapshots()
    events = catchup.summarize(old, new, rc_since=[])
    assert len(events) > 8, f"expected more than 8 events, got {len(events)}"


def test_results_sorted_by_rank_descending():
    old, new = _make_snapshots()
    events = catchup.summarize(old, new, rc_since=[])
    ranks = [e.rank for e in events]
    assert ranks == sorted(ranks, reverse=True), f"not sorted descending: {ranks}"


def test_per_car_dedupe_holds():
    old, new = _make_snapshots()
    events = catchup.summarize(old, new, rc_since=[])
    cars = [e.car for e in events if e.car is not None]
    assert len(cars) == len(set(cars)), f"car appeared more than once: {cars}"


def test_explicit_cap_still_works():
    old, new = _make_snapshots()
    events = catchup.summarize(old, new, rc_since=[], cap=5)
    assert len(events) <= 5


if __name__ == "__main__":
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
