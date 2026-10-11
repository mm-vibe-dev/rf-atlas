"""Continuous discovery lifecycle and persisted scheduling; synthetic only."""
from pathlib import Path
from contextlib import closing
import tempfile
import time
import unittest
from unittest.mock import patch

from rfatlas.autosurvey import AutoSurveyController, _InventoryChild, survey_profiles
from rfatlas.config import Config
from rfatlas.inventory import InventoryWorker, configure_regions, default_profiles, load_profiles
from rfatlas.storage import open_database
from tests.test_scanner import FakeProcess, FakeJob


class Child:
    def __init__(self):
        self.state = 'idle'
        self.calls = []
    def start(self, params):
        self.calls.append(params)
        self.state = 'running'
    def status(self):
        return {'active': self.state == 'running', 'status': self.state, 'return_code': 0}
    def stop(self):
        self.state = 'stopped'


class AutoSurveyTests(unittest.TestCase):
    def test_cooldown_wait_avoids_idle_child_launches_and_ready_jobs_wake(self):
        controller=AutoSurveyController(self.config,child=Child())
        self.addCleanup(controller.close)
        with closing(open_database(self.root)) as db:
            db.execute("INSERT INTO inventory_regions(region_id,enabled,next_due) VALUES('r',1,?)",(time.time()+600,))
            db.commit()
        self.assertEqual(controller._idle_delay(),30)
        with closing(open_database(self.root)) as db:
            db.execute("INSERT INTO investigation_jobs(job_id,status,next_eligible) VALUES('j','pending',0)")
            db.commit()
        self.assertEqual(controller._idle_delay(),1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = Config(self.root, lock_root=self.root / 'locks', tools={'rtl_power': 'synthetic-tool'})

    def test_modes_strict_validation_and_canonical_full_coverage(self):
        mode, quick = survey_profiles({'mode': 'quick'})
        self.assertEqual(mode, 'quick')
        self.assertIn('fm-broadcast', [p['id'] for p in quick])
        self.assertTrue(next(p for p in quick if p['id'] == 'land-mobile')['capture_allowed'])
        _, full = survey_profiles({'mode': 'full', 'gain': 15.7, 'integration_s': 5})
        self.assertEqual(full[0]['start_hz'], 500000)
        self.assertEqual(full[-1]['stop_hz'], 1766000000)
        self.assertTrue(all(a['stop_hz'] == b['start_hz'] for a,b in zip(full,full[1:])))
        self.assertTrue(all(p['duration_s'] >= 5 for p in full))
        edited = default_profiles()
        edited[0]['start_hz'] = 1000000
        self.assertEqual(survey_profiles({'mode':'full'},edited)[1][0]['start_hz'],500000)
        for bad in [{'mode': 'unsupported'}, {'profiles': []}, {'profiles': ['no-such-band']},
                    {'profiles': ['hf','hf']}, {'mode':'full','profiles':['hf']},
                    {'integration_s': True}, {'revisit_s': 0}, {'gain': 'auto'}, {'arbitrary': '--new'}]:
            with self.assertRaises(ValueError):
                survey_profiles(bad)

    def test_opt_in_pause_resume_and_synchronous_stop(self):
        child = Child()
        controller = AutoSurveyController(self.config, child=child, poll_seconds=.01)
        self.addCleanup(controller.close)
        self.assertFalse(controller.status()['active'])
        self.assertEqual(child.calls, [])
        state = controller.start({'mode': 'quick', 'profiles': ['ads-b']})
        self.assertTrue(state['active'])
        profiles = load_profiles(self.root/'runtime/auto-survey.toml')
        self.assertEqual([p['id'] for p in profiles], ['ads-b'])
        with self.assertRaises(RuntimeError):
            controller.start({'mode': 'full'})
        self.assertEqual(controller.pause()['status'], 'paused')
        self.assertFalse(child.status()['active'])
        self.assertIsNone(controller._thread)
        self.assertTrue(controller.resume()['active'])
        self.assertEqual(len(child.calls), 2)
        controller.stop()
        with self.assertRaises(RuntimeError):
            controller.resume()

    def test_child_completion_continues_and_failure_does_not_retry(self):
        child = Child()
        controller = AutoSurveyController(self.config, child=child, poll_seconds=.01)
        self.addCleanup(controller.close)
        controller.start({'mode':'smart'})
        child.state = 'complete'
        deadline = time.monotonic()+3
        while len(child.calls)<2 and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(len(child.calls),2)
        self.assertEqual(controller.status()['cycles_completed'],1)
        child.state = 'failed'
        deadline=time.monotonic()+1
        while controller.status()['status']=='running' and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(controller.status()['status'],'failed')
        self.assertEqual(len(child.calls),2)
        self.assertNotIn('synthetic-tool',str(controller.status()))

    def test_stopped_child_requeues_interrupted_job_without_ghost_running_status(self):
        db=open_database(self.root)
        db.execute("INSERT INTO signals(signal_id,investigation_status) VALUES('test','running')")
        db.execute("INSERT INTO investigation_jobs(job_id,signal_id,status) VALUES('job','test','running')")
        db.commit()
        child=Child();controller=AutoSurveyController(self.config,child=child)
        controller.start({'mode':'smart','profiles':['ads-b']})
        self.assertEqual(controller.stop()['status'],'stopped')
        self.assertEqual(db.execute('SELECT status FROM investigation_jobs').fetchone()[0],'pending')
        self.assertEqual(db.execute('SELECT investigation_status FROM signals').fetchone()[0],'pending')
        self.assertGreater(db.execute('SELECT next_eligible FROM investigation_jobs').fetchone()[0],time.time())
        db.close()

    def test_bounded_child_fixed_arguments_and_process_cleanup(self):
        child = _InventoryChild(self.config)
        self.addCleanup(child.stop)
        process = FakeProcess()
        job = FakeJob()
        with patch('rfatlas.autosurvey.RTLBackend.tool',return_value=Path('synthetic-tool')), \
             patch('rfatlas.autosurvey.subprocess.Popen',return_value=process) as popen, \
             patch('rfatlas.autosurvey.WindowsJob',return_value=job), \
             patch('rfatlas.scanner.os.killpg',create=True):
            child.start({'path':self.root/'profiles.toml','mode':'smart'})
            command=popen.call_args.args[0]
            self.assertIn('inventory-run',command)
            self.assertIn('--smart',command)
            self.assertNotIn('--new',command)
            self.assertEqual(command[command.index('--max-steps')+1],'3')
            snapshot=child._snapshot
            self.assertTrue(snapshot.is_file())
            child.stop()
            self.assertIsNotNone(process.poll())
            self.assertFalse(snapshot.exists())

    def test_smart_prioritizes_unknown_and_periodically_explores(self):
        db=open_database(self.root)
        self.addCleanup(db.close)
        profiles=[dict(default_profiles()[0],id='synthetic',start_hz=100000000,stop_hz=106000000)]
        configure_regions(db,profiles)
        db.execute("UPDATE inventory_regions SET visit_count=5,valid_count=5,next_due=0")
        db.execute("UPDATE inventory_regions SET visit_count=1 WHERE start_hz=104000000")
        db.execute("INSERT INTO signals(signal_id,center_frequency_hz,modulation,protocol,duty_cycle) VALUES('synthetic',102100000,'UNKNOWN','UNKNOWN',0.1)")
        db.commit()
        worker=InventoryWorker(db,self.config,None,None,None)
        # Persisted phase 0 explores the least visited region.
        self.assertEqual(worker._due_region(1000,smart=True)[0]['start_hz'],104000000)
        region=db.execute('SELECT region_id FROM inventory_regions LIMIT 1').fetchone()[0]
        db.execute('INSERT INTO inventory_measurements(region_id,observed,valid) VALUES(?,1,1)',(region,));db.commit()
        self.assertEqual(worker._due_region(1000,smart=True)[0]['start_hz'],102000000)
        db.close()
        db=open_database(self.root)
        self.addCleanup(db.close)
        worker=InventoryWorker(db,self.config,None,None,None)
        self.assertEqual(worker._due_region(1000,smart=True)[0]['start_hz'],102000000)
        db.execute('INSERT INTO inventory_measurements(region_id,observed,valid) VALUES(?,2,1)',(region,))
        db.execute('INSERT INTO inventory_measurements(region_id,observed,valid) VALUES(?,3,1)',(region,));db.commit()
        self.assertEqual(worker._due_region(1000,smart=True)[0]['start_hz'],104000000)


if __name__ == '__main__':
    unittest.main()
