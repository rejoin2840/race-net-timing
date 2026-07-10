"""
SQLite persistence layer for the IMSA Al Kamel timing scraper.

Plain stdlib sqlite3 — no ORM. Five tables:

  sessions           one row per session OID (race metadata)
  session_entry      static per-car info (driver / team / vehicle / class)
  standings_current  latest snapshot per car (UPSERT every standings message)
  lap_history        append-only, one row per completed lap (catch-and-pass projection)
  pit_events         append-only, one row per pit stop (last-pit-lap for VFT tracker)

Idempotency is enforced by the schema, not by caller state:
  - lap_history  PK (session_oid, car_number, lap_number)  + INSERT OR IGNORE
  - pit_events   PK (session_oid, car_number, stop_number)  + INSERT OR IGNORE
So a reconnect that re-sends the full feed (added messages) can never create
duplicate history rows.

The Al Kamel feed exposes no per-lap or per-stop history of its own — both
tables are derived here by diffing the live standings / pit_info collections:
  - a new lap_history row    when lastLapTime advances (lap_number = laps)
  - a new pit_events  row    when the standings 'pits' counter increments
  - stop_duration_ms / pit_entry_hour_ms are back-filled from session_pit_info
    (lastPitTime is the duration of the most recent stop; lastPitHour its epoch)
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_DB_PATH = Path("data/race.db")

# flags that count as a caution period (mirror of calculator.CAUTION_FLAGS)
CAUTION_FLAGS = {"YF", "FCY", "CY", "SC", "VSC", "FCY1", "SCS"}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _scalar(v):
    """
    Make a value safe to bind into sqlite. Meteor EJSON can wrap numbers/dates
    as {'$type':..., '$value':...}; unwrap those, and reject anything that is
    still a container so a malformed field can never crash the writer.
    """
    if isinstance(v, dict):
        v = v.get("$value")
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return None  # lists / nested objects → drop rather than raise


def _parse_sectors(last_sectors: Optional[str]) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Al Kamel 'lastSectors' format:
      "1;24547;flag;flag;flag;flag;2;38776;...;3;40808;..."
    i.e. repeating groups of (sector_num, time_ms, 4 boolean flags).
    Return (s1_ms, s2_ms, s3_ms); any missing sector is None.
    """
    if not last_sectors:
        return None, None, None
    parts = last_sectors.split(";")
    out: dict[int, int] = {}
    i = 0
    while i + 1 < len(parts):
        sec = parts[i].strip()
        val = parts[i + 1].strip()
        if sec.isdigit() and val.lstrip("-").isdigit():
            out[int(sec)] = int(val)
        i += 6  # advance past the 4 trailing flag fields
    return out.get(1), out.get(2), out.get(3)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_oid   TEXT PRIMARY KEY,
    champ_name    TEXT,
    event_name    TEXT,
    session_name  TEXT,
    session_type  TEXT,
    series        TEXT,           -- racing series discriminator (imsa/f1/…) — routes profile
    first_seen    TEXT,
    last_seen     TEXT
);

CREATE TABLE IF NOT EXISTS session_entry (
    session_oid  TEXT,
    car_number   TEXT,
    name         TEXT,
    team         TEXT,
    vehicle      TEXT,
    class        TEXT,
    drivers      TEXT,          -- JSON array of co-drivers
    updated_at   TEXT,
    PRIMARY KEY (session_oid, car_number)
);

CREATE TABLE IF NOT EXISTS standings_current (
    session_oid       TEXT,
    car_number        TEXT,
    overall_position  INTEGER,
    pos_in_class      INTEGER,
    car_class         TEXT,
    laps              INTEGER,
    laps_behind       INTEGER,
    gap_ms            INTEGER,
    elapsed_ms        INTEGER,  -- cumulative race time (ms); diffs give real same-lap gaps
    last_lap_ms       INTEGER,
    best_lap_ms       INTEGER,
    best_lap_num      INTEGER,
    track_status      TEXT,
    pits              INTEGER,
    last_pit_lap      INTEGER,  -- car's own lap on which it last pitted (live-observed)
    fuel_pct          REAL,     -- virtual fuel tank %, real telemetry (None if no data)
    fuel_flag         TEXT,     -- IMSA low-fuel warning: '' / 'yellow' / 'red'
    tire_compound     TEXT,     -- tyre compound this stint (series-specific codes); NULL when not available
    tire_age          INTEGER,  -- laps on the current tyre set; NULL when not available
    override_state    TEXT,     -- energy/override state (series-specific); NULL when not available
    is_running        INTEGER,
    updated_at        TEXT,
    PRIMARY KEY (session_oid, car_number)
);

