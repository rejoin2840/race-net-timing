"""Behavioral contracts for causal validation and race-state reliability."""
import copy
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import calculator
import config
import evaluator
import penalties
import predictor
import replay
import timing71
import validate_races
from db import RaceDB
from event_schedule import load_schedule
from poller import Poller
from runtime import IngestionLock

ROOT = Path(__file__).resolve().parents[1]


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'race.db'
        self.db = RaceDB(self.path)
        self.seed('race')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def seed(self, oid):
        self.db.set_session(oid, {'name': 'Race', 'type': 'RACE', 'eventName': 'Test'})
        self.db.update_status({'currentFlag': 'GF', 'currentLap': 10, 'isSessionRunning': True,
                               'startTime': time.time() - 100, 'finalType': 'BY_TIME', 'finalTime': 3600})
        for car, pos in [('7', 1), ('9', 2)]:
            self.db.upsert_entry(car, {'class': 'GTP', 'name': 'Driver'})
            self.db.ingest_car(car, {'laps': 10, 'overall_position': pos, 'pos_in_class': pos,
                                    'gap_ms': pos * 1000, 'track_status': 'TRACK'},
                               {'class': 'GTP', 'isRunning': True, 'lastLapTime': 100000,
                                'bestLapTime': 100000, 'elapsedTime': 1000000 + pos * 1000}, 10)
        self.db.commit()

    def test_primary_poll_records_once_across_two_displays(self):
        p = Poller(force_oid='race', db_path=self.path)
        q = Poller(force_oid='race', db_path=self.path)
        try:
            self.assertIsNotNone(p.poll())
            self.assertIsNotNone(q.poll())
            self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0], 2)
        finally:
            p.conn.close(); q.conn.close()

    def test_stale_inputs_never_rejuvenated_by_computation(self):
        old = '2000-01-01T00:00:00Z'
        self.db.conn.execute('UPDATE standings_current SET timing_updated_at=?', (old,))
        self.db.commit()
        p = Poller(force_oid='race', db_path=self.path)
        try:
            snap = p.poll()
            self.assertIsNotNone(snap)
            self.assertTrue(all(c.net_position is None and c.fuel_due is None for c in snap[1]))
            self.assertGreater(p.real_age(), 12)
            self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0], 0)
        finally:
            p.conn.close()

    def test_unknown_active_source_is_not_live(self):
        self.db.conn.execute("UPDATE standings_current SET timing_updated_at=NULL WHERE car_number='7'")
        self.db.commit()
        p=Poller(force_oid='race',db_path=self.path)
        try:
            p.poll()
            self.assertIsNone(p.real_age())
            self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],0)
        finally:
            p.conn.close()

    def test_telemetry_update_does_not_refresh_timing(self):
        old = '2000-01-01T00:00:00Z'
        self.db.conn.execute('UPDATE standings_current SET timing_updated_at=?', (old,))
        self.db.ingest_car('7', {'laps': 10}, {'class': 'GTP', 'fuelPct': 4, 'isRunning': True},
                           10, timing_observed=False, telemetry_observed=True)
        self.db.commit()
        row = self.db.conn.execute("SELECT timing_updated_at,telemetry_updated_at FROM standings_current WHERE car_number='7'").fetchone()
        self.assertEqual(row[0], old)
        self.assertIsNotNone(row[1])

    def test_session_change_clears_delay_and_latches(self):
        p = Poller(db_path=self.path)
        try:
            p.poll()
            p.window_locked.add('7'); p.lapped_latch.add('7'); p.hist['old'] = []
            self.seed('new-race')
            result = p.poll(delay_s=120)
            self.assertEqual(result[0].session_oid, 'new-race')
            self.assertNotIn('old', p.hist)
            self.assertNotIn('7', p.window_locked)
            self.assertNotIn('7', p.lapped_latch)
        finally:
            p.conn.close()

    def test_failed_publish_rolls_back_all_tables(self):
        p = Poller(force_oid='race', db_path=self.path)
        try:
            p.poll()
            before = self.db.conn.execute('SELECT updated_at FROM net_analysis LIMIT 1').fetchone()[0]
            with patch.object(p, '_write_rail_battles', side_effect=sqlite3.OperationalError('injected')):
                self.assertIsNotNone(p.poll())
            after = self.db.conn.execute('SELECT updated_at FROM net_analysis LIMIT 1').fetchone()[0]
            self.assertEqual(before, after)
        finally:
            p.conn.close()

    def test_explicit_clock_and_scheduled_distance(self):
        start = 1700000000
        self.db.conn.execute('UPDATE session_status SET start_time_s=?, final_type="BY_LAPS", final_laps=100', (start,))
        self.db.commit()
        ctx, cars = calculator.analyse(self.db.conn, 'race', now_s=start + 90)
        self.assertEqual(ctx.elapsed_s, 90)
        self.assertGreater(cars[0].est_stops_left, 0)
        self.assertEqual(ctx.final_laps, 100)

    def test_final_truth_is_independent_of_last_prediction(self):
        ctx, cars = calculator.analyse(self.db.conn, 'race')
        predictor.log_cycle(self.db.conn, 'race', ctx, cars, 1000)
        self.db.conn.execute('CREATE TABLE final_classification (session_oid TEXT,car_number TEXT,pos_in_class INTEGER)')
        self.db.conn.executemany('INSERT INTO final_classification VALUES (?,?,?)', [('race','7',2),('race','9',1)])
        self.db.commit()
        self.assertEqual(evaluator.eval_net_position(self.db.conn, 'race')['track_mae'], 1)

    def test_incomplete_race_has_no_finish_score(self):
        ctx, cars = calculator.analyse(self.db.conn, 'race')
        predictor.log_cycle(self.db.conn, 'race', ctx, cars, 1000)
        self.assertIsNone(evaluator.eval_net_position(self.db.conn, 'race'))

    def test_kernel_lease_prevents_competing_writers_and_releases(self):
        lease = IngestionLock(self.path, 'imsa')
        try:
            with self.assertRaises(RuntimeError):
                IngestionLock(self.path, 'imsa')
        finally:
            lease.close()
        second = IngestionLock(self.path, 'imsa')
        second.close()


