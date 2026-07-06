"""
calculator.py — derived race analytics for the IMSA net-position tracker.

Pure, read-only computation over data/race.db (written by alkameldp.py).
No scraping, no UI. Turns the raw tables into the numbers a strategist watches.

Produces, per car (within its class):
  • Net position   — pit-adjusted order: where you really stand once everyone has
                     completed the same outstanding stops. Caution-aware (a stop
                     already taken under yellow is reflected in the real gap; future
                     stops are costed at the *current* flag's penalty).
  • Pit-now        — if this car pits this lap, what position does it rejoin in,
                     and amongst which cars (traffic)?
  • Catch & pass   — laps until it reaches the car ahead in class, from rolling pace.
  • Undercut/over  — flag a rival it can jump by pitting earlier / running longer.
  • Projected fin  — net order carried to the flag, nudged by pace over laps remaining.

Lapped status is read straight from each car's lap count vs the class leader's
(no reliance on the feed's ambiguous laps_behind field).

Usage:
  python src/calculator.py            # analyse current data/race.db, print tables
  python src/calculator.py --db X     # alternate database path
"""

import argparse
import math
import sqlite3
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config
import penalties
import series_profiles

DB_PATH = Path("data/race.db")

# ── tunables (hot-reloaded from config.json — see config.py) ─────────────────
# These names are module globals so existing references work unchanged; their
# values are (re)applied from config.CONFIG each analysis cycle via _apply_config().
def _apply_config():
    globals().update(config.CONFIG.as_dict())

_apply_config()   # seed at import (defaults if no config.json yet)

# ── lap-aligned gap history (powers the "catching" trend gate + battle arrows) ──
# Per-car deque of (lap, gap-to-class-leader ms), sampled once per new lap UNDER GREEN
# only — so caution bunching never reads as "closing". Module-level state: single-
# session desktop app, bounded by the deque; only recent samples are ever consulted.
_GAP_HIST: dict[tuple, deque] = {}


def reset_gap_history(oid: str) -> None:
    """Clear the lap-aligned gap history for one session oid.

    _GAP_HIST is module-level and keyed (oid, car), so back-to-back session
    builds under the SAME oid in one process (validate_races.py runs every
    race as oid="replay") otherwise inherit the previous race's end-of-race
    samples: _sample_gap rejects the new race's lower lap numbers and the
    catching gate reads stale gaps. Found 07-05 as run-to-run catch-metric
    non-determinism. replay._init_db calls this before every build/stream.
    """
    for key in [k for k in _GAP_HIST if k[0] == oid]:
        del _GAP_HIST[key]


def _sample_gap(oid: str, car: str, lap: Optional[int], gap_ms: Optional[float]) -> None:
    if lap is None or gap_ms is None:
        return
    dq = _GAP_HIST.get((oid, car))
    if dq is None:
        dq = deque(maxlen=6)
        _GAP_HIST[(oid, car)] = dq
    if dq and dq[-1][0] == lap:          # same lap re-analysed → refine the sample
        dq[-1] = (lap, gap_ms)
    elif not dq or lap > dq[-1][0]:      # a new lap → record it
        dq.append((lap, gap_ms))


def _gap_trend_seq(oid: str, chaser: str, ahead: str, cur_lap: int,
                    trend_laps: int) -> Optional[list]:
    """Inter-car gap (chaser − ahead, both vs the class leader) at each sampled green
    lap within the trend window, oldest first. None if there isn't enough history yet."""
    dc, da = _GAP_HIST.get((oid, chaser)), _GAP_HIST.get((oid, ahead))
    if not dc or not da:
        return None
    ahead_by_lap = {l: g for l, g in da}
    pts = sorted((l, gc - ahead_by_lap[l]) for l, gc in dc
                 if l in ahead_by_lap and l > cur_lap - (trend_laps + 1))
    if len(pts) < trend_laps:
        return None
    return [g for _, g in pts]


def _gap_closing(oid: str, chaser: str, ahead: str, cur_lap: int, trend_laps: int,
                  min_drop_ms: float = 150, noise_tol_ms: float = 50) -> bool:
    """True when the inter-car gap has been non-increasing across the last `trend_laps`
    green laps, with a meaningful net drop. Only samples within the window are used, so
    pre-caution history (sampling pauses under yellow) can't leak in and read as a false
    close. `min_drop_ms`/`noise_tol_ms` default to the main-board "catching" call's
    strict thresholds; callers with a looser bar (e.g. the BATTLES rail) can pass their
    own without affecting that call."""
    seq = _gap_trend_seq(oid, chaser, ahead, cur_lap, trend_laps)
    if seq is None:
        return False
    return (seq[-1] <= seq[0] - min_drop_ms
            and all(b <= a + noise_tol_ms for a, b in zip(seq, seq[1:])))


def _gap_close_rate_s(oid: str, chaser: str, ahead: str, cur_lap: int,
                       trend_laps: int) -> Optional[float]:
    """Average per-lap gap closure over the trend window, in seconds (always positive
    when _gap_closing is True, since that gate requires a net drop across the window —
    unlike a bare last-lap-over-lap delta, which the looser BATTLES noise tolerance can
    let go slightly negative on the final step even while the overall trend still
    closes). Call only when _gap_closing is True."""
    seq = _gap_trend_seq(oid, chaser, ahead, cur_lap, trend_laps)
    if seq is None or len(seq) < 2:
        return None
    return (seq[0] - seq[-1]) / 1000.0 / (len(seq) - 1)

# Pit-sequence states where the feed's pos_in_class is stale (frozen mid-stop, only
# re-sorting at S/F) — these cars get re-ranked by cumulative-time gap. Mirrors
# dashboard.PIT_LANE_STATES (kept local to avoid a calculator→dashboard import cycle).
_PIT_SEQUENCE_STATES = ("BOX", "IN_LAP", "OUT_LAP", "PIT", "STOPPED")

GREEN_FLAGS   = {"GF"}
CAUTION_FLAGS = {"YF", "FCY", "CY", "SC", "VSC", "FCY1", "SCS"}
VSC_FLAGS     = {"VSC"}
RACE_EXCLUDE_TYPES = {"QUALIFYING_BEST_LAP", "QUALIFYING_AVG_LAP", "PRACTICE", "WARM_UP"}


# ── data shapes ─────────────────────────────────────────────────────────────
@dataclass
class RaceContext:
    session_oid:    str
    event:          str
    session_name:   str
    session_type:   str
    is_race:        bool
    flag:           str
    under_caution:  bool
    current_lap:    int
    leader_laps:    int
    elapsed_s:      float
    remaining_s:    float
    final_type:     str
    is_finished:    bool
    pit_model:      "PitCostModel"
    green_typical_ms: float      # representative green stop (header display only)
    # caution clustering (within-race history)
    caution_count:  int = 0
    last_caution_lap: Optional[int] = None
    cautions:       list = field(default_factory=list)   # [(start_lap, end_lap, dur_s)]
    # series profile — routes class/strategy behaviour. Defaults to IMSA so any
    # legacy single-series call path is unchanged; analyse() sets the real one.
    profile: "series_profiles.SeriesProfile" = series_profiles.IMSA


