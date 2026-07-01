"""
f1_live.py — F1 live timing adapter via SignalR Core.

Connects to the unofficial F1 live timing endpoint and feeds data
through db.ingest_car() into the same DB schema used by IMSA / replay.

Usage:
  python src/f1_live.py               # live mode (persists to data/race.db)
  python src/f1_live.py --discover    # 30s protocol dump
  python src/f1_live.py --no-db       # dry run (no persistence)
  python src/f1_live.py --no-auth     # attempt without auth (partial data)
  python src/f1_live.py --db data/f1_live.db  # custom DB path

Requires:
  - signalrcore (installed via FastF1)
  - fastf1 (for auth token)
  - An active F1TV subscription for full data
"""

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
from signalrcore.messages.completion_message import CompletionMessage

from db import RaceDB, DEFAULT_DB_PATH

# ── config ────────────────────────────────────────────────────────────────────
CONNECTION_URL = "wss://livetiming.formula1.com/signalrcore"
NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"
SNAPSHOT_EVERY = 10

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"f1live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("f1live")

TOPICS = [
    "TimingData", "TimingAppData", "DriverList",
    "SessionInfo", "SessionStatus", "TrackStatus",
    "RaceControlMessages", "LapCount", "ExtrapolatedClock",
    "WeatherData",
]

_STATUS_MAP = {
    "1": "GF", "2": "YF", "4": "SC", "5": "RF", "6": "VSC", "7": "GF",
}

AVG_LAP_MS = 90_000


# ── parsers (pure functions, tested independently) ────────────────────────────

def _deep_merge(target: dict, update) -> dict:
    """Recursively merge update into target. Modifies target in place."""
    if not isinstance(update, dict):
        return target
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def _laptime_to_ms(s) -> Optional[int]:
    """Parse F1 lap/sector time string to integer milliseconds.
    Formats: "1:23.456", "23.456", "" → None
    """
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return int(float(s) * 1000) if s > 0 else None
    s = str(s).strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":", 1)
        try:
            return int((int(parts[0]) * 60 + float(parts[1])) * 1000)
        except (ValueError, IndexError):
            return None
    try:
        return int(float(s) * 1000)
    except ValueError:
        return None


_LAP_GAP = re.compile(r"\+?(\d+)\s+LAPS?", re.IGNORECASE)

def _parse_gap(gap_str, avg_lap_ms: int = AVG_LAP_MS) -> tuple:
    """Parse F1 GapToLeader string.
    Returns (gap_ms: int, laps_behind: int).
    """
    if gap_str is None or gap_str == "":
        return (0, 0)
    s = str(gap_str).strip()
    if not s:
        return (0, 0)
    m = _LAP_GAP.search(s)
    if m:
        n = int(m.group(1))
        return (n * avg_lap_ms, n)
    s = s.lstrip("+")
    try:
        return (int(float(s) * 1000), 0)
    except ValueError:
        return (0, 0)


def _f1_sectors_to_ak(sectors_dict) -> Optional[str]:
    """Convert F1 live Sectors dict to Al Kamel sector string.
    Input: {"0": {"Value": "24.547"}, "1": {...}, "2": {...}}
    Output: "1;24547;0;0;0;0;2;38776;0;0;0;0;3;40808;0;0;0;0"
    """
    if not isinstance(sectors_dict, dict):
        return None
    parts = []
    for i in range(3):
        sec = sectors_dict.get(str(i))
        if not isinstance(sec, dict):
            return None
        val = sec.get("Value")
        ms = _laptime_to_ms(val)
        if ms is None:
            return None
        parts.append(f"{i + 1};{ms};0;0;0;0")
    return ";".join(parts)