class CausalReplayTests(unittest.TestCase):
    def early(self, future_multiplier):
        archive = timing71.load(str(ROOT / 'sample-archives/Detroit-2026-Race.zip'))
        stops = archive.pit_stops()
        for seq in stops.values():
            for stop in seq:
                if stop.duration_s:
                    stop.duration_s *= future_multiplier
        archive.pit_stops = lambda: stops
        first_entry = min(stop.in_ts for seq in stops.values() for stop in seq if stop.in_ts)
        with tempfile.TemporaryDirectory() as tmp:
            db, col, _, flag, feed = replay._init_db(archive, str(Path(tmp)/'test.db'), 'test')
            try:
                self.assertEqual(db.conn.execute('SELECT COUNT(*) FROM pit_events').fetchone()[0], 0)
                for ts, frame in archive.full_frames:
                    if ts * 1000 >= first_entry:
                        break
                    replay._ingest_frame(db, 'test', ts, frame, col, feed, flag)
                db.commit()
                ctx, cars = calculator.analyse(db.conn, 'test', now_s=ts)
                self.assertTrue(ctx.pit_model.thin)
                return [(c.car_number, c.net_position, c.next_stop_ms, c.est_stops_left) for c in cars]
            finally:
                db.close()

    def test_future_stop_mutation_cannot_change_early_predictions(self):
        self.assertEqual(self.early(1), self.early(10))

    def test_duration_only_appears_after_exit(self):
        archive = SimpleNamespace(pit_stops=lambda: {'7': [timing71.PitStop('7', in_ts=100, out_ts=200, duration_s=30)]})
        with tempfile.TemporaryDirectory() as tmp:
            db=RaceDB(Path(tmp)/'x.db'); db.set_session('test', {})
            try:
                feed=replay._ReplayEvents(archive, lambda *_: 5, lambda *_:'GF')
                feed.advance(db, 100)
                self.assertIsNone(db.conn.execute('SELECT stop_duration_ms FROM pit_events').fetchone()[0])
                feed.advance(db, 199)
                self.assertIsNone(db.conn.execute('SELECT stop_duration_ms FROM pit_events').fetchone()[0])
                feed.advance(db, 200)
                self.assertEqual(db.conn.execute('SELECT stop_duration_ms FROM pit_events').fetchone()[0], 30000)
            finally:
                db.close()

    def test_zero_clock_without_terminal_flag_is_incomplete(self):
        r=timing71.Replay('Test',0,{},[(1,{'session':{'timeRemain':0,'flagState':'green'}})],[])
        self.assertFalse(r.complete)


