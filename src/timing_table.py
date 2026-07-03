"""
timing_table.py — dense timing table models, row builders, and shared palette.

Extracted from dashboard.py so the table layer can be reused by the Timing ↗
satellite window without importing the full Dashboard app shell. dashboard.py
re-exports all names from here so existing callers (including dashboard_calm.py's
`import dashboard as dash`) need no changes.
"""

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QFont

import calculator
import series_profiles

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

# Pit-lane state sets — shared by the row builders and Poller in dashboard.py.
# Broad (PIT_LANE_STATES) for the in-pit indicator; strict (BOX_STATES) for the
# stop timer so in/out-lap transit doesn't inflate the "In pits" clock.
PIT_LANE_STATES = ("BOX", "IN_LAP", "OUT_LAP", "PIT", "STOPPED")
BOX_STATES      = ("BOX", "PIT", "STOPPED")

# Module-level IMSA defaults — re-exported for backward compat. Callers that
# have a live RaceContext should pass ctx.profile to the row builders instead.
CLASS_COLORS = dict(series_profiles.IMSA.class_colors)
CLASS_ORDER  = dict(series_profiles.IMSA.class_order)

# ── column definitions ───────────────────────────────────────────────────────
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


def _class_color(cls: str, profile=None) -> str:
    colors = profile.class_colors if profile is not None else CLASS_COLORS
    return colors.get(cls, "#555B66")


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
    cls_color: str = "#555B66"     # resolved from ctx.profile at build time
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
    pit_scope: str = ""                 # car/class/field/default — pit model scope