@dataclass
class CarAnalysis:
    car_number:       str
    car_class:        str
    driver:           Optional[str]
    team:             Optional[str]
    track_position:   Optional[int]               # overall on-track position (whole field)
    pos_in_class:     Optional[int]               # on-track position within class
    laps:             Optional[int]
    laps_down:        int = 0
    effective_pos_in_class: Optional[int] = None  # pos_in_class for running cars; for
                                                  #   cars in the pit sequence, re-ranked
                                                  #   by real cumulative-time gap (the feed
                                                  #   freezes pos mid-stop — see _derive_class)
    elapsed_ms:       Optional[int] = None       # cumulative race time (for overall gaps)
    class_gap_ms:     Optional[float] = None     # time gap to class leader (same-lap)
    last_lap_ms:      Optional[int] = None
    best_lap_ms:      Optional[int] = None
    avg_pace_ms:      Optional[float] = None
    track_status:     Optional[str] = None
    stops:            int = 0
    last_pit_lap:     Optional[int] = None
    stint_laps:       Optional[int] = None
    est_stops_left:   int = 0
    owes_driver_change: bool = False
    next_stop_ms:     Optional[float] = None     # predicted cost of this car's next stop
    next_stop_std_ms: Optional[float] = None     # 1σ spread on that prediction
    pit_scope:        str = "default"            # which model scope predicted the next stop
    # sector analysis (best sector vs class best)
    best_s1_ms:       Optional[int] = None
    best_s2_ms:       Optional[int] = None
    best_s3_ms:       Optional[int] = None
    sec_delta_ms:     tuple = (None, None, None)  # (Δs1, Δs2, Δs3) vs class best, ms
    # stint / fuel window
    fuel_laps_left:   Optional[int] = None        # laps until fuel runs out at avg stint
    must_pit_lap:     Optional[int] = None        # session lap this car must pit by
    pit_window_open:  bool = False                # within the strategic pit window now
    # real fuel telemetry (virtual fuel tank). fuel_pct None = no data (e.g. LMP2).
    fuel_pct:         Optional[float] = None      # virtual fuel tank %, real telemetry
    fuel_flag:        Optional[str] = None        # IMSA low-fuel warning: 'yellow' / 'red'
    fuel_due:         Optional[str] = None        # 'due' from stint estimate (pit window open + ~1 lap fuel left); drives rail roster only
    # tire degradation
    deg_ms_per_lap:   Optional[float] = None      # lap-time loss per lap this stint (None = not significant)
    # F1 tyres / 2026 energy state (Phase 1 schema; NULL for IMSA)
    tire_compound:    Optional[str] = None         # SOFT/MEDIUM/HARD/INTERMEDIATE/WET
    tire_age:         Optional[int] = None         # laps on the current tyre set
    override_state:   Optional[str] = None         # 2026 manual-override/boost state, live-only
    # penalties (parsed from race control)
    penalty_s:        float = 0.0                 # pending in-race seconds → carried into NET
    penalty_post_s:   float = 0.0                 # post-race time penalty → projected finish only
    penalty_note:     str = ""
    dq:               bool = False
    # derived
    net_position:     Optional[int] = None       # net position in class (headline)
    net_settled:      bool = False                # class's final pit cycle done and no pending
                                                  # penalty on this car → net ≡ track position
    net_gap_ms:       Optional[float] = None
    net_gap_band_ms:  Optional[float] = None      # ± uncertainty on net gap
    pit_now_position: Optional[int] = None
    pit_now_among:    list = field(default_factory=list)
    catching:         Optional[str] = None
    catch_in_laps:    Optional[float] = None
    strategy_note:    str = ""
    projected_finish: Optional[int] = None


# ── helpers ─────────────────────────────────────────────────────────────────
def _ms_to_laptime(ms) -> str:
    if ms is None or ms <= 0:
        return "—"
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:06.3f}"


def _gap_str(ms) -> str:
    if ms is None:
        return "—"
    if ms <= 0:
        return "—"
    return f"+{ms / 1000:.1f}s"


def _is_caution(flag: Optional[str]) -> bool:
    return (flag or "").upper() in CAUTION_FLAGS


# ── loaders ─────────────────────────────────────────────────────────────────
def _load_context(conn: sqlite3.Connection, oid: str) -> RaceContext:
    sess = conn.execute(
        "SELECT event_name, session_name, session_type FROM sessions WHERE session_oid=?",
        (oid,)).fetchone()
    st = conn.execute(
        "SELECT * FROM session_status WHERE session_oid=?", (oid,)).fetchone()

    session_type = (sess["session_type"] if sess else "") or ""
    is_race = session_type not in RACE_EXCLUDE_TYPES

    flag = st["current_flag"] if st and st["current_flag"] else "?"
    current_lap = (st["current_lap"] if st and st["current_lap"] else 0)
    final_type = (st["final_type"] if st else "") or ""

    # clock: elapsed/remaining for a BY_TIME race
    elapsed_s = remaining_s = 0.0
    if st and st["start_time_s"]:
        elapsed_s = max(0.0, time.time() - st["start_time_s"] - (st["stopped_s"] or 0))
        if final_type == "BY_TIME" and st["final_time_s"]:
            total = st["final_time_s"] + (st["extra_time_s"] or 0 if st["has_extra_time"] else 0)
            elapsed_s = min(elapsed_s, total)
            remaining_s = max(0.0, total - elapsed_s)

    leader_laps = conn.execute(
        "SELECT MAX(laps) FROM standings_current WHERE session_oid=? AND is_running=1",
        (oid,)).fetchone()[0] or 0

    # The feed never sets currentFlag to checkered — it signals the end via
    # isFinished (+ a "CHECKERED FLAG" race-control message), leaving the flag
    # stuck on whatever was flying (often FCY). Force checkered when finished.
    # is_finished from the feed isn't always reliable (especially in replay/test
    # data), so also infer finish from the session's own end condition: BY_LAPS
    # once the leader has completed the scheduled distance, or BY_TIME once the
    # clock (which has actually started) has run out.
    final_laps = (st["final_laps"] if st and "final_laps" in st.keys() else None)
    start_time_s = (st["start_time_s"] if st and "start_time_s" in st.keys() else None)
    final_time_s = (st["final_time_s"] if st and "final_time_s" in st.keys() else None)
    finished = bool(st and st["is_finished"])
    if not finished and final_type == "BY_LAPS" and final_laps:
        finished = leader_laps >= final_laps
    if not finished and final_type == "BY_TIME" and start_time_s and final_time_s:
        finished = remaining_s <= 0
    if finished:
        flag = "CH"

    pit_model = PitCostModel.build(conn, oid)
    cautions = _load_cautions(conn, oid)

    return RaceContext(
        session_oid=oid,
        event=(sess["event_name"] if sess else "?"),
        session_name=(sess["session_name"] if sess else "?"),
        session_type=session_type,
        is_race=is_race,
        flag=flag,
        under_caution=_is_caution(flag),
        current_lap=current_lap,
        leader_laps=leader_laps,
        elapsed_s=elapsed_s,
        remaining_s=remaining_s,
        final_type=final_type,
        is_finished=finished,
        pit_model=pit_model,
        green_typical_ms=pit_model.green_typical_ms,
        caution_count=len(cautions),
        last_caution_lap=(cautions[-1][0] if cautions else None),
        cautions=cautions,
    )


