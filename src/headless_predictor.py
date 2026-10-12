"""
headless_predictor.py — logs prediction accuracy snapshots without any GUI.

dashboard.py's poll loop already does this (predictor.log_cycle every
PREDICT_EVERY_S, race-only) as a side effect of the QTimer that redraws the
table. For unattended runs there's no dashboard open, so this is the same
logic lifted out: find the latest session for a series, analyse it, log one
row per car, sleep, repeat. Exits cleanly on SIGTERM (used by the conductor)
or Ctrl-C.

Run:  venv/bin/python src/headless_predictor.py --series imsa
      venv/bin/python src/headless_predictor.py --series wec --db data/race.db
"""

import argparse
import signal
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calculator
import predictor
from db import DEFAULT_DB_PATH

_stop = False


def _handle_signal(signum, frame):
    global _stop
    _stop = True


def run(db_path: str, series: str, poll_s: float = 5.0):
    from poller import Poller
    poller = Poller(series=series, db_path=Path(db_path))
    try:
        while not _stop:
            poller.poll()
            deadline = time.monotonic() + poll_s
            while not _stop and time.monotonic() < deadline:
                time.sleep(0.1)
    finally:
        if poller.conn:
            poller.conn.close()



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", required=True, choices=["imsa", "wec"])
    ap.add_argument("--db", default=str(DEFAULT_DB_PATH))
    ap.add_argument("--poll", type=float, default=5.0,
                    help="how often to check for new data (s); actual logging "
                         "is still throttled to predictor.PREDICT_EVERY_S")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run(args.db, args.series, poll_s=args.poll)


if __name__ == "__main__":
    main()
