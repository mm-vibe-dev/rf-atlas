"""Discovery counts and time scopes use stored evidence, never band guesses."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rfatlas.discovery import DiscoveryView, HealthMonitor
from rfatlas.storage import open_database


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        with closing(open_database(self.root)) as db:
            for run in ('first','second'):
                db.execute("INSERT INTO survey_runs(run_id,status) VALUES(?,'complete')",(run,))
            for signal,protocol in [('unknown','UNKNOWN'),('air','ADS-B')]:
                db.execute('INSERT INTO signals(signal_id,center_frequency_hz,protocol) VALUES(?,1090000000,?)',(signal,protocol))
            for signal,run,observed in [('unknown','first','2026-01-01 01:00:00'),('air','first','2026-01-01T00:30:00Z'),('unknown','second','2026-01-01T03:00:00+02:00')]:
                db.execute('INSERT INTO spectral_observations(signal_id,run_id,observed,center_hz) VALUES(?,?,?,1090000000)',(signal,run,observed))
            db.commit()
        self.view=DiscoveryView(self.root,started='2026-01-01T00:45:00Z')

    def test_counts_categories_and_chronological_feed(self):
        result=self.view.snapshot({})
        self.assertEqual(result['totals'],dict(detections=3,unique_signals=2,verified_protocols=1))
        categories={c['category']:c for c in result['categories']}
        self.assertEqual(categories['Unknown / Other']['detections'],2)
        self.assertEqual(categories['Aircraft']['detections'],1)
        self.assertEqual(result['recent'][1]['signal_id'],'air')
        self.assertIsNone(result['recent'][-1]['observed_utc'])
        self.assertEqual(result['recent'][0]['identifiers'],[])
        self.assertEqual(result['database']['schema_version'],11)

    def test_utc_half_open_window_and_session_comparison(self):
        result=self.view.snapshot({'since':['2026-01-01T02:00:00+02:00'],'until':['2026-01-01T01:00:00Z']})
        self.assertEqual(result['totals']['detections'],1)
        self.assertEqual(self.view.snapshot({'session':['live']})['totals']['detections'],0)
        with closing(open_database(self.root)) as db:
            db.execute("INSERT INTO spectral_observations(signal_id,run_id,observed) VALUES('unknown','second','2026-01-01 01:00:00')")
            db.commit()
        self.assertEqual(self.view.snapshot({'session':['live']})['totals']['detections'],1)
        result=self.view.snapshot({'session':['first'],'compare':['second'],'category':['Aircraft']})
        self.assertEqual(result['totals']['detections'],2)
        self.assertEqual(len(result['recent']),1)
        self.assertEqual(result['comparison']['delta']['detections'],0)

    def test_bad_scopes_and_bound_feed(self):
        for query in ({'session':["' OR 1=1--"]},{'since':['2026-01-01']},{'limit':['201']},{'limit':['1','2']},{'extra':['1']},{'since':['2026-01-02Z'],'until':['2026-01-01Z']}):
            with self.subTest(query=query),self.assertRaises(ValueError):self.view.snapshot(query)
        self.assertEqual(len(self.view.snapshot({'limit':['1']})['recent']),1)

    def test_unavailable_cpu_is_not_zero_and_delta_is_measured(self):
        monitor=HealthMonitor(self.root,4096)
        with patch.object(monitor,'_host',side_effect=[((20,100),1000,250),((30,200),1000,300)]),patch('rfatlas.discovery.time.monotonic',side_effect=[10,14]):
            first=monitor.sample();second=monitor.sample()
        self.assertIsNone(first['cpu_percent'])
        self.assertEqual(second['cpu_percent'],90)
        self.assertEqual(second['ram_percent'],30)
        self.assertEqual(second['recording_bytes'],0)
        self.assertEqual(second['recording_limit_bytes'],4096)