# ── pit-cost model ──────────────────────────────────────────────────────────
def _linfit(xs: list[float], ys: list[float]):
    """Ordinary least squares. Returns (intercept, slope, resid_std) or None."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-9:                       # no spread in x → slope undefined
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    std = (sum(r * r for r in resid) / n) ** 0.5
    return intercept, slope, std


def _mean_std(vals: list[float]):
    if not vals:
        return None
    m = sum(vals) / len(vals)
    s = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
    return m, s


def _reject_long_stops(stops: list["_Stop"]) -> list["_Stop"]:
    """Drop implausibly long stop durations before fitting the pit-cost model.

    A green "service stop" should be pit-lane transit + fuel/tyres/driver. Cars
    parked for repairs or retirements get recorded as multi-minute "stops" that
    poison the fuel regression (we saw a negative fuel slope and a σ larger than
    the mean on real 24h data). Reject the upper tail robustly via median + K·MAD
    (MAD scaled to σ by 1.4826). Lower tail is left alone — short splashes are
    handled elsewhere and the transit floor is a legitimate min."""
    if len(stops) < 4:
        return stops
    ds = sorted(s.duration_ms for s in stops)
    med = ds[len(ds) // 2]
    mad = sorted(abs(d - med) for d in ds)[len(ds) // 2]
    if mad <= 0:                       # degenerate spread → no robust cutoff
        return stops
    hi = med + STOP_OUTLIER_MAD * 1.4826 * mad
    return [s for s in stops if s.duration_ms <= hi]


@dataclass
class _Stop:
    car: str
    cls: str
    stint_laps: float
    duration_ms: float
    is_dc: bool
    green: bool


class PitCostModel:
    """
    Predicts the time cost of a pit stop, learned from observed stops.

    The feed gives only total stop duration — never the fuel/tire/driver split —
    so the cost is *inferred*:
      • transit floor  = fastest green stop seen (pit-lane transit + a splash)
      • fuel fill      = regression of green non-driver-change stop duration on the
                         preceding stint length (more laps run → more fuel → longer)
      • driver change  = average extra time when the active driver changed at a stop
    Predictions fall back car → class → field → constant as data thins out, and
    every prediction carries a 1σ spread so net position can show a ± band.
    """
    def __init__(self):
        self.transit_ms = DEFAULT_GREEN_PIT_MS
        self.green_typical_ms = DEFAULT_GREEN_PIT_MS
        self.dc_delta_ms = DRIVER_CHANGE_DELTA_MS
        self._fit_car: dict = {}      # car   → (a, b, std)
        self._fit_cls: dict = {}      # class → (a, b, std)
        self._fit_all = None          # field → (a, b, std)
        self._flat_car: dict = {}     # car   → (mean, std)
        self._flat_cls: dict = {}     # class → (mean, std)
        self._flat_all = None         # field → (mean, std)

    @classmethod
    def build(cls, conn: sqlite3.Connection, oid: str) -> "PitCostModel":
        m = cls()
        stops = _load_stops(conn, oid)
        # robust upper-tail rejection: garage/repair "stops" otherwise poison the fuel fit
        green = _reject_long_stops([s for s in stops if s.green])
        if green:
            m.transit_ms = min(s.duration_ms for s in green)

        nodc = [s.duration_ms for s in green if not s.is_dc]
        base = nodc or [s.duration_ms for s in green]   # service stops drive the typical
        if base:
            m.green_typical_ms = sum(base) / len(base)

        # fuel-fill fits on green, non-driver-change stops, at each scope
        fuel = [s for s in green if not s.is_dc]
        m._fit_all = _linfit([s.stint_laps for s in fuel], [s.duration_ms for s in fuel]) \
            if len(fuel) >= MIN_FIT_POINTS else None
        m._flat_all = _mean_std([s.duration_ms for s in fuel])
        for scope_key, store_fit, store_flat in (("cls", m._fit_cls, m._flat_cls),
                                                 ("car", m._fit_car, m._flat_car)):
            groups: dict = {}
            for s in fuel:
                groups.setdefault(s.cls if scope_key == "cls" else s.car, []).append(s)
            for key, ss in groups.items():
                store_flat[key] = _mean_std([s.duration_ms for s in ss])
                if len(ss) >= MIN_FIT_POINTS:
                    fit = _linfit([s.stint_laps for s in ss], [s.duration_ms for s in ss])
                    if fit:
                        store_fit[key] = fit

        # driver-change delta, isolated from fuel: a DC stop's duration minus what the
        # fuel curve predicts for its stint. (Naive mean(DC)−mean(non-DC) would absorb
        # fuel time whenever DC stops run on longer stints.) Pooled across the field.
        dc_stops = [s for s in green if s.is_dc]
        if dc_stops and m._fit_all:
            a, b, _ = m._fit_all
            res = [s.duration_ms - (a + b * s.stint_laps) for s in dc_stops]
            m.dc_delta_ms = max(0.0, sum(res) / len(res))
        elif dc_stops and nodc:
            m.dc_delta_ms = max(0.0,
                sum(s.duration_ms for s in dc_stops) / len(dc_stops) - sum(nodc) / len(nodc))
        return m

    @property
    def thin(self) -> bool:
        return self._fit_all is None and not self._fit_cls and not self._fit_car

    def predict_stop(self, car: str, cls: str, stint_laps: float,
                     owes_dc: bool) -> tuple[float, float, str]:
        """Return (mean_ms, std_ms, scope) for this car's next green stop."""
        mean = std = None
        scope = "default"
        for fit_store, flat_store, key, sc in (
                (self._fit_car, self._flat_car, car, "car"),
                (self._fit_cls, self._flat_cls, cls, "class")):
            if key in fit_store:
                a, b, s = fit_store[key]
                mean, std = a + b * max(0.0, stint_laps), s
                scope = sc
                break
            if key in flat_store:
                mean, std = flat_store[key]
                scope = sc
                break
        if mean is None and self._fit_all:
            a, b, s = self._fit_all
            mean, std = a + b * max(0.0, stint_laps), s
            scope = "field"
        if mean is None and self._flat_all:
            mean, std = self._flat_all
            scope = "field"
        if mean is None:
            mean, std = DEFAULT_GREEN_PIT_MS, DEFAULT_STOP_STD_MS
        mean = max(mean, self.transit_ms)
        if owes_dc:
            mean += self.dc_delta_ms
        return mean, (std or DEFAULT_STOP_STD_MS), scope


