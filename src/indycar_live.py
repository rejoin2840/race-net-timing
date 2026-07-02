"""
indycar_live.py — live IndyCar timing scraper (direct from INDYCAR's public feed).

Source: the same Azure blob INDYCAR's own leaderboard (and Timing71's IndyCar
connector) polls — discovered by reading T71's shipped connector JS:

    https://indycar.blob.core.windows.net/racecontrol/timingscoring-ris.json?<ts>

Plain HTTPS GET, JSONP-wrapped JSON, refreshed ~1s during sessions, no auth.
Between sessions the blob keeps serving the LAST session's payload (frozen),
which is what makes this scraper testable any day of the week.

Field semantics (verified against live payloads + T71's parser):
  - `Item` array order == running order (overallRank/liveRank agree with index).
  - `diff` = gap to LEADER (cumulative), `gap` = interval to car AHEAD.
    Yes, INDYCAR's naming is inverted from intuition — T71 maps diff->GAP col,
    gap->INT col, and cumulative sums confirm it (P3.diff = P2.diff + P3.gap).
  - `marker` == "InPit" (with onTrack == "False") while a car is in pit lane;
    "Finished" at the flag. `status` == "Active" normally, "dnf"/etc. when out.
  - `lastPitLap` / `pitStops` are authoritative per-car pit data — better than
    anything the IMSA feed gives us. lastPitLap covers pre-connect stops too,
    so stint math works even if we join mid-session.
  - Lap times are strings: "24.0376" (S.ssss) or "15:43.9114" (M:SS.ssss).
  - heartbeat carries flag/session identity; flagCounts.green[0] is an ISO
    timestamp of session start (clock anchor).

Stop DURATIONS aren't in the feed — we measure them ourselves from InPit
state transitions (debounced), and feed db.update_pit_info() a synthetic
lastPitHour (pit-entry wall clock) + measured lastPitTime, matching the
IMSA path's contract: a CHANGED lastPitHour == one new completed stop.

Run:  venv/bin/python src/indycar_live.py               # scrape -> data/race.db
      venv/bin/python src/indycar_live.py --discover    # dump one payload, exit
      venv/bin/python src/indycar_live.py --record F    # also append raw JSONL
"""

import argparse
import gzip
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import db as dbmod

FEED_URL = "https://indycar.blob.core.windows.net/racecontrol/timingscoring-ris.json"
POLL_S = 1.0                 # feed refresh cadence during sessions
SNAPSHOT_EVERY_S = 10        # console status line cadence
STALE_WARN_S = 90            # warn if payload hash unchanged this long under green
PIT_DEBOUNCE_POLLS = 2       # consecutive polls before a pit state change is believed
HTTP_TIMEOUT_S = 10

TIRE_MAP = {"P": "PRIMARY", "O": "ALTERNATE", "W": "WET"}

# INDYCAR flag strings -> our canonical flags (calculator/dashboard vocabulary)
FLAG_MAP = {
    "GREEN": "GF", "YELLOW": "FCY", "RED": "RF", "CHECKERED": "CH",
    "CHECKERED FLAG": "CH", "WHITE": "GF", "COLD": "COLD", "WARMUP": "COLD",
}


# ── parsing helpers ──────────────────────────────────────────────────────────

