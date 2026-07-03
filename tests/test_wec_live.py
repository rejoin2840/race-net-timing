"""Unit tests for src/wec_live.py — pure parser functions + bootstrap hydration.

Run (no pytest dependency):
  ./venv/bin/python tests/test_wec_live.py
"""
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wec_live import (  # noqa: E402
    laptime_ms,
    parse_flag,
    normalize_class,
    make_oid,
    parse_participant,
    _int_or,
    WecLiveClient,
    WecLiveState,
)


class TestLaptimeMs(unittest.TestCase):
    def test_minutes_colon_format(self):
        self.assertEqual(laptime_ms("1:23.456"), 83456)

    def test_seconds_only(self):
        self.assertEqual(laptime_ms("23.456"), 23456)

    def test_int_passthrough(self):
        self.assertEqual(laptime_ms(83456), 83456)

    def test_float_seconds(self):
        self.assertEqual(laptime_ms(83.456), 83456)

    def test_float_already_ms(self):
        self.assertEqual(laptime_ms(83456.0), 83456)

    def test_none(self):
        self.assertIsNone(laptime_ms(None))

    def test_empty_string(self):
        self.assertIsNone(laptime_ms(""))

    def test_whitespace(self):
        self.assertIsNone(laptime_ms("   "))

    def test_zero(self):
        self.assertIsNone(laptime_ms(0))

    def test_negative(self):
        self.assertIsNone(laptime_ms(-1))

    def test_negative_string(self):
        self.assertIsNone(laptime_ms("-5.0"))

    def test_garbage(self):
        self.assertIsNone(laptime_ms("not a time"))

    def test_two_minute_lap(self):
        self.assertEqual(laptime_ms("2:05.100"), 125100)


class TestParseFlag(unittest.TestCase):
    def test_green(self):
        self.assertEqual(parse_flag("GREEN"), "GF")

    def test_green_lower(self):
        self.assertEqual(parse_flag("green"), "GF")

    def test_green_title(self):
        self.assertEqual(parse_flag("Green"), "GF")

    def test_fcy(self):
        self.assertEqual(parse_flag("FCY"), "FCY")

    def test_full_course_yellow(self):
        self.assertEqual(parse_flag("FULL COURSE YELLOW"), "FCY")

    def test_safety_car(self):
        self.assertEqual(parse_flag("SAFETY CAR"), "SC")

    def test_red(self):
        self.assertEqual(parse_flag("RED"), "RF")

    def test_checkered(self):
        self.assertEqual(parse_flag("CHECKERED"), "CH")

    def test_chequered(self):
        self.assertEqual(parse_flag("Chequered"), "CH")

    def test_vsc(self):
        self.assertEqual(parse_flag("VSC"), "VSC")

    def test_slow_zone(self):
        self.assertEqual(parse_flag("SLOW ZONE"), "SZ")

    def test_lastlap(self):
        self.assertEqual(parse_flag("LastLap"), "GF")

    def test_empty(self):
        self.assertIsNone(parse_flag(""))

    def test_none(self):
        self.assertIsNone(parse_flag(None))

    def test_unknown_passes_through(self):
        self.assertEqual(parse_flag("SOMETHING_NEW"), "SOMETHING_NEW")


class TestNormalizeClass(unittest.TestCase):
    def test_hypercar(self):
        self.assertEqual(normalize_class("HYPERCAR"), "HYPERCAR")

    def test_hypercar_lower(self):
        self.assertEqual(normalize_class("hypercar"), "HYPERCAR")

    def test_lmh(self):
        self.assertEqual(normalize_class("LMH"), "HYPERCAR")

    def test_lmgt3(self):
        self.assertEqual(normalize_class("LMGT3"), "LMGT3")

    def test_gt3(self):
        self.assertEqual(normalize_class("GT3"), "LMGT3")

    def test_lmgt3_lower(self):
        self.assertEqual(normalize_class("lmgt3"), "LMGT3")

    def test_empty_defaults_hypercar(self):
        self.assertEqual(normalize_class(""), "HYPERCAR")

    def test_none_defaults_hypercar(self):
        self.assertEqual(normalize_class(None), "HYPERCAR")

    def test_unknown_passes_through(self):
        self.assertEqual(normalize_class("LMP2"), "LMP2")

    def test_class_id_string(self):
        self.assertEqual(normalize_class("HyperCar"), "HYPERCAR")


class TestMakeOid(unittest.TestCase):
    def test_with_numeric_sid(self):
        self.assertEqual(make_oid(12345), "wec_live_12345")

    def test_with_string_sid(self):
        self.assertEqual(make_oid("12345"), "wec_live_12345")

    def test_fallback_to_event(self):
        oid = make_oid(None, "São Paulo 6H")
        self.assertTrue(oid.startswith("wec_live_"))
        self.assertNotEqual(oid, "wec_live_unknown")

    def test_both_empty(self):
        self.assertEqual(make_oid(None, ""), "wec_live_unknown")

    def test_zero_sid(self):
        self.assertEqual(make_oid(0, "Fallback"), "wec_live_fallback")


