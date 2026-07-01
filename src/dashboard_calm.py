"""
dashboard_calm.py — the redesigned "catch-up" Strategy screen (PyQt6).

A calmer, glance-first take on the pit-wall dashboard. Reuses the existing data
layer untouched (dashboard.Poller → calculator.analyse → _build_rows) and only
replaces the presentation:

  • one entry point per row — the NET number is big; everything else recedes
  • colour is a spotlight, not wallpaper (leader green, penalty amber, class spine)
  • a "breath": a row glows gently for ~2.4s when its net position changes or a
    penalty appears — noticeable, not jarring
  • an always-on right rail: WATCH (actionable items) · RACE CONTROL · ON TRACK
  • a surface switch [Strategy | Timing] — Timing (the dense per-car view) is next

The original dashboard.py is left intact as the fallback. Run:
  QT_QPA_PLATFORM=offscreen is honoured for headless screenshots.
  ./venv/bin/python src/dashboard_calm.py
"""

import sys
import time
from datetime import datetime

from PyQt6.QtCore import (Qt, QTimer, QPropertyAnimation, pyqtProperty,
                          QEasingCurve, QRect)
from PyQt6.QtGui import QColor, QFont, QPainter, QFontMetrics
from PyQt6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect,
                             QHBoxLayout, QLabel, QMainWindow, QPushButton,
                             QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

import json
import pathlib
import sqlite3

import calculator
import config
import race_control
import catchup
import series_profiles
import quali
import dashboard as dash   # reuse Poller, Row, _build_rows, FLAG_STYLE, CLASS_ORDER
import dashboard_quali as dq   # F1 knockout-qualifying cut-line panel (Phase 2b)
import session_picker          # F1 race/session picker dialog

# ── static entry-list fallback (team/driver names when feed is silent) ────────
def _load_entries() -> dict:
    """Search data/ for entries_*.json files, return car# → {team, drivers} merged dict."""
    out = {}
    data_dir = pathlib.Path(__file__).parent.parent / "data"
    for f in sorted(data_dir.glob("entries_*.json")):
        try:
            doc = json.loads(f.read_text())
            for car, info in doc.get("entries", {}).items():
                out[car] = info
        except Exception:
            pass
    return out

_ENTRIES: dict = _load_entries()


# ── F1 team table (car# → TLA/team/colour) — scripted off FastF1, see
# src/build_f1_team_table.py. Falls back to the driver-identity generic path
# below when a car isn't in the table (e.g. a mid-season reserve driver).
def _load_f1_teams() -> dict:
    out = {}
    data_dir = pathlib.Path(__file__).parent.parent / "data"
    for f in sorted(data_dir.glob("f1_*.json")):
        try:
            doc = json.loads(f.read_text())
            out.update(doc.get("drivers", {}))
        except Exception:
            pass
    return out

_F1_TEAMS: dict = _load_f1_teams()

# ── calm palette (nudged ~8% brighter; row LINE more visible per feel-test) ───
BG      = "#10141A"
RAIL    = "#0D1117"
LINE    = "#3C4756"   # row hairline — brighter so the eye stays level scanning across
HEAD    = "#3C4757"
TXT     = "#F5F7FA"
DIM     = "#C2CAD6"
MUTE    = "#8A94A2"
FAINT   = "#697483"
GREEN   = "#6BE6A8"   # gain / fresh tyres
RED     = "#F2706F"   # loss
AMBER   = "#F4C485"   # penalty / pit-due (red tank → due now)
AMBER_SOFT = "#BFA074" # fuel window opening (yellow tank → due soon)
BLUE    = "#8CB9F2"   # strategy (undercut/overcut/in-pit)

# brighter class spines than the dense-table palette (calm screen wants a touch more pop).
# Canonical values live on the IMSA SeriesProfile; re-exported so references are unchanged.
# Phase 2 flips this to the ACTIVE session's profile so F1/WEC/IndyCar bring their own.
SPINE = dict(series_profiles.IMSA.spine)

MONO = "Menlo"
SANS = "Helvetica Neue"

OVERRIDE = "#FFD166"   # F1 2026 manual-override/boost active — distinct from every other cue

# FIA-standard tyre-compound colours (F1 only; NULL for IMSA — no chip painted).
# Third element: use dark text on the chip (light backgrounds only).
TIRE_STYLE = {
    "SOFT":         ("S", "#F2444A", False),
    "MEDIUM":       ("M", "#F4C445", True),
    "HARD":         ("H", "#F5F5F5", True),
    "INTERMEDIATE": ("I", "#3FAE4E", False),
    "WET":          ("W", "#3B82F6", False),
}


def _spine(cls: str, profile=None) -> str:
    if profile is not None:
        return profile.spine_of(cls)
    return SPINE.get(cls, "#5F6B7A")


# ── catch-up ("while you were away") tone → palette + compact inline labels ────
TONE_HEX = {
    catchup.LEAD: GREEN, catchup.GAIN: GREEN, catchup.LOSS: RED,
    catchup.PIT: BLUE, catchup.PENALTY: AMBER, catchup.DQ: RED,
    catchup.RESCINDED: GREEN, catchup.RETIRED: MUTE, catchup.CAUTION: AMBER_SOFT,
}


def _since_short(ev) -> str:
    """A few characters for the lingering inline 'SINCE' trail badge on a changed row."""
    return {
        catchup.LEAD: "led", catchup.GAIN: "▲ moved", catchup.LOSS: "▼ moved",
        catchup.PIT: "pitted", catchup.PENALTY: "⚠ pen", catchup.DQ: "DQ",
        catchup.RESCINDED: "cleared", catchup.RETIRED: "out",
    }.get(ev.tone, "since")


def _short_driver(name: str, roster=None) -> str:
    """The CURRENT driver as 'F. Lastname'. The feed gives 'LAST, First' (UPPERCASE
    surname); use the entry-list roster (properly cased) for the surname when we can
    match it. Empty when no current driver is known — we never guess who's in the car."""
    if not name or name == "?":
        return ""
    if "," in name:                                   # "VAN DER LINDE, Sheldon"
        last, first = (p.strip() for p in name.split(",", 1))
    else:                                             # "Sheldon van der Linde"
        toks = name.split()
        first = toks[0] if toks else ""
        last = " ".join(toks[1:]) if len(toks) > 1 else name
    if roster:                                        # borrow the roster's nice casing
        for full in roster:
            if last and last.lower() in full.lower():
                rt = full.split()
                first = rt[0]
                last = " ".join(rt[1:]) if len(rt) > 1 else full
                break
    if last.isupper():
        last = last.title()
    return (f"{first[:1].upper()}. {last}" if first else last).strip()


def _alert_for(ca):
    """The one real strategic call for a car, derived from the analysis (NOT the
    display string, which the in-pit timer overwrites). Returns (text, colour) or
    None. Penalty > undercut/overcut > catch; in-pit/fuel chatter is never an alert."""
    if ca is None:
        return None
    if ca.dq:
        return ("DQ — to back", AMBER)
    if ca.penalty_note and ca.penalty_s > 0:
        return (ca.penalty_note, AMBER)
    note = ca.strategy_note or ""
    if note.startswith("undercut #"):
        return (f"Undercut #{note.split('#', 1)[1].split()[0]}", BLUE)
    if note.startswith("overcut #"):
        return (f"Overcut #{note.split('#', 1)[1].split()[0]}", BLUE)
    if ca.catching and ca.catch_in_laps is not None and 0 < ca.catch_in_laps <= 5:
        return (f"Catching #{ca.catching}", DIM)
    return None


def _gap_cell(ca, dim: bool):
    """On-track gap to the class leader — the REAL spacing right now (not the net
    projection). Laps when genuinely lapped. This is what 'how far back' means."""
    if ca is None:
        return ("—", MUTE)
    if (ca.effective_pos_in_class or ca.pos_in_class) == 1:
        return ("LEAD", MUTE)
    if ca.laps_down and ca.laps_down > 0:
        return (f"+{ca.laps_down}L", MUTE)
    # a non-leader can't truly be 0.000 behind — that's the feed's "no gap" sentinel
    # cancelling out (e.g. a whole class reading 900000ms), not a real dead heat
    if ca.class_gap_ms is not None and 0.05 <= ca.class_gap_ms < 600_000:
        return (f"+{ca.class_gap_ms / 1000:.3f}", MUTE if dim else TXT)
    return ("—", MUTE)


