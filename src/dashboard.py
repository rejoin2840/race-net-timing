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

import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QProcess, Qt, QTimer)
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel,
                             QMainWindow, QMessageBox, QPushButton, QSpinBox,
                             QSplitter, QTableView, QTextEdit, QVBoxLayout, QWidget)

import calculator
import config
import predictor
import race_control
import weather as weather_mod

from timing_table import (
    BG, PANEL, PANEL2, GRID, TEXT, TEXT_DIM, ACCENT, PURPLE, AMBER, RED, GREEN,
    PIT_LANE_STATES, BOX_STATES,
    CLASS_COLORS, CLASS_ORDER,
    COLS, COL_TIPS,
    C_NET, C_TREND, C_DELTA, C_CLS, C_LAST, C_BEST, C_PACE, C_GAP, C_WINDOW, C_STRAT,
    _class_color, _blend,
    Row, _strategy_text, _build_rows, StrategyModel,
    RUN_COLS, RunRow, _build_run_rows, RunningModel,
)
from poller import (
    Poller,
    ROOT, DB_PATH,
    REFRESH_MS, TREND_WINDOW_S, TREND_MIN_AGE_S, STALE_AFTER_S, MAX_DELAY_S,
)

PYTHON    = ROOT / "venv" / "bin" / "python"
SCRAPER   = ROOT / "src" / "alkameldp.py"
EVALUATOR = ROOT / "src" / "evaluator.py"
EVAL_EVERY_MS    = 45 * 60 * 1000   # background accuracy read cadence
WEATHER_EVERY_MS = 5 * 60 * 1000    # track-weather poll cadence

FLAG_STYLE = {
    "GF":  ("#0B7A33", "GREEN"),
    "YF":  ("#B58900", "YELLOW"),
    "FCY": ("#B58900", "FULL-COURSE YELLOW"),
    "SC":  ("#B58900", "SAFETY CAR"),
    "VSC": ("#B58900", "VIRTUAL SC"),
    "RF":  ("#A01020", "RED FLAG"),
    "CH":  ("#444444", "CHECKERED"),
}


# ── main window ─────────────────────────────────────────────────────────────
class Dashboard(QMainWindow):
    def __init__(self, force_oid=None, series=None):
        super().__init__()
        self.setWindowTitle("Overcut — Net Position")
        self.resize(1500, 880)
        self.poller = Poller(force_oid=force_oid, series=series)
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
        cc = r.cls_color
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
        if self.run_model.set_rows(_build_run_rows(cars, self.filter_cls, self.poller,
                                                   profile=ctx.profile)):
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
            cc = r.cls_color
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