def _load_stops(conn: sqlite3.Connection, oid: str) -> list[_Stop]:
    """All observed stops with preceding stint length + driver-change classification."""
    rows = conn.execute(
        """SELECT p.car_number AS car, p.stop_number AS sn, p.pit_lap AS lap,
                  p.flag AS flag, p.stop_duration_ms AS dur, s.car_class AS cls
             FROM pit_events p
             LEFT JOIN standings_current s
               ON s.session_oid=p.session_oid AND s.car_number=p.car_number
            WHERE p.session_oid=? AND p.stop_duration_ms IS NOT NULL AND p.pit_lap IS NOT NULL
            ORDER BY p.car_number, p.stop_number""", (oid,)).fetchall()
    # driver-change laps per car (seq>=2 are actual changes)
    dc_laps: dict[str, list[int]] = {}
    for r in conn.execute(
        """SELECT car_number AS car, session_lap AS lap FROM driver_changes
             WHERE session_oid=? AND seq >= 2 AND session_lap IS NOT NULL""", (oid,)):
        dc_laps.setdefault(r["car"], []).append(r["lap"])

    stops: list[_Stop] = []
    prev_lap: dict[str, int] = {}
    for r in rows:
        car, lap = r["car"], r["lap"]
        stint = lap - prev_lap.get(car, 0)          # first stop's stint = laps from start
        prev_lap[car] = lap
        is_dc = any(abs(lap - dl) <= DC_NEAR_LAPS for dl in dc_laps.get(car, []))
        stops.append(_Stop(car=car, cls=r["cls"] or "?", stint_laps=max(1.0, stint),
                           duration_ms=r["dur"], is_dc=is_dc,
                           green=not _is_caution(r["flag"])))
    return stops


def _avg_pace(conn: sqlite3.Connection, oid: str, car: str,
              best_lap_ms: Optional[int]) -> Optional[float]:
    """Rolling average of the last PACE_WINDOW clean laps (outliers filtered)."""
    rows = conn.execute(
        """SELECT lap_time_ms FROM lap_history
             WHERE session_oid=? AND car_number=? AND lap_time_ms > 0
             ORDER BY lap_number DESC LIMIT ?""",
        (oid, car, PACE_WINDOW * 3)).fetchall()
    times = [r["lap_time_ms"] for r in rows]
    if not times:
        return None
    ref = best_lap_ms or min(times)
    clean = [t for t in times if t <= ref * PACE_OUTLIER_FACTOR][:PACE_WINDOW]
    if not clean:
        clean = sorted(times)[:PACE_WINDOW]   # all "dirty": fall back to fastest seen
    return sum(clean) / len(clean)


def _best_sectors(conn: sqlite3.Connection, oid: str) -> dict[str, tuple]:
    """Per car, the fastest S1/S2/S3 seen (min is naturally green — yellow laps are
    slower, so they never win the min)."""
    rows = conn.execute(
        """SELECT car_number,
                  MIN(NULLIF(s1_ms,0)) AS s1,
                  MIN(NULLIF(s2_ms,0)) AS s2,
                  MIN(NULLIF(s3_ms,0)) AS s3
             FROM lap_history WHERE session_oid=? GROUP BY car_number""",
        (oid,)).fetchall()
    return {r["car_number"]: (r["s1"], r["s2"], r["s3"]) for r in rows}


def _tire_deg(conn: sqlite3.Connection, oid: str, car: str,
              ref_lap_ms: Optional[int], last_pit_lap: Optional[int]) -> Optional[float]:
    """Lap-time degradation slope (ms per lap) over the CURRENT stint, or None if the
    signal isn't clean enough to trust. Traffic/yellow laps are filtered out first;
    we only report a slope when it clearly exceeds the residual noise — so a noisy or
    flat stint self-suppresses rather than showing a bogus number."""
    if last_pit_lap is None:
        return None
    rows = conn.execute(
        """SELECT lap_number, lap_time_ms FROM lap_history
             WHERE session_oid=? AND car_number=? AND lap_time_ms > 0
                   AND lap_number > ? ORDER BY lap_number""",
        (oid, car, last_pit_lap)).fetchall()
    if len(rows) < 6:
        return None
    ref = ref_lap_ms or min(r["lap_time_ms"] for r in rows)
    pts = [(r["lap_number"] - last_pit_lap, r["lap_time_ms"]) for r in rows
           if r["lap_time_ms"] <= ref * PACE_OUTLIER_FACTOR]   # drop traffic/in-out/yellow
    if len(pts) < 6:
        return None
    fit = _linfit([x for x, _ in pts], [y for _, y in pts])
    if not fit:
        return None
    _intercept, slope, resid_std = fit
    span = max(x for x, _ in pts) - min(x for x, _ in pts)
    # require a positive slope whose effect over the stint clearly beats the noise
    if slope <= 0 or span <= 0 or slope * span < 2 * resid_std:
        return None
    return slope


def _load_penalties(conn: sqlite3.Connection, oid: str) -> dict:
    """Parse race-control text into per-car penalty carry. Returns
    {car: (pending_s, post_race_s, note, dq)}."""
    try:
        rows = conn.execute(
            "SELECT message FROM race_control WHERE session_oid=?", (oid,)).fetchall()
    except sqlite3.Error:
        return {}
    return penalties.aggregate(r["message"] for r in rows if r["message"])


def _load_cautions(conn: sqlite3.Connection, oid: str) -> list[tuple]:
    """Completed/ongoing caution periods this race: [(start_lap, end_lap, dur_s)]."""
    try:
        rows = conn.execute(
            """SELECT start_lap, end_lap, duration_s FROM caution_periods
                 WHERE session_oid=? ORDER BY start_lap""", (oid,)).fetchall()
    except sqlite3.Error:
        return []   # table may predate this feature
    return [(r["start_lap"], r["end_lap"], r["duration_s"]) for r in rows]


