"""
wec_live.py — FIA WEC live timing adapter via Griiip's SignalR + MessagePack.

Connects to the Griiip live session hub at insights.griiip.com, joins a
session group ("SID-{sid}"), and receives multiplexed timing data via the
"ReceiveBatch" server method. Initial state is hydrated from a REST
bootstrap endpoint.

Usage:
  python src/wec_live.py                       # auto-discover WEC session
  python src/wec_live.py --sid 12345           # connect to specific session
  python src/wec_live.py --discover            # list live sessions, then 30s dump
  python src/wec_live.py --no-db               # dry run (no persistence)
  python src/wec_live.py --record F.jsonl.gz   # raw-capture every frame
  python src/wec_live.py --db data/wec.db      # custom DB path

Raw-capture-first: --record writes every decoded frame BEFORE dispatch.
Dispatch is wrapped in try/except; the capture write never is. Even a
zero-parse race day yields a complete archive for offline replay.

Protocol (discovered 2026-07-03 from livetiming.fiawec.com JS bundle):
  Hub: https://insights.griiip.com/live-session-stream (SignalR Core, msgpack v2)
  Join: invoke("JoinGroup", "SID-{sid}")
  Data: on("ReceiveBatch", {items: [{channel, view}, ...]})
  Bootstrap: GET /api/v2/public/live/session/{sid}/bootstrap
  Schedule: GET /meta/sessions-schedule-live
  WEC series ID: 10
"""

import argparse
import gzip
import json
import logging
import re
import sys
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from signalrcore.hub_connection_builder import HubConnectionBuilder
from signalrcore.protocol.messagepack_protocol import MessagePackHubProtocol

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod

# ── config ────────────────────────────────────────────────────────────────────

API_BASE = "https://insights.griiip.com"
HUB_URL = f"{API_BASE}/live-session-stream"
BOOTSTRAP_URL = f"{API_BASE}/api/v2/public/live/session/{{sid}}/bootstrap"
SCHEDULE_URL = f"{API_BASE}/meta/sessions-schedule-live"

WEC_SERIES_ID = 10

SNAPSHOT_EVERY_S = 10
STALE_TIMEOUT_S = 120
RECONNECT_PAUSE_S = 10

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"weclive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("weclive")

# Griiip channel names (from JS bundle enum extraction)
CH_RANKS = "ranks"
CH_GAPS = "gaps"
CH_LAPS = "laps"
CH_SESSION_INFO = "session-info"
CH_SESSION_CLOCK = "session-clock"
CH_RACE_FLAGS = "race-flags"
CH_PARTICIPANTS = "participants"
CH_RUNNING_STATUS = "participants-running-status"
CH_PIT_IN = "pit-in"
CH_PIT_OUT = "pit-out"
CH_PIT_STAND_START = "pit-standing-start"
CH_PIT_STAND_FINISH = "pit-standing-finish"
CH_SESSION_LENGTH = "session-length-limit"
CH_TIRES = "tires"
CH_WEATHER = "Weather"
CH_VET = "cars-energy-tanks"
CH_SECTOR_CROSS = "sector-cross-updates"
CH_RACE_LOG = "RaceLog"
CH_SESSION_CLOSED = "session-closed"

FLAG_MAP = {
    "GREEN": "GF",
    "YELLOW": "YF",
    "FCY": "FCY",
    "FULL COURSE YELLOW": "FCY",
    "FULLCOURSEYELLOW": "FCY",
    "SAFETY CAR": "SC",
    "SAFETYCAR": "SC",
    "SC": "SC",
    "VSC": "VSC",
    "RED": "RF",
    "RED FLAG": "RF",
    "CHECKERED": "CH",
    "CHEQUERED": "CH",
    "FINISH": "CH",
    "LASTLAP": "GF",
    "SLOW ZONE": "SZ",
    "SLOWZONE": "SZ",
}


# ── pure parser helpers (tested in test_wec_live.py) ──────────────────────────

def laptime_ms(v) -> Optional[int]:
    """Parse a lap/sector time to integer milliseconds.
    Accepts: '1:23.456', '23.456', 83456 (int ms), 83.456 (float s).
    """
    if v is None:
        return None
    if isinstance(v, int) and v > 0:
        return v
    if isinstance(v, float) and v > 0:
        return int(v * 1000) if v < 1000 else int(v)
    s = str(v).strip()
    if not s:
        return None
    if ":" in s:
        try:
            parts = s.split(":", 1)
            return int((int(parts[0]) * 60 + float(parts[1])) * 1000)
        except (ValueError, IndexError):
            return None
    try:
        val = float(s)
        if val <= 0:
            return None
        return int(val * 1000) if val < 1000 else int(val)
    except ValueError:
        return None


