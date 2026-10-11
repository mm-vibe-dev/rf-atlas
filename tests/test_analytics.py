import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from rfatlas.analytics import (AnalyticsUnavailable, snapshot_catalog, build_analytics,
                               query_analytics, export_parquet)
from rfatlas.storage import open_database


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = open_database(self.root)
        self.source = self.root / 'data' / 'rf_atlas.sqlite'
        self.snapshot = self.root / 'snapshot.sqlite'
        self.db.execute("INSERT INTO signals(signal_id,center_frequency_hz,occupied_bandwidth_hz,modulation,protocol) VALUES('synthetic',100000000,12000,'UNKNOWN','UNKNOWN')")
        for key, lo, hi in [('a',100,200),('b',150,250),('c',250,300)]:
            self.db.execute('INSERT INTO inventory_regions(region_id,start_hz,stop_hz,enabled) VALUES(?,?,?,1)', (key,lo,hi))
        for key, when, valid, count in [('a',1000,1,0),('b',1100,1,2),('a',1200,0,0),('c',1500,1,1)]:
            self.db.execute('INSERT INTO inventory_measurements(region_id,observed,valid,signal_count) VALUES(?,?,?,?)',(key,when,valid,count))
        self.db.execute("INSERT INTO spectral_observations(signal_id,observed,peak_snr_db,noise_db,gain_db,gain_mode,fft_resolution_hz) VALUES('synthetic','1970-01-01T00:18:20Z',20,-60,15.7,'manual',1000)")
        self.db.execute("INSERT INTO identification_runs(run_key,signal_id,created,result) VALUES('test','synthetic','1970-01-01T00:18:20Z',?)", (json.dumps({'protocol':'AX.25','protocol_verified':True,'attempts':[{},{}]}),))
        self.db.execute("INSERT INTO investigation_jobs(job_id,signal_id,created,status) VALUES('test','synthetic',1100,'complete')")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_snapshot_consistency_wal_and_refuse_overwrite(self):
        metadata = snapshot_catalog(self.source, self.snapshot)
        self.assertEqual(len(metadata['snapshot_sha256']),64)
        self.db.execute("INSERT INTO signals(signal_id) VALUES('later')");self.db.commit()
        check = sqlite3.connect(self.snapshot)
        try:
            self.assertEqual(check.execute('SELECT count(*) FROM signals').fetchone()[0],1)
        finally:check.close()
        with self.assertRaises(ValueError):snapshot_catalog(self.source,self.snapshot)
        with self.assertRaises(ValueError):snapshot_catalog(self.source,self.source)

    def test_snapshot_cancel_removes_partial_and_missing_dependency_is_optional(self):
        with self.assertRaises(TimeoutError):snapshot_catalog(self.source,self.snapshot,cancel=lambda:True)
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(list(self.root.glob('.snapshot-*')))
        with patch('rfatlas.analytics.importlib.import_module',side_effect=ImportError):
            with self.assertRaises(AnalyticsUnavailable):build_analytics(self.source,self.root/'missing.duckdb')
        self.assertFalse((self.root/'missing.duckdb').exists())

    def test_online_snapshot_completes_with_frequent_wal_writer(self):
        self.db.execute('CREATE TABLE bulk (value BLOB)')
        self.db.executemany('INSERT INTO bulk VALUES(?)',[(bytes(100000),)]*40)
        self.db.commit()
        stop=threading.Event();started=threading.Event();errors=[]
        def writer():
            connection=sqlite3.connect(self.source,timeout=1)
            try:
                while not stop.is_set():
                    connection.execute('INSERT INTO bulk VALUES(?)',(bytes(100),));connection.commit()
                    started.set();stop.wait(.001)
            except Exception as error:errors.append(error)
            finally:connection.close()
        thread=threading.Thread(target=writer);thread.start()
        try:
            self.assertTrue(started.wait(2))
            snapshot_catalog(self.source,self.snapshot,timeout_s=5)
        finally:stop.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        check=sqlite3.connect(self.snapshot)
        try:self.assertEqual(check.execute('PRAGMA quick_check').fetchone()[0],'ok')
        finally:check.close()

    @unittest.skipUnless(importlib.util.find_spec('duckdb'),'optional DuckDB absent')
    def test_detached_analytics_time_coverage_verification_and_parquet(self):
        snapshot_catalog(self.source,self.snapshot)
        output = self.root/'analytics.duckdb'
        manifest = build_analytics(self.snapshot,output)
        self.assertEqual(manifest['rows']['visits'],4)
        self.db.execute('DELETE FROM inventory_measurements');self.db.commit()
        result = query_analytics(output,1000,1300,bucket_s=100,limit=10)
        self.assertEqual(result['coverage']['valid_observed_hz'],150)
        self.assertEqual([r['detection_visit_fraction'] for r in result['occupancy']],[0.,1.,None])
        self.assertEqual(sum(r['attempted'] for r in result['occupancy']),3)
        self.assertEqual(result['classification'][0]['protocol'],'UNKNOWN')
        self.assertEqual(result['decoder_verification'][0]['verified'],1)
        self.assertEqual(result['decoder_verification'][0]['decoder_attempts'],2)
        self.assertEqual(result['noise_trends'][0]['gain_db'],15.7)
        empty = query_analytics(output,2000,2100)
        self.assertEqual(empty['occupancy'],[])
        self.assertEqual(empty['coverage']['valid_observed_hz'],0)
        downsampled = query_analytics(output,1000,2000,bucket_s=1,limit=2)
        self.assertLessEqual(len(downsampled['occupancy']),2)
        self.assertEqual(sum(r['attempted'] for r in downsampled['occupancy']),4)
        destination=self.root/'parquet'
        export_parquet(output,destination)
        import duckdb
        connection=duckdb.connect()
        try:
            self.assertEqual(connection.execute('SELECT count(*) FROM read_parquet(?)',[str(destination/'visits.parquet')]).fetchone()[0],4)
        finally:connection.close()
        with self.assertRaises(ValueError):export_parquet(output,destination)
        with self.assertRaises(ValueError):build_analytics(self.snapshot,output)

    @unittest.skipUnless(importlib.util.find_spec('duckdb'),'optional DuckDB absent')
    def test_bounds_and_cancelled_build_no_partial(self):
        with self.assertRaisesRegex(ValueError,'detached'):
            build_analytics(self.source,self.root/'live.duckdb')
        for args in [(10,0), (float('nan'),20)]:
            with self.assertRaises(ValueError):query_analytics('absent',*args)
        with self.assertRaises(ValueError):query_analytics('absent',0,20,limit=100000)
        with self.assertRaises(ValueError):query_analytics('absent',0,20,timeout_s=0)
        output=self.root/'cancel.duckdb'
        with self.assertRaises(TimeoutError):build_analytics(self.source,output,cancel=lambda:True)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('.analytics-*')))


if __name__ == '__main__':unittest.main()
