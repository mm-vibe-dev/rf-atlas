"""Independent tones, stream boundaries, lifecycle and receiver exclusion."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from scipy import signal

from rfatlas.config import Config
from rfatlas.monitor import AnalogDSP, LiveMonitor, settings
from rfatlas.platforms import DeviceLock
from rfatlas.runtime import RuntimeSupervisor


class DemodulationTests(unittest.TestCase):
    def tones(self,mode):
        rate=1024000; t=np.arange(rate)/rate; tone=1000
        if mode=='AM': x=(.25+.1*np.cos(2*np.pi*tone*t)).astype(complex)
        elif mode in ('WFM','NFM'):
            deviation=20000 if mode=='WFM' else 2500
            x=.3*np.exp(1j*deviation/tone*np.sin(2*np.pi*tone*t))
        elif mode in ('USB','LSB'):
            x=.3*np.exp((1 if mode=='USB' else -1)*2j*np.pi*tone*t)
            # Strong opposite sideband must be suppressed by the chosen filter.
            x+=.3*np.exp((-1 if mode=='USB' else 1)*2j*np.pi*1200*t)
        else: x=np.full(rate,.3+0j)
        return x,700 if mode=='CW' else tone

    def test_all_modes_recover_independent_expected_tone(self):
        for mode in ('AM','NFM','WFM','USB','LSB','CW'):
            with self.subTest(mode=mode):
                x,tone=self.tones(mode); dsp=AnalogDSP(settings({'mode':mode,'deemphasis_us':0}))
                chunks=[dsp.process(x[i:i+102400],30)[0] for i in range(0,len(x),102400)]
                pcm=np.frombuffer(b''.join(chunks),dtype='<i2').astype(float)[4800:]
                self.assertEqual(sum(len(c) for c in chunks)//2,48000)
                f,p=signal.welch(pcm,fs=48000,nperseg=8192)
                self.assertLess(abs(f[np.argmax(p)]-tone),8)
                concentration=np.sum(p[abs(f-tone)<20])/np.sum(p)
                self.assertGreater(concentration,.95)
                self.assertTrue(np.isfinite(pcm).all())

    def test_squelch_hysteresis_mute_and_limiter_without_rf_changes(self):
        x,_=self.tones('AM'); x=x[:102400]
        dsp=AnalogDSP(settings({'mode':'AM','squelch':True,'squelch_db':10}))
        self.assertFalse(dsp.process(x,11)[1]['squelch_open'])
        self.assertTrue(dsp.process(x,13)[1]['squelch_open'])
        self.assertTrue(dsp.process(x,7)[1]['squelch_open'])
        self.assertTrue(dsp.process(x,7)[1]['squelch_open'])
        audio,m=dsp.process(x,7);self.assertFalse(m['squelch_open']);self.assertFalse(any(audio))
        audio,_=AnalogDSP(settings({'mode':'AM','mute':True})).process(x,30);self.assertFalse(any(audio))
        audio,m=AnalogDSP(settings({'mode':'AM','audio_gain':10,'volume':1})).process(x,30)
        self.assertLessEqual(np.max(abs(np.frombuffer(audio,dtype='<i2').astype(float))),32112)
        self.assertGreater(m['limited_samples'],0)

    def test_all_supported_rates_produce_exact_audio_clock(self):
        for rate in (250000,1024000,2048000,2400000):
            for mode in ('AM','NFM','WFM','USB','LSB','CW'):
                with self.subTest(rate=rate,mode=mode):
                    dsp=AnalogDSP(settings({'sample_rate':rate,'mode':mode}))
                    t=np.arange(rate//10)/rate
                    iq=.3*np.exp(2j*np.pi*1000*t)
                    a,_=dsp.process(iq,20);b,_=dsp.process(iq,20)
                    self.assertEqual(len(a),9600);self.assertEqual(len(b),9600)

    def test_invalid_settings_do_not_enable_agc_or_exceed_filter(self):
        for bad in ({'gain':'auto'},{'gain':0},{'frequency_hz':True},{'ppm':.5},{'sample_rate':True},
                    {'volume':float('nan')},{'mode':'USB','filter_hz':200000},
                    {'offset_hz':100000,'sample_rate':250000},{'deemphasis_us':100}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):settings(bad)


class StreamTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.config=Config(self.root,lock_root=self.root/'locks')
        self.monitor=LiveMonitor(self.config);self.addCleanup(self.monitor.stop)

    def test_continuous_iq_lock_buffers_settings_and_disconnect(self):
        script=self.root/'stream.py'
        script.write_text('import sys,time\nb=bytes([160,127])*25000\nwhile True:\n sys.stdout.buffer.write(b);sys.stdout.buffer.flush();time.sleep(.1)\n')
        real=subprocess.Popen
        def launch(*args,**kwargs):return real([sys.executable,str(script)],**kwargs)
        with patch.object(self.monitor.backend,'tool',return_value=Path(sys.executable)),patch('rfatlas.monitor.subprocess.Popen',side_effect=launch):
            result=self.monitor.start({'sample_rate':250000,'mode':'NFM'})
            self.assertTrue(result['active']);self.assertIsNotNone(result['latest'])
            with self.assertRaises(RuntimeError):
                with DeviceLock(self.config.lock_root,'rtl-sdr'):pass
            time.sleep(.4);state=self.monitor.status();self.assertGreater(state['sequence'],1)
            pcm,seq,epoch=self.monitor.audio_packet(0,state['epoch']);self.assertGreater(len(pcm),0)
            self.assertLessEqual(len(pcm),28800)
            self.monitor.update({'mute':True});time.sleep(.3)
            state=self.monitor.status();pcm,_,_=self.monitor.audio_packet(seq,state['epoch'])
            self.assertFalse(any(pcm));self.assertGreater(state['sequence'],seq)
            self.monitor.stop()
        with DeviceLock(self.config.lock_root,'rtl-sdr'):pass
        self.assertFalse(self.monitor.status()['active'])
        self.assertFalse(self.monitor.iq);self.assertFalse(self.monitor.audio)

    def test_no_samples_and_stalled_stream_fail_and_release_receiver(self):
        for emit_first in (False, True):
            with self.subTest(emit_first=emit_first):
                script=self.root/'stall.py'
                script.write_text('import sys,time\n'+
                    ('for _ in range(6):\n sys.stdout.buffer.write(bytes([160,127])*25000);sys.stdout.buffer.flush();time.sleep(.1)\n' if emit_first else '')+
                    'time.sleep(30)\n')
                real=subprocess.Popen
                def launch(*args,**kwargs):return real([sys.executable,str(script)],**kwargs)
                with patch.object(self.monitor.backend,'tool',return_value=Path(sys.executable)),patch('rfatlas.monitor.subprocess.Popen',side_effect=launch),patch('rfatlas.monitor.IQ_DELIVERY_TIMEOUT_S',.25):
                    if emit_first:
                        self.monitor.start({'sample_rate':250000,'mode':'NFM'})
                        deadline=time.monotonic()+4
                        while self.monitor.status()['active'] and time.monotonic()<deadline:time.sleep(.05)
                        self.assertGreater(self.monitor.sequence,0)
                    else:
                        with self.assertRaises(RuntimeError):self.monitor.start({'sample_rate':250000,'mode':'NFM'})
                    state=self.monitor.status()
                    self.assertFalse(state['active']);self.assertEqual(state['status'],'failed')
                    self.assertIn('stopped delivering I/Q',state['error'])
                    self.assertIsNone(self.monitor.process)
                    self.assertFalse(self.monitor.iq);self.assertFalse(self.monitor.audio)
                    with DeviceLock(self.config.lock_root,'rtl-sdr'):pass

    def test_signal_keyed_user_presets_roundtrip(self):
        self.monitor.signal_id='channel-a';self.monitor.selected=settings({'mode':'AM','volume':.2,'ppm':3})
        self.monitor.save_preset();self.assertEqual(self.monitor.preset('channel-a')['settings']['ppm'],3)
        self.assertIsNone(self.monitor.preset('same-frequency-different-channel'))
        self.assertEqual(self.monitor.preset('channel-a')['source'],'USER')

    def test_last_reception_preferences_restore_without_creating_preset(self):
        path=self.root/'runtime/monitor-sessions.json';path.parent.mkdir()
        path.write_text(json.dumps([dict(settings=settings({'gain':'29.7','ppm':2}))]))
        restored=LiveMonitor(self.config)
        self.assertEqual(restored.selected['gain'],'29.7');self.assertEqual(restored.selected['ppm'],2)
        self.assertIsNone(restored.signal_id);self.assertIsNone(restored.preset('unrelated'))
        path.write_text('malformed')
        self.assertEqual(LiveMonitor(self.config).selected,settings({}))

    def test_optimizer_rejects_stronger_clipped_reception_and_restores(self):
        import threading
        self.monitor.thread=threading.current_thread()
        previous=dict(self.monitor.selected)
        def apply(selected,restoring=False):
            self.monitor.selected=dict(selected);self.monitor.sequence+=1
            gain=selected['gain'];snr={'15.7':18,'29.7':22,'40.2':30}[gain]
            self.monitor.metrics=dict(snr_db=snr+(.1 if selected['offset_hz'] else 0),
                adc_clip_fraction=.05 if gain=='40.2' else 0)
        self.monitor.optimize(apply);self.monitor.optimize_thread.join(10)
        self.assertFalse(self.monitor.optimize_thread.is_alive())
        result=self.monitor.optimization
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['recommended']['gain'],'29.7')
        self.assertEqual(result['recommended']['offset_hz'],0)
        self.assertEqual(self.monitor.selected,previous)
        self.assertFalse((self.root/'runtime/reception-presets.json').exists())

    def test_lease_expiry_restores_survey_and_releases_lock(self):
        from tests.test_runtime import Controller
        runtime=RuntimeSupervisor(self.config);self.addCleanup(runtime.close)
        calls=[];runtime.survey=Controller('survey',calls);runtime.survey.state='paused'
        runtime._monitor_restore=True
        runtime.monitor.state='failed'
        deadline=time.monotonic()+3
        while not calls and time.monotonic()<deadline:time.sleep(.05)
        self.assertIn(('survey','resume'),calls)
        self.assertFalse(runtime._monitor_restore)

    def test_inventory_handoff_and_real_stream_lease_expiry(self):
        from tests.test_runtime import Controller
        from rfatlas.storage import open_database
        db=open_database(self.root)
        db.execute("INSERT INTO signals(signal_id,center_frequency_hz,protocol) VALUES('unknown',100000000,'UNKNOWN')")
        db.commit();db.close()
        runtime=RuntimeSupervisor(self.config);runtime.monitor=self.monitor;self.addCleanup(runtime.close)
        calls=[];runtime.survey=Controller('survey',calls);runtime.scanner=Controller('scanner',calls)
        runtime.tuner=Controller('tuner',calls);runtime.survey.state='running'
        self.monitor.selected=settings({'offset_hz':500})
        script=self.root/'stream.py';script.write_text('import sys,time\nb=bytes([160,127])*102400\nwhile True:\n sys.stdout.buffer.write(b);sys.stdout.buffer.flush();time.sleep(.1)\n')
        real=subprocess.Popen
        def launch(*args,**kwargs):return real([sys.executable,str(script)],**kwargs)
        with patch.object(self.monitor.backend,'tool',return_value=Path(sys.executable)),patch('rfatlas.monitor.subprocess.Popen',side_effect=launch):
            state=runtime.action('monitor/start',{'signal_id':'unknown'})
            self.assertEqual(state['mode'],'monitor');self.assertEqual(runtime.survey.state,'paused')
            self.assertEqual(state['monitor']['settings']['offset_hz'],0)
            self.assertIn(('survey','pause'),calls)
            self.monitor.last_heartbeat=time.monotonic()-31
            deadline=time.monotonic()+4
            while runtime.survey.state!='running' and time.monotonic()<deadline:time.sleep(.05)
            self.assertIn(('survey','resume'),calls);self.assertFalse(self.monitor.status()['active'])
        with DeviceLock(self.config.lock_root,'rtl-sdr'):pass
        sessions=json.loads((self.root/'runtime/monitor-sessions.json').read_text())
        self.assertEqual(sessions[-1]['signal_id'],'unknown')


if __name__=='__main__':unittest.main()
