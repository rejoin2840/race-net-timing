"""DUE audit contracts: episode boundaries, causality and passive observers."""
import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("due_study", ROOT / "tools/studies/due_timing_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)
import replay
import timing71


class MatchingTests(unittest.TestCase):
    def observer(self):
        o = study.Observer()
        o.series = "wec"
        o.classes['7'] = 'HYPERCAR'
        return o

    def test_first_stop_missed_and_prior_episode_cannot_cross_exit(self):
        o = self.observer()
        o.samples['7'] = [(1,1,False,False,'green'), (8,8,False,True,'green'),
                          (12,10,False,False,'green'), (18,18,False,False,'green')]
        o.pits['7'] = [{'entry_ms':10,'exit_ms':11,'pit_lap':10,'flag':'GF'},
                       {'entry_ms':20,'exit_ms':21,'pit_lap':20,'flag':'SC'}]
        rows, eps = study.score(o)
        current = [r for r in rows if r['policy']=='current']
        legacy = [r for r in rows if r['policy'].startswith('legacy')]
        self.assertTrue(all(r['missed'] for r in current))
        self.assertEqual(legacy[0]['lead_laps'],2)
        self.assertTrue(legacy[1]['missed'])
        groups = study.summarize(rows, eps)
        self.assertTrue(any('flag_bucket=caution' in r['group'] for r in groups))

    def test_matches_active_episode_not_withdrawn_outlap_blip(self):
        o = self.observer()
        o.samples['7'] = [(1,1,True,True,'green'),(2,2,False,False,'green'),
                          (8,8,True,True,'green'),(9,9,True,True,'green'),
                          (15,15,True,True,'green')]
        o.pits['7'] = [{'entry_ms':10,'exit_ms':11,'pit_lap':10,'flag':'GF'}]
        rows, eps = study.score(o)
        self.assertEqual(rows[0]['lead_laps'],2)
        self.assertEqual(rows[0]['warning_ms'],8)
        self.assertTrue(any(e['outcome']=='withdrawn_before_entry' for e in eps))
        self.assertTrue(any(e['next_entry_ms'] is None for e in eps))

    def test_same_lap_sample_after_entry_is_never_used(self):
        o = self.observer()
        o.samples['7'] = [(1,5,False,False,'green'),(11,5,True,True,'green')]
        o.pits['7'] = [{'entry_ms':10,'exit_ms':12,'pit_lap':5,'flag':'GF'}]
        self.assertTrue(study.score(o)[0][0]['missed'])

    def test_post_finish_visits_are_not_scored_as_race_warnings(self):
        o=self.observer()
        o.samples['7']=[(1,1,True,True,'green')]
        o.decision_end_ms=5
        o.pits['7']=[{'entry_ms':10,'exit_ms':12,'pit_lap':2,'flag':'GF'}]
        row=study.score(o)[0][0]
        self.assertFalse(row['eligible'])
        self.assertEqual(row['exclusion'],'after calculator finish')

    def test_future_pit_truth_cannot_change_recorded_decisions(self):
        o = self.observer()
        ctx=SimpleNamespace(is_race=True,is_finished=False,profile=SimpleNamespace(key='wec'),flag='GF')
        car=SimpleNamespace(car_number='7',car_class='HYPERCAR',laps=5,fuel_due='due',
                            pit_window_open=True,fuel_laps_left=1,fuel_pct=3,est_stops_left=1)
        o.sample(None,'race',1,ctx,[car])
        recorded=copy.deepcopy(o.samples)
        o.pits['7']=[{'entry_ms':999,'exit_ms':1000,'pit_lap':10,'flag':'GF'}]
        study.score(o)
        self.assertEqual(o.samples,recorded)

    def test_legacy_retains_identical_vet_gate(self):
        car=SimpleNamespace(pit_window_open=False,fuel_laps_left=None,fuel_pct=2,est_stops_left=1)
        self.assertTrue(study.legacy_due(car,'wec'))
        self.assertFalse(study.legacy_due(car,'imsa'))
        car.est_stops_left=0
        self.assertFalse(study.legacy_due(car,'wec'))

    def test_missing_and_failed_required_inputs_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'missing.json'
            self.assertEqual(study.main(['/missing/race.zip','--output',str(out)]),1)
            with patch.object(study,'run_one',side_effect=ValueError('processing failure')):
                self.assertEqual(study.main(['failed.zip','--output',str(Path(tmp)/'failed.json')]),1)
            self.assertTrue(json.loads(out.read_text())['errors'])

    def test_config_drift_fails_even_if_later_restored(self):
        o=self.observer()
        with patch.object(study,'config_stamp',return_value='changed'):
            with self.assertRaisesRegex(ValueError,'configuration changed'):
                o.sample(None,'race',1,SimpleNamespace(),[])


class ObserverTests(unittest.TestCase):
    def test_wec_predictions_equal_with_both_observers(self):
        from db import RaceDB
        from wec_live import WecLiveClient, replay_predict
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            observed = study.Observer()
            for i in range(2):
                client = WecLiveClient(db_path='', no_db=True)
                client.db = RaceDB(str(Path(tmp)/f'{i}.db'))
                try:
                    replay_predict(client, ROOT/'tests/fixtures/wec_capture_sample.jsonl.gz',
                                   cadence_s=5, on_sample=observed.sample if i else None,
                                   on_event=observed.event if i else None)
                    rows.append([tuple(r) for r in client.db.conn.execute(
                        'SELECT * FROM predictions ORDER BY ts,car_number')])
                finally:
                    client.db.close()
            self.assertTrue(rows[0])
            self.assertEqual(rows[0],rows[1])
            self.assertTrue(observed.pits['4'])

    def test_future_stop_durations_do_not_change_earlier_observer_samples(self):
        def early(multiplier):
            race=timing71.load(str(ROOT/'sample-archives/Detroit-2026-Race.zip'))
            stops=race.pit_stops()
            first=min(s.in_ts for seq in stops.values() for s in seq if s.in_ts)
            for seq in stops.values():
                for stop in seq:
                    if stop.duration_s: stop.duration_s *= multiplier
            race.pit_stops=lambda: stops
            race.full_frames=[(ts,f) for ts,f in race.full_frames if ts*1000<first]
            observer=study.Observer()
            with tempfile.TemporaryDirectory() as tmp:
                replay.build(race,str(Path(tmp)/'race.db'),cadence_s=5,on_sample=observer.sample)
            return dict(observer.samples)
        original=early(1)
        self.assertTrue(original)
        self.assertEqual(original,early(10))

    def test_zip_predictions_equal_with_and_without_observer(self):
        # Use a real small archive; its timestamps, flags and pit events exercise
        # the production replay path rather than duplicating it in a fixture.
        archive = next((ROOT/'sample-archives').glob('*.zip'))
        race=timing71.load(str(archive))
        race.full_frames=[(ts,f) for ts,f in race.full_frames
                          if f.get('session',{}).get('timeElapsed') is not None and f.get('cars')][:20]
        with tempfile.TemporaryDirectory() as tmp:
            paths=[str(Path(tmp)/f'{i}.db') for i in range(2)]
            replay.build(race,paths[0],cadence_s=5)
            o=study.Observer()
            replay.build(race,paths[1],cadence_s=5,on_sample=o.sample)
            def predictions(path):
                with sqlite3.connect(path) as conn:
                    return conn.execute('SELECT * FROM predictions ORDER BY ts,car_number').fetchall()
            self.assertTrue(predictions(paths[0]))
            self.assertEqual(predictions(paths[0]),predictions(paths[1]))


if __name__ == '__main__':
    unittest.main()
