"""Synthetic bounded dashboard/read-write/decoder benchmark; never uses hardware."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rfatlas.storage import open_database
from rfatlas.explorer import explorer_view


def benchmark(size):
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        with closing(open_database(root)) as db,db:
            db.execute("INSERT INTO signals(signal_id,center_frequency_hz,protocol) VALUES('synthetic',100050000,'UNKNOWN')")
            db.executemany('INSERT INTO spectral_observations(signal_id,observed,center_hz,peak_snr_db,noise_db) VALUES(?,?,?,?,?)',
                (('synthetic','2026-01-01T00:00:00Z',100050000,12,-60) for _ in range(size)))
        def read(_):
            start=time.perf_counter();view=explorer_view(root,{})
            assert len(view['history'])<=500
            return (time.perf_counter()-start)*1000
        def write():
            with closing(sqlite3.connect(root/'data/rf_atlas.sqlite',timeout=2)) as db:
                started=time.perf_counter()
                for _ in range(20):
                    db.execute("INSERT INTO spectral_observations(signal_id,observed,center_hz) VALUES('synthetic','2026-01-02',100050000)");db.commit()
                return (time.perf_counter()-started)*1000
        tracemalloc.start()
        with ThreadPoolExecutor(max_workers=5) as pool:
            writing=pool.submit(write)
            latencies=list(pool.map(read,range(20)))
            writer_ms=writing.result()
        _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        return {'observations':size,'requests':20,'reader_mean_ms':statistics.mean(latencies),
                'reader_max_ms':max(latencies),'writer_20_commits_ms':writer_ms,
                'python_tracemalloc_peak_bytes':peak}


def retained_spectrum_benchmark():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        with closing(open_database(root)) as db,db:
            raw=root/'data'/'synthetic.csv'
            powers=','.join(str(-60+20*(i==2033)) for i in range(4096))
            raw.write_text(''.join(f'2026-01-01,00:00:{i:02d},100000000,100409600,100,100,'+powers+'\n' for i in range(32)))
            db.execute("INSERT INTO survey_runs(run_id,settings) VALUES('synthetic','{\"gain\":15.7,\"seconds\":1}')")
            db.execute('INSERT INTO scan_regions VALUES(?,?,?,?,?,?)',('synthetic',100000000,100409600,'complete','2026-01-01T00:00:31Z',str(raw)))
        latencies=[]
        for _ in range(4):
            start=time.perf_counter();view=explorer_view(root,{})
            latencies.append((time.perf_counter()-start)*1000)
            assert max(view['spectrum']['power_db'])==-40
        return {'raw_rows':32,'raw_bins_per_row':4096,'display_rows':len(view['waterfall']['rows']),
            'display_bins':len(view['spectrum']['power_db']),'cold_ms':latencies[0],
            'warm_mean_ms':statistics.mean(latencies[1:]),'narrow_peak_preserved':True}


if __name__=='__main__':
    import numpy as np
    from rfatlas.decoders import DecoderInput,run_decoder
    from rfatlas.protocols import ModeSDecoder
    rng=np.random.default_rng(700)
    recording=DecoderInput(rng.normal(size=65536)+1j*rng.normal(size=65536),2400000,1090000000)
    start=time.perf_counter()
    for _ in range(10):
        assert not run_decoder(ModeSDecoder(),recording)['protocol_verified']
    decoder_ms=(time.perf_counter()-start)*100
    print(json.dumps({'synthetic':True,'retained_spectrum':retained_spectrum_benchmark(),
        'dashboard':[benchmark(n) for n in (1000,10000,100000)],
        'native_decoder_noise_mean_ms':decoder_ms,
        'memory_basis':'Python traced allocations across concurrent requests, excludes native/RSS.',
        'survey_basis':'No new hardware survey; acquisition code unchanged. Existing bounded live validation remains documented.'},indent=2))
