"""Regression test: the standings list must rebuild itself after timer suspension.

Bug (observed live, WEC São Paulo race 2026-07-12): the dashboard ran for hours
behind a locked screen. macOS App Nap suspended the Qt timers/paint plumbing;
on unlock the header chips (in-place QLabel updates) recovered, but the
standings table — cached custom-painted RowWidgets detached and re-parented
every tick — kept showing a ~3-hour-old board even though the DB (and the
poller's data) were fully current.

The fix is a rebuild watchdog: whenever a refresh tick arrives long after the
previous one (process was suspended), the window returns after a long absence,
or the list render's generation stamp trails the data, every cached row widget
is thrown away and the table is repainted with brand-new widgets.

Verifies:
  • rebuild_reason (pure): gap / away / generation-lag thresholds
  • healthy consecutive refreshes REUSE the cached widgets (no churn added)
  • a refresh after a simulated 3h suspension recreates every row widget and
    the new widgets carry the CURRENT data
  • a generation-stamp lag alone (render path died, data path alive) also
    forces the rebuild

Run (no pytest needed):
    ./venv/bin/python tests/test_table_rebuild.py
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

import series_profiles
import dashboard as dash
import dashboard_calm as dc
from calculator import CarAnalysis


def ok(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ── pure decision logic ───────────────────────────────────────────────────────
def test_rebuild_reason_tick_gap():
    ok(dc.rebuild_reason(gap_s=2.0) is None, "normal 2s tick must not rebuild")
    ok(dc.rebuild_reason(gap_s=dc.SUSPEND_GAP_S) is None, "boundary gap must not rebuild")
    ok(dc.rebuild_reason(gap_s=3 * 3600) is not None, "3h gap must rebuild")


def test_rebuild_reason_away():
    ok(dc.rebuild_reason(away_s=None) is None, "no blur timestamp → no rebuild")
    ok(dc.rebuild_reason(away_s=5.0) is None, "quick alt-tab must not rebuild")
    ok(dc.rebuild_reason(away_s=dc.REBUILD_AFTER_AWAY_S) is not None,
       "long absence must rebuild")


def test_rebuild_reason_generation_lag():
    ok(dc.rebuild_reason(data_gen=10, table_gen=10) is None, "in-sync → no rebuild")
    ok(dc.rebuild_reason(data_gen=10, table_gen=10 - dc.GEN_LAG_MAX) is None,
       "lag at the threshold must not rebuild")
    ok(dc.rebuild_reason(data_gen=10, table_gen=10 - dc.GEN_LAG_MAX - 1) is not None,
       "render trailing the data past the threshold must rebuild")


# ── integration: the watchdog wired into CalmDashboard.refresh ───────────────
def _ctx():
    return types.SimpleNamespace(
        profile=series_profiles.WEC, is_race=True, is_finished=False,
        flag="GF", event="Test 6h", session_name="Race", current_lap=100,
        final_type="BY_TIME", remaining_s=3600.0, caution_count=0, cautions=[],
        pit_model=types.SimpleNamespace(thin=False))


def _cars(leader):
    """3 HYPERCAR cars; `leader` (car number) runs P1."""
    order = [leader] + [n for n in ("7", "8", "50") if n != leader]
    cars = []
    for i, num in enumerate(order, 1):
        cars.append(CarAnalysis(
            car_number=num, car_class="HYPERCAR", driver=f"D{i}", team=f"T{i}",
            track_position=i, pos_in_class=i, effective_pos_in_class=i,
            net_position=i, laps=100))
    return cars


class FakePoller(dash.Poller):
    """Real Poller state dicts (for _build_rows), canned poll data (no DB)."""
    result_cars = _cars("7")

    def poll(self, delay_s: int = 0):
        cars = FakePoller.result_cars
        trend = {c.car_number: 0 for c in cars}
        return _ctx(), cars, [], 1.0, trend

    def real_age(self):
        return 1.0


def _make_dashboard():
    orig = dc.dash.Poller
    dc.dash.Poller = FakePoller
    try:
        w = dc.CalmDashboard(force_oid="test")   # __init__ runs one refresh
    finally:
        dc.dash.Poller = orig
    w.timer.stop()                               # tests drive refresh() directly
    return w


def test_healthy_refresh_reuses_widgets():
    w = _make_dashboard()
    ids1 = {car: id(rw) for car, rw in w._rows.items()}
    ok(len(ids1) == 3, f"expected 3 cached rows, got {len(ids1)}")
    w.refresh()
    ids2 = {car: id(rw) for car, rw in w._rows.items()}
    ok(ids1 == ids2, "a normal 0s-gap refresh must reuse the cached row widgets")


def test_suspension_gap_forces_fresh_widgets_with_current_data():
    w = _make_dashboard()
    old_rows = dict(w._rows)                     # hold refs so ids can't be recycled
    # while "suspended": the race moved on (leader changed) and 3h passed
    FakePoller.result_cars = _cars("8")
    w._last_tick_wall = time.time() - 3 * 3600
    try:
        w.refresh()
    finally:
        FakePoller.result_cars = _cars("7")
    ok(all(id(w._rows[c]) != id(old_rows[c]) for c in old_rows),
       "every row widget must be recreated after a suspension-sized tick gap")
    ok(w._rows["8"].vm["pos_text"] == "1",
       f"rebuilt board must show the CURRENT leader (#8 P1), "
       f"got POS {w._rows['8'].vm['pos_text']}")
    ok(not w._needs_rebuild, "rebuild flag must clear once the rebuild has run")
    ok(w._table_gen == w._data_gen, "render stamp must be back in sync after rebuild")


def test_generation_lag_alone_forces_rebuild():
    w = _make_dashboard()
    old_rows = dict(w._rows)
    w._table_gen = w._data_gen - (dc.GEN_LAG_MAX + 2)   # render path fell behind
    w.refresh()
    ok(all(id(w._rows[c]) != id(old_rows[c]) for c in old_rows),
       "a render-generation lag must force a from-scratch rebuild")
    ok(w._table_gen == w._data_gen, "render stamp must resync after the rebuild")


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