def _class_stint_laps(conn: sqlite3.Connection, oid: str) -> dict[str, float]:
    """Average observed *green* stint length (laps between stops) per class.

    Only learns from representative green stints. Stops taken under caution and
    implausibly short stints (caution splashes / closed-pit cycles) are excluded
    so a flurry of early yellow stops can't collapse the class average — that
    would falsely fling every car's fuel window 'OPEN' and explode est-stops.
    A class needs MIN_SAMPLE clean stints before it overrides the prior; until
    then the caller falls back to DEFAULT_STINT_LAPS.
    """
    MIN_SAMPLE = 2
    rows = conn.execute(
        """SELECT s.car_class AS cls, p.car_number AS car, p.pit_lap AS lap, p.flag AS flag
             FROM pit_events p
             JOIN standings_current s
               ON s.session_oid=p.session_oid AND s.car_number=p.car_number
            WHERE p.session_oid=? AND p.pit_lap IS NOT NULL
            ORDER BY p.car_number, p.stop_number""", (oid,)).fetchall()
    stints: dict[str, list[int]] = {}
    last_lap: dict[str, int] = {}
    for r in rows:
        car, lap, cls = r["car"], r["lap"], (r["cls"] or "?")
        if car in last_lap and lap > last_lap[car]:
            length = lap - last_lap[car]
            # the stint that just ended at this pit is only representative if the
            # stop wasn't under caution and the run was a plausible green length
            floor = 0.5 * DEFAULT_STINT_LAPS.get(cls, DEFAULT_STINT_FALLBACK)
            if (r["flag"] or "") not in CAUTION_FLAGS and length >= floor:
                stints.setdefault(cls, []).append(length)
        last_lap[car] = lap
    return {cls: sum(v) / len(v)
            for cls, v in stints.items() if len(v) >= MIN_SAMPLE}


def _driver_obligation(conn: sqlite3.Connection, oid: str) -> dict[str, bool]:
    """
    Per car, does it still owe a mandatory driver change? Heuristic: every listed
    co-driver must drive at least once, so required changes ≈ (lineup size − 1).
    Validated against the 2026 regs (data/regulations_2026.json): for WEC 6H every
    legal lineup requires exactly lineup−1 changes, so the count is correct there;
    time-based obligations (4h rolling cap, per-class minimums) are a future
    upgrade — see BACKLOG research items.
    """
    import json as _json
    lineup: dict[str, int] = {}
    for r in conn.execute(
        "SELECT car_number, drivers FROM session_entry WHERE session_oid=?", (oid,)):
        n = 0
        if r["drivers"]:
            try:
                d = _json.loads(r["drivers"])
                n = len(d) if isinstance(d, (dict, list)) else 0
            except Exception:
                n = 0
        lineup[r["car_number"]] = n
    done: dict[str, int] = {}
    for r in conn.execute(
        """SELECT car_number, MAX(seq) AS mx FROM driver_changes
             WHERE session_oid=? GROUP BY car_number""", (oid,)):
        done[r["car_number"]] = max(0, (r["mx"] or 1) - 1)   # seq 1 = baseline
    owes: dict[str, bool] = {}
    for car, size in lineup.items():
        required = max(0, size - 1)
        owes[car] = done.get(car, 0) < required
    return owes


# ── core assembly ───────────────────────────────────────────────────────────
def analyse(conn: sqlite3.Connection, oid: str) -> tuple[RaceContext, list[CarAnalysis]]:
    config.CONFIG.reload_if_changed()   # pick up live config.json edits (~2s latency)
    _apply_config()
    ctx = _load_context(conn, oid)
    ctx.profile = series_profiles.get_profile(session_series(conn, oid))
    observed_stints = _class_stint_laps(conn, oid)
    owes_dc = _driver_obligation(conn, oid)
    best_sectors = _best_sectors(conn, oid)
    penalties = _load_penalties(conn, oid)

    rows = conn.execute(
        """SELECT s.car_number, s.overall_position, s.pos_in_class, s.car_class,
                  s.laps, s.gap_ms, s.elapsed_ms, s.last_lap_ms, s.best_lap_ms, s.pits,
                  s.last_pit_lap, s.track_status, s.is_running,
                  s.fuel_pct, s.fuel_flag,
                  s.tire_compound, s.tire_age, s.override_state,
                  e.name AS driver, e.team AS team
             FROM standings_current s
             LEFT JOIN session_entry e
               ON e.session_oid=s.session_oid AND e.car_number=s.car_number
            WHERE s.session_oid=?""", (oid,)).fetchall()

    cars: list[CarAnalysis] = []
    for r in rows:
        cls = r["car_class"] or "?"
        ca = CarAnalysis(
            car_number=r["car_number"], car_class=cls,
            driver=r["driver"], team=r["team"],
            track_position=r["overall_position"], pos_in_class=r["pos_in_class"],
            laps=r["laps"], last_lap_ms=r["last_lap_ms"], best_lap_ms=r["best_lap_ms"],
            track_status=r["track_status"], stops=r["pits"] or 0,
            last_pit_lap=r["last_pit_lap"],
            elapsed_ms=r["elapsed_ms"],
        )
        ca.fuel_pct  = r["fuel_pct"]
        ca.fuel_flag = r["fuel_flag"]
        ca.tire_compound  = r["tire_compound"]
        ca.tire_age       = r["tire_age"]
        ca.override_state = r["override_state"]
        # NOTE: fuel_due is NOT derived from the VFT flag. That telemetry is replay-only
        # (the live Al Kamel feed carries no VFT) and proved unreliable — cars read
        # near-empty for laps after refuelling, lighting "DUE" on the whole field. fuel_due
        # is set below from the stint estimate (pit window open + ~1 lap of fuel left).
        ca.avg_pace_ms = _avg_pace(conn, oid, r["car_number"], r["best_lap_ms"])
        if r["last_pit_lap"] is not None and r["laps"] is not None:
            ca.stint_laps = max(0, r["laps"] - r["last_pit_lap"])
        ca.owes_driver_change = owes_dc.get(r["car_number"], False)
        bs = best_sectors.get(r["car_number"], (None, None, None))
        ca.best_s1_ms, ca.best_s2_ms, ca.best_s3_ms = bs
        ca.deg_ms_per_lap = _tire_deg(conn, oid, r["car_number"],
                                      r["best_lap_ms"], r["last_pit_lap"])
        pen = penalties.get(r["car_number"])
        if pen:
            ca.penalty_s, ca.penalty_post_s, ca.penalty_note, ca.dq = pen
        cars.append(ca)

    # group by class and derive everything within-class
    by_class: dict[str, list[CarAnalysis]] = {}
    for ca in cars:
        by_class.setdefault(ca.car_class, []).append(ca)

    # Per-car "time to overall leader" proxy. The feed's gap_ms field is 0 even
    # under green, so we prefer cumulative elapsed_ms — its differences give the
    # real same-lap gaps. Decided all-or-nothing: mixing elapsed (~millions of ms)
    # with a 0 fallback would pick a bogus leader and explode every gap, so we
    # only switch to elapsed once *every* car reports it (full snapshot in); until
    # then we keep the old gap_ms behaviour.
    has_elapsed = bool(rows) and "elapsed_ms" in rows[0].keys()
    use_elapsed = has_elapsed and all(r["elapsed_ms"] is not None for r in rows)
    feed_gap = {r["car_number"]: (r["elapsed_ms"] if use_elapsed else r["gap_ms"])
                for r in rows}

    for cls, group in by_class.items():
        _derive_class(ctx, cls, group, feed_gap, observed_stints.get(cls))

    cars.sort(key=lambda c: (c.track_position if c.track_position else 9999))
    return ctx, cars