def parse_flag(raw) -> Optional[str]:
    """Map a Griiip flag string to our canonical flag code."""
    if not raw:
        return None
    key = str(raw).strip().upper()
    if not key:
        return None
    return FLAG_MAP.get(key, key)


def normalize_class(raw) -> str:
    """Normalize a WEC class name/ID to our canonical codes."""
    if not raw:
        return "HYPERCAR"
    up = str(raw).strip().upper()
    if not up:
        return "HYPERCAR"
    if "HYPER" in up or "LMH" in up:
        return "HYPERCAR"
    if "GT3" in up or "LMGT" in up:
        return "LMGT3"
    return up


def make_oid(sid, event_name: str = "") -> str:
    """Build a stable session OID from Griiip session ID."""
    if sid:
        return f"wec_live_{sid}"
    slug = re.sub(r"[^\w]+", "_", event_name).strip("_").lower()[:60]
    return f"wec_live_{slug or 'unknown'}"


def parse_participant(p: dict) -> dict:
    """Extract entry info from a Griiip participants message."""
    drivers = p.get("drivers") or []
    driver_names = []
    for d in drivers:
        if isinstance(d, dict):
            name = d.get("displayName") or ""
            if name:
                driver_names.append(name.strip())
        elif isinstance(d, str):
            driver_names.append(d.strip())

    display = p.get("displayName") or ""
    if display and not driver_names:
        driver_names = [display.strip()]

    return {
        "class": normalize_class(p.get("classId") or ""),
        "team": p.get("teamName") or None,
        "vehicle": p.get("manufacturer") or None,
        "name": driver_names[0] if driver_names else display or None,
        "drivers": driver_names or None,
    }


# ── REST helpers ──────────────────────────────────────────────────────────────

def _http_headers() -> dict:
    return {"User-Agent": "race-net-timing/1.0"}


def find_wec_session() -> Optional[int]:
    """Query the live schedule for an active WEC session. Returns sid or None."""
    try:
        r = requests.get(SCHEDULE_URL, timeout=15, headers=_http_headers())
        r.raise_for_status()
        sessions = r.json()
    except Exception as e:
        log.warning("Schedule fetch failed: %s", e)
        return None

    for s in sessions:
        sid = s.get("sid")
        if not sid:
            continue
        try:
            info = fetch_bootstrap(sid).get("sessionInfo", {})
            if info.get("seriesId") == WEC_SERIES_ID:
                log.info("Found WEC session: sid=%s (%s — %s)",
                         sid, info.get("eventName"), info.get("sessionName"))
                return sid
        except Exception:
            continue
    return None


def fetch_bootstrap(sid: int) -> dict:
    """Fetch the full bootstrap snapshot for a session."""
    url = BOOTSTRAP_URL.format(sid=sid)
    r = requests.get(url, timeout=20, headers=_http_headers(),
                     params={"includeViewers": "true",
                             "includeUnclassifiedRanks": "false"})
    r.raise_for_status()
    return r.json()


# ── state ─────────────────────────────────────────────────────────────────────

@dataclass
class WecLiveState:
    sid: Optional[int] = None
    session_oid: Optional[str] = None
    current_flag: str = "GF"
    current_lap: int = 0
    is_running: bool = False
    is_finished: bool = False

    # accumulated per-car state from multiplexed channels
    car_ranks: dict = field(default_factory=dict)     # car -> {pos, pos_class}
    car_gaps: dict = field(default_factory=dict)       # car -> {gap_ms, laps_behind}
    car_laps: dict = field(default_factory=dict)       # car -> {lap, last_ms, best_ms, best_num}
    car_status: dict = field(default_factory=dict)     # car -> running status
    car_classes: dict = field(default_factory=dict)    # car -> class string
    car_tires: dict = field(default_factory=dict)      # car -> tire info

    entries_written: set = field(default_factory=set)
    pit_in_times: dict = field(default_factory=dict)   # car -> entry epoch ms

PIT_DEBOUNCE = 2


def _int_or(v, default):
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


# ── client ────────────────────────────────────────────────────────────────────

