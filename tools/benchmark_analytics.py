"""Synthetic local analytics benchmark; never reads an operational catalog."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rfatlas.analytics import snapshot_catalog, build_analytics, query_analytics, export_parquet
from rfatlas.storage import open_database


def peak_rss_bytes():
    if os.name != 'nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    class Counters(ctypes.Structure):
        _fields_ = [('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)] + [
            (name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize',
             'QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage',
             'QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    value=Counters();value.cb=ctypes.sizeof(value)
    ctypes.windll.kernel32.GetCurrentProcess.restype=ctypes.c_void_p
    reader=ctypes.windll.psapi.GetProcessMemoryInfo
    reader.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters),ctypes.c_ulong]
    if not reader(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(value),value.cb):
        raise OSError('Cannot measure process peak RSS')
    return value.PeakWorkingSetSize


def benchmark(size):
    with tempfile.TemporaryDirectory(prefix='rfatlas-synthetic-analytics-') as directory:
        root=Path(directory);db=open_database(root)
        db.execute('CREATE TABLE benchmark_writes (value INTEGER)')
        db.execute("INSERT INTO signals(signal_id,center_frequency_hz,occupied_bandwidth_hz) VALUES('synthetic',100000000,12000)")
        db.execute("INSERT INTO inventory_regions(region_id,start_hz,stop_hz,enabled) VALUES('synthetic',100000000,102000000,1)")
        for offset in range(0,size,1000):
            rows=[('synthetic',float(1000+i),int(i%10!=0),int(i%3==0)) for i in range(offset,min(size,offset+1000))]
            db.executemany('INSERT INTO inventory_measurements(region_id,observed,valid,signal_count) VALUES(?,?,?,?)',rows)
            db.executemany("INSERT INTO spectral_observations(signal_id,observed,peak_snr_db,noise_db,gain_db,gain_mode,fft_resolution_hz) VALUES('synthetic',datetime(?,'unixepoch'),20,-60,15.7,'manual',1000)",[(r[1],) for r in rows])
        db.commit()
        begin=time.perf_counter()
        expected=db.execute('SELECT count(*),sum(CASE WHEN valid=1 THEN 1 ELSE 0 END) FROM inventory_measurements WHERE observed>=? AND observed<?',(1000,1000+size)).fetchone()
        sqlite_seconds=time.perf_counter()-begin
        db.close()
        stop=threading.Event();latencies=[];errors=[]
        def writer():
            connection=sqlite3.connect(root/'data'/'rf_atlas.sqlite',timeout=1)
            try:
                while not stop.is_set():
                    begin=time.perf_counter()
                    connection.execute('INSERT INTO benchmark_writes VALUES(1)');connection.commit()
                    if len(latencies)<10000:latencies.append(time.perf_counter()-begin)
                    stop.wait(.005)
            except Exception as error:errors.append(type(error).__name__)
            finally:connection.close()
        thread=threading.Thread(target=writer);thread.start()
        try:
            measurements={}
            def timed(name,callback):
                begin=time.perf_counter();result=callback();measurements[name]=time.perf_counter()-begin;return result
            snapshot=root/'snapshot.sqlite';analytics=root/'analytics.duckdb'
            timed('snapshot_s',lambda:snapshot_catalog(root/'data'/'rf_atlas.sqlite',snapshot))
            manifest=timed('build_s',lambda:build_analytics(snapshot,analytics,timeout_s=600))
            summary=timed('query_s',lambda:query_analytics(analytics,1000,1000+size,bucket_s=60))
            timed('parquet_s',lambda:export_parquet(analytics,root/'parquet'))
        finally:
            stop.set();thread.join(5)
        if errors or thread.is_alive():raise RuntimeError('Synthetic concurrent writer failed: '+repr(errors))
        assert sum(row['attempted'] for row in summary['occupancy'])==expected[0]
        assert sum(row['valid'] for row in summary['occupancy'])==expected[1]
        return {'synthetic_visits':size,'synthetic_observations':size,**measurements,
            'sqlite_simple_count_s':sqlite_seconds,'snapshot_bytes':snapshot.stat().st_size,
            'duckdb_bytes':analytics.stat().st_size,'parquet_bytes':sum(p.stat().st_size for p in (root/'parquet').iterdir()),
            'process_lifetime_peak_rss_bytes':peak_rss_bytes(),'writer_commits':len(latencies),
            'writer_mean_commit_s':sum(latencies)/len(latencies) if latencies else None,
            'writer_max_commit_s':max(latencies,default=None),'writer_errors':errors,
            'duckdb_version':manifest['duckdb_version'], 'aggregate_counts_equal':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes',nargs='+',type=int,default=[1000,10000,100000])
    args=parser.parse_args()
    if any(not 1<=size<=1000000 for size in args.sizes):parser.error('sizes must be 1..1000000')
    print(json.dumps({'scope':'Synthetic, detached local analytics; simple SQLite count is not an equivalent full dashboard query.',
                      'results':[benchmark(size) for size in args.sizes]},indent=2))