def _assign_effective_positions(group: list[CarAnalysis]) -> None:
    """Set effective_pos_in_class for one class group (call after class_gap_ms/laps_down).

    The feed's pos_in_class only updates at timing-line crossings, so it FREEZES while a
    car sits in the pits (verified on replay: held its pre-stop slot through 18/33 GTP
    stops, snapping only after the out-lap) — yet its cumulative-time gap grows
    continuously. On the track-led board that desyncs position from gap: a stale-high
    slot carrying a ballooning gap. Fix: keep the official feed order for running cars
    (it matches the broadcast), but re-rank cars in the pit sequence by their real
    class_gap_ms so they sink smoothly and the gap stays monotonic. Hands back to the
    feed value the moment the car returns to RUN (re-sorted at S/F by then).
    """
    running = sorted([c for c in group
                      if (c.track_status or "") not in _PIT_SEQUENCE_STATES],
                     key=lambda c: c.pos_in_class or 99)
    boxed = sorted([c for c in group
                    if (c.track_status or "") in _PIT_SEQUENCE_STATES],
                   key=lambda c: c.class_gap_ms if c.class_gap_ms is not None else 9e18)
    ordered = list(running)
    for b in boxed:
        g = b.class_gap_ms
        if g is None:
            # no real time gap (feed sentinel) — can't time-rank; keep its feed slot
            idx = min(len(ordered), max(0, (b.pos_in_class or len(ordered) + 1) - 1))
        else:
            idx = len(ordered)
            for i, c in enumerate(ordered):
                if c.class_gap_ms is not None and c.class_gap_ms > g:
                    idx = i
                    break
        ordered.insert(idx, b)
    for i, ca in enumerate(ordered, 1):
        ca.effective_pos_in_class = i