def _pit_cell(ca, current_lap, in_box: bool):
    """Live pit STATUS only — who's mid-stop or on their out lap. Both are bounded,
    factual states (only cars physically in the pit sequence), so they stay on the row
    and are exempt from the highlight budget. DUE is no longer shown here — the fuel/pit
    call is list-shaped and lives in the rail's "DUE TO PIT" roster, keeping the board
    calm. OUT is the single out lap (pit exit → start/finish), from the feed's
    track_status. Blank = calm."""
    if in_box:
        return ("● IN PIT", BLUE)                 # pitting right now
    if ca is None:
        return ("", MUTE)
    if (ca.track_status or "") == "OUT_LAP":
        return ("OUT", GREEN)                     # the out lap — back up to speed
    return ("", MUTE)


def _row_vm(r, ca, current_lap, cycle_active=True, since=None, allow_net=True,
           profile=None) -> dict:
    """Flatten a Row (+ its CarAnalysis) into the text/colour view-model the
    custom-painted RowWidget consumes.

    Board is TRACK-led: the big number is the car's on-track position (reality),
    so the list and the GAP column read straight down. NET is an overlay that only
    speaks when effective position differs — ▲P{n} (will gain) / ▼P{n} (will drop)
    — AND only while the class is out of sequence on stops (cycle_active). Before
    anyone pits, every car shares one plan, so net≠track is pure projection noise."""
    # In-box = genuinely stopped/in pit lane (BOX/PIT/STOPPED). An out lap (OUT_LAP) is
    # racing again, so it must NOT dim like an in-pit car — _pit_cell gives it its own
    # GREEN "OUT" badge and the gap reads live.
    in_box = ca is not None and (ca.track_status or "") in dash.BOX_STATES
    dim = in_box
    trk, net = r.trk, r.net

    pos_text  = str(trk) if trk else "—"
    pos_color = MUTE if dim else (GREEN if trk == 1 else TXT)   # green = on-track class leader

    stint_text, stint_color = _pit_cell(ca, current_lap, in_box)
    gap_text, gap_color = _gap_cell(ca, dim)

    # net overlay speaks only when: the class is out of sequence on stops (cycle in
    # play) AND this car has a REAL same-lap gap to build net on. A sentinel/missing
    # gap (the feed's 900000 / 100000 "no precise gap" values, or a lapped car) makes
    # net junk too — so stay quiet rather than show a confident-looking arrow on noise.
    # mirror _gap_cell's "real gap" test exactly: lead-lap, and either the class
    # leader (gap 0) or a genuine positive sub-sentinel gap. Anything else → "—" gap,
    # so net stays "—" too (never an arrow over a dashed gap).
    gap_ok = (ca is not None and (ca.laps_down or 0) == 0
              and ((ca.effective_pos_in_class or ca.pos_in_class) == 1
                   or (ca.class_gap_ms is not None and 0.05 <= ca.class_gap_ms < 600_000)))
    # in-box cars are mid-stop (position/gap in flux, row dimmed) — keep net quiet.
    # allow_net = this car won the per-class attention budget (the global cap that stops
    # the board lighting up field-wide); when it didn't, stay "—" even if the gates pass.
    if cycle_active and gap_ok and not in_box and net and trk and net != trk and allow_net:
        if net < trk:  net_text, net_color = f"▲P{net}", GREEN  # effective spot is higher → gaining
        else:          net_text, net_color = f"▼P{net}", RED    # effective spot is lower → dropping
    else:
        net_text, net_color = "—", FAINT

    # CALL on-row is reserved for the critical, car-specific call: penalty / DQ only.
    # Undercut/overcut/catch are list-shaped and live in the rail (RACE AT A GLANCE).
    if ca is not None and ca.dq:
        call_text, call_color = ("DQ — to back", AMBER)
    elif ca is not None and ca.penalty_note and ca.penalty_s > 0:
        call_text, call_color = (ca.penalty_note, AMBER)
    else:
        call_text, call_color = ("", MUTE)

    # SINCE — the lingering catch-up trail: a faint badge on a row that changed while
    # you were away. It borrows the NOTE slot (CALL is almost always empty on the calm
    # board) and breathes once when first armed (handled in RowWidget.update_row).
    since_text = _since_short(since) if since is not None else ""
    since_tone = TONE_HEX.get(since.tone, MUTE) if since is not None else MUTE

    # identity slot: "driver"-identity series (F1) lead with the TLA in team colour and
    # push the full team name to the dim slot — the IMSA "team · driver" reading flipped
    # to match how F1 boards are actually read. Falls back to the generic team/driver
    # text when the car isn't in the scripted table (e.g. a mid-season reserve driver).
    team_text, driver_text = r.team, r.driver
    team_color = MUTE if dim else DIM
    driver_color = FAINT if dim else MUTE
    if profile is not None and profile.identity == "driver":
        info = _F1_TEAMS.get(r.car)
        if info:
            team_text = info.get("tla") or r.team
            team_color = MUTE if dim else info.get("color", DIM)
            driver_text = info.get("team") or r.driver

    # tyre chip — letter + age, FIA compound colour. NULL for IMSA (no chip painted).
    tire_letter, tire_color, tire_dark, tire_age_text = "", MUTE, False, ""
    if ca is not None and ca.tire_compound:
        tire_letter, tire_color, tire_dark = TIRE_STYLE.get(
            ca.tire_compound.upper(), ("?", MUTE, False))
        if ca.tire_age is not None:
            tire_age_text = str(ca.tire_age)

    # override/boost — reserved slot, only lights when the feed actually populates it
    # (2026 field names are provisional; see series_profiles.py docstring).
    override_on = bool(ca is not None and ca.override_state)

    return {
        "net": net, "has_penalty": r.has_penalty,          # net drives the breath (filters pit-shuffle)
        "pos_text": pos_text, "pos_color": pos_color,
        "net_text": net_text, "net_color": net_color,
        "car_num": f"#{r.car}", "num_color": DIM if dim else TXT,
        "team": team_text, "team_color": team_color,
        "driver": driver_text, "driver_color": driver_color,
        "tire_letter": tire_letter, "tire_color": tire_color, "tire_dark": tire_dark,
        "tire_age_text": tire_age_text, "override_on": override_on,
        "stops_text": (str(ca.stops) if ca is not None else ""),
        "stint_text": stint_text, "stint_color": stint_color,
        "gap_text": gap_text, "gap_color": gap_color,
        "call_text": call_text, "call_color": call_color,
        "since_text": since_text, "since_tone": since_tone,
    }


# ── shared column geometry (RowWidget + ColumnHeader must agree) ──────────────
def _columns(W: int) -> dict:
    pad = 18
    call_l  = W - pad - 150
    gap_r   = call_l - 16
    gap_l   = gap_r - 64
    stint_r = gap_l - 8
    stint_l = stint_r - 84
    stops_r = stint_l - 16
    stops_l = stops_r - 48
    return {"pos_x": 18, "net_x": 56, "car_x": 104,
            "stops_l": stops_l, "stops_r": stops_r,
            "stint_l": stint_l, "stint_r": stint_r,
            "gap_l": gap_l, "gap_r": gap_r,
            "call_l": call_l, "car_r": stops_l - 14}


# ── a single car row — fully custom-painted in ONE pass ──────────────────────
# No child widgets: the entire row (background, breath overlay, hairline, and
# every column of text) is drawn in paintEvent. This is the only way to get the
# breath + hairline to span the full width — child labels would composite on top
# and occlude them (the old "breathes only in the corner" bug) — and it gives
# pixel control to match the mockup.
ROW_H = 42

class RowWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(ROW_H)
        self.vm: dict | None = None
        self._breath = 0.0
        self._breath_color = QColor(GREEN)
        self._prev_net = None
        self._prev_pen = False
        self._prev_since = ""           # last SINCE badge shown (to breathe once on arm)

        self.f_net   = QFont(MONO, 21, QFont.Weight.Medium)
        self.f_delta = QFont(MONO, 12)
        self.f_num   = QFont(SANS, 13, QFont.Weight.DemiBold)
        self.f_team  = QFont(SANS, 12, QFont.Weight.Medium)
        self.f_drv   = QFont(SANS, 11)
        self.f_stint = QFont(MONO, 11)
        self.f_gap   = QFont(MONO, 13)
        self.f_call  = QFont(SANS, 11)
        self.f_tire  = QFont(SANS, 9, QFont.Weight.Bold)

        self._anim = QPropertyAnimation(self, b"breath")
        self._anim.setDuration(2400)
        self._anim.setKeyValueAt(0.0, 0.0)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setKeyValueAt(1.0, 0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)

    def getBreath(self):
        return self._breath

    def setBreath(self, v):
        self._breath = v
        self.update()

    breath = pyqtProperty(float, getBreath, setBreath)

    def _pulse(self, color: str):
        self._breath_color = QColor(color)
        self._anim.stop()
        self._anim.start()

    def update_row(self, vm: dict):
        # breath fires on a penalty, or on a net-position change — but ONLY when the
        # NET overlay is actually shown (gated: real cycle + trustworthy gap). Raw
        # net_position jitters from projection noise early in the race; breathing on
        # that lit up stable rows for no reason. Tie the glow to the displayed signal.
        #
        # The net-change pulse is a NEUTRAL "this row just moved" cue (soft blue), not
        # green/red — those belong solely to the steady ▲P/▼P arrow (net-vs-track). A
        # green flash on a ▼P row otherwise reads as a contradiction (the flash means
        # "net just improved over time"; the arrow means "still owes a stop"). Penalty
        # keeps its own amber; the SINCE badge keeps its catch-up tone.
        shown = vm["net_text"] != "—"
        if self._prev_net is not None:
            if vm["has_penalty"] and not self._prev_pen:
                self._pulse(AMBER)
            elif shown and vm["net"] and self._prev_net and vm["net"] != self._prev_net:
                self._pulse(BLUE)
        # a newly-armed catch-up SINCE badge breathes once in its own tone — the board
        # itself drawing your eye to what moved after the welcome-back card is dismissed
        since = vm.get("since_text", "")
        if since and since != self._prev_since:
            self._pulse(vm.get("since_tone", MUTE))
        self._prev_since = since
        self._prev_net = vm["net"]
        self._prev_pen = vm["has_penalty"]
        self.vm = vm
        self.update()

    def paintEvent(self, _e):
        if self.vm is None:
            return
        vm = self.vm
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        W, H = self.width(), self.height()
        VC = Qt.AlignmentFlag.AlignVCenter
        L, R = Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignRight

        p.fillRect(self.rect(), QColor(BG))
        if self._breath > 0:                       # full-width breath overlay
            c = QColor(self._breath_color)
            c.setAlphaF(0.30 * self._breath)
            p.fillRect(self.rect(), c)
        p.fillRect(0, H - 1, W, 1, QColor(LINE))    # full-width bottom hairline

        c = _columns(W)
        car_r = c["car_r"]

        # POS — big on-track position (the entry point: the race as it stands)
        p.setFont(self.f_net); p.setPen(QColor(vm["pos_color"]))
        p.drawText(QRect(c["pos_x"], 0, 36, H), L | VC, vm["pos_text"])
        # NET overlay — speaks only when effective position differs (▲P / ▼P)
        p.setFont(self.f_delta); p.setPen(QColor(vm["net_color"]))
        p.drawText(QRect(c["net_x"], 0, 46, H), L | VC, vm["net_text"])

        # CALL (the one strategic note; mostly empty)
        if vm["call_text"]:
            fm = QFontMetrics(self.f_call)
            p.setFont(self.f_call); p.setPen(QColor(vm["call_color"]))
            p.drawText(QRect(c["call_l"], 0, 150, H), L | VC,
                       fm.elidedText(vm["call_text"], Qt.TextElideMode.ElideRight, 150))
        # SINCE — lingering catch-up trail, right-aligned at the row edge (tone-coloured)
        if vm.get("since_text"):
            p.setFont(self.f_call); p.setPen(QColor(vm["since_tone"]))
            p.drawText(QRect(c["call_l"], 0, W - 18 - c["call_l"], H), R | VC, vm["since_text"])
        # GAP (on-track gap to class leader — real spacing, climbs down the list)
        p.setFont(self.f_gap); p.setPen(QColor(vm["gap_color"]))
        p.drawText(QRect(c["gap_l"], 0, c["gap_r"] - c["gap_l"], H), R | VC, vm["gap_text"])
        # STOPS — count made so far (the "who's out of sequence" read), dim reference
        if vm["stops_text"]:
            p.setFont(self.f_stint); p.setPen(QColor(FAINT if vm["stops_text"] == "0" else MUTE))
            p.drawText(QRect(c["stops_l"], 0, c["stops_r"] - c["stops_l"], H), R | VC, vm["stops_text"])
        # STINT / pit state
        if vm["stint_text"]:
            p.setFont(self.f_stint); p.setPen(QColor(vm["stint_color"]))
            p.drawText(QRect(c["stint_l"], 0, c["stint_r"] - c["stint_l"], H), R | VC, vm["stint_text"])

        # CAR identity:  #num (bold) · [tyre] [override] · team (medium) · driver (dim)
        x = c["car_x"]
        fm_num = QFontMetrics(self.f_num)
        p.setFont(self.f_num); p.setPen(QColor(vm["num_color"]))
        p.drawText(QRect(x, 0, car_r - x, H), L | VC, vm["car_num"])
        x += fm_num.horizontalAdvance(vm["car_num"]) + 10

        # TYRE chip — compound letter in a small rounded box + stint age. F1 only;
        # blank (no chip) whenever tire_compound is NULL, e.g. every IMSA row.
        if vm["tire_letter"] and x < car_r:
            chip_w = 18
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(vm["tire_color"]))
            p.drawRoundedRect(QRect(x, (H - 16) // 2, chip_w, 16), 3, 3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QColor("#10141A" if vm["tire_dark"] else "#F5F7FA"))
            p.setFont(self.f_tire)
            p.drawText(QRect(x, 0, chip_w, H), Qt.AlignmentFlag.AlignCenter, vm["tire_letter"])
            x += chip_w + 3
            if vm["tire_age_text"]:
                p.setFont(self.f_stint); p.setPen(QColor(FAINT))
                age_w = QFontMetrics(self.f_stint).horizontalAdvance(vm["tire_age_text"])
                p.drawText(QRect(x, 0, age_w + 2, H), L | VC, vm["tire_age_text"])
                x += age_w + 10
            else:
                x += 7

        # OVERRIDE/boost — reserved slot (2026 field, provisional): lights only when the
        # feed actually populates override_state, blank otherwise on every other row.
        if vm["override_on"] and x < car_r:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(OVERRIDE))
            p.drawEllipse(x, H // 2 - 3, 6, 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            x += 6 + 8

        if vm["team"] and x < car_r:
            fm_t = QFontMetrics(self.f_team)
            p.setFont(self.f_team); p.setPen(QColor(vm["team_color"]))
            team = fm_t.elidedText(vm["team"], Qt.TextElideMode.ElideRight, car_r - x)
            p.drawText(QRect(x, 0, car_r - x, H), L | VC, team)
            x += fm_t.horizontalAdvance(team)
        if vm["driver"] and x + 18 < car_r:
            fm_d = QFontMetrics(self.f_drv)
            p.setFont(self.f_drv); p.setPen(QColor(FAINT))
            p.drawText(QRect(x, 0, car_r - x, H), L | VC, "  ·  ")
            x += fm_d.horizontalAdvance("  ·  ")
            p.setPen(QColor(vm["driver_color"]))
            drv = fm_d.elidedText(vm["driver"], Qt.TextElideMode.ElideRight, car_r - x)
            p.drawText(QRect(x, 0, car_r - x, H), L | VC, drv)


class ColumnHeader(QWidget):
    """Fixed header row labelling the columns — painted with the SAME geometry as
    RowWidget so the labels sit over their columns."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(24)
        self.f = QFont(SANS, 9, QFont.Weight.Medium)
        self.f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.3)
        self.profile = None      # set via set_profile() once the active session is known

    def set_profile(self, profile):
        if profile is not self.profile:
            self.profile = profile
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        W, H = self.width(), self.height()
        VC = Qt.AlignmentFlag.AlignVCenter
        L, R = Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignRight
        p.fillRect(self.rect(), QColor(BG))
        p.fillRect(0, H - 1, W, 1, QColor(LINE))
        p.setFont(self.f); p.setPen(QColor(FAINT))
        c = _columns(W)
        p.drawText(QRect(c["pos_x"], 0, 40, H), L | VC, "POS")
        p.drawText(QRect(c["net_x"], 0, 46, H), L | VC, "NET")
        # driver identity (F1) leads with TLA, so the label flips to match
        identity_label = ("DRIVER · TEAM" if self.profile is not None
                          and self.profile.identity == "driver" else "TEAM · DRIVER")
        p.drawText(QRect(c["car_x"], 0, c["car_r"] - c["car_x"], H), L | VC, identity_label)
        p.drawText(QRect(c["stops_l"], 0, c["stops_r"] - c["stops_l"], H), R | VC, "STOPS")
        p.drawText(QRect(c["stint_l"], 0, c["stint_r"] - c["stint_l"], H), R | VC, "PIT")
        p.drawText(QRect(c["gap_l"], 0, c["gap_r"] - c["gap_l"], H), R | VC, "GAP")
        p.drawText(QRect(c["call_l"], 0, 150, H), L | VC, "NOTE")


class ClassHeader(QFrame):
    def __init__(self, cls: str, count: int, profile=None):
        super().__init__()
        self.setFixedHeight(34)
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 12, 16, 4); lay.setSpacing(9)
        spine = QFrame(); spine.setFixedSize(3, 14)
        spine.setStyleSheet(f"background:{_spine(cls, profile)};")
        name = QLabel(cls)
        f = QFont(SANS, 12, QFont.Weight.Medium); f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        name.setFont(f); name.setStyleSheet(f"color:{TXT};")
        cnt = QLabel(str(count)); cnt.setFont(QFont(SANS, 11)); cnt.setStyleSheet(f"color:{MUTE};")
        lay.addWidget(spine); lay.addWidget(name); lay.addWidget(cnt); lay.addStretch(1)


class ExpanderRow(QFrame):
    """The '▸ +N more' / '▾ show less' accordion toggle at the foot of a class."""
    def __init__(self, cls: str, hidden: int, collapsed: bool, on_click):
        super().__init__()
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on_click = on_click
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 0, 15, 0); lay.setSpacing(0)
        arrow = "▸" if collapsed else "▾"
        txt = f"{arrow}  +{hidden} more" if collapsed else f"{arrow}  show less"
        lab = QLabel(txt); lab.setFont(QFont(SANS, 11)); lab.setStyleSheet(f"color:{MUTE};")
        lab.setContentsMargins(38, 0, 0, 0)        # align under the driver column
        lay.addWidget(lab); lay.addStretch(1)

    def mousePressEvent(self, _e):
        self._on_click()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        p.fillRect(0, 0, self.width(), 1, QColor(LINE))


# ── "while you were away": split the full ranked event list into the headline moves
# (shown by default — the count VARIES with how impactful the stint was) and the
# remainder (the "+N smaller moves", revealed on demand by clicking the footer). ──
HEADLINE_RANK = 43     # >= this shows by default: every typed event down to RETIRED(60),
                       # plus in-class position moves of >= 3 spots (move rank = 40 + spots)
MIN_SHOWN = 3          # never look empty on a quiet stint
MAX_SHOWN = 8          # never run away on a chaotic one


def _split_moves(events: list) -> tuple[list, list]:
    """events arrive ranked-desc from catchup.summarize. Headline = the impactful ones
    (rank >= HEADLINE_RANK), clamped to [MIN_SHOWN, MAX_SHOWN]; remainder = the rest."""
    n = sum(1 for e in events if e.rank >= HEADLINE_RANK)
    n = min(max(n, MIN_SHOWN), MAX_SHOWN, len(events))
    return events[:n], events[n:]


class _ClickLabel(QLabel):
    """A QLabel whose click can either fire a handler and swallow the event, or (when it
    has no handler) fall through to the parent so the card still dismisses normally."""
    def __init__(self):
        super().__init__("")
        self._on_click = None

    def set_click(self, fn):
        self._on_click = fn
        self.setCursor(Qt.CursorShape.PointingHandCursor if fn
                       else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e):
        if self._on_click:
            e.accept(); self._on_click()      # expand — do NOT bubble to dismiss
        else:
            e.ignore()                        # bubble to the card → dismiss


class CatchupCard(QFrame):
    """The 'while you were away' welcome-back card (direction B). A normal-flow child of
    the central widget, raised + centred over the body (not a QDialog) so it fades up in
    place and any keypress clears it. Speaks once, then gets out of the way — the opposite
    of always-on wallpaper. After dismiss the board's inline SINCE trail carries on."""

    def __init__(self, parent, on_dismiss, on_resize=None):
        super().__init__(parent)
        self._on_dismiss = on_dismiss
        self._on_resize = on_resize           # re-center after the card grows (expand)
        self._remainder: list = []            # the "smaller moves" held back for expand
        self.setFixedWidth(360)
        self.setStyleSheet(f"background:{BG}; border:1px solid #2A323D; border-radius:14px;")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 18, 20, 16); self.lay.setSpacing(0)

        self._title = QLabel("While you were away")
        self._title.setFont(QFont(SANS, 16, QFont.Weight.Medium))
        self._title.setStyleSheet(f"color:{TXT};")
        self._meta = QLabel("")
        self._meta.setFont(QFont(MONO, 11)); self._meta.setStyleSheet(f"color:{FAINT};")
        self.lay.addWidget(self._title); self.lay.addSpacing(2); self.lay.addWidget(self._meta)
        self.lay.addSpacing(14)
        # rows live in a height-capped scroll area so an expanded list stays on-screen
        self._rowsw = QWidget(); self._rowsw.setStyleSheet("background:transparent;")
        self._rowbox = QVBoxLayout(self._rowsw)
        self._rowbox.setContentsMargins(0, 0, 0, 0); self._rowbox.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._rowsw); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("background:transparent;")
        self.lay.addWidget(self._scroll)
        self.lay.addSpacing(12)
        self._foot = _ClickLabel()
        self._foot.setFont(QFont(SANS, 11)); self._foot.setStyleSheet(f"color:{FAINT};")
        self.lay.addWidget(self._foot)

        self._fx = QGraphicsOpacityEffect(self); self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity")
        self._anim.setDuration(260)
        self._anim.setStartValue(0.0); self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()

    def _clear_rows(self):
        while self._rowbox.count():
            it = self._rowbox.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
            elif it.layout():
                lay = it.layout()
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget():
                        sub.widget().setParent(None)

    def _add_event_row(self, ev):
        row = QHBoxLayout(); row.setSpacing(10); row.setContentsMargins(0, 5, 0, 5)
        tone = TONE_HEX.get(ev.tone, TXT)
        tag = QLabel(f"#{ev.car}" if ev.car else "⚑")
        tag.setFont(QFont(MONO, 13)); tag.setFixedWidth(44)
        tag.setStyleSheet(f"color:{tone};")
        body = ev.text + (f"  ·  {ev.sub}" if ev.sub else "")
        lab = QLabel(); lab.setFont(QFont(SANS, 13))
        lab.setStyleSheet(f"color:{TXT};"); lab.setWordWrap(False)
        lab.setText(QFontMetrics(QFont(SANS, 13)).elidedText(
            body, Qt.TextElideMode.ElideRight, 268))   # card width − tag − padding
        row.addWidget(tag); row.addWidget(lab, 1)
        self._rowbox.addLayout(row)

    def _set_footer(self):
        """Footer is a real affordance: blue + clickable when there are held-back moves to
        reveal, dim + inert (falls through to dismiss) once everything is shown."""
        n = len(self._remainder)
        if n > 0:
            self._foot.setText(f"▸  show {n} smaller move" + ("" if n == 1 else "s"))
            self._foot.setStyleSheet(f"color:{BLUE};")
            self._foot.set_click(self._expand)
        else:
            self._foot.setText("press any key")
            self._foot.setStyleSheet(f"color:{FAINT};")
            self._foot.set_click(None)

    def _size_scroll(self):
        """Fit the scroll area to its rows, capped at ~60% of the window so a long expanded
        list scrolls instead of running off-screen."""
        self._rowsw.adjustSize()
        content = self._rowsw.sizeHint().height()
        parent = self.parent()
        maxh = int((parent.height() if parent else 700) * 0.6)
        self._scroll.setFixedHeight(max(40, min(content, maxh)))

    def show_brief(self, headline, remainder, meta_text):
        self._clear_rows()
        self._remainder = list(remainder)
        self._meta.setText(meta_text)
        for ev in headline:
            self._add_event_row(ev)
        self._set_footer()
        self._size_scroll()
        self.adjustSize()
        if self._on_resize:
            self._on_resize()
        self.show(); self.raise_()
        self._anim.stop(); self._anim.start()

    def _expand(self):
        for ev in self._remainder:
            self._add_event_row(ev)
        self._remainder = []
        self._set_footer()
        self._size_scroll()
        self.adjustSize()
        if self._on_resize:
            self._on_resize()

    def mousePressEvent(self, _e):
        self._on_dismiss()


