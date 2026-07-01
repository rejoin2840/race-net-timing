"""
dashboard.py — IMSA net-position strategy dashboard (PyQt6).

A pit-wall style live strategy screen. Reads data/race.db (written by the scraper)
on a timer, runs calculator.analyse(), and renders:

  • flag/clock header that recolours with the race state
  • dense, class-coloured strategy table — net position is the headline, with
    track position, trend arrows, pace, stint, stops left, predicted next stop,
    catch ETA, projected finish and strategy notes
  • race-control ticker (penalties highlighted)
  • strategy-alerts panel (pit-now / undercut / imminent catches)
  • data-freshness indicator + one-click scraper start/stop

Architecture: the UI never touches the websocket. It polls the SQLite DB the
scraper writes (WAL mode → concurrent reads), so it stays responsive and decoupled.

Usage:
  python src/dashboard.py          # launches the window; use Connect to start the feed
"""

import re
import sqlite3
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QAbstractTableModel, QModelIndex, QProcess, Qt, QTimer)
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel,
                             QMainWindow, QMessageBox, QPushButton, QSpinBox,
                             QSplitter, QTableView, QTextEdit, QVBoxLayout, QWidget)

import calculator
import config
import predictor
import race_control
import series_profiles
import weather as weather_mod

# ── paths ───────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
DB_PATH   = ROOT / "data" / "race.db"
PYTHON    = ROOT / "venv" / "bin" / "python"
SCRAPER   = ROOT / "src" / "alkameldp.py"
EVALUATOR = ROOT / "src" / "evaluator.py"
EVAL_EVERY_MS = 45 * 60 * 1000     # background accuracy read cadence
WEATHER_EVERY_MS = 5 * 60 * 1000   # track-weather poll cadence

REFRESH_MS      = 2000      # DB poll / re-analyse cadence
TREND_WINDOW_S  = 300       # compare net position to this many seconds ago
TREND_MIN_AGE_S = 45        # need at least this much history before showing an arrow
STALE_AFTER_S   = 12        # data older than this → flagged stale
MAX_DELAY_S     = 120       # max broadcast-delay offset the UI can buffer

# Pit-lane presence (broad — for the in-pit indicator) vs actually stopped in the
# box (strict — for the stop timer). OUT_LAP/IN_LAP are pit-lane transit, not box
# time; counting them inflates the "In pits" clock to minutes, especially under
# yellow when the in/out laps crawl.
PIT_LANE_STATES = ("BOX", "IN_LAP", "OUT_LAP", "PIT", "STOPPED")
BOX_STATES      = ("BOX", "PIT", "STOPPED")

# ── palette ─────────────────────────────────────────────────────────────────
BG        = "#0B0E13"
PANEL     = "#12161D"
PANEL2    = "#1A2030"   # more distinct from BG for alternating rows
GRID      = "#2A3340"   # slightly lighter grid lines
TEXT      = "#E8EDF3"   # brighter primary text
TEXT_DIM  = "#8A97A8"   # dimmed but still readable
ACCENT    = "#3DDC97"
PURPLE    = "#C49FFF"   # slightly brighter purple
AMBER     = "#FFB454"
RED       = "#FF6B78"
GREEN     = "#3DDC97"

# Class palette/order now live on the IMSA SeriesProfile (single source of truth
# shared with the calm board); re-exported here so existing references are unchanged.
# Phase 2 flips the render path to read the ACTIVE session's profile dynamically.
CLASS_COLORS = dict(series_profiles.IMSA.class_colors)
# IMSA WeatherTech class hierarchy (LMP3 no longer runs in the series)
CLASS_ORDER = dict(series_profiles.IMSA.class_order)

FLAG_STYLE = {
    "GF":  ("#0B7A33", "GREEN"),
    "YF":  ("#B58900", "YELLOW"),
    "FCY": ("#B58900", "FULL-COURSE YELLOW"),
    "SC":  ("#B58900", "SAFETY CAR"),
    "VSC": ("#B58900", "VIRTUAL SC"),
    "RF":  ("#A01020", "RED FLAG"),
    "CH":  ("#444444", "CHECKERED"),
}

# Lean main table — the headline (NET / Δ / gap) plus identity and the call.
# Everything diagnostic (lap times, pace, sectors, stint, stops, catch, proj)
# moved to the double-click car-detail popup so the screen stays glanceable.
COLS = ["NET", "", "Δ", "CAR", "CLS", "DRIVER", "LAST", "BEST", "PACE", "NET GAP", "WINDOW", "STRATEGY"]

COL_TIPS = {
    "NET":      "Pit-adjusted position — where this car will run once everyone has made their remaining stops. This is the real running order.",
    "":         "Position trend: ▲ gaining, ▼ losing net position over the last 5 minutes.",
    "Δ":        "Net vs on-track gap. Green +N = car holds N hidden positions (will gain when others pit). Red −N = running N spots ahead of where it will ultimately be.",
    "CAR":      "Car number + team. Double-click a row for full detail (sectors, stint, stops, catch).",
    "CLS":      "Class: GTP · LMP2 · GTD PRO · GTD.",
    "DRIVER":   "Current driver behind the wheel.",
    "LAST":     "Last lap time.",
    "BEST":     "Personal best lap time. Purple = fastest in class.",
    "PACE":     "Recent green-lap average (last 5 laps). Amber ↑.NN = tyres going off.",
    "NET GAP":  "Time gap to the net class leader, in seconds. ± band shows 1σ uncertainty from pit-cost model. Includes any pending penalty.",
    "WINDOW":   "Fuel window: laps left in the tank at the class-average stint. OPEN = within strategic pit range now.",
    "STRATEGY": "Recommended action or situation. Double-click the row for the full picture.",
}
C_NET    = 0
C_TREND  = 1
C_DELTA  = 2
C_CLS    = 4
C_LAST   = 6
C_BEST   = 7
C_PACE   = 8
C_GAP    = 9
C_WINDOW = 10
C_STRAT  = len(COLS) - 1   # 11


def _class_color(cls: str) -> str:
    return CLASS_COLORS.get(cls, "#555B66")


def _blend(hex_a: str, hex_b: str, t: float) -> QColor:
    a, b = QColor(hex_a), QColor(hex_b)
    return QColor(int(a.red()   * (1 - t) + b.red()   * t),
                  int(a.green() * (1 - t) + b.green() * t),
                  int(a.blue()  * (1 - t) + b.blue()  * t))


# ── row view-model ──────────────────────────────────────────────────────────
@dataclass
class Row:
    is_header: bool = False
    header_label: str = ""
    car: str = ""
    team: str = ""
    cls: str = ""
    net: Optional[int] = None
    trend: int = 0                 # +1 gaining, -1 losing, 0 flat/unknown
    trk: Optional[int] = None      # on-track position WITHIN class (for the Δ vs net)
    trk_overall: Optional[int] = None  # overall on-track position (whole field)
    driver: str = ""
    net_gap: str = ""
    last: str = "—"
    best: str = "—";     best_purple: bool = False
    pace: str = "—"
    degrading: bool = False        # tyre deg significant this stint
    deg_str: str = ""              # e.g. "↑.12" lap-time loss per lap
    sectors: str = "—"             # "Δs1/Δs2/Δs3" vs class best
    sect_weak: bool = False        # a sector is notably off (colour cue)
    stint: Optional[int] = None
    window: str = "—"              # fuel laps left / OPEN
    window_open: bool = False
    stops: str = "0"
    nxt: str = "—"
    catch: str = "—"
    proj: Optional[int] = None
    strategy: str = ""
    actionable: bool = False
    in_box: bool = False
    net_leader: bool = False
    dq: bool = False
    box_s: Optional[float] = None       # seconds currently in the pit box
    just_pitted: bool = False           # stop count incremented in last 45s
    net_pos_delta: Optional[int] = None # positions gained(+)/lost(-) vs pre-pit net
    has_penalty: bool = False           # pending in-race time penalty owed
    catch_imminent: bool = False        # catching target within 3 laps


