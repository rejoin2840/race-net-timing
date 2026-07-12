"""Unit tests for dashboard_calm.feed_status — the header's feed-liveness tag.

Regression for the "half-alive dashboard" bug: the session clock is derived from
wall time, so when the writer behind race.db dies (a replay stream killed
mid-run, or a live-feed outage) the clock keeps counting down while the timing
table silently freezes. feed_status is the pure helper that decides when the
header must say the data has stalled.

Run (no pytest dependency — matches the other tests in this dir):
  QT_QPA_PLATFORM=offscreen ./venv/bin/python tests/test_feed_status.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dashboard_calm import feed_status  # noqa: E402
from dashboard import STALE_AFTER_S     # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    if got != want:
        print(f"FAIL {name}: got {got!r}, want {want!r}")
        fails += 1
    else:
        print(f"ok   {name}")


# healthy feed → no tag
check("fresh data", feed_status(2.0, False), None)
check("exactly at threshold", feed_status(float(STALE_AFTER_S), False), None)

# no data yet (DB empty / session starting) → no tag, header says its own thing
check("no data yet", feed_status(None, False), None)

# a finished session is legitimately static — never flag it
check("finished session", feed_status(9999.0, True), None)

# stalled feed → tag, seconds first, minutes once it's been dead a while
check("just stalled", feed_status(STALE_AFTER_S + 1.0, False),
      f"DATA STALLED · {STALE_AFTER_S + 1}s")
check("stalled 45s", feed_status(45.0, False), "DATA STALLED · 45s")
check("stalled 5 min", feed_status(300.0, False), "DATA STALLED · 5m")

print()
if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all feed_status tests passed")
