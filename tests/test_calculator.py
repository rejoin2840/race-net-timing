"""Unit tests for src/calculator.py — the core net-position / pit-cost math.

Scoped deliberately to the *pure* calculation functions, where a wrong sign or
off-by-one yields a plausible-but-wrong dashboard number rather than a crash:
  • _linfit / _mean_std        — the OLS + stats primitives
  • _reject_long_stops         — robust (median+MAD) pit-stop outlier rejection
  • _gap_closing               — the "catching" trend gate (reads _GAP_HIST)
  • PitCostModel.predict_stop  — scope fallback (car→class→field→const) + floors
  • PitCostModel.build         — end-to-end fuel regression over an in-memory DB

Constants are read off the module (they hot-reload from config.json) so the tests
track config rather than hardcoding tunables.

Run (no pytest dependency — matches the other tests in this dir):
  ./venv/bin/python tests/test_calculator.py
"""
import os
import sqlite3
import sys
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import calculator  # noqa: E402
from calculator import (  # noqa: E402
    PitCostModel,
    _Stop,
    _gap_closing,
    _linfit,
    _mean_std,
    _reject_long_stops,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def close(a, b, abs_tol=1e-6):
    """Float comparison without a pytest dependency."""
    return abs(a - b) <= abs_tol


# ── _linfit ───────────────────────────────────────────────────────────────────
def test_linfit_exact_line():
    # y = 2x + 1, no noise → slope 2, intercept 1, zero residual std
    out = _linfit([0, 1, 2, 3], [1, 3, 5, 7])
    assert out is not None
    intercept, slope, std = out
    assert close(intercept, 1.0)
    assert close(slope, 2.0)
    assert close(std, 0.0)


def test_linfit_too_few_points():
    assert _linfit([1], [5]) is None
    assert _linfit([], []) is None


def test_linfit_no_x_spread():
    # all xs equal → slope undefined (sxx ≈ 0)
    assert _linfit([4, 4, 4], [1, 2, 3]) is None


def test_linfit_nonzero_residual():
    # scattered points off any single line → positive residual std
    out = _linfit([0, 1, 2, 3], [1, 2, 2, 5])
    assert out is not None
    _, _, std = out
    assert std > 0


# ── _mean_std ─────────────────────────────────────────────────────────────────
def test_mean_std_known():
    # classic worked example: population stdev of this set is exactly 2
    m, s = _mean_std([2, 4, 4, 4, 5, 5, 7, 9])
    assert close(m, 5.0)
    assert close(s, 2.0)


def test_mean_std_empty():
    assert _mean_std([]) is None


# ── _reject_long_stops ────────────────────────────────────────────────────────
def _stop(dur):
    return _Stop(car="7", cls="GTD", stint_laps=30.0, duration_ms=dur,
                 is_dc=False, green=True)


def test_reject_long_stops_drops_garage_stop():
    stops = [_stop(d) for d in (33000, 34000, 35000, 36000, 37000)]
    garage = _stop(300000)            # a 5-minute repair "stop"
    kept = _reject_long_stops(stops + [garage])
    durs = {s.duration_ms for s in kept}
    assert 300000 not in durs
    assert durs == {33000, 34000, 35000, 36000, 37000}


def test_reject_long_stops_guard_below_four():
    # len < 4 → returned untouched even with an obvious outlier
    stops = [_stop(35000), _stop(35000), _stop(300000)]
    assert _reject_long_stops(stops) == stops


def test_reject_long_stops_degenerate_mad():
    # MAD == 0 (no robust spread) → no cutoff, outlier survives
    stops = [_stop(35000), _stop(35000), _stop(35000), _stop(35000), _stop(999999)]
    kept = _reject_long_stops(stops)
    assert any(s.duration_ms == 999999 for s in kept)


def test_reject_long_stops_keeps_short_splash():
    # lower tail is intentionally left alone — a fast splash is legitimate
    stops = [_stop(d) for d in (35000, 35500, 36000, 36500)]
    splash = _stop(8000)
    kept = _reject_long_stops(stops + [splash])
    assert any(s.duration_ms == 8000 for s in kept)


# ── _robust_linfit (residual-space two-sided rejection for the fuel fit) ─────
def _sstop(stint, dur):
    return _Stop(car="7", cls="GTD", stint_laps=float(stint), duration_ms=float(dur),
                 is_dc=False, green=True)


def test_robust_linfit_keeps_long_stint_long_stop():
    """The Qatar failure: a legitimately long stop after a long stint must
    survive rejection (the old duration clip chopped that mode and biased
    the fit -46s there). In residual space the stint length explains it."""
    stops = ([_sstop(20, 60000 + i * 100) for i in range(4)]
             + [_sstop(45, 110000 + i * 100) for i in range(4)])
    a, b, _ = calculator._robust_linfit(stops)
    assert a + b * 45 > 100000        # prediction lives near the long mode


def test_robust_linfit_rejects_repair_stop():
    stops = [_sstop(30 + i, 70000 + i * 500) for i in range(8)]
    repair = _sstop(34, 991000)       # the real SP #8 repair "stop"
    a, b, _ = calculator._robust_linfit(stops + [repair])
    assert abs((a + b * 34) - 72000) < 5000   # fit unmoved by the repair


def test_robust_linfit_rejects_splash_from_fit():
    """A splash after a full stint is a huge NEGATIVE residual — the old
    pipeline kept it in the fit, dragging predictions low. Two-sided
    rejection drops it (while _reject_long_stops still keeps it for the
    transit floor, where it belongs)."""
    stops = [_sstop(30 + i, 70000 + i * 500) for i in range(8)]
    splash = _sstop(34, 8000)
    a, b, _ = calculator._robust_linfit(stops + [splash])
    assert abs((a + b * 34) - 72000) < 5000


def test_robust_linfit_small_n_no_rejection():
    stops = [_sstop(30, 70000), _sstop(31, 71000), _sstop(32, 300000)]
    fit = calculator._robust_linfit(stops)   # < 6 stops → plain fit
    plain = _linfit([s.stint_laps for s in stops],
                    [s.duration_ms for s in stops])
    assert fit == plain


# ── _lap_history_elapsed (Griiip channel-coherence gap source) ────────────────
def _mkdb_laps(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE lap_history (session_oid TEXT, car_number TEXT, "
                 "lap_number INTEGER, lap_time_ms INTEGER)")
    conn.executemany("INSERT INTO lap_history VALUES ('o',?,?,?)", rows)
    return conn


def test_lap_history_elapsed_prefix_sums():
    conn = _mkdb_laps([("7", 1, 90000), ("7", 2, 91000), ("7", 3, 92000)])
    out = calculator._lap_history_elapsed(conn, "o", {"7": 2})
    assert out["7"] == 181000          # truncated at the counter lap, not the newest


def test_lap_history_elapsed_hole_invalidates_later_laps():
    conn = _mkdb_laps([("7", 1, 90000), ("7", 3, 92000)])   # lap 2 missing
    out = calculator._lap_history_elapsed(conn, "o", {"7": 3})
    assert "7" not in out              # cumulative time would drift — fall back
    out1 = calculator._lap_history_elapsed(conn, "o", {"7": 1})
    assert out1["7"] == 90000          # laps before the hole still valid


def test_lap_history_elapsed_missing_counter_falls_back():
    conn = _mkdb_laps([("7", 1, 90000)])
    assert calculator._lap_history_elapsed(conn, "o", {"7": 5}) == {}


# ── _gap_closing (reads module-level _GAP_HIST) ───────────────────────────────
def _hist(oid, car, samples):
    calculator._GAP_HIST[(oid, car)] = deque(samples, maxlen=6)


def test_gap_closing_true_when_shrinking():
    calculator._GAP_HIST.clear()
    # inter-car gap (chaser − ahead) falls 1000→400ms over laps 7..10 → closing
    _hist("o", "chaser", [(7, 1000), (8, 800), (9, 600), (10, 400)])
    _hist("o", "ahead",  [(7, 0), (8, 0), (9, 0), (10, 0)])
    assert _gap_closing("o", "chaser", "ahead", cur_lap=10, trend_laps=3) is True


def test_gap_closing_false_when_widening():
    calculator._GAP_HIST.clear()
    _hist("o", "chaser", [(7, 400), (8, 500), (9, 600), (10, 700)])
    _hist("o", "ahead",  [(7, 0), (8, 0), (9, 0), (10, 0)])
    assert _gap_closing("o", "chaser", "ahead", cur_lap=10, trend_laps=3) is False


def test_gap_closing_false_insufficient_points():
    calculator._GAP_HIST.clear()
    # only 2 in-window samples but trend_laps=3
    _hist("o", "chaser", [(9, 800), (10, 400)])
    _hist("o", "ahead",  [(9, 0), (10, 0)])
    assert _gap_closing("o", "chaser", "ahead", cur_lap=10, trend_laps=3) is False


def test_gap_closing_excludes_stale_presample():
    calculator._GAP_HIST.clear()
    # The big drop lives only at lap 5, which is OUTSIDE the window (l > 10-4=6).
    # In-window laps 8,9,10 are flat, so a correct gate returns False — proving the
    # pre-caution sample can't leak in as a false close.
    _hist("o", "chaser", [(5, 1000), (8, 400), (9, 400), (10, 400)])
    _hist("o", "ahead",  [(5, 0), (8, 0), (9, 0), (10, 0)])
    assert _gap_closing("o", "chaser", "ahead", cur_lap=10, trend_laps=3) is False


def test_gap_closing_false_when_history_missing():
    calculator._GAP_HIST.clear()
    _hist("o", "chaser", [(8, 600), (9, 500), (10, 400)])
    # no history for "ahead"
    assert _gap_closing("o", "chaser", "ahead", cur_lap=10, trend_laps=3) is False


# ── PitCostModel.predict_stop (scope fallback + floors) ───────────────────────
def test_predict_stop_car_scope_wins():
    m = PitCostModel()
    m._fit_car = {"7": (40000.0, 0.0, 1000.0)}     # flat fit, mean 40000
    m._fit_cls = {"GTD": (45000.0, 0.0, 1500.0)}
    m._fit_all = (50000.0, 0.0, 2000.0)
    mean, std, scope = m.predict_stop("7", "GTD", stint_laps=30, owes_dc=False)
    assert close(mean, 40000.0)
    assert close(std, 1000.0)
    assert scope == "car"


def test_predict_stop_class_beats_field():
    m = PitCostModel()
    m._fit_cls = {"GTD": (45000.0, 0.0, 1500.0)}
    m._fit_all = (50000.0, 0.0, 2000.0)
    mean, std, scope = m.predict_stop("99", "GTD", stint_laps=30, owes_dc=False)
    assert close(mean, 45000.0)
    assert close(std, 1500.0)
    assert scope == "class"


def test_predict_stop_field_then_floor_and_dc():
    m = PitCostModel()
    m.transit_ms = calculator.DEFAULT_GREEN_PIT_MS      # 35000 floor
    m.dc_delta_ms = calculator.DRIVER_CHANGE_DELTA_MS   # +12000 on a driver change
    m._fit_all = (10000.0, 0.0, 800.0)                  # below the transit floor
    # floored up to transit, then driver-change delta added
    mean, std, scope = m.predict_stop("5", "LMP2", stint_laps=25, owes_dc=True)
    assert close(mean, calculator.DEFAULT_GREEN_PIT_MS + calculator.DRIVER_CHANGE_DELTA_MS)
    assert close(std, 800.0)
    assert scope == "field"


def test_predict_stop_std_fallback():
    m = PitCostModel()
    m._flat_all = (40000.0, None)                       # fit carries no usable std
    mean, std, scope = m.predict_stop("5", "LMP2", stint_laps=25, owes_dc=False)
    assert close(mean, 40000.0)
    assert close(std, calculator.DEFAULT_STOP_STD_MS)
    assert scope == "field"


def test_predict_stop_default_scope():
    m = PitCostModel()
    _, _, scope = m.predict_stop("99", "LMP2", stint_laps=25, owes_dc=False)
    assert scope == "default"


def test_pit_model_thin():
    m = PitCostModel()
    assert m.thin is True
    m._fit_all = (50000.0, 0.0, 2000.0)
    assert m.thin is False


# ── PitCostModel.build (end-to-end fuel regression over in-memory DB) ──────────
def _build_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE pit_events (
            session_oid TEXT, car_number TEXT, stop_number INTEGER,
            pit_lap INTEGER, flag TEXT, stop_duration_ms INTEGER);
        CREATE TABLE standings_current (
            session_oid TEXT, car_number TEXT, car_class TEXT);
        CREATE TABLE driver_changes (
            session_oid TEXT, car_number TEXT, session_lap INTEGER, seq INTEGER);
        """
    )
    conn.execute("INSERT INTO standings_current VALUES ('o','7','GTD')")
    # three green service stops, stint length rising 20→30→40 laps, cost rising
    # 34k→36k→38k ms → a clean +200 ms/lap fuel slope, intercept 30000.
    rows = [("o", "7", 1, 20, "GF", 34000),
            ("o", "7", 2, 50, "GF", 36000),
            ("o", "7", 3, 90, "GF", 38000)]
    conn.executemany("INSERT INTO pit_events VALUES (?,?,?,?,?,?)", rows)
    return conn


def test_build_learns_fuel_slope():
    conn = _build_db()
    m = PitCostModel.build(conn, "o")
    # transit floor = fastest green stop observed
    assert close(m.transit_ms, 34000.0)
    # predicted cost at a 30-lap stint follows the learned line: 30000 + 200*30
    mean, _, scope = m.predict_stop("7", "GTD", stint_laps=30, owes_dc=False)
    assert close(mean, 36000.0, abs_tol=1.0)
    assert scope == "car"


def test_series_overrides_merge_only_for_matching_series():
    """as_dict(series) applies SERIES_OVERRIDES on top of base; as_dict() doesn't;
    unknown keys inside an override are ignored (a typo can't inject a global)."""
    import config
    saved = config.CONFIG._vals
    try:
        config.CONFIG._vals = dict(saved)
        config.CONFIG._vals["DRIVER_CHANGE_DELTA_MS"] = 12000
        config.CONFIG._vals["SERIES_OVERRIDES"] = {
            "wec": {"DRIVER_CHANGE_DELTA_MS": 45000, "NOT_A_KNOB": 1}}
        assert config.CONFIG.as_dict()["DRIVER_CHANGE_DELTA_MS"] == 12000
        assert config.CONFIG.as_dict("imsa")["DRIVER_CHANGE_DELTA_MS"] == 12000
        wec = config.CONFIG.as_dict("wec")
        assert wec["DRIVER_CHANGE_DELTA_MS"] == 45000
        assert "NOT_A_KNOB" not in wec
    finally:
        config.CONFIG._vals = saved


def test_apply_config_routes_series_to_module_globals():
    """_apply_config('wec') lands the override in calculator's module globals
    (what PitCostModel and every knob reference actually read)."""
    import config
    saved = config.CONFIG._vals
    try:
        config.CONFIG._vals = dict(saved)
        config.CONFIG._vals["SERIES_OVERRIDES"] = {
            "wec": {"DRIVER_CHANGE_DELTA_MS": 45000}}
        calculator._apply_config("wec")
        assert calculator.DRIVER_CHANGE_DELTA_MS == 45000
        calculator._apply_config("imsa")
        assert calculator.DRIVER_CHANGE_DELTA_MS == saved["DRIVER_CHANGE_DELTA_MS"]
    finally:
        config.CONFIG._vals = saved
        calculator._apply_config()


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
