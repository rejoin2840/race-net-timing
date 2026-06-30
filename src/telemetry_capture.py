"""
telemetry_capture.py — recorder for the IMSA live telemetry feed (imsa.com/telemetry).

That dashboard is an Angular/Amplify app subscribing to the **AWS AppSync Events API**
(channel-based pub/sub over a websocket; AppSync + Kinesis backend). The data is GTP-only:
fuel/energy, tyre life, pit / jack status. This module connects to the AppSync Events realtime
endpoint, subscribes to the telemetry channels, and appends every frame to a timestamped JSONL
so we have a real sample to build the fuel scraper against (and, eventually, a live feed for the
dashboard's "DUE" call).

Why this exists: the live Al Kamel timing feed carries NO fuel data, and the earlier capture
attempt produced a 0-byte file because a plain curl can't complete the AppSync websocket
handshake. This recorder does the handshake properly and is **loud** — it prints a live frame
counter and exits non-zero if it never gets connection_ack / subscribe_success or sees zero data
frames, so an empty capture can never happen silently again.

AppSync Events realtime protocol (confirmed from a live HAR, 2026-06-30):
  connect  wss://<id>.appsync-realtime-api.<region>.amazonaws.com/event/realtime
           Sec-WebSocket-Protocol: aws-appsync-event-ws, header-<base64url {host, x-api-key}>
  send     {"type":"connection_init"}
  recv     {"type":"connection_ack","connectionTimeoutMs":300000}
  send     {"type":"subscribe","id":<uuid>,"channel":"telemetry/message",
            "authorization":{"host":<api_host>,"x-api-key":<key>}}        (one per channel)
  recv     {"id":<uuid>,"type":"subscribe_success"}
  recv     {"id":<uuid>,"type":"data","event":"<json string>"}            (the telemetry)
  recv     {"type":"ka"}                                                  (keepalive)

Connection details (endpoint host, api key, channels) are captured from the page's network
traffic — see config/telemetry_feed.example.json. config/telemetry_feed.json holds the real ones.

Usage:
  ./venv/bin/python src/telemetry_capture.py --selftest    # prove handshake+subscribe (any time)
  ./venv/bin/python src/telemetry_capture.py               # record forever (use at a green session)
  ./venv/bin/python src/telemetry_capture.py --seconds 600 # record for 10 min then stop
  ./venv/bin/python src/telemetry_capture.py --config path/to/feed.json
"""

from __future__ import annotations  # PEP 604 "X | None" hints on Python 3.9

import asyncio
import base64
import json
import logging
import pathlib
import sys
import time
import uuid
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "telemetry_feed.json"
EXAMPLE_PATH = ROOT / "config" / "telemetry_feed.example.json"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("telemetry")

COUNTER_EVERY_S = 5          # how often to print the live frame counter
ACK_TIMEOUT_S = 15          # how long to wait for connection_ack before declaring failure
RECONNECT_EVERY_S = 5
DEFAULT_CHANNELS = ["telemetry/message", "telemetry/session"]


# ── config ────────────────────────────────────────────────────────────────────
class ConfigError(Exception):
    pass


