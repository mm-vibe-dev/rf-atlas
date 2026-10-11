"""Reports separate observations, candidates, identifiers and measured coverage."""
from pathlib import Path
import tempfile
import unittest
from rfatlas.performance import discovery_report
from rfatlas.storage import open_database


class PerformanceTests(unittest.TestCase):
    def test_later_replay_verification_counts_one_recording_in_capture_window(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=open_database(root)
            db.execute("INSERT INTO signals(signal_id) VALUES('a')")
            for key,created,verified in [('first','1970-01-01T00:16:45Z',False),('revision','1970-01-02T00:00:00Z',True)]:
                result=dict(capture_timestamp='1970-01-01T00:16:42Z',protocol='RDS' if verified else 'UNKNOWN',protocol_verified=verified)
                db.execute('INSERT INTO identification_runs(run_key,signal_id,created,source_sha256,result) VALUES(?,?,?,?,?)',
                    (key,'a',created,'same-recording-digest',json.dumps(result)))
            db.commit();db.close()
            report=discovery_report(root,'1970-01-01T00:16:40Z','1970-01-01T00:17:00Z')
            self.assertEqual(report['analyzed_capture_instances'],1)
            self.assertEqual(report['verified_capture_instances'],1)
            self.assertEqual(report['verified_protocol_instances'],0)
            self.assertEqual(report['capture_protocol_verification_rate'],1)

    def test_union_revisits_invalid_and_ambiguous_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=open_database(root)
            db.execute("INSERT INTO signals(signal_id,protocol) VALUES('a','UNKNOWN')")
            db.execute("INSERT INTO inventory_regions(region_id,profile_id,start_hz,stop_hz) VALUES('r','test',100,200)")
            for when,valid in [(1000,1),(1005,1),(1008,0)]:
                db.execute('INSERT INTO inventory_measurements(region_id,observed,valid,quality) VALUES(?,?,?,?)',('r',when,valid,'{}'))
            for when in ('1970-01-01T00:16:41Z','1970-01-01T00:16:45Z','1970-01-01 00:16:42'):
                db.execute("INSERT INTO spectral_observations(signal_id,observed) VALUES('a',?)",(when,))
            initial=db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]
            db.commit();db.close()
            report=discovery_report(root,'1970-01-01T00:16:40Z','1970-01-01T00:17:00Z',initial_database_bytes=initial,
                health=[dict(cpu_percent=10,ram_used_bytes=1000),dict(cpu_percent=30,ram_used_bytes=2000)])
            self.assertEqual(report['successfully_measured_hz_gross'],200)
            self.assertEqual(report['successfully_measured_hz_unique'],100)
            self.assertEqual(report['invalid_measurements'],1)
            self.assertEqual(report['observations'],2)
            self.assertEqual(report['unique_candidate_signals'],1)
            self.assertEqual(report['legacy_ambiguous_observations_excluded'],1)
            self.assertEqual(report['host_cpu_percent']['mean'],20)
            self.assertEqual(report['stations_identified'],0)
            with self.assertRaises(ValueError):discovery_report(root,'1970-01-01','1970-01-02')
