import math
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from rfatlas.fcc import LocalFCCProvider, ingest_assignments


def assignment(**values):
    row = dict(frequency_hz=145000000, service='TEST', licensee='Synthetic licensee',
               callsign='TEST-ONLY', status='A', latitude=0, longitude=0, location='Synthetic location')
    row.update(values)
    return row


class FCCContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fcc.sqlite'
        self.provider = LocalFCCProvider(self.path)

    def ingest(self, rows, **options):
        return ingest_assignments(self.path, rows, source='synthetic-test', updated='2026-10-09', **options)

    def test_missing_is_optional_and_creates_nothing(self):
        self.assertFalse(self.provider.lookup(145000000)['available'])
        self.assertFalse(self.path.exists())

    def test_exact_tolerance_source_and_location(self):
        self.ingest([assignment(), assignment(frequency_hz=145000100)])
        exact = self.provider.lookup(145000000)
        self.assertEqual(len(exact['matches']), 1)
        self.assertEqual(exact['matches'][0]['source'], 'synthetic-test')
        self.assertEqual(exact['matches'][0]['updated'], '2026-10-09')
        self.assertNotIn('latitude', exact['matches'][0])
        self.assertNotIn('location', exact['matches'][0])
        self.assertIn('not proof of transmitter identity', exact['interpretation'])
        self.assertEqual(len(self.provider.lookup(145000050, 50)['matches']), 2)
        self.assertEqual(self.provider.lookup(145000000, include_location=True)['matches'][0]['latitude'], 0)
        self.assertEqual(self.provider.lookup(146000000)['matches'], [])

    def test_bound_and_order(self):
        self.ingest(assignment(frequency_hz=145000000+i) for i in range(150))
        result = self.provider.lookup(145000050, 200, limit=100)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['matches']), 100)
        self.assertEqual(result['matches'][0]['frequency_hz'], 145000050)

    def test_atomic_source_replacement_and_failed_import(self):
        self.ingest([assignment()])
        with self.assertRaises(ValueError):
            self.ingest([assignment(frequency_hz=146000000), assignment(latitude=math.nan)])
        self.assertEqual(len(self.provider.lookup(145000000)['matches']), 1)
        self.assertEqual(self.provider.lookup(146000000)['matches'], [])
        self.ingest([assignment(frequency_hz=146000000)])
        self.assertEqual(self.provider.lookup(145000000)['matches'], [])
        self.assertEqual(len(self.provider.lookup(146000000)['matches']), 1)
        with self.assertRaises(ValueError):
            self.ingest([assignment(), assignment()], max_rows=1)
        self.assertEqual(len(self.provider.lookup(146000000)['matches']), 1)

    def test_reject_bad_input(self):
        for options in ({'frequency_hz': True}, {'frequency_hz': math.inf}, {'latitude': 91},
                        {'longitude': None}, {'licensee': ''}, {'status': 'A\n'}, {'unexpected': 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.ingest([assignment(**options)])
        for options in ({'limit': True}, {'limit': 101}, {'tolerance_hz': -1},
                        {'tolerance_hz': 1000001}, {'include_location': 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.provider.lookup(145000000, **options)
        for source, updated in (('', '2026-10-09'), ('test', 'not-a-date')):
            with self.assertRaises(ValueError):
                ingest_assignments(self.path, [], source=source, updated=updated)

    def test_refuse_operational_or_future_database(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE signals(signal_id TEXT)')
        with self.assertRaises(ValueError):
            self.ingest([assignment()])
        self.assertFalse(self.provider.lookup(145000000)['available'])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            self.ingest([assignment()])

    def test_multiple_sources_preserve_status_and_no_payload_interpretation(self):
        self.ingest([assignment(status='E')])
        ingest_assignments(self.path, [assignment(status='C')], source='other-synthetic', updated='2026-10-09T01:00:00Z')
        rows = self.provider.lookup(145000000)['matches']
        self.assertEqual({row['status'] for row in rows}, {'E', 'C'})
        self.assertTrue(all('protocol' not in row for row in rows))


if __name__ == '__main__':
    unittest.main()
