import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from rfatlas.config import Config
from rfatlas.inventory import (InventoryWorker,default_profiles,validate_profile,load_profiles,
    write_profiles,configure_regions,inventory_view,queue_view,enqueue_investigations,cancel_job,compare_spectra,_rows)
from rfatlas.platforms import DeviceLock
from rfatlas.storage import open_database


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.root=Path(self.directory.name)
        self.db=open_database(self.root);self.now=1000.
        self.config=Config(self.root,lock_root=self.root/'locks')
        self.profile=validate_profile(dict(default_profiles()[9],start_hz=1089000000,stop_hz=1091000000))
        configure_regions(self.db,[self.profile],['ads-b'])
        self.db.execute("INSERT INTO signals(signal_id,center_frequency_hz,occupied_bandwidth_hz,peak_snr_db,modulation,protocol,observation_count) VALUES('synthetic',1090000000,10000,20,'UNKNOWN','UNKNOWN',1)");self.db.commit()
        self.calls=[]
        self.worker=InventoryWorker(self.db,self.config,self.scan,self.capture,self.analyze,clock=lambda:self.now)

    def tearDown(self):self.db.close();self.directory.cleanup()

    def scan(self,region,profile):
        self.calls.append(('scan',region['stage']))
        run='run-'+str(len(self.calls));self.now+=5
        self.db.execute('INSERT INTO survey_runs VALUES(?,?,?, ?,?,?,?,NULL,?)',
                        (run,'synthetic','synthetic','complete',region['start_hz'],region['stop_hz'],'quick','{}'))
        self.db.execute('INSERT INTO spectral_observations(run_id,signal_id,observed) VALUES(?,?,?)',(run,'synthetic','synthetic'))
        path=self.root/(run+'.csv')
        path.write_text(f"2026-01-01, 00:00:00, {region['start_hz']}, {region['stop_hz']}, 125000, 1, "+','.join(['-60']*16)+'\n')
        self.db.execute('INSERT INTO scan_regions VALUES(?,?,?,?,?,?)',(run,region['start_hz'],region['stop_hz'],'complete','synthetic',str(path)))
        self.db.commit();return run

    def capture(self,job,settings):
        self.calls.append(('capture',settings['sample_rate']))
        return self.root/'synthetic.cu8',{'signal_id':job['signal_id']}

    def analyze(self,path,metadata,job):self.calls.append(('analyze',));return 'synthetic-result-key'

    def test_profiles_roundtrip_receiver_envelope_and_manual_gain(self):
        path=self.root/'profiles.toml';write_profiles(path)
        profiles=load_profiles(path);self.assertEqual(len(profiles),28)
        with self.assertRaises(ValueError):write_profiles(path)
        for changes in ({'gain':'auto'},{'start_hz':100},{'bin_hz':-1},{'duration_s':100},{'enabled':1}):
            with self.assertRaises(ValueError):validate_profile(dict(self.profile,**changes))
        with self.assertRaises(ValueError):configure_regions(self.db,profiles,['missing'])

    def test_configuration_is_idempotent_and_edits_disable_old_schedule(self):
        self.assertEqual(configure_regions(self.db,[self.profile],['ads-b']),0)
        changed=dict(self.profile,bin_hz=5000)
        self.assertEqual(configure_regions(self.db,[changed],['ads-b']),1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM inventory_regions WHERE enabled=1').fetchone()[0],1)

    def test_valid_coverage_fine_revisit_history_and_detection_stages(self):
        self.worker.run(3,300)
        view=inventory_view(self.db)
        self.assertEqual(view['coverage']['visited_hz'],2000000)
        self.assertEqual(view['coverage']['valid_hz'],2000000)
        self.assertEqual(view['regions'][0]['quality']['resolution_hz'],125000)
        self.assertEqual(view['regions'][0]['quality']['requested_bin_hz'],1000)
        self.assertEqual(self.calls[:2],[('scan','coarse'),('scan','fine')])
        self.assertEqual(self.calls[2][0],'capture')
        comparisons=[json.loads(row[0]) for row in self.db.execute('SELECT comparison FROM inventory_measurements')]
        self.assertIsNone(comparisons[0]['previous_signal_count'])
        self.assertEqual(comparisons[1]['signal_count_change'],0)
        self.assertEqual(queue_view(self.db)['counts'],{'complete':1})

    def test_invalid_measurement_visited_is_not_valid_and_cooldown(self):
        def fail(*args):raise ValueError('Malformed synthetic measurement')
        self.worker.scan=fail;self.worker.run(2,300)
        view=inventory_view(self.db)
        self.assertEqual(view['coverage']['visited_hz'],2000000)
        self.assertEqual(view['coverage']['valid_hz'],0)
        self.assertEqual(self.db.execute('SELECT visit_count FROM inventory_regions').fetchone()[0],1)

    def test_queue_priority_bounds_quality_and_stable_verified_avoidance(self):
        self.assertEqual(enqueue_investigations(self.db,self.profile,self.now,limit=1),1)
        self.assertEqual(enqueue_investigations(self.db,self.profile,self.now,limit=1),0)
        self.db.execute("UPDATE investigation_jobs SET status='complete',updated=0")
        self.db.execute("UPDATE signals SET protocol='ADS-B'");self.db.commit()
        # Changed protocol fingerprint allows a single reconsideration after cooldown.
        self.assertEqual(enqueue_investigations(self.db,self.profile,5000),1)
        self.db.execute("UPDATE investigation_jobs SET status='complete',updated=5000");self.db.commit()
        self.assertEqual(enqueue_investigations(self.db,self.profile,10000),0)
        self.assertEqual(enqueue_investigations(self.db,dict(self.profile,capture_allowed=False),10000),0)

    def test_failure_cancellation_and_profile_revocation(self):
        enqueue_investigations(self.db,self.profile,self.now)
        job=_rows(self.db,'SELECT * FROM investigation_jobs')[0]
        self.assertTrue(cancel_job(self.db,job['job_id'],self.now))
        self.assertFalse(cancel_job(self.db,job['job_id'],self.now))
        self.db.execute('DELETE FROM investigation_events');self.db.execute('DELETE FROM investigation_jobs');self.db.commit()
        enqueue_investigations(self.db,self.profile,self.now)
        job=_rows(self.db,'SELECT * FROM investigation_jobs')[0]
        configure_regions(self.db,[dict(self.profile,capture_allowed=False)],['ads-b'])
        self.worker.step_investigate(job)
        self.assertEqual(queue_view(self.db)['counts'],{'failed':1})
        self.assertFalse(any(c[0]=='capture' for c in self.calls))

    def test_worker_resume_ownership_budget_and_interruption(self):
        self.db.execute("UPDATE inventory_regions SET status='running'");self.db.commit()
        with DeviceLock(self.config.lock_root,'inventory-scheduler'):
            with self.assertRaises(RuntimeError):self.worker.run()
        self.assertEqual(self.worker.run(1,1)['steps_completed'],0)
        self.assertEqual(self.db.execute('SELECT status FROM inventory_regions').fetchone()[0],'pending')
        def interrupt(*args):raise KeyboardInterrupt()
        self.worker.scan=interrupt
        with self.assertRaises(KeyboardInterrupt):self.worker.run()
        self.assertEqual(self.db.execute('SELECT status FROM inventory_regions').fetchone()[0],'failed')

    def test_additive_migration_consistent_backup_and_reopen(self):
        root=self.root/'legacy';(root/'data').mkdir(parents=True)
        db=sqlite3.connect(root/'data/rf_atlas.sqlite')
        # Obtain the exact trusted 0.5 schema rather than weaken this migration fixture.
        from tests.schema05 import open_database as old_database
        db.close();db=old_database(root)
        db.execute("INSERT INTO signals(signal_id,center_frequency_hz) VALUES('legacy',100000000)");db.commit();db.close()
        migrated=open_database(root)
        self.assertEqual(migrated.execute('SELECT signal_id FROM signals').fetchone()[0],'legacy')
        self.assertEqual(migrated.execute('PRAGMA user_version').fetchone()[0],11);migrated.close()
        backups=list((root/'data/backups').glob('*.sqlite'));self.assertEqual(len(backups),1)
        backup=sqlite3.connect(backups[0]);self.assertEqual(backup.execute('SELECT signal_id FROM signals').fetchone()[0],'legacy');backup.close()
        migrated=open_database(root);migrated.close()
        self.assertEqual(len(list((root/'data/backups').glob('*.sqlite'))),1)

    def test_queue_ordering_and_cancel_during_analysis(self):
        self.db.execute("INSERT INTO signals(signal_id,center_frequency_hz,occupied_bandwidth_hz,peak_snr_db,modulation,protocol,observation_count) VALUES('known-mod',1090005000,10000,30,'NFM','UNKNOWN',1)");self.db.commit()
        self.assertEqual(enqueue_investigations(self.db,self.profile,self.now),2)
        jobs=_rows(self.db,'SELECT * FROM investigation_jobs ORDER BY priority DESC')
        self.assertEqual(jobs[0]['signal_id'],'synthetic')
        def cancel_during_analysis(path,meta,job):
            cancel_job(self.db,job['job_id'],self.now)
            return 'retained-analysis-reference'
        self.worker.analyze=cancel_during_analysis
        self.worker.step_investigate(jobs[0])
        row=self.db.execute('SELECT status,result_key FROM investigation_jobs WHERE job_id=?',(jobs[0]['job_id'],)).fetchone()
        self.assertEqual(row,('cancelled','retained-analysis-reference'))
        self.assertEqual(self.db.execute("SELECT investigation_status FROM signals WHERE signal_id='synthetic'").fetchone()[0],'cancelled')
        cancel_job(self.db,jobs[1]['job_id'],self.now)
        calls=len(self.calls);self.worker.step_investigate(jobs[1]);self.assertEqual(len(self.calls),calls)

    def test_recovery_updates_signal_state_and_events(self):
        enqueue_investigations(self.db,self.profile,self.now)
        self.db.execute("UPDATE investigation_jobs SET status='running'");self.db.commit();self.worker.recover()
        self.assertEqual(self.db.execute('SELECT investigation_status FROM signals').fetchone()[0],'pending')
        self.assertEqual(self.db.execute('SELECT count(*) FROM investigation_events').fetchone()[0],2)

    def test_complete_status_without_raw_coverage_is_rejected(self):
        def incomplete(region,profile):
            run=self.scan(region,profile)
            self.db.execute('DELETE FROM scan_regions WHERE run_id=?',(run,));self.db.commit();return run
        self.worker.scan=incomplete;self.worker.run()
        self.assertEqual(inventory_view(self.db)['coverage']['valid_hz'],0)

    def test_historical_spectral_comparison_preserves_grid_constraints(self):
        previous=self.root/'previous.csv';current=self.root/'current.csv'
        header='2026-01-01,00:00:00,100000000,100016000,1000,1,'
        previous.write_text(header+','.join(['-60']*16)+'\n')
        current.write_text(header+','.join(['-57']*16)+'\n')
        comparison=compare_spectra(previous,current)
        self.assertTrue(comparison['comparable']);self.assertEqual(comparison['median_change_db'],3)
        current.write_text(header.replace('100016000','100032000').replace(',1000,',',2000,')+','.join(['-57']*16)+'\n')
        self.assertFalse(compare_spectra(previous,current)['comparable'])


if __name__=='__main__':unittest.main()
