"""
series_profiles.py — per-series identity so one codebase serves multiple racing
series (IMSA, and — added incrementally — F1, WEC, IndyCar).

The app was born single-series (IMSA/Al-Kamel). Everything that is genuinely
DIFFERENT between series used to be hard-coded in the render and calculator
layers: the class taxonomy + colours, whether a series is multi-class at all,
whether pit strategy is fuel-driven, and which race-control/penalty wording to
parse. A `SeriesProfile` collects exactly those divergent knobs behind one
object, keyed by a short series string that also lives on `sessions.series`.

What is NOT here on purpose:
  - Flag styling (FLAG_STYLE in dashboard.py) is already series-neutral — the
    same canonical codes (GF/YF/FCY/SC/VSC/RF/CH) serve every series; a new
    source just maps its flags onto them.
  - Stint-length priors (DEFAULT_STINT_LAPS) stay in config.py so they remain
    hot-reloadable via config.json. F1 doesn't use them (net = track, no fuel
    model), so there's nothing to move.

Design intent: the IMSA profile holds the CANONICAL palette/class values that
dashboard.py / dashboard_calm.py re-export, so extracting them here is provably
byte-identical (regression-gated by validate_races.py + headless render). F1 is
built and validated against this seam; WEC/IndyCar are designed-not-built (add a
profile when their adapter lands).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SeriesProfile:
    key: str                       # matches sessions.series ("imsa" | "f1" | …)
    display_name: str

    # ── class taxonomy ──────────────────────────────────────────────────────
    # Ordered class codes (leaderboard grouping order). Single-class series list
    # one code; the board suppresses the redundant class header for them.
    classes: tuple = ()
    class_order: dict = field(default_factory=dict)   # code → sort index
    class_colors: dict = field(default_factory=dict)  # code → hex (dense table)
    spine: dict = field(default_factory=dict)         # code → hex (calm spine)
    spine_default: str = "#5F6B7A"

    @property
    def single_class(self) -> bool:
        return len(self.classes) <= 1

    # ── strategy / parsing selectors ────────────────────────────────────────
    # pit_model: "refuel" → the fuel-fill regression + driver-change model
    #            (IMSA/WEC/IndyCar); "track" → net-position collapses to track
    #            position (F1 v1: tyre-only stops, no refuel — situational only).
    pit_model: str = "refuel"
    # identity: how a car is keyed/labelled. "car_class" = number + class (IMSA);
    #           "driver" = driver number + TLA + team colour (F1).
    identity: str = "car_class"
    # rc_dialect: which race-control / penalty wording parser to use.
    rc_dialect: str = "imsa"

    def spine_of(self, cls: Optional[str]) -> str:
        return self.spine.get(cls, self.spine_default)


# ── IMSA — the canonical values other modules re-export (byte-identical) ──────
IMSA = SeriesProfile(
    key="imsa",
    display_name="IMSA WeatherTech",
    classes=("GTP", "LMP2", "GTDPRO", "GTD"),
    class_order={"GTP": 0, "LMP2": 1, "GTDPRO": 2, "GTD": 3},
    class_colors={
        "GTP":    "#D0103A",
        "LMP2":   "#1E5BD6",
        "GTDPRO": "#E07A00",
        "GTD":    "#1FA14E",
    },
    # brighter class spines for the calm screen (a touch more pop than the table)
    spine={"GTP": "#F01744", "LMP2": "#2E6FF0", "GTDPRO": "#F08A1E", "GTD": "#23A65A"},
    spine_default="#5F6B7A",
    pit_model="refuel",
    identity="car_class",
    rc_dialect="imsa",
)


# ── F1 — single class, tyre-only stops, driver identity ──────────────────────
# v1 is situational (net = track); tyre-strategy pit model is a later iteration.
# Team colours/TLAs come from data/f1_*.json at render time (Phase 2), so the
# class palette here is just a neutral single-class placeholder — the redundant
# class header is suppressed for single_class series anyway.
F1 = SeriesProfile(
    key="f1",
    display_name="Formula 1",
    classes=("F1",),
    class_order={"F1": 0},
    class_colors={"F1": "#E8EDF3"},
    spine={"F1": "#5F6B7A"},
    spine_default="#5F6B7A",
    pit_model="track",
    identity="driver",
    rc_dialect="f1",
)


# ── IndyCar — single class, refuel + tyre stops, driver identity ─────────────
# Unlike F1, IndyCar cars are refuelled during pit stops (like IMSA), so the
# fuel-fill/pit-cost regression applies (pit_model="refuel") even though no
# fuel-percentage telemetry exists — PitCostModel infers cost purely from
# observed stop-duration vs stint-length, the same path IMSA's GTD class
# already runs (GTD has no VFT either). Team colours/TLAs come from
# data/indycar_*.json at render time, same pattern as F1's data/f1_*.json.
INDYCAR = SeriesProfile(
    key="indycar",
    display_name="NTT INDYCAR SERIES",
    classes=("INDYCAR",),
    class_order={"INDYCAR": 0},
    class_colors={"INDYCAR": "#C8102E"},
    spine={"INDYCAR": "#C8102E"},
    spine_default="#5F6B7A",
    pit_model="refuel",
    identity="driver",
    rc_dialect="indycar",
)


PROFILES = {p.key: p for p in (IMSA, F1, INDYCAR)}

DEFAULT_SERIES = "imsa"


def get_profile(series: Optional[str]) -> SeriesProfile:
    """Resolve a series string to its profile, defaulting to IMSA for unknown
    or missing values (so legacy single-series sessions Just Work)."""
    return PROFILES.get((series or DEFAULT_SERIES).lower(), IMSA)
