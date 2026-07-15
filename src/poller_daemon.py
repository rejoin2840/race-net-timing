"""
poller_daemon.py — populates net_analysis without any GUI.

The Electron web UI reads its net math (net position/gap, stops left,
penalty carry) from the net_analysis table, which the Poller writes as a
side effect of every poll() — but until now only the PyQt6 dashboards ever
instantiated a Poller. This is the same loop lifted out, Qt-free: poll the
latest session (which analyses + writes net_analysis), sleep, repeat.
Exits cleanly on SIGTERM or Ctrl-C.

Run:  venv/bin/python src/poller_daemon.py
      venv/bin/python src/poller_daemon.py --series wec
      venv/bin/python src/poller_daemon.py --oid <session_oid>   # pin a replay
"""

import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from poller import Poller, REFRESH_MS

_stop = False


def _handle_signal(signum, frame):
    global _stop
    _stop = True


def run(series, force_oid, poll_s: float):
    p = Poller(force_oid=force_oid, series=series)
    scope = f"oid={force_oid!r}" if force_oid else f"series={series!r}"
    print(f"  poller daemon writing net_analysis ({scope}, every {poll_s:.0f}s)")
    last_oid = None
    while not _stop:
        snap = p.poll()
        if p.last_oid != last_oid:
            print(f"  session: {p.last_oid}")
            last_oid = p.last_oid
        if snap is not None:
            age = p.real_age()
            print(f"  [{time.strftime('%H:%M:%S')}] wrote {len(p.last_cars or [])} cars"
                  f"  (data age {age:.0f}s)" if age is not None else
                  f"  [{time.strftime('%H:%M:%S')}] wrote {len(p.last_cars or [])} cars")
        # sleep in small slices so SIGTERM lands promptly
        deadline = time.time() + poll_s
        while not _stop and time.time() < deadline:
            time.sleep(0.2)
    print("  poller daemon stopped")


def main():
    ap = argparse.ArgumentParser(description="Headless net_analysis writer for the web UI")
    ap.add_argument("--series", default=None, help="scope latest-session lookup (imsa/wec)")
    ap.add_argument("--oid", default=None, help="pin to one session oid (replay)")
    ap.add_argument("--interval", type=float, default=REFRESH_MS / 1000,
                    help="poll interval seconds (default: dashboard cadence)")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run(args.series, args.oid, args.interval)


if __name__ == "__main__":
    main()
