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
import os
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
# If the transport drops and signalrcore's in-place reconnect does not restore a
# working connection within this window, stop waiting in a zombie state and tear
# down for a full rebuild (which re-bootstraps + re-joins and does resume data).
DISCONNECT_TIMEOUT_S = 30

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
CH_OFFICIAL_RANK = "official-rank"
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

# Channels whose arrival proves the live timing feed is genuinely flowing. The
# stale watchdog measures freshness against THESE only — and the set must stay
# minimal: after a dead in-place reconnect, Griiip keeps emitting keepalive
# frames AND some non-group channels (observed live at Sao Paulo FP1: a first
# cut that included sector-cross-updates never tripped the watchdog, because
# that channel kept flowing on a zombie connection while every group-scoped
# timing channel was dead). Core timing only: if none of ranks/gaps/laps
# arrives for STALE_TIMEOUT_S during a live session, the feed is dead and a
# full rebuild is the only thing that resumes it. A restart during genuinely
# quiet track time (long red flag) is cheap, logged churn — a silent freeze on
# race day is not.
LIVENESS_CHANNELS = frozenset({CH_RANKS, CH_GAPS, CH_LAPS, CH_OFFICIAL_RANK})

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
    # Live SignalR frames identify cars by Griiip participant id ('pid') with
    # NO carNumber — only the REST bootstrap and 'participants' frames carry
    # both. Verified across the full São Paulo FP3 raw capture: 0 of ~15k live
    # per-car items had a carNumber. Every handler resolves through this map.
    pid_to_car: dict = field(default_factory=dict)     # int pid -> car number str
    # accumulated session_status fields — db.update_status() overwrites EVERY
    # column from the dict it's given, so partial updates (flag-only, clock-only)
    # must be merged here first or they null each other's fields out.
    status_acc: dict = field(default_factory=dict)


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
        # set by _dispatch_channel's ts_ms arg during replay so pit-duration
        # handlers use the recorded frame time instead of live wall-clock;
        # None on the live path, where time.time() IS the correct clock
        self._frame_ts_ms: Optional[int] = None

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
        self._recorder.flush()

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
        # Arm the stale watchdog. signalrcore's in-place reconnect re-joins the
        # group, but Griiip does not reliably resume the data stream on it — so
        # mark the socket connected WITHOUT resetting _last_message_time. If data
        # really resumes, _on_receive_batch refreshes that clock; if it does not,
        # the watchdog trips STALE_TIMEOUT_S after the last *real* batch and
        # forces a full rebuild that does resume. (Previously this left
        # _is_connected False, which disabled the watchdog and left the client
        # frozen forever after any network blip.)
        self._is_connected = True
        log.info("SignalR reconnected (#%d) — re-joining group", self._reconnect_count)
        self._join_group()

    def _on_error(self, error):
        log.error("SignalR error: %s", error)

    def _on_receive_batch(self, msg):
        """Entry point for all hub data. Record FIRST, then dispatch."""
        items = []
        if isinstance(msg, dict):
            items = msg.get("items") or []
        elif isinstance(msg, list):
            if msg and isinstance(msg[0], dict) and "items" in msg[0]:
                items = msg[0].get("items") or []
            else:
                items = msg

        got_live_data = False
        for item in items:
            if not isinstance(item, dict):
                continue
            channel = item.get("channel") or ""
            view = item.get("view")
            if view is None:
                view = item

            # raw capture — always runs, never in try/except
            self._record_frame(channel, view)

            # only real timing channels count toward feed-liveness (see
            # LIVENESS_CHANNELS) — keepalive/empty frames must not reset the
            # stale watchdog after a dead reconnect.
            if channel in LIVENESS_CHANNELS:
                got_live_data = True

            # dispatch — wrapped so a parser crash never kills capture
            try:
                self._dispatch_channel(channel, view)
            except Exception:
                log.exception("Error dispatching channel %s", channel)

        if got_live_data:
            self._last_message_time = time.time()

        now = time.time()
        if now - self._last_snapshot_time >= SNAPSHOT_EVERY_S:
            self._last_snapshot_time = now
            self._snapshot()

    # ── channel dispatch ─────────────────────────────────────────────────

    def _dispatch_channel(self, channel: str, data, ts_ms: Optional[int] = None):
        if data is None:
            return
        with self._lock:
            self._frame_ts_ms = ts_ms
            handler = {
                CH_RANKS: self._handle_ranks,
                CH_GAPS: self._handle_gaps,
                CH_OFFICIAL_RANK: self._handle_official_rank,
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

    def _push_status(self, partial: dict):
        """Merge a partial status update into the accumulator and persist the
        full merged dict — see WecLiveState.status_acc for why."""
        self.state.status_acc.update(partial)
        if self.db:
            self.db.update_status(dict(self.state.status_acc))

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
        if oid != self.state.session_oid:
            self.state.session_oid = oid
            self.state.entries_written.clear()
            self.state.status_acc.clear()
            self.state.is_finished = data.get("hasSeenChequered", False)
            self.state.is_running = data.get("isStarted", False)
            log.info("Session: %s — %s (oid=%s, type=%s)",
                     event, session_name, oid, stype)

        # ALWAYS re-assert the session on the DB, even when the oid is
        # unchanged: every watchdog restart opens a fresh RaceDB whose
        # session_oid is None until set_session() runs, and RaceDB silently
        # drops every write until then. Skipping this on "same session" is
        # why FP3 and São Paulo quali captured nothing after the first
        # teardown — set_session is an upsert, so re-calling it is free.
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
        start_time = data.get("startTime")
        if self.db and start_time:
            try:
                # Griiip sends JS-style 'Z'-suffixed ISO timestamps, which
                # fromisoformat() can't parse before Python 3.11.
                iso = str(start_time).replace("Z", "+00:00")
                start_s = int(datetime.fromisoformat(iso).timestamp())
                self._push_status({"startTime": start_s})
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
            self._push_status(status)
            self.db.commit()

    def _handle_race_flags(self, data):
        if not isinstance(data, dict):
            return
        # Sector/local flags (a yellow shown only in specific sectors) carry a
        # non-empty sectorNumbers list. They are NOT session-wide full-course
        # states and must never drive the current flag or open a caution period.
        if data.get("sectorNumbers"):
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
            self._push_status({
                "currentFlag": self.state.current_flag,
                "isFinished": self.state.is_finished,
            })
            self.db.commit()

    def _resolve_car(self, data: dict) -> str:
        """Car number for a per-car frame: direct carNumber (bootstrap shapes)
        or pid lookup (all live SignalR shapes — see WecLiveState.pid_to_car)."""
        car = str(data.get("carNumber") or "").strip()
        if car:
            return car
        pid = _int_or(data.get("pid"), None)
        return self.state.pid_to_car.get(pid, "") if pid is not None else ""

    def _handle_ranks(self, data):
        if not isinstance(data, dict):
            return
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                if isinstance(item, dict):
                    self._handle_ranks(item)
            return
        car = self._resolve_car(data)
        if not car:
            return
        # live rank items carry no classId — don't clobber the class we got
        # from participants/bootstrap with the normalize_class fallback
        if data.get("classId"):
            self.state.car_classes[car] = normalize_class(data["classId"])
        self.state.car_ranks[car] = {
            "pos": _int_or(data.get("overallPosition"), None),
            "pos_class": _int_or(data.get("position"), None),
        }
        self._flush_car(car)

    def _handle_official_rank(self, data):
        """RACE-session position + gap stream. During the SP 2026 race the
        `ranks` and `gaps` channels carried ZERO frames — real races publish
        `official-rank` instead (51,514 frames there), which the client never
        dispatched. Every mid-race gap therefore came from the occasional
        bootstrap re-hydration: hours-stale gaps made the whole field look
        nose-to-tail, which is what produced the 14 noise catch calls (true
        gaps at call time were −32s..+80s vs the ≤2s gate).

        Fields (verified against the SP capture): `position` is the OVERALL
        position (matches final standings 35/35); `gapToFirstMillis` /
        `gapToFirstLaps` are gaps to the overall leader; `elapsedTimeMillis`
        is cumulative race time — persisting it activates calculator's
        preferred elapsed-diff gap path. −1 is Griiip's "no value" sentinel
        on all of them."""
        if not isinstance(data, dict):
            return
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                if isinstance(item, dict):
                    self._handle_official_rank(item)
            return
        car = self._resolve_car(data)
        if not car:
            return
        pos = _int_or(data.get("position"), None)
        if pos is not None and pos > 0:
            ranks = self.state.car_ranks.setdefault(car, {})
            ranks["pos"] = pos
            # in-class position = rank of this car's overall position among
            # same-class cars. Single-car update; official-rank refreshes
            # every car ~15s so the class converges immediately after a swap.
            cls = self.state.car_classes.get(car)
            if cls:
                ahead = sum(
                    1 for other, r in self.state.car_ranks.items()
                    if other != car and r.get("pos") is not None
                    and r["pos"] < pos
                    and self.state.car_classes.get(other) == cls)
                ranks["pos_class"] = ahead + 1
        gaps = self.state.car_gaps.setdefault(car, {})
        gap_ms = _int_or(data.get("gapToFirstMillis"), -1)
        if gap_ms >= 0:
            gaps["gap_ms"] = gap_ms
        laps_behind = _int_or(data.get("gapToFirstLaps"), -1)
        if laps_behind >= 0:
            gaps["laps_behind"] = laps_behind
        elapsed = _int_or(data.get("elapsedTimeMillis"), -1)
        if elapsed > 0:
            gaps["elapsed_ms"] = elapsed
        self._flush_car(car)

    def _handle_gaps(self, data):
        if not isinstance(data, dict):
            return
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                if isinstance(item, dict):
                    self._handle_gaps(item)
            return
        car = self._resolve_car(data)
        if not car:
            return
        gap_ms = _int_or(data.get("gapToFirstMillis"), 0)
        laps_behind = _int_or(data.get("gapToFirstLaps"), 0)
        # Griiip uses negative values (-1/-2) as "no value" sentinels on both
        # fields — seen live even for the leader, so they are not lap deficits
        if gap_ms < 0:
            gap_ms = 0
        if laps_behind < 0:
            laps_behind = 0
        self.state.car_gaps[car] = {
            "gap_ms": gap_ms,
            "laps_behind": laps_behind,
        }
        self._flush_car(car)

    def _handle_laps(self, data):
        if not isinstance(data, dict):
            return
        car = self._resolve_car(data)
        if not car:
            return
        lap_num = _int_or(data.get("lapNumber"), None)
        lap_ms = _int_or(data.get("lapTimeMillis"), None)
        if lap_num is not None:
            existing = self.state.car_laps.get(car, {})
            # bootstrap sends each car's recent laps NEWEST-FIRST — never let an
            # older entry regress the lap counter or the last-lap time
            if lap_num >= existing.get("lap", 0):
                existing["lap"] = lap_num
                existing["last_ms"] = lap_ms
            # keep the live best lap current (bootstrap only seeds it once);
            # laps explicitly marked invalid don't count as a best
            if lap_ms and data.get("isValid") is not False:
                best = existing.get("best_ms")
                if best is None or lap_ms < best:
                    existing["best_ms"] = lap_ms
                    existing["best_num"] = lap_num
            self.state.car_laps[car] = existing
            if lap_num > self.state.current_lap:
                self.state.current_lap = lap_num
                self._push_status({
                    "currentLap": self.state.current_lap,
                    "isSessionRunning": True,
                })
        self._flush_car(car)

    def _handle_participants(self, data):
        if not isinstance(data, dict):
            return
        car = str(data.get("carNumber") or "").strip()
        if not car:
            return
        # participants is the ONLY channel carrying both pid and carNumber —
        # register the mapping before the db guard so it exists in --no-db runs
        pid = _int_or(data.get("pid"), None)
        if pid is not None:
            self.state.pid_to_car[pid] = car
        if not self.db:
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
        car = self._resolve_car(data)
        if not car:
            return
        status = str(data.get("status") or "").lower()
        self.state.car_status[car] = status
        self._flush_car(car)

    def _handle_pit_in(self, data):
        if not isinstance(data, dict):
            return
        car = self._resolve_car(data)
        if not car:
            return
        now_ms = self._frame_ts_ms if self._frame_ts_ms is not None else int(time.time() * 1000)
        self.state.pit_in_times[car] = now_ms
        log.info("Pit IN: #%s", car)

    def _handle_pit_out(self, data):
        if not isinstance(data, dict):
            return
        car = self._resolve_car(data)
        if not car:
            return
        entry_ms = self.state.pit_in_times.pop(car, None)
        if entry_ms and self.db:
            now_ms = self._frame_ts_ms if self._frame_ts_ms is not None else int(time.time() * 1000)
            # live_observed: this stop was seen happen (pit-in + pit-out events),
            # so the first stop per car is real — don't apply the IMSA baseline
            # rule, which exists for feed values that may predate our connect.
            self.db.update_pit_info(car, {
                "lastPitHour": entry_ms,
                "lastPitTime": max(0, now_ms - entry_ms),
                "totalPitTime": None,
            }, live_observed=True)
            self.db.commit()
        log.info("Pit OUT: #%s", car)

    def _handle_tires(self, data):
        """Griiip tires frames nest a per-corner list:
        {"tires": [{"id": "frontLeft", "compound": "MEDIUM",
                    "ageInLaps": 7, "isChanged": true}, ...x4], "pid": ...}
        We keep one compound (corners match in practice) and the max corner
        age — the conservative wear number for pit-window prediction."""
        if not isinstance(data, dict):
            return
        car = self._resolve_car(data)
        if not car:
            return
        corners = [t for t in (data.get("tires") or []) if isinstance(t, dict)]
        if not corners:
            return
        compound = next((c.get("compound") for c in corners
                         if c.get("compound")), None)
        ages = [a for a in (_int_or(c.get("ageInLaps"), None) for c in corners)
                if a is not None]
        self.state.car_tires[car] = {
            "compound": compound,
            "age": max(ages) if ages else None,
        }
        self._flush_car(car)

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
            self._push_status({"isFinished": True})
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
            "elapsedTime": gaps.get("elapsed_ms"),
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

        # raceFlags is the session's full historical flag log (Green→Yellow→…).
        # Replaying every entry would churn the flag through _track_caution and
        # seed a phantom caution_period — all stamped with the bootstrap instant —
        # for each past yellow, even in a green practice session. Only the CURRENT
        # session-wide state matters at connect: take the last full-course entry
        # (empty sectorNumbers) and apply it once. A genuine active caution then
        # opens exactly one real period; green opens none.
        full_course = [f for f in (data.get("raceFlags") or [])
                       if isinstance(f, dict) and not f.get("sectorNumbers")]
        if full_course:
            self._handle_race_flags(full_course[-1])

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

        # bootstrap laps arrive newest-first per car — replay them oldest-first
        # so every lap lands in lap_history and the lap counter ends on the max
        # (_handle_laps ignores regressions, so newest-first would drop all but
        # the first entry per car)
        boot_laps = [l for l in (data.get("laps") or []) if isinstance(l, dict)]
        boot_laps.sort(key=lambda l: _int_or(l.get("lapNumber"), 0))
        for lap in boot_laps:
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

        for t in (data.get("tires") or []):
            if isinstance(t, dict):
                self._handle_tires(t)

        n_cars = len(self.state.car_ranks)
        log.info("Bootstrap hydrated: %d cars, lap %d, flag %s",
                 n_cars, self.state.current_lap, self.state.current_flag)

    # ── console snapshot ─────────────────────────────────────────────────

    def _snapshot(self):
        n_cars = len(self.state.car_ranks)
        n_pit = len(self.state.pit_in_times)
        # data_age = seconds since the last LIVENESS_CHANNELS batch. Makes a
        # zombie connection visible at a glance (keepalives keep this snapshot
        # printing while data_age climbs toward STALE_TIMEOUT_S).
        age = (time.time() - self._last_message_time
               if self._last_message_time > 0 else -1)
        log.info("[%s] sid=%s  flag=%s  lap=%d  cars=%d  pit=%d  connected=%s  data_age=%.0fs",
                 time.strftime("%H:%M:%S"), self.state.sid,
                 self.state.current_flag, self.state.current_lap,
                 n_cars, n_pit, self._is_connected, age)

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
            disconnected_since = None
            while not self._stopping:
                try:
                    time.sleep(1)
                except KeyboardInterrupt:
                    self._stopping = True
                    break

                now = time.time()
                # track how long we've been without a live socket
                disconnected_since = (None if self._is_connected
                                      else (disconnected_since or now))
                restart, reason = self._should_restart(now, disconnected_since)
                if restart:
                    log.warning("%s — tearing down for full restart", reason)
                    break
        finally:
            self._cleanup()

    def _should_restart(self, now: float, disconnected_since: "float | None"):
        """Watchdog decision (pure, so it can be unit-tested). Returns
        (restart: bool, reason: str). Breaking the run() loop drops to the outer
        supervisor, which does a full rebuild (re-bootstrap + re-join) — the only
        path we've confirmed actually resumes the Griiip data stream."""
        # A — socket reports connected but the data stream has gone silent. Also
        # catches a signalrcore in-place reconnect that re-joined the group yet
        # never resumed data; measured from the last real ReceiveBatch.
        if (self._is_connected
                and self._last_message_time > 0
                and now - self._last_message_time > STALE_TIMEOUT_S):
            return True, f"No data for {STALE_TIMEOUT_S}s"
        # B — transport is down and signalrcore has not brought it back within
        # the grace window; don't sit frozen in a zombie state.
        if (disconnected_since is not None
                and now - disconnected_since > DISCONNECT_TIMEOUT_S):
            return True, f"Disconnected {DISCONNECT_TIMEOUT_S}s with no recovery"
        return False, ""

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


# ── offline capture replay ────────────────────────────────────────────────────

def iter_capture(path):
    """Yield (channel, data, ts_ms) frames from a --record capture (gzip
    JSONL). ts_ms is the recorder's wall-clock epoch ms for the frame (None on
    captures that predate the ts field). Torn trailing lines (the hard-kill
    artifact documented in WEC_RACE_WEEK.md) are skipped rather than raised —
    a crashed capture must still replay. A hard kill also leaves the gzip
    container itself without an end-of-stream trailer (`_recorder.flush()`
    sync-flushes each frame but nothing ever calls close()); Python's gzip
    reader raises EOFError hitting that missing trailer, so it's caught here
    too and treated the same as a torn trailing line — stop, don't raise."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    log.warning("Skipping torn capture line (%d bytes)", len(line))
                    continue
                if isinstance(frame, dict):
                    yield (frame.get("channel") or "", frame.get("data"),
                           _int_or(frame.get("ts"), None))
        except EOFError:
            log.warning("Capture gzip stream ended without a trailer "
                        "(hard-kill artifact) — stopping replay here")


def replay_capture(client: "WecLiveClient", path) -> tuple:
    """Feed a recorded capture through the full parse/dispatch path offline.
    Returns (n_frames, n_dispatch_errors).

    This is the race-week Commit-5 workflow: --record at FP1, then replay the
    file here against a scratch DB to find/fix field-mapping mistakes offline.
    """
    n = errs = 0
    for channel, data, ts in iter_capture(path):
        n += 1
        if channel == "_bootstrap":
            if isinstance(data, dict):
                client._hydrate_bootstrap(data)
            continue
        try:
            client._dispatch_channel(channel, data, ts_ms=ts)
        except Exception:
            errs += 1
            log.exception("Replay dispatch error on channel %s", channel)
    if client.db:
        client.db.commit()
    return n, errs


def _reanchor_clock(conn, oid: str, frame_ts_ms: int,
                    real_start_s: Optional[float]) -> None:
    """Make calculator.analyse's wall-clock elapsed correct during replay.

    analyse() derives elapsed as time.time() - start_time_s (- stopped_s);
    replayed later, that yields days. Each recorded frame carries the true
    wall-clock ts, and real_start_s is the TRUE race-start epoch from the
    feed (the caller reads it off the status accumulator, never off the DB —
    the DB's start_time_s may hold this function's own previous re-anchored
    value if no session-clock frame landed since the last cycle, and
    re-anchoring off that yields elapsed≈0 and a full-race remaining_s).
    Shift start_time_s so `now - start` reproduces elapsed-at-this-frame —
    the same trick replay.build() uses for Timing71 archives (see replay.py).
    """
    if real_start_s is None:
        return
    elapsed_s = max(0.0, frame_ts_ms / 1000.0 - real_start_s)
    conn.execute(
        "UPDATE session_status SET start_time_s=? WHERE session_oid=?",
        (time.time() - elapsed_s, oid))


def replay_predict(client: "WecLiveClient", path, cadence_s: int = 60) -> dict:
    """Replay a --record capture AND regenerate predictions offline.

    Same dispatch path as replay_capture, plus the live prediction loop:
    every cadence_s of recorded race time, run calculator.analyse() and log a
    predictions row per car stamped with the frame's ts — a deterministic
    offline rebuild of what headless_predictor logged live, but under the
    CURRENT config.json. This is the calibration loop: edit config, rebuild
    into a scratch DB, re-score with evaluator.py, repeat.
    """
    import calculator
    import predictor

    if not client.db:
        raise ValueError("replay_predict needs a DB (no_db is not supported)")
    predictor.ensure(client.db.conn)

    n = errs = n_logged = 0
    last_log_ts = None
    fallback_start_s = None
    for channel, data, ts in iter_capture(path):
        n += 1
        if ts is not None and fallback_start_s is None:
            fallback_start_s = ts / 1000.0
        if channel == "_bootstrap":
            if isinstance(data, dict):
                client._hydrate_bootstrap(data)
        else:
            try:
                client._dispatch_channel(channel, data, ts_ms=ts)
            except Exception:
                errs += 1
                log.exception("Replay dispatch error on channel %s", channel)

        oid = client.state.session_oid
        if ts is None or oid is None:
            continue
        if last_log_ts is not None and ts - last_log_ts < cadence_s * 1000:
            continue
        client.db.commit()
        if last_log_ts is None:
            # first analyse cycle: drop any lap-gap history a previous build of
            # this oid left in calculator's module-level _GAP_HIST — same
            # stale-history guard replay._init_db applies (back-to-back
            # in-process replays otherwise feed the catching gate old gaps)
            calculator.reset_gap_history(oid)
        real_start = client.state.status_acc.get("startTime") or fallback_start_s
        _reanchor_clock(client.db.conn, oid, ts, real_start)
        ctx, cars = calculator.analyse(client.db.conn, oid)
        n_logged += predictor.log_cycle(client.db.conn, oid, ctx, cars, ts)
        client.db.conn.commit()
        last_log_ts = ts

    if client.db:
        client.db.commit()
    return {"frames": n, "dispatch_errors": errs, "predictions": n_logged,
            "session_oid": client.state.session_oid}


# ── discover mode ─────────────────────────────────────────────────────────────

def discover_mode(sid: Optional[int] = None):
    """List live sessions, optionally connect to one for 30s, dump everything."""
    log.info("=== Live sessions ===")
    sessions = []
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
    ap.add_argument("--replay", metavar="FILE.jsonl.gz",
                    help="replay a --record capture offline through the full "
                         "parse/dispatch path (field-mapping iteration)")
    ap.add_argument("--replay-predict", metavar="FILE.jsonl.gz",
                    help="replay a --record capture AND regenerate the "
                         "predictions offline under the current config.json "
                         "(the calibration loop). Requires an explicit "
                         "non-production --db")
    ap.add_argument("--sid", type=int, default=None,
                    help="Griiip session ID (auto-discovers WEC if omitted)")
    args = ap.parse_args()

    if args.discover:
        discover_mode(sid=args.sid)
        return

    if args.replay_predict:
        # calibration replays must never touch the production DB — a rebuild
        # under experimental config would poison the real race's predictions
        prod_db = (root / "data" / "race.db").resolve()
        if Path(args.db).resolve() == prod_db:
            ap.error("--replay-predict refuses the production DB "
                     f"({prod_db}); pass an explicit scratch --db")
        client = WecLiveClient(db_path=args.db)
        client.db = dbmod.RaceDB(client.db_path)
        try:
            res = replay_predict(client, args.replay_predict)
            log.info("Replayed %d frames (%d dispatch errors), "
                     "%d predictions logged for %s",
                     res["frames"], res["dispatch_errors"],
                     res["predictions"], res["session_oid"])
        finally:
            client._cleanup()
        return

    if args.replay:
        client = WecLiveClient(db_path=args.db, no_db=args.no_db)
        if not client.no_db:
            client.db = dbmod.RaceDB(client.db_path)
        try:
            n, errs = replay_capture(client, args.replay)
            log.info("Replayed %d frames (%d dispatch errors)", n, errs)
        finally:
            client._cleanup()
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
            except BrokenPipeError:
                # stdout pipe to a dead parent (e.g. dashboard QProcess) —
                # nobody is reading; exit instead of reconnecting forever
                log.error("Broken pipe on stdout — parent gone, exiting.")
                break
            except Exception as e:
                log.error("Session error: %s", e, exc_info=True)

            if client._stopping:
                break

            if os.getppid() == 1:
                # reparented to launchd/init: original parent died without
                # cleanup — don't keep live subscriptions as an orphan
                log.error("Orphaned (parent died) — exiting.")
                break

            # Session handoff — WEC runs quali as back-to-back segments, each
            # with its OWN sid; race day has warmup then race. Discovery only
            # ran at process start, so after a segment ended we kept
            # re-bootstrapping its dead sid for the rest of the window (São
            # Paulo quali: GT quali captured, the other three segments lost).
            # Re-check the schedule on every restart; mid-session the current
            # sid is still the one listed, so this is a no-op until the feed
            # actually moves on. An explicit --sid pins the session and skips
            # handoff entirely.
            if args.sid is None:
                new_sid = find_wec_session()
                if new_sid and new_sid != sid:
                    log.info("Session handoff: sid %s -> %s", sid, new_sid)
                    sid = new_sid
                    # per-session state (pid map, laps, ranks, flags) must not
                    # leak across sessions — start clean like a fresh process
                    client.state = WecLiveState(sid=new_sid)
                    client._reconnect_count = 0

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
