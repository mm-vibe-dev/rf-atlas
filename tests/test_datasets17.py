import hashlib
from pathlib import Path
import tempfile
import unittest
from rfatlas.datasets import audit_partitions,recording_entry,support_reliability


class DatasetTests(unittest.TestCase):
    def test_recording_manifest_rejects_predictions_as_truth_and_checks_original(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'source.cu8';p.write_bytes(b'\x80\x81'*4096)
            meta=dict(format='cu8',sample_rate=250000,sample_count=4096,center_hz=100000000,gain_db=15.7,device_identity='constructed-receiver',bias_tee=False,sample_rate_reported_hz=250000.,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            e=recording_entry(p,meta,group='session-A')
            self.assertEqual(e['ground_truth'],{});self.assertEqual(e['label_status'],'UNLABELED')
            self.assertEqual(e['receiver_configuration']['gain_db'],15.7)
            self.assertEqual(e['receiver_configuration']['device_identity'],'constructed-receiver')
            self.assertIs(e['receiver_configuration']['bias_tee'],False)
            self.assertEqual(e['receiver_configuration']['sample_rate_reported_hz'],250000.)
            with self.assertRaises(ValueError):recording_entry(p,meta,group='A',label=dict(basis='CATALOG_PREDICTION',source='prior engine',confidence='STRONG'))
            p.write_bytes(b'\x80\x82'*4096)
            with self.assertRaises(ValueError):recording_entry(p,meta,group='A')

    def test_session_and_duplicate_content_leakage_even_with_different_ids(self):
        rows=[dict(recording_id='a',sha256='x',group='sessionA',partition='TRAINING'),dict(recording_id='b',sha256='y',group='sessionA',partition='TEST')]
        self.assertFalse(audit_partitions(rows)['valid'])
        rows[1].update(group='sessionB',sha256='x')
        report=audit_partitions(rows);self.assertFalse(report['valid']);self.assertEqual(report['duplicate_recordings'],['b'])
        rows[1]['partition']='TRAINING';self.assertTrue(audit_partitions(rows)['valid'])

    def test_reliability_excludes_abstentions_and_is_not_calibration(self):
        rows=[dict(truth='DIGITAL',predicted='DIGITAL',score=.8),dict(truth='DIGITAL',predicted='ANALOG',score=.8),dict(truth='DIGITAL',predicted='UNKNOWN',score=0)]
        r=support_reliability(rows);self.assertEqual(r['count'],2);self.assertFalse(r['calibrated'])
        self.assertAlmostEqual(r['ece'],.3);self.assertAlmostEqual(r['brier'],.34)