def _strategy_text(c) -> tuple[str, bool]:
    """Compose a concise strategy call + whether it's an actionable alert.

    Kept short so the column doesn't overflow; fuller context lives in the
    dedicated columns (CATCH, NEXT, etc.)."""
    lead, actionable = "", False
    note = c.strategy_note or ""
    # penalties take priority — they're the most consequential thing on screen
    if c.dq:
        return "DISQUALIFIED", True
    if c.penalty_note:
        return c.penalty_note, True
    if note.startswith("undercut #"):
        return f"Undercut #{note.split('#', 1)[1].split()[0]}", True
    if note.startswith("overcut #"):
        return f"Overcut #{note.split('#', 1)[1].split()[0]}", True
    if c.catching and c.catch_in_laps is not None and c.catch_in_laps <= 8:
        return f"Catching #{c.catching}", True
    if c.pit_window_open:
        return "In pit window", False
    if c.pit_now_position:
        return f"If pits now: P{c.pit_now_position}", False
    return "", False


def _build_rows(ctx, cars, trend_map: dict, filter_cls: Optional[str],
                poller=None) -> list[Row]:
    by_class: dict[str, list] = {}
    for c in cars:
        by_class.setdefault(c.car_class, []).append(c)
    fastest: dict[str, float] = {}
    for c in cars:
        if c.best_lap_ms:
            fastest[c.car_class] = min(fastest.get(c.car_class, 9e18), c.best_lap_ms)

    # always ordered by net position — the tool's headline. Track order is read
    # off the Δ column (track = net + Δ), no table reshuffle needed.
    key = lambda c: (c.net_position or 99)

    rows: list[Row] = []
    for cls in sorted(by_class, key=lambda k: (CLASS_ORDER.get(k, 9), k)):
        if filter_cls and cls != filter_cls:
            continue
        group = by_class[cls]
        rows.append(Row(is_header=True, cls=cls,
                        header_label=f"{cls}   ·   {len(group)} cars"))
        for c in sorted(group, key=key):
            in_box = (c.track_status or "") in PIT_LANE_STATES
            if c.net_position == 1:
                gap = "LEADER"
            elif c.laps_down:
                gap = f"+{c.laps_down} lap" + ("s" if c.laps_down > 1 else "")
            elif c.net_gap_ms is not None:
                band = (f" ±{c.net_gap_band_ms/1000:.0f}"
                        if c.net_gap_band_ms and c.net_gap_band_ms < 20_000 else "")
                gap = f"+{c.net_gap_ms/1000:.1f}s{band}"
            else:
                gap = "—"
            strat, actionable = _strategy_text(c)

            # ── live-event fields ──────────────────────────────────────────
            now = datetime.now().timestamp()
            box_s = (now - poller.box_since[c.car_number]
                     if poller and c.car_number in poller.box_since else None)
            just_pitted = bool(poller and c.car_number in poller.just_pitted_ts)
            catch_imminent = bool(c.catch_in_laps is not None and c.catch_in_laps <= 3
                                  and c.catching)

            # in-box: override strategy text with duration (flag very long stops)
            if in_box and box_s is not None:
                m, s = int(box_s) // 60, int(box_s) % 60
                strat = f"In pits  {m}:{s:02d}"
                actionable = box_s > 90   # mechanical / penalty stop

            # net-position delta since last stop (shown for 2 min after exit)
            net_pos_delta: Optional[int] = None
            if (poller and c.car_number in poller.pit_delta_ts
                    and c.car_number in poller.pit_before_net and not in_box):
                before = poller.pit_before_net[c.car_number]
                after = c.net_position or 99
                net_pos_delta = before - after   # positive = gained positions
                if net_pos_delta > 0:
                    sign = f"Gained {net_pos_delta} on stop"
                elif net_pos_delta < 0:
                    sign = f"Lost {-net_pos_delta} on stop"
                else:
                    sign = "Held position on stop"
                strat = f"{sign}  ·  {strat}" if strat else sign

            # ── sector deltas vs class best; amber only when one sector is
            # disproportionately worse than this car's own other sectors
            sd = c.sec_delta_ms
            sectors = "/".join(f"{d/1000:.1f}" if d is not None else "—" for d in sd)
            valid_sd = [d for d in sd if d is not None]
            if len(valid_sd) >= 2:
                worst = max(valid_sd)
                others = [d for d in valid_sd if d != worst]
                _sect_weak = bool(others and (worst - min(others)) > 400
                                  and worst > min(others) * 1.5)
            else:
                _sect_weak = False
            # fuel window — latched open once triggered (prevents S/F crossing churn)
            window_open = (c.pit_window_open or
                           bool(poller and c.car_number in poller.window_locked))
            if window_open:
                window = "OPEN"
            elif c.fuel_laps_left is not None:
                window = f"{c.fuel_laps_left}L"
            else:
                window = "—"
            rows.append(Row(
                car=c.car_number, team=(c.team or ""), cls=cls, net=c.net_position,
                trend=trend_map.get(c.car_number, 0),
                trk=(c.effective_pos_in_class or c.pos_in_class), trk_overall=c.track_position,
                driver=(c.driver or c.team or "?"),
                net_gap=gap,
                last=calculator._ms_to_laptime(c.last_lap_ms),
                best=calculator._ms_to_laptime(c.best_lap_ms),
                best_purple=bool(c.best_lap_ms and fastest.get(cls) == c.best_lap_ms),
                pace=calculator._ms_to_laptime(int(c.avg_pace_ms)) if c.avg_pace_ms else "—",
                degrading=(c.deg_ms_per_lap is not None and c.deg_ms_per_lap > 0),
                deg_str=(f"↑{c.deg_ms_per_lap/1000:.2f}".replace("0.", ".")
                         if c.deg_ms_per_lap else ""),
                sectors=sectors, sect_weak=_sect_weak,
                stint=c.stint_laps,
                window=window, window_open=window_open,
                stops=(f"+{c.est_stops_left}" if c.est_stops_left else "0"),
                nxt=(f"{c.next_stop_ms/1000:.0f}s" if c.next_stop_ms else "—"),
                catch=(f"#{c.catching} {c.catch_in_laps:.0f}L"
                       if c.catching and c.catch_in_laps is not None else "—"),
                proj=c.projected_finish, strategy=strat, actionable=actionable,
                in_box=in_box, net_leader=(c.net_position == 1), dq=c.dq,
                box_s=box_s, just_pitted=just_pitted, net_pos_delta=net_pos_delta,
                has_penalty=(c.penalty_s > 0 and not c.dq),
                catch_imminent=catch_imminent,
            ))
    return rows


