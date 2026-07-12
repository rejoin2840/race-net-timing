"""
Al Kamel DDP scraper — IMSA WeatherTech live timing.

Connects natively via DDP over SockJS WebSocket (Meteor.js protocol).
No Playwright, no DOM — raw structured JSON from the timing server.

Subscription chain:
  livetimingFeed("imsa") → session_info → entry + sessionStatus +
  sessionClasses + pitInfo + raceControl + standings

Data structures (all confirmed against live Al Kamel IMSA feed):
  standings:     keyed by position; data="pos;car;state;clspos;laps;gap_l;gap_t;ts;status;pits"
  session_entry: keyed by car number; has driver, team, vehicle, class
  session_pit_info: keyed by car number; lastPitHour, lastPitTime, totalPitTime
  session_status: currentFlag, currentLap, startTime, isFinished, isSessionRunning
  session_classes: class colour definitions
  race_control: live race director messages

Usage:
  python src/alkameldp.py               # live polling loop
  python src/alkameldp.py --discover    # 30s dump, print all collections & exit
"""

import asyncio
import json
import logging
import os
import pathlib
import random
import string
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import websockets
from websockets.exceptions import ConnectionClosed

from db import RaceDB, DEFAULT_DB_PATH

# ── config ────────────────────────────────────────────────────────────────────
WS_HOST        = "livetiming.alkamelsystems.com"
FEED_NAME      = "imsa"
SNAPSHOT_EVERY = 10   # seconds between printed snapshots during live polling

LOG_DIR = pathlib.Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"ddp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("ddp")

# Subscriptions needed for full timing picture
TIMING_SUBS = [
    "entry",              # → session_entry        : car/driver/team/vehicle per car number
    "sessionStatus",      # → session_status        : flag, lap, clock, race state
    "sessionClasses",     # → session_classes       : class definitions & colours
    "pitInfo",            # → session_pit_info      : pit in/out timestamps per car
    "raceControl",        # → race_control          : race director messages
    "standings",          # → standings             : live positions + timing per car
    "sessionPositioning", # → session_positioning   : track map positions
]


# ── SockJS / DDP wire helpers ─────────────────────────────────────────────────
def _ws_url() -> str:
    server  = str(random.randint(0, 999)).zfill(3)
    session = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"wss://{WS_HOST}/sockjs/{server}/{session}/websocket"

def _sub_id() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=17))

def _wrap(msg: dict) -> str:
    return json.dumps([json.dumps(msg)])

def _oid_str(v) -> "str | None":
    """Unwrap Meteor EJSON oid {'$type':'oid','$value':'...'} → plain hex string."""
    if isinstance(v, dict) and "$value" in v:
        return v["$value"]
    return v if isinstance(v, str) else None


def _parse_frame(raw: str) -> list[dict]:
    if raw in ("o", "h", "c"):
        return []
    if raw.startswith("a"):
        try:
            return [json.loads(item) for item in json.loads(raw[1:])]
        except Exception:
            pass
    return []


# ── data model ────────────────────────────────────────────────────────────────
@dataclass
class CarEntry:
    """One row of timing data per car, ready for display / persistence."""
    scraped_at:       str
    # from standings.data field (semicolon-delimited)
    overall_position: Optional[int]  = None
    car_number:       Optional[str]  = None
    state:            Optional[str]  = None   # CLASSIFIED / NOT_CLASSIFIED / RETIRED
    pos_in_class:     Optional[int]  = None
    laps:             Optional[int]  = None
    laps_behind:      Optional[int]  = None   # laps behind leader (0 = lead lap); RACE only
    gap_ms:           Optional[int]  = None   # time gap to leader in ms; 0 for leader or lapped
    track_status:     Optional[str]  = None   # TRACK / BOX / OUT_LAP / IN_LAP / STOPPED
    pits:             Optional[int]  = None
    # from standings fields
    last_lap_ms:      Optional[int]  = None
    best_lap_ms:      Optional[int]  = None
    best_lap_num:     Optional[int]  = None
    is_running:       bool           = False
    # from session_entry (joined by car number)
    car_class:        Optional[str]  = None
    driver_name:      Optional[str]  = None   # current driver on track
    team:             Optional[str]  = None
    vehicle:          Optional[str]  = None
    # from session_pit_info
    last_pit_hour_ms: Optional[int]  = None   # epoch ms of last pit entry
    last_pit_session_ms: Optional[int] = None # session-elapsed ms at last pit
    total_pit_ms:     Optional[int]  = None