class PenaltyEventsTests(unittest.TestCase):
    def test_distinct_equal_penalties_accumulate(self):
        msg='CAR 7 - 5 SECONDS PENALTY'
        self.assertEqual(penalties.aggregate([(1700000000000,msg),(1700000001000,msg)])['7'][0],10)

    def test_car_number_padding_does_not_split_carry(self):
        events=[(1700000000000,'CAR 007 - 5 SECONDS PENALTY'),(1700000001000,'CAR 7 - 5 SECONDS PENALTY')]
        result=penalties.aggregate(events)
        self.assertEqual(len(result),1)
        self.assertEqual(next(iter(result.values()))[0],10)

    def test_duplicate_feed_event_deduplicates(self):
        event=(1700000000000,'CAR 7 - 5 SECONDS PENALTY')
        self.assertEqual(penalties.aggregate([event,event])['7'][0],5)

    def test_rescind_reverses_original(self):
        events=[(1700000000000,'CAR 7 - DRIVE THROUGH PENALTY'),(1700000001000,'CAR 7 - DRIVE THROUGH PENALTY RESCINDED')]
        self.assertEqual(penalties.aggregate(events),{})
        self.assertEqual(penalties.aggregate(reversed(events)),{})

    def test_amendment_replaces_terms(self):
        events=[(1700000000000,'CAR 7 - 5 SECONDS PENALTY'),(1700000001000,'CAR 7 - 10 SECONDS PENALTY *AMENDED')]
        self.assertEqual(penalties.aggregate(events)['7'][0],10)

    def test_amendment_can_change_penalty_kind(self):
        events=[(1700000000000,'CAR 7 - DRIVE THROUGH PENALTY'),(1700000001000,'CAR 7 - 10 SECONDS PENALTY *AMENDED')]
        self.assertEqual(penalties.aggregate(events)['7'][0],10)

    def test_unparseable_amendment_retains_carry(self):
        events=[(1700000000000,'CAR 7 - 5 SECONDS PENALTY'),(1700000001000,'CAR 7 - PENALTY *AMENDED')]
        result=penalties.aggregate(events)['7']
        self.assertEqual(result[0],5)
        self.assertIn('unconfirmed',result[2])

    def test_ambiguous_rescind_is_visible(self):
        events=[(1700000000000,'CAR 7 - 5 SECONDS PENALTY'),(1700000001000,'CAR 7 - 5 SECONDS PENALTY'),(1700000002000,'CAR 7 - 5 SECONDS PENALTY RESCINDED')]
        result=penalties.aggregate(events)['7']
        self.assertEqual(result[0],10)
        self.assertIn('unconfirmed',result[2])


