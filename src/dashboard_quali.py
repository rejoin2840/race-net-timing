"""
dashboard_quali.py — F1 knockout-qualifying (Q1/Q2/Q3) cut-line board.

QualiListPanel is the reusable piece: a scrollable ranked list + cut line, with
a render(ctx, cars) method. It's embedded directly in CalmDashboard's body (see
dashboard_calm.CalmDashboard — swapped in via a QStackedWidget whenever the
active session is a qualifying segment) AND wrapped standalone by QualiBoard
below for launching against a replay DB on its own.

Qualifying's shape — rank by best lap THIS SEGMENT, with a hard cut line — has
nothing in common with RowWidget's race-shaped columns (pit/net/gap-to-class-
leader), so this gets its own simple row painter rather than reusing RowWidget.
Palette, fonts, and the F1 team table are reused from dashboard_calm rather
than duplicated.

Run standalone:
  QT_QPA_PLATFORM=offscreen honoured for headless screenshots.
  ./venv/bin/python src/dashboard_quali.py --db data/f1_quali_replay.db --oid <oid>
"""
import sqlite3
import sys

from PyQt6.QtCore import Qt, QRect, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                             QMainWindow, QScrollArea, QVBoxLayout, QWidget)

import dashboard_calm as dc   # reuse palette, fonts, F1 team table
import quali

ROW_H = 40


def _fmt_ms(ms) -> str:
    if ms is None:
        return "—"
    s = ms / 1000
    return f"{int(s // 60)}:{s % 60:06.3f}"


def _fmt_gap(ms, leader_ms) -> str:
    if ms is None or leader_ms is None:
        return "—"
    if ms <= leader_ms:
        return "POLE"
    return f"+{(ms - leader_ms) / 1000:.3f}"


