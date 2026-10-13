"""WEC energy authority, source-age fallback and live/replay decision parity."""
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import calculator
from db import RaceDB
from poller import Poller
from wec_live import WecLiveClient, CH_VET


def stamp(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class EnergyTests(unittest.TestCase):
    def test_energy_range_age_and_threshold(self):
        for value in (None, -1, 101, float('nan'), float('inf'), '5'):
            self.assertIsNone(calculator._fresh_energy(value, stamp(100), 100))
        for when in (None, 'invalid', stamp(101), stamp(87.999)):
            self.assertIsNone(calculator._fresh_energy(50, when, 100))
        self.assertEqual(calculator._fresh_energy(50, stamp(88), 100), 50)
        for pct, expected in ((0, 'due'), (5, 'due'), (5.001, None), (50, None), (100, None)):
            car=SimpleNamespace(fuel_pct=pct, stint_fuel_due=True, est_stops_left=1)
            self.assertEqual(calculator._fuel_due(car, 'wec'), expected)
            self.assertEqual(calculator._fuel_due(car, 'imsa'), 'due')
        car.fuel_pct=None
        self.assertEqual(calculator._fuel_due(car, 'wec'), 'due')
        car.stint_fuel_due=False
        self.assertIsNone(calculator._fuel_due(car, 'wec'))
        car.fuel_pct=0;car.est_stops_left=0
        self.assertIsNone(calculator._fuel_due(car, 'wec'))

    def seed(self, path, now):
        db=RaceDB(path)
        db.set_session('race', {'name':'Race','type':'RACE'}, series='wec')
        db.update_status({'currentFlag':'GF','currentLap':50,'isSessionRunning':True,
                          'startTime':now-3600,'finalType':'BY_TIME','finalTime':21600})
        db.upsert_entry('7', {'class':'HYPERCAR','name':'Driver'})
        db.ingest_car('7', {'laps':50,'overall_position':1,'pos_in_class':1,'track_status':'TRACK'},
                      {'class':'HYPERCAR','isRunning':True,'lastLapTime':90000,'bestLapTime':90000,
                       'fuelPct':50}, 50, telemetry_observed=True, telemetry_observed_at=stamp(now))
        db.conn.execute("UPDATE standings_current SET last_pit_lap=10,pits=1")
        db.commit()
        return db

    def test_clock_source_age_and_non_due_predictions_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=self.seed(Path(tmp)/'race.db', 1700000000)
            try:
                _,fresh=calculator.analyse(db.conn,'race',now_s=1700000000)
                self.assertTrue(fresh[0].stint_fuel_due)
                self.assertIsNone(fresh[0].fuel_due)
                db.conn.execute('CREATE TABLE session_clock (session_oid TEXT PRIMARY KEY,now_s REAL,mode TEXT)')
                db.conn.execute("INSERT INTO session_clock VALUES ('race',1700000000,'replay')")
                _,recorded=calculator.analyse(db.conn,'race')
                self.assertEqual(recorded[0].fuel_pct,50)
                db.conn.execute('UPDATE standings_current SET telemetry_updated_at=?',(stamp(1699999980),))
                _,stale=calculator.analyse(db.conn,'race')
                self.assertIsNone(stale[0].fuel_pct)
                self.assertEqual(stale[0].fuel_due,'due')
                for field in ('net_position','net_gap_ms','projected_finish','est_stops_left','next_stop_ms',
                              'pit_window_open','fuel_laps_left'):
                    self.assertEqual(getattr(fresh[0],field),getattr(stale[0],field))
                # Explicit analysis time overrides the stored replay clock.
                _,later=calculator.analyse(db.conn,'race',now_s=1700000100)
                self.assertIsNone(later[0].fuel_pct)
            finally:db.close()

    def test_poller_keeps_stint_fallback_when_energy_stalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'race.db';db=self.seed(path,time.time())
            db.conn.execute('UPDATE standings_current SET telemetry_updated_at=?',(stamp(time.time()-20),))
            db.commit()
            poller=Poller(force_oid='race',db_path=path)
            try:
                snap=poller.poll()
                self.assertIsNotNone(snap)
                self.assertEqual(snap[1][0].fuel_due,'due')
                self.assertIsNone(snap[1][0].fuel_pct)
                self.assertIsNotNone(snap[1][0].net_position)
            finally:
                if poller.conn:poller.conn.close()
                db.close()

    def test_recorded_vet_stamp_is_not_refreshed_by_timing_or_invalid_energy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=self.seed(Path(tmp)/'race.db',1700000000)
            client=WecLiveClient(db_path='',no_db=True);client.db=db
            client.state.session_oid='race';client.state.pid_to_car={1:'7'}
            client.state.car_classes={'7':'HYPERCAR'}
            try:
                client._dispatch_channel(CH_VET, {'items':[{'pid':1,'e':50}]},ts_ms=1700000000000)
                row=lambda:db.conn.execute('SELECT telemetry_updated_at FROM standings_current').fetchone()[0]
                self.assertEqual(row(),stamp(1700000000))
                client._dispatch_channel(CH_VET, {'items':[{'pid':1,'e':101}]},ts_ms=1700000100000)
                client._frame_ts_ms=1700000200000
                client._flush_car('7',timing_observed=True)
                self.assertEqual(row(),stamp(1700000000))
            finally:db.close()


if __name__=='__main__':unittest.main()
