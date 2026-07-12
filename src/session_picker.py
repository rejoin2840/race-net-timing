"""
session_picker.py — IMSA / WEC session picker dialog.

Opened from a header button in dashboard_calm.py. Lets you pick a series,
then Live vs a historical Replay, by clicking instead of hand-editing CLI
args. Launches the matching adapter as a managed QProcess:

  IMSA  Live    -> alkameldp.py
  IMSA  Replay  -> replay.py ARCHIVE.zip --stream --speed N
  WEC   Live    -> wec_live.py  [Epic 8; --record raw-capture is on by default]
  WEC   Replay  -> replay.py ARCHIVE.zip --stream --speed N

On accept, the caller reads `series` (the OID isn't knowable ahead of time
for Live, and replay.py auto-detects series from the archive manifest, so the
caller scopes Poller(series=...) and lets latest_session() find it).
"""

from datetime import datetime
import os
import pathlib

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON = ROOT / "venv" / "bin" / "python"
DATA_DIR = ROOT / "data"

BG     = "#10141A"
PANEL  = "#171C24"
LINE   = "#3C4756"
TXT    = "#F5F7FA"
DIM    = "#C2CAD6"
MUTE   = "#8A94A2"
ACCENT = "#8CB9F2"
ERROR  = "#F2706F"


class SessionPickerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Session")
        self.setFixedSize(420, 320)
        self.setStyleSheet(f"QDialog{{background:{BG};}}")

        self.proc: "QProcess | None" = None
        self.force_oid: "str | None" = None
        self.series: "str | None" = None
        self._archive_path: "str | None" = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        seriesrow = QHBoxLayout()
        self.imsa_series_radio = QRadioButton("IMSA")
        self.wec_series_radio = QRadioButton("WEC")
        self.imsa_series_radio.setChecked(True)
        for r in (self.imsa_series_radio, self.wec_series_radio):
            r.setStyleSheet(f"QRadioButton{{color:{TXT}; font-size:13px; font-weight:600;}}")
        series_group = QButtonGroup(self)
        series_group.addButton(self.imsa_series_radio)
        series_group.addButton(self.wec_series_radio)
        seriesrow.addWidget(self.imsa_series_radio)
        seriesrow.addWidget(self.wec_series_radio)
        seriesrow.addStretch(1)
        root.addLayout(seriesrow)

        moderow = QHBoxLayout()
        self.live_radio = QRadioButton("Live")
        self.replay_radio = QRadioButton("Replay")
        self.replay_radio.setChecked(True)
        for r in (self.live_radio, self.replay_radio):
            r.setStyleSheet(f"QRadioButton{{color:{TXT}; font-size:13px;}}")
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.live_radio)
        mode_group.addButton(self.replay_radio)
        moderow.addWidget(self.live_radio)
        moderow.addWidget(self.replay_radio)
        moderow.addStretch(1)
        root.addLayout(moderow)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_imsa_live_page())   # 0: IMSA Live
        self.stack.addWidget(self._build_replay_page())      # 1: Archive Replay (shared)
        self.stack.addWidget(self._build_wec_live_page())    # 2: WEC Live stub

        self.live_radio.toggled.connect(lambda on: on and self._sync_stack())
        self.replay_radio.toggled.connect(lambda on: on and self._sync_stack())
        self.imsa_series_radio.toggled.connect(lambda on: on and self._sync_stack())
        self.wec_series_radio.toggled.connect(lambda on: on and self._sync_stack())
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

    def _sync_stack(self):
        is_live = self.live_radio.isChecked()
        if self.imsa_series_radio.isChecked():
            self.stack.setCurrentIndex(0 if is_live else 1)
        else:  # WEC
            self.stack.setCurrentIndex(2 if is_live else 1)

    # ── IMSA Live page ───────────────────────────────────────────────────
    def _build_imsa_live_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        note = QLabel("Connects to the official live IMSA timing feed.")
        note.setStyleSheet(f"color:{DIM}; font-size:12px;")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        launch = QPushButton("Launch Live Feed")
        launch.setStyleSheet(self._btn_style(primary=True))
        launch.clicked.connect(self._launch_imsa_live)
        v.addWidget(launch)
        return w

    # ── Archive Replay page — Timing71 .zip, shared for IMSA and WEC ────
    def _build_replay_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(6)

        lbl = QLabel("Timing71 archive (.zip)")
        lbl.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        v.addWidget(lbl)

        filerow = QHBoxLayout()
        self.archive_file_label = QLabel("No file chosen")
        self.archive_file_label.setStyleSheet(f"color:{DIM}; font-size:12px;")
        self.archive_file_label.setWordWrap(True)
        choose = QPushButton("Choose…")
        choose.setStyleSheet(self._btn_style(primary=False))
        choose.clicked.connect(self._choose_archive)
        filerow.addWidget(self.archive_file_label, 1)
        filerow.addWidget(choose)
        v.addLayout(filerow)

        speedlbl = QLabel("Speed")
        speedlbl.setStyleSheet(f"color:{MUTE}; font-size:11px;")
        v.addWidget(speedlbl)
        self.replay_speed_combo = QComboBox()
        self.replay_speed_combo.addItems(["1×", "10×", "30×", "60×", "120×"])
        self.replay_speed_combo.setCurrentText("60×")
        self.replay_speed_combo.setStyleSheet(self._combo_style())
        v.addWidget(self.replay_speed_combo)

        v.addStretch(1)
        launch = QPushButton("Launch Replay")
        launch.setStyleSheet(self._btn_style(primary=True))
        launch.clicked.connect(self._launch_replay)
        v.addWidget(launch)
        return w

    # ── WEC Live page ────────────────────────────────────────────────────
    def _build_wec_live_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        note = QLabel(
            "Connects to the FIA WEC live timing feed (Griiip SignalR).\n"
            "Auto-discovers the active WEC session. Use --record to capture "
            "raw frames for offline replay.")
        note.setStyleSheet(f"color:{DIM}; font-size:12px;")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        launch = QPushButton("Launch Live Feed")
        launch.setStyleSheet(self._btn_style(primary=True))
        launch.clicked.connect(self._launch_wec_live)
        v.addWidget(launch)
        return w

    # ── helpers ──────────────────────────────────────────────────────────
    def _choose_archive(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Timing71 archive", str(ROOT), "Timing71 archives (*.zip)")
        if path:
            self._archive_path = path
            self.archive_file_label.setText(pathlib.Path(path).name)

    def _btn_style(self, primary=False) -> str:
        bg = ACCENT if primary else PANEL
        fg = "#0D1117" if primary else TXT
        hover = "#9FC6F5" if primary else "#20272F"
        return (f"QPushButton{{background:{bg}; color:{fg}; border:none; border-radius:6px;"
                f"padding:8px; font-size:13px; font-weight:600; min-height:20px;}}"
                f"QPushButton:hover{{background:{hover};}}"
                f"QPushButton:disabled{{background:{PANEL}; color:{MUTE};}}")

    def _combo_style(self) -> str:
        return (f"QComboBox{{background:{PANEL}; color:{TXT}; border:1px solid {LINE};"
                f"border-radius:6px; padding:6px; font-size:13px; min-height:20px;}}")

    def _background_scraper_since(self, series: str) -> "str | None":
        """Returns start-time string if a live scraper lock file exists and its
        process is still running; None if no lock or stale."""
        lock = DATA_DIR / f".{series}_live.lock"
        try:
            text = lock.read_text().strip()
        except OSError:
            return None
        parts = text.split(" ", 1)
        if len(parts) != 2:
            return None
        try:
            pid = int(parts[0])
        except ValueError:
            return None
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return None
        try:
            started = datetime.fromisoformat(parts[1])
            return started.strftime("%H:%M")
        except ValueError:
            return "an earlier time"

    def _confirm_double_launch(self, series: str) -> bool:
        since = self._background_scraper_since(series)
        if since is None:
            return True
        resp = QMessageBox.question(
            self, "Already running",
            f"A {series.upper()} live feed already appears to be running in the "
            f"background (started {since}) — probably weekend_conductor.py.\n\n"
            "Just open the dashboard to view it; you don't need to launch again.\n"
            "Launching a second copy can cause duplicate pit-stop detection.\n\n"
            "Launch anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return resp == QMessageBox.StandardButton.Yes

    # ── launchers ────────────────────────────────────────────────────────
    def _make_proc(self) -> QProcess:
        proc = QProcess(self)
        proc.setWorkingDirectory(str(ROOT))
        # inherit the dashboard's stdout/stderr instead of an unread pipe buffer —
        # otherwise a crashed feed/replay child dies with its traceback invisible
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)
        return proc

    def _launch_imsa_live(self):
        if not self._confirm_double_launch("imsa"):
            return
        self.proc = self._make_proc()
        self.proc.start(str(PYTHON), [str(ROOT / "src" / "alkameldp.py")])
        self.series = "imsa"
        self.force_oid = None
        self.status.setText("Launching live feed…")
        self.accept()

    def _launch_wec_live(self):
        if not self._confirm_double_launch("wec"):
            return
        self.proc = self._make_proc()
        args = [str(ROOT / "src" / "wec_live.py"), "--record",
                str(DATA_DIR / f"wec_raw_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl.gz")]
        self.proc.start(str(PYTHON), args)
        self.series = "wec"
        self.force_oid = None
        self.status.setText("Launching WEC live feed…")
        self.accept()

    def _launch_replay(self):
        if not self._archive_path:
            self.status.setStyleSheet(f"color:{ERROR}; font-size:11px;")
            self.status.setText("Choose a Timing71 archive first.")
            return
        speed = self.replay_speed_combo.currentText().rstrip("×")
        self.force_oid = None
        self.series = "wec" if self.wec_series_radio.isChecked() else "imsa"
        self.proc = self._make_proc()
        self.proc.start(str(PYTHON), [
            str(ROOT / "src" / "replay.py"), self._archive_path,
            "--stream", "--speed", speed,
        ])
        self.status.setText(f"Launching {self.series} replay…")
        self.accept()
