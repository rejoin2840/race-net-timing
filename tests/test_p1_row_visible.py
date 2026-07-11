"""Regression test: the official P1 row must stay on the visible board in
practice/quali sessions.

Bug (observed live, WEC FP1 2026-07-10, car #83): in non-race sessions the
feed's pos_in_class IS the official classification (best lap), but _build_rows
ranked rows by the race-logic effective position (calculator's pit-aware
re-rank, which sinks any pit-lane car to the back of the class). A practice
leader is parked in the garage most of the session, so its row was ordered
last — and on the calm board (TOP_N=5 + collapsed "+N more" accordion) it
disappeared from the rendered list entirely, leaving the board to start at P2.

Verifies:
  • non-race ctx: rows ordered by the official pos_in_class; the official P1
    lands inside the calm board's visible top-5 window with POS label 1
  • race ctx: the pit-aware effective order still wins (unchanged behaviour)
  • ctx without is_race at all (legacy/test contexts): race behaviour

Run (no pytest needed):
    ./venv/bin/python tests/test_p1_row_visible.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import series_profiles
from timing_table import _build_rows
from calculator import CarAnalysis

TOP_N = 5   # dashboard_calm.CalmWindow.TOP_N — multi-class visible rows per class


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _ctx(is_race):
    ctx = types.SimpleNamespace()
    ctx.profile = series_profiles.WEC
    if is_race is not None:
        ctx.is_race = is_race
    return ctx


def _practice_field():
    """8 HYPERCAR cars. Official classification (pos_in_class, by best lap) has
    #36 P1 — but #36 is parked in the pits, so the race-logic effective re-rank
    (effective_pos_in_class) has pushed it to the back of the class, exactly the
    live-FP1 state. net_position mirrors the effective (race) order."""
    cars = []
    for i, num in enumerate(["36", "19", "007", "17", "50", "38", "51", "35"], 1):
        c = CarAnalysis(car_number=num, car_class="HYPERCAR",
                        driver=f"D{i}", team=f"T{i}",
                        track_position=i, pos_in_class=i, laps=20)
        cars.append(c)
    # official P1 (#36) sits in the garage: effective order demotes it to P8,
    # everyone else moves up one effective slot
    cars[0].track_status = "STOPPED"
    cars[0].effective_pos_in_class = 8
    cars[0].net_position = 8
    for i, c in enumerate(cars[1:], 1):
        c.effective_pos_in_class = i
        c.net_position = i
    return cars


def _visible_cars(rows):
    """Replicate dashboard_calm._render_list: per-class sort by trk, then the
    top-5 visible window (the rest live in the collapsed accordion)."""
    car_rows = [r for r in rows if not r.is_header]
    car_rows.sort(key=lambda r: (r.trk if r.trk else 99))
    return car_rows[:TOP_N], car_rows


def test_practice_p1_visible():
    rows = _build_rows(_ctx(is_race=False), _practice_field(), {}, None)
    visible, all_rows = _visible_cars(rows)
    vis_cars = [r.car for r in visible]
    ok("36" in vis_cars,
       f"official P1 (#36) must be on the visible board, got top-5 {vis_cars}")
    ok(visible[0].car == "36" and visible[0].trk == 1,
       f"official P1 must render first with POS 1, got #{visible[0].car} "
       f"POS {visible[0].trk}")
    ok([r.trk for r in all_rows] == list(range(1, 9)),
       f"practice board must follow the official classification 1..8, got "
       f"{[r.trk for r in all_rows]}")


def test_race_effective_order_unchanged():
    rows = _build_rows(_ctx(is_race=True), _practice_field(), {}, None)
    visible, all_rows = _visible_cars(rows)
    ok(visible[0].car == "19" and visible[0].trk == 1,
       f"race board must lead with the effective P1 (#19), got "
       f"#{visible[0].car} POS {visible[0].trk}")
    ok(all_rows[-1].car == "36" and all_rows[-1].trk == 8,
       f"race board must keep the pit-parked car re-ranked last, got "
       f"#{all_rows[-1].car} POS {all_rows[-1].trk}")


def test_missing_is_race_defaults_to_race():
    rows = _build_rows(_ctx(is_race=None), _practice_field(), {}, None)
    visible, _ = _visible_cars(rows)
    ok(visible[0].car == "19",
       "ctx without is_race must keep the historical (race) ordering")


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