class WecLiveClient:
    def __init__(self, db_path: str, record_path: Optional[str] = None,
                 no_db: bool = False):
        self.db_path = db_path
        self.no_db = no_db
        self.record_path = record_path
        self._recorder = None

        self.db: Optional[dbmod.RaceDB] = None
        self.state = WecLiveState()

        self._connection = None
        self._is_connected = False
        self._stopping = False
        self._reconnect_count = 0
        self._last_message_time = 0.0
        self._last_snapshot_time = 0.0
        self._lock = threading.Lock()

    # ── raw capture ──────────────────────────────────────────────────────

    def _record_frame(self, channel: str, data):
        """Write a raw frame to the capture file. Never wrapped in try/except
        by callers — capture integrity is non-negotiable."""
        if not self._recorder:
            return
        frame = {
            "ts": int(time.time() * 1000),
            "channel": channel,
            "data": data,
        }
        self._recorder.write((json.dumps(frame, default=str) + "\n")
                             .encode("utf-8"))

    # ── connection setup ─────────────────────────────────────────────────

    def _build_connection(self):
        options = {
            "verify_ssl": True,
            "headers": {
                "User-Agent": "race-net-timing/1.0",
            },
        }

        builder = HubConnectionBuilder() \
            .with_url(HUB_URL, options=options) \
            .with_hub_protocol(MessagePackHubProtocol()) \
            .configure_logging(logging.WARNING) \
            .with_automatic_reconnect({
                "type": "raw",
                "keep_alive_interval": 15,
                "reconnect_interval": 5,
                "max_attempts": None,
            })

        self._connection = builder.build()
        self._connection.on_open(self._on_connect)
        self._connection.on_close(self._on_close)
        self._connection.on_reconnect(self._on_reconnect)
        self._connection.on_error(self._on_error)
        self._connection.on("ReceiveBatch", self._on_receive_batch)

    # ── callbacks ────────────────────────────────────────────────────────

    def _join_group(self):
        if not self.state.sid:
            return
        group = f"SID-{self.state.sid}"
        log.info("Joining group %s", group)
        try:
            self._connection.send("JoinGroup", [group])
        except Exception as e:
            log.error("JoinGroup failed: %s", e)

    def _on_connect(self):
        self._is_connected = True
        self._last_message_time = time.time()
        log.info("SignalR connected (msgpack)")
        self._join_group()

    def _on_close(self):
        self._is_connected = False
        log.warning("SignalR connection closed")

    def _on_reconnect(self):
        self._reconnect_count += 1
        log.info("SignalR reconnected (#%d) — re-joining group", self._reconnect_count)
        self._join_group()

    def _on_error(self, error):
        log.error("SignalR error: %s", error)

    def _on_receive_batch(self, msg):
        """Entry point for all hub data. Record FIRST, then dispatch."""
        self._last_message_time = time.time()

        items = []
        if isinstance(msg, dict):
            items = msg.get("items") or []
        elif isinstance(msg, list):
            if msg and isinstance(msg[0], dict) and "items" in msg[0]:
                items = msg[0].get("items") or []
            else:
                items = msg

        for item in items:
            if not isinstance(item, dict):
                continue
            channel = item.get("channel") or ""
            view = item.get("view")
            if view is None:
                view = item

            # raw capture — always runs, never in try/except
            self._record_frame(channel, view)

            # dispatch — wrapped so a parser crash never kills capture
            try:
                self._dispatch_channel(channel, view)
            except Exception:
                log.exception("Error dispatching channel %s", channel)

        now = time.time()
        if now - self._last_snapshot_time >= SNAPSHOT_EVERY_S:
            self._last_snapshot_time = now
            self._snapshot()

    # ── channel dispatch ─────────────────────────────────────────────────

    def _dispatch_channel(self, channel: str, data):
        if data is None:
            return
        with self._lock:
            handler = {
                CH_RANKS: self._handle_ranks,
                CH_GAPS: self._handle_gaps,
                CH_LAPS: self._handle_laps,
                CH_SESSION_INFO: self._handle_session_info,
                CH_SESSION_CLOCK: self._handle_session_clock,
                CH_RACE_FLAGS: self._handle_race_flags,
                CH_PARTICIPANTS: self._handle_participants,
                CH_RUNNING_STATUS: self._handle_running_status,
                CH_PIT_IN: self._handle_pit_in,
                CH_PIT_OUT: self._handle_pit_out,
                CH_SESSION_LENGTH: self._handle_session_length,
                CH_TIRES: self._handle_tires,
                CH_WEATHER: self._handle_weather,
                CH_VET: self._handle_vet,
                CH_RACE_LOG: self._handle_race_log,
                CH_SESSION_CLOSED: self._handle_session_closed,
            }.get(channel)
            if handler:
                handler(data)

    # ── channel handlers ─────────────────────────────────────────────────

    def _handle_session_info(self, data):
        if not isinstance(data, dict):
            return
        sid = data.get("sid") or self.state.sid
        event = data.get("eventName") or "WEC"
        session_name = data.get("sessionName") or "Session"
        session_type = data.get("sessionType") or ""

        type_map = {"Race": "RACE", "Qualifying": "QUALIFYING",
                    "Practice": "PRACTICE", "Free Practice": "PRACTICE",
                    "Warmup": "PRACTICE"}
        stype = type_map.get(session_type, "SESSION")

        oid = make_oid(sid, event)
        if oid == self.state.session_oid:
            return

        self.state.session_oid = oid
        self.state.entries_written.clear()
        self.state.is_finished = data.get("hasSeenChequered", False)
        self.state.is_running = data.get("isStarted", False)
        log.info("Session: %s — %s (oid=%s, type=%s)", event, session_name, oid, stype)

        if self.db:
            self.db.set_session(oid, {
                "champName": "FIA WEC",
                "eventName": event,
                "name": session_name,
                "type": stype,
            }, series="wec")

    def _handle_session_clock(self, data):
        if not isinstance(data, dict):
            return
        elapsed = data.get("elapsedTimeMillis")
        start_time = data.get("startTime")
        if self.db and start_time:
            try:
                start_s = int(datetime.fromisoformat(str(start_time)).timestamp())
                self.db.update_status({"startTime": start_s})
            except (ValueError, TypeError):
                pass

    def _handle_session_length(self, data):
        if not isinstance(data, dict) or not self.db:
            return
        length_type = data.get("sessionLengthType") or ""
        status = {}
        if "Laps" in length_type:
            laps = _int_or(data.get("lapsLimit"), None)
            if laps and laps > 0:
                status["finalType"] = "BY_LAPS"
                status["finalLaps"] = laps
        elif "Time" in length_type:
            secs = _int_or(data.get("timeLimitSeconds"), None)
            if secs and secs > 0:
                status["finalType"] = "BY_TIME"
                status["finalTime"] = secs
        if status:
            self.db.update_status(status)
            self.db.commit()

    def _handle_race_flags(self, data):
        if not isinstance(data, dict):
            return
        raw = data.get("flag") or ""
        flag = parse_flag(raw) or self.state.current_flag
        lap = _int_or(data.get("lapNumber"), self.state.current_lap)
        if flag != self.state.current_flag:
            log.info("Flag: %s -> %s (lap %d)", self.state.current_flag, flag, lap)
            self.state.current_flag = flag
        if flag == "CH":
            self.state.is_finished = True
        if self.db:
            self.db.update_status({
                "currentFlag": self.state.current_flag,
                "isFinished": self.state.is_finished,
            })
            self.db.commit()

    def _handle_ranks(self, data):
        if not isinstance(data, dict):
            return
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                if isinstance(item, dict):
                    self._handle_ranks(item)
            return
        car = str(data.get("carNumber") or "").strip()
        if not car:
            return
        cls = normalize_class(data.get("classId") or "")
        self.state.car_classes[car] = cls
        self.state.car_ranks[car] = {
            "pos": _int_or(data.get("overallPosition"), None),
            "pos_class": _int_or(data.get("position"), None),
        }
        self._flush_car(car)

    def _handle_gaps(self, data):
        if not isinstance(data, dict):
            return
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                if isinstance(item, dict):
                    self._handle_gaps(item)
            return
        car = str(data.get("carNumber") or "").strip()
        if not car:
            return
        gap_ms = _int_or(data.get("gapToFirstMillis"), 0)
        laps_behind = _int_or(data.get("gapToFirstLaps"), 0)
        if gap_ms < 0:
            gap_ms = 0
        self.state.car_gaps[car] = {
            "gap_ms": gap_ms,
            "laps_behind": laps_behind,
        }
        self._flush_car(car)

    def _handle_laps(self, data):
        if not isinstance(data, dict):
            return
        car = str(data.get("carNumber") or "").strip()
        if not car:
            return
        lap_num = _int_or(data.get("lapNumber"), None)
        lap_ms = _int_or(data.get("lapTimeMillis"), None)
        if lap_num is not None:
            existing = self.state.car_laps.get(car, {})
            existing["lap"] = lap_num
            existing["last_ms"] = lap_ms
            self.state.car_laps[car] = existing
            if lap_num > self.state.current_lap:
                self.state.current_lap = lap_num
                if self.db:
                    self.db.update_status({
                        "currentLap": self.state.current_lap,
                        "isSessionRunning": True,
                    })
        self._flush_car(car)

    def _handle_participants(self, data):
        if not isinstance(data, dict):
            return
        car = str(data.get("carNumber") or "").strip()
        if not car or not self.db:
            return
        cls = normalize_class(data.get("classId") or "")
        self.state.car_classes[car] = cls
        entry_key = (car, cls)
        if entry_key not in self.state.entries_written:
            self.state.entries_written.add(entry_key)
            self.db.upsert_entry(car, parse_participant(data))

    def _handle_running_status(self, data):
        if not isinstance(data, dict):
            return
        car = str(data.get("carNumber") or "").strip()
        if not car:
            return
        status = str(data.get("status") or "").lower()
        self.state.car_status[car] = status
        self._flush_car(car)

    def _handle_pit_in(self, data):
        if not isinstance(data, dict):
            return
        car = str(data.get("carNumber") or "").strip()
        if not car:
            return
        now_ms = int(time.time() * 1000)
        self.state.pit_in_times[car] = now_ms
        log.info("Pit IN: #%s", car)

    def _handle_pit_out(self, data):
        if not isinstance(data, dict):
            return
        car = str(data.get("carNumber") or "").strip()
        if not car:
            return
        entry_ms = self.state.pit_in_times.pop(car, None)
        if entry_ms and self.db:
            now_ms = int(time.time() * 1000)
            self.db.update_pit_info(car, {
                "lastPitHour": entry_ms,
                "lastPitTime": max(0, now_ms - entry_ms),
                "totalPitTime": None,
            })
            self.db.commit()
        log.info("Pit OUT: #%s", car)

    def _handle_tires(self, data):
        if not isinstance(data, dict):
            return
        car = str(data.get("carNumber") or "").strip()
        if car:
            self.state.car_tires[car] = {
                "compound": data.get("compound") or data.get("tyre") or None,
                "age": _int_or(data.get("age") or data.get("laps"), None),
            }

    def _handle_weather(self, data):
        pass

    def _handle_vet(self, data):
        pass

    def _handle_race_log(self, data):
        if not isinstance(data, dict) or not self.db:
            return
        text = data.get("message") or data.get("text") or ""
        ts = data.get("ts") or ""
        if text:
            self.db.record_race_control([(str(ts), str(text))])
            self.db.commit()
            log.info("[RC] %s", text)

    def _handle_session_closed(self, data):
        self.state.is_finished = True
        if self.db:
            self.db.update_status({"isFinished": True})
            self.db.commit()
        log.info("Session closed")

    # ── flush accumulated car state to DB ────────────────────────────────

    def _flush_car(self, car: str):
        if not self.db or not self.state.session_oid:
            return

        ranks = self.state.car_ranks.get(car, {})
        gaps = self.state.car_gaps.get(car, {})
        laps_info = self.state.car_laps.get(car, {})
        status = self.state.car_status.get(car, "running")
        cls = self.state.car_classes.get(car, "HYPERCAR")
        tires = self.state.car_tires.get(car, {})

        is_in_pit = car in self.state.pit_in_times
        is_stopped = status in ("retired", "dnf", "withdrawn", "disqualified")

        track_status = ("STOPPED" if is_stopped else
                        "PIT" if is_in_pit else "TRACK")

        laps = laps_info.get("lap", 0)

        d = {
            "overall_position": ranks.get("pos"),
            "pos_in_class": ranks.get("pos_class"),
            "laps": laps,
            "laps_behind": gaps.get("laps_behind", 0),
            "gap_ms": gaps.get("gap_ms", 0),
            "track_status": track_status,
        }

        standing = {
            "class": cls,
            "isRunning": not is_stopped,
            "lastLapTime": laps_info.get("last_ms"),
            "bestLapTime": laps_info.get("best_ms"),
            "bestLapNumber": laps_info.get("best_num"),
            "lastSectors": None,
            "elapsedTime": None,
            "tireCompound": tires.get("compound"),
            "tireAge": tires.get("age"),
        }

        self.db.ingest_car(car, d, standing, self.state.current_lap,
                           self.state.current_flag)
        self.db.commit()

    # ── bootstrap hydration ──────────────────────────────────────────────

    def _hydrate_bootstrap(self, data: dict):
        """Hydrate state from the REST bootstrap snapshot."""
        info = data.get("sessionInfo")
        if isinstance(info, dict):
            self._handle_session_info(info)

        clock = data.get("sessionClock")
        if isinstance(clock, dict):
            self._handle_session_clock(clock)

        limit = data.get("sessionLengthLimit")
        if isinstance(limit, dict):
            self._handle_session_length(limit)

        for flag in (data.get("raceFlags") or []):
            if isinstance(flag, dict):
                self._handle_race_flags(flag)

        for p in (data.get("participants") or []):
            if isinstance(p, dict):
                self._handle_participants(p)

        for r in (data.get("ranks") or []):
            if isinstance(r, dict):
                self._handle_ranks(r)

        for g in (data.get("gaps") or []):
            if isinstance(g, dict):
                self._handle_gaps(g)

        best_by_car = {}
        for b in (data.get("bestLaps") or []):
            if isinstance(b, dict):
                car = str(b.get("carNumber") or "").strip()
                if car:
                    best_by_car[car] = {
                        "best_ms": _int_or(b.get("lapTimeMillis"), None),
                        "best_num": _int_or(b.get("lapNumber"), None),
                    }

        for lap in (data.get("laps") or []):
            if isinstance(lap, dict):
                car = str(lap.get("carNumber") or "").strip()
                if car and car in best_by_car:
                    existing = self.state.car_laps.get(car, {})
                    existing.update(best_by_car.pop(car))
                    self.state.car_laps[car] = existing
                self._handle_laps(lap)

        for car, best in best_by_car.items():
            existing = self.state.car_laps.get(car, {})
            existing.update(best)
            self.state.car_laps[car] = existing

        for s in (data.get("runningStatuses") or []):
            if isinstance(s, dict):
                self._handle_running_status(s)

        n_cars = len(self.state.car_ranks)
        log.info("Bootstrap hydrated: %d cars, lap %d, flag %s",
                 n_cars, self.state.current_lap, self.state.current_flag)

    # ── console snapshot ─────────────────────────────────────────────────

    def _snapshot(self):
        n_cars = len(self.state.car_ranks)
        n_pit = len(self.state.pit_in_times)
        log.info("[%s] sid=%s  flag=%s  lap=%d  cars=%d  pit=%d  connected=%s",
                 time.strftime("%H:%M:%S"), self.state.sid,
                 self.state.current_flag, self.state.current_lap,
                 n_cars, n_pit, self._is_connected)

    # ── run loop ─────────────────────────────────────────────────────────

    def run(self, sid: int):
        self.state.sid = sid

        if not self.no_db:
            self.db = dbmod.RaceDB(self.db_path)

        if self.record_path:
            self._recorder = gzip.open(self.record_path, "ab")
            log.info("Recording raw frames -> %s", self.record_path)

        log.info("Bootstrapping session %d...", sid)
        try:
            bootstrap = fetch_bootstrap(sid)
            self._record_frame("_bootstrap", bootstrap)
            self._hydrate_bootstrap(bootstrap)
        except Exception as e:
            log.warning("Bootstrap failed (continuing to SignalR): %s", e)

        self._stopping = False
        self._build_connection()
        log.info("Connecting to SignalR hub (sid=%d)", sid)
        self._connection.start()
        self._last_message_time = time.time()

        try:
            while not self._stopping:
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    self._stopping = True
                    break

                if (self._is_connected
                        and self._last_message_time > 0
                        and time.time() - self._last_message_time > STALE_TIMEOUT_S):
                    log.warning("No messages for %ds — forcing reconnect",
                                STALE_TIMEOUT_S)
                    try:
                        self._connection.stop()
                    except Exception:
                        pass
                    break
        finally:
            self._cleanup()

    def _cleanup(self):
        if self._connection:
            try:
                self._connection.stop()
            except Exception:
                pass
        if self._recorder:
            self._recorder.close()
            self._recorder = None
        if self.db:
            self.db.close()
            self.db = None

    def stop(self):
        self._stopping = True
        self._cleanup()