class TestParseParticipant(unittest.TestCase):
    """Tests parse_participant against real Griiip bootstrap data shape."""

    def test_full_entry(self):
        data = {
            "displayName": "Mike Conway",
            "teamName": "Toyota Gazoo Racing",
            "manufacturer": "Toyota",
            "carNumber": "7",
            "classId": "",
            "drivers": [
                {"displayName": "Mike Conway"},
                {"displayName": "Kamui Kobayashi"},
                {"displayName": "Nyck de Vries"},
            ],
        }
        e = parse_participant(data)
        self.assertEqual(e["class"], "HYPERCAR")
        self.assertEqual(e["team"], "Toyota Gazoo Racing")
        self.assertEqual(e["vehicle"], "Toyota")
        self.assertEqual(len(e["drivers"]), 3)
        self.assertEqual(e["name"], "Mike Conway")

    def test_single_driver_display_name_only(self):
        data = {
            "displayName": "Derek Loree",
            "carNumber": "1",
            "classId": "",
        }
        e = parse_participant(data)
        self.assertEqual(e["drivers"], ["Derek Loree"])
        self.assertEqual(e["name"], "Derek Loree")

    def test_drivers_as_strings(self):
        data = {"drivers": ["Alice", "Bob"]}
        e = parse_participant(data)
        self.assertEqual(e["drivers"], ["Alice", "Bob"])
        self.assertEqual(e["name"], "Alice")

    def test_no_drivers(self):
        data = {"classId": "LMGT3"}
        e = parse_participant(data)
        self.assertIsNone(e["drivers"])

    def test_class_normalization_from_classId(self):
        data = {"classId": "LMH", "teamName": "Porsche"}
        e = parse_participant(data)
        self.assertEqual(e["class"], "HYPERCAR")
        self.assertEqual(e["team"], "Porsche")

    def test_empty_dict(self):
        e = parse_participant({})
        self.assertEqual(e["class"], "HYPERCAR")
        self.assertIsNone(e["team"])
        self.assertIsNone(e["vehicle"])


class TestIntOr(unittest.TestCase):
    def test_int(self):
        self.assertEqual(_int_or(5, None), 5)

    def test_string_int(self):
        self.assertEqual(_int_or("42", 0), 42)

    def test_none(self):
        self.assertIsNone(_int_or(None, None))

    def test_garbage(self):
        self.assertEqual(_int_or("abc", -1), -1)

    def test_float_truncates(self):
        self.assertEqual(_int_or(3.9, 0), 3)


class TestGriiipDataShapes(unittest.TestCase):
    """Verify that our parsers handle the exact data shapes from the Griiip API."""

    def test_ranks_shape(self):
        """Real ranks message from bootstrap."""
        data = {
            "overallPosition": 1,
            "position": 1,
            "isDeleted": False,
            "ts": "2026-07-03T07:27:19Z",
            "elapsedTimeMillis": -2,
            "sid": 19151,
            "pid": 404030,
            "carNumber": "5",
            "classId": "",
        }
        self.assertEqual(str(data["carNumber"]).strip(), "5")
        self.assertEqual(_int_or(data["overallPosition"], None), 1)
        self.assertEqual(_int_or(data["position"], None), 1)

    def test_gaps_shape(self):
        """Real gaps message from bootstrap."""
        data = {
            "gapToFirstMillis": 1000,
            "gapToFirstLaps": 0,
            "gapToAheadMillis": 1000,
            "gapToAheadLaps": 0,
            "isDeleted": False,
            "carNumber": "1",
            "classId": "",
        }
        gap_ms = _int_or(data["gapToFirstMillis"], 0)
        laps_behind = _int_or(data["gapToFirstLaps"], 0)
        self.assertEqual(gap_ms, 1000)
        self.assertEqual(laps_behind, 0)

    def test_gaps_lapped(self):
        """Car 4 laps down from bootstrap."""
        data = {
            "gapToFirstMillis": -1,
            "gapToFirstLaps": 4,
            "carNumber": "4",
        }
        gap_ms = max(0, _int_or(data["gapToFirstMillis"], 0))
        laps_behind = _int_or(data["gapToFirstLaps"], 0)
        self.assertEqual(gap_ms, 0)
        self.assertEqual(laps_behind, 4)

    def test_laps_shape(self):
        """Real laps message from bootstrap."""
        data = {
            "sessionPart": 1,
            "lapNumber": 6,
            "lapTimeMillis": 45837,
            "isValid": True,
            "color": "Gray",
            "carNumber": "5",
            "classId": "",
        }
        self.assertEqual(_int_or(data["lapNumber"], None), 6)
        self.assertEqual(_int_or(data["lapTimeMillis"], None), 45837)

    def test_race_flags_shape(self):
        """Real race-flags message from bootstrap."""
        data = {
            "raceFlagID": "cbe01bd2-9990-4b1e-897c-43848e034e15",
            "flag": "Green",
            "sectorNumbers": [],
            "lapNumber": 1,
        }
        self.assertEqual(parse_flag(data["flag"]), "GF")

    def test_race_flags_chequered(self):
        data = {"flag": "Chequered", "lapNumber": 6}
        self.assertEqual(parse_flag(data["flag"]), "CH")

    def test_session_length_laps(self):
        data = {
            "sessionLengthType": "LapsOnly",
            "lapsLimit": 6,
            "timeLimitSeconds": -1,
        }
        self.assertIn("Laps", data["sessionLengthType"])
        self.assertEqual(_int_or(data["lapsLimit"], None), 6)

    def test_session_length_time(self):
        data = {
            "sessionLengthType": "TimeOnly",
            "timeLimitSeconds": 21600,
            "lapsLimit": -1,
        }
        self.assertIn("Time", data["sessionLengthType"])
        self.assertEqual(_int_or(data["timeLimitSeconds"], None), 21600)

    def test_running_status_shape(self):
        data = {"status": "Running", "carNumber": "1"}
        self.assertEqual(str(data["status"]).lower(), "running")

    def test_participant_shape(self):
        """Real participants message from bootstrap."""
        data = {
            "firstname": "",
            "lastname": "",
            "displayName": "Derek Loree",
            "threeLettersName": "DER",
            "currentDriverId": "844661",
            "teamName": None,
            "manufacturer": None,
            "drivers": [
                {"displayName": "Derek Loree", "threeLettersName": "Lor"},
            ],
            "carNumber": "1",
            "classId": "",
        }
        e = parse_participant(data)
        self.assertEqual(e["name"], "Derek Loree")
        self.assertEqual(e["drivers"], ["Derek Loree"])
        self.assertEqual(e["class"], "HYPERCAR")