def _ms_to_laptime(ms: Optional[int]) -> str:
    if ms is None or ms <= 0:
        return "—"
    total_s = ms / 1000
    mins    = int(total_s // 60)
    secs    = total_s % 60
    return f"{mins}:{secs:06.3f}"


@dataclass
class RaceState:
    """Mutable in-memory snapshot of the full session."""
    # DDP subscription tracking
    feed_id:        Optional[str]              = None
    session_oids:   list                       = field(default_factory=list)
    timing_subs_sent: bool                     = False

    # session_info fields
    champ_name:     str                        = "?"
    event_name:     str                        = "?"
    session_name:   str                        = "?"
    session_type:   str                        = "?"

    # session_status fields
    current_flag:   str                        = "?"
    current_lap:    int                        = 0
    is_running:     bool                       = False
    is_finished:    bool                       = False
    start_time_s:   Optional[int]              = None

    # session_classes
    classes:        dict                       = field(default_factory=dict)

    # session_entry: car_number → entry dict
    entries:        dict[str, dict]            = field(default_factory=dict)

    # session_pit_info: car_number → pit dict
    pit_info:       dict[str, dict]            = field(default_factory=dict)

    # standings: position_key → standing dict
    standings:      dict[str, dict]            = field(default_factory=dict)

    # race_control log
    rc_messages:    list[str]                  = field(default_factory=list)


# ── data parsing ──────────────────────────────────────────────────────────────
def _parse_data_field(s: str) -> dict:
    """
    Decode Al Kamel's semicolon-delimited 'data' string in standings entries.
    Confirmed format from live IMSA feed (cross-referenced against 33-car qualifying data):

      [0] overall_position  (1-based rank across all classes)
      [1] car_number
      [2] state             (CLASSIFIED / NOT_CLASSIFIED / RETIRED)
      [3] pos_in_class
      [4] laps              (total laps completed this session)
      [5] laps_behind       (RACE: laps behind leader, 0 = lead lap;
                             QUALIFYING: classification flag, not a lap count)
      [6] gap_ms            (RACE: time gap to leader in ms, 0 = leader or lapped;
                             QUALIFYING: 0 while in session, time diff when complete)
      [7] lap_start_ts      (epoch ms — current lap start timestamp)
      [8] track_status      (TRACK / BOX / OUT_LAP / IN_LAP / STOPPED)
      [9] pits              (DO NOT TRUST: this reads as current-lap-number
                            (laps+1) in the RACE feed, not a stop count. It only
                            looked like a pit count in quali where it stayed 0.
                            Real pit detection is driven by pit_info.lastPitHour
                            in db.update_pit_info(). raw_data is persisted to
                            standings_current so the true layout can be verified
                            against a live feed.)
    """
    parts = s.split(";")
    def _int(idx): return int(parts[idx]) if len(parts) > idx and parts[idx].lstrip("-").isdigit() else None
    def _str(idx): return parts[idx].strip() if len(parts) > idx else None
    return {
        "overall_position": _int(0),
        "car_number":       _str(1),
        "state":            _str(2),
        "pos_in_class":     _int(3),
        "laps":             _int(4),
        "laps_behind":      _int(5),
        "gap_ms":           _int(6),
        # index 7 = lap start timestamp — used in Phase 3 calculations
        "track_status":     _str(8),
        "pits":             _int(9),
    }


def _build_snapshot(state: RaceState) -> list[CarEntry]:
    """Join all collections into a list of CarEntry sorted by overall position."""
    now = datetime.utcnow().isoformat() + "Z"
    entries: list[CarEntry] = []

    for _pos_key, standing in state.standings.items():
        raw_data = standing.get("data", "")
        if not raw_data:
            continue
        d = _parse_data_field(raw_data)
        car_num = d.get("car_number")

        # Join session_entry (driver / team / vehicle)
        entry_info = state.entries.get(car_num, {}) if car_num else {}
        pit        = state.pit_info.get(car_num, {}) if car_num else {}

        ce = CarEntry(
            scraped_at         = now,
            overall_position   = d["overall_position"],
            car_number         = car_num,
            state              = d["state"],
            pos_in_class       = d["pos_in_class"],
            laps               = d["laps"],
            laps_behind        = d["laps_behind"],
            gap_ms             = d["gap_ms"],
            track_status       = d["track_status"],
            pits               = d["pits"],
            last_lap_ms        = standing.get("lastLapTime"),
            best_lap_ms        = standing.get("bestLapTime"),
            best_lap_num       = standing.get("bestLapNumber"),
            is_running         = standing.get("isRunning", False),
            car_class          = standing.get("class") or entry_info.get("class"),
            driver_name        = entry_info.get("name"),         # current driver
            team               = entry_info.get("team"),
            vehicle            = entry_info.get("vehicle"),
            last_pit_hour_ms   = pit.get("lastPitHour"),
            last_pit_session_ms= pit.get("lastPitTime"),
            total_pit_ms       = pit.get("totalPitTime"),
        )
        entries.append(ce)

    return sorted(entries, key=lambda e: (e.overall_position or 9999,))


def _print_snapshot(entries: list[CarEntry], state: RaceState, cycle: int) -> None:
    flag_symbols = {"GF": "🟢 GREEN", "YF": "🟡 YELLOW", "RF": "🔴 RED",
                    "CH": "🏁 CHECKERED", "CY": "🟡 FCY", "SC": "🚗 SC", "VSC": "🚗 VSC"}
    flag_str = flag_symbols.get(state.current_flag, f"? {state.current_flag}")

    print(f"\n{'═'*100}")
    print(f"  {state.event_name}  |  {state.session_name}  |  Lap {state.current_lap}  |  "
          f"{flag_str}  |  Cycle #{cycle:04d}  {datetime.utcnow().strftime('%H:%M:%S')} UTC")
    print(f"{'═'*100}")
    print(f"  {'OVR':>3}  {'CAR':>4}  {'CLS':<8}  {'CLP':>3}  {'DRIVER':<22}  "
          f"{'LAP':>4}  {'STATUS':<8}  {'LAST LAP':>9}  {'BEST LAP':>9}  {'GAP':>9}  "
          f"{'PITS':>4}  {'LAST PIT':>9}")
    print(f"  {'---':>3}  {'---':>4}  {'---':<8}  {'---':>3}  {'------':<22}  "
          f"{'---':>4}  {'------':<8}  {'--------':>9}  {'--------':>9}  {'---':>9}  "
          f"{'----':>4}  {'---------':>9}")

    is_race = state.session_type not in ("QUALIFYING_BEST_LAP", "QUALIFYING_AVG_LAP",
                                         "PRACTICE", "WARM_UP")
    for e in entries:
        if e.overall_position == 1:
            gap_str = "LEADER"
        elif is_race and (e.laps_behind or 0) > 0:
            gap_str = f"+{e.laps_behind}L"
        elif (e.gap_ms or 0) > 0:
            gap_str = _ms_to_laptime(e.gap_ms)
        elif is_race:
            gap_str = "—"
        else:
            # Qualifying: show time diff from best laps (computed in Phase 3)
            gap_str = "—"

        last_pit_str = _ms_to_laptime(e.last_pit_session_ms) if e.last_pit_session_ms else "—"

        print(
            f"  {str(e.overall_position or '?'):>3}  "
            f"{(e.car_number or '?'):>4}  "
            f"{(e.car_class or '?'):<8}  "
            f"{str(e.pos_in_class or '?'):>3}  "
            f"{(e.driver_name or e.team or '?'):<22}  "
            f"{str(e.laps or '?'):>4}  "
            f"{(e.track_status or '?')[:8]:<8}  "
            f"{_ms_to_laptime(e.last_lap_ms):>9}  "
            f"{_ms_to_laptime(e.best_lap_ms):>9}  "
            f"{gap_str:>9}  "
            f"{str(e.pits or '—'):>4}  "
            f"{last_pit_str:>9}"
        )

    # Recent race control
    if state.rc_messages:
        print(f"\n  [RACE CONTROL] {state.rc_messages[-1]}")
    print()


# ── DDP client ────────────────────────────────────────────────────────────────
class AlKamelClient:
    def __init__(self, db: "RaceDB | None" = None):
        self.state    = RaceState()
        self._cycle   = 0
        self._ws      = None
        self._pending: dict[str, str] = {}   # sub_id → sub_name
        self.db       = db                    # None → run without persistence

    # ── persistence helpers ──────────────────────────────────────────────────
    def _persist_entries(self, update: dict) -> None:
        if not self.db:
            return
        for car, e in update.items():
            if isinstance(e, dict):
                self.db.upsert_entry(car, e)
                self.db.record_driver(car, e.get("name"), self.state.current_lap)
        self.db.commit()

    def _persist_pit(self, update: dict) -> None:
        if not self.db:
            return
        for car, pit in update.items():
            if isinstance(pit, dict):
                self.db.update_pit_info(car, pit)
        self.db.commit()

    def _persist_standings(self, keys) -> None:
        """Ingest the merged standings entries for the given position keys."""
        if not self.db:
            return
        for k in keys:
            st = self.state.standings.get(k)
            if not isinstance(st, dict):
                continue
            raw = st.get("data", "")
            if not raw:
                continue
            d   = _parse_data_field(raw)
            car = d.get("car_number")
            if car:
                self.db.ingest_car(car, d, st, self.state.current_lap,
                                   self.state.current_flag, raw_data=raw)
        self.db.commit()

    async def _sub(self, name: str, params: list = None) -> str:
        sid = _sub_id()
        self._pending[sid] = name
        await self._ws.send(_wrap({"msg": "sub", "id": sid, "name": name, "params": params or []}))
        return sid

    async def _handle(self, msg: dict) -> None:
        mt   = msg.get("msg", "")
        coll = msg.get("collection", "")
        oid  = msg.get("id", "")
        flds = msg.get("fields", {})

        # ── connected → subscribe to feed ──────────────────────────────────
        if mt == "connected":
            log.info("DDP connected")
            await self._sub("livetimingFeed", [FEED_NAME])

        # ── added ──────────────────────────────────────────────────────────
        elif mt == "added":
            if coll == "feeds":
                sessions = flds.get("sessions", [])
                self.state.session_oids = sessions
                log.info("Feed '%s' — %d session(s)", FEED_NAME, len(sessions))
                if sessions:
                    await self._sub("sessionInfo", [sessions])

            elif coll == "session_info":
                info   = flds.get("info", {})
                active = flds.get("session")
                self.state.champ_name    = info.get("champName", "?")
                self.state.event_name    = info.get("eventName", "?")
                self.state.session_name  = info.get("name", "?")
                self.state.session_type  = info.get("type", "?")
                log.info("Session: %s — %s (%s)", self.state.event_name,
                         self.state.session_name, self.state.session_type)
                if self.db:
                    self.db.set_session(_oid_str(active), info)
                if not self.state.timing_subs_sent:
                    self.state.timing_subs_sent = True
                    params = [active] if active else []
                    for s in TIMING_SUBS:
                        await self._sub(s, params)
                    log.info("Subscribed to %d timing feeds", len(TIMING_SUBS))

            elif coll == "session_status":
                status = flds.get("status", {})
                self._update_status(status)

            elif coll == "session_classes":
                self.state.classes = flds.get("classes", {}).get("classes", {})

            elif coll == "session_entry":
                update = flds.get("entry", {})
                self.state.entries.update(update)
                log.info("Entry list: %d cars", len(self.state.entries))
                self._persist_entries(update)

            elif coll == "session_pit_info":
                update = flds.get("pitOuts", {})
                self.state.pit_info.update(update)
                log.debug("Pit info: %d cars", len(self.state.pit_info))
                self._persist_pit(update)

            elif coll == "standings":
                # flds["standings"] = {"displayDrivers":bool, "hasClasses":bool, "standings":{pos→car}}
                outer = flds.get("standings", {})
                inner = outer.get("standings", outer)   # drill into nested "standings" key
                merged = {k: v for k, v in inner.items() if isinstance(v, dict)}
                self.state.standings.update(merged)
                log.info("Standings: %d entries", len(self.state.standings))
                self._persist_standings(merged.keys())

            elif coll == "race_control":
                self._update_rc(flds)

        # ── changed ────────────────────────────────────────────────────────
        elif mt == "changed":
            if coll == "session_status":
                self._update_status(flds.get("status", {}))

            elif coll == "session_entry":
                update = flds.get("entry", {})
                for k, v in update.items():
                    if k in self.state.entries:
                        self.state.entries[k].update(v)
                    else:
                        self.state.entries[k] = v
                self._persist_entries(update)

            elif coll == "session_pit_info":
                update = flds.get("pitOuts", {})
                for k, v in update.items():
                    if k in self.state.pit_info:
                        self.state.pit_info[k].update(v)
                    else:
                        self.state.pit_info[k] = v
                self._persist_pit(update)

            elif coll == "standings":
                outer  = flds.get("standings", {})
                update = outer.get("standings", outer)  # drill into nested "standings" key
                changed_keys = []
                for k, v in update.items():
                    if not isinstance(v, dict):
                        continue
                    if k in self.state.standings:
                        self.state.standings[k].update(v)
                    else:
                        self.state.standings[k] = v
                    changed_keys.append(k)
                self._persist_standings(changed_keys)

            elif coll == "race_control":
                self._update_rc(flds)

        # ── nosub (subscription not found — log and ignore) ────────────────
        elif mt == "nosub":
            err = msg.get("error", {}).get("reason", "?")
            log.warning("nosub: %s", err)

    def _update_status(self, status: dict) -> None:
        if not status:
            return
        self.state.current_flag = status.get("currentFlag", self.state.current_flag)
        self.state.current_lap  = status.get("currentLap",  self.state.current_lap)
        self.state.is_running   = status.get("isSessionRunning", self.state.is_running)
        self.state.is_finished  = status.get("isFinished",  self.state.is_finished)
        if "startTime" in status:
            self.state.start_time_s = status["startTime"]
        if self.db:
            self.db.update_status(status)
            self.db.commit()

    def _update_rc(self, flds: dict) -> None:
        rc   = flds.get("raceControlMessages", {})
        curr = rc.get("currentMessages", {})
        log_msgs = rc.get("log", {})
        for v in curr.values():
            msg_text = v.get("message", "") if isinstance(v, dict) else ""
            if msg_text and (not self.state.rc_messages or self.state.rc_messages[-1] != msg_text):
                self.state.rc_messages.append(msg_text)
                log.info("[RC] %s", msg_text)
        if self.db:
            batch = []
            for v in log_msgs.values():           # timestamped history (authoritative)
                if isinstance(v, dict):
                    batch.append((v.get("date"), v.get("message")))
            for v in curr.values():               # current display (may lack a timestamp)
                if isinstance(v, dict):
                    batch.append((v.get("date"), v.get("message")))
            if batch:
                self.db.record_race_control(batch)
                self.db.commit()

    def snapshot(self) -> None:
        self._cycle += 1
        entries = _build_snapshot(self.state)
        if entries:
            _print_snapshot(entries, self.state, self._cycle)
            out = LOG_DIR / f"cycle_{self._cycle:04d}.json"
            out.write_text(
                json.dumps([asdict(e) for e in entries], indent=2),
                encoding="utf-8",
            )
            log.debug("Snapshot → %s", out)
        else:
            log.info("Cycle #%d: no standings data yet", self._cycle)

    async def run_once(self) -> None:
        # Each fresh SockJS connection must re-run the subscription chain.
        self.state.timing_subs_sent = False
        url = _ws_url()
        log.info("Connecting to %s ...", url)

        async with websockets.connect(
            url,
            additional_headers={"Origin": f"https://{WS_HOST}"},
            ping_interval=20,
            close_timeout=5,
        ) as ws:
            self._ws = ws

            first = await ws.recv()
            if first == "o":
                log.info("SockJS open")
            else:
                log.warning("Unexpected first frame: %r", first[:100])

            await ws.send(_wrap({"msg": "connect", "version": "1",
                                 "support": ["1", "pre2", "pre1"]}))

            last_snapshot = asyncio.get_event_loop().time()
            async for raw in ws:
                for msg in _parse_frame(raw):
                    await self._handle(msg)

                now = asyncio.get_event_loop().time()
                if now - last_snapshot >= SNAPSHOT_EVERY and self.state.standings:
                    self.snapshot()
                    last_snapshot = now


# ── discover helper ───────────────────────────────────────────────────────────
async def discover_mode() -> None:
    """Subscribe to full chain and dump everything to logs/discover_all.json."""
    log.info("Discover mode — 30s observation")
    client  = AlKamelClient()
    url     = _ws_url()
    all_msgs: list[dict] = []

    async with websockets.connect(
        url,
        additional_headers={"Origin": f"https://{WS_HOST}"},
        ping_interval=20,
    ) as ws:
        client._ws = ws
        first = await ws.recv()
        log.info("SockJS: %r", first)
        await ws.send(_wrap({"msg": "connect", "version": "1",
                             "support": ["1", "pre2", "pre1"]}))
        try:
            deadline = asyncio.get_event_loop().time() + 30
            while asyncio.get_event_loop().time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                for msg in _parse_frame(raw):
                    all_msgs.append(msg)
                    log.info("[%s] coll=%s keys=%s",
                             msg.get("msg","?"),
                             msg.get("collection","—"),
                             list(msg.get("fields",{}).keys())[:6])
                    await client._handle(msg)
        except asyncio.TimeoutError:
            pass
        except ConnectionClosed:
            pass

    out = LOG_DIR / "discover_all.json"
    out.write_text(json.dumps(all_msgs, indent=2), encoding="utf-8")
    colls  = {m.get("collection") for m in all_msgs if m.get("collection")}
    nosubs = [m for m in all_msgs if m.get("msg") == "nosub"]
    log.info("Collections seen: %s", sorted(colls))
    log.info("nosub errors: %d", len(nosubs))
    log.info("Full dump → %s", out)

    # Print current snapshot
    if client.state.standings:
        client.snapshot()


# ── main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    if "--discover" in sys.argv:
        await discover_mode()
        return

    db = None if "--no-db" in sys.argv else RaceDB(DEFAULT_DB_PATH)
    if db:
        log.info("Persisting to %s", db.path)

    log.info("Starting IMSA Al Kamel live timing scraper (Ctrl-C to stop)")
    client = AlKamelClient(db=db)
    try:
        while True:
            try:
                await client.run_once()
            except ConnectionClosed as e:
                log.warning("Connection closed: %s", e)
            except KeyboardInterrupt:
                log.info("Stopped.")
                break
            except BrokenPipeError:
                # stdout pipe to a dead parent (e.g. dashboard QProcess) —
                # nobody is reading; exit instead of reconnecting forever
                log.error("Broken pipe on stdout — parent gone, exiting.")
                break
            except Exception as e:
                log.error("Session error: %s", e)

            if os.getppid() == 1:
                # reparented to launchd/init: original parent died without
                # cleanup — don't keep live subscriptions as an orphan
                log.error("Orphaned (parent died) — exiting.")
                break

            log.info("Reconnecting in 5s ...")
            try:
                await asyncio.sleep(5)
            except KeyboardInterrupt:
                break
    finally:
        if db:
            db.close()
            log.info("Database closed.")


if __name__ == "__main__":
    asyncio.run(main())