def _laptime_ms(v) -> "int | None":
    """'24.0376' or '15:43.9114' (or numeric seconds) -> int ms; None if unusable."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        if ":" in s:
            mins, rest = s.split(":", 1)
            return int(round((int(mins) * 60 + float(rest)) * 1000))
        return int(round(float(s) * 1000))
    except (ValueError, TypeError):
        return None


_LAPS_RE = re.compile(r"(\d+)\s*LAP", re.IGNORECASE)


def _gap_fields(v) -> "tuple[int, int]":
    """A diff/gap cell -> (gap_ms, laps_behind). Cells are '1.2345' seconds or
    'N LAPS' strings for lapped cars (mirrors replay.py's handling)."""
    if v is None:
        return 0, 0
    s = str(v).strip()
    if not s:
        return 0, 0
    m = _LAPS_RE.search(s)
    if m:
        return 0, int(m.group(1))
    try:
        return int(round(float(s) * 1000)), 0
    except (ValueError, TypeError):
        return 0, 0


def _time_to_go_s(v) -> "int | None":
    """'08:50' or '1:08:50' -> seconds remaining."""
    if not v:
        return None
    try:
        parts = [int(p) for p in str(v).split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _iso_epoch_s(v) -> "int | None":
    """'2026-06-23T17:00:00+00:00' -> epoch seconds."""
    if not v:
        return None
    try:
        return int(datetime.fromisoformat(str(v)).timestamp())
    except ValueError:
        return None


def fetch_payload() -> "dict | None":
    url = f"{FEED_URL}?{int(time.time() * 1000)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "race-net-timing/1.0"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ⚠ fetch failed: {e}", file=sys.stderr)
        return None
    raw = re.sub(r"^jsonCallback\(", "", raw.strip())
    raw = re.sub(r"\);?$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"  ⚠ bad JSON: {e}", file=sys.stderr)
        return None


# ── scraper ──────────────────────────────────────────────────────────────────

class IndyCarLive:
    def __init__(self, db_path: str, record_path: "str | None" = None):
        self.db_path = db_path
        self.db: "dbmod.RaceDB | None" = None
        self.oid: "str | None" = None
        self.record_path = record_path
        # pit state machine: car -> {"in_pit": bool, "pending": (state, count),
        #                            "entry_ms": int|None}
        self._pit: dict = {}
        self._prev_pitstops: dict = {}
        self._entries_written: set = set()
        self._last_hash = None
        self._last_change_t = time.time()
        self._last_snapshot_t = 0.0

    # -- session identity ----------------------------------------------------
    def _ensure_session(self, hb: dict):
        eid = str(hb.get("EventID") or "").strip()
        sid = str(hb.get("EventSessionID") or "").strip()
        if eid or sid:
            ident = f"{eid}_{sid}"
        else:
            # between sessions (and for some test events) the IDs are blank —
            # fall back to a name slug so distinct sessions still get distinct oids
            slug = f"{hb.get('eventName', '')}_{hb.get('SessionName', '')}"
            ident = re.sub(r"[^\w]+", "_", slug).strip("_").lower()[:60] or "unknown"
        oid = f"indycar_live_{ident}"
        if oid == self.oid:
            return
        # new session (or first payload): (re)open the DB bound to this oid
        self.db = dbmod.RaceDB(self.db_path)
        self.db.set_session(oid, {
            "champName": "IndyCar",
            "eventName": hb.get("eventName") or hb.get("trackName") or "IndyCar",
            "name": hb.get("SessionName") or hb.get("preamble") or "Session",
            "type": {"R": "RACE", "Q": "QUALIFYING", "P": "PRACTICE",
                     "I": "QUALIFYING"}.get(str(hb.get("SessionType", ""))[:1], "SESSION"),
        }, series="indycar")
        self.oid = oid
        self._pit.clear()
        self._prev_pitstops.clear()
        self._entries_written.clear()
        print(f"  session: {oid}  ({hb.get('eventName')} — {hb.get('SessionName')})")

    # -- pit state machine ---------------------------------------------------
    def _pit_state(self, car: str, in_pit_now: bool, now_ms: int) -> bool:
        """Debounced pit tracker. Returns the BELIEVED in-pit state. On a believed
        pit EXIT, reports the completed stop to db.update_pit_info()."""
        st = self._pit.setdefault(car, {"in_pit": False, "pending": None, "entry_ms": None})
        if in_pit_now == st["in_pit"]:
            st["pending"] = None
            return st["in_pit"]
        # state differs from believed: debounce
        if st["pending"] and st["pending"][0] == in_pit_now:
            count = st["pending"][1] + 1
        else:
            count = 1
        st["pending"] = (in_pit_now, count)
        if count < PIT_DEBOUNCE_POLLS:
            return st["in_pit"]
        # believed transition
        st["in_pit"] = in_pit_now
        st["pending"] = None
        if in_pit_now:
            st["entry_ms"] = now_ms
        else:
            entry = st["entry_ms"]
            st["entry_ms"] = None
            if entry:
                self.db.update_pit_info(car, {
                    "lastPitHour": entry,                    # unique per stop
                    "lastPitTime": max(0, now_ms - entry),   # measured duration
                    "totalPitTime": None,
                })
        return st["in_pit"]

    # -- one payload -> DB ----------------------------------------------------
    def ingest(self, payload: dict):
        tr = payload.get("timing_results") or {}
        hb = tr.get("heartbeat") or {}
        items = tr.get("Item") or []
        if not hb or not isinstance(items, list):
            return
        self._ensure_session(hb)
        now_ms = int(time.time() * 1000)

        # session status / clock / flag
        flag_raw = str(hb.get("currentFlag") or "").upper()
        flag = FLAG_MAP.get(flag_raw, flag_raw or None)
        remain_s = _time_to_go_s(hb.get("overallTimeToGo"))
        greens = (hb.get("flagCounts") or {}).get("green") or []
        start_s = _iso_epoch_s(greens[0]) if greens else None
        session_lap = max((int(e.get("laps") or 0) for e in items), default=None)
        finished = any(str(e.get("marker")) == "Finished" for e in items[:3])
        status = {
            "currentFlag": flag,
            "currentLap": session_lap,
            "isSessionRunning": bool(flag_raw and flag_raw not in ("COLD", "CHECKERED")),
            "isFinished": finished,
            "startTime": start_s,
            "stoppedSeconds": 0,
        }
        total_laps = hb.get("totalLaps")
        if str(hb.get("SessionType", "")).upper().startswith("R") and total_laps:
            status["finalType"] = "BY_LAPS"
            status["finalLaps"] = int(total_laps)
        elif remain_s is not None and start_s is not None:
            status["finalType"] = "BY_TIME"
            status["finalTime"] = int(time.time()) - start_s + remain_s
        self.db.update_status(status)

        # cars — array order IS the running order
        pos = 0
        for e in items:
            car = str(e.get("no") or "").strip()
            if not car:
                continue
            try:
                if int(e.get("DriverID") or 0) <= 0:
                    continue                        # placeholder entries (T71 does the same)
            except (TypeError, ValueError):
                pass
            pos += 1
            laps = int(e.get("laps") or 0)
            gap_ms, laps_behind = _gap_fields(e.get("diff"))     # diff == gap to leader

            marker = str(e.get("marker") or "")
            status_s = str(e.get("status") or "").lower()
            in_pit_now = marker == "InPit" or str(e.get("onTrack")) == "False"
            dnf = status_s.startswith("dnf") or "withdrawn" in status_s
            in_pit = self._pit_state(car, in_pit_now and not dnf, now_ms)
            track_status = ("STOPPED" if dnf else
                            "PIT" if in_pit else "TRACK")

            # authoritative pit context from the feed (covers pre-connect stops):
            # seed RaceDB's internal last-pit-lap tracker so stint/fuel math works
            # from the first frame. Documented internals, no schema change.
            try:
                lpl = int(e.get("lastPitLap") or 0)
                if lpl > 0:
                    self.db._last_pit_lap[car] = lpl
            except (TypeError, ValueError):
                lpl = 0

            tire = TIRE_MAP.get(str(e.get("Tire") or "").strip(), None)
            tire_age = (laps - lpl) if (laps and lpl) else (laps or None)

            d = {
                "overall_position": pos,
                "pos_in_class": pos,                 # single class
                "laps": laps,
                "laps_behind": laps_behind,
                "gap_ms": gap_ms,
                "track_status": track_status,
            }
            standing = {
                "class": "INDYCAR",
                "isRunning": not dnf,
                "lastLapTime": _laptime_ms(e.get("lastLapTime")),
                "bestLapTime": _laptime_ms(e.get("bestLapTime")),
                "bestLapNumber": e.get("bestLap"),
                "lastSectors": None,
                "elapsedTime": None,
                "tireCompound": tire,
                "tireAge": tire_age,
            }
            self.db.ingest_car(car, d, standing, session_lap, flag)

            key = (car, e.get("firstName"), e.get("lastName"), e.get("team"))
            if key not in self._entries_written:
                self._entries_written.add(key)
                name = " ".join(p for p in (e.get("firstName"), e.get("lastName")) if p)
                self.db.upsert_entry(car, {
                    "name": name or None,
                    "team": e.get("team"),
                    "vehicle": e.get("equipment"),
                    "class": "INDYCAR",
                    "drivers": [name] if name else None,
                })
        self.db.commit()

    # -- console -------------------------------------------------------------
    def _snapshot(self, payload: dict):
        tr = payload.get("timing_results") or {}
        hb = tr.get("heartbeat") or {}
        items = tr.get("Item") or []
        top = "  ".join(f"P{i+1} #{e.get('no')}" for i, e in enumerate(items[:5]))
        print(f"  [{time.strftime('%H:%M:%S')}] {hb.get('currentFlag'):>6}  "
              f"{len(items)} cars  togo {hb.get('overallTimeToGo')}  {top}")

    # -- main loop -----------------------------------------------------------
    def run(self, poll_s: float = POLL_S):
        rec = None
        if self.record_path:
            rec = gzip.open(self.record_path, "at", encoding="utf-8")
            print(f"  recording raw payloads -> {self.record_path}")
        print(f"  polling {FEED_URL} every {poll_s}s  (Ctrl-C to stop)")
        try:
            while True:
                t0 = time.time()
                payload = fetch_payload()
                if payload:
                    h = hash(json.dumps(payload, sort_keys=True))
                    if h != self._last_hash:
                        self._last_hash = h
                        self._last_change_t = t0
                        if rec:
                            rec.write(json.dumps({"ts": int(t0 * 1000),
                                                  "payload": payload}) + "\n")
                    elif (t0 - self._last_change_t) > STALE_WARN_S:
                        hb = (payload.get("timing_results") or {}).get("heartbeat") or {}
                        if str(hb.get("currentFlag", "")).upper() == "GREEN":
                            print(f"  ⚠ payload unchanged {int(t0 - self._last_change_t)}s "
                                  f"under green — feed stalled?", file=sys.stderr)
                            self._last_change_t = t0   # don't spam
                    try:
                        self.ingest(payload)
                    except Exception as e:
                        print(f"  ⚠ ingest error: {e}", file=sys.stderr)
                    if t0 - self._last_snapshot_t >= SNAPSHOT_EVERY_S:
                        self._last_snapshot_t = t0
                        self._snapshot(payload)
                time.sleep(max(0.0, poll_s - (time.time() - t0)))
        except KeyboardInterrupt:
            print("\n  stopped.")
        finally:
            if rec:
                rec.close()


def discover():
    payload = fetch_payload()
    if not payload:
        print("no payload — feed unreachable")
        return 1
    tr = payload.get("timing_results") or {}
    hb = tr.get("heartbeat") or {}
    items = tr.get("Item") or []
    out = Path(__file__).resolve().parent.parent / "logs" / "indycar_discover.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(json.dumps(hb, indent=2))
    print(f"\n  {len(items)} cars; full payload saved -> {out}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parent.parent
    ap.add_argument("--db", default=str(root / "data" / "race.db"))
    ap.add_argument("--poll", type=float, default=POLL_S)
    ap.add_argument("--record", metavar="FILE.jsonl.gz",
                    help="append raw payloads (gzip JSONL) for offline analysis")
    ap.add_argument("--discover", action="store_true",
                    help="fetch one payload, dump heartbeat, save full JSON, exit")
    args = ap.parse_args()
    if args.discover:
        sys.exit(discover())
    IndyCarLive(args.db, record_path=args.record).run(poll_s=args.poll)


if __name__ == "__main__":
    main()