# ── table model ─────────────────────────────────────────────────────────────
class StrategyModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[Row] = []

    def set_rows(self, rows: list[Row]) -> bool:
        """Returns True if the row structure changed (model was reset)."""
        if len(rows) == len(self.rows):
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0),
                                      self.index(len(rows) - 1, len(COLS) - 1))
            return False
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        return True

    def rowCount(self, parent=QModelIndex()):    return len(self.rows)
    def columnCount(self, parent=QModelIndex()): return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        if role == Qt.ItemDataRole.ToolTipRole:
            return COL_TIPS.get(COLS[section], "")
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        col = index.column()

        if r.is_header:
            if role == Qt.ItemDataRole.DisplayRole:
                return f"  {r.header_label}" if col == 0 else ""
            if role == Qt.ItemDataRole.BackgroundRole:
                return _blend(_class_color(r.cls), BG, 0.70)   # more class color visible
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(_class_color(r.cls)).lighter(160)
            if role == Qt.ItemDataRole.FontRole:
                f = QFont("Helvetica Neue", 13); f.setBold(True)
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
                return f
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(r, col)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(r, col)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (3, 5, C_STRAT):   # CAR, DRIVER, STRATEGY left
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.BackgroundRole:
            if col == 4:
                return QColor(_class_color(r.cls))
            # row-level tints reserved for rare/drastic states only — a full-row
            # colour for something as common as an open fuel window floods the
            # screen, so the window cue lives in the WINDOW cell instead.
            if r.dq:
                return QColor(75, 12, 18)       # red tint — DQ is drastic
            if r.has_penalty:
                return QColor(65, 42, 0)        # amber tint — penalty owed
            if r.in_box:
                return QColor(8, 18, 30)        # dark steel — car currently in pits
            if r.just_pitted and not r.in_box:
                return QColor(15, 38, 70)       # blue flash — just came out
            if col == C_STRAT and r.actionable and not r.in_box:
                return QColor("#2A2410")
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._fg(r, col)
        if role == Qt.ItemDataRole.FontRole:
            return self._font(r, col)
        return None

    def _tooltip(self, r: Row, col: int):
        """Plain-language explanation of the cell's value (not just the column)."""
        if col == C_NET:
            return ("Net position — where this car ends up once everyone has taken "
                    "their remaining pit stops. This is the real running order. "
                    "Double-click for full detail.")
        if col == C_TREND:
            return {1: "Gaining net positions vs 5 minutes ago.",
                    -1: "Losing net positions vs 5 minutes ago.",
                    0: "Net position steady over the last 5 minutes."}[r.trend]
        if col == C_DELTA:
            if r.net and r.trk:
                d = r.trk - r.net
                if d > 0:
                    return (f"Holds {d} hidden position(s): runs P{r.trk} on track now "
                            f"but nets out to P{r.net} — will gain when others pit.")
                if d < 0:
                    return (f"Running {-d} position(s) ahead of where it nets out "
                            f"(P{r.trk} on track vs P{r.net} net) — owes more pit time.")
                return "On-track and net position match — no hidden swing."
            return "Net/track delta unavailable."
        if col == C_WINDOW and r.window_open:
            return "In the pit window — can take a strategic stop now without losing net spots."
        if col == C_STRAT and r.strategy:
            return self._strategy_tip(r.strategy)
        return "Double-click for full car detail."

    @staticmethod
    def _strategy_tip(s: str) -> str:
        low = s.lower()
        if "disqualified" in low:
            return "Disqualified — dropped to the back of class."
        if "driver change due" in low:
            return "Still owes a mandatory driver change before the end of the race."
        if "post-race" in low:
            return "Carries a post-race time penalty — added to projected finish only."
        if "drive through" in low or "drive-thru" in low or "stop" in low and "go" in low:
            return "Has an in-race penalty to serve — already costed into net position."
        if "undercut" in low:
            return ("Undercut chance: the car ahead pits later, so pitting first and "
                    "running fresh tyres can jump it.")
        if "overcut" in low:
            return ("Overcut chance: staying out while a rival pits, using clear track "
                    "to build enough gap to come out ahead.")
        if "catching" in low:
            return "Closing on the car shown — see CATCH for laps until the pass."
        if "in pit window" in low:
            return "Within fuel range to take a strategic stop now."
        if "if pits now" in low:
            return "Position this car would rejoin in if it pitted this lap."
        if "in pits" in low:
            return "Currently stopped in the pits — timer counts the stop; long = trouble."
        if "on stop" in low:
            return "Net positions gained or lost across the stop just completed."
        return s

    def _display(self, r: Row, col: int):
        if r.net and r.trk:
            d = r.trk - r.net
            delta = f"+{d}" if d > 0 else (str(d) if d < 0 else "·")
        else:
            delta = "—"
        car_cell = f"#{r.car}  {r.team}" if r.team else f"#{r.car}"
        trend_cell = "■" if r.in_box else {1: "▲", -1: "▼", 0: ""}[r.trend]
        return [
            (str(r.net) if r.net else "—"),
            trend_cell,
            delta,
            car_cell, r.cls, r.driver, r.last, r.best, r.pace,
            r.net_gap, r.window, r.strategy,
        ][col]

    def _fg(self, r: Row, col: int):
        if col == C_CLS:
            return QColor("#FFFFFF")
        if r.dq:                                   # disqualified → whole row muted
            return QColor("#FF5C6C") if col == C_STRAT else QColor("#555B66")
        if col == C_TREND:
            if r.in_box:
                return QColor("#3A6080")           # muted steel — parked indicator
            return QColor(GREEN if r.trend > 0 else RED if r.trend < 0 else TEXT_DIM)
        if col == C_BEST and r.best_purple:
            return QColor(PURPLE)
        if col == C_PACE and r.degrading:
            return QColor(AMBER)
        if col == C_WINDOW and r.window_open:       # pit window open
            return QColor(ACCENT)
        if col == C_STRAT and r.actionable:
            if r.catch_imminent:
                return QColor("#FFFFFF")        # bright white — catch is NOW
            if r.in_box and r.box_s and r.box_s > 90:
                return QColor(RED)              # long stop — possible problem
            return QColor(AMBER)
        if col == C_DELTA and r.net and r.trk:
            d = r.trk - r.net
            return QColor(GREEN if d > 0 else RED if d < 0 else TEXT_DIM)
        if col == C_NET and r.net_leader:
            return QColor(ACCENT)
        return QColor(TEXT)

    def _font(self, r: Row, col: int):
        f = QFont("Menlo", 14)
        if col == C_NET:                # NET is the headline — make it stand out
            f.setBold(True); f.setPointSize(18)
        if col == 3:                    # CAR
            f.setBold(True)
        if col == C_CLS:
            f.setBold(True); f.setPointSize(12)
        if col == C_GAP:                # NET GAP — slightly larger emphasis
            f.setPointSize(15)
        if r.in_box and col == 5:       # DRIVER italic when in pits
            f.setItalic(True)
        return f


# ── running-order panel (the actual race, on-track order, grouped by class) ───
# Class-grouped to mirror the NET table on the left: same class blocks line up
# side-by-side, so you can read net order vs true track order class-for-class.
# P  = position WITHIN class on track.   INT = gap to the car ahead in the SAME class.
RUN_COLS = ["P", "CAR", "INT"]


@dataclass
class RunRow:
    is_header: bool = False
    header_label: str = ""
    cls: str = ""
    pos: int = 0                 # position within class on track
    car: str = ""
    interval: str = ""           # in-class gap to the car ahead on track
    in_box: bool = False
    just_pitted: bool = False


def _build_run_rows(cars, filter_cls: Optional[str], poller=None) -> list[RunRow]:
    """On-track running order, grouped by class to mirror the NET table.

    Within a class, cars are in true on-track order (pos_in_class) and the
    interval is the gap to the car ahead *in the same class* — the figure that
    actually drives in-class strategy, not the raw overall gap to mixed traffic."""
    by_class: dict[str, list] = {}
    for c in cars:
        if c.track_position is None:
            continue
        if filter_cls and c.car_class != filter_cls:
            continue
        by_class.setdefault(c.car_class, []).append(c)

    out: list[RunRow] = []
    for cls in sorted(by_class, key=lambda k: (CLASS_ORDER.get(k, 9), k)):
        group = by_class[cls]
        group.sort(key=lambda c: (c.pos_in_class or 99, c.track_position or 999))
        out.append(RunRow(is_header=True, cls=cls,
                          header_label=f"{cls}   ·   {len(group)} cars"))
        prev_elapsed = None
        for c in group:
            e = c.elapsed_ms
            if e is not None and prev_elapsed is not None and e >= prev_elapsed:
                gap = e - prev_elapsed
                interval = (f"+{gap/1000:.3f}" if gap < 60_000
                            else f"+{int(gap//60000)}:{gap%60000/1000:06.3f}")
            else:
                interval = "—"            # class leader, or no elapsed yet
            if e is not None:
                prev_elapsed = e
            in_box = (c.track_status or "") in PIT_LANE_STATES
            out.append(RunRow(
                pos=(c.pos_in_class or 0), car=c.car_number, cls=cls,
                interval=interval, in_box=in_box,
                just_pitted=bool(poller and c.car_number in poller.just_pitted_ts),
            ))
    return out


class RunningModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows: list[RunRow] = []

    def set_rows(self, rows: list[RunRow]) -> bool:
        """Returns True if the row structure changed (model was reset)."""
        if len(rows) == len(self.rows):
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0),
                                      self.index(len(rows) - 1, len(RUN_COLS) - 1))
            return False
        self.beginResetModel(); self.rows = rows; self.endResetModel()
        return True

    def rowCount(self, parent=QModelIndex()):    return len(self.rows)
    def columnCount(self, parent=QModelIndex()): return len(RUN_COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return RUN_COLS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        col = index.column()

        if r.is_header:
            if role == Qt.ItemDataRole.DisplayRole:
                return f"  {r.header_label}" if col == 0 else ""
            if role == Qt.ItemDataRole.BackgroundRole:
                return _blend(_class_color(r.cls), BG, 0.70)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(_class_color(r.cls)).lighter(160)
            if role == Qt.ItemDataRole.FontRole:
                f = QFont("Helvetica Neue", 13); f.setBold(True)  # match NET headers
                f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
                return f
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return [str(r.pos), r.car, r.interval][col]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) \
                if col == 1 else int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ForegroundRole:
            if r.just_pitted:
                return QColor("#6FA8FF")        # just pitted — matches main table
            if r.in_box:
                return QColor("#5A6470")
            if col == 0:
                return QColor(TEXT_DIM)
            return QColor(TEXT)
        if role == Qt.ItemDataRole.FontRole:
            f = QFont("Menlo", 14)               # match the NET table body size
            if col in (0, 1):
                f.setBold(True)
            return f
        return None


# ── data poller (DB → analysis → rows, + trend history) ─────────────────────
class Poller:
    def __init__(self, force_oid: Optional[str] = None, series: Optional[str] = None):
        # when set, always read this session instead of latest_session() — pins the
        # view to e.g. a replay 'stream' so a concurrent live scraper can't steal it
        self.force_oid = force_oid
        # when set (and force_oid isn't), scopes latest_session() to one series so an
        # F1-live session can't be pre-empted by a fresher IMSA one in the same DB
        self.series = series
        self.conn: Optional[sqlite3.Connection] = None
        self.hist: dict[str, deque] = {}
        self.buffer: deque = deque()        # (capture_ts, snapshot) for broadcast delay
        self.latest_ts: Optional[float] = None
        self.latest_age: Optional[float] = None
        # raw analyse output of the most recent fetch (for prediction logging)
        self.last_ctx = None
        self.last_cars = None
        self.last_oid: Optional[str] = None
        # live-event tracking (updated on every fetch against real-time data)
        self.box_since:     dict[str, float] = {}  # car → ts entered box
        self.prev_stops:    dict[str, int]   = {}  # car → last known stop count
        self.prev_net:      dict[str, int]   = {}  # car → net_pos from previous cycle
        self.just_pitted_ts: dict[str, float] = {} # car → ts stop count incremented
        self.pit_before_net: dict[str, int]  = {}  # net pos the cycle before the stop
        self.pit_delta_ts:  dict[str, float] = {}  # when delta was recorded (expires 2m)
        self.window_locked: set[str]         = set() # cars whose pit window is latched open

    def _connect(self):
        if self.conn is None and DB_PATH.exists():
            self.conn = sqlite3.connect(str(DB_PATH))
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA busy_timeout=5000")   # ride out scraper write locks
        return self.conn

    def trend_for(self, car: str, net: Optional[int]) -> int:
        if net is None:
            return 0
        now = datetime.now().timestamp()
        dq = self.hist.setdefault(car, deque())
        dq.append((now, net))
        while dq and now - dq[0][0] > TREND_WINDOW_S:
            dq.popleft()
        ref_ts, ref_net = dq[0]
        if now - ref_ts < TREND_MIN_AGE_S:
            return 0
        return 1 if ref_net > net else -1 if ref_net < net else 0

    def poll(self, delay_s: int = 0):
        """Fetch the latest snapshot into the buffer and return the one to display.

        With delay_s>0 we return the snapshot captured ~delay_s ago, so the whole
        screen matches a delayed broadcast (YouTube stream lag). Real feed health is
        tracked separately via latest_ts/latest_age so 'live/stale' stays honest.
        """
        snap = self._fetch()
        now = datetime.now().timestamp()
        if snap is not None:
            self.buffer.append((now, snap))
            self.latest_ts = now
            self.latest_age = snap[3]                       # real age at capture
        while self.buffer and now - self.buffer[0][0] > MAX_DELAY_S + 10:
            self.buffer.popleft()
        if not self.buffer:
            return None
        if delay_s <= 0:
            return self.buffer[-1][1]
        target = now - delay_s
        chosen = self.buffer[0][1]                          # oldest we have, if not enough history
        for ts, s in self.buffer:
            if ts <= target:
                chosen = s
            else:
                break
        return chosen

    def real_age(self) -> Optional[float]:
        """Age of the freshest data relative to now (independent of display delay)."""
        if self.latest_ts is None or self.latest_age is None:
            return None
        return self.latest_age + (datetime.now().timestamp() - self.latest_ts)

    def _fetch(self):
        """Return (ctx, rows, rc_messages, age_s) or None if no data yet.

        Tolerant by design: the DB may not exist, be mid-initialisation, or be
        momentarily locked — any of those just means 'no data yet', never a crash.
        """
        try:
            conn = self._connect()
            if conn is None:
                return None
            oid = self.force_oid or calculator.latest_session(conn, series=self.series)
            if not oid:
                return None
            ctx, cars = calculator.analyse(conn, oid)
            self.last_ctx, self.last_cars, self.last_oid = ctx, cars, oid
            now = datetime.now().timestamp()
            self._update_tracking(cars, now)
            # compute trend on the real timeline now; rows are built at display time
            trend_map = {c.car_number: self.trend_for(c.car_number, c.net_position)
                         for c in cars}
            rc = conn.execute(
                """SELECT ts, message FROM race_control WHERE session_oid=?
                     ORDER BY ts DESC, rowid DESC LIMIT 50""", (oid,)).fetchall()
            age = self._data_age(conn, oid)
            return ctx, cars, rc, age, trend_map
        except sqlite3.Error:
            # stale/empty DB handle → drop it so the next tick reconnects cleanly
            try:
                if self.conn is not None:
                    self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None
            return None

    def _update_tracking(self, cars, now: float):
        """Maintain in-box timers and just-pitted signals against live (undelayed) data."""
        for c in cars:
            car = c.car_number
            # timer only runs while genuinely stopped in the box, not during the
            # in-lap / out-lap pit-lane transit (which crawls under yellow).
            in_box = (c.track_status or "") in BOX_STATES

            # box entry / exit
            if in_box and car not in self.box_since:
                self.box_since[car] = now
            elif not in_box:
                self.box_since.pop(car, None)

            # just-pitted detection: stop count increased AND car was seen in box.
            # The feed sometimes flickers pits+1 at S/F lap registration — requiring
            # a prior box_since entry filters those phantom increments out.
            cur = c.stops or 0
            prev = self.prev_stops.get(car)
            if prev is not None and cur > prev and car in self.box_since:
                self.just_pitted_ts[car] = now
                if car in self.prev_net:
                    self.pit_before_net[car] = self.prev_net[car]
                self.pit_delta_ts[car] = now
                self.window_locked.discard(car)   # reset latch after a stop
            self.prev_stops[car] = cur
            self.prev_net[car] = c.net_position or 99

            # hysteresis: once window opens, keep it latched until next stop
            if c.pit_window_open:
                self.window_locked.add(car)

        # expire just-pitted flash after 45s; expire delta display after 2 min
        self.just_pitted_ts = {k: v for k, v in self.just_pitted_ts.items()
                               if now - v < 45}
        self.pit_delta_ts   = {k: v for k, v in self.pit_delta_ts.items()
                               if now - v < 120}

    def _data_age(self, conn, oid) -> Optional[float]:
        row = conn.execute(
            "SELECT MAX(updated_at) FROM standings_current WHERE session_oid=?",
            (oid,)).fetchone()
        if not row or not row[0]:
            return None
        try:
            ts = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - ts).total_seconds()
        except Exception:
            return None


