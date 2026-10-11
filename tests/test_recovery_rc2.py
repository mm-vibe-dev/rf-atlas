"""Audio sideband preservation, decoder stages and bounded browser timing."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import numpy as np
from scipy import signal
from rfatlas.recognition import classify


class RecoveryTests(unittest.TestCase):
    def test_distributed_am_sidebands_survive_carrier_bandwidth_estimate(self):
        rate=250000;rng=np.random.default_rng(881);n=75000
        audio=signal.sosfilt(signal.butter(4,2200,fs=rate,output='sos'),rng.normal(size=n));audio/=np.max(abs(audio))
        t=np.arange(n)/rate
        x=.15*(1+.65*audio)*np.exp(2j*np.pi*12000*t)+.001*(rng.normal(size=n)+1j*rng.normal(size=n))
        a=classify(x,rate)
        self.assertEqual(a['modulation'],'AM');self.assertEqual(a['signal_class'],'ANALOG')
        self.assertEqual(a['protocol'],'UNKNOWN');self.assertGreaterEqual(a['recognition_features']['analysis_bandwidth_hz'],12000)

    def test_carrier_does_not_claim_cw_keying(self):
        rate=250000;t=np.arange(75000)/rate
        a=classify(.2*np.exp(2j*np.pi*12000*t),rate)
        self.assertEqual(a['signal_class'],'CARRIER');self.assertEqual(a['modulation'],'CARRIER')

    def test_multiplex_pilot_supports_fm_without_verifying_rds(self):
        rate=1024000;n=400000;t=np.arange(n)/rate;rng=np.random.default_rng(995)
        audio=signal.sosfilt(signal.butter(4,11000,fs=rate,output='sos'),rng.normal(size=n));audio/=np.max(abs(audio))
        multiplex=.8*audio+.1*np.cos(2*np.pi*19000*t)
        x=.2*np.exp(2j*np.pi*(np.cumsum(multiplex)*75000/rate+17000*t))
        x+=.005*(rng.normal(size=n)+1j*rng.normal(size=n))
        a=classify(x,rate)
        self.assertEqual(a['signal_class'],'ANALOG');self.assertEqual(a['modulation'],'WFM');self.assertEqual(a['protocol'],'UNKNOWN')

    def test_browser_clock_accounts_for_stalls_and_resets_epochs(self):
        node=shutil.which('node')
        if not node:
            candidate=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            if candidate.is_file():node=str(candidate)
        if not node:self.skipTest('Node unavailable for pure browser clock fixture')
        asset=Path(__file__).resolve().parents[1]/'rfatlas/web_assets/audio_clock.js'
        code="""const Clock=require(process.argv[1]);const c=new Clock();let oldEnd=0,oldGaps=0;
        for(let i=0;i<200;i++){const now=i*.1+(i%20===19?.29:0);if(oldEnd<now){if(i)oldGaps++;oldEnd=now+.16;}oldEnd+=.1;c.schedule(now,.1);}
        if(!(c.underruns===oldGaps&&c.gapSeconds>0&&c.target===.16&&c.end-19.9<.9))throw Error(JSON.stringify(c));
        c.reset();if(c.packets||c.underruns||c.end)throw Error('epoch reset failed');
        console.log(JSON.stringify({oldGaps}));"""
        result=subprocess.run([node,'-e',code,str(asset)],capture_output=True,text=True,check=True)
        self.assertGreater(json.loads(result.stdout)['oldGaps'],1)

    def test_decoder_pipeline_does_not_invent_missing_stage_evidence(self):
        from rfatlas.pipeline import diagnose
        a=dict(decoder=dict(name='one'),protocol_verified=False,input_sample_sha256='a'*64,
               evidence=dict(frames_examined=3,frames_with_sync=2,frames_with_valid_checksum=0,frames_with_sync_and_valid_checksum=0))
        stages=diagnose(dict(candidates=[dict(decoder='one',eligible=True)],attempts=[a]))['routing'][0]['stages']
        self.assertTrue(stages['input_valid']);self.assertTrue(stages['synchronization'])
        self.assertFalse(stages['checksum']);self.assertIsNone(stages['framing']);self.assertFalse(stages['verified'])