def _derive_class(ctx: RaceContext, cls: str, group: list[CarAnalysis],
                  feed_gap: dict, observed_stint: Optional[float]) -> None:
    # class leader = most laps, then smallest overall feed gap
    group_sorted = sorted(
        group, key=lambda c: (-(c.laps or 0), feed_gap.get(c.car_number) or 0))
    leader = group_sorted[0]
    leader_gap = feed_gap.get(leader.car_number) or 0
    leader_laps = leader.laps or 0
    # representative class lap, for a TIME-based "is it really lapped?" test
    lap_ref_ms = _median_pace(group) or leader.best_lap_ms or 0

    # class gap (same-lap) + laps down
    for ca in group_sorted:
        g = feed_gap.get(ca.car_number)
        ca.class_gap_ms = (g - leader_gap) if (g is not None) else None
        # The integer lap count flickers ±1 at S/F crossings during pit cycles (the
        # un-pitted leader laps-ahead illusion). Trust TIME: a car is only genuinely a
        # lap down if its cumulative-time gap to the class leader exceeds a lap.
        raw_down = max(0, leader_laps - (ca.laps or 0))
        if raw_down > 0 and lap_ref_ms and ca.class_gap_ms is not None \
                and 0 <= ca.class_gap_ms < lap_ref_ms:
            raw_down = 0
        ca.laps_down = raw_down
        if raw_down > 0 and lap_ref_ms:
            # genuinely lapped: the cumulative-time difference isn't a same-lap gap
            # (it compares different lap counts) — express it as the lap deficit so
            # net ordering and the "+NL" display stay sane.
            ca.class_gap_ms = raw_down * lap_ref_ms

    _assign_effective_positions(group_sorted)

    # record lap-aligned gaps for the catching trend gate (green only — caution
    # bunching must never count as a car "closing")
    if ctx.flag in GREEN_FLAGS:
        for ca in group_sorted:
            _sample_gap(ctx.session_oid, ca.car_number, ca.laps, ca.class_gap_ms)

    # stint / remaining-stops estimate
    avg_stint = (observed_stint or DEFAULT_STINT_LAPS.get(cls, DEFAULT_STINT_FALLBACK))
    avg_green_lap = _median_pace(group) or (leader.best_lap_ms or 0)
    model = ctx.pit_model
    for ca in group_sorted:
        ca.est_stops_left = _stops_left(ctx, ca, avg_stint, avg_green_lap)
        # predicted cost of this car's NEXT stop (full-length stint, owed change if due)
        ca.next_stop_ms, ca.next_stop_std_ms, ca.pit_scope = model.predict_stop(
            ca.car_number, cls, avg_stint, ca.owes_driver_change)
        # fuel / pit window: laps left in the tank, and the session lap it must pit by
        if ca.stint_laps is not None and avg_stint > 0:
            ca.fuel_laps_left = max(0, int(round(avg_stint - ca.stint_laps)))
            if ca.laps is not None:
                ca.must_pit_lap = ca.laps + ca.fuel_laps_left
            ca.pit_window_open = (ca.est_stops_left > 0 and
                                  ca.fuel_laps_left <= PIT_WINDOW_LAPS)
            # pit-due from the trustworthy stint estimate (NOT VFT): basically out of
            # fuel. Drives the rail's "DUE TO PIT" roster, never an on-row highlight.
            if ca.pit_window_open and ca.fuel_laps_left <= 1:
                ca.fuel_due = "due"

    # sector deltas vs the class-best sector (who's losing time where)
    for s in (1, 2, 3):
        vals = [getattr(c, f"best_s{s}_ms") for c in group_sorted
                if getattr(c, f"best_s{s}_ms")]
        cbest = min(vals) if vals else None
        for ca in group_sorted:
            mine = getattr(ca, f"best_s{s}_ms")
            d = (mine - cbest) if (mine and cbest) else None
            ca.sec_delta_ms = ca.sec_delta_ms[:s-1] + (d,) + ca.sec_delta_ms[s:]

    # ── net position: each car's remaining stops costed by its OWN predicted stop
    #    time (fuel + driver change), with a ± band from the prediction spread.
    #    Future stops are green; cheap under-yellow stops already taken are already
    #    baked into the real gap. ──
    def future_pit(ca: CarAnalysis) -> tuple[float, float]:
        # "track" pit model (F1 v1): tyre-only stops, no refuelling — the fuel-fill
        # regression doesn't apply, so net position collapses to the real running
        # order (situational). Pending time penalties still shift net_gap below.
        if ctx.profile.pit_model == "track":
            return 0.0, 0.0
        n = ca.est_stops_left
        if n <= 0:
            return 0.0, 0.0
        # only the soonest stop carries the owed driver-change increment
        per, std, _ = model.predict_stop(ca.car_number, cls, avg_stint, False)
        total = n * per
        if ca.owes_driver_change:
            total += model.dc_delta_ms
        return total, math.sqrt(n) * std            # independent stops → variance adds
    lead_future, _ = future_pit(leader)
    for ca in group_sorted:
        if ca.class_gap_ms is None:
            ca.net_gap_ms = None
            continue
        fut, band = future_pit(ca)
        # pending in-race penalties (drive-through / stop-go) will cost time soon → net
        ca.net_gap_ms = ca.class_gap_ms + fut - lead_future + ca.penalty_s * 1000
        ca.net_gap_band_ms = band
    # DQ'd cars drop to the back of the class on net order
    net_order = sorted(
        group_sorted,
        key=lambda c: (1 if c.dq else 0, c.laps_down,
                       c.net_gap_ms if c.net_gap_ms is not None else 9e12))
    for i, ca in enumerate(net_order, 1):
        ca.net_position = i
    # ── settled: once every car in class has taken its final stop, the pit model
    #    has nothing left to say and net collapses to track order — displays dim it
    #    (decisions log 07-04). A pending penalty keeps that car's net live: the
    #    served/added time is still coming. ──
    cls_settled = all(ca.est_stops_left <= 0 for ca in group_sorted if not ca.dq)
    for ca in group_sorted:
        ca.net_settled = cls_settled and ca.penalty_s == 0 and not ca.dq

    # ── pit-now projection: cost = this car's predicted stop, scaled cheap if we're
    #    currently under caution (field bunched) ──
    cur_order = sorted(group_sorted,
                       key=lambda c: (c.laps_down, c.class_gap_ms if c.class_gap_ms is not None else 9e12))
    for ca in cur_order:
        if ca.class_gap_ms is None:
            continue
        pit_pen = (ca.next_stop_ms or ctx.green_typical_ms)
        if ctx.under_caution:
            # VSC preserves gaps (cars slow ~40%); pitting is cheaper than green
            # but not as cheap as a full SC where the field bunches completely.
            if ctx.flag in VSC_FLAGS:
                pit_pen *= (1.0 + CAUTION_PENALTY_FACTOR) / 2
            else:
                pit_pen *= CAUTION_PENALTY_FACTOR
        new_gap = ca.class_gap_ms + pit_pen
        ahead = [o for o in cur_order
                 if o is not ca and o.class_gap_ms is not None
                 and (o.laps_down < ca.laps_down or
                      (o.laps_down == ca.laps_down and o.class_gap_ms < new_gap))]
        ca.pit_now_position = len(ahead) + 1
        ca.pit_now_among = [o.car_number for o in cur_order
                            if o is not ca and o.class_gap_ms is not None
                            and abs(o.class_gap_ms - new_gap) <= 3_000][:3]

    # ── catch & pass: each car vs the car ahead in current class order ──
    for i in range(1, len(cur_order)):
        ca, ahead = cur_order[i], cur_order[i - 1]
        if (ca.avg_pace_ms and ahead.avg_pace_ms and ca.class_gap_ms is not None
                and ahead.class_gap_ms is not None and ca.laps_down == ahead.laps_down):
            pace_delta = ahead.avg_pace_ms - ca.avg_pace_ms   # >0 → chaser faster
            time_gap = ca.class_gap_ms - ahead.class_gap_ms
            # a gap never closes at the full raw pace delta — traffic, dirty air on
            # the final approach, and the leader responding all bleed it off. Scale
            # the closing rate by an efficiency factor so catch ETAs aren't optimistic.
            close_rate = pace_delta * CATCH_CLOSING_EFFICIENCY
            # Only call it "catching" when the chaser is genuinely on the car's gearbox
            # (within CATCH_GAP_S) AND the gap has actually been coming down for the last
            # CATCH_TREND_LAPS green laps — kills the steady-state / one-fast-lap noise.
            if (close_rate > 0 and 0 < time_gap <= CATCH_GAP_S * 1000
                    and _gap_closing(ctx.session_oid, ca.car_number, ahead.car_number,
                                     ctx.current_lap, int(CATCH_TREND_LAPS))):
                laps = time_gap / close_rate
                if laps <= CATCH_MAX_LAPS:
                    ca.catching = ahead.car_number
                    ca.catch_in_laps = laps

    # ── undercut / overcut + strategy note ──
    # An undercut/overcut is only a real, actionable call if BOTH cars still have a
    # stop left to leverage AND the pivotal stop is near (one of them in its pit
    # window). Without these gates this fires on any stint-length difference between
    # adjacent cars — flooding the CALL column with steady-state noise (e.g. at race
    # end, when est_stops_left == 0 and no stop can change anything).
    for i in range(1, len(cur_order)):
        ca, ahead = cur_order[i], cur_order[i - 1]
        if ca.class_gap_ms is None or ahead.class_gap_ms is None:
            continue
        if ca.est_stops_left <= 0 or ahead.est_stops_left <= 0:
            continue
        if not (ca.pit_window_open or ahead.pit_window_open):
            continue
        gap_to_ahead = ca.class_gap_ms - ahead.class_gap_ms
        if ca.laps_down != ahead.laps_down or gap_to_ahead > UNDERCUT_WINDOW_MS:
            continue
        a_stint = ahead.stint_laps if ahead.stint_laps is not None else -1
        c_stint = ca.stint_laps if ca.stint_laps is not None else -1
        if a_stint >= 0 and c_stint >= 0:
            if a_stint > c_stint + 2:
                ca.strategy_note = f"undercut #{ahead.car_number} (it pits sooner)"
            elif c_stint > a_stint + 2:
                ca.strategy_note = f"overcut #{ahead.car_number} (more fuel in hand)"

    # ── projected finish: TRACK-ANCHORED blend ──
    # Validated across 6 complete races (validate_races.py): the running order is
    # the strongest simple finish predictor; net only adds signal when stops remain
    # to cycle (long/early races). A pure net-gap projection was WORSE than track
    # (pooled MAE 2.80 vs 2.71); this blend lands at 2.69. Lean on net in proportion
    # to a car's remaining stops (capped), else it's essentially the running order.
    # Post-race penalties nudge a car back (~1 spot / 30s); DQ drops to class-back.
    def finish_score(ca: CarAnalysis) -> float:
        net = ca.net_position if ca.net_position is not None else (ca.pos_in_class or 99)
        trk = ca.pos_in_class if ca.pos_in_class is not None else net
        w = min(0.6, 0.15 * (ca.est_stops_left or 0))   # more stops left → trust net more
        return w * net + (1 - w) * trk + ca.penalty_post_s / 30.0
    fin_order = sorted(group_sorted,
                       key=lambda c: (1 if c.dq else 0, c.laps_down, finish_score(c)))
    for i, ca in enumerate(fin_order, 1):
        ca.projected_finish = i


