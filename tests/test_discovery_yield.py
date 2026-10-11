"""Independent behavioral gates for bounded scheduling and report accounting."""
import json
from pathlib import Path
import tempfile
import unittest
from rfatlas.storage import open_database
from rfatlas.inventory import default_profiles,enqueue_investigations
from rfatlas.performance import discovery_report,compare_reports

class YieldTests(unittest.TestCase):
 def test_fm_passband_deferral_keeps_catalog_and_adjacent_channel(self):
  with tempfile.TemporaryDirectory() as folder:
   d=open_database(Path(folder));p=dict(default_profiles()[0],id='fm',start_hz=96000000,stop_hz=98000000,capture_allowed=True)
   for identity,freq,bw,snr in [('broad',97000000,180000,20),('fragment',97010000,5000,30),('adjacent',97200000,180000,20),('weak',97500000,10000,9)]:
    d.execute('INSERT INTO signals(signal_id,center_frequency_hz,occupied_bandwidth_hz,peak_snr_db) VALUES(?,?,?,?)',(identity,freq,bw,snr))
   self.assertEqual(enqueue_investigations(d,p,1000),2)
   self.assertEqual(d.execute('SELECT count(*) FROM signals').fetchone()[0],4)
   jobs=d.execute('SELECT signal_id,settings FROM investigation_jobs').fetchall()
   self.assertEqual({r[0] for r in jobs},{'broad','adjacent'})
   self.assertTrue(all(json.loads(r[1])['sample_rate']==1024000 for r in jobs))
   reasons=dict(d.execute('SELECT signal_id,reason FROM investigation_decisions'))
   self.assertEqual(reasons['fragment'],'FM_PASSBAND_ALREADY_INVESTIGATED')
   self.assertEqual(reasons['weak'],'BELOW_QUALITY_THRESHOLD');d.close()

 def test_frequency_selects_adsb_rate_independent_of_profile_name(self):
  with tempfile.TemporaryDirectory() as folder:
   d=open_database(Path(folder));p=dict(default_profiles()[0],id='custom-aircraft',start_hz=1089000000,stop_hz=1091000000,capture_allowed=True)
   d.execute("INSERT INTO signals(signal_id,center_frequency_hz,occupied_bandwidth_hz,peak_snr_db) VALUES('a',1090000000,10000,20)")
   self.assertEqual(enqueue_investigations(d,p,1000),1)
   self.assertEqual(json.loads(d.execute('SELECT settings FROM investigation_jobs').fetchone()[0])['sample_rate'],2400000);d.close()

 def test_capacity_and_permission_are_persisted_not_silently_dropped(self):
  with tempfile.TemporaryDirectory() as folder:
   d=open_database(Path(folder));p=dict(default_profiles()[9])
   for identity in ('a','b'):d.execute('INSERT INTO signals(signal_id,center_frequency_hz,occupied_bandwidth_hz,peak_snr_db) VALUES(?,1090000000,10000,20)',(identity,))
   enqueue_investigations(d,p,1000,limit=1)
   self.assertEqual(dict(d.execute('SELECT reason,count(*) FROM investigation_decisions GROUP BY reason')),{'ENQUEUED':1,'QUEUE_CAPACITY':1})
   enqueue_investigations(d,dict(p,capture_allowed=False),1001)
   self.assertEqual(d.execute("SELECT count(*) FROM investigation_decisions WHERE reason='PROFILE_CAPTURE_DISABLED'").fetchone()[0],2);d.close()

 def test_report_cohort_and_revisit_decisions_use_distinct_denominators(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);d=open_database(root)
   for identity in ('a','b'):
    d.execute('INSERT INTO signals(signal_id) VALUES(?)',(identity,))
    d.execute("INSERT INTO spectral_observations(signal_id,observed) VALUES(?,'1970-01-01T00:16:41Z')",(identity,))
   d.execute("INSERT INTO investigation_jobs(job_id,signal_id,status,created,updated) VALUES('j','a','complete',1001,1010)")
   for when in (1001,1002):d.execute("INSERT INTO investigation_decisions(signal_id,created,reason) VALUES('a',?,'ENQUEUED')",(when,))
   d.commit();d.close()
   r=discovery_report(root,'1970-01-01T00:16:40Z','1970-01-01T00:17:00Z')
   self.assertEqual(r['candidate_to_completed_investigation_rate'],.5)
   self.assertEqual(r['investigation_completion_rate'],1)
   self.assertIsNone(r['decoder_attempt_rate'])
   self.assertEqual(r['investigation_decision_events']['ENQUEUED'],2)
   self.assertEqual(r['latest_candidate_decisions']['ENQUEUED'],1)
   self.assertIn('no speed',compare_reports(r,r)['basis'])
