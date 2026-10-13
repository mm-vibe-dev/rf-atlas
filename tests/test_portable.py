import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from rfatlas.config import Config,load_config,resolve_path
from rfatlas.platforms import DeviceLock,run_process
from rfatlas.hardware import RTLBackend,gain_value
from rfatlas.storage import open_database
from rfatlas.features import classify
from rfatlas.iq import checksum,record,replay,read_recording
from rfatlas import legacy


def synth(kind,rate=250000,n=25000):
    rng=np.random.default_rng(22);t=np.arange(n)/rate
    if kind=='noise': return (rng.normal(size=n)+1j*rng.normal(size=n))*.02
    if kind=='CW': return .5*np.exp(2j*np.pi*4000*t)
    if kind=='AM': return (.5+.2*np.cos(2*np.pi*1000*t))*np.exp(2j*np.pi*4000*t)
    if kind=='OOK': return .5*(np.arange(n)//500%2)*np.exp(2j*np.pi*4000*t)
    levels=np.array([-2000,2000]) if kind=='2FSK' else np.array([-3000,-1000,1000,3000])
    frequencies=np.repeat(rng.choice(levels,size=(n+49)//50),50)[:n]
    return .5*np.exp(2j*np.pi*np.cumsum(frequencies)/rate)


class PortableTests(unittest.TestCase):
    def test_configuration_and_relative_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='rf atlas ') as folder:
            path=Path(folder)/'local.toml';path.write_text('[runtime]\nroot="./runtime dir"\n[hardware]\ndevice="0"\n[tools]\nrtl_sdr="./tools dir/receiver"\n')
            config=load_config(path)
            self.assertEqual(config.root,(Path(folder)/'runtime dir').resolve())
            self.assertEqual(config.tools['rtl_sdr'],str((Path(folder)/'tools dir/receiver').resolve()))
            self.assertEqual(resolve_path('relative',Path(folder)),(Path(folder)/'relative').resolve())

    def test_invalid_configuration(self):
        for text in ['[runtime]\nmax_capture_bytes=-1','[hardware]\nbias_tee=true','[tools]\ntransmitter="x"','[unexpected]\na=1']:
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'bad.toml';path.write_text(text)
                with self.assertRaises(ValueError): load_config(path)

    def test_foreign_path_guard(self):
        if os.name!='nt':
            with self.assertRaises(ValueError): resolve_path('C:/foreign/path',Path.cwd())
        else:
            self.assertTrue(resolve_path('C:/example/path',Path.cwd()).is_absolute())

    def test_missing_tool_and_invalid_gain(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=RTLBackend(Config(Path(folder),tools={'rtl_sdr':str(Path(folder)/'missing-tool')}))
            with self.assertRaisesRegex(RuntimeError,'unavailable'): backend.tool('rtl_sdr')
        with self.assertRaises(argparse.ArgumentTypeError): gain_value('inf')

    def test_lock_contention_and_release(self):
        with tempfile.TemporaryDirectory() as folder:
            with DeviceLock(folder):
                with self.assertRaises(RuntimeError):
                    with DeviceLock(folder): pass
            with DeviceLock(folder): pass

    def test_process_arguments_errors_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'command.json'
            stdout,_=run_process([sys.executable,'-c','import sys;print(sys.argv[1])','argument with spaces'],10,path)
            self.assertEqual(stdout.strip(),'argument with spaces')
            self.assertEqual(json.loads(path.read_text())['return_code'],0)
            with self.assertRaisesRegex(RuntimeError,'failed'):
                run_process([sys.executable,'-c','import sys;sys.stderr.write("device disconnected");sys.exit(2)'],10,path)
            self.assertIn('device disconnected',json.loads(path.read_text())['stderr'])
            with self.assertRaises(subprocess.TimeoutExpired): run_process([sys.executable,'-c','import time;time.sleep(10)'],.2,path)
            self.assertIsNotNone(json.loads(path.read_text())['return_code'])

    def test_missing_runtime_graceful(self):
        with self.assertRaisesRegex(RuntimeError,'missing|failed'):
            run_process(['nonexistent-rf-atlas-executable'],10)

    def test_driver_setup_warning_rejects_success_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=RTLBackend(Config(Path(folder)))
            with patch('rfatlas.hardware.run_process',return_value=('', 'WARNING: Failed to enable manual gain.')):
                with self.assertRaisesRegex(RuntimeError,'setup failed'): backend.run(['rtl_power'],10)

    def test_database_existing_schema_additive(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data').mkdir()
            db=open_database(root)
            db.execute("INSERT INTO antenna_profiles VALUES('synthetic','synthetic')");db.commit();db.close()
            db=open_database(root)
            self.assertEqual(db.execute('SELECT name FROM antenna_profiles').fetchone()[0],'synthetic')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');db.close()

    def test_synthetic_classifier_and_unknown_protection(self):
        for kind in ['CW','AM','OOK','2FSK','4FSK']:
            with self.subTest(kind=kind):
                result=classify(synth(kind),250000)
                self.assertEqual(result['modulation'],kind)
                self.assertEqual(result['protocol'],'UNKNOWN');self.assertEqual(result['protection'],'UNKNOWN')
                self.assertEqual(result['basis'],'HEURISTIC');self.assertTrue(result['evidence'])
        self.assertEqual(classify(synth('noise'),250000)['modulation'],'UNKNOWN')

    def test_corrupt_iq_and_oversize_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.cu8';path.write_bytes(b'\x80\x80'*5000)
            meta={'format':'cu8','sample_rate':250000,'center_hz':100000000,'sample_count':5000,'sha256':checksum(path)}
            header=path.with_suffix('.json');header.write_text(json.dumps(meta))
            with self.assertRaises(ValueError): read_recording(path,max_bytes=8192)
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError): read_recording(path)
            path.write_bytes(b'\x7f\x80'*5000)
            with self.assertRaisesRegex(ValueError,'checksum'): read_recording(path)

    def test_record_replay_with_synthetic_receiver(self):
        # Only acquisition is substituted; real IQ encoding/DSP/storage run.
        class Receiver:
            selector='0'
            def tool(self,name): return name
            def lock(self): return DeviceLock(root/'locks')
            def run(self,args,timeout,diagnostic_path=None):
                n=int(args[args.index('-n')+1]);iq=synth('4FSK',n=n)
                raw=np.empty(n*2,np.uint8);raw[::2]=np.clip(iq.real*127.5+127.5,0,255).astype(np.uint8);raw[1::2]=np.clip(iq.imag*127.5+127.5,0,255).astype(np.uint8)
                Path(args[-1]).write_bytes(raw)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data').mkdir();db=open_database(root)
            config=Config(root)
            try:
                path,meta=record(config,Receiver(),100000000,250000,.1,'15.7',offset=0,db=db)
                result,_=replay(path)
                self.assertEqual(result['modulation'],'4FSK')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM captures').fetchone()[0],1)
                self.assertEqual(meta['gain_db'],15.7)
                with self.assertRaisesRegex(ValueError,'manual'): record(config,Receiver(),100000000,250000,.1,'auto')
                with self.assertRaisesRegex(ValueError,'budget'): record(Config(root,max_capture_bytes=8192),Receiver(),100000000,250000,1,'15.7')
            finally: db.close()

    def test_disconnected_capture_releases_lock(self):
        class Receiver:
            selector='0'
            def tool(self,name):return name
            def lock(self):return DeviceLock(root/'locks')
            def run(self,*args):raise RuntimeError('USB disconnected')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with self.assertRaisesRegex(RuntimeError,'USB'):record(Config(root),Receiver(),100000000,250000,.1,'15.7')
            with DeviceLock(root/'locks'): pass

    def test_checkpoint_resume_preserves_completed_regions(self):
        from rfatlas.survey import survey
        class Receiver:
            selector='0'
            fail=True
            calls=0
            ingest=staticmethod(legacy.ingest)
            def tool(self,name):return name
            def lock(self):return DeviceLock(root/'locks')
            def run(self,args,timeout,diagnostic_path=None):
                self.calls+=1
                if self.fail and self.calls==2:raise RuntimeError('Synthetic disconnect')
                low,high,step=map(int,args[args.index('-f')+1].split(':'))
                n=(high-low)//step
                power=np.full(n,-60.);power[n//2:n//2+3]=-40
                with Path(args[-1]).open('w',newline='') as file:
                    csv.writer(file).writerow(['2026-01-01','12:00:00',low,low+n*step,step,100,*power])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data/spectra').mkdir(parents=True)
            db=open_database(root);backend=Receiver()
            args=argparse.Namespace(start=100000000,stop=104000000,profile='balanced',integration=1,gain='15.7',antenna='synthetic',new=False)
            try:
                with self.assertRaisesRegex(RuntimeError,'disconnect'):survey(db,args,root,backend)
                run=db.execute('SELECT run_id FROM survey_runs').fetchone()[0]
                self.assertEqual(db.execute("SELECT COUNT(*) FROM scan_regions WHERE status='complete'").fetchone()[0],1)
                before=db.execute('SELECT COUNT(*) FROM spectral_observations').fetchone()[0]
                backend.fail=False;survey(db,args,root,backend)
                self.assertEqual(backend.calls,3)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM survey_runs').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT status FROM survey_runs WHERE run_id=?',(run,)).fetchone()[0],'complete')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM spectral_observations').fetchone()[0],before+1)
                observation=db.execute('SELECT fft_resolution_hz,integration_s,threshold_margin_db,measurement_settings FROM spectral_observations LIMIT 1').fetchone()
                self.assertEqual(observation[:3],(5000.,1.,6.))
                self.assertIn('threshold_min_db',json.loads(observation[3]))
            finally:db.close()


if __name__=='__main__': unittest.main()
