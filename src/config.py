"""
config.py — hot-reloadable tuning parameters.

The calculator's knobs live here, mirrored to an editable config.json at the
project root. The running app re-reads the file every analysis cycle, so editing
a value during a race takes effect within ~2s — no restart. Bad/missing JSON
falls back to the defaults below (never crashes the live screen).

Only PARAMETERS are hot-reloadable. Changing actual logic still needs a restart
(cheap + lossless — the DB persists all state and the scraper re-seeds on
reconnect).
"""

import json
import copy
import logging
import math
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# canonical defaults — also the schema (only these keys are accepted from JSON)
DEFAULTS = {
    "PACE_WINDOW":            5,        # laps in the rolling pace average
    "PACE_OUTLIER_FACTOR":    1.07,     # drop laps slower than best*this (in/out, yellow, traffic)
    "CAUTION_PENALTY_FACTOR": 0.35,     # a stop under yellow costs this fraction of a green stop
    "DEFAULT_GREEN_PIT_MS":   35000,    # fallback per-stop loss before any stop observed
    "DEFAULT_STOP_STD_MS":    6000,     # fallback stop-time spread
    "DRIVER_CHANGE_DELTA_MS": 12000,    # fallback extra time a driver-change stop adds
    "UNDERCUT_WINDOW_MS":     25000,    # max class gap over which an undercut is realistic
    "CATCH_MAX_LAPS":         200,      # don't report catches further off than this
    "CATCH_CLOSING_EFFICIENCY": 0.75,   # fraction of raw pace delta that actually closes a gap (traffic/dirty air/leader response)
    "CATCH_GAP_S":            2.0,      # only flag "catching" when within this in-class gap (s)
    "CATCH_TREND_LAPS":       3,        # …and the gap has been closing for this many green laps
    "BATTLE_GAP_S":           2.0,      # in-class gap (s) at/under which a pair counts as a battle to watch
    "BATTLE_TREND_LAPS":      3,        # BATTLES-rail-only closing gate: green laps required (looser than CATCH_TREND_LAPS)
    "BATTLE_MIN_DROP_MS":     80,       # …minimum net gap drop over that window to call it "catching"
    "BATTLE_NOISE_TOL_MS":    120,      # …per-lap noise tolerance (vs the main board's stricter 50ms)
    "MIN_FIT_POINTS":         3,        # min stops before trusting a fuel-fill regression
    "STOP_OUTLIER_MAD":       4.0,      # reject stop durations beyond median + this·(robust σ) before fitting (garage/repair stops)
    "DC_NEAR_LAPS":           2,        # a stop is a driver-change stop if a change is within ±this
    "DEFAULT_STINT_FALLBACK": 30,       # fallback green-stint length (laps)
    "DEFAULT_STINT_LAPS": {             # per-class green-stint priors (laps)
        "GTP": 28, "LMP2": 30, "GTD": 32, "GTDPRO": 32,
        # WEC priors (Interlagos-ish; self-correct once a real stint is
        # observed — tune live via config.json during FP/race)
        "HYPERCAR": 33, "LMGT3": 30},
    "PIT_WINDOW_LAPS":        5,        # fuel-laps-left at/under which the pit window is "open"
    "VET_DUE_PCT":            5.0,      # WEC energy-tank % at/under which fuel_due lights (≈1.5 laps at a 33-lap stint; VET is WEC-only, IMSA ignores this)
    # ── WEC "DUE TO PIT" roster timing (fuel_due stint path; DISPLAY-ONLY —
    #    net/projected_finish never read fuel_due). WEC-ONLY: the stint-path DUE
    #    used to gate on the class MEAN stint, dragged below the tank by
    #    strategic/short stops, so the roster lit ~4 laps early even on genuine
    #    fuel stops (07-21 study: realized WEC fuel stints ran +2.6..+2.7 laps
    #    longer than the mean). DUE now references the demonstrated fuel RANGE
    #    (per-car longest clean green stint). IMSA keeps the mean gate — its
    #    stint length is strategy-variable with no reliable fuel telemetry, so no
    #    reference calibrates its tail (07-21 dead-end). See calculator._derive_class. ──
    "DUE_MARGIN_LAPS":        1,        # fire DUE when (fuel_ref − stint_laps) <= this
    "DUE_REF_SLACK_LAPS":     1,        # additive lift on the class-mean fallback (cars w/o a completed clean stint yet)
    "DUE_REF_CAP_LAPS":       6,        # cap the per-car range at class_mean + this (one freak long stint can't mute DUE)
    "FINISH_BLEND_MAX_W":     0.3,      # projected-finish blend: cap on net's weight
    "FINISH_BLEND_W_PER_STOP": 0.08,    # …net weight gained per estimated remaining stop
                                        # (halved 07-13: on honest post-official-rank data
                                        # the old 0.6/0.15 was the worst of the swept
                                        # weights — mean projMAE 2.82 vs 2.73 at 0.3/0.08,
                                        # which keeps the deep-pit-cycle wins: Daytona 24h,
                                        # Lone Star, Imola)
    "STOPS_LEFT_SLACK":       0.5,      # stint fraction a car can stretch to absorb a
                                        # fractional remaining-stop remainder (0 = plain ceil;
                                        # 0.5 ≈ round — swept 0/0.25/0.5 across 14 races 07-12)
    "PENDING_STOP_WINDOW_LAPS": 2,      # stint-laps at/under which a just-taken stop may still
                                        # be un-charged in the cumulative gap (post-stop handoff,
                                        # BACKLOG 07-18/07-19)
    "PENDING_STOP_CHARGE_FRACTION": 0.5,  # a lap carrying at least this fraction of the predicted
                                          # stop cost over clean pace means the stop's time loss
                                          # has reached the gap (additive, so it adapts to any
                                          # lap-length/stop-cost ratio)
    "BUDGET_PER_CLASS":       1,        # max NET-overlay highlights allowed per class on the calm board (0 = monochrome)
    "TRACK_LAT":              42.337,   # circuit latitude  (Watkins Glen — first race)
    "TRACK_LON":             -76.927,   # circuit longitude (edit per round for weather)
    "DEV_SHOW_ALL_SESSIONS":  False,    # picker shows Race sessions only; True re-exposes all session types for debugging
    "ARCHIVE_DIR":            "IMSA Archives",  # where IMSA Timing71 replay zips live (validate_races.py; ~ expanded, relative = repo root)
    # per-series knob overrides, applied on top of the base values when the
    # active session's series matches: {"wec": {"DRIVER_CHANGE_DELTA_MS": 45000}}.
    # Lets race-day tuning for one series (WEC driver changes run far longer than
    # IMSA's) leave the other series' calibration untouched. Hot-reloads like
    # every other knob; unknown keys inside an override are ignored.
    # WEC catch gate tightened 07-12 (calibration): in multiclass WEC traffic the
    # IMSA-tuned gate fired on pace noise — 0 of 14 SP catch calls were real.
    # Longer pace window + longer closing trend, swept across the 7 WEC archives
    # + SP capture: pooled horizon hit-rate 56%→62% on ~10% fewer calls, median
    # lateness collapsed (Fuji 1.7→0.1, Imola 8.6→3.3 laps); SP noise calls 14→3.
    "SERIES_OVERRIDES":       {"wec": {"CATCH_TREND_LAPS": 5, "PACE_WINDOW": 8}},
}