class LegendCard(QFrame):
    """On-demand key for the board's symbols & colours (press ? or the header ?). Same
    overlay pattern as CatchupCard — a raised, centred child of the central widget that
    fades up and is cleared by any key or click. Static content, built once; documents the
    now-restrained calm vocabulary so it's learnable. Swatches use the live palette so the
    key matches what's actually on screen."""

    def __init__(self, parent, on_dismiss):
        super().__init__(parent)
        self._on_dismiss = on_dismiss
        self.setFixedWidth(600)
        self.setStyleSheet(f"background:{BG}; border:1px solid #2A323D; border-radius:14px;")
        lay = QVBoxLayout(self); lay.setContentsMargins(22, 18, 22, 16); lay.setSpacing(0)
        title = QLabel("Key"); title.setFont(QFont(SANS, 16, QFont.Weight.Medium))
        title.setStyleSheet(f"color:{TXT};")
        lay.addWidget(title); lay.addSpacing(10)
        body = QLabel(); body.setTextFormat(Qt.TextFormat.RichText)
        body.setFont(QFont(SANS, 12)); body.setWordWrap(True); body.setText(self._html())
        lay.addWidget(body)
        foot = QLabel("press any key to close"); foot.setFont(QFont(SANS, 11))
        foot.setStyleSheet(f"color:{FAINT};"); lay.addSpacing(12); lay.addWidget(foot)

        self._fx = QGraphicsOpacityEffect(self); self.setGraphicsEffect(self._fx)
        self._anim = QPropertyAnimation(self._fx, b"opacity")
        self._anim.setDuration(260)
        self._anim.setStartValue(0.0); self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()

    @staticmethod
    def _html() -> str:
        def sw(s, c): return f'<span style="color:{c};">{s}</span>'
        def sec(name):
            return (f'<tr><td colspan="2" style="padding:10px 0 3px 0; color:{FAINT};'
                    f' font-size:10px;">{name}</td></tr>')
        def row(sample, meaning):
            return (f'<tr><td style="padding:2px 14px 2px 0;">{sample}</td>'
                    f'<td style="padding:2px 0; color:{DIM};">{meaning}</td></tr>')
        def col(blocks):  # one column = an inner table of its section blocks
            return (f'<table cellspacing="0" cellpadding="0">{"".join(blocks)}</table>')

        left = [
            sec("POSITION"),
            row(sw("P1", GREEN), "class leader"),
            row(sw("P4", MUTE), "dimmed = in the pits"),
            sec("NET — effective position"),
            row(sw("▲P3", GREEN), "will GAIN to P3 when the cycle resolves"),
            row(sw("▼P6", RED), "will DROP to P6 (owes a stop)"),
            row(sw("—", FAINT), "settled — only the most out-of-place car/class"),
            sec("PIT"),
            row(sw("● IN PIT", BLUE), "stopped in the box now"),
            row(sw("OUT", GREEN), "out lap — back up to speed"),
            sec("GAP"),
            row(sw("LEAD", MUTE), "class leader"),
            row(sw("+1.234", TXT), "seconds behind class leader"),
            row(sw("+1L", MUTE), "a lap or more down"),
        ]
        right = [
            sec("OTHER COLUMNS"),
            row(sw("STOPS", FAINT), "pit-stop count (faint when 0)"),
            row(sw("CALL", AMBER), "penalty / DQ — the only on-row call"),
            sec("MOTION"),
            row(sw("breath", BLUE) + " " + sw("breath", AMBER),
                "a row glows ~2s when something moves — blue = its NET position just "
                "changed, amber = a penalty landed. (Up/down lives in the ▲P/▼P arrow.)"),
            row(sw("SINCE", AMBER_SOFT), "~30s tag on return — what moved while away"),
            sec("CLASS SPINES"),
            row(sw("■ GTP", SPINE["GTP"]) + " " + sw("■ LMP2", SPINE["LMP2"]),
                "left-edge colour = class"),
            row(sw("■ GTDPRO", SPINE["GTDPRO"]) + " " + sw("■ GTD", SPINE["GTD"]), ""),
            sec("RAIL — right column"),
            row(sw("RACE AT A GLANCE", DIM), "actionable calls (penalty/undercut/catch)"),
            row(sw("RACE CONTROL", DIM), "official messages"),
            row(sw("DUE TO PIT", DIM), "cars near their fuel window"),
            row(sw("BATTLES", DIM), "close in-class fights · ▼ = gap closing"),
        ]
        return (f'<table cellspacing="0" cellpadding="0"><tr>'
                f'<td valign="top" style="padding-right:30px;">{col(left)}</td>'
                f'<td valign="top">{col(right)}</td></tr></table>')

    def show_card(self):
        self.adjustSize(); self.show(); self.raise_()
        self._anim.stop(); self._anim.start()

    def mousePressEvent(self, _e):
        self._on_dismiss()


