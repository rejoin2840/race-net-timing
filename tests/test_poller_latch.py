"""Unit tests for Poller._update_tracking lapped-display latch.

The latch suppresses +1L ↔ on-lead-lap flicker at S/F crossings.
Three properties under test:
  A) frame-scale chatter (0↔1 every 2s) never flips the display back to 0
  B) sustained on-lead-lap for ≥LAPPED_RELEASE_S releases the latch
  C) a genuine pit stop (stop-count increment with prior box_since) clears
     the latch immediately regardless of how long laps_down was held

Run (no pytest needed):
  ./venv/bin/python tests/test_poller_latch.py
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import poller as poller_mod
from poller import LAPPED_RELEASE_S


def _car(car_num, laps_down, stops=0, track_status="TRACK", net_position=5,
         pit_window_open=False):
    c = types.SimpleNamespace()
    c.car_number     = car_num
    c.laps_down      = laps_down
    c.stops          = stops
    c.track_status   = track_status
    c.net_position   = net_position
    c.pit_window_open = pit_window_open
    return c


class TestLappedLatch(unittest.TestCase):
    def setUp(self):
        self.p = poller_mod.Poller()

    def _tick(self, cars, now):
        """Run _update_tracking and return list of laps_down values."""
        self.p._update_tracking(cars, now)
        return [c.laps_down for c in cars]

    # ── A: chatter suppression ────────────────────────────────────────────

    def test_chatter_holds_at_one(self):
        """Alternating 1→0→1→0 every 2s must always display +1L."""
        now = 1000.0
        for i in range(20):
            ld = (i + 1) % 2       # starts 1, then 0, 1, 0 …
            c = _car("7", laps_down=ld)
            result = self._tick([c], now)
            self.assertEqual(result[0], 1,
                             f"iteration {i} (laps_down={ld}): expected 1, got {result[0]}")
            now += 2.0

    def test_latch_not_set_before_first_one(self):
        """A car that never reads +1L must not be held."""
        now = 1000.0
        for _ in range(10):
            c = _car("99", laps_down=0)
            self._tick([c], now)
            self.assertEqual(c.laps_down, 0)
            now += 2.0

    # ── B: sustained release ─────────────────────────────────────────────

    def test_sustained_releases_near_threshold(self):
        """After continuous laps_down=0 for ≥LAPPED_RELEASE_S, latch must clear.

        The release timer starts on the FIRST laps_down=0 tick after arming.
        One tick just below threshold → still held.
        A second tick ≥LAPPED_RELEASE_S after the first zero-tick → released.
        """
        now = 2000.0
        self._tick([_car("7", laps_down=1)], now)
        self.assertIn("7", self.p.lapped_latch)

        # first zero-tick starts the timer at t=now_first_zero
        now_first_zero = now + 5.0
        c = _car("7", laps_down=0)
        self._tick([c], now_first_zero)
        self.assertEqual(c.laps_down, 1, "should still be held: timer just started")

        # just below: still held
        c2 = _car("7", laps_down=0)
        self._tick([c2], now_first_zero + LAPPED_RELEASE_S - 2)
        self.assertEqual(c2.laps_down, 1, "should still be held just below threshold")

        # at/above threshold from first zero-tick: released
        c3 = _car("7", laps_down=0)
        self._tick([c3], now_first_zero + LAPPED_RELEASE_S + 1)
        self.assertEqual(c3.laps_down, 0, "should release at/after threshold")
        self.assertNotIn("7", self.p.lapped_latch)

    def test_any_one_reading_rearms(self):
        """A single +1L reading in the middle of a sustained-0 run restarts the timer."""
        now = 3000.0
        self._tick([_car("7", laps_down=1)], now)

        # advance almost to release
        halfway = now + LAPPED_RELEASE_S - 5
        self._tick([_car("7", laps_down=0)], halfway)

        # one +1L reading re-arms
        self._tick([_car("7", laps_down=1)], halfway + 2)
        self.p.lap0_since.pop("7", None)   # clear timer (re-arm resets it)

        # the previous near-threshold elapsed time must not count — latch holds
        c = _car("7", laps_down=0)
        self._tick([c], halfway + 4)
        self.assertEqual(c.laps_down, 1, "re-arm must restart the hold timer")

    # ── C: pit stop release ───────────────────────────────────────────────

    def test_pit_clears_latch_immediately(self):
        """A genuine pit stop (stops count up + prior box_since) clears the latch."""
        now = 4000.0
        self._tick([_car("9", laps_down=1, stops=1)], now)
        self.assertIn("9", self.p.lapped_latch)

        # car enters box
        self._tick([_car("9", laps_down=1, stops=1, track_status="BOX")], now + 2)

        # stop count increments → latch cleared
        c = _car("9", laps_down=0, stops=2, track_status="BOX")
        self._tick([c], now + 40)
        self.assertEqual(c.laps_down, 0, "pit stop must clear the latch immediately")
        self.assertNotIn("9", self.p.lapped_latch)

    def test_no_box_since_does_not_clear(self):
        """A stop-count increment without prior box_since (feed phantom) must not clear."""
        now = 5000.0
        self._tick([_car("11", laps_down=1, stops=1)], now)
        self.assertIn("11", self.p.lapped_latch)
        # stops goes up but car was never seen in box → phantom increment
        c = _car("11", laps_down=0, stops=2, track_status="TRACK")
        self._tick([c], now + 4)
        self.assertEqual(c.laps_down, 1, "phantom stop-count must not clear the latch")

    # ── D: 2+ laps down ──────────────────────────────────────────────────

    def test_two_laps_down_clears_latch(self):
        """A car going 2+ laps down is its own display bucket — latch must not hold it."""
        now = 6000.0
        self._tick([_car("55", laps_down=1, stops=1)], now)
        self.assertIn("55", self.p.lapped_latch)
        # car goes 2 down
        c = _car("55", laps_down=2, stops=1)
        self._tick([c], now + 60)
        self.assertEqual(c.laps_down, 2)
        self.assertNotIn("55", self.p.lapped_latch)


if __name__ == "__main__":
    unittest.main()
