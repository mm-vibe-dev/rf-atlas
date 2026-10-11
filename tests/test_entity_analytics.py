"""Device/station analytics retain aggregate discovery, never transmitter IDs."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from rfatlas.analytics import snapshot_catalog, build_analytics, query_analytics, export_parquet
from rfatlas.entities import ensure_evidence_schema
from rfatlas.storage import open_database


@unittest.skipUnless(importlib.util.find_spec('duckdb'), 'optional DuckDB absent')
class EntityAnalyticsTests(unittest.TestCase):
    def test_discovery_activity_time_bounds_and_identifier_exclusion(self):
        import duckdb
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = open_database(root); ensure_evidence_schema(db)
            try:
                db.execute("INSERT INTO signals(signal_id) VALUES('synthetic')")
                db.execute("INSERT INTO identification_runs(run_key,signal_id) VALUES('synthetic','synthetic')")
                for key, kind, protocol, first in [('a','device','SyntheticSensor','1970-01-01T00:18:20Z'),('b','station','SyntheticStation','1970-01-01T00:19:10Z'),('old','device','SyntheticSensor','1970-01-01T00:01:00Z')]:
                    db.execute('INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?)', (key,kind,protocol,'Synthetic model','PRIVATE TEST ID '+key,'synthetic-category',first,'1970-01-01T00:20:00Z','{"private":"PRIVATE TEST FIELDS"}'))
                    db.execute('INSERT INTO recovered_frames VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (key,'synthetic','synthetic',first,100000000,protocol,'synthetic','1','VERIFIED',2,'FFFF',0,1,1,'testsync','testcrc','{}'))
                    db.execute('INSERT INTO entity_observations VALUES(?,?,?,?,?,?)', (key,key,'synthetic','1970-01-01T00:20:00Z',100000000,'{}'))
                db.commit()
                snapshot_catalog(root/'data'/'rf_atlas.sqlite',root/'snapshot.sqlite')
            finally: db.close()
            manifest = build_analytics(root/'snapshot.sqlite',root/'analytics.duckdb')
            self.assertEqual(manifest['rows']['entity_discoveries'],3)
            result = query_analytics(root/'analytics.duckdb',1000,1300,bucket_s=100)
            self.assertEqual(sum(r['new_identifiers'] for r in result['entity_discovery_statistics']),2)
            self.assertEqual(sum(r['verified_identifier_observations'] for r in result['entity_observation_statistics']),3)
            self.assertEqual(sum(r['new_identifiers'] for r in result['entity_discovery_history']),2)
            self.assertEqual(query_analytics(root/'analytics.duckdb',1300,1400)['entity_discovery_statistics'],[])
            export_parquet(root/'analytics.duckdb',root/'parquet')
            connection = duckdb.connect()
            try:
                for table in ('entity_discoveries','entity_activity'):
                    path = str(root/'parquet'/f'{table}.parquet')
                    columns = connection.execute('DESCRIBE SELECT * FROM read_parquet(?)',[path]).fetchall()
                    self.assertFalse({'entity_id','identifier','latest_fields','fields','raw_hex'} & {r[0] for r in columns})
                    values = connection.execute('SELECT * FROM read_parquet(?)',[path]).fetchall()
                    self.assertNotIn('PRIVATE TEST',str(values))
            finally: connection.close()

    def test_pre_entity_snapshot_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = open_database(root)
            try:
                # Simulate a pre-1.0 snapshot even after the operational schema migrates.
                db.execute('DROP TABLE IF EXISTS entity_observations')
                db.execute('DROP TABLE IF EXISTS recovered_frames')
                db.execute('DROP TABLE IF EXISTS entities')
                db.commit()
                snapshot_catalog(root/'data'/'rf_atlas.sqlite',root/'snapshot.sqlite')
            finally: db.close()
            manifest = build_analytics(root/'snapshot.sqlite',root/'analytics.duckdb')
            self.assertEqual(manifest['rows']['entity_discoveries'],0)
            result = query_analytics(root/'analytics.duckdb',1000,1300)
            self.assertEqual(result['entity_discovery_statistics'],[])
            self.assertEqual(result['entity_observation_statistics'],[])


if __name__ == '__main__': unittest.main()