def load_config(path: pathlib.Path) -> dict:
    """Load + validate the AppSync Events connection config, with actionable errors.
    Placeholder values from the example file are treated as 'not filled in yet' so we fail
    loudly with instructions rather than connecting to nothing (the old empty-capture trap)."""
    if not path.exists():
        raise ConfigError(
            f"No telemetry feed config at {path}.\n"
            f"  → Copy {EXAMPLE_PATH.name} to {path.name} and fill in the endpoint + key +\n"
            f"    channels captured from imsa.com/telemetry (see that file's _README)."
        )
    try:
        cfg = json.loads(path.read_text())
    except Exception as e:
        raise ConfigError(f"Could not parse {path}: {e}")

    api_host = (cfg.get("api_host") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    channels = cfg.get("channels") or []
    missing = []
    if not api_host or "EXAMPLEID" in api_host:
        missing.append("api_host (e.g. <id>.appsync-api.<region>.amazonaws.com)")
    if not api_key or "REPLACE" in api_key:
        missing.append("api_key (the da2-... AppSync key)")
    if not channels:
        missing.append("channels (e.g. [\"telemetry/message\", \"telemetry/session\"])")
    if missing:
        raise ConfigError(
            "Telemetry feed config is incomplete — still needs:\n  - "
            + "\n  - ".join(missing)
            + f"\nSee {EXAMPLE_PATH} for how to capture these from the live page."
        )

    realtime_url = (cfg.get("realtime_url") or "").strip()
    if not realtime_url:
        host = api_host.replace("appsync-api", "appsync-realtime-api")
        realtime_url = f"wss://{host}/event/realtime"
    return {
        "realtime_url": realtime_url,
        "api_host": api_host,
        "api_key": api_key,
        "channels": channels,
    }


# ── AppSync Events wire helpers ───────────────────────────────────────────────
def _auth(cfg: dict) -> dict:
    # API-key auth needs only host + x-api-key (no x-amz-date, which is IAM/SigV4 only — and
    # would expire). This is why we rebuild it rather than replay the captured header.
    return {"host": cfg["api_host"], "x-api-key": cfg["api_key"]}


def _subprotocols(cfg: dict) -> list[str]:
    raw = json.dumps(_auth(cfg), separators=(",", ":")).encode()
    header_b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")  # base64url, no padding
    return ["aws-appsync-event-ws", f"header-{header_b64}"]


def _subscribe_message(cfg: dict, channel: str) -> dict:
    return {
        "type": "subscribe",
        "id": str(uuid.uuid4()),
        "channel": channel,
        "authorization": _auth(cfg),
    }


# ── recorder ──────────────────────────────────────────────────────────────────
class TelemetryRecorder:
    def __init__(self, cfg: dict, out_path: pathlib.Path | None):
        self.cfg = cfg
        self.out_path = out_path
        self._fh = out_path.open("a", encoding="utf-8") if out_path else None
        self.total_frames = 0          # every message from the server
        self.data_frames = 0           # 'data' (actual telemetry) messages
        self.got_ack = False
        self.subscribed = set()        # channels that returned subscribe_success
        self._sub_ids: dict[str, str] = {}   # id → channel (to resolve subscribe_success)

    def _record(self, msg: dict) -> None:
        if self._fh is None:
            return
        self._fh.write(json.dumps({"ts": time.time(), "msg": msg}) + "\n")
        self._fh.flush()

    async def _handshake(self, ws) -> None:
        await ws.send(json.dumps({"type": "connection_init"}))
        deadline = asyncio.get_event_loop().time() + ACK_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=ACK_TIMEOUT_S))
            t = msg.get("type")
            if t == "connection_ack":
                self.got_ack = True
                log.info("connection_ack ✓  (timeout %sms)", msg.get("connectionTimeoutMs", "?"))
                return
            if t in ("connection_error", "error"):
                raise RuntimeError(f"AppSync rejected the connection: {json.dumps(msg)[:300]}")
        raise TimeoutError(f"no connection_ack within {ACK_TIMEOUT_S}s — check api_host / api_key")

    async def _subscribe_all(self, ws) -> None:
        for ch in self.cfg["channels"]:
            m = _subscribe_message(self.cfg, ch)
            self._sub_ids[m["id"]] = ch
            await ws.send(json.dumps(m))
            log.info("subscribe → %s (id=%s)", ch, m["id"][:8])

    def _on_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "subscribe_success":
            ch = self._sub_ids.get(msg.get("id"), msg.get("id"))
            self.subscribed.add(ch)
            log.info("subscribe_success ✓  %s", ch)
        elif t == "data":
            self.data_frames += 1
            self._record(msg)
        elif t == "ka":
            pass                       # keepalive — normal, ignore
        elif t in ("subscribe_error", "error", "connection_error"):
            log.warning("error frame: %s", json.dumps(msg)[:300])
            self._record(msg)
        else:
            self._record(msg)
        self.total_frames += 1

    async def run_once(self) -> None:
        """One connect → handshake → subscribe → record loop. Raises on disconnect."""
        log.info("connecting to %s ...", self.cfg["realtime_url"])
        async with websockets.connect(self.cfg["realtime_url"],
                                      subprotocols=_subprotocols(self.cfg),
                                      ping_interval=20, close_timeout=5) as ws:
            await self._handshake(ws)
            await self._subscribe_all(ws)
            last_count = asyncio.get_event_loop().time()
            async for raw in ws:
                self._on_message(json.loads(raw))
                now = asyncio.get_event_loop().time()
                if now - last_count >= COUNTER_EVERY_S:
                    log.info("frames: %d data / %d total  (subscribed: %s)",
                             self.data_frames, self.total_frames, sorted(self.subscribed))
                    last_count = now

    def close(self):
        if self._fh:
            self._fh.close()