def _strategy_text(c) -> tuple[str, bool]:
    """Compose a concise strategy call + whether it's an actionable alert."""
    note = c.strategy_note or ""
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
                poller=None) -> list["Row"]:
    _profile = getattr(ctx, "profile", series_profiles.IMSA)
    by_class: dict[str, list] = {}
    for c in cars:
        by_class.setdefault(c.car_class, []).append(c)
    fastest: dict[str, float] = {}
    for c in cars:
        if c.best_lap_ms:
            fastest[c.car_class] = min(fastest.get(c.car_class, 9e18), c.best_lap_ms)

    key = lambda c: (c.net_position or 99)

    rows: list[Row] = []
    for cls in sorted(by_class, key=lambda k: (_profile.class_order.get(k, 9), k)):
        if filter_cls and cls != filter_cls:
            continue
        group = by_class[cls]
        color = _class_color(cls, _profile)
        rows.append(Row(is_header=True, cls=cls, cls_color=color,
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

            from datetime import datetime
            now = datetime.now().timestamp()
            box_s = (now - poller.box_since[c.car_number]
                     if poller and c.car_number in poller.box_since else None)
            just_pitted = bool(poller and c.car_number in poller.just_pitted_ts)
            catch_imminent = bool(c.catch_in_laps is not None and c.catch_in_laps <= 3
                                  and c.catching)

            if in_box and box_s is not None:
                m, s = int(box_s) // 60, int(box_s) % 60
                strat = f"In pits  {m}:{s:02d}"
                actionable = box_s > 90

            net_pos_delta: Optional[int] = None
            if (poller and c.car_number in poller.pit_delta_ts
                    and c.car_number in poller.pit_before_net and not in_box):
                before = poller.pit_before_net[c.car_number]
                after = c.net_position or 99
                net_pos_delta = before - after
                if net_pos_delta > 0:
                    sign = f"Gained {net_pos_delta} on stop"
                elif net_pos_delta < 0:
                    sign = f"Lost {-net_pos_delta} on stop"
                else:
                    sign = "Held position on stop"
                strat = f"{sign}  ·  {strat}" if strat else sign

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

            window_open = (c.pit_window_open or
                           bool(poller and c.car_number in poller.window_locked))
            if window_open:
                window = "OPEN"
            elif c.fuel_laps_left is not None:
                window = f"{c.fuel_laps_left}L"
            else:
                window = "—"

            rows.append(Row(
                car=c.car_number, team=(c.team or ""), cls=cls, cls_color=color,
                net=c.net_position,
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
                pit_scope=c.pit_scope,
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
                return _blend(r.cls_color, BG, 0.70)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(r.cls_color).lighter(160)
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
                return QColor(r.cls_color)
            if r.dq:
                return QColor(75, 12, 18)
            if r.has_penalty:
                return QColor(65, 42, 0)
            if r.in_box:
                return QColor(8, 18, 30)
            if r.just_pitted and not r.in_box:
                return QColor(15, 38, 70)
            if col == C_STRAT and r.actionable and not r.in_box:
                return QColor("#2A2410")
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._fg(r, col)
        if role == Qt.ItemDataRole.FontRole:
            return self._font(r, col)
        return None

    def _tooltip(self, r: Row, col: int):
        if col == C_NET:
            return ("Net position — where this car ends up once everyone has taken "
                    "their remaining pit stops. This is the real running order. "
                    "Double-click for full detail.")
        if col == C_GAP and r.pit_scope and r.pit_scope != "car":
            scope_label = {"class": "class-level", "field": "field-wide",
                           "default": "default (no pit data)"}.get(r.pit_scope, r.pit_scope)
            return f"Pit prediction from {scope_label} data — band may be wide."
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
        if r.dq:
            return QColor("#FF5C6C") if col == C_STRAT else QColor("#555B66")
        if col == C_TREND:
            if r.in_box:
                return QColor("#3A6080")
            return QColor(GREEN if r.trend > 0 else RED if r.trend < 0 else TEXT_DIM)
        if col == C_BEST and r.best_purple:
            return QColor(PURPLE)
        if col == C_PACE and r.degrading:
            return QColor(AMBER)
        if col == C_WINDOW and r.window_open:
            return QColor(ACCENT)
        if col == C_STRAT and r.actionable:
            if r.catch_imminent:
                return QColor("#FFFFFF")
            if r.in_box and r.box_s and r.box_s > 90:
                return QColor(RED)
            return QColor(AMBER)
        if col == C_DELTA and r.net and r.trk:
            d = r.trk - r.net
            return QColor(GREEN if d > 0 else RED if d < 0 else TEXT_DIM)
        if col == C_NET and r.net_leader:
            return QColor(ACCENT)
        return QColor(TEXT)

    def _font(self, r: Row, col: int):
        f = QFont("Menlo", 14)
        if col == C_NET:
            f.setBold(True); f.setPointSize(18)
        if col == 3:
            f.setBold(True)
        if col == C_CLS:
            f.setBold(True); f.setPointSize(12)
        if col == C_GAP:
            f.setPointSize(15)
        if r.in_box and col == 5:
            f.setItalic(True)
        return f


# ── running-order panel ──────────────────────────────────────────────────────
RUN_COLS = ["P", "CAR", "INT"]


@dataclass
class RunRow:
    is_header: bool = False
    header_label: str = ""
    cls: str = ""
    cls_color: str = "#555B66"     # resolved from profile at build time
    pos: int = 0
    car: str = ""
    interval: str = ""
    in_box: bool = False
    just_pitted: bool = False


def _build_run_rows(cars, filter_cls: Optional[str], poller=None,
                    profile=None) -> list[RunRow]:
    """On-track running order, grouped by class to mirror the NET table."""
    _profile = profile or series_profiles.IMSA
    by_class: dict[str, list] = {}
    for c in cars:
        if c.track_position is None:
            continue
        if filter_cls and c.car_class != filter_cls:
            continue
        by_class.setdefault(c.car_class, []).append(c)

    out: list[RunRow] = []
    for cls in sorted(by_class, key=lambda k: (_profile.class_order.get(k, 9), k)):
        group = by_class[cls]
        group.sort(key=lambda c: (c.pos_in_class or 99, c.track_position or 999))
        color = _class_color(cls, _profile)
        out.append(RunRow(is_header=True, cls=cls, cls_color=color,
                          header_label=f"{cls}   ·   {len(group)} cars"))
        prev_elapsed = None
        for c in group:
            e = c.elapsed_ms
            if e is not None and prev_elapsed is not None and e >= prev_elapsed:
                gap = e - prev_elapsed
                interval = (f"+{gap/1000:.3f}" if gap < 60_000
                            else f"+{int(gap//60000)}:{gap%60000/1000:06.3f}")
            else:
                interval = "—"
            if e is not None:
                prev_elapsed = e
            in_box = (c.track_status or "") in PIT_LANE_STATES
            out.append(RunRow(
                pos=(c.pos_in_class or 0), car=c.car_number, cls=cls, cls_color=color,
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
                return _blend(r.cls_color, BG, 0.70)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(r.cls_color).lighter(160)
            if role == Qt.ItemDataRole.FontRole:
                f = QFont("Helvetica Neue", 13); f.setBold(True)
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
                return QColor("#6FA8FF")
            if r.in_box:
                return QColor("#5A6470")
            if col == 0:
                return QColor(TEXT_DIM)
            return QColor(TEXT)
        if role == Qt.ItemDataRole.FontRole:
            f = QFont("Menlo", 14)
            if col in (0, 1):
                f.setBold(True)
            return f
        return None