def _median_pace(group: list[CarAnalysis]) -> Optional[float]:
    paces = sorted(c.avg_pace_ms for c in group if c.avg_pace_ms)
    if not paces:
        return None
    n = len(paces)
    return paces[n // 2] if n % 2 else (paces[n // 2 - 1] + paces[n // 2]) / 2


def _stops_left(ctx: RaceContext, ca: CarAnalysis,
                avg_stint_laps: float, avg_lap_ms: float) -> int:
    """Estimate remaining stops from race time left, fuel in hand, and stint length."""
    if not ctx.is_race or avg_stint_laps <= 0 or not avg_lap_ms:
        return 0
    if ctx.final_type == "BY_TIME":
        laps_remaining = ctx.remaining_s * 1000 / avg_lap_ms
    else:
        laps_remaining = max(0, ctx.leader_laps - (ca.laps or 0))
    fuel_in_hand = avg_stint_laps - (ca.stint_laps if ca.stint_laps is not None else avg_stint_laps)
    needed = laps_remaining - max(0.0, fuel_in_hand)
    if needed <= 0:
        return 0
    return int(math.ceil(needed / avg_stint_laps))


# ── presentation ────────────────────────────────────────────────────────────
def _print(ctx: RaceContext, cars: list[CarAnalysis]) -> None:
    flag_txt = {"GF": "🟢 GREEN", "YF": "🟡 YELLOW", "FCY": "🟡 FCY",
                "SC": "🚗 SC", "RF": "🔴 RED", "CH": "🏁 CHK"}.get(ctx.flag, ctx.flag)
    mins = lambda s: f"{int(s // 60)}:{int(s % 60):02d}"
    print(f"\n{'═'*108}")
    head = f"  {ctx.event}  |  {ctx.session_name}  |  {flag_txt}  |  Lap {ctx.current_lap}"
    if ctx.is_race and ctx.final_type == "BY_TIME":
        head += f"  |  {mins(ctx.remaining_s)} remaining"
    head += (f"  |  green stop≈{ctx.green_typical_ms/1000:.0f}s  "
             f"transit≈{ctx.pit_model.transit_ms/1000:.0f}s  "
             f"DC+{ctx.pit_model.dc_delta_ms/1000:.0f}s")
    print(head)
    print(f"{'═'*108}")

    if not ctx.is_race:
        print("  (Not a race session — net position / strategy metrics apply to races only.)")

    by_class: dict[str, list[CarAnalysis]] = {}
    for c in cars:
        by_class.setdefault(c.car_class, []).append(c)

    for cls, group in by_class.items():
        group.sort(key=lambda c: (c.net_position or c.pos_in_class or 99))
        print(f"\n  ── {cls} ──")
        print(f"  {'NET':>3} {'(TRK)':>5}  {'CAR':>4}  {'DRIVER':<18}  {'NETGAP':>12}  "
              f"{'PACE':>8}  {'STINT':>5}  {'STOPS':>5}  {'NEXT':>6}  {'CATCH':>13}  "
              f"{'PROJ':>4}  STRATEGY")
        for c in group:
            net = str(c.net_position or "—")
            trk = f"({c.track_position})" if c.track_position else "—"
            if c.net_position == 1:
                gap = "LEADER"
            elif c.laps_down:
                gap = f"{c.laps_down}L"
            elif c.net_gap_ms is not None:
                band = f"±{c.net_gap_band_ms/1000:.0f}" if c.net_gap_band_ms else ""
                gap = f"+{c.net_gap_ms/1000:.1f}{band}"
            else:
                gap = "—"
            pace = _ms_to_laptime(int(c.avg_pace_ms)) if c.avg_pace_ms else "—"
            stint = str(c.stint_laps) if c.stint_laps is not None else "—"
            stops = f"+{c.est_stops_left}" if c.est_stops_left else "0"
            nxt = f"{c.next_stop_ms/1000:.0f}s" if c.next_stop_ms else "—"
            if c.catching and c.catch_in_laps is not None:
                catch = f"#{c.catching} {c.catch_in_laps:.0f}L"
            else:
                catch = "—"
            note = c.strategy_note
            if c.owes_driver_change:
                note = (note + "  " if note else "") + "owes DC"
            if c.pit_now_position:
                note = (note + "  " if note else "") + f"[pit→P{c.pit_now_position}]"
            print(f"  {net:>3} {trk:>5}  {c.car_number:>4}  {(c.driver or c.team or '?')[:18]:<18}  "
                  f"{gap:>12}  {pace:>8}  {stint:>5}  {stops:>5}  {nxt:>6}  {catch:>13}  "
                  f"{str(c.projected_finish or '—'):>4}  {note}")
    print()


# ── entrypoint ──────────────────────────────────────────────────────────────
def latest_session(conn: sqlite3.Connection, series: Optional[str] = None) -> Optional[str]:
    # Prefer the session whose status was most recently written (updated every frame);
    # fall back to sessions.last_seen for sessions that never got a status row.
    # `series` scopes the pick so an F1 and an IMSA session in the same DB don't
    # collide on "most recent" (None = any series, the legacy behaviour).
    if series:
        row = conn.execute("""
            SELECT s.session_oid
            FROM sessions s
            LEFT JOIN session_status ss ON ss.session_oid = s.session_oid
            WHERE COALESCE(s.series, 'imsa') = ?
            ORDER BY COALESCE(ss.updated_at, s.last_seen) DESC
            LIMIT 1""", (series,)).fetchone()
    else:
        row = conn.execute("""
            SELECT s.session_oid
            FROM sessions s
            LEFT JOIN session_status ss ON ss.session_oid = s.session_oid
            ORDER BY COALESCE(ss.updated_at, s.last_seen) DESC
            LIMIT 1""").fetchone()
    return row[0] if row else None


def session_series(conn: sqlite3.Connection, oid: str) -> str:
    """The series string for a session ('imsa' when unset). Used to resolve the
    SeriesProfile that routes class/palette/strategy behaviour for this session."""
    try:
        row = conn.execute(
            "SELECT series FROM sessions WHERE session_oid=?", (oid,)).fetchone()
    except sqlite3.Error:
        return "imsa"
    return (row[0] if row and row[0] else "imsa")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--session", default=None, help="session OID (default: most recent)")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"No database at {args.db} — run the scraper first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    oid = args.session or latest_session(conn)
    if not oid:
        print("No sessions in database yet.", file=sys.stderr)
        sys.exit(1)

    ctx, cars = analyse(conn, oid)
    _print(ctx, cars)
    conn.close()


if __name__ == "__main__":
    main()