def _get_nested(d, *keys):
    """Safe nested dict access."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _make_oid(session_info: dict) -> str:
    """Build a session OID from SessionInfo data."""
    meeting = session_info.get("Meeting", {})
    name = meeting.get("Name", "unknown")
    safe = re.sub(r"[^\w]+", "_", name).lower().strip("_")
    key = session_info.get("Key", "")
    return f"f1_live_{safe}_{key}".rstrip("_")


def _session_type(session_info: dict) -> str:
    """Map SessionInfo.Name/Type to our canonical types."""
    name = (session_info.get("Name") or "").upper()
    if "RACE" in name or "SPRINT" in name:
        return "RACE"
    if "QUALIFYING" in name:
        return "QUALIFYING_BEST_LAP"
    return name or "PRACTICE"


# ── state ─────────────────────────────────────────────────────────────────────

@dataclass
class F1LiveState:
    """Cumulative in-memory state, deep-merge target for all topics."""
    timing_data: dict = field(default_factory=dict)
    timing_app: dict = field(default_factory=dict)
    driver_list: dict = field(default_factory=dict)
    session_info: dict = field(default_factory=dict)
    session_status: dict = field(default_factory=dict)
    track_status: dict = field(default_factory=dict)
    lap_count: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
    rc_messages: dict = field(default_factory=dict)
    weather: dict = field(default_factory=dict)

    session_oid: Optional[str] = None
    session_started: bool = False
    current_flag: str = "GF"
    current_lap: int = 0
    total_laps: int = 0

    # pit detection trackers
    pit_counts: dict = field(default_factory=dict)
    in_pit: dict = field(default_factory=dict)
    pit_entry_time: dict = field(default_factory=dict)


# ── client ────────────────────────────────────────────────────────────────────

class F1LiveClient:
    def __init__(self, db: "RaceDB | None" = None, no_auth: bool = False):
        self.state = F1LiveState()
        self.db = db
        self._no_auth = no_auth
        self._connection = None
        self._is_connected = False
        self._cycle = 0
        self._last_snapshot = 0.0
        self._lock = threading.Lock()

    # ── connection setup ──────────────────────────────────────────────────

    def _get_cookies(self) -> dict:
        """Capture AWSALBCORS cookie via OPTIONS pre-flight."""
        try:
            r = requests.options(NEGOTIATE_URL, timeout=10)
            if "AWSALBCORS" in r.cookies:
                return {"Cookie": f"AWSALBCORS={r.cookies['AWSALBCORS']}"}
        except Exception as e:
            log.warning("Cookie pre-flight failed: %s", e)
        return {}

    def _get_auth_factory(self):
        if self._no_auth:
            return None
        try:
            from fastf1.internals.f1auth import get_auth_token
            return get_auth_token
        except ImportError:
            log.warning("fastf1.internals.f1auth not available; "
                        "running without auth")
            return None

    def _build_connection(self):
        cookies = self._get_cookies()
        auth_factory = self._get_auth_factory()

        options = {
            "verify_ssl": True,
            "headers": cookies,
        }
        if auth_factory is not None:
            options["access_token_factory"] = auth_factory

        builder = HubConnectionBuilder() \
            .with_url(CONNECTION_URL, options=options) \
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
        self._connection.on("feed", self._on_feed)

    # ── callbacks ─────────────────────────────────────────────────────────

    def _on_connect(self):
        self._is_connected = True
        log.info("SignalR connected — subscribing to %d topics", len(TOPICS))
        self._connection.send(
            "Subscribe", [TOPICS], on_invocation=self._on_subscribe_result)

    def _on_close(self):
        self._is_connected = False
        log.warning("SignalR connection closed")

    def _on_reconnect(self):
        log.info("SignalR reconnected — re-subscribing")
        self._connection.send(
            "Subscribe", [TOPICS], on_invocation=self._on_subscribe_result)

    def _on_error(self, error):
        log.error("SignalR error: %s", error)

    def _on_subscribe_result(self, msg):
        """Handle the CompletionMessage response to Subscribe.
        Contains the full current state for all topics.
        """
        if isinstance(msg, CompletionMessage) and msg.result:
            log.info("Subscribe response: %d topics",
                     len(msg.result) if isinstance(msg.result, dict) else 0)
            if isinstance(msg.result, dict):
                for topic, data in msg.result.items():
                    self._dispatch(topic, data)
        elif isinstance(msg, list):
            for item in msg:
                if isinstance(item, list) and len(item) >= 2:
                    self._dispatch(item[0], item[1])

    def _on_feed(self, msg):
        """Handle streaming 'feed' events (incremental updates)."""
        if isinstance(msg, list):
            if len(msg) >= 2 and isinstance(msg[0], str):
                self._dispatch(msg[0], msg[1])
            else:
                for item in msg:
                    if isinstance(item, list) and len(item) >= 2:
                        self._dispatch(item[0], item[1])

        now = time.time()
        if now - self._last_snapshot >= SNAPSHOT_EVERY:
            self._maybe_snapshot()
            self._last_snapshot = now

    # ── topic dispatch ────────────────────────────────────────────────────

    def _dispatch(self, topic: str, data):
        if data is None:
            return
        with self._lock:
            handler = {
                "TimingData": self._handle_timing_data,
                "TimingAppData": self._handle_timing_app,
                "DriverList": self._handle_driver_list,
                "SessionInfo": self._handle_session_info,
                "SessionStatus": self._handle_session_status,
                "TrackStatus": self._handle_track_status,
                "RaceControlMessages": self._handle_rc_messages,
                "LapCount": self._handle_lap_count,
                "ExtrapolatedClock": self._handle_clock,
                "WeatherData": self._handle_weather,
            }.get(topic)
            if handler:
                try:
                    handler(data)
                except Exception:
                    log.exception("Error handling %s", topic)
            else:
                log.debug("Unhandled topic: %s", topic)

    # ── topic handlers ────────────────────────────────────────────────────

    def _handle_timing_data(self, data):
        if not isinstance(data, dict):
            return
        lines = data.get("Lines")
        if not isinstance(lines, dict):
            _deep_merge(self.state.timing_data, data)
            return

        _deep_merge(self.state.timing_data, data)
        changed_drivers = list(lines.keys())

        for drv in changed_drivers:
            drv_data = lines[drv]
            if not isinstance(drv_data, dict):
                continue
            self._detect_pit(drv, drv_data)

        if self.db and self.state.session_oid:
            self._persist_standings(changed_drivers)

    def _handle_timing_app(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.timing_app, data)

    def _handle_driver_list(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.driver_list, data)
        if self.db and self.state.session_oid:
            self._persist_entries(data)

    def _handle_session_info(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.session_info, data)
        oid = _make_oid(self.state.session_info)
        if oid != self.state.session_oid:
            self.state.session_oid = oid
            log.info("Session: %s (oid=%s)", self.state.session_info.get(
                "Meeting", {}).get("Name", "?"), oid)
            if self.db:
                info = self.state.session_info
                meeting = info.get("Meeting", {})
                self.db.set_session(oid, {
                    "champName": "Formula 1",
                    "eventName": meeting.get("Name", "Unknown GP"),
                    "name": info.get("Name", "?"),
                    "type": _session_type(info),
                }, series="f1")
                self.db.commit()

    def _handle_session_status(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.session_status, data)
        status = data.get("Status") or self.state.session_status.get("Status")
        if status:
            self.state.session_started = status in ("Started", "Aborted")
            is_finished = status in ("Finished", "Finalised", "Ends")
            if self.db:
                self.db.update_status({
                    "isSessionRunning": self.state.session_started,
                    "isFinished": is_finished,
                })
                self.db.commit()
            log.info("Session status: %s", status)

    def _handle_track_status(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.track_status, data)
        code = str(data.get("Status", ""))
        flag = _STATUS_MAP.get(code, self.state.current_flag)
        if flag != self.state.current_flag:
            log.info("Flag: %s → %s (%s)",
                     self.state.current_flag, flag,
                     data.get("Message", ""))
            self.state.current_flag = flag
            if self.db:
                self.db.update_status({"currentFlag": flag})
                self.db.commit()

    def _handle_rc_messages(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.rc_messages, data)
        messages = data.get("Messages")
        if not isinstance(messages, dict):
            return
        if self.db:
            batch = []
            for _key, msg in messages.items():
                if isinstance(msg, dict):
                    text = msg.get("Message", "")
                    utc = msg.get("Utc", "")
                    if text:
                        batch.append((utc, text))
                        log.info("[RC] %s", text)
            if batch:
                self.db.record_race_control(batch)
                self.db.commit()

    def _handle_lap_count(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.lap_count, data)
        cl = data.get("CurrentLap")
        tl = data.get("TotalLaps")
        if cl is not None:
            self.state.current_lap = int(cl)
        if tl is not None:
            self.state.total_laps = int(tl)
        if self.db and (cl is not None or tl is not None):
            update = {"currentLap": self.state.current_lap}
            if self.state.total_laps > 0:
                update["finalLaps"] = self.state.total_laps
                update["finalType"] = "BY_LAPS"
            self.db.update_status(update)
            self.db.commit()

    def _handle_clock(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.clock, data)

    def _handle_weather(self, data):
        if not isinstance(data, dict):
            return
        _deep_merge(self.state.weather, data)

    # ── pit detection ─────────────────────────────────────────────────────

    def _detect_pit(self, drv: str, drv_data: dict):
        """Detect pit stops from NumberOfPitStops and InPit transitions."""
        pit_count = drv_data.get("NumberOfPitStops")
        if pit_count is not None:
            try:
                new_count = int(pit_count)
            except (ValueError, TypeError):
                new_count = None
            if new_count is not None:
                prev = self.state.pit_counts.get(drv)
                self.state.pit_counts[drv] = new_count
                if prev is not None and new_count > prev and self.db:
                    car = str(drv)
                    drv_full = _get_nested(
                        self.state.timing_data, "Lines", drv)
                    laps = None
                    if isinstance(drv_full, dict):
                        laps = drv_full.get("NumberOfLaps")
                        if laps is not None:
                            try:
                                laps = int(laps)
                            except (ValueError, TypeError):
                                laps = None
                    dur_ms = None
                    entry_time = self.state.pit_entry_time.pop(drv, None)
                    if entry_time is not None:
                        dur_ms = int((time.time() - entry_time) * 1000)
                    self.db._pit_count[car] = new_count
                    if laps is not None:
                        self.db._last_pit_lap[car] = laps
                    self.db._record_pit(
                        car, new_count, laps,
                        self.state.current_flag,
                        int(time.time() * 1000),
                        dur_ms, None)
                    self.db.commit()
                    log.info("Pit stop #%d for car %s (lap %s)",
                             new_count, drv, laps)

        in_pit = drv_data.get("InPit")
        if in_pit is True and not self.state.in_pit.get(drv, False):
            self.state.in_pit[drv] = True
            self.state.pit_entry_time[drv] = time.time()
        elif in_pit is False and self.state.in_pit.get(drv, False):
            self.state.in_pit[drv] = False

    # ── persistence ───────────────────────────────────────────────────────

    def _persist_entries(self, data: dict):
        """Write driver list entries to DB."""
        if not self.db:
            return
        for drv_num, info in data.items():
            if not isinstance(info, dict):
                continue
            tla = info.get("Tla", drv_num)
            team = info.get("TeamName", "")
            full = info.get("FullName", tla)
            self.db.upsert_entry(str(drv_num), {
                "class": "F1",
                "team": team,
                "vehicle": None,
                "name": full,
                "drivers": [full],
            })
        self.db.commit()

    def _persist_standings(self, changed_drivers: list):
        """Build d + standing dicts and call db.ingest_car() for changed drivers."""
        if not self.db:
            return
        lines = self.state.timing_data.get("Lines", {})
        app_lines = self.state.timing_app.get("Lines", {})

        leader_laps = 0
        for _drv, drv_data in lines.items():
            if not isinstance(drv_data, dict):
                continue
            nlaps = drv_data.get("NumberOfLaps")
            if nlaps is not None:
                try:
                    nlaps = int(nlaps)
                    if nlaps > leader_laps:
                        leader_laps = nlaps
                except (ValueError, TypeError):
                    pass

        for drv in changed_drivers:
            drv_data = lines.get(drv)
            if not isinstance(drv_data, dict):
                continue
            car = str(drv)

            pos = drv_data.get("Position")
            if pos is not None:
                try:
                    pos = int(pos)
                except (ValueError, TypeError):
                    pos = None

            nlaps = drv_data.get("NumberOfLaps")
            if nlaps is not None:
                try:
                    nlaps = int(nlaps)
                except (ValueError, TypeError):
                    nlaps = None

            laps_behind = 0
            gap_ms = 0
            gap_str = drv_data.get("GapToLeader")
            if gap_str is not None:
                gap_ms, laps_behind = _parse_gap(gap_str)
            elif nlaps is not None and leader_laps > 0:
                laps_behind = leader_laps - nlaps
                if laps_behind > 0:
                    gap_ms = laps_behind * AVG_LAP_MS

            in_pit = drv_data.get("InPit", False)
            pit_out = drv_data.get("PitOut", False)
            retired = drv_data.get("Retired", False)
            stopped = drv_data.get("Stopped", False)

            if stopped or retired:
                track_status = "STOPPED"
            elif in_pit is True:
                track_status = "BOX"
            elif pit_out is True:
                track_status = "OUT_LAP"
            else:
                track_status = "TRACK"

            d = {
                "overall_position": pos,
                "car_number": car,
                "pos_in_class": pos,
                "laps": nlaps,
                "laps_behind": laps_behind,
                "gap_ms": gap_ms,
                "track_status": track_status,
            }

            last_lap = _get_nested(drv_data, "LastLapTime", "Value")
            best_lap = _get_nested(drv_data, "BestLapTime", "Value")
            best_lap_num = _get_nested(drv_data, "BestLapTime", "Lap")
            is_pb = bool(_get_nested(
                drv_data, "LastLapTime", "PersonalFastest"))
            is_ob = bool(_get_nested(
                drv_data, "LastLapTime", "OverallFastest"))

            sectors = drv_data.get("Sectors")
            ak_sectors = _f1_sectors_to_ak(sectors) if sectors else None

            compound = None
            tire_age = None
            app_data = app_lines.get(drv)
            if isinstance(app_data, dict):
                stints = app_data.get("Stints")
                if isinstance(stints, dict) and stints:
                    last_stint_key = max(stints.keys(), key=lambda k: int(k)
                                         if k.isdigit() else -1)
                    last_stint = stints.get(last_stint_key, {})
                    compound = last_stint.get("Compound")
                    tire_age = last_stint.get("TotalLaps")
                    if tire_age is not None:
                        try:
                            tire_age = int(tire_age)
                        except (ValueError, TypeError):
                            tire_age = None
                elif isinstance(stints, list) and stints:
                    last_stint = stints[-1] if stints else {}
                    if isinstance(last_stint, dict):
                        compound = last_stint.get("Compound")
                        tire_age = last_stint.get("TotalLaps")
                        if tire_age is not None:
                            try:
                                tire_age = int(tire_age)
                            except (ValueError, TypeError):
                                tire_age = None

            standing = {
                "class": "F1",
                "isRunning": not (retired or stopped),
                "lastLapTime": _laptime_to_ms(last_lap),
                "bestLapTime": _laptime_to_ms(best_lap),
                "bestLapNumber": int(best_lap_num) if best_lap_num else None,
                "lastSectors": ak_sectors,
                "isLastLapBestPersonal": is_pb,
                "isLastLapBestOverall": is_ob,
                "elapsedTime": None,
                "fuelPct": None,
                "fuelFlag": None,
                "tireCompound": compound,
                "tireAge": tire_age,
                "overrideState": None,
            }

            self.db.ingest_car(car, d, standing,
                               self.state.current_lap,
                               self.state.current_flag)
        self.db.commit()

    # ── console snapshot ──────────────────────────────────────────────────

    def _maybe_snapshot(self):
        lines = self.state.timing_data.get("Lines", {})
        if not lines:
            return
        self._cycle += 1

        flag_symbols = {
            "GF": "🟢 GREEN", "YF": "🟡 YELLOW", "RF": "🔴 RED",
            "SC": "🚗 SC", "VSC": "🚗 VSC",
        }
        flag_str = flag_symbols.get(
            self.state.current_flag,
            f"? {self.state.current_flag}")

        meeting = self.state.session_info.get("Meeting", {})
        name = meeting.get("Name", "?")
        session_name = self.state.session_info.get("Name", "?")

        laps_str = f"Lap {self.state.current_lap}"
        if self.state.total_laps > 0:
            laps_str += f"/{self.state.total_laps}"

        print(f"\n{'═' * 95}")
        print(f"  {name}  |  {session_name}  |  {laps_str}  |  "
              f"{flag_str}  |  Cycle #{self._cycle:04d}  "
              f"{datetime.utcnow().strftime('%H:%M:%S')} UTC")
        print(f"{'═' * 95}")
        print(f"  {'P':>3}  {'#':>3}  {'DRIVER':<5}  {'TEAM':<20}  "
              f"{'LAP':>4}  {'STATUS':<8}  {'LAST':>9}  "
              f"{'BEST':>9}  {'GAP':>10}  {'TIRE':<6}  {'PITS':>4}")
        print(f"  {'─' * 3}  {'─' * 3}  {'─' * 5}  {'─' * 20}  "
              f"{'─' * 4}  {'─' * 8}  {'─' * 9}  "
              f"{'─' * 9}  {'─' * 10}  {'─' * 6}  {'─' * 4}")

        sorted_drivers = sorted(
            lines.items(),
            key=lambda x: int(x[1].get("Position", 99))
            if isinstance(x[1], dict)
            and x[1].get("Position") is not None
            else 99)

        for drv, drv_data in sorted_drivers:
            if not isinstance(drv_data, dict):
                continue
            pos = drv_data.get("Position", "?")
            tla = _get_nested(self.state.driver_list, drv, "Tla") or drv
            team = (_get_nested(self.state.driver_list, drv, "TeamName")
                    or "")[:20]
            nlaps = drv_data.get("NumberOfLaps", "?")

            in_pit = drv_data.get("InPit", False)
            retired = drv_data.get("Retired", False)
            stopped = drv_data.get("Stopped", False)
            if stopped or retired:
                status = "STOPPED"
            elif in_pit:
                status = "BOX"
            else:
                status = "TRACK"

            last_ms = _laptime_to_ms(
                _get_nested(drv_data, "LastLapTime", "Value"))
            best_ms = _laptime_to_ms(
                _get_nested(drv_data, "BestLapTime", "Value"))

            gap = drv_data.get("GapToLeader", "")
            if not gap or pos == 1 or str(pos) == "1":
                gap_str = "LEADER"
            else:
                gap_str = str(gap)[:10]

            compound = ""
            app_data = self.state.timing_app.get("Lines", {}).get(drv)
            if isinstance(app_data, dict):
                stints = app_data.get("Stints")
                if isinstance(stints, dict) and stints:
                    lk = max(stints.keys(),
                             key=lambda k: int(k) if k.isdigit() else -1)
                    compound = stints.get(lk, {}).get("Compound", "")[:6]
                elif isinstance(stints, list) and stints:
                    compound = (stints[-1].get("Compound", "")
                                if isinstance(stints[-1], dict) else "")[:6]

            pits = self.state.pit_counts.get(drv, 0)

            def _fmt_ms(ms):
                if ms is None:
                    return "    —    "
                total_s = ms / 1000
                mins = int(total_s // 60)
                secs = total_s % 60
                return f"{mins}:{secs:06.3f}"

            print(
                f"  {str(pos):>3}  {str(drv):>3}  {tla:<5}  {team:<20}  "
                f"{str(nlaps):>4}  {status:<8}  {_fmt_ms(last_ms):>9}  "
                f"{_fmt_ms(best_ms):>9}  {gap_str:>10}  {compound:<6}  "
                f"{pits:>4}")
        print()

    # ── run ────────────────────────────────────────────────────────────────

    def run(self):
        """Start the connection and block until stopped."""
        self._build_connection()
        log.info("Starting F1 live timing client")
        self._connection.start()

        while self._is_connected or True:
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                log.info("Stopping...")
                self._connection.stop()
                break

    def stop(self):
        if self._connection:
            self._connection.stop()


# ── discover mode ─────────────────────────────────────────────────────────────

def discover_mode(no_auth: bool = False):
    """Subscribe to all topics for 30s and dump messages to a log file."""
    log.info("Discover mode — 30s observation")
    all_messages = []

    client = F1LiveClient(db=None, no_auth=no_auth)

    original_dispatch = client._dispatch

    def capturing_dispatch(topic, data):
        all_messages.append({
            "time": datetime.utcnow().isoformat(),
            "topic": topic,
            "data_keys": list(data.keys()) if isinstance(data, dict) else type(data).__name__,
            "data": data,
        })
        original_dispatch(topic, data)

    client._dispatch = capturing_dispatch

    client._build_connection()
    client._connection.start()

    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    client._connection.stop()

    out = LOG_DIR / "f1_discover.json"
    out.write_text(json.dumps(all_messages, indent=2, default=str),
                   encoding="utf-8")

    topics_seen = {m["topic"] for m in all_messages}
    log.info("Topics seen (%d): %s", len(topics_seen), sorted(topics_seen))
    log.info("Total messages: %d", len(all_messages))
    log.info("Full dump → %s", out)

    if client.state.timing_data.get("Lines"):
        client._maybe_snapshot()


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="F1 live timing adapter")
    parser.add_argument("--discover", action="store_true",
                        help="30s protocol dump")
    parser.add_argument("--no-db", action="store_true",
                        help="dry run, no persistence")
    parser.add_argument("--no-auth", action="store_true",
                        help="skip F1TV auth (partial data)")
    parser.add_argument("--db", type=str, default=DEFAULT_DB_PATH,
                        help="database path")
    args = parser.parse_args()

    if args.discover:
        discover_mode(no_auth=args.no_auth)
        return

    db = None if args.no_db else RaceDB(args.db)
    if db:
        log.info("Persisting to %s", db.path)

    # Auth pre-flight check
    if not args.no_auth:
        try:
            from fastf1.internals.f1auth import get_auth_token
            token = get_auth_token()
            if token:
                log.info("F1TV auth token OK")
            else:
                log.warning("F1TV auth failed — data may be partial")
        except Exception as e:
            log.warning("Auth pre-flight failed: %s", e)

    client = F1LiveClient(db=db, no_auth=args.no_auth)
    try:
        while True:
            try:
                client.run()
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error("Session error: %s", e)

            log.info("Restarting in 10s ...")
            try:
                time.sleep(10)
            except KeyboardInterrupt:
                break
    finally:
        if db:
            db.close()
            log.info("Database closed.")


if __name__ == "__main__":
    main()
