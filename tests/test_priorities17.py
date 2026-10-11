from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from rfatlas.priorities import weights,score,queue,observation_history
from rfatlas.storage import open_database


class PriorityTests(unittest.TestCase):
    def test_configurable_ranking_favors_recorded_recurring_evidence(self):
        base=dict(observation_count=20,peak_snr_db=30,modulation='UNKNOWN',signal_class='UNKNOWN')
        self.assertGreater(score(dict(base,recording_count=1))['score'],score(dict(base,recording_count=0))['score'])
        self.assertLess(score(dict(base,signal_class='NOISE_CANDIDATE'))['score'],score(base)['score'])
        self.assertEqual(score(base,dict.fromkeys(weights(),0))['score'],0)
        for bad in ({'snr':float('nan')},{'snr':True},{'snr':21},{'suspicion':3}):
            with self.assertRaises(ValueError):weights(bad)

    def test_queue_excludes_verified_protocol_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as d,closing(open_database(Path(d))) as db:
            db.executemany('INSERT INTO signals(signal_id,protocol,modulation,observation_count) VALUES(?,?,?,?)',[('unresolved','UNKNOWN','UNKNOWN',10),('verified','DMR','4FSK',100)])
            db.commit();before=db.total_changes
            self.assertEqual([r['signal_id'] for r in queue(db)['items']],['unresolved'])
            self.assertEqual(db.total_changes,before)

    def test_ambiguous_timestamps_and_frequency_span_are_not_identity(self):
        r=observation_history([dict(center_hz=1000,observed='2026-01-01T04:00:00Z'),dict(center_hz=1100,observed='2026-01-01T05:00:00')])
        self.assertEqual(r['center_span_hz'],100);self.assertEqual(r['utc_timed_observations'],1)
        self.assertEqual(r['hour_counts_utc']['4'],1)
