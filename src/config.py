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
    "BUDGET_PER_CLASS":       1,        # max NET-overlay highlights allowed per class on the calm board (0 = monochrome)
    "TRACK_LAT":              42.337,   # circuit latitude  (Watkins Glen — first race)
    "TRACK_LON":             -76.927,   # circuit longitude (edit per round for weather)
    "DEV_SHOW_ALL_SESSIONS":  False,    # picker shows Race sessions only; True re-exposes all session types for debugging
    "ARCHIVE_DIR":            "~/Downloads",  # where IMSA Timing71 replay zips live (validate_races.py; ~ expanded)
    # per-series knob overrides, applied on top of the base values when the
    # active session's series matches: {"wec": {"DRIVER_CHANGE_DELTA_MS": 45000}}.
    # Lets race-day tuning for one series (WEC driver changes run far longer than
    # IMSA's) leave the other series' calibration untouched. Hot-reloads like
    # every other knob; unknown keys inside an override are ignored.
    "SERIES_OVERRIDES":       {},
}


class _Config:
    def __init__(self):
        self._vals = dict(DEFAULTS)
        self._mtime = None
        self._load()

    def _load(self):
        try:
            if CONFIG_PATH.exists():
                data = json.loads(CONFIG_PATH.read_text())
                merged = dict(DEFAULTS)
                merged.update({k: v for k, v in data.items() if k in DEFAULTS})
                self._vals = merged
            else:
                self._vals = dict(DEFAULTS)
        except Exception:
            # malformed JSON mid-edit → keep whatever we had (last good / defaults)
            pass

    def reload_if_changed(self):
        """Cheap mtime check; reloads only when config.json actually changes."""
        try:
            m = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else None
        except OSError:
            m = None
        if m != self._mtime:
            self._mtime = m
            self._load()

    def as_dict(self, series: str = None) -> dict:
        """Base knob values, optionally with the given series' overrides applied
        (only keys that exist in DEFAULTS are honoured — a typo in an override
        can't inject a new global)."""
        out = dict(self._vals)
        if series:
            ov = (self._vals.get("SERIES_OVERRIDES") or {}).get(series) or {}
            out.update({k: v for k, v in ov.items() if k in DEFAULTS})
        return out

    def __getattr__(self, key):
        # only reached for names not found normally; _vals is a real instance attr
        try:
            return self._vals[key]
        except KeyError as e:
            raise AttributeError(key) from e


CONFIG = _Config()