class ConfigAndGateTests(unittest.TestCase):
    def test_invalid_edit_keeps_last_good_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'config.json'; p.write_text('{"PACE_WINDOW": 6}')
            with patch.object(config,'CONFIG_PATH',p):
                cfg=config._Config()
                p.write_text('{"PACE_WINDOW": "invalid"}')
                cfg.reload_if_changed()
                self.assertEqual(cfg.PACE_WINDOW,6)
                self.assertIsNotNone(cfg.error)
                p.write_text('{"PACE_WINDOW": 7}')
                cfg.reload_if_changed()
                self.assertEqual(cfg.PACE_WINDOW,7)
                self.assertIsNone(cfg.error)

    def test_invalid_nested_and_nonfinite_values_rejected(self):
        for value in ({'SERIES_OVERRIDES':{'wec':{'PACE_WINDOW':0}}}, {'TRACK_LAT':91},
                      {'VET_DUE_PCT':float('nan')},{'DEFAULT_STINT_LAPS':{'GTP':-1}}):
            with self.assertRaises(ValueError): config.validate(value)

    def test_baseline_regression_and_coverage_fail(self):
        old={'input':'race','sha256':'abc','net':{'proj_mae':2},'catch':{'n':10,'hit_rate':.8}}
        baseline={'races':[old],'tolerances':{'net.proj_mae':.1,'catch.hit_rate':.05}}
        worse=copy.deepcopy(old); worse['net']['proj_mae']=2.5; worse['catch']['n']=2
        self.assertGreaterEqual(len(validate_races.compare_baseline([worse],baseline)),2)

    def test_missing_archive_exits_nonzero(self):
        with patch.object(sys,'argv',['validate_races','/missing/input.zip']), patch('builtins.print'):
            self.assertEqual(validate_races.main(),1)

    def test_processing_failure_exits_nonzero(self):
        with tempfile.NamedTemporaryFile(suffix='.zip') as source:
            with patch.object(sys,'argv',['validate_races',source.name]), patch.object(validate_races,'run_one',side_effect=ValueError('failed processing')), patch('builtins.print'):
                self.assertEqual(validate_races.main(),1)

    def test_nonfinite_metrics_rejected(self):
        with self.assertRaises(ValueError):
            validate_races.validate_numbers({'net':{'proj_mae':float('nan')}})

    def test_supervisor_bounds_failed_child_restarts(self):
        import weekend_conductor as conductor
        start=datetime.now(timezone.utc)
        class Clock(datetime):
            current=start
            @classmethod
            def now(cls, tz=None): return cls.current
        class Child:
            def __init__(self,args,**kwargs): self.args=args; self.returncode=1; self.pid=123
            def poll(self): return self.returncode
        def advance(seconds): Clock.current += timedelta(seconds=seconds)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(conductor,'LOG_DIR',Path(tmp)), patch.object(conductor,'DATA_DIR',Path(tmp)), patch.object(conductor,'datetime',Clock), patch.object(conductor.time,'sleep',side_effect=advance), patch.object(conductor.time,'monotonic',return_value=0), patch.object(conductor.subprocess,'Popen',side_effect=Child) as launch, patch.object(conductor,'_log') as log, patch.object(conductor,'_write_lock'), patch.object(conductor,'_remove_lock'), patch.object(conductor.session_healthcheck,'check',return_value=''):
                conductor.run_session('imsa','Test','practice',start,1)
                self.assertEqual(launch.call_count,4)
                self.assertTrue(any('failed after 3 restarts' in call.args[0] for call in log.call_args_list))
                self.assertEqual(conductor._active_procs,set())

    def test_schedule_rejects_overlap_and_validates_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'schedule.json'
            data={'timezone':'America/Indianapolis','sessions':[
                {'series':'wec','label':'Race','kind':'race','start':'2026-07-12T10:30:00-04:00','duration_minutes':480}]}
            p.write_text(json.dumps(data))
            self.assertIsNotNone(load_schedule(p)[0][3].tzinfo)
            data['sessions'].append({**data['sessions'][0],'label':'Overlap'})
            p.write_text(json.dumps(data))
            with self.assertRaises(ValueError): load_schedule(p)


if __name__ == '__main__':
    unittest.main()
