"""Independent signal and subprocess fixtures, with no receiver access."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import numpy as np
from rfatlas.deep_investigation import measure,summarize,recipes
from rfatlas.adaptive_capture import acquire
from rfatlas.config import Config
from rfatlas.features import extract_features
from rfatlas.signal_lab import view
from rfatlas.storage import open_database


class DeepTests(unittest.TestCase):
    def test_mode_s_verified_by_frame_outside_frequency_presets(self):
        from tests.test_protocol_native import make_frame,ppm_iq
        from rfatlas.identification import DecoderRegistry,identify
        from rfatlas.decoders import DecoderInput
        from rfatlas.protocols import ModeSDecoder
        registry=DecoderRegistry();registry.register(ModeSDecoder())
        result=identify(DecoderInput(ppm_iq(make_frame(51)),2400000,471234567),{},registry)
        self.assertTrue(result['protocol_verified']);self.assertEqual(result['protocol'],'ADS-B')
        self.assertIn('outside typical band context; remains eligible',result['candidates'][0]['hints'])

    def test_recipe_fallback_and_feedback_require_frame_integrity(self):
        from rfatlas.deep_investigation import plan
        from rfatlas.investigation_feedback import history
        self.assertEqual(plan({'modulation':'AM'},'ais')['selected_recipe'],'generic')
        self.assertEqual(plan({'modulation':'4FSK'},'land-mobile')['decoder_protocol_preference'],['DMR'])
        with tempfile.TemporaryDirectory() as directory:
            db=open_database(Path(directory));db.execute("INSERT INTO signals(signal_id) VALUES('fixture')")
            payload=dict(attempts=[dict(decoder=dict(name='test'),protocol_verified=True,evidence=dict(frames_with_sync_and_valid_checksum=0))])
            db.execute('INSERT INTO identification_runs VALUES(?,?,?,?,?,?,?)',('run','fixture','2026-01-01',None,'fixture','fixture',json.dumps(payload)))
            self.assertEqual(history(db)['decoders']['test']['verified_runs'],0);db.close()

    def test_rectangular_fsk_timing_measures_without_protocol_proof(self):
        rate=48000;rng=np.random.default_rng(170211)
        symbols=np.repeat(rng.choice([-1,1],1000),20)
        x=.4*np.exp(2j*np.pi*np.cumsum(900+1800*symbols)/rate)
        a=dict(signal_detected=True,modulation='2FSK',features=extract_features(x,rate))
        m=measure(x,rate,a,471234567)
        self.assertAlmostEqual(m['instantaneous_frequency']['median_hz'],2700,delta=3600)
        self.assertTrue(m['symbol_rate_candidates'])
        self.assertAlmostEqual(m['symbol_rate_candidates'][0]['approximate_rate_baud'],2400,delta=130)
        result=dict(analysis=a,protocol_verified=False,verified_protocols=[],attempts=[],candidates=[])
        report=summarize(result,m)
        self.assertFalse(report['stages']['valid_frame']);self.assertFalse(report['stages']['protocol_sync'])
        self.assertEqual(report['triage'],'NO_COMPATIBLE_DECODER')

    def test_noise_and_tone_have_no_timing_or_encryption_claim(self):
        rng=np.random.default_rng(170212)
        for x in (rng.normal(size=16384)+1j*rng.normal(size=16384),np.ones(16384,complex)):
            m=measure(x,48000,dict(features=extract_features(x,48000)),123456789)
            self.assertEqual(m['symbol_rate_candidates'],[])
            self.assertNotIn('encryption',m)
        self.assertEqual({r['id'] for r in recipes()},{'generic','analog','land-mobile','packet','ais','sensor','broadcast'})

    def test_memory_acquisition_is_finite_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Config(Path(directory));backend=self.backend()
            original=subprocess.Popen
            def launch(command,**kwargs):
                self.assertEqual(command[-1],'-');self.assertIn('-n',command)
                count=int(command[command.index('-n')+1])
                return original([sys.executable,'-c',f'import sys;sys.stdout.buffer.write(bytes([145,125])*{count});sys.stdout.buffer.flush()'],**kwargs)
            with patch('rfatlas.adaptive_capture.subprocess.Popen',side_effect=launch):
                iq,meta=acquire(config,backend,98765432,window_s=.1)
            self.assertEqual(len(iq),25000);self.assertFalse(meta['acquisition']['raw_persisted'])
            self.assertEqual(meta['acquisition']['stored_recording_duration_s'],0)
            self.assertEqual(list(Path(directory).rglob('*.cu8')),[]);self.assertFalse(backend.owned)

    def test_acquisition_cancel_and_incomplete_release_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Config(Path(directory));backend=self.backend();original=subprocess.Popen
            def launch(command,**kwargs):
                return original([sys.executable,'-c','import sys,time;sys.stdout.buffer.write(bytes([140,130])*4096);sys.stdout.buffer.flush();time.sleep(4)'],**kwargs)
            cancel=threading.Event();timer=threading.Timer(.3,cancel.set);timer.start()
            with patch('rfatlas.adaptive_capture.subprocess.Popen',side_effect=launch):
                with self.assertRaises(InterruptedError):acquire(config,backend,98765432,cancel_event=cancel)
            timer.join();self.assertFalse(backend.owned)
            def short(command,**kwargs):return original([sys.executable,'-c','print("x")'],**kwargs)
            with patch('rfatlas.adaptive_capture.subprocess.Popen',side_effect=short):
                with self.assertRaises(RuntimeError):acquire(config,backend,98765432,window_s=.1)
            self.assertFalse(backend.owned)

    def test_burst_watch_retains_the_pretrigger_and_has_a_finite_posttrigger(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Config(Path(directory));backend=self.backend();original=subprocess.Popen
            def launch(command,**kwargs):
                count=int(command[command.index('-n')+1])
                # A quiet baseline, then a real energy step. No RF access.
                source=f'import sys;sys.stdout.buffer.write(bytes([128,128])*100000+bytes([180,128])*({count}-100000));sys.stdout.buffer.flush()'
                return original([sys.executable,'-c',source],**kwargs)
            with patch('rfatlas.adaptive_capture.subprocess.Popen',side_effect=launch):
                iq,meta=acquire(config,backend,98765432,strategy='burst-watch')
            a=meta['acquisition'];self.assertIsNotNone(a['burst_trigger_sample'])
            self.assertLess(a['acquisition_duration_s'],20)
            self.assertLess(a['analysis_start_sample'],a['burst_trigger_sample'])
            self.assertLess(float(np.mean(abs(iq[:10000]))),float(np.mean(abs(iq[-10000:])))/10)
            self.assertFalse(backend.owned)

    def test_targeted_capture_ring_reports_acquisition_separately_from_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Config(Path(directory));backend=self.backend();original=subprocess.Popen
            def launch(command,**kwargs):
                count=int(command[command.index('-n')+1])
                return original([sys.executable,'-c',f'import sys;sys.stdout.buffer.write(bytes([145,125])*{count});sys.stdout.buffer.flush()'],**kwargs)
            with patch('rfatlas.adaptive_capture.subprocess.Popen',side_effect=launch):
                iq,meta=acquire(config,backend,98765432,strategy='targeted')
            a=meta['acquisition'];self.assertEqual(a['acquired_samples'],5_000_000)
            self.assertEqual(len(iq),4_000_000);self.assertEqual(a['analysis_start_sample'],1_000_000)
            self.assertEqual(a['analysis_duration_s'],16);self.assertEqual(a['stored_recording_duration_s'],0)
            self.assertFalse(backend.owned)

    @staticmethod
    def backend():
        class Backend:
            selector='fixture';owned=False
            def tool(self,name):return Path(sys.executable)
            @contextmanager
            def lock(self):
                self.owned=True
                try:yield
                finally:self.owned=False
        return Backend()

    def test_signal_lab_actual_recording_and_path_restriction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=open_database(root)
            folder=root/'data/captures';folder.mkdir();path=folder/'fixture.cu8'
            t=np.arange(16384)/250000;x=.3*np.exp(2j*np.pi*12500*t)
            raw=np.column_stack((np.rint(x.real*127.5+127.5),np.rint(x.imag*127.5+127.5))).astype('uint8').tobytes();path.write_bytes(raw)
            meta=dict(format='cu8',sample_rate=250000,center_hz=123456789,sample_count=len(x),sha256=hashlib.sha256(raw).hexdigest())
            path.with_suffix('.json').write_text(json.dumps(meta))
            db.execute("INSERT INTO signals(signal_id) VALUES('fixture')")
            db.execute("INSERT INTO antenna_profiles VALUES('other','fixture')")
            db.execute('INSERT INTO captures VALUES(?,?,?,?,?)',('capture','fixture','other',str(path),json.dumps(meta)));db.commit()
            actual=view(root,'fixture');self.assertTrue(actual['available'])
            peak=np.argmax(actual['spectrum']['relative_psd_db'])
            self.assertAlmostEqual(actual['spectrum']['offset_hz'][peak],12500,delta=500)
            self.assertEqual(len(actual['time_s']),256);self.assertLessEqual(len(actual['waterfall']['time_s']),128)
            db.execute('UPDATE captures SET path=?',(str(root/'outside.cu8'),));db.commit()
            with self.assertRaises(ValueError):view(root,'fixture')
            self.assertFalse(view(root,'missing')['available']);db.close()
