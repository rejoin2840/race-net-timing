"""
Tests for f1_live.py — parsers, delta-merge, pit detection, field mapping.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from f1_live import (
    _deep_merge,
    _laptime_to_ms,
    _parse_gap,
    _f1_sectors_to_ak,
    _get_nested,
    _make_oid,
    _session_type,
    _STATUS_MAP,
    F1LiveState,
    F1LiveClient,
)


class TestDeepMerge(unittest.TestCase):
    def test_flat(self):
        t = {"a": 1, "b": 2}
        _deep_merge(t, {"b": 3, "c": 4})
        self.assertEqual(t, {"a": 1, "b": 3, "c": 4})

    def test_nested(self):
        t = {"Lines": {"1": {"Position": 1, "Gap": "+0.5"}}}
        _deep_merge(t, {"Lines": {"1": {"Gap": "+1.2"}}})
        self.assertEqual(t["Lines"]["1"]["Position"], 1)
        self.assertEqual(t["Lines"]["1"]["Gap"], "+1.2")

    def test_new_driver(self):
        t = {"Lines": {"1": {"Position": 1}}}
        _deep_merge(t, {"Lines": {"44": {"Position": 2}}})
        self.assertIn("1", t["Lines"])
        self.assertIn("44", t["Lines"])

    def test_deep_nested(self):
        t = {"Lines": {"1": {"Sectors": {"0": {"Value": "24.5"}}}}}
        _deep_merge(t, {"Lines": {"1": {"Sectors": {"0": {"Value": "24.1"}}}}})
        self.assertEqual(t["Lines"]["1"]["Sectors"]["0"]["Value"], "24.1")

    def test_non_dict_update(self):
        t = {"a": 1}
        result = _deep_merge(t, "not a dict")
        self.assertEqual(result, {"a": 1})

    def test_overwrite_dict_with_scalar(self):
        t = {"a": {"b": 1}}
        _deep_merge(t, {"a": 5})
        self.assertEqual(t["a"], 5)


class TestLaptimeToMs(unittest.TestCase):
    def test_minutes_seconds(self):
        self.assertEqual(_laptime_to_ms("1:23.456"), 83456)

    def test_seconds_only(self):
        self.assertEqual(_laptime_to_ms("23.456"), 23456)

    def test_zero_minutes(self):
        self.assertEqual(_laptime_to_ms("0:58.123"), 58123)

    def test_none(self):
        self.assertIsNone(_laptime_to_ms(None))

    def test_empty(self):
        self.assertIsNone(_laptime_to_ms(""))

    def test_numeric(self):
        self.assertEqual(_laptime_to_ms(83.456), 83456)

    def test_zero(self):
        self.assertIsNone(_laptime_to_ms(0))

    def test_whitespace(self):
        self.assertEqual(_laptime_to_ms("  1:23.456  "), 83456)

    def test_invalid(self):
        self.assertIsNone(_laptime_to_ms("DNF"))


class TestParseGap(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(_parse_gap("+5.432"), (5432, 0))

    def test_no_plus(self):
        self.assertEqual(_parse_gap("5.432"), (5432, 0))

    def test_one_lap(self):
        gap_ms, laps = _parse_gap("+1 LAP")
        self.assertEqual(laps, 1)
        self.assertGreater(gap_ms, 0)

    def test_two_laps(self):
        gap_ms, laps = _parse_gap("+2 LAPS")
        self.assertEqual(laps, 2)

    def test_laps_no_plus(self):
        gap_ms, laps = _parse_gap("1 LAP")
        self.assertEqual(laps, 1)

    def test_leader_empty(self):
        self.assertEqual(_parse_gap(""), (0, 0))

    def test_leader_none(self):
        self.assertEqual(_parse_gap(None), (0, 0))

    def test_custom_avg(self):
        gap_ms, laps = _parse_gap("+1 LAP", avg_lap_ms=80_000)
        self.assertEqual(gap_ms, 80_000)
        self.assertEqual(laps, 1)

    def test_invalid_string(self):
        self.assertEqual(_parse_gap("DNF"), (0, 0))


class TestF1SectorsToAk(unittest.TestCase):
    def test_normal(self):
        sectors = {
            "0": {"Value": "24.547"},
            "1": {"Value": "38.776"},
            "2": {"Value": "40.808"},
        }
        result = _f1_sectors_to_ak(sectors)
        self.assertEqual(result, "1;24547;0;0;0;0;2;38776;0;0;0;0;3;40808;0;0;0;0")

    def test_missing_sector(self):
        sectors = {
            "0": {"Value": "24.547"},
            "1": {"Value": "38.776"},
        }
        self.assertIsNone(_f1_sectors_to_ak(sectors))

    def test_empty_value(self):
        sectors = {
            "0": {"Value": ""},
            "1": {"Value": "38.776"},
            "2": {"Value": "40.808"},
        }
        self.assertIsNone(_f1_sectors_to_ak(sectors))

    def test_not_dict(self):
        self.assertIsNone(_f1_sectors_to_ak(None))
        self.assertIsNone(_f1_sectors_to_ak("not a dict"))

    def test_minute_sector(self):
        sectors = {
            "0": {"Value": "1:04.547"},
            "1": {"Value": "38.776"},
            "2": {"Value": "40.808"},
        }
        result = _f1_sectors_to_ak(sectors)
        self.assertTrue(result.startswith("1;64547;"))


class TestGetNested(unittest.TestCase):
    def test_deep(self):
        d = {"a": {"b": {"c": 42}}}
        self.assertEqual(_get_nested(d, "a", "b", "c"), 42)

    def test_missing(self):
        d = {"a": {"b": 1}}
        self.assertIsNone(_get_nested(d, "a", "c"))

    def test_none_input(self):
        self.assertIsNone(_get_nested(None, "a"))


class TestMakeOid(unittest.TestCase):
    def test_basic(self):
        info = {
            "Meeting": {"Name": "Austrian Grand Prix"},
            "Key": "9999",
        }
        oid = _make_oid(info)
        self.assertTrue(oid.startswith("f1_live_"))
        self.assertIn("austrian", oid)
        self.assertIn("9999", oid)

    def test_empty(self):
        oid = _make_oid({})
        self.assertTrue(oid.startswith("f1_live_"))


class TestSessionType(unittest.TestCase):
    def test_race(self):
        self.assertEqual(_session_type({"Name": "Race"}), "RACE")

    def test_sprint(self):
        self.assertEqual(_session_type({"Name": "Sprint"}), "RACE")

    def test_qualifying(self):
        self.assertEqual(
            _session_type({"Name": "Qualifying"}), "QUALIFYING_BEST_LAP")

    def test_practice(self):
        self.assertEqual(_session_type({"Name": "Practice 1"}), "PRACTICE 1")


class TestStatusMap(unittest.TestCase):
    def test_all_codes(self):
        self.assertEqual(_STATUS_MAP["1"], "GF")
        self.assertEqual(_STATUS_MAP["2"], "YF")
        self.assertEqual(_STATUS_MAP["4"], "SC")
        self.assertEqual(_STATUS_MAP["5"], "RF")
        self.assertEqual(_STATUS_MAP["6"], "VSC")
        self.assertEqual(_STATUS_MAP["7"], "GF")


class TestPitDetection(unittest.TestCase):
    def test_pit_count_increment(self):
        state = F1LiveState()
        state.pit_counts["1"] = 1
        client = F1LiveClient(db=None)
        client.state = state
        client._detect_pit("1", {"NumberOfPitStops": 2})
        self.assertEqual(state.pit_counts["1"], 2)

    def test_pit_count_first_seen(self):
        state = F1LiveState()
        client = F1LiveClient(db=None)
        client.state = state
        client._detect_pit("1", {"NumberOfPitStops": 0})
        self.assertEqual(state.pit_counts["1"], 0)

    def test_in_pit_transition(self):
        state = F1LiveState()
        client = F1LiveClient(db=None)
        client.state = state
        client._detect_pit("1", {"InPit": True})
        self.assertTrue(state.in_pit["1"])
        self.assertIn("1", state.pit_entry_time)
        client._detect_pit("1", {"InPit": False})
        self.assertFalse(state.in_pit["1"])

    def test_no_false_pit_on_same_count(self):
        state = F1LiveState()
        state.pit_counts["1"] = 1
        client = F1LiveClient(db=None)
        client.state = state
        client._detect_pit("1", {"NumberOfPitStops": 1})
        self.assertEqual(state.pit_counts["1"], 1)


class TestFieldMapping(unittest.TestCase):
    """Test that _persist_standings builds correct d + standing dicts."""

    def test_full_field_mapping(self):
        mock_db = MagicMock()
        mock_db._pit_count = {}
        mock_db._last_pit_lap = {}
        mock_db._last_pit_hour = {}
        mock_db._car_lap = {}

        client = F1LiveClient(db=mock_db)
        client.state.session_oid = "test"
        client.state.current_lap = 5
        client.state.current_flag = "GF"
        client.state.timing_data = {
            "Lines": {
                "1": {
                    "Position": 1,
                    "NumberOfLaps": 5,
                    "GapToLeader": "",
                    "LastLapTime": {"Value": "1:23.456"},
                    "BestLapTime": {"Value": "1:22.000", "Lap": 3},
                    "Sectors": {
                        "0": {"Value": "24.547"},
                        "1": {"Value": "38.776"},
                        "2": {"Value": "40.808"},
                    },
                    "InPit": False,
                },
            }
        }
        client.state.timing_app = {
            "Lines": {
                "1": {
                    "Stints": {
                        "0": {"Compound": "SOFT", "TotalLaps": 5},
                    }
                }
            }
        }

        client._persist_standings(["1"])

        mock_db.ingest_car.assert_called_once()
        call_args = mock_db.ingest_car.call_args
        car, d, standing, lap, flag = call_args[0]

        self.assertEqual(car, "1")
        self.assertEqual(d["overall_position"], 1)
        self.assertEqual(d["laps"], 5)
        self.assertEqual(d["track_status"], "TRACK")
        self.assertEqual(standing["class"], "F1")
        self.assertEqual(standing["lastLapTime"], 83456)
        self.assertEqual(standing["bestLapTime"], 82000)
        self.assertEqual(standing["bestLapNumber"], 3)
        self.assertIn("1;24547;", standing["lastSectors"])
        self.assertEqual(standing["tireCompound"], "SOFT")
        self.assertEqual(standing["tireAge"], 5)
        self.assertTrue(standing["isRunning"])
        self.assertIsNone(standing["fuelPct"])


class TestReconnectTracking(unittest.TestCase):
    def _client_with_mock_conn(self):
        client = F1LiveClient(db=None)
        client._connection = MagicMock()
        return client

    def test_reconnect_counter(self):
        client = self._client_with_mock_conn()
        self.assertEqual(client._reconnect_count, 0)
        client._on_reconnect()
        self.assertEqual(client._reconnect_count, 1)
        client._on_reconnect()
        self.assertEqual(client._reconnect_count, 2)

    def test_last_message_time_updated_on_feed(self):
        client = F1LiveClient(db=None)
        self.assertEqual(client._last_message_time, 0.0)
        client._on_feed(["TimingData", {"Lines": {}}])
        self.assertGreater(client._last_message_time, 0)

    def test_stopping_flag(self):
        client = F1LiveClient(db=None)
        self.assertFalse(client._stopping)
        client.stop()
        self.assertTrue(client._stopping)

    def test_connect_sets_message_time(self):
        client = self._client_with_mock_conn()
        client._on_connect()
        self.assertGreater(client._last_message_time, 0)
        self.assertTrue(client._is_connected)


class TestSubscribeResponseLogging(unittest.TestCase):
    def test_state_recovery_count(self):
        client = F1LiveClient(db=None)
        client.state.timing_data = {
            "Lines": {"1": {"Position": 1}, "44": {"Position": 2}}
        }
        n = len(client.state.timing_data.get("Lines", {}))
        self.assertEqual(n, 2)


if __name__ == "__main__":
    unittest.main()