# ── main window ──────────────────────────────────────────────────────────────
class CalmDashboard(QMainWindow):
    def __init__(self, force_oid=None):
        super().__init__()
        self.setWindowTitle("IMSA Strategy — Catch-up")
        self.resize(1180, 760)
        self.poller = dash.Poller(force_oid=force_oid)
        self._rows: dict[str, RowWidget] = {}
        self._collapsed: dict[str, bool] = {}      # per-class accordion state (default collapsed)
        self._profile = series_profiles.IMSA       # active series' palette; set for real in refresh()
        self._last = None                          # cached (rows, camap) for re-render on toggle
        # ── catch-up ("while you were away") state ──
        self._snap_now = None                      # latest Snapshot (refreshed each tick)
        self._rc_now: list = []                    # latest raw race-control rows
        self._mark = None                          # Snapshot frozen when you stepped away
        self._mark_rc: set = set()                 # RC message-keys already seen at the mark
        self._mark_wall = 0.0                      # wall time of the mark (for the "N min" line)
        self._pending_show = False                 # window re-activated → show brief next refresh
        self._manual_armed = False                 # a manual M mark is waiting (2nd M shows it)
        self._badge_events: dict = {}              # car → Event for the lingering inline trail
        self._badge_until = 0.0                    # wall time the trail expires
        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.card = CatchupCard(self.centralWidget(), self._dismiss_card,
                                on_resize=self._position_card)
        self.legend = LegendCard(self.centralWidget(), self._dismiss_legend)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(dash.REFRESH_MS)
        self.refresh()

    def _build_ui(self):
        central = QWidget(); central.setStyleSheet(f"background:{BG};")
        root = QVBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # header
        header = QFrame(); header.setFixedHeight(58)
        header.setStyleSheet(f"background:{BG}; border-bottom:1px solid {HEAD};")
        hl = QHBoxLayout(header); hl.setContentsMargins(18, 0, 20, 0); hl.setSpacing(14)
        self.flag = QLabel("—"); self.flag.setFixedHeight(26)
        self.flag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont(SANS, 11, QFont.Weight.Medium); f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.4)
        self.flag.setFont(f)

        seg = QFrame(); seg.setStyleSheet(f"background:#171C24; border-radius:8px;")
        sl = QHBoxLayout(seg); sl.setContentsMargins(3, 3, 3, 3); sl.setSpacing(4)
        self.tab_strat = QPushButton("Strategy"); self.tab_time = QPushButton("Timing  ↗")
        for b, on in ((self.tab_strat, True), (self.tab_time, False)):
            b.setFlat(True); b.setFixedHeight(26)
            b.setStyleSheet(
                f"QPushButton{{color:{'#FFFFFF' if on else MUTE}; background:{'#2A323D' if on else 'transparent'};"
                f"border:none; border-radius:6px; padding:0 14px; font-size:12px;}}")
            sl.addWidget(b)
        self.tab_time.clicked.connect(self._timing_stub)

        # race name + lap (restored from mockup) — centre block
        self.event = QLabel(""); self.event.setFont(QFont(SANS, 13))
        self.event.setStyleSheet(f"color:{TXT};")
        self.eventsub = QLabel(""); self.eventsub.setFont(QFont(SANS, 10))
        self.eventsub.setStyleSheet(f"color:{MUTE};")
        evbox = QVBoxLayout(); evbox.setSpacing(2)
        evbox.addWidget(self.event); evbox.addWidget(self.eventsub)

        self.clock = QLabel("--:--:--")
        self.clock.setFont(QFont(MONO, 26, QFont.Weight.Medium))
        self.clock.setStyleSheet(f"color:{TXT};")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sub = QLabel(""); self.sub.setFont(QFont(SANS, 13, QFont.Weight.Medium))
        self.sub.setStyleSheet(f"color:{DIM};")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        clockbox = QVBoxLayout(); clockbox.setSpacing(1)
        clockbox.addWidget(self.clock); clockbox.addWidget(self.sub)

        # on-demand legend toggle (zero chrome until needed; ? hotkey also works)
        self.help_btn = QPushButton("?"); self.help_btn.setFlat(True)
        self.help_btn.setFixedSize(26, 26)
        self.help_btn.setToolTip("Key — what the symbols & colours mean (?)")
        self.help_btn.setStyleSheet(
            f"QPushButton{{color:{MUTE}; background:#171C24; border:none; border-radius:13px;"
            f"font-size:14px;}} QPushButton:hover{{color:{TXT};}}")
        self.help_btn.clicked.connect(self._toggle_legend)

        # F1 race/session picker — launch a live feed or historical replay by
        # clicking instead of hand-editing CLI args (backlog item 10)
        self.f1_btn = QPushButton("F1 ▾"); self.f1_btn.setFlat(True)
        self.f1_btn.setFixedHeight(26)
        self.f1_btn.setToolTip("Pick an F1 live feed or replay session")
        self.f1_btn.setStyleSheet(
            f"QPushButton{{color:{MUTE}; background:#171C24; border:none; border-radius:6px;"
            f"padding:0 10px; font-size:12px;}} QPushButton:hover{{color:{TXT};}}")
        self.f1_btn.clicked.connect(self._open_session_picker)

        hl.addWidget(self.flag); hl.addWidget(seg)
        hl.addSpacing(18); hl.addLayout(evbox); hl.addStretch(1)
        hl.addWidget(self.f1_btn); hl.addSpacing(8)
        hl.addWidget(self.help_btn); hl.addSpacing(12); hl.addLayout(clockbox)
        root.addWidget(header)

        # body: left list | right rail
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            f"QScrollArea{{background:{BG}; border-right:1px solid #2A323D;}}"
            f"QScrollBar:vertical{{background:transparent; width:6px; margin:0;}}"
            f"QScrollBar::handle:vertical{{background:#2A323D; border-radius:3px; min-height:30px;}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
            f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}")
        self.listw = QWidget(); self.listw.setStyleSheet(f"background:{BG};")
        self.listl = QVBoxLayout(self.listw); self.listl.setContentsMargins(0, 4, 0, 14); self.listl.setSpacing(0)
        self.listl.addStretch(1)
        self.scroll.setWidget(self.listw)

        # left column = fixed ColumnHeader stacked above the scrolling list
        leftcol = QWidget(); leftcol.setStyleSheet(f"background:{BG};")
        leftv = QVBoxLayout(leftcol); leftv.setContentsMargins(0, 0, 0, 0); leftv.setSpacing(0)
        self.colheader = ColumnHeader()
        leftv.addWidget(self.colheader); leftv.addWidget(self.scroll, 1)

        self.rail = QFrame(); self.rail.setFixedWidth(238)
        self.rail.setStyleSheet(f"background:{RAIL};")
        self.raill = QVBoxLayout(self.rail); self.raill.setContentsMargins(15, 13, 15, 13); self.raill.setSpacing(0)

        body.addWidget(leftcol, 1); body.addWidget(self.rail)
        self.race_body = QWidget(); self.race_body.setLayout(body)

        # F1 knockout-qualifying swaps in here in place of the race body (see
        # refresh()) — a session either has race-shaped standings or quali
        # segment data, never both, so a stacked page is a clean either/or.
        self.quali_panel = dq.QualiListPanel()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.race_body)
        self.stack.addWidget(self.quali_panel)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

    def _timing_stub(self):
        self.sub.setText("Timing view — coming next")

    def _open_session_picker(self):
        dlg = session_picker.SessionPickerDialog(self)
        if dlg.exec() == session_picker.SessionPickerDialog.DialogCode.Accepted:
            self._f1_proc = dlg.proc   # keep the QProcess alive for the app's lifetime
            self.poller = dash.Poller(force_oid=dlg.force_oid, series=dlg.series)

    # ---- F1 knockout qualifying (Q1/Q2/Q3) ----
    def _is_quali_session(self) -> bool:
        """A session either has race-shaped standings or quali segment data,
        never both — quali_status having a row for this oid is the signal."""
        oid, conn = self.poller.last_oid, self.poller.conn
        if not oid or conn is None:
            return False
        try:
            row = conn.execute(
                "SELECT 1 FROM quali_status WHERE session_oid=? LIMIT 1", (oid,)
            ).fetchone()
        except sqlite3.Error:
            return False   # older DB without the quali tables — definitely not quali
        return row is not None

    def _refresh_quali(self):
        self.stack.setCurrentWidget(self.quali_panel)
        qctx, qcars = quali.analyse(self.poller.conn, self.poller.last_oid)
        self._render_header_quali(qctx)
        self.quali_panel.render(qctx, qcars)

    def _render_header_quali(self, qctx):
        neutral = "#3A4150"
        if qctx is None:
            self.flag.setText("  Q  ")
            self.flag.setStyleSheet(f"background:{neutral}; color:#FFFFFF; border-radius:5px;")
            self.sub.setText("waiting for data")
            return
        self.flag.setText(f"  {qctx.segment}  ")
        self.flag.setStyleSheet(f"background:{neutral}; color:#FFFFFF; border-radius:5px;")
        self.event.setText(qctx.event)
        self.eventsub.setText("Qualifying")
        remaining = max(0, qctx.segment_total_s - qctx.segment_elapsed_s)
        if qctx.is_finished:
            self.clock.setText("—")
            self.sub.setText("SEGMENT COMPLETE")
        else:
            self.clock.setText(f"{remaining // 60}:{remaining % 60:02d}")
            self.sub.setText(f"CUT: TOP {qctx.advance_n}" if qctx.advance_n is not None
                             else f"{qctx.entries} CARS")

    # ---- refresh ----
    def refresh(self):
        res = self.poller.poll(0)
        if res is None:
            self.sub.setText("waiting for data")
            return

        if self._is_quali_session():
            self._refresh_quali()
            return

        ctx, cars, rc, _age, trend = res
        self.stack.setCurrentWidget(self.race_body)
        self._profile = ctx.profile          # active series' palette/single-class-ness
        self.colheader.set_profile(self._profile)
        # freeze the diff-relevant slice every tick so a mark (manual or on blur) and the
        # return brief always have current data to compare against
        self._snap_now = catchup.snapshot(ctx, cars)
        self._rc_now = list(rc or [])
        rows = dash._build_rows(ctx, cars, trend, None, poller=self.poller)
        # patch missing team/driver from static entry list
        for r in rows:
            if r.is_header:
                continue
            e = _ENTRIES.get(r.car)
            roster = e.get("drivers") if e else None
            if e and (not r.team or r.team == "?"):
                r.team = e["team"]
            # show only the driver currently in the car, as "F. Lastname"
            r.driver = _short_driver(r.driver, roster)
        camap = {c.car_number: c for c in cars}
        self._current_lap = ctx.current_lap
        # per-class "out of sequence on stops?" — net overlay only speaks when the
        # field has diverged on stop counts (a pit cycle is in play). Include cars up
        # to 1 lap down: during a cycle the cars that just pitted often read +1L
        # transiently, and they're exactly the ones we must keep. Only genuinely
        # retired/lapped-out cars (≥2 down) are dropped so one DNF can't force it on.
        stops_by_cls: dict[str, list] = {}
        for c in cars:
            if (c.laps_down or 0) <= 1:
                stops_by_cls.setdefault(c.car_class, []).append(c.stops)
        self._cycle_active = {}
        for cls, counts in stops_by_cls.items():
            # a real cycle is a CLUSTER out of sequence — need ≥2 cars off the modal
            # stop count, so one early/odd stop (or a start-from-pit) can't flip the
            # whole class on and fill it with early-race projection noise.
            modal = max(set(counts), key=counts.count)
            self._cycle_active[cls] = sum(1 for s in counts if s != modal) >= 2
        # attention budget: cap how many rows may light a NET overlay per class so a
        # field-wide signal can never paint the whole board (the "christmas tree")
        self._budget_net = self._highlight_budget(rows, camap)
        self._last = (rows, camap)
        self._render_header(ctx)
        self._render_list(rows, camap)
        self._render_rail(rows, rc, camap)

        # window re-activated (or just marked) → build + show the catch-up brief now that
        # we have a fresh snapshot to diff the mark against
        if self._pending_show and self._mark is not None:
            self._pending_show = False
            self._maybe_show_catchup()

    # ---- catch-up ("while you were away") ----
    @staticmethod
    def _rc_key(row) -> str:
        raw = (row["message"] if hasattr(row, "keys") else row[1]) or ""
        return " ".join(raw.split()).lower()

    def _mark_moment(self, manual: bool):
        """Freeze the field as a comparison point. Auto on window blur, or manual via M.
        We also remember which RC lines were already on screen, so the return brief shows
        only penalties/retirements logged WHILE away (robust to RC ts units)."""
        if self._snap_now is None:
            return
        self._mark = self._snap_now
        self._mark_rc = {self._rc_key(r) for r in self._rc_now}
        self._mark_wall = time.time()
        self._manual_armed = manual
        if manual:
            self.sub.setText(f"MARKED · LAP {getattr(self, '_current_lap', 0)} · press M to review")

    def _maybe_show_catchup(self):
        """Diff the mark against now and, if anything meaningful happened, show the card
        and arm the inline trail. Nothing happened → stay calm, clear the mark silently."""
        mark = self._mark
        self._mark = None
        self._manual_armed = False
        if mark is None or self._snap_now is None:
            return
        rc_since = [r for r in self._rc_now if self._rc_key(r) not in self._mark_rc]
        events = catchup.summarize(mark, self._snap_now, rc_since)
        if not events:
            return
        mins = max(1, round((time.time() - self._mark_wall) / 60))
        laps = f"laps {mark.lap}→{self._snap_now.lap}"
        ncau = max(0, self._snap_now.caution_count - mark.caution_count)
        meta = f"{mins} min · {laps}" + (f" · {ncau} caution" if ncau else "")
        headline, remainder = _split_moves(events)
        self._position_card()
        self.card.show_brief(headline, remainder, meta)
        # arm the lingering inline trail for the cars that changed (direction C)
        self._badge_events = {ev.car: ev for ev in events if ev.car}
        self._badge_until = time.time() + catchup.BADGE_TTL_S
        if self._last:
            self._render_list(*self._last)

    def _dismiss_card(self):
        if self.card.isVisible():
            self.card.hide()

    def _position_card(self):
        central = self.centralWidget()
        if central is None:
            return
        self.card.adjustSize()
        cw, ch = central.width(), central.height()
        w, h = self.card.width(), self.card.height()
        self.card.move(max(0, (cw - w) // 2), max(58, (ch - h) // 2 - 20))

    def _position_legend(self):
        central = self.centralWidget()
        if central is None:
            return
        self.legend.adjustSize()
        cw, ch = central.width(), central.height()
        w, h = self.legend.width(), self.legend.height()
        self.legend.move(max(0, (cw - w) // 2), max(58, (ch - h) // 2 - 20))

    def _toggle_legend(self):
        if self.legend.isVisible():
            self._dismiss_legend()
            return
        self._dismiss_card()                  # never stack the two overlays
        self._position_legend()
        self.legend.show_card()
        self.setFocus()                       # so the next key dismisses it

    def _dismiss_legend(self):
        if self.legend.isVisible():
            self.legend.hide()

    def changeEvent(self, e):
        from PyQt6.QtCore import QEvent
        if e.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow():
                self._mark_moment(manual=False)            # stepped away → snapshot silently
            elif self._mark is not None:
                self._pending_show = True                  # came back → brief on next refresh
        super().changeEvent(e)

    def keyPressEvent(self, e):
        if self.legend.isVisible():                        # any key closes the legend
            self._dismiss_legend(); return
        if self.card.isVisible():                          # any key clears the welcome-back card
            self._dismiss_card(); return
        if e.key() == Qt.Key.Key_Question:                 # ? → toggle the symbol/colour key
            self._toggle_legend(); return
        if e.key() == Qt.Key.Key_M:
            if self._manual_armed and self._mark is not None:
                self._manual_armed = False                 # 2nd M → review changes since the mark
                self._pending_show = True
                self.refresh()
            else:
                self._mark_moment(manual=True)             # 1st M → mark this moment
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.card.isVisible():
            self._position_card()
        if self.legend.isVisible():
            self._position_legend()

    def _render_header(self, ctx):
        bg, label = dash.FLAG_STYLE.get(ctx.flag, ("#3A4150", ctx.flag or "—"))
        self.flag.setText(f"  {label}  ")
        self.flag.setStyleSheet(f"background:{bg}; color:#FFFFFF; border-radius:5px;")
        self.event.setText(ctx.event or "")
        self.eventsub.setText(ctx.session_name or "")
        if ctx.is_finished:
            self.clock.setText("—")
            self.sub.setText("🏁 FINISHED")
        else:
            if ctx.is_race and ctx.final_type == "BY_TIME" and ctx.remaining_s:
                s = int(ctx.remaining_s)
                self.clock.setText(f"{s//3600}:{(s%3600)//60:02d}:{s%60:02d}")
            else:
                self.clock.setText("—")
            self.sub.setText(f"LAP {ctx.current_lap}")

    TOP_N = 5                  # per-class row cap before the "+N more" accordion (multi-class)
    TOP_N_SINGLE_CLASS = 30    # F1-style single-class grid: show the whole field by default

    @property
    def _top_n(self) -> int:
        return self.TOP_N_SINGLE_CLASS if self._profile.single_class else self.TOP_N

    def _toggle_class(self, cls):
        self._collapsed[cls] = not self._collapsed.get(cls, True)
        self._anim_cls = cls            # animate just this class on the coming render
        if self._last:
            self._render_list(*self._last)

    def _highlight_budget(self, rows, camap) -> set:
        """The structural fix for the "christmas tree": one global cap on how many rows
        may show a NET overlay per class. Among the cars that ALREADY pass the net gates
        (real cycle, trustworthy gap, not in box, net≠track — mirrored from _row_vm),
        admit only the most out-of-position — top BUDGET_PER_CLASS by |net−track|.
        Penalty/DQ and the leader marker are NOT capped (rare/bounded, must never be
        suppressed); only the unbounded signal (net moves) is governed. BUDGET_PER_CLASS
        is a live config knob (0 = pure monochrome)."""
        try:
            config.CONFIG.reload_if_changed()
            per_class = int(config.CONFIG.BUDGET_PER_CLASS)
        except Exception:
            per_class = 1
        cand: dict = {}
        for r in rows:
            if r.is_header:
                continue
            ca = camap.get(r.car)
            if ca is None or not r.net or not r.trk or r.net == r.trk:
                continue
            if (ca.track_status or "") in dash.BOX_STATES:
                continue
            if not self._cycle_active.get(r.cls, True):
                continue
            gap_ok = ((ca.laps_down or 0) == 0
                      and ((ca.effective_pos_in_class or ca.pos_in_class) == 1
                           or (ca.class_gap_ms is not None
                               and 0.05 <= ca.class_gap_ms < 600_000)))
            if not gap_ok:
                continue
            cand.setdefault(r.cls, []).append((abs(r.net - r.trk), r.car))
        allowed = set()
        for cls, lst in cand.items():
            lst.sort(reverse=True)
            for _, car in lst[:max(0, per_class)]:
                allowed.add(car)
        return allowed

    def _render_list(self, rows, camap):
        # the lingering catch-up trail: active only until it expires (then auto-clears)
        badges = self._badge_events if time.time() < self._badge_until else {}
        holders = getattr(self, "_holders", None)
        if holders is None:
            holders = self._holders = {}      # per-class collapsible-row containers (persistent)

        # detach top-level layout items. Cached RowWidgets (self._rows) and per-class holders
        # (self._holders) survive because those dicts keep Python refs — and a holder must
        # NEVER be destroyed while it still parents cached rows, or Qt cascade-deletes them
        # and the breath-state cache goes stale.
        while self.listl.count():
            it = self.listl.takeAt(0)
            if it.widget():
                it.widget().setParent(None)

        # segment the flat row list into [(class, [car_rows])]
        groups, cur = [], None
        for r in rows:
            if r.is_header:
                cur = (r.cls, [])
                groups.append(cur)
            elif cur is not None:
                cur[1].append(r)

        # which class (if any) the user just toggled — only that one animates this render;
        # data-tick re-renders set the height directly so they never re-trigger motion.
        anim_cls = getattr(self, "_anim_cls", None)
        self._anim_cls = None

        def _row_widget(r, cls):
            rw = self._rows.get(r.car)
            if rw is None:
                rw = RowWidget(); self._rows[r.car] = rw
            ca = camap.get(r.car)
            active = getattr(self, "_cycle_active", {}).get(cls, True)
            allow = r.car in getattr(self, "_budget_net", set())
            vm = _row_vm(r, ca, getattr(self, "_current_lap", 0),
                         cycle_active=active, since=badges.get(r.car), allow_net=allow,
                         profile=self._profile)
            rw.update_row(vm)
            return rw

        single_class = self._profile.single_class
        for cls, cars in groups:
            # TRACK-led board: show in real on-track order (gaps then climb down the list)
            cars.sort(key=lambda r: (r.trk if r.trk else 99))
            if not single_class:
                # a single-class series (F1) has exactly one group — the "F1 · 20 cars"
                # header is pure redundancy on a board that's already flat; the field
                # header (event name) already tells you what you're looking at.
                self.listl.addWidget(ClassHeader(cls, str(len(cars)), self._profile))
            collapsed = self._collapsed.get(cls, True)

            top_n = self._top_n
            for r in cars[:top_n]:
                rw = _row_widget(r, cls); rw.setParent(self.listw)
                self.listl.addWidget(rw)

            extra = cars[top_n:]
            if extra:
                # the collapsible rows live in a height-clamped PERSISTENT holder so
                # expand/collapse can be animated; rows are reused (breath state stays warm)
                # and just clipped to height 0 when collapsed.
                holder = holders.get(cls)
                if holder is None:
                    holder = QWidget(self.listw)
                    hl = QVBoxLayout(holder)
                    hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(0)
                    holders[cls] = holder
                hl = holder.layout()
                while hl.count():                       # detach prior rows (persist in _rows)
                    w = hl.takeAt(0).widget()
                    if w:
                        w.setParent(None)
                for r in extra:
                    hl.addWidget(_row_widget(r, cls))
                full = len(extra) * ROW_H
                target = full if not collapsed else 0
                if cls == anim_cls:
                    start = 0 if not collapsed else full     # animate from the old state
                    holder.setMaximumHeight(start)
                    a = QPropertyAnimation(holder, b"maximumHeight", holder)
                    a.setDuration(360)
                    a.setEasingCurve(QEasingCurve.Type.InOutCubic)
                    a.setStartValue(start); a.setEndValue(target)
                    a.start()
                    self._accordion_anim = a                 # keep a ref (else GC'd mid-flight)
                else:
                    holder.setMaximumHeight(target)
                self.listl.addWidget(holder)
                self.listl.addWidget(
                    ExpanderRow(cls, len(extra), collapsed,
                                lambda c=cls: self._toggle_class(c)))
        self.listl.addStretch(1)

    def _rail_label(self, text):
        lab = QLabel(text); f = QFont(SANS, 10)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        lab.setFont(f); lab.setStyleSheet(f"color:{FAINT};")
        return lab

    def _render_rail(self, rows, rc, camap):
        while self.raill.count():
            it = self.raill.takeAt(0)
            if it.widget():
                it.widget().setParent(None)

        # WATCH — built from real alerts (penalty / undercut / catch), in net order
        watch = []
        for r in rows:
            if r.is_header:
                continue
            al = _alert_for(camap.get(r.car))
            if al:
                watch.append((r, al))
        head = QHBoxLayout()
        head.addWidget(self._rail_label("RACE AT A GLANCE")); head.addStretch(1)
        cnt = QLabel(str(len(watch))); cnt.setFont(QFont(SANS, 10)); cnt.setStyleSheet(f"color:{MUTE};")
        head.addWidget(cnt)
        hw = QWidget(); hw.setLayout(head); self.raill.addWidget(hw)
        self.raill.addSpacing(8)

        for r, (text, col) in watch[:6]:
            if col == AMBER:  accent, bg = "#C98A2E", "#1A150A"
            elif col == BLUE: accent, bg = "#2E6FF0", "#0C1320"
            else:             accent, bg = "#3A4350", "#141921"
            chip = QFrame()
            chip.setStyleSheet(f"background:{bg}; border-left:2px solid {accent};")
            cl = QVBoxLayout(chip); cl.setContentsMargins(10, 7, 10, 7); cl.setSpacing(2)
            t = QLabel(f"#{r.car}  {text}"); t.setFont(QFont(SANS, 12)); t.setStyleSheet(f"color:{col};")
            s = QLabel(r.cls); s.setFont(QFont(SANS, 10)); s.setStyleSheet(f"color:{MUTE};")
            cl.addWidget(t); cl.addWidget(s)
            self.raill.addWidget(chip); self.raill.addSpacing(6)

        # DUE TO PIT — the fuel/pit-window roster (stint estimate, NOT the unreliable VFT
        # flag). Moved off the board so a field-wide window can't paint every row; listed
        # here, compact and class-coloured, in track order. Bounded.
        due = []
        for r in rows:
            if r.is_header:
                continue
            ca = camap.get(r.car)
            if ca is None or (ca.track_status or "") in dash.BOX_STATES:
                continue
            if getattr(ca, "fuel_due", None) == "due":
                due.append(r)
        self.raill.addSpacing(10)
        self.raill.addWidget(self._rail_label("DUE TO PIT"))
        self.raill.addSpacing(6)
        if due:
            by_class = {}
            for r in due:
                by_class.setdefault(r.cls, []).append(r)
            for cls in sorted(by_class, key=lambda k: (dash.CLASS_ORDER.get(k, 9), k)):
                cdue = sorted(by_class[cls], key=lambda r: r.trk_overall or 99)
                cars = [r.car for r in cdue[:10]]
                cextra = len(cdue) - 10
                text = " ".join(cars)
                if cextra > 0:
                    text += f" +{cextra}"
                dl = QLabel(f'<span style="color:{_spine(cls, self._profile)};">{cls}</span>  {text}')
                dl.setTextFormat(Qt.TextFormat.RichText)
                dl.setWordWrap(True); dl.setFont(QFont(MONO, 11))
                self.raill.addWidget(dl)
        else:
            dn = QLabel("none"); dn.setFont(QFont(SANS, 11)); dn.setStyleSheet(f"color:{MUTE};")
            self.raill.addWidget(dn)

        # RACE CONTROL
        self.raill.addSpacing(10)
        self.raill.addWidget(self._rail_label("RACE CONTROL"))
        self.raill.addSpacing(6)
        rc_box = QLabel(); rc_box.setTextFormat(Qt.TextFormat.RichText); rc_box.setWordWrap(False)
        rc_box.setFont(QFont(SANS, 11))
        # colour reserved for what matters: penalties loud, a rescind reads as relief,
        # everything kept-but-quiet (reviews / yellow-cause incidents) stays dim.
        RC_COLOR = {"penalty": AMBER, "dq": RED, "rescinded": GREEN,
                    "retired": TXT, "flag": TXT,
                    "review": DIM, "warning": DIM, "incident": DIM}
        lines = []
        for msg, _tier, kind in race_control.feed(rc, limit=6):
            c = RC_COLOR.get(kind, DIM)
            short = msg[:40] + "…" if len(msg) > 40 else msg
            lines.append(f'<div style="color:{c}; padding:2px 0;">{short}</div>')
        rc_box.setText("".join(lines) or f'<span style="color:{MUTE};">no messages</span>')
        self.raill.addWidget(rc_box)

        # BATTLES — the close in-class fights actually worth watching (replaces the old
        # "ON TRACK overall", which was just the GTP leaders). Adjacent same-class,
        # same-lap pairs within BATTLE_GAP_S; a ▼ marks a gap that's been closing (reuses
        # the catching trend gate so it can't fire on caution bunching).
        self.raill.addSpacing(14)
        self.raill.addWidget(self._rail_label("BATTLES"))
        self.raill.addSpacing(6)
        try:
            gap_s = float(config.CONFIG.BATTLE_GAP_S)
            trend_laps = int(config.CONFIG.CATCH_TREND_LAPS)
        except Exception:
            gap_s, trend_laps = 2.0, 3
        oid = getattr(self.poller, "last_oid", None)
        cur_lap = getattr(self, "_current_lap", 0)
        by_cls: dict = {}
        for r in rows:
            if r.is_header:
                continue
            ca = camap.get(r.car)
            if ca is None or ca.class_gap_ms is None or (ca.laps_down or 0) > 0:
                continue
            by_cls.setdefault(r.cls, []).append(ca)
        battles = []
        for cls, cas in by_cls.items():
            cas.sort(key=lambda c: c.class_gap_ms)        # track order within class
            for ahead, chaser in zip(cas, cas[1:]):
                gap = (chaser.class_gap_ms or 0) - (ahead.class_gap_ms or 0)
                if 0 < gap <= gap_s * 1000:
                    closing = bool(oid and calculator._gap_closing(
                        oid, chaser.car_number, ahead.car_number, cur_lap, trend_laps))
                    battles.append((gap, cls, ahead.car_number, chaser.car_number, closing))
        battles.sort(key=lambda b: b[0])                  # tightest first
        if battles:
            for gap, cls, a, c, closing in battles[:6]:
                arrow = f' <span style="color:{AMBER};">▼</span>' if closing else ""
                lab = QLabel(
                    f'<span style="color:{_spine(cls, self._profile)};">{cls}</span>  '
                    f'#{a} <span style="color:{MUTE};">▸</span> #{c}  '
                    f'<span style="color:{TXT};">{gap/1000:.1f}s</span>{arrow}')
                lab.setTextFormat(Qt.TextFormat.RichText); lab.setFont(QFont(MONO, 11))
                self.raill.addWidget(lab); self.raill.addSpacing(2)
        else:
            bn = QLabel("none close"); bn.setFont(QFont(SANS, 11)); bn.setStyleSheet(f"color:{MUTE};")
            self.raill.addWidget(bn)
        self.raill.addStretch(1)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", "--oid", dest="session", default=None,
                    help="pin to a session OID (e.g. 'stream' for a replay) instead "
                         "of auto-picking the most-recently-written one")
    args, _ = ap.parse_known_args()
    app = QApplication.instance() or QApplication(sys.argv)
    w = CalmDashboard(force_oid=args.session)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
