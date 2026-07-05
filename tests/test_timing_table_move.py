"""Smoke-tests for src/timing_table.py after the pure-move from dashboard.py.

Verifies:
  • Row/RunRow dataclass fields are present with expected defaults
  • cls_color is resolved from the active profile (not a stale global)
  • StrategyModel.data() and RunningModel.data() don't crash and return
    the right Python types for DisplayRole

Run (no pytest needed):
    ./venv/bin/python tests/test_timing_table_move.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import series_profiles
from timing_table import (
    CLASS_COLORS,
    Row,
    RunRow,
    RunningModel,
    StrategyModel,
    _build_rows,
    _build_run_rows,
)
from calculator import CarAnalysis


# ── helpers ───────────────────────────────────────────────────────────────────

def _car(number="10", cls="GTP", trk=1, pos_in_class=1, elapsed_ms=3_600_000,
         net_pos=1):
    c = CarAnalysis(
        car_number=number,
        car_class=cls,
        driver="Driver A",
        team="Team Alpha",
        track_position=trk,
        pos_in_class=pos_in_class,
        laps=30,
    )
    c.elapsed_ms = elapsed_ms
    c.net_position = net_pos
    c.net_gap_ms = 0.0 if net_pos == 1 else 15_000.0
    return c


def _ctx(profile=None):
    ctx = types.SimpleNamespace()
    ctx.profile = profile or series_profiles.IMSA
    return ctx


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ── Row / RunRow field defaults ───────────────────────────────────────────────

def test_row_defaults():
    r = Row()
    ok(r.cls_color == "#555B66", f"expected default cls_color, got {r.cls_color!r}")
    ok(r.is_header is False, "is_header should default False")
    ok(r.net is None, "net should default None")


def test_run_row_defaults():
    r = RunRow()
    ok(r.cls_color == "#555B66", f"expected default cls_color, got {r.cls_color!r}")
    ok(r.is_header is False, "is_header should default False")


# ── cls_color baked in at build time ─────────────────────────────────────────

def test_build_rows_cls_color_imsa():
    cars = [_car("10", "GTP")]
    rows = _build_rows(_ctx(series_profiles.IMSA), cars, {}, None)
    expected = series_profiles.IMSA.class_colors.get("GTP", "#555B66")
    car_rows = [r for r in rows if not r.is_header]
    ok(car_rows, "expected at least one car row")
    ok(car_rows[0].cls_color == expected,
       f"cls_color mismatch: {car_rows[0].cls_color!r} != {expected!r}")


def test_build_rows_header_cls_color():
    cars = [_car("91", "GTD")]
    rows = _build_rows(_ctx(series_profiles.IMSA), cars, {}, None)
    header_rows = [r for r in rows if r.is_header]
    expected = series_profiles.IMSA.class_colors.get("GTD", "#555B66")
    ok(header_rows, "expected a header row")
    ok(header_rows[0].cls_color == expected,
       f"header cls_color mismatch: {header_rows[0].cls_color!r} != {expected!r}")


def test_build_rows_wec_profile():
    cars = [_car("2", "HYPERCAR")]
    rows = _build_rows(_ctx(series_profiles.WEC), cars, {}, None)
    expected = series_profiles.WEC.class_colors.get("HYPERCAR", "#555B66")
    car_rows = [r for r in rows if not r.is_header]
    ok(car_rows, "expected at least one car row for WEC")
    ok(car_rows[0].cls_color == expected,
       f"WEC cls_color mismatch: {car_rows[0].cls_color!r} != {expected!r}")


def test_build_run_rows_cls_color():
    cars = [_car("10", "GTP")]
    rows = _build_run_rows(cars, None, profile=series_profiles.IMSA)
    car_rows = [r for r in rows if not r.is_header]
    expected = series_profiles.IMSA.class_colors.get("GTP", "#555B66")
    ok(car_rows, "expected at least one run-row car entry")
    ok(car_rows[0].cls_color == expected,
       f"run_row cls_color mismatch: {car_rows[0].cls_color!r} != {expected!r}")


def test_build_run_rows_filter_cls():
    cars = [_car("10", "GTP"), _car("91", "GTD", trk=2, pos_in_class=1,
                                     elapsed_ms=3_650_000, net_pos=1)]
    rows = _build_run_rows(cars, filter_cls="GTP", profile=series_profiles.IMSA)
    classes = {r.cls for r in rows}
    ok("GTD" not in classes, "filter_cls='GTP' should exclude GTD rows")


# ── StrategyModel.data() ─────────────────────────────────────────────────────

def test_strategy_model_data_types():
    from PyQt6.QtCore import Qt
    cars = [_car("10", "GTP"), _car("91", "GTD", trk=2, pos_in_class=1,
                                     elapsed_ms=3_650_000, net_pos=1)]
    rows = _build_rows(_ctx(), cars, {}, None)
    model = StrategyModel()
    model.set_rows(rows)

    for r in range(model.rowCount()):
        for c in range(model.columnCount()):
            idx = model.index(r, c)
            val = model.data(idx, Qt.ItemDataRole.DisplayRole)
            ok(val is None or isinstance(val, str),
               f"DisplayRole at ({r},{c}) should be str|None, got {type(val)}")


# ── RunningModel.data() ──────────────────────────────────────────────────────

def test_running_model_data_types():
    from PyQt6.QtCore import Qt
    cars = [_car("10", "GTP"), _car("91", "GTD", trk=2, pos_in_class=1,
                                     elapsed_ms=3_650_000, net_pos=1)]
    rows = _build_run_rows(cars, None, profile=series_profiles.IMSA)
    model = RunningModel()
    model.set_rows(rows)

    for r in range(model.rowCount()):
        for c in range(model.columnCount()):
            idx = model.index(r, c)
            val = model.data(idx, Qt.ItemDataRole.DisplayRole)
            ok(val is None or isinstance(val, str),
               f"RunningModel DisplayRole at ({r},{c}) should be str|None, got {type(val)}")


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