def validate(data, nested=False):
    if not isinstance(data, dict):
        raise ValueError("configuration must be an object")
    for key, value in data.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown configuration key: {key}")
        default = DEFAULTS[key]
        if key == "SERIES_OVERRIDES":
            if nested or not isinstance(value, dict):
                raise ValueError("SERIES_OVERRIDES must map series to objects")
            for series, overrides in value.items():
                if series not in ("imsa", "wec"):
                    raise ValueError(f"unknown series: {series}")
                validate(overrides, nested=True)
        elif key == "DEFAULT_STINT_LAPS":
            if not isinstance(value, dict) or not value or any(
                    not isinstance(k, str) or type(v) not in (int, float)
                    or not math.isfinite(v) or v <= 0 for k, v in value.items()):
                raise ValueError("DEFAULT_STINT_LAPS needs positive finite lap counts")
        elif isinstance(default, bool):
            if type(value) is not bool:
                raise ValueError(f"{key} must be boolean")
        elif isinstance(default, str):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a nonempty string")
        else:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{key} must be finite numeric")
            if isinstance(default, int) and type(value) is not int:
                raise ValueError(f"{key} must be an integer")
            if key == "TRACK_LAT":
                lo, hi = -90, 90
            elif key == "TRACK_LON":
                lo, hi = -180, 180
            elif key in ("CAUTION_PENALTY_FACTOR", "CATCH_CLOSING_EFFICIENCY",
                         "FINISH_BLEND_MAX_W", "FINISH_BLEND_W_PER_STOP", "STOPS_LEFT_SLACK",
                         "PENDING_STOP_CHARGE_FRACTION"):
                lo, hi = 0, 1
            elif key == "VET_DUE_PCT":
                lo, hi = 0, 100
            else:
                lo, hi = 0, float("inf")
            if not lo <= value <= hi:
                raise ValueError(f"{key} out of range [{lo}, {hi}]")
            if key in ("PACE_WINDOW", "CATCH_TREND_LAPS", "BATTLE_TREND_LAPS",
                       "MIN_FIT_POINTS", "DEFAULT_STINT_FALLBACK", "DEFAULT_GREEN_PIT_MS",
                       "DEFAULT_STOP_STD_MS", "STOP_OUTLIER_MAD") and value <= 0:
                raise ValueError(f"{key} must be positive")
            if key in ("CATCH_TREND_LAPS", "BATTLE_TREND_LAPS") and value > 6:
                raise ValueError(f"{key} exceeds the six-lap history")
            if key == "PACE_OUTLIER_FACTOR" and value < 1:
                raise ValueError("PACE_OUTLIER_FACTOR must be at least 1")


class _Config:
    def __init__(self):
        self._vals = copy.deepcopy(DEFAULTS)
        self._mtime = None
        self.error = None
        self._load()

    def _load(self):
        try:
            data = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
            validate(data)
            merged = copy.deepcopy(DEFAULTS)
            merged.update(data)
            self._vals = merged
            self.error = None
            self._mtime = CONFIG_PATH.stat().st_mtime_ns if CONFIG_PATH.exists() else None
        except (OSError, ValueError, TypeError) as exc:
            self.error = str(exc)
            logging.getLogger(__name__).warning("Config rejected; retaining last good values: %s", exc)

    def reload_if_changed(self):
        try:
            m = CONFIG_PATH.stat().st_mtime_ns if CONFIG_PATH.exists() else None
        except OSError:
            m = None
        if m != self._mtime:
            self._mtime = m
            self._load()

    def as_dict(self, series: str = None) -> dict:
        out = copy.deepcopy(self._vals)
        if series:
            out.update({k: copy.deepcopy(v) for k, v in ((self._vals.get("SERIES_OVERRIDES") or {}).get(series) or {}).items() if k in DEFAULTS})
        return out

    def __getattr__(self, key):
        try:
            return self._vals[key]
        except KeyError as e:
            raise AttributeError(key) from e


CONFIG = _Config()