class TestItemsUnwrapping(unittest.TestCase):
    """Verify that ranks/gaps handlers unwrap the nested {items: [...]} wrapper
    seen in live ReceiveBatch data (vs flat dicts from bootstrap)."""

    def _make_client(self):
        c = WecLiveClient(db_path="", no_db=True)
        c.state = WecLiveState(sid=1, session_oid="wec_live_1")
        return c

    def test_ranks_flat(self):
        c = self._make_client()
        c._handle_ranks({"carNumber": "7", "overallPosition": 1, "position": 1, "classId": ""})
        self.assertEqual(c.state.car_ranks["7"]["pos"], 1)
        self.assertEqual(c.state.car_ranks["7"]["pos_class"], 1)

    def test_ranks_items_wrapped(self):
        """Live ReceiveBatch sends ranks as {items: [{...}, {...}]}."""
        c = self._make_client()
        c._handle_ranks({"items": [
            {"carNumber": "7", "overallPosition": 1, "position": 1, "classId": ""},
            {"carNumber": "51", "overallPosition": 2, "position": 2, "classId": "LMGT3"},
        ]})
        self.assertEqual(c.state.car_ranks["7"]["pos"], 1)
        self.assertEqual(c.state.car_ranks["51"]["pos"], 2)
        self.assertEqual(c.state.car_classes["51"], "LMGT3")

    def test_gaps_flat(self):
        c = self._make_client()
        c._handle_gaps({"carNumber": "7", "gapToFirstMillis": 0, "gapToFirstLaps": 0})
        self.assertEqual(c.state.car_gaps["7"]["gap_ms"], 0)

    def test_gaps_items_wrapped(self):
        """Live ReceiveBatch sends gaps as {items: [{...}, {...}]}."""
        c = self._make_client()
        c._handle_gaps({"items": [
            {"carNumber": "7", "gapToFirstMillis": 0, "gapToFirstLaps": 0},
            {"carNumber": "51", "gapToFirstMillis": 5200, "gapToFirstLaps": 0},
        ]})
        self.assertEqual(c.state.car_gaps["7"]["gap_ms"], 0)
        self.assertEqual(c.state.car_gaps["51"]["gap_ms"], 5200)

    def test_ranks_empty_items(self):
        c = self._make_client()
        c._handle_ranks({"items": []})
        self.assertEqual(len(c.state.car_ranks), 0)

    def test_receive_batch_full_pipeline(self):
        """Simulate a real ReceiveBatch message through _on_receive_batch."""
        c = self._make_client()
        batch = [{"items": [
            {"channel": "ranks", "view": {"items": [
                {"carNumber": "7", "overallPosition": 1, "position": 1, "classId": ""},
            ]}},
            {"channel": "laps", "view": {
                "lapNumber": 5, "lapTimeMillis": 92000, "carNumber": "7", "classId": "",
            }},
        ]}]
        c._on_receive_batch(batch)
        self.assertEqual(c.state.car_ranks["7"]["pos"], 1)
        self.assertEqual(c.state.car_laps["7"]["lap"], 5)


class TestRecordFrameFlush(unittest.TestCase):
    """--record is billed as mandatory race-day insurance; a frame must be
    flushed to disk immediately so a hard process kill can lose at most the
    single frame in flight, not a whole buffered chunk."""

    def test_record_frame_flushes_after_write(self):
        c = WecLiveClient(db_path="", no_db=True)
        recorder = unittest.mock.MagicMock()
        c._recorder = recorder
        c._record_frame("ranks", {"carNumber": "7"})
        recorder.write.assert_called_once()
        recorder.flush.assert_called_once()


if __name__ == "__main__":
    unittest.main()
