"""
session_picker.py — F1 / IndyCar race/session picker dialog.

Opened from a header button in dashboard_calm.py. Lets you pick a series,
then Live vs a historical Replay, by clicking instead of hand-editing CLI
args. Launches the matching adapter as a managed QProcess:

  F1      Live    -> f1_live.py
  F1      Replay  -> replay_f1.py YEAR GP SESSION --stream --speed N
  IndyCar Replay  -> replay.py ARCHIVE.zip --stream --speed N
  IndyCar Live    -> not built yet (racecontrol.indycar.com feed unbuilt)

On accept, the caller reads `force_oid` (F1 Replay — pins the dashboard's
Poller to the exact session the subprocess will write; FastF1's schedule
lets us compute the OID before launching) or `series` (F1 Live, IndyCar
Replay — the OID isn't knowable ahead of time for Live, and isn't worth
threading through for IndyCar's single-archive-at-a-time replay, so the
caller scopes Poller(series=...) and lets latest_session() find it instead).

F1 session-of-the-weekend is selected by POSITION (1-5), not by name/code —
FastF1 supports numeric session identifiers, and top-level session names
have drifted across seasons (e.g. 2023's "Sprint Shootout" vs later years'
"Sprint Qualifying" for the same slot). Picking by position sidesteps that
entirely. IndyCar has no schedule API equivalent (no FastF1-for-IndyCar) —
the only replay source is a Timing71 archive the user downloads by hand, so
its picker is just a file chooser rather than a year/GP/session hierarchy.
"""

from datetime import datetime
import pathlib

import fastf1