class QualiRow(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(ROW_H)
        self.vm: dict | None = None
        self.f_rank = QFont(dc.MONO, 18, QFont.Weight.Medium)
        self.f_num  = QFont(dc.SANS, 13, QFont.Weight.DemiBold)
        self.f_team = QFont(dc.SANS, 12, QFont.Weight.Medium)
        self.f_time = QFont(dc.MONO, 14)
        self.f_gap  = QFont(dc.MONO, 12)

    def update_row(self, vm: dict):
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

        p.fillRect(self.rect(), QColor(dc.BG if vm["advancing"] else "#191317"))
        p.fillRect(0, H - 1, W, 1, QColor(dc.LINE))

        # RANK — green if currently inside the cut, red if in the drop zone
        p.setFont(self.f_rank); p.setPen(QColor(vm["rank_color"]))
        p.drawText(QRect(18, 0, 44, H), L | VC, vm["rank_text"])

        # CAR identity: #num · [team-colour tick] TLA · team, dims in the drop zone
        x = 74
        p.setFont(self.f_num); p.setPen(QColor(dc.TXT if vm["advancing"] else dc.FAINT))
        p.drawText(QRect(x, 0, 46, H), L | VC, vm["car_num"])
        x += 46

        if vm["tla_color"]:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(vm["tla_color"]))
            p.drawRoundedRect(QRect(x, (H - 18) // 2, 4, 18), 2, 2)
            p.setBrush(Qt.BrushStyle.NoBrush)
            x += 10
        p.setFont(self.f_team)
        p.setPen(QColor(vm["tla_color"] or dc.DIM) if vm["advancing"] else QColor(dc.FAINT))
        p.drawText(QRect(x, 0, 56, H), L | VC, vm["tla"])
        x += 56

        p.setFont(self.f_team); p.setPen(QColor(dc.MUTE if vm["advancing"] else dc.FAINT))
        fm_t = QFontMetrics(self.f_team)
        avail = max(0, W - 260 - x)
        team = fm_t.elidedText(vm["team"], Qt.TextElideMode.ElideRight, avail)
        p.drawText(QRect(x, 0, avail, H), L | VC, team)

        # TIME / GAP — right-aligned, dims in the drop zone
        p.setFont(self.f_time); p.setPen(QColor(dc.TXT if vm["advancing"] else dc.MUTE))
        p.drawText(QRect(W - 260, 0, 110, H), R | VC, vm["time_text"])

        p.setFont(self.f_gap); p.setPen(QColor(vm["gap_color"]))
        p.drawText(QRect(W - 140, 0, 110, H), R | VC, vm["gap_text"])


class CutLine(QFrame):
    """The horizontal rule marking the elimination cut — drawn once, between the
    last advancing car and the first one currently in the drop zone."""
    def __init__(self, advance_n: int, entries: int):
        super().__init__()
        self.setFixedHeight(28)
        lay = QHBoxLayout(self); lay.setContentsMargins(18, 0, 18, 0); lay.setSpacing(10)
        line1 = QFrame(); line1.setFixedHeight(1); line1.setStyleSheet(f"background:{dc.RED};")
        lab = QLabel(f"CUT — TOP {advance_n} ADVANCE  ·  {entries - advance_n} ELIMINATED")
        lab.setFont(QFont(dc.SANS, 9, QFont.Weight.Medium))
        lab.setStyleSheet(f"color:{dc.RED};")
        line2 = QFrame(); line2.setFixedHeight(1); line2.setStyleSheet(f"background:{dc.RED};")
        lay.addWidget(line1, 1); lay.addWidget(lab); lay.addWidget(line2, 1)


class QualiListPanel(QWidget):
    """The reusable piece: scrollable ranked list + cut line. render(ctx, cars)
    is the only entry point a host window needs — it owns no DB/poll state of
    its own, so it slots into any refresh loop (standalone or embedded)."""
    def __init__(self):
        super().__init__()
        self._rows: dict[str, QualiRow] = {}
        self.setStyleSheet(f"background:{dc.BG};")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(f"QScrollArea{{background:{dc.BG}; border:none;}}")
        self.listw = QWidget(); self.listw.setStyleSheet(f"background:{dc.BG};")
        self.listl = QVBoxLayout(self.listw)
        self.listl.setContentsMargins(0, 4, 0, 14); self.listl.setSpacing(0)
        self.listl.addStretch(1)
        self.scroll.setWidget(self.listw)
        lay.addWidget(self.scroll, 1)

    def render(self, ctx, cars: list) -> None:
        while self.listl.count():
            it = self.listl.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        if ctx is None:
            self.listl.addStretch(1)
            return

        leader_ms = cars[0].best_lap_ms if cars and cars[0].best_lap_ms else None
        cut_drawn = False
        for c in cars:
            if (ctx.advance_n is not None and not cut_drawn
                    and c.rank == ctx.advance_n + 1):
                self.listl.addWidget(CutLine(ctx.advance_n, ctx.entries))
                cut_drawn = True
            rw = self._rows.get(c.car_number)
            if rw is None:
                rw = QualiRow(); self._rows[c.car_number] = rw
            info = dc._F1_TEAMS.get(c.car_number, {})
            vm = {
                "rank_text": str(c.rank),
                "rank_color": dc.GREEN if c.advancing else dc.RED,
                "car_num": f"#{c.car_number}",
                "tla": info.get("tla", ""), "tla_color": info.get("color"),
                "team": info.get("team", ""),
                "time_text": _fmt_ms(c.best_lap_ms),
                "gap_text": _fmt_gap(c.best_lap_ms, leader_ms),
                "gap_color": dc.MUTE,
                "advancing": c.advancing,
            }
            rw.update_row(vm)
            self.listl.addWidget(rw)
        self.listl.addStretch(1)


class QualiBoard(QMainWindow):
    """Standalone window: header (event/segment/clock) + a QualiListPanel, with
    its own DB poll. Used for launching the cut-line view directly against a
    replay DB; CalmDashboard embeds QualiListPanel itself instead of this class."""
    def __init__(self, db_path: str, oid: str):
        super().__init__()
        self.setWindowTitle("F1 Qualifying — Cut Line")
        self.resize(780, 860)
        self.db_path = db_path
        self.oid = oid
        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def _build_ui(self):
        central = QWidget(); central.setStyleSheet(f"background:{dc.BG};")
        root = QVBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        header = QFrame(); header.setFixedHeight(70)
        header.setStyleSheet(f"background:{dc.BG}; border-bottom:1px solid {dc.HEAD};")
        hl = QVBoxLayout(header); hl.setContentsMargins(18, 10, 18, 10); hl.setSpacing(2)
        self.event_lbl = QLabel(""); self.event_lbl.setFont(QFont(dc.SANS, 13))
        self.event_lbl.setStyleSheet(f"color:{dc.TXT};")
        self.segment_lbl = QLabel("")
        self.segment_lbl.setFont(QFont(dc.MONO, 20, QFont.Weight.Medium))
        self.segment_lbl.setStyleSheet(f"color:{dc.TXT};")
        hl.addWidget(self.event_lbl); hl.addWidget(self.segment_lbl)
        root.addWidget(header)

        self.panel = QualiListPanel()
        root.addWidget(self.panel, 1)
        self.setCentralWidget(central)

    def refresh(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
        except Exception:
            return
        ctx, cars = quali.analyse(conn, self.oid)
        conn.close()
        if ctx is None:
            self.segment_lbl.setText("waiting for data")
            self.panel.render(None, [])
            return

        self.event_lbl.setText(ctx.event)
        remaining = max(0, ctx.segment_total_s - ctx.segment_elapsed_s)
        clock = f"{remaining // 60}:{remaining % 60:02d}"
        status = "  ·  SEGMENT COMPLETE" if ctx.is_finished else f"   {clock} remaining"
        self.segment_lbl.setText(f"{ctx.segment}{status}")
        self.panel.render(ctx, cars)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/f1_quali_replay.db")
    ap.add_argument("--oid", required=True, help="session OID (see replay_f1_quali.py output)")
    args, _ = ap.parse_known_args()
    app = QApplication.instance() or QApplication(sys.argv)
    w = QualiBoard(args.db, args.oid)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