# ── discover mode ─────────────────────────────────────────────────────────────

def discover_mode(sid: Optional[int] = None):
    """List live sessions, optionally connect to one for 30s, dump everything."""
    log.info("=== Live sessions ===")
    try:
        r = requests.get(SCHEDULE_URL, timeout=15, headers=_http_headers())
        sessions = r.json()
        for s in sessions:
            live_sid = s.get("sid")
            if not live_sid:
                continue
            try:
                info = fetch_bootstrap(live_sid).get("sessionInfo", {})
                log.info("  sid=%s  series=%s (id=%s)  event=%s  session=%s  flag=%s",
                         live_sid, info.get("seriesName"), info.get("seriesId"),
                         info.get("eventName"), info.get("sessionName"),
                         s.get("currentFlag"))
                if info.get("seriesId") == WEC_SERIES_ID and sid is None:
                    sid = live_sid
            except Exception as e:
                log.info("  sid=%s  (bootstrap failed: %s)", live_sid, e)
    except Exception as e:
        log.warning("Schedule fetch failed: %s", e)

    if sid is None:
        if sessions:
            sid = sessions[0].get("sid")
        if sid is None:
            log.info("No live sessions found")
            return

    log.info("\n=== Bootstrap for sid=%s ===", sid)
    try:
        bootstrap = fetch_bootstrap(sid)
        out = LOG_DIR / "wec_bootstrap.json"
        out.write_text(json.dumps(bootstrap, indent=2, default=str), encoding="utf-8")
        log.info("Bootstrap saved -> %s", out)
        info = bootstrap.get("sessionInfo", {})
        log.info("  Series: %s (id=%s)", info.get("seriesName"), info.get("seriesId"))
        log.info("  Event: %s", info.get("eventName"))
        log.info("  Session: %s (%s)", info.get("sessionName"), info.get("sessionType"))
        log.info("  Cars: %d ranks, %d participants",
                 len(bootstrap.get("ranks", [])),
                 len(bootstrap.get("participants", [])))
    except Exception as e:
        log.error("Bootstrap failed: %s", e)

    log.info("\n=== SignalR 30s observation (sid=%s) ===", sid)
    all_messages = []

    client = WecLiveClient(db_path="", no_db=True)
    client.state.sid = sid

    original_handler = client._on_receive_batch

    def capturing_handler(msg):
        all_messages.append({
            "time": datetime.utcnow().isoformat(),
            "raw": msg,
        })
        original_handler(msg)

    client._build_connection()
    client._connection.on("ReceiveBatch", capturing_handler)
    client._connection.start()
    client._last_message_time = time.time()

    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    try:
        client._connection.stop()
    except Exception:
        pass

    out = LOG_DIR / "wec_discover.json"
    out.write_text(json.dumps(all_messages, indent=2, default=str), encoding="utf-8")
    log.info("Captured %d batches -> %s", len(all_messages), out)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="WEC live timing adapter (Griiip SignalR+msgpack)")
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--db", default=str(root / "data" / "race.db"))
    ap.add_argument("--no-db", action="store_true", help="dry run, no persistence")
    ap.add_argument("--record", metavar="FILE.jsonl.gz",
                    help="append raw frames (gzip JSONL) for offline analysis")
    ap.add_argument("--discover", action="store_true",
                    help="list live sessions + 30s protocol dump")
    ap.add_argument("--sid", type=int, default=None,
                    help="Griiip session ID (auto-discovers WEC if omitted)")
    args = ap.parse_args()

    if args.discover:
        discover_mode(sid=args.sid)
        return

    sid = args.sid
    if sid is None:
        log.info("Auto-discovering WEC session...")
        sid = find_wec_session()
        if sid is None:
            log.error("No live WEC session found. Use --sid to specify one, "
                      "or --discover to list all live sessions.")
            sys.exit(1)

    client = WecLiveClient(db_path=args.db, record_path=args.record,
                           no_db=args.no_db)
    try:
        while True:
            try:
                client.run(sid)
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error("Session error: %s", e, exc_info=True)

            if client._stopping:
                break

            log.info("Restarting in %ds (reconnects: %d) ...",
                     RECONNECT_PAUSE_S, client._reconnect_count)
            try:
                time.sleep(RECONNECT_PAUSE_S)
            except KeyboardInterrupt:
                break
    finally:
        client._cleanup()
        log.info("Shutdown complete (reconnects: %d).", client._reconnect_count)


if __name__ == "__main__":
    main()