from PyQt6.QtCore import QProcess, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
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
        self.setWindowTitle("Session")
        self.setFixedSize(420, 380)
        self.setStyleSheet(f"QDialog{{background:{BG};}}")

        self.proc: "QProcess | None" = None
        self.force_oid: "str | None" = None
        self.series: "str | None" = None
        self._fetch: "_ScheduleFetch | None" = None
        self._schedule = None   # cached DataFrame for the currently-selected year
        self._indycar_path: "str | None" = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        seriesrow = QHBoxLayout()
        self.f1_series_radio = QRadioButton("F1")
        self.indycar_series_radio = QRadioButton("IndyCar")
        self.f1_series_radio.setChecked(True)
        for r in (self.f1_series_radio, self.indycar_series_radio):
            r.setStyleSheet(f"QRadioButton{{color:{TXT}; font-size:13px; font-weight:600;}}")
        series_group = QButtonGroup(self)
        series_group.addButton(self.f1_series_radio)
        series_group.addButton(self.indycar_series_radio)
        seriesrow.addWidget(self.f1_series_radio)
        seriesrow.addWidget(self.indycar_series_radio)
        seriesrow.addStretch(1)
        root.addLayout(seriesrow)

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
        self.stack.addWidget(self._build_live_page())          # 0: F1 Live
        self.stack.addWidget(self._build_replay_page())        # 1: F1 Replay
        self.stack.addWidget(self._build_indycar_live_page())  # 2: IndyCar Live
        self.stack.addWidget(self._build_indycar_replay_page())  # 3: IndyCar Replay
        self.live_radio.toggled.connect(lambda on: on and self._sync_stack())
        self.replay_radio.toggled.connect(lambda on: on and self._sync_stack())
        self.f1_series_radio.toggled.connect(lambda on: on and self._sync_stack())
        self.indycar_series_radio.toggled.connect(lambda on: on and self._sync_stack())
        self._sync_stack()

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

    def _sync_stack(self):
        """Route the (series, Live/Replay) combo to its stack page. Replay is
        the useful default pre-live-validation for both series."""
        is_f1 = self.f1_series_radio.isChecked()
        is_live = self.live_radio.isChecked()
        if is_f1:
            self.stack.setCurrentIndex(0 if is_live else 1)
        else:
            self.stack.setCurrentIndex(2 if is_live else 3)

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

    # ── IndyCar Live page (stub — no live feed built yet, see indycar_data_sources.md) ──
    def _build_indycar_live_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        note = QLabel(
            "Live IndyCar timing isn't built yet — racecontrol.indycar.com\n"
            "has no public API and needs to be reverse-engineered.\n"
            "Use Replay with a Timing71 archive for now.")
        note.setStyleSheet(f"color:{DIM}; font-size:12px;")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        launch = QPushButton("Coming soon")
        launch.setEnabled(False)
        launch.setStyleSheet(self._btn_style(primary=False))
        v.addWidget(launch)
        return w

    # ── IndyCar Replay page — a Timing71 archive picked by hand (no schedule
    # API exists for IndyCar the way FastF1 provides one for F1) ────────────
    def _build_indycar_replay_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(6)

        lbl = QLabel("Timing71 archive (.zip)")
        lbl.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        v.addWidget(lbl)

        filerow = QHBoxLayout()
        self.indycar_file_label = QLabel("No file chosen")
        self.indycar_file_label.setStyleSheet(f"color:{DIM}; font-size:12px;")
        self.indycar_file_label.setWordWrap(True)
        choose = QPushButton("Choose…")
        choose.setStyleSheet(self._btn_style(primary=False))
        choose.clicked.connect(self._choose_indycar_archive)
        filerow.addWidget(self.indycar_file_label, 1)
        filerow.addWidget(choose)
        v.addLayout(filerow)

        speedlbl = QLabel("Speed")
        speedlbl.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        v.addWidget(speedlbl)
        self.indycar_speed_combo = QComboBox()
        self.indycar_speed_combo.addItems(["1×", "10×", "30×", "60×", "120×"])
        self.indycar_speed_combo.setCurrentText("60×")
        self.indycar_speed_combo.setStyleSheet(self._combo_style())
        v.addWidget(self.indycar_speed_combo)

        v.addStretch(1)
        launch = QPushButton("Launch Replay")
        launch.setStyleSheet(self._btn_style(primary=True))
        launch.clicked.connect(self._launch_indycar_replay)
        v.addWidget(launch)
        return w

    def _choose_indycar_archive(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Timing71 archive", str(ROOT), "Timing71 archives (*.zip)")
        if path:
            self._indycar_path = path
            self.indycar_file_label.setText(pathlib.Path(path).name)

    # ── styling ──────────────────────────────────────────────────────────
    def _btn_style(self, primary=False) -> str:
        bg = ACCENT if primary else PANEL
        fg = "#0D1117" if primary else TXT
        hover = "#9FC6F5" if primary else "#20272F"
        return (f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:6px;"
                f"padding:8px; font-size:13px; font-weight:600; min-height:20px;}}"
                f"QPushButton:hover{{background:{hover};}}")

    def _combo_style(self) -> str:
        return (f"QComboBox{{background:{PANEL}; color:{TXT}; border:1px solid {LINE};"
                f"border-radius:6px; padding:6px; font-size:13px; min-height:20px;}}")

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

    def _launch_indycar_replay(self):
        if not self._indycar_path:
            self.status.setStyleSheet(f"color:{ERROR}; font-size:11px;")
            self.status.setText("Choose a Timing71 archive first.")
            return
        speed = self.indycar_speed_combo.currentText().rstrip("×")

        # No FastF1-equivalent schedule API for IndyCar, so (unlike F1 Replay)
        # the OID isn't computable ahead of the subprocess — scope by series
        # and let latest_session() find replay.py's default "stream" oid,
        # same as F1 Live does for its own not-known-ahead-of-time OID.
        self.force_oid = None
        self.series = "indycar"

        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.start(str(PYTHON), [
            str(ROOT / "src" / "replay.py"), self._indycar_path,
            "--stream", "--speed", speed,
        ])
        self.status.setText("Launching IndyCar replay…")
        self.accept()
