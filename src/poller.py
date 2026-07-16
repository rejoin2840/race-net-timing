"""
poller.py — DB polling and live-event tracking (engine-side, no UI imports).

Extracted from dashboard.py so the data layer can be used by both the PyQt6
display (dashboard.py / dashboard_calm.py) and the upcoming Electron web UI
without pulling in any Qt dependencies.
"""

import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import calculator
import config
import race_control as rc_classifier

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "race.db"

# Track-status buckets (engine-side home so this module stays Qt-free;
# timing_table re-imports them for the display layer).
# Broad (PIT_LANE_STATES) for the in-pit indicator; strict (BOX_STATES) for the
# stationary-in-box timer.
PIT_LANE_STATES = ("BOX", "IN_LAP", "OUT_LAP", "PIT", "STOPPED")
BOX_STATES      = ("BOX", "PIT", "STOPPED")

REFRESH_MS      = 2000   # DB poll / re-analyse cadence (ms)
TREND_WINDOW_S  = 300    # compare net position to this many seconds ago
TREND_MIN_AGE_S = 45     # need at least this much history before showing a trend arrow
STALE_AFTER_S   = 12     # data older than this → flagged stale
MAX_DELAY_S     = 120    # max broadcast-delay offset the UI can buffer


class Poller:
    def __init__(self, force_oid: Optional[str] = None, series: Optional[str] = None):
        # when set, always read this session instead of latest_session() — pins the
        # view to e.g. a replay 'stream' so a concurrent live scraper can't steal it
        self.force_oid = force_oid
        # when set (and force_oid isn't), scopes latest_session() to one series so a
        # live session for one series can't be pre-empted by a newer session in the same DB
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
        self.lapped_latch:  set[str]         = set() # cars latched at +1L to suppress S/F flicker

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

    def _ensure_net_analysis_table(self, conn) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS net_analysis (
                session_oid        TEXT,
                car_number         TEXT,
                net_position       INTEGER,
                net_gap_ms         REAL,
                net_gap_band_ms    REAL,
                class_gap_ms       REAL,
                laps_down          INTEGER,
                est_stops_left     INTEGER,
                penalty_s          REAL,
                penalty_note       TEXT,
                owes_driver_change INTEGER,
                net_settled        INTEGER,
                updated_at         TEXT,
                projected_finish   INTEGER,
                fuel_due           TEXT,
                catching           TEXT,
                catch_in_laps      REAL,
                strategy_note      TEXT,
                PRIMARY KEY (session_oid, car_number)
            )""")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(net_analysis)")}
        for col, ddl in [
            ("class_gap_ms",    "REAL"),
            ("laps_down",       "INTEGER"),
            ("projected_finish","INTEGER"),
            ("fuel_due",        "TEXT"),
            ("catching",        "TEXT"),
            ("catch_in_laps",   "REAL"),
            ("strategy_note",   "TEXT"),
            ("next_stop_ms",    "REAL"),
            ("next_stop_std_ms","REAL"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE net_analysis ADD COLUMN {col} {ddl}")

    def _ensure_rail_battles_table(self, conn) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rail_battles (
                session_oid    TEXT,
                rank           INTEGER,
                car_class      TEXT,
                car_ahead      TEXT,
                car_chaser     TEXT,
                gap_ms         REAL,
                closing        INTEGER,
                rate_s_per_lap REAL,
                updated_at     TEXT,
                PRIMARY KEY (session_oid, rank)
            )""")

    def _write_rail_battles(self, conn, oid: str, cars, cur_lap: int) -> None:
        try:
            gap_s        = float(config.CONFIG.BATTLE_GAP_S)
            trend_laps   = int(config.CONFIG.BATTLE_TREND_LAPS)
            min_drop_ms  = float(config.CONFIG.BATTLE_MIN_DROP_MS)
            noise_tol_ms = float(config.CONFIG.BATTLE_NOISE_TOL_MS)
        except Exception:
            gap_s, trend_laps, min_drop_ms, noise_tol_ms = 2.0, 3, 80, 120

        by_cls: dict = {}
        for ca in cars:
            if ca.laps_down or ca.class_gap_ms is None:
                continue
            by_cls.setdefault(ca.car_class, []).append(ca)

        battles = []
        for cls, cas in by_cls.items():
            cas.sort(key=lambda c: c.class_gap_ms)
            for ahead, chaser in zip(cas, cas[1:]):
                gap = (chaser.class_gap_ms or 0) - (ahead.class_gap_ms or 0)
                if 0 < gap <= gap_s * 1000:
                    closing = bool(oid and calculator._gap_closing(
                        oid, chaser.car_number, ahead.car_number, cur_lap, trend_laps,
                        min_drop_ms=min_drop_ms, noise_tol_ms=noise_tol_ms))
                    rate = (calculator._gap_close_rate_s(
                                oid, chaser.car_number, ahead.car_number, cur_lap, trend_laps)
                            if closing else None)
                    battles.append((gap, cls, ahead.car_number, chaser.car_number, closing, rate))
        battles.sort(key=lambda b: b[0])

        now = datetime.now(timezone.utc).isoformat()
        conn.execute("DELETE FROM rail_battles WHERE session_oid=?", (oid,))
        conn.executemany(
            """INSERT INTO rail_battles
               (session_oid, rank, car_class, car_ahead, car_chaser, gap_ms, closing, rate_s_per_lap, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [(oid, i, b[1], b[2], b[3], b[0], 1 if b[4] else 0, b[5], now)
             for i, b in enumerate(battles[:6])])
        conn.commit()

    def _ensure_rc_classification(self, conn, oid: str) -> None:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(race_control)")}
        if "tier" not in cols:
            conn.execute("ALTER TABLE race_control ADD COLUMN tier INTEGER")
        if "kind" not in cols:
            conn.execute("ALTER TABLE race_control ADD COLUMN kind TEXT")
        rows = conn.execute(
            "SELECT rowid, message FROM race_control WHERE session_oid=? AND tier IS NULL",
            (oid,)).fetchall()
        if rows:
            classified = []
            for rowid, msg in rows:
                tier, kind = rc_classifier.classify(msg)
                classified.append((tier, kind, rowid))
            conn.executemany(
                "UPDATE race_control SET tier=?, kind=? WHERE rowid=?", classified)
            conn.commit()

    def _write_session_computed(self, conn, oid: str, ctx) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_computed (
                session_oid  TEXT PRIMARY KEY,
                remaining_s  REAL,
                elapsed_s    REAL,
                updated_at   TEXT
            )""")
        now = datetime.now(timezone.utc).isoformat()
        # the calculator leaves both at 0.0 when the clock hasn't started (or
        # for BY_LAPS sessions) — write NULL so readers can't mistake a
        # not-yet-started race for a finished one
        remaining = getattr(ctx, 'remaining_s', None) or None
        elapsed   = getattr(ctx, 'elapsed_s', None) or None
        conn.execute("""
            INSERT INTO session_computed (session_oid, remaining_s, elapsed_s, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(session_oid) DO UPDATE SET
              remaining_s=excluded.remaining_s,
              elapsed_s=excluded.elapsed_s,
              updated_at=excluded.updated_at""",
            (oid, remaining, elapsed, now))
        conn.commit()

    def _write_net_analysis(self, conn, oid: str, cars) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (oid, c.car_number,
             c.net_position, c.net_gap_ms, c.net_gap_band_ms,
             c.class_gap_ms, c.laps_down,
             c.est_stops_left, c.penalty_s, getattr(c, 'penalty_note', None),
             1 if getattr(c, 'owes_driver_change', False) else 0,
             1 if getattr(c, 'net_settled', False) else 0,
             now,
             getattr(c, 'projected_finish', None),
             getattr(c, 'fuel_due', None),
             getattr(c, 'catching', None),
             getattr(c, 'catch_in_laps', None),
             getattr(c, 'strategy_note', None) or None,
             getattr(c, 'next_stop_ms', None),
             getattr(c, 'next_stop_std_ms', None))
            for c in cars
        ]
        conn.executemany("""
            INSERT INTO net_analysis
              (session_oid, car_number, net_position, net_gap_ms, net_gap_band_ms,
               class_gap_ms, laps_down,
               est_stops_left, penalty_s, penalty_note, owes_driver_change,
               net_settled, updated_at,
               projected_finish, fuel_due, catching, catch_in_laps, strategy_note,
               next_stop_ms, next_stop_std_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_oid, car_number) DO UPDATE SET
              net_position=excluded.net_position,
              net_gap_ms=excluded.net_gap_ms,
              net_gap_band_ms=excluded.net_gap_band_ms,
              class_gap_ms=excluded.class_gap_ms,
              laps_down=excluded.laps_down,
              est_stops_left=excluded.est_stops_left,
              penalty_s=excluded.penalty_s,
              penalty_note=excluded.penalty_note,
              owes_driver_change=excluded.owes_driver_change,
              net_settled=excluded.net_settled,
              updated_at=excluded.updated_at,
              projected_finish=excluded.projected_finish,
              fuel_due=excluded.fuel_due,
              catching=excluded.catching,
              catch_in_laps=excluded.catch_in_laps,
              strategy_note=excluded.strategy_note,
              next_stop_ms=excluded.next_stop_ms,
              next_stop_std_ms=excluded.next_stop_std_ms""",
            rows)
        conn.commit()

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
            # write computed net analysis back so Electron (or any reader) can see it
            try:
                self._ensure_net_analysis_table(conn)
                self._write_net_analysis(conn, oid, cars)
                self._ensure_rail_battles_table(conn)
                self._write_rail_battles(conn, oid, cars, ctx.current_lap)
                self._ensure_rc_classification(conn, oid)
                self._write_session_computed(conn, oid, ctx)
            except sqlite3.Error:
                pass  # non-fatal — Electron will just show dashes
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
                self.lapped_latch.discard(car)    # pit resets lapped display latch
            self.prev_stops[car] = cur
            self.prev_net[car] = c.net_position or 99

            # hysteresis: once window opens, keep it latched until next stop
            if c.pit_window_open:
                self.window_locked.add(car)

            # lapped-display latch: suppress +1L ↔ on-lap flicker at S/F crossings.
            # Once a car reads laps_down≥1, hold it there until the car genuinely pits
            # (latch cleared above) or goes 2+ laps down (own separate display bucket).
            if (c.laps_down or 0) >= 2:
                self.lapped_latch.discard(car)   # 2+ down is its own display state
            elif (c.laps_down or 0) == 1:
                self.lapped_latch.add(car)
            elif car in self.lapped_latch:
                c.laps_down = 1                  # enforce latch — don't flip to 0

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
