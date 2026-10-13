"""Unattended windows cannot grade old sessions or silently incomplete captures."""
import gzip
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import capture_readiness as readiness
from db import RaceDB


def stamp(ts):return datetime.fromtimestamp(ts,timezone.utc).isoformat()


class DatabaseFixture:
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=RaceDB(Path(self.tmp.name)/'race.db')
        self.seed('old',900,finished=True)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def seed(self,oid,ts,finished=False,series='wec',kind='RACE'):
        db=self.db
        db.set_session(oid,{'name':'Test','type':kind},series=series)
        db.update_status({'currentFlag':'GF','currentLap':10,'isFinished':finished,
                          'startTime':100,'finalType':'BY_TIME','finalTime':1})
        db.ingest_car('7',{'laps':10,'overall_position':1,'pos_in_class':1},
                      {'class':'HYPERCAR','lastLapTime':90000,'isRunning':True},10)
        db.conn.execute('UPDATE lap_history SET recorded_at=? WHERE session_oid=?',(stamp(ts),oid))
        db.conn.execute('UPDATE standings_current SET timing_updated_at=? WHERE session_oid=?',(stamp(ts),oid))
        db.conn.execute('CREATE TABLE IF NOT EXISTS predictions (session_oid TEXT,ts INTEGER,net_position INTEGER)')
        db.conn.execute('INSERT INTO predictions (session_oid,ts,car_number,net_position,pos_in_class,projected_finish) VALUES (?,?,"7",1,1,1)',(oid,ts*1000))
        db.commit()
    def select(self,kind='race'):return readiness.window_session(self.db.conn,'wec',kind,1000,1100)


class WindowTests(DatabaseFixture, unittest.TestCase):
    def test_old_complete_race_cannot_satisfy_current_window(self):
        with self.assertRaisesRegex(ValueError,'found 0'):self.select()
    def test_expired_clock_is_not_terminal_evidence(self):
        self.seed('new',1050)
        with self.assertRaisesRegex(ValueError,'terminal state'):self.select()
    def test_current_complete_session_is_pinned_despite_newer_other_kind(self):
        self.seed('new',1050,finished=True)
        self.seed('practice',1060,kind='PRACTICE')
        self.assertEqual(self.select(),'new')
        self.assertEqual(self.select('practice'),'practice')
    def test_other_series_and_missing_prediction_fail(self):
        self.seed('imsa',1050,finished=True,series='imsa')
        with self.assertRaisesRegex(ValueError,'found 0'):self.select()
        self.seed('new',1050,finished=True)
        self.db.conn.execute('DELETE FROM predictions WHERE session_oid="new"')
        with self.assertRaisesRegex(ValueError,'predictions'):self.select()
    def test_multiple_races_do_not_silently_choose_latest(self):
        self.seed('one',1050,finished=True);self.seed('two',1060,finished=True)
        with self.assertRaisesRegex(ValueError,'found 2'):self.select()
    def test_telemetry_cannot_substitute_for_timing(self):
        self.seed('new',1050,finished=True)
        self.db.conn.execute('UPDATE standings_current SET timing_updated_at=? WHERE session_oid="new"',(stamp(900),))
        with self.assertRaisesRegex(ValueError,'timing observations'):self.select()