# ── main window ─────────────────────────────────────────────────────────────
class Dashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("IMSA Strategy — Net Position")
        self.resize(1500, 880)
        self.poller = Poller()
        self.proc: Optional[QProcess] = None
        self.delay_s = 0
        self.filter_cls: Optional[str] = None
        self.write_conn: Optional[sqlite3.Connection] = None   # prediction logging
        self.last_log_ts = 0.0
        self.eval_proc: Optional[QProcess] = None              # background accuracy read
        self.autotune = False                                  # opt-in live knob tuning
        cfg = config.CONFIG.as_dict()
        self.weather_poll = weather_mod.WeatherPoll(cfg["TRACK_LAT"], cfg["TRACK_LON"])
        self.weather = weather_mod.Weather(ok=False)
        self._build_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_MS)
        self.refresh()

        self.eval_timer = QTimer(self)
        self.eval_timer.timeout.connect(self._run_eval)
        self.eval_timer.start(EVAL_EVERY_MS)

        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self._update_weather)
        self.weather_timer.start(WEATHER_EVERY_MS)
        self._update_weather()

    # ---- layout ----
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header — two rows so race info has room to breathe as it builds up:
        #   top:    FLAG  ·  event / session / lap  ·  clock
        #   bottom: cautions  ·  penalties  ·  weather (stretches, never clips)
        self.header = QFrame(); self.header.setObjectName("header")
        self.header.setFixedHeight(88)
        hv = QVBoxLayout(self.header); hv.setContentsMargins(18, 6, 18, 6); hv.setSpacing(2)

        top = QHBoxLayout(); top.setSpacing(0)
        self.flag_lbl = QLabel("—"); self.flag_lbl.setObjectName("flag")
        self.flag_lbl.setFixedWidth(240)
        self.flag_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.event_lbl = QLabel(""); self.event_lbl.setObjectName("event")
        self.clock_lbl = QLabel("--:--:--"); self.clock_lbl.setObjectName("clock")
        self.clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.flag_lbl)
        top.addSpacing(14)
        top.addWidget(self.event_lbl, 1)
        top.addWidget(self.clock_lbl)

        bot = QHBoxLayout(); bot.setSpacing(0)
        self.caution_lbl = QLabel(""); self.caution_lbl.setObjectName("caution")
        self.pen_btn = QPushButton(""); self.pen_btn.setObjectName("pen_btn")
        self.pen_btn.setFlat(True); self.pen_btn.setVisible(False)
        self.pen_btn.clicked.connect(self._focus_next_penalty)
        self._penalty_idx = 0
        self.weather_lbl = QLabel(""); self.weather_lbl.setObjectName("weather")
        self.weather_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bot.addSpacing(254)      # indent to clear the flag block
        bot.addWidget(self.caution_lbl)
        bot.addSpacing(18)
        bot.addWidget(self.pen_btn)
        bot.addStretch(1)
        bot.addWidget(self.weather_lbl)

        hv.addLayout(top, 1)
        hv.addLayout(bot)
        root.addWidget(self.header)

        # filter bar
        fbar = QFrame(); fbar.setObjectName("fbar"); fbar.setFixedHeight(44)
        fl = QHBoxLayout(fbar); fl.setContentsMargins(14, 6, 14, 6); fl.setSpacing(6)
        self.filter_btns = {}
        for name in ["ALL", "GTP", "LMP2", "GTDPRO", "GTD"]:
            b = QPushButton(name); b.setCheckable(True); b.setObjectName("chip")
            b.clicked.connect(lambda _=False, n=name: self._set_filter(n))
            fl.addWidget(b); self.filter_btns[name] = b
        self.filter_btns["ALL"].setChecked(True)

        key_btn = QPushButton("?  KEY"); key_btn.setObjectName("chip")
        key_btn.clicked.connect(self._show_legend)
        fl.addSpacing(18); fl.addWidget(key_btn)

        fl.addStretch(1)
        delay_lbl = QLabel("BROADCAST DELAY"); delay_lbl.setObjectName("dlabel")
        fl.addWidget(delay_lbl)
        self.delay_spin = QSpinBox(); self.delay_spin.setObjectName("delay")
        self.delay_spin.setRange(0, MAX_DELAY_S); self.delay_spin.setSuffix(" s")
        self.delay_spin.setSingleStep(5); self.delay_spin.setFixedWidth(78)
        self.delay_spin.valueChanged.connect(self._set_delay)
        fl.addWidget(self.delay_spin)
        fl.addSpacing(16)
        self.fresh_lbl = QLabel(""); self.fresh_lbl.setObjectName("fresh")
        fl.addWidget(self.fresh_lbl)
        root.addWidget(fbar)

        # table (class-grouped; section spans are reapplied on structural change)
        self.model = StrategyModel()
        self._span_sig: tuple = ()
        self._run_span_sig: tuple = ()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.doubleClicked.connect(self._show_car_detail)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)  # drag to resize, Excel-style
        hh.setStretchLastSection(True)                               # STRATEGY fills remainder
        hh.setHighlightSections(False)
        hh.setMinimumSectionSize(24)
        #        NET trd  Δ  CAR  CLS DRIVER LAST BEST PACE NETGAP WIN
        widths = [58, 30, 46, 130, 84, 160,  82,  82,  82,  140,  76]
        for i, w in enumerate(widths):
            self.table.setColumnWidth(i, w)
        self.table.verticalHeader().setDefaultSectionSize(36)

        # running-order panel (the actual race, on-track order) to the right
        self.run_model = RunningModel()
        self.run_table = QTableView(); self.run_table.setObjectName("runtable")
        self.run_table.setModel(self.run_model)
        self.run_table.setShowGrid(False)
        self.run_table.setAlternatingRowColors(True)
        self.run_table.verticalHeader().setVisible(False)
        self.run_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.run_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.run_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rhh = self.run_table.horizontalHeader()
        rhh.setHighlightSections(False); rhh.setStretchLastSection(True)
        for i, w in enumerate([36, 60]):
            self.run_table.setColumnWidth(i, w)
        self.run_table.verticalHeader().setDefaultSectionSize(36)  # match NET rows so blocks align
        self.run_table.setMinimumWidth(190)

        run_wrap = QFrame(); run_wrap.setObjectName("runwrap")
        rw = QVBoxLayout(run_wrap); rw.setContentsMargins(0, 0, 0, 0); rw.setSpacing(0)
        run_title = QLabel("  RUNNING ORDER · ON TRACK"); run_title.setObjectName("runtitle")
        run_title.setFixedHeight(24)
        rw.addWidget(run_title); rw.addWidget(self.run_table, 1)
        run_wrap.setMinimumWidth(200)

        # draggable split: NET strategy table (left) vs on-track running order (right).
        # min widths + non-collapsible so neither side can be dragged to nothing.
        split = QSplitter(Qt.Orientation.Horizontal); split.setObjectName("split")
        split.addWidget(self.table); split.addWidget(run_wrap)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 0)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        split.setSizes([1180, 240])
        self.table.setMinimumWidth(560)
        root.addWidget(split, 1)

        # link the two views: select a car on either side → highlight it on the other
        self._sync_guard = False
        self.table.selectionModel().selectionChanged.connect(self._on_main_selected)
        self.run_table.selectionModel().selectionChanged.connect(self._on_run_selected)

        # scroll-lock: both panels share row height + class structure, so their
        # vertical scrollbars track 1:1. Bind each to the other (Qt suppresses the
        # echo when the value is unchanged, so no feedback loop).
        self.table.verticalScrollBar().valueChanged.connect(
            self.run_table.verticalScrollBar().setValue)
        self.run_table.verticalScrollBar().valueChanged.connect(
            self.table.verticalScrollBar().setValue)

        # bottom dock: race control + strategy alerts
        bottom = QFrame(); bottom.setObjectName("bottom"); bottom.setFixedHeight(150)
        bl = QHBoxLayout(bottom); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        self.rc_panel = self._panel("RACE CONTROL")
        self.alert_panel = self._panel("STRATEGY ALERTS")
        bl.addWidget(self.rc_panel["frame"], 1)
        bl.addWidget(self.alert_panel["frame"], 1)
        root.addWidget(bottom)

        # status bar
        self.connect_btn = QPushButton("● CONNECT")
        self.connect_btn.setObjectName("connect")
        self.connect_btn.clicked.connect(self._toggle_feed)
        self.statusBar().addWidget(self.connect_btn)
        self.autotune_btn = QPushButton("⚙ AUTO-TUNE: OFF")
        self.autotune_btn.setObjectName("autotune")
        self.autotune_btn.setCheckable(True)
        self.autotune_btn.clicked.connect(self._toggle_autotune)
        self.statusBar().addWidget(self.autotune_btn)
        self.acc_lbl = QLabel("")
        self.acc_lbl.setObjectName("acc")
        self.statusBar().addPermanentWidget(self.acc_lbl)
        self.status_lbl = QLabel("idle")
        self.statusBar().addPermanentWidget(self.status_lbl)

        self.setCentralWidget(central)
        self.setStyleSheet(QSS)

    def _panel(self, title):
        frame = QFrame(); frame.setObjectName("panel")
        v = QVBoxLayout(frame); v.setContentsMargins(12, 8, 12, 8); v.setSpacing(2)
        t = QLabel(title); t.setObjectName("paneltitle")
        body = QTextEdit(); body.setObjectName("panelbody")
        body.setReadOnly(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        v.addWidget(t); v.addWidget(body, 1)
        return {"frame": frame, "body": body}

    # ---- behaviour ----
    def _set_filter(self, name):
        for n, b in self.filter_btns.items():
            b.setChecked(n == name)
        self.filter_cls = None if name == "ALL" else name
        self.refresh()

    def _show_legend(self):
        html = f"""
        <div style="font-family:Helvetica Neue; font-size:13px; color:{TEXT}">
        <b style="font-size:15px">Reading the screen</b><br><br>
        <b>NET</b> &mdash; the real running order once everyone has taken their
        remaining pit stops. <span style="color:{ACCENT}">Green = class leader.</span><br>
        <b>&#916; (net vs track)</b> &mdash;
        <span style="color:{GREEN}">green +N</span>: holds N hidden positions, will
        gain when others pit. <span style="color:{RED}">red &minus;N</span>: running
        ahead of where it nets out, owes pit time. &middot; = matched.<br>
        <b>&#9650;/&#9660;</b> &mdash; gaining / losing net position over 5 min.<br>
        <b>NET GAP</b> &mdash; time to the net class leader (&plusmn; = uncertainty).<br>
        <b>PACE</b> &mdash; recent green-lap average.
        <span style="color:{AMBER}">Amber &#8593;.NN</span> = tyres going off (sec lost per lap).<br>
        <b>SECTORS</b> &mdash; &#916; vs class-best sector.
        <span style="color:{AMBER}">Amber</span> = one sector well off this car's own pace.<br>
        <b>BEST</b> &mdash; <span style="color:{PURPLE}">purple = fastest in class.</span><br>
        <b>WINDOW</b> &mdash; fuel laps left;
        <span style="color:{ACCENT}">OPEN</span> = can take a strategic stop now.<br>
        <b>PROJ</b> &mdash; projected finishing position (includes post-race penalties).<br><br>

        <b style="font-size:15px">Strategy terms</b><br><br>
        <b>Undercut #X</b> &mdash; pit before rival X and use fresh tyres to jump them.<br>
        <b>Overcut #X</b> &mdash; stay out while X pits, build a gap on clear track.<br>
        <b>Catching #X</b> &mdash; closing on X (see CATCH for laps to the pass).<br>
        <b>In pit window</b> &mdash; within fuel range to stop now.<br>
        <b>If pits now: P#</b> &mdash; position it would rejoin in if it pitted this lap.<br>
        <b>In pits M:SS</b> &mdash; stopped now; long stop = possible trouble.<br>
        <b>Gained / Lost N on stop</b> &mdash; net positions swung by the last stop.<br><br>

        <b style="font-size:15px">Row &amp; flag colours</b><br><br>
        <span style="color:{RED}">Red row</span> = disqualified &nbsp;
        <span style="color:{AMBER}">Amber row</span> = penalty to serve &nbsp;
        <span style="color:#6FA8FF">Blue row</span> = just pitted (~45s)<br>
        Race Control: <span style="color:{RED}">red = penalty</span>,
        <span style="color:{AMBER}">amber = warning</span>.
        </div>
        """
        box = QMessageBox(self)
        box.setWindowTitle("Key — what the screen is telling you")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(html)
        box.setStyleSheet(f"QMessageBox {{ background:{PANEL}; }} "
                          f"QLabel {{ color:{TEXT}; }} "
                          f"QPushButton {{ background:{PANEL2}; color:{TEXT}; "
                          f"border:1px solid {GRID}; border-radius:4px; padding:4px 14px; }}")
        box.exec()

    def _show_car_detail(self, index):
        """Double-click a car row → full detail (everything cut from the table)."""
        if not index.isValid():
            return
        rows = self.model.rows
        if index.row() >= len(rows):
            return
        r = rows[index.row()]
        if r.is_header or not r.car:
            return

        def row(label, value, hint=""):
            h = f' <span style="color:{TEXT_DIM}">{hint}</span>' if hint else ""
            return (f'<tr><td style="color:{TEXT_DIM}; padding:2px 16px 2px 0">{label}</td>'
                    f'<td style="color:{TEXT}; font-family:Menlo">{value}{h}</td></tr>')

        trk = f"P{r.trk} in class" if r.trk else "—"
        if r.trk_overall:
            trk += f"  ·  P{r.trk_overall} overall"
        delta = ("—" if not (r.net and r.trk) else
                 (f"+{r.trk - r.net} (hidden positions to gain)" if r.trk > r.net else
                  f"{r.trk - r.net} (ahead of net)" if r.trk < r.net else "matched"))
        pace = r.pace + (f"   tyres +{r.deg_str.lstrip('↑')}s/lap" if r.deg_str else "")
        best = r.best + ("   (fastest in class)" if r.best_purple else "")
        body = "".join([
            row("Net position", f"P{r.net}" if r.net else "—"),
            row("On track", trk),
            row("Net vs track", delta),
            row("Net gap", r.net_gap),
            row("Projected finish", f"P{r.proj}" if r.proj else "—"),
            "<tr><td colspan=2><hr style='border:none;border-top:1px solid %s'></td></tr>" % GRID,
            row("Last lap", r.last),
            row("Best lap", best),
            row("Pace", pace),
            row("Sectors Δ", r.sectors, "(Δs1 / Δs2 / Δs3 vs class best)"),
            "<tr><td colspan=2><hr style='border:none;border-top:1px solid %s'></td></tr>" % GRID,
            row("Stint", f"{r.stint} laps" if r.stint is not None else "—"),
            row("Fuel window", "OPEN" if r.window_open else r.window),
            row("Stops left", r.stops),
            row("Next stop cost", r.nxt),
            row("Catching", r.catch),
            row("Call", r.strategy or "—"),
        ])
        cc = _class_color(r.cls)
        html = (f'<div style="font-family:Helvetica Neue">'
                f'<span style="font-size:17px; font-weight:700; color:{cc}">#{r.car}</span> '
                f'<span style="font-size:15px; color:{TEXT}">{r.driver}</span> '
                f'<span style="color:{TEXT_DIM}">· {r.cls}</span><br><br>'
                f'<table>{body}</table></div>')
        box = QMessageBox(self)
        box.setWindowTitle(f"Car #{r.car} — full detail")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(html)
        box.setStyleSheet(f"QMessageBox {{ background:{PANEL}; }} "
                          f"QLabel {{ color:{TEXT}; }} "
                          f"QPushButton {{ background:{PANEL2}; color:{TEXT}; "
                          f"border:1px solid {GRID}; border-radius:4px; padding:4px 14px; }}")
        box.exec()

    def _toggle_autotune(self, checked):
        self.autotune = checked
        self.autotune_btn.setText(f"⚙ AUTO-TUNE: {'ON' if checked else 'OFF'}")
        self.autotune_btn.style().unpolish(self.autotune_btn)
        self.autotune_btn.style().polish(self.autotune_btn)


    def _set_delay(self, value):
        self.delay_s = value

    def _toggle_feed(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            if not self.proc.waitForFinished(2000):
                self.proc.kill()
            self.proc = None
            self.connect_btn.setText("● CONNECT")
            self.connect_btn.setProperty("live", False)
        else:
            self.proc = QProcess(self)
            self.proc.setWorkingDirectory(str(ROOT))
            self.proc.start(str(PYTHON), [str(SCRAPER)])
            self.connect_btn.setText("■ DISCONNECT")
            self.connect_btn.setProperty("live", True)
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)

    def refresh(self):
        result = self.poller.poll(self.delay_s)
        if result is None:
            self.status_lbl.setText(f"waiting for data — {DB_PATH}")
            return
        ctx, cars, rc, _age, trend_map = result
        rows = _build_rows(ctx, cars, trend_map, self.filter_cls, poller=self.poller)
        if self.model.set_rows(rows):
            self._apply_spans()
        if self.run_model.set_rows(_build_run_rows(cars, self.filter_cls, self.poller)):
            self._apply_run_spans()
        self._update_header(ctx)
        self._render_weather()
        self._update_rc(rc)
        self._update_alerts(rows)
        # freshness reflects the REAL feed, not the (intentionally) delayed view
        self._update_freshness(self.poller.real_age())
        self._maybe_log()

    def _apply_spans(self):
        """Make class-header rows span the full width."""
        sig = tuple(i for i, r in enumerate(self.model.rows) if r.is_header)
        if sig == self._span_sig:
            return
        self.table.clearSpans()
        for i in sig:
            self.table.setSpan(i, 0, 1, len(COLS))
            self.table.setRowHeight(i, 34)
        self._span_sig = sig

    def _apply_run_spans(self):
        """Make class-header rows span the full width of the running-order panel."""
        sig = tuple(i for i, r in enumerate(self.run_model.rows) if r.is_header)
        if sig == self._run_span_sig:
            return
        self.run_table.clearSpans()
        for i in sig:
            self.run_table.setSpan(i, 0, 1, len(RUN_COLS))
            self.run_table.setRowHeight(i, 34)   # match NET header band height
        self._run_span_sig = sig

    # ---- selection linking between the NET table and the running-order panel ----
    @staticmethod
    def _row_for_car(rows, car: str) -> int:
        if not car:
            return -1
        for i, r in enumerate(rows):
            if not getattr(r, "is_header", False) and getattr(r, "car", "") == car:
                return i
        return -1

    def _selected_car(self, table, rows) -> str:
        idx = table.selectionModel().currentIndex()
        if not idx.isValid() or idx.row() >= len(rows):
            return ""
        r = rows[idx.row()]
        return "" if getattr(r, "is_header", False) else getattr(r, "car", "")

    def _select_car_in(self, table, rows, car: str):
        self._sync_guard = True
        try:
            i = self._row_for_car(rows, car)
            if i < 0:
                table.clearSelection()
            else:
                table.selectRow(i)
                table.scrollTo(table.model().index(i, 0))
        finally:
            self._sync_guard = False

    def _on_main_selected(self, *_):
        if self._sync_guard:
            return
        car = self._selected_car(self.table, self.model.rows)
        self._select_car_in(self.run_table, self.run_model.rows, car)

    def _on_run_selected(self, *_):
        if self._sync_guard:
            return
        car = self._selected_car(self.run_table, self.run_model.rows)
        self._select_car_in(self.table, self.model.rows, car)

    def _maybe_log(self):
        """Throttled prediction logging from the freshest analysis (race only)."""
        p = self.poller
        ctx = p.last_ctx
        if not (ctx and p.last_cars and p.last_oid) or not ctx.is_race:
            return
        age = p.real_age()
        if age is None or age > STALE_AFTER_S:     # don't log stale repeats
            return
        now = datetime.now().timestamp()
        if now - self.last_log_ts < predictor.PREDICT_EVERY_S:
            return
        try:
            if self.write_conn is None:
                self.write_conn = sqlite3.connect(str(DB_PATH))
                self.write_conn.execute("PRAGMA busy_timeout=5000")  # wait, don't drop, on lock
                predictor.ensure(self.write_conn)
            predictor.log_cycle(self.write_conn, p.last_oid, ctx, p.last_cars,
                                int(now * 1000))
            self.last_log_ts = now
        except sqlite3.Error:
            try:
                if self.write_conn:
                    self.write_conn.close()
            except sqlite3.Error:
                pass
            self.write_conn = None

    def _focus_next_penalty(self):
        """Cycle through penalty rows in the main table on each click."""
        pen_rows = [i for i, r in enumerate(self.model.rows)
                    if not r.is_header and r.has_penalty]
        if not pen_rows:
            return
        self._penalty_idx = self._penalty_idx % len(pen_rows)
        row_i = pen_rows[self._penalty_idx]
        self._penalty_idx = (self._penalty_idx + 1) % len(pen_rows)
        self.table.selectRow(row_i)
        self.table.scrollTo(self.model.index(row_i, 0))

    def _update_header(self, ctx):
        bg, label = FLAG_STYLE.get(ctx.flag, ("#3A4150", ctx.flag or "—"))
        self.flag_lbl.setText(label)
        self.flag_lbl.setStyleSheet(
            f"background:{bg}; color:#fff; border-radius:4px; font-weight:700;")
        self.event_lbl.setText(f"{ctx.event}   ·   {ctx.session_name}"
                               f"   ·   Lap {ctx.current_lap}")
        # caution clustering summary
        if ctx.caution_count:
            txt = f"⚑ {ctx.caution_count} caution{'s' if ctx.caution_count > 1 else ''}"
            if ctx.last_caution_lap:
                txt += f" · last L{ctx.last_caution_lap}"
            self.caution_lbl.setText(txt)
        else:
            self.caution_lbl.setText("")
        # penalty counter — separate clickable button so the user can jump to those rows
        cars = self.poller.last_cars or []
        pen_count = sum(1 for c in cars if c.penalty_s > 0 and not c.dq)
        if pen_count:
            self.pen_btn.setText(f"⚠ {pen_count} penalty pending")
            self.pen_btn.setVisible(True)
        else:
            self.pen_btn.setVisible(False)
            self._penalty_idx = 0
        if ctx.is_race and ctx.final_type == "BY_TIME" and ctx.remaining_s:
            s = int(ctx.remaining_s)
            self.clock_lbl.setText(f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}")
        else:
            self.clock_lbl.setText("—")

    def _update_weather(self):
        """Kick a background fetch (blocking I/O) — the result is rendered by refresh()."""
        def work():
            try:
                self.weather = self.weather_poll.get()
            except Exception:
                self.weather = weather_mod.Weather(ok=False)
        threading.Thread(target=work, daemon=True).start()

    def _render_weather(self):
        w = self.weather
        if not w.ok:
            self.weather_lbl.setText(
                f'<span style="color:{TEXT_DIM}">weather n/a</span>')
            return
        color = PURPLE if w.is_wet else TEXT_DIM
        wet = "  ⚠ WET" if w.is_wet else ""
        self.weather_lbl.setText(
            f'<span style="color:{color}">{w.summary()}{wet}</span>')
        # make the auto-tune pause visible when it's wet
        if self.autotune:
            self.autotune_btn.setText(
                "⚙ AUTO-TUNE: PAUSED (wet)" if w.is_wet else "⚙ AUTO-TUNE: ON")

    def _update_rc(self, rc):
        # filter out procedural admin / routine warnings / resolved chatter so the
        # ticker only carries signal (shared classifier — see race_control.py).
        color_for = {"penalty": RED, "dq": RED, "rescinded": GREEN,
                     "retired": TEXT, "flag": TEXT,
                     "review": TEXT_DIM, "warning": AMBER, "incident": TEXT_DIM}
        lines = []
        for msg, _tier, kind in race_control.feed(rc, limit=12):
            lines.append(f'<span style="color:{color_for.get(kind, TEXT_DIM)}">{msg}</span>')
        self.rc_panel["body"].setHtml("<br>".join(lines) or
                                      f'<span style="color:{TEXT_DIM}">no messages</span>')

    def _update_alerts(self, rows):
        alerts = []
        for r in rows:
            if r.is_header or not r.actionable or not r.strategy:
                continue
            cc = _class_color(r.cls)
            alerts.append(
                f'<span style="color:{cc}">#{r.car}</span> '
                f'<span style="color:{TEXT}">{r.strategy}</span>')
        self.alert_panel["body"].setHtml("<br>".join(alerts) or
                                         f'<span style="color:{TEXT_DIM}">no actionable calls</span>')

    def _update_freshness(self, age):
        delay_tag = (f'<span style="color:{PURPLE}">⏵ DELAYED {self.delay_s}s</span>'
                     f'&nbsp;&nbsp;') if self.delay_s else ""
        self.fresh_lbl.setTextFormat(Qt.TextFormat.RichText)
        if age is None:
            self.fresh_lbl.setText(delay_tag + f'<span style="color:{TEXT_DIM}">no data</span>')
        elif age > STALE_AFTER_S:
            self.fresh_lbl.setText(delay_tag + f'<span style="color:{AMBER}">STALE {age:.0f}s</span>')
        else:
            self.fresh_lbl.setText(delay_tag + f'<span style="color:{GREEN}">live · {age:.0f}s</span>')
        running = self.proc and self.proc.state() != QProcess.ProcessState.NotRunning
        self.status_lbl.setText("feed running" if running else "feed external/stopped")

    def _run_eval(self):
        """Background accuracy read (race only). Self-gates to 1h elapsed; writes a
        full report to logs/ and returns a one-line summary for the status bar."""
        ctx = self.poller.last_ctx
        if not (ctx and ctx.is_race):
            return
        if self.eval_proc and self.eval_proc.state() != QProcess.ProcessState.NotRunning:
            return
        self.eval_proc = QProcess(self)
        self.eval_proc.setWorkingDirectory(str(ROOT))
        self.eval_proc.finished.connect(self._eval_done)
        # auto-tune is paused while the track is wet — pace/pit costs swing on a
        # dry↔wet transition and tuning to that would corrupt the dry baseline.
        wet = getattr(self.weather, "is_wet", False)
        auto = self.autotune and not wet
        eargs = [str(EVALUATOR), "--oneline"] + (["--auto"] if auto else [])
        self.eval_proc.start(str(PYTHON), eargs)

    def _eval_done(self):
        try:
            out = bytes(self.eval_proc.readAllStandardOutput()).decode(errors="ignore")
        except Exception:
            out = ""
        line = next((l.strip() for l in reversed(out.splitlines()) if l.strip()), "")
        if line:
            self.acc_lbl.setText(line)
        self.eval_proc = None

    def closeEvent(self, e):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            self.proc.waitForFinished(1500)
        if self.eval_proc and self.eval_proc.state() != QProcess.ProcessState.NotRunning:
            self.eval_proc.kill()
        if self.write_conn:
            try:
                self.write_conn.close()
            except sqlite3.Error:
                pass
        super().closeEvent(e)


QSS = f"""
QMainWindow, QWidget {{ background:{BG}; color:{TEXT};
    font-family:'Helvetica Neue','Segoe UI',sans-serif; }}
#header {{ background:{PANEL}; border-bottom:2px solid {GRID}; }}
#flag {{ font-size:16px; letter-spacing:1.5px; font-weight:700; }}
#event {{ color:{TEXT}; font-size:18px; font-weight:600; padding-left:18px; }}
#caution {{ color:{AMBER}; font-family:'Menlo',monospace; font-size:13px; font-weight:700; }}
#pen_btn {{ color:{RED}; font-family:'Menlo',monospace; font-size:13px; font-weight:700;
    background:transparent; border:none; padding:0; text-align:left; }}
#pen_btn:hover {{ color:#FF9090; text-decoration:underline; }}
#weather {{ font-family:'Menlo',monospace; font-size:13px; }}
#clock {{ color:{ACCENT}; font-family:'Menlo',monospace; font-size:32px; font-weight:700; }}
#fbar {{ background:{PANEL}; border-bottom:1px solid {GRID}; }}
#fresh {{ font-family:'Menlo',monospace; font-size:13px; }}
#dlabel {{ color:{TEXT_DIM}; font-size:11px; font-weight:700; letter-spacing:1px; }}
QSpinBox#delay {{ background:{PANEL2}; color:{TEXT}; border:1px solid {GRID};
    border-radius:4px; padding:2px 6px; font-family:'Menlo',monospace; font-size:12px; }}
QSpinBox#delay::up-button, QSpinBox#delay::down-button {{ width:14px; background:{GRID}; }}
QPushButton#chip {{ background:{PANEL2}; color:{TEXT_DIM}; border:1px solid {GRID};
    border-radius:13px; padding:4px 14px; font-size:12px; font-weight:600; }}
QPushButton#chip:checked {{ background:{ACCENT}; color:#06231A; border:1px solid {ACCENT}; }}
QPushButton#chip:hover {{ color:{TEXT}; }}
QTableView {{ background:{BG}; alternate-background-color:{PANEL2};
    gridline-color:{GRID}; selection-background-color:#1E3A4F;
    selection-color:#fff; border:none; }}
QHeaderView::section {{ background:#1C2330; color:{TEXT}; border:none;
    border-bottom:2px solid {GRID}; padding:6px 4px; font-size:12px;
    font-weight:700; letter-spacing:1.5px; }}
#bottom {{ background:{PANEL}; border-top:2px solid {GRID}; }}
#runwrap {{ background:{BG}; border-left:2px solid {GRID}; }}
#runtitle {{ background:#1C2330; color:{TEXT}; font-size:11px; font-weight:700;
    letter-spacing:1.5px; border-bottom:2px solid {GRID}; }}
QTableView#runtable {{ background:{BG}; alternate-background-color:{PANEL2};
    border:none; }}
QTableView#runtable QHeaderView::section {{ background:{PANEL}; color:{TEXT_DIM};
    border:none; border-bottom:1px solid {GRID}; padding:3px 2px; font-size:10px;
    font-weight:700; letter-spacing:1px; }}
#panel {{ background:{PANEL}; border-right:1px solid {GRID}; }}
#paneltitle {{ color:#B0BACA; font-size:12px; font-weight:700; letter-spacing:2.5px; }}
#panelbody {{ font-family:'Menlo',monospace; font-size:13px; color:{TEXT};
    background:{PANEL}; border:none; }}
QTextEdit#panelbody QScrollBar:vertical {{ background:{PANEL}; width:8px; }}
QTextEdit#panelbody QScrollBar::handle:vertical {{ background:{GRID}; border-radius:4px; }}
QStatusBar {{ background:{PANEL}; color:{TEXT_DIM}; border-top:1px solid {GRID}; }}
#acc {{ color:{PURPLE}; font-family:'Menlo',monospace; font-size:12px; padding-right:14px; }}
QPushButton#autotune {{ background:{PANEL2}; color:{TEXT_DIM}; border:1px solid {GRID};
    border-radius:4px; padding:3px 10px; font-size:11px; font-weight:700; }}
QPushButton#autotune:checked {{ background:{AMBER}; color:#241A00; border:1px solid {AMBER}; }}
QPushButton#connect {{ background:{PANEL2}; color:{ACCENT}; border:1px solid {GRID};
    border-radius:4px; padding:4px 14px; font-weight:700; }}
QPushButton#connect[live="true"] {{ color:{RED}; }}
QScrollBar:vertical {{ background:{BG}; width:10px; }}
QScrollBar::handle:vertical {{ background:{GRID}; border-radius:5px; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = Dashboard()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
