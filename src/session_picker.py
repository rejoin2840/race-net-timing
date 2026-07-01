"""
session_picker.py — F1 race/session picker dialog.

Opened from a header button in dashboard_calm.py. Lets you pick Live vs a
historical Replay (year / Grand Prix / session) by clicking, instead of
hand-editing CLI args, then launches the matching adapter as a managed
QProcess:

  Live    -> f1_live.py
  Replay  -> replay_f1.py YEAR GP SESSION --stream --speed N

On accept, the caller reads `force_oid` (Replay — pins the dashboard's
Poller to the exact session the subprocess will write) or `series`
(Live — the OID isn't knowable until the feed connects, so the caller
scopes Poller(series="f1") instead).

Session-of-the-weekend is selected by POSITION (1-5), not by name/code —
FastF1 supports numeric session identifiers, and top-level session names
have drifted across seasons (e.g. 2023's "Sprint Shootout" vs later years'
"Sprint Qualifying" for the same slot). Picking by position sidesteps that
entirely.
"""

from datetime import datetime
import pathlib

import fastf1

from PyQt6.QtCore import QProcess, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)

import replay_f1

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON = ROOT / "venv" / "bin" / "python"

BG     = "#10141A"
PANEL  = "#171C24"
LINE   = "#3C4756"
TXT    = "#F5F7FA"
DIM    = "#C2CAD6"
MUTE   = "#8A94A2"
ACCENT = "#8CB9F2"
ERROR  = "#F2706F"
SANS   = "Helvetica Neue"


class _ScheduleFetch(QThread):
    """Fetches fastf1.get_event_schedule(year) off the GUI thread — a cold
    call can take several seconds and must not freeze the dialog."""
    done = pyqtSignal(object)   # emits the DataFrame, or None on failure

    def __init__(self, year: int, parent=None):
        super().__init__(parent)
        self.year = year

    def run(self):
        try:
            sched = fastf1.get_event_schedule(self.year, include_testing=False)
            self.done.emit(sched)
        except Exception:
            self.done.emit(None)


class SessionPickerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("F1 Session")
        self.setFixedSize(420, 340)
        self.setStyleSheet(f"QDialog{{background:{BG};}}")

        self.proc: "QProcess | None" = None
        self.force_oid: "str | None" = None
        self.series: "str | None" = None
        self._fetch: "_ScheduleFetch | None" = None
        self._schedule = None   # cached DataFrame for the currently-selected year

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        moderow = QHBoxLayout()
        self.live_radio = QRadioButton("Live")
        self.replay_radio = QRadioButton("Replay")
        self.replay_radio.setChecked(True)
        for r in (self.live_radio, self.replay_radio):
            r.setStyleSheet(f"QRadioButton{{color:{TXT}; font-size:13px;}}")
        group = QButtonGroup(self)
        group.addButton(self.live_radio)
        group.addButton(self.replay_radio)
        moderow.addWidget(self.live_radio)
        moderow.addWidget(self.replay_radio)
        moderow.addStretch(1)
        root.addLayout(moderow)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_live_page())
        self.stack.addWidget(self._build_replay_page())
        self.live_radio.toggled.connect(lambda on: on and self.stack.setCurrentIndex(0))
        self.replay_radio.toggled.connect(lambda on: on and self.stack.setCurrentIndex(1))
        self.stack.setCurrentIndex(1)   # Replay is the useful default pre-live-validation

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        btnrow = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btnrow.addStretch(1)
        btnrow.addWidget(cancel)
        root.addLayout(btnrow)

        self._populate_years()
        self._on_year_changed()

    # ── Live page ────────────────────────────────────────────────────────
    def _build_live_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        note = QLabel(
            "Connects to the official live F1 timing feed.\n"
            "Requires F1TV auth — first time only, from Terminal:\n"
            "venv/bin/python -m fastf1 auth --authenticate f1tv")
        note.setStyleSheet(f"color:{DIM}; font-size:12px;")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        launch = QPushButton("Launch Live Feed")
        launch.setStyleSheet(self._btn_style(primary=True))
        launch.clicked.connect(self._launch_live)
        v.addWidget(launch)
        return w

    # ── Replay page ──────────────────────────────────────────────────────
    def _build_replay_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(6)

        self.year_combo = QComboBox()
        self.gp_combo = QComboBox()
        self.session_combo = QComboBox()
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["1×", "10×", "30×", "60×", "120×"])
        self.speed_combo.setCurrentText("60×")

        for label, combo in (("Year", self.year_combo), ("Grand Prix", self.gp_combo),
                             ("Session", self.session_combo), ("Speed", self.speed_combo)):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color:{MUTE}; font-size:11px;")
            v.addWidget(lbl)
            combo.setStyleSheet(self._combo_style())
            v.addWidget(combo)

        self.year_combo.currentIndexChanged.connect(self._on_year_changed)
        self.gp_combo.currentIndexChanged.connect(self._on_gp_changed)

        v.addStretch(1)
        launch = QPushButton("Launch Replay")
        launch.setStyleSheet(self._btn_style(primary=True))
        launch.clicked.connect(self._launch_replay)
        v.addWidget(launch)
        return w

    # ── styling ──────────────────────────────────────────────────────────
    def _btn_style(self, primary=False) -> str:
        bg = ACCENT if primary else PANEL
        fg = "#0D1117" if primary else TXT
        hover = "#9FC6F5" if primary else "#20272F"
        return (f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:6px;"
                f"padding:8px; font-size:13px; font-weight:600;}}"
                f"QPushButton:hover{{background:{hover};}}")

    def _combo_style(self) -> str:
        return (f"QComboBox{{background:{PANEL}; color:{TXT}; border:1px solid {LINE};"
                f"border-radius:6px; padding:6px;}}")

    # ── data population ──────────────────────────────────────────────────
    def _populate_years(self):
        cur = datetime.now().year
        self.year_combo.addItems([str(y) for y in range(cur, cur - 5, -1)])

    def _on_year_changed(self):
        if self.year_combo.currentIndex() < 0:
            return
        year = int(self.year_combo.currentText())
        self.gp_combo.clear()
        self.gp_combo.setEnabled(False)
        self.session_combo.clear()
        self.session_combo.setEnabled(False)
        self.status.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        self.status.setText(f"Loading {year} schedule…")
        self._fetch = _ScheduleFetch(year, self)
        self._fetch.done.connect(self._on_schedule_loaded)
        self._fetch.start()

    def _on_schedule_loaded(self, sched):
        self._schedule = sched
        if sched is None or sched.empty:
            self.status.setStyleSheet(f"color:{ERROR}; font-size:11px;")
            self.status.setText("Couldn't load schedule — check your network and try again.")
            return
        self.status.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        self.status.setText(f"Loaded {len(sched)} events.")
        done = sched[sched["EventDate"] < datetime.now()]
        rows = done if not done.empty else sched
        for _, row in rows.iterrows():
            label = f"{int(row['RoundNumber']):>2}  {row['EventName']}"
            self.gp_combo.addItem(label, int(row["RoundNumber"]))
        self.gp_combo.setEnabled(True)
        self._on_gp_changed()

    def _on_gp_changed(self):
        self.session_combo.clear()
        if self._schedule is None or self.gp_combo.currentIndex() < 0:
            return
        round_num = self.gp_combo.currentData()
        row = self._schedule[self._schedule["RoundNumber"] == round_num]
        if row.empty:
            return
        row = row.iloc[0]
        for i in range(1, 6):
            name = row.get(f"Session{i}")
            if isinstance(name, str) and name:
                self.session_combo.addItem(name, i)   # itemData = position, not name/code
        self.session_combo.setEnabled(self.session_combo.count() > 0)

    # ── launch ───────────────────────────────────────────────────────────
    def _launch_live(self):
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.start(str(PYTHON), [str(ROOT / "src" / "f1_live.py")])
        self.series = "f1"
        self.force_oid = None
        self.status.setText("Launching live feed…")
        self.accept()

    def _launch_replay(self):
        if self.gp_combo.currentIndex() < 0 or self.session_combo.currentIndex() < 0:
            self.status.setStyleSheet(f"color:{ERROR}; font-size:11px;")
            self.status.setText("Pick a Grand Prix and session first.")
            return
        year = int(self.year_combo.currentText())
        gp = self.gp_combo.currentData()               # round number
        session_pos = str(self.session_combo.currentData())   # "1".."5" — by position
        speed = self.speed_combo.currentText().rstrip("×")

        self.force_oid = replay_f1._make_oid(year, gp, session_pos)
        self.series = None

        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.start(str(PYTHON), [
            str(ROOT / "src" / "replay_f1.py"), str(year), str(gp), session_pos,
            "--stream", "--speed", speed,
        ])
        self.status.setText(f"Launching replay ({self.force_oid})…")
        self.accept()