class ConductorTests(DatabaseFixture, unittest.TestCase):
    def test_children_inherit_runtime(self):
        import weekend_conductor as c
        self.assertEqual(c.PYTHON,sys.executable)
    def test_evaluator_nonzero_fails_and_receives_acquired_session(self):
        import weekend_conductor as c
        self.seed('new',1050,finished=True,series='imsa')
        with patch.object(c.subprocess,'run',return_value=type('Result',(),{'returncode':2,'stdout':'','stderr':'failure'})()) as run, patch.object(c,'_log'):
            with self.assertRaisesRegex(ValueError,'evaluator failed'):
                c.postprocess_window('imsa','race',Path(self.tmp.name)/'race.db',None,None,1000,1100)
            self.assertEqual(run.call_args.args[0][-3:],['--session','new','--force'])
    def test_no_current_data_never_invokes_evaluator(self):
        import weekend_conductor as c
        with patch.object(c.subprocess,'run') as run:
            with self.assertRaises(ValueError):
                c.postprocess_window('wec','race',Path(self.tmp.name)/'race.db',None,None,1000,1100)
            run.assert_not_called()
    def test_failed_pinned_practice_health_is_not_success(self):
        import weekend_conductor as c
        self.seed('practice',1050,kind='PRACTICE',series='imsa')
        with patch.object(c.session_healthcheck,'check',return_value='[FAIL] no activity') as check, patch.object(c,'_log'):
            with self.assertRaisesRegex(ValueError,'activity health'):
                c.postprocess_window('imsa','practice',Path(self.tmp.name)/'race.db',None,None,1000,1100)
            self.assertEqual(check.call_args.kwargs['session_oid'],'practice')

    def test_conductor_exit_reflects_failed_window(self):
        import weekend_conductor as c
        now=datetime.now(timezone.utc)
        schedule=[('imsa','Test','practice',now,1)]
        with patch.object(c,'LOG_DIR',Path(self.tmp.name)), patch.object(c,'DATA_DIR',Path(self.tmp.name)), patch.object(sys,'argv',['conductor']), patch('event_schedule.load_schedule',return_value=schedule), patch.object(c,'run_session',return_value=False), patch.object(c.signal,'signal'), patch.object(c,'_log'), patch.object(c,'_file_logger'), patch('runtime.prune_logs'):
            self.assertEqual(c.main(),1)

    def test_health_cli_fails_without_creating_missing_database(self):
        import subprocess
        path=Path(self.tmp.name)/'absent.db'
        result=subprocess.run([sys.executable,str(ROOT/'src/session_healthcheck.py'),'--db',str(path),'--series','wec'],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('[FAIL]',result.stdout)
        self.assertFalse(path.exists())


class CaptureTests(unittest.TestCase):
    def clean_frames(self):
        with gzip.open(ROOT/'tests/fixtures/wec_capture_sample.jsonl.gz','rt') as stream:
            rows=[]
            for line in stream:
                try:rows.append(json.loads(line))
                except ValueError:pass
        return rows
    def write(self,path,rows):
        with gzip.open(path,'wt') as stream:
            for row in rows:stream.write(json.dumps(row)+'\n')
    def test_terminal_protocol_and_input_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'capture.gz';self.write(path,self.clean_frames())
            result=readiness.check_wec_capture(path,'wec_live_19152')
            self.assertTrue(result['terminal_observed'])
            self.assertTrue(result['gzip_trailer_complete'])
            self.assertEqual(result['frames'],17)
            with self.assertRaisesRegex(ValueError,'match'):readiness.check_wec_capture(path,'wrong')
    def test_old_raw_capture_cannot_satisfy_current_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'capture.gz';self.write(path,self.clean_frames())
            with self.assertRaisesRegex(ValueError,'acquisition window'):
                readiness.check_wec_capture(path,'wec_live_19152',window_start_s=1,window_end_s=2)

    def test_intact_gzip_without_terminal_frame_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=self.clean_frames()[:-1]
            rows[0]['data']['sessionInfo']['hasSeenChequered']=False
            rows[0]['data']['raceFlags']=[]
            path=Path(tmp)/'capture.gz';self.write(path,rows)
            with self.assertRaisesRegex(ValueError,'terminal'):readiness.check_wec_capture(path,'wec_live_19152')
    def test_malformed_frame_and_dispatch_failure_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'capture.gz';self.write(path,self.clean_frames())
            with gzip.open(path,'at') as stream:stream.write('{torn\n')
            with self.assertRaises(ValueError):readiness.check_wec_capture(path,'wec_live_19152')
            self.write(path,self.clean_frames())
            with patch('wec_live.WecLiveClient._dispatch_channel',side_effect=RuntimeError('dispatch failure')):
                with self.assertRaisesRegex(RuntimeError,'dispatch failure'):readiness.check_wec_capture(path,'wec_live_19152')
    def test_terminal_capture_with_missing_trailer_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'capture.gz'
            with path.open('wb') as raw:
                stream=gzip.GzipFile(fileobj=raw,mode='wb')
                for row in self.clean_frames():stream.write((json.dumps(row)+'\n').encode())
                stream.flush();data=path.read_bytes()
                stream.close()
            path.write_bytes(data)
            result=readiness.check_wec_capture(path,'wec_live_19152')
            self.assertFalse(result['gzip_trailer_complete'])
            self.assertTrue(result['terminal_observed'])
    def test_bootstrap_alone_is_not_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'capture.gz';self.write(path,self.clean_frames()[:1])
            with self.assertRaisesRegex(ValueError,'beyond bootstrap'):readiness.check_wec_capture(path,'wec_live_19152')

    def test_empty_timing_payload_is_not_acquisition(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=self.clean_frames()[:2];rows[1]['data']=None
            path=Path(tmp)/'capture.gz';self.write(path,rows)
            with self.assertRaisesRegex(ValueError,'beyond bootstrap'):
                readiness.check_wec_capture(path,'wec_live_19152')

    def test_backwards_or_unknown_clock_fails(self):
        for ts in (None,1):
            with tempfile.TemporaryDirectory() as tmp:
                rows=self.clean_frames();rows[-1]['ts']=ts
                path=Path(tmp)/'capture.gz';self.write(path,rows)
                with self.assertRaisesRegex(ValueError,'timestamp'):readiness.check_wec_capture(path,'wec_live_19152')


if __name__=='__main__':unittest.main()
