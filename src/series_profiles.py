"""
series_profiles.py — per-series identity so one codebase serves multiple racing
series (IMSA, WEC, and future additions).

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
    hot-reloadable via config.json.

Design intent: the IMSA profile holds the CANONICAL palette/class values that
dashboard.py / dashboard_calm.py re-export, so extracting them here is provably
byte-identical (regression-gated by validate_races.py + headless render). WEC
is next: add a profile when the adapter lands.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SeriesProfile:
    key: str                       # matches sessions.series ("imsa" | "wec" | …)
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
    #            (endurance series: IMSA, WEC). "track" → net-position collapses
    #            to track position (non-refuel series — no current users).
    pit_model: str = "refuel"
    # identity: how a car is keyed/labelled. "car_class" = number + class (IMSA/WEC).
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

WEC = SeriesProfile(
    key="wec",
    display_name="FIA WEC",
    classes=("HYPERCAR", "LMGT3"),
    class_order={"HYPERCAR": 0, "LMGT3": 1},
    class_colors={
        "HYPERCAR": "#E8102E",
        "LMGT3":    "#00A651",
    },
    spine={"HYPERCAR": "#F01744", "LMGT3": "#1EC96A"},
    spine_default="#5F6B7A",
    pit_model="refuel",
    identity="car_class",
    rc_dialect="wec",
)

PROFILES = {p.key: p for p in (IMSA, WEC)}

DEFAULT_SERIES = "imsa"


def get_profile(series: Optional[str]) -> SeriesProfile:
    """Resolve a series string to its profile, defaulting to IMSA for unknown
    or missing values (so legacy single-series sessions Just Work)."""
    return PROFILES.get((series or DEFAULT_SERIES).lower(), IMSA)