# ── modes ─────────────────────────────────────────────────────────────────────
async def selftest(cfg: dict) -> int:
    """Prove the recorder can reach connection_ack + subscribe_success (and, if data is flowing,
    see frames). Succeeds on ack+subscribe alone — between sessions there are no telemetry frames,
    and that's fine. This is the check that the empty-capture failure is fixed, runnable any time."""
    rec = TelemetryRecorder(cfg, out_path=None)
    log.info("SELFTEST — handshake + subscribe, observe ~8s")
    try:
        async with websockets.connect(cfg["realtime_url"], subprotocols=_subprotocols(cfg),
                                      ping_interval=20, close_timeout=5) as ws:
            await rec._handshake(ws)
            await rec._subscribe_all(ws)
            deadline = asyncio.get_event_loop().time() + 8
            while asyncio.get_event_loop().time() < deadline:
                try:
                    rec._on_message(json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0)))
                except asyncio.TimeoutError:
                    pass
    except Exception as e:
        log.error("SELFTEST FAILED before ack/subscribe: %s", e)
        return 1
    log.info("SELFTEST result: ack=%s subscribed=%s data_frames=%d",
             rec.got_ack, sorted(rec.subscribed), rec.data_frames)
    if not rec.got_ack or not rec.subscribed:
        log.error("SELFTEST FAILED — ack=%s, subscribed channels=%s",
                  rec.got_ack, sorted(rec.subscribed))
        return 1
    log.info("SELFTEST PASSED ✓  (handshake + subscribe work; the empty-capture bug is fixed)."
             + ("" if rec.data_frames else "  No data frames yet — expected with no session live."))
    return 0


async def capture(cfg: dict, seconds: int | None) -> int:
    """Record forever (or for `seconds`), reconnecting on drop. Writes data/telemetry_<ts>.jsonl.
    Returns non-zero if the whole run never saw a single data frame (so a dud capture is loud)."""
    out = DATA_DIR / f"telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    rec = TelemetryRecorder(cfg, out_path=out)
    log.info("recording → %s   (Ctrl-C to stop)", out)
    start = time.time()
    try:
        while True:
            try:
                if seconds is not None:
                    await asyncio.wait_for(rec.run_once(),
                                           timeout=max(1, seconds - (time.time() - start)))
                else:
                    await rec.run_once()
            except asyncio.TimeoutError:
                log.info("reached --seconds limit, stopping")
                break
            except (ConnectionClosed, OSError) as e:
                log.warning("connection dropped: %s", e)
            except KeyboardInterrupt:
                break
            if seconds is not None and time.time() - start >= seconds:
                break
            log.info("reconnecting in %ds ...", RECONNECT_EVERY_S)
            await asyncio.sleep(RECONNECT_EVERY_S)
    except KeyboardInterrupt:
        pass
    finally:
        rec.close()
    log.info("capture done: %d data frames / %d total → %s",
             rec.data_frames, rec.total_frames, out)
    if rec.data_frames == 0:
        log.error("NO DATA FRAMES captured — file is effectively empty. Was a session live? "
                  "Check the channels in the config.")
        return 1
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Record the IMSA AppSync Events telemetry feed.")
    ap.add_argument("--selftest", action="store_true",
                    help="verify the handshake reaches connection_ack + subscribe_success, then exit")
    ap.add_argument("--seconds", type=int, default=None,
                    help="record for this many seconds then stop (default: until Ctrl-C)")
    ap.add_argument("--config", default=str(CONFIG_PATH),
                    help=f"path to the feed config (default: {CONFIG_PATH})")
    args = ap.parse_args()

    try:
        cfg = load_config(pathlib.Path(args.config))
    except ConfigError as e:
        log.error("%s", e)
        return 2

    if args.selftest:
        return asyncio.run(selftest(cfg))
    return asyncio.run(capture(cfg, args.seconds))


if __name__ == "__main__":
    sys.exit(main())