CREATE TABLE IF NOT EXISTS lap_history (
    session_oid       TEXT,
    car_number        TEXT,
    lap_number        INTEGER,
    lap_time_ms       INTEGER,
    s1_ms             INTEGER,
    s2_ms             INTEGER,
    s3_ms             INTEGER,
    is_best_personal  INTEGER,
    is_best_overall   INTEGER,
    recorded_at       TEXT,
    PRIMARY KEY (session_oid, car_number, lap_number)
);

CREATE TABLE IF NOT EXISTS pit_events (
    session_oid        TEXT,
    car_number         TEXT,
    stop_number        INTEGER,   -- = pit counter value at this stop
    pit_lap            INTEGER,   -- car's own lap count when stop detected
    session_lap        INTEGER,   -- session-wide current lap when stop detected
    flag               TEXT,      -- track flag at pit time (GF/YF/SC/... ) — caution-aware
    pit_entry_hour_ms  INTEGER,   -- epoch ms of pit (from session_pit_info.lastPitHour)
    stop_duration_ms   INTEGER,   -- from session_pit_info.lastPitTime
    total_pit_ms       INTEGER,   -- cumulative pit time after this stop
    detected_at        TEXT,
    PRIMARY KEY (session_oid, car_number, stop_number)
);

CREATE TABLE IF NOT EXISTS session_status (
    session_oid     TEXT PRIMARY KEY,
    current_flag    TEXT,
    current_lap     INTEGER,
    is_running      INTEGER,
    is_finished     INTEGER,
    final_type      TEXT,        -- BY_TIME / BY_LAPS
    final_time_s    INTEGER,     -- session duration (seconds) when BY_TIME
    final_laps      INTEGER,     -- session length (laps) when BY_LAPS
    start_time_s    INTEGER,     -- epoch seconds the clock started
    stopped_s       INTEGER,     -- seconds lost to stoppages (red flags)
    has_extra_time  INTEGER,
    extra_time_s    INTEGER,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS race_control (
    session_oid  TEXT,
    ts           INTEGER,   -- message epoch ms (from the feed log) when available
    message      TEXT,
    detected_at  TEXT,
    PRIMARY KEY (session_oid, ts, message)
);

CREATE TABLE IF NOT EXISTS caution_periods (
    session_oid  TEXT,
    period_num   INTEGER,   -- 1-based caution count this session
    start_lap    INTEGER,
    start_ts     TEXT,
    end_lap      INTEGER,    -- NULL while the caution is still running
    end_ts       TEXT,
    duration_s   INTEGER,    -- filled when the caution ends
    cause        TEXT,       -- flag that opened it (YF/FCY/SC/...)
    PRIMARY KEY (session_oid, period_num)
);

CREATE TABLE IF NOT EXISTS driver_changes (
    session_oid  TEXT,
    car_number   TEXT,
    seq          INTEGER,   -- 1 = first driver seen (baseline); 2+ = a change
    driver       TEXT,
    session_lap  INTEGER,   -- session lap when this driver took over
    detected_at  TEXT,
    PRIMARY KEY (session_oid, car_number, seq)
);

CREATE TABLE IF NOT EXISTS predictions (
    session_oid       TEXT,
    ts                INTEGER,   -- epoch ms when the prediction was made
    session_lap       INTEGER,
    car_number        TEXT,
    car_class         TEXT,
    track_position    INTEGER,   -- ACTUAL overall position at prediction time (ground truth)
    pos_in_class      INTEGER,   -- ACTUAL in-class position at prediction time
    laps              INTEGER,
    stops             INTEGER,   -- actual pit count so far
    net_position      INTEGER,   -- PREDICTED in-class net position
    net_gap_ms        REAL,
    est_stops_left    INTEGER,
    next_stop_ms      REAL,      -- PREDICTED next stop duration
    next_stop_std_ms  REAL,
    owes_dc           INTEGER,
    catching          TEXT,      -- car predicted to be caught
    catch_in_laps     REAL,
    projected_finish  INTEGER,
    PRIMARY KEY (session_oid, ts, car_number)
);

CREATE INDEX IF NOT EXISTS idx_lap_history_car
    ON lap_history (session_oid, car_number, lap_number DESC);
CREATE INDEX IF NOT EXISTS idx_pit_events_car
    ON pit_events (session_oid, car_number, stop_number DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_car
    ON predictions (session_oid, car_number, ts);
"""


class RaceDB:
    def __init__(self, path: Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the WEC SignalR client creates this connection
        # on the main thread (bootstrap) but writes from the SignalR callback
        # thread. All those writes are serialized under WecClient._lock and the
        # bootstrap completes before SignalR starts, so there is never concurrent
        # use — the default same-thread *identity* check is what was crashing
        # every race-flag update, not real contention.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL lets the dashboard read while the scraper writes, without lock contention.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        # Two processes write this DB during a race (scraper standings + dashboard
        # predictions). Wait up to 5s for the lock instead of failing instantly on
        # a collision, so a momentary overlap never drops the scraper's commit.
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

        self.session_oid: Optional[str] = None
        # diff trackers (seeded from DB on set_session so reconnects don't dup)
        # Pit detection is driven by pit_info.lastPitHour (a real per-stop epoch),
        # NOT the standings 'pits' field — that field is actually the current lap
        # number in the race feed (laps+1), which fired a phantom stop every lap.
        self._pit_count: dict[str, int] = {}    # real detected stop count per car
        self._last_pit_hour: dict[str, int] = {}  # last lastPitHour seen per car
        self._car_lap: dict[str, int] = {}      # latest lap per car (stamps pit_lap)
        self._last_pit_lap: dict[str, int] = {}
        self._last_driver: dict[str, str] = {}
        self._driver_seq: dict[str, int] = {}
        self._last_flag: Optional[str] = None        # for caution-period edge detection
        self._caution_num: int = 0                   # cautions opened so far
        self._caution_open: bool = False             # a caution period is currently running

    # ── session ──────────────────────────────────────────────────────────────
    def set_session(self, oid: Optional[str], info: dict, series: str = "imsa") -> None:
        if not oid:
            return
        self.session_oid = oid
        now = _now()
        self.conn.execute(
            """INSERT INTO sessions
                 (session_oid, champ_name, event_name, session_name, session_type,
                  series, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(session_oid) DO UPDATE SET
                 champ_name=excluded.champ_name,
                 event_name=excluded.event_name,
                 session_name=excluded.session_name,
                 session_type=excluded.session_type,
                 series=excluded.series,
                 last_seen=excluded.last_seen""",
            (oid, info.get("champName"), info.get("eventName"),
             info.get("name"), info.get("type"), series, now, now),
        )
        self.conn.commit()
        self._seed_trackers()

    def update_status(self, status: dict) -> None:
        """Persist the live session clock/flag (one row per session)."""
        if not self.session_oid or not status:
            return
        self.conn.execute(
            """INSERT INTO session_status
                 (session_oid, current_flag, current_lap, is_running, is_finished,
                  final_type, final_time_s, final_laps, start_time_s, stopped_s,
                  has_extra_time, extra_time_s, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_oid) DO UPDATE SET
                 current_flag=excluded.current_flag, current_lap=excluded.current_lap,
                 is_running=excluded.is_running, is_finished=excluded.is_finished,
                 final_type=excluded.final_type, final_time_s=excluded.final_time_s,
                 final_laps=excluded.final_laps, start_time_s=excluded.start_time_s,
                 stopped_s=excluded.stopped_s, has_extra_time=excluded.has_extra_time,
                 extra_time_s=excluded.extra_time_s, updated_at=excluded.updated_at""",
            (self.session_oid, _scalar(status.get("currentFlag")),
             _scalar(status.get("currentLap")),
             1 if status.get("isSessionRunning") else 0,
             1 if status.get("isFinished") else 0,
             _scalar(status.get("finalType")), _scalar(status.get("finalTime")),
             _scalar(status.get("finalLaps")), _scalar(status.get("startTime")),
             _scalar(status.get("stoppedSeconds")),
             1 if status.get("hasExtraTime") else 0,
             _scalar(status.get("extraTime")), _now()),
        )
        self._track_caution(_scalar(status.get("currentFlag")),
                            _scalar(status.get("currentLap")))

    def _track_caution(self, flag, lap) -> None:
        """Open a caution_periods row on green→caution, close it on caution→green."""
        if not self.session_oid or flag is None:
            return
        is_caution = str(flag).upper() in CAUTION_FLAGS
        if self._last_flag is None:
            self._last_flag = flag           # first reading — no edge yet
        if is_caution and not self._caution_open:
            self._caution_num += 1
            self._caution_open = True
            self.conn.execute(
                """INSERT OR IGNORE INTO caution_periods
                     (session_oid, period_num, start_lap, start_ts, cause)
                   VALUES (?,?,?,?,?)""",
                (self.session_oid, self._caution_num, lap, _now(), flag))
        elif not is_caution and self._caution_open:
            self._caution_open = False
            self.conn.execute(
                """UPDATE caution_periods
                     SET end_lap=?, end_ts=?,
                         duration_s = CAST((julianday(?) - julianday(start_ts)) * 86400 AS INTEGER)
                   WHERE session_oid=? AND period_num=?""",
                (lap, _now(), _now(), self.session_oid, self._caution_num))
        self._last_flag = flag

    def _migrate(self) -> None:
        """Add columns introduced after a DB was first created (idempotent)."""
        # sessions.series — added when the app went multi-series. Backfill any
        # pre-existing rows to 'imsa' (the only series before this column).
        scols = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(sessions)").fetchall()}
        if "series" not in scols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN series TEXT")
            self.conn.execute("UPDATE sessions SET series='imsa' WHERE series IS NULL")
        cols = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(standings_current)").fetchall()}
        if "elapsed_ms" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN elapsed_ms INTEGER")
        # lastPitHour persisted so a reconnect doesn't re-count the last stop
        if "last_pit_hour_ms" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN last_pit_hour_ms INTEGER")
        # raw standings data string (latest per car) — lets us verify the real
        # field layout against a live feed without re-instrumenting the scraper
        if "raw_data" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN raw_data TEXT")
        # real virtual-fuel-tank telemetry (GTP/GTDPRO/GTD; empty for LMP2)
        if "fuel_pct" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN fuel_pct REAL")
        if "fuel_flag" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN fuel_flag TEXT")
        # tyre + energy telemetry columns (series-specific, NULL when not available)
        if "tire_compound" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN tire_compound TEXT")
        if "tire_age" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN tire_age INTEGER")
        if "override_state" not in cols:
            self.conn.execute(
                "ALTER TABLE standings_current ADD COLUMN override_state TEXT")

    def _seed_trackers(self) -> None:
        """Reload diff trackers from persisted state so a reconnect resumes cleanly."""
        if not self.session_oid:
            return
        self._pit_count.clear()
        self._last_pit_hour.clear()
        self._car_lap.clear()
        self._last_pit_lap.clear()
        self._last_driver.clear()
        self._driver_seq.clear()
        rows = self.conn.execute(
            """SELECT car_number, pits, last_pit_lap, last_pit_hour_ms, laps
                 FROM standings_current WHERE session_oid=?""",
            (self.session_oid,),
        ).fetchall()
        for r in rows:
            if r["pits"] is not None:
                self._pit_count[r["car_number"]] = r["pits"]
            if r["last_pit_lap"] is not None:
                self._last_pit_lap[r["car_number"]] = r["last_pit_lap"]
            if r["last_pit_hour_ms"] is not None:
                self._last_pit_hour[r["car_number"]] = r["last_pit_hour_ms"]
            if r["laps"] is not None:
                self._car_lap[r["car_number"]] = r["laps"]
        # seed driver trackers from the latest recorded stint per car
        for r in self.conn.execute(
            """SELECT car_number, driver, MAX(seq) AS seq FROM driver_changes
                 WHERE session_oid=? GROUP BY car_number""", (self.session_oid,)):
            self._last_driver[r["car_number"]] = r["driver"]
            self._driver_seq[r["car_number"]] = r["seq"]
        # seed caution trackers so a reconnect mid-yellow doesn't open a duplicate period
        cp = self.conn.execute(
            """SELECT period_num, end_lap FROM caution_periods
                 WHERE session_oid=? ORDER BY period_num DESC LIMIT 1""",
            (self.session_oid,)).fetchone()
        if cp:
            self._caution_num = cp["period_num"]
            self._caution_open = cp["end_lap"] is None   # still running if never closed
        else:
            self._caution_num = 0
            self._caution_open = False
        self._last_flag = None

    # ── entries (static) ─────────────────────────────────────────────────────
    def upsert_entry(self, car: str, e: dict) -> None:
        if not self.session_oid or not car:
            return
        drivers = e.get("drivers")
        self.conn.execute(
            """INSERT INTO session_entry
                 (session_oid, car_number, name, team, vehicle, class, drivers, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(session_oid, car_number) DO UPDATE SET
                 name=excluded.name, team=excluded.team, vehicle=excluded.vehicle,
                 class=excluded.class, drivers=excluded.drivers,
                 updated_at=excluded.updated_at""",
            (self.session_oid, car, _scalar(e.get("name")), _scalar(e.get("team")),
             _scalar(e.get("vehicle")), _scalar(e.get("class")),
             json.dumps(drivers) if drivers is not None else None, _now()),
        )

    def record_race_control(self, messages) -> None:
        """Persist raw race-control messages (append-only, deduped). messages = iterable
        of (ts_ms_or_None, text). Penalty parsing is deferred — we just keep the text."""
        if not self.session_oid:
            return
        now = _now()
        for ts, text in messages:
            text = _scalar(text)
            if not text:
                continue
            self.conn.execute(
                """INSERT OR IGNORE INTO race_control (session_oid, ts, message, detected_at)
                   VALUES (?,?,?,?)""",
                (self.session_oid, int(ts) if ts else 0, text, now),
            )

    def record_driver(self, car: str, driver, session_lap: Optional[int]) -> None:
        """Log a driver stint when the car's current driver changes (append-only)."""
        driver = _scalar(driver)
        if not self.session_oid or not car or not driver:
            return
        if self._last_driver.get(car) == driver:
            return
        seq = self._driver_seq.get(car, 0) + 1
        self._driver_seq[car] = seq
        self._last_driver[car] = driver
        self.conn.execute(
            """INSERT OR IGNORE INTO driver_changes
                 (session_oid, car_number, seq, driver, session_lap, detected_at)
               VALUES (?,?,?,?,?,?)""",
            (self.session_oid, car, seq, driver, session_lap, _now()),
        )

    # ── standings + derived history ──────────────────────────────────────────
    def ingest_car(self, car: str, d: dict, standing: dict,
                   session_lap: Optional[int], flag: Optional[str] = None,
                   raw_data: Optional[str] = None) -> None:
        """
        d        = parsed semicolon 'data' dict (overall_position, laps, ...)
        standing = full merged standings entry (lastLapTime, bestLapTime, flags, sectors)
        flag     = current track flag, stamped onto any pit stop detected this call
        raw_data = raw semicolon data string, stored for live field-layout verification

        Pit detection does NOT happen here — it's driven by pit_info.lastPitHour in
        update_pit_info(). The standings 'pits' field is the current lap number in the
        race feed, not a stop count, so we never trust it for pits.
        """
        if not self.session_oid or not car:
            return

        laps     = d.get("laps")
        last_lap = _scalar(standing.get("lastLapTime"))
        best_lap = _scalar(standing.get("bestLapTime"))
        if laps is not None:
            self._car_lap[car] = laps     # stamp pit_lap when a stop is detected

        # 2. lap history — append the just-completed lap (idempotent on lap_number)
        if last_lap and laps and laps > 0:
            s1, s2, s3 = _parse_sectors(standing.get("lastSectors"))
            self.conn.execute(
                """INSERT OR IGNORE INTO lap_history
                     (session_oid, car_number, lap_number, lap_time_ms,
                      s1_ms, s2_ms, s3_ms, is_best_personal, is_best_overall, recorded_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (self.session_oid, car, laps, last_lap, s1, s2, s3,
                 1 if standing.get("isLastLapBestPersonal") else 0,
                 1 if standing.get("isLastLapBestOverall") else 0, _now()),
            )

        # 3. current snapshot upsert — 'pits' column holds the REAL detected stop
        # count (from pit_info), not the standings field.
        self.conn.execute(
            """INSERT INTO standings_current
                 (session_oid, car_number, overall_position, pos_in_class, car_class,
                  laps, laps_behind, gap_ms, elapsed_ms, last_lap_ms, best_lap_ms,
                  best_lap_num, track_status, pits, last_pit_lap, last_pit_hour_ms,
                  fuel_pct, fuel_flag, tire_compound, tire_age, override_state,
                  raw_data, is_running, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(session_oid, car_number) DO UPDATE SET
                 overall_position=excluded.overall_position,
                 pos_in_class=excluded.pos_in_class, car_class=excluded.car_class,
                 laps=excluded.laps, laps_behind=excluded.laps_behind, gap_ms=excluded.gap_ms,
                 elapsed_ms=excluded.elapsed_ms,
                 last_lap_ms=excluded.last_lap_ms, best_lap_ms=excluded.best_lap_ms,
                 best_lap_num=excluded.best_lap_num, track_status=excluded.track_status,
                 pits=excluded.pits, last_pit_lap=excluded.last_pit_lap,
                 last_pit_hour_ms=excluded.last_pit_hour_ms,
                 fuel_pct=excluded.fuel_pct, fuel_flag=excluded.fuel_flag,
                 tire_compound=excluded.tire_compound, tire_age=excluded.tire_age,
                 override_state=excluded.override_state,
                 raw_data=excluded.raw_data,
                 is_running=excluded.is_running, updated_at=excluded.updated_at""",
            (self.session_oid, car, d.get("overall_position"), d.get("pos_in_class"),
             standing.get("class"), laps, d.get("laps_behind"), d.get("gap_ms"),
             _scalar(standing.get("elapsedTime")),
             last_lap, best_lap, _scalar(standing.get("bestLapNumber")),
             d.get("track_status"), self._pit_count.get(car, 0),
             self._last_pit_lap.get(car), self._last_pit_hour.get(car),
             standing.get("fuelPct"), standing.get("fuelFlag"),
             standing.get("tireCompound"), standing.get("tireAge"),
             standing.get("overrideState"), raw_data,
             1 if standing.get("isRunning") else 0, _now()),
        )

    def _record_pit(self, car: str, stop_number: int, pit_lap: Optional[int],
                    flag: Optional[str], hour_ms: Optional[int],
                    duration_ms: Optional[int], total_ms: Optional[int]) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO pit_events
                 (session_oid, car_number, stop_number, pit_lap, session_lap, flag,
                  pit_entry_hour_ms, stop_duration_ms, total_pit_ms, detected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (self.session_oid, car, stop_number, pit_lap, pit_lap, flag,
             hour_ms, duration_ms, total_ms, _now()),
        )

    # ── pit detection (the real one) ─────────────────────────────────────────
    def update_pit_info(self, car: str, pit: dict, live_observed: bool = False) -> None:
        """
        Detect pit stops off session_pit_info.lastPitHour — a real per-stop epoch.
        A *changed* lastPitHour means a new completed stop. The first value seen
        for a car is taken as a baseline (a stop that may predate our connect, so
        not counted) — matching the long-standing "only live-observed stops" rule.

        live_observed=True skips that baseline rule: the caller generated the
        timestamp from pit events it watched happen (WEC pit-in/pit-out), so the
        first stop per car is real and must be counted, not swallowed.

        lastPitTime is that stop's duration; totalPitTime the cumulative pit time.
        """
        if not self.session_oid or not car:
            return
        hour = _scalar(pit.get("lastPitHour"))
        if hour is None or hour == 0:
            return
        prev = self._last_pit_hour.get(car)
        self._last_pit_hour[car] = hour
        if prev is None and not live_observed:
            return                      # baseline — don't count a pre-connect stop
        if hour == prev:
            return                      # same stop, nothing new
        # new stop
        n = self._pit_count.get(car, 0) + 1
        self._pit_count[car] = n
        pit_lap = self._car_lap.get(car)
        self._last_pit_lap[car] = pit_lap
        self._record_pit(car, n, pit_lap, self._last_flag,
                         hour, _scalar(pit.get("lastPitTime")),
                         _scalar(pit.get("totalPitTime")))

    # ── lifecycle ────────────────────────────────────────────────────────────
    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()
