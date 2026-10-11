"""Deterministic protocol evidence, recovery, caching and quality regression gates."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
from rfatlas.config import Config
from rfatlas.storage import open_database
from rfatlas.recognition import classify
from rfatlas.reanalysis import ReanalysisController
from rfatlas.identification import DecoderRegistry
from rfatlas.dmr import DMRDecoder
from rfatlas.pipeline import diagnose,signal_status
from rfatlas.similarity import vector,score
from rfatlas.synthetic import generate
from tests.test_discovery15 import dmr_wave


class RecognitionTests(unittest.TestCase):
    def test_fm_family_evidence_and_noise_rejection(self):
        for mode in ('NFM','WFM'):
            result=classify(generate(mode,snr_db=28,offset=5000),250000)
            self.assertEqual(result['signal_class'],'ANALOG');self.assertEqual(result['modulation'],mode)
            self.assertEqual(result['protocol'],'UNKNOWN');self.assertIn('prior_dsp',result)
        noise=classify(generate('NOISE'),250000)
        self.assertNotIn(noise['signal_class'],('ANALOG','DIGITAL'));self.assertEqual(noise['modulation'],'UNKNOWN')

    def test_cu8_quality_cannot_be_promoted(self):
        x=generate('NFM',snr_db=28,gain=.001)
        result=classify(x,250000,dict(format='cu8'))
        self.assertEqual(result['modulation'],'UNKNOWN')

    def test_optional_model_bad_input_falls_back_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.json';path.write_text('{"schema":1,"algorithm":"execute-python"}')
            result=classify(generate('NFM'),250000,backend='rfai',model_path=path)
            self.assertEqual(result['signal_class'],'ANALOG');self.assertTrue(any('fallback' in s for s in result['quality']['warnings']))
            from rfatlas.rfai import LABELS,VERSION
            path.write_text(json.dumps(dict(schema=1,algorithm=VERSION,labels=LABELS,mean=[0]*14,scale=[1]*14,centers=[[1e300]*14]*10,variance=[[1]*14]*10,rejection_score=.65,rejection_margin=.15)))
            result=classify(generate('NFM'),250000,backend='rfai',model_path=path)
            self.assertEqual(result['signal_class'],'ANALOG');self.assertNotIn('rfai_prediction',result)

    def test_similarity_has_no_rf_frequency_input(self):
        a=classify(generate('NFM'),250000);b=dict(a,frequency_hz=1000000000)
        self.assertEqual(vector(a),vector(b));self.assertAlmostEqual(score(vector(a),vector(b)),1)
        self.assertIsNone(vector(dict(a,adc_amplitude_p95_counts=1)))

    def test_pipeline_distinguishes_budget_and_integrity_failure(self):
        result=dict(analysis=dict(signal_detected=True,signal_class='UNKNOWN',modulation='NFM'),protocol_verified=False,
            candidates=[dict(decoder='one',eligible=True),dict(decoder='two',eligible=True)],
            attempts=[dict(decoder=dict(name='one'),status='COMPLETE',outcome='CRC_FAILURE')])
        states=diagnose(result)['states'];self.assertIn('FRAME_VERIFICATION_FAILED',states);self.assertIn('COMPATIBLE_DECODER_NOT_INVOKED',states)

    def test_broad_sensor_adapters_do_not_starve_in_context_dmr_probe(self):
        from rfatlas.identification import default_registry,select_candidates
        from rfatlas.decoders import DecoderInput
        registry=default_registry()
        for decoder in registry.decoders.values():decoder.available=lambda:True
        recording=DecoderInput(np.ones(250000,dtype=complex),250000,400000000)
        candidates=select_candidates(registry,recording,dict(modulation='UNKNOWN',features=dict(occupied_bandwidth_hz=100000),activity=dict(events=[{}])))
        selected=[c['decoder'] for c in candidates if c['eligible']][:4]
        self.assertIn('native-dmr-bs-csbk',selected)
        ais=next(c for c in candidates if c['decoder']=='native-ais-position')
        self.assertTrue(ais['eligible']);self.assertIn('outside typical band context; remains eligible',ais['hints'])


class ReanalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.config=Config(self.root,lock_root=self.root/'locks')
        self.db=open_database(self.root);self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO signals(signal_id,center_frequency_hz,protocol) VALUES('test',400000000,'UNKNOWN')");self.db.commit()
        self.path=self.root/'sample.cu8';x=dmr_wave();raw=np.empty(len(x)*2,np.uint8);raw[::2]=np.clip(np.round(127.5+x.real*40),0,255);raw[1::2]=np.clip(np.round(127.5+x.imag*40),0,255);self.path.write_bytes(raw.tobytes())
        self.meta=dict(capture_id='sample',signal_id='test',format='cu8',sample_rate=250000,center_hz=400000000,sample_count=len(x),created='2026-01-01T00:00:00+00:00',sha256=hashlib.sha256(raw.tobytes()).hexdigest())
        self.path.with_suffix('.json').write_text(json.dumps(self.meta))
        self.db.execute('INSERT INTO captures VALUES(?,?,?,?,?)',('sample','test',None,str(self.path),json.dumps(self.meta)));self.db.commit()
        self.registry=DecoderRegistry();self.registry.register(DMRDecoder())
        self.patch=patch('rfatlas.identification.default_registry',return_value=self.registry);self.patch.start();self.addCleanup(self.patch.stop)
        self.controller=ReanalysisController(self.config);self.addCleanup(self.controller.close)

    def finish(self):
        self.controller.thread.join(timeout=30);self.assertFalse(self.controller.thread.is_alive())
        return self.controller.status()['jobs'][0]

    def test_verified_replay_then_cache_then_force(self):
        self.controller.start(dict(signal_id='test'));job=self.finish();self.assertEqual(job['progress']['verified'],1)
        self.assertEqual(self.db.execute('select protocol from signals').fetchone()[0],'DMR')
        raw=self.db.execute('select result from identification_runs').fetchone()[0];result=json.loads(raw)
        self.assertFalse(result['retention']['frames']);self.assertFalse(result['retention']['identifiers']);self.assertTrue(result['reanalysis']['historical'])
        self.controller.start(dict(signal_id='test'));self.assertEqual(self.finish()['progress']['skipped'],1)
        self.assertEqual(self.db.execute('select count(*) from identification_runs').fetchone()[0],1)
        self.controller.start(dict(signal_id='test',force=True));self.assertEqual(self.finish()['progress']['completed'],1)
        self.assertEqual(self.db.execute('select count(*) from identification_runs').fetchone()[0],2)

    def test_cache_still_checks_raw_integrity(self):
        self.controller.start(dict(signal_id='test'));self.finish();self.path.write_bytes(b'broken')
        self.controller.start(dict(signal_id='test'));job=self.finish();self.assertEqual(job['progress']['failed'],1);self.assertEqual(job['progress']['skipped'],0)

    def test_optional_model_reaches_recording_replay_and_keeps_native_verification(self):
        from rfatlas.rfai import LABELS,VERSION
        model=self.root/'prototype.json'
        model.write_text(json.dumps(dict(schema=1,algorithm=VERSION,labels=LABELS,mean=[0]*14,scale=[1]*14,centers=[[0]*14]*10,variance=[[1]*14]*10,rejection_score=.65,rejection_margin=.15)))
        self.controller.start(dict(signal_id='test',backend='rfai'),model_path=model)
        self.assertEqual(self.finish()['progress']['verified'],1)
        result=json.loads(self.db.execute('select result from identification_runs').fetchone()[0])
        self.assertIn('rfai_prediction',result['analysis'])
        self.assertEqual(result['protocol'],'DMR')

    def test_missing_iq_does_not_synthesize_history(self):
        self.path.unlink();self.controller.start(dict(signal_id='test'));job=self.finish()
        self.assertEqual(job['progress']['failed'],1);self.assertEqual(self.db.execute('select count(*) from identification_runs').fetchone()[0],0)

    def test_cached_recording_rejects_changed_rate_metadata(self):
        self.controller.start(dict(signal_id='test'));self.finish()
        sidecar=self.path.with_suffix('.json');meta=json.loads(sidecar.read_text());meta['sample_rate']=500000;sidecar.write_text(json.dumps(meta))
        self.controller.start(dict(signal_id='test'));job=self.finish()
        self.assertEqual(job['progress']['failed'],1);self.assertEqual(job['progress']['skipped'],0)
        self.assertEqual(self.db.execute('select count(*) from identification_runs').fetchone()[0],1)

    def test_cancellation_and_second_worker_exclusion(self):
        entered=threading.Event();release=threading.Event()
        def slow(*args,**kwargs):
            entered.set();release.wait(5)
            if kwargs['cancel_event'].is_set():raise InterruptedError()
        with patch('rfatlas.identification.identify_recording',side_effect=slow):
            self.controller.start(dict(signal_id='test'));self.assertTrue(entered.wait(5))
            other=ReanalysisController(self.config);self.addCleanup(other.close)
            with self.assertRaises(RuntimeError):other.start(dict(signal_id='test'))
            self.controller.cancel();release.set();self.assertEqual(self.finish()['status'],'cancelled')

    def test_restart_recovers_interrupted_state(self):
        self.db.execute('INSERT INTO reanalysis_jobs VALUES(?,?,?,?,?,?)',('stale',0,0,'running','{}','{}'));self.db.commit()
        other=ReanalysisController(self.config);self.addCleanup(other.close)
        self.assertEqual(other.status()['jobs'][0]['status'],'interrupted')

    def test_offline_worker_does_not_take_receiver_lock(self):
        from rfatlas.platforms import DeviceLock
        with DeviceLock(self.config.lock_root,'rtl-sdr'):
            self.controller.start(dict(signal_id='test'));self.assertEqual(self.finish()['progress']['verified'],1)

    def test_bounds_and_no_recording_state(self):
        for options in [dict(limit=101),dict(max_seconds=1000),dict(force='yes'),dict(backend='rfai')]:
            with self.assertRaises(ValueError):self.controller.start(options)
        self.db.execute("INSERT INTO signals(signal_id) VALUES('no-iq')");self.db.commit()
        self.assertIn('NO_USABLE_IQ_RECORDING',signal_status(self.db,'no-iq')['states'])


if __name__=='__main__':unittest.main()
