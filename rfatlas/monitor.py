"""Transient single-receiver I/Q monitoring and stateful analog reception.

No audio or I/Q files are written. Receiver levels are relative dBFS, never dBm.
"""
from collections import deque
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import math
import os
import queue
from pathlib import Path
import signal as os_signal
import subprocess
import sys
import threading
import time

import numpy as np
from scipy import signal

from .diagnostics import atomic_json
from .hardware import RTLBackend, gain_value
from .platforms import WindowsJob

IQ_DELIVERY_TIMEOUT_S = 5.

MODES = {'AM': (2000, 20000, 12000), 'NFM': (4000, 30000, 16000),
         'WFM': (100000, 240000, 200000), 'USB': (1000, 5000, 3000),
         'LSB': (1000, 5000, 3000), 'CW': (100, 3000, 500)}
DEFAULTS = dict(frequency_hz=100000000, sample_rate=1024000, mode='WFM',
                gain='15.7', filter_hz=200000, ppm=0, offset_hz=0,
                squelch=False, squelch_db=10., volume=.5, mute=False,
                audio_gain=1., audio_agc=False, deemphasis_us=75,
                audio_hz=15000, dc_remove=True, decode_enabled=True)


def settings(params, previous=None):
    if not isinstance(params, dict) or set(params)-set(DEFAULTS):
        raise ValueError('Unknown monitor setting')
    s = dict(DEFAULTS, **(previous or {})); s.update(params)
    if s['mode'] not in MODES: raise ValueError('Unsupported reception mode')
    if 'mode' in params and 'filter_hz' not in params:
        s['filter_hz'] = MODES[s['mode']][2]
        s['audio_hz'] = 15000 if s['mode']=='WFM' else 3000
    for key, low, high in [('frequency_hz',500000,1766000000), ('ppm',-200,200),
                          ('offset_hz',-100000,100000), ('filter_hz',100,240000),
                          ('squelch_db',0,50), ('volume',0,1), ('audio_gain',.1,10),
                          ('audio_hz',100,15000)]:
        v=s[key]
        if type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high:
            raise ValueError('Monitor setting outside limits')
    for key in ('frequency_hz','ppm','offset_hz','filter_hz','audio_hz'):
        if int(s[key])!=s[key]: raise ValueError('Integer setting required')
        s[key]=int(s[key])
    if s['sample_rate'] not in (250000,1024000,2048000,2400000) or type(s['sample_rate']) is not int:
        raise ValueError('Unsupported sample rate')
    for key in ('mute','squelch','audio_agc','dc_remove','decode_enabled'):
        if type(s[key]) is not bool: raise ValueError('Boolean setting required')
    if type(s['deemphasis_us']) is not int or s['deemphasis_us'] not in (0,50,75):
        raise ValueError('Unsupported de-emphasis')
    low,high,_=MODES[s['mode']]
    if not low<=s['filter_hz']<=high or s['filter_hz']+2*abs(s['offset_hz'])>s['sample_rate']*.8:
        raise ValueError('Filter exceeds usable receiver passband')
    if not 500000<=s['frequency_hz']+s['offset_hz']<=1766000000:
        raise ValueError('Demodulation frequency outside receiver limits')
    try: s['gain']=gain_value(str(s['gain']))
    except Exception as error: raise ValueError('Unsupported gain') from error
    # rtl_sdr reserves numeric zero for automatic gain, unlike rtl_power.
    if s['gain']=='auto' or float(s['gain'])==0: raise ValueError('Positive manual gain required')
    return s


class AnalogDSP:
    """Streaming FIR decimation, channel filtering, demodulation and 48 kHz PCM.

    Fixed 100 ms input blocks make rational resampling exact. FIR history and
    IIR states survive block boundaries; settings changes start a new epoch.
    """
    def __init__(self, selected):
        self.s=settings(selected); self.rate=self.s['sample_rate']; self.phase=0
        self.previous=0j; self.open=False; self.below=0; self.agc=1.
        # Decimate to at least 256 kHz for WFM or 48 kHz for other modes.
        target=256000 if self.s['mode']=='WFM' else 48000
        self.factor=max(v for v in range(1,max(1,int(self.rate//target))+1) if (self.rate//10)%v==0)
        self.ifrate=self.rate/self.factor
        self.taps=signal.firwin(161,min(self.ifrate*.43,self.rate*.43),fs=self.rate)
        self.zi=np.zeros(len(self.taps)-1,dtype=complex)
        self.channel=signal.butter(6,self.s['filter_hz']/2,fs=self.ifrate,output='sos')
        self.channel_zi=np.zeros((len(self.channel),2),complex)
        self.audio_filter=signal.butter(4,min(self.s['audio_hz'],self.ifrate*.4,20000),fs=self.ifrate,output='sos')
        self.audio_zi=np.zeros((len(self.audio_filter),2))
        self.dc_zi=np.zeros(1); self.de_zi=np.zeros(1); self.cw_phase=0
        # Streaming rational polyphase conversion using persistent FIR state.
        divisor=math.gcd(int(self.rate),48000*self.factor)
        self.up=48000*self.factor//divisor; self.down=int(self.rate)//divisor
        self.resample_taps=(np.ones(1) if self.up==self.down==1 else
            signal.firwin(12*max(self.up,self.down)+1,1/max(self.up,self.down))*self.up)
        from .audio_dsp import StreamingResampler
        self.resampler=StreamingResampler(self.resample_taps,self.up,self.down)

    def process(self, iq, snr):
        s=self.s; n=np.arange(len(iq))+self.phase
        # SSB uses a complex bandpass centered at +/- half the selected width.
        side=s['filter_hz']/2 if s['mode']=='USB' else -s['filter_hz']/2 if s['mode']=='LSB' else 0
        translated=iq*np.exp(-2j*np.pi*(s['offset_hz']+side)*n/self.rate)
        self.phase+=len(iq)
        filtered,self.zi=signal.lfilter(self.taps,[1],translated,zi=self.zi)
        start=(-int(n[0]))%self.factor
        x,self.channel_zi=signal.sosfilt(self.channel,filtered[start::self.factor],zi=self.channel_zi)
        if s['mode']=='AM': audio=abs(x)*2
        elif s['mode'] in ('NFM','WFM'):
            audio=np.angle(x*np.conj(np.r_[self.previous,x[:-1]]))*self.ifrate/(2*np.pi*(75000 if s['mode']=='WFM' else 5000))
            self.previous=x[-1]
        else:
            hz=side+(700 if s['mode']=='CW' else 0)
            t=np.arange(len(x))+self.cw_phase; self.cw_phase+=len(x)
            audio=np.real(x*np.exp(2j*np.pi*hz*t/self.ifrate))*2
        if s['dc_remove']:
            audio,self.dc_zi=signal.lfilter([1,-1],[1,-.995],audio,zi=self.dc_zi)
        if s['mode']=='WFM' and s['deemphasis_us']:
            a=math.exp(-1/(self.ifrate*s['deemphasis_us']*1e-6))
            audio,self.de_zi=signal.lfilter([1-a],[1,-a],audio,zi=self.de_zi)
        audio,self.audio_zi=signal.sosfilt(self.audio_filter,audio,zi=self.audio_zi)
        audio=self.resampler.process(audio)
        if not s['squelch']: self.open=True
        elif not self.open and snr>=s['squelch_db']+2: self.open=True; self.below=0
        elif self.open:
            self.below=self.below+1 if snr<s['squelch_db']-2 else 0
            if self.below>=3: self.open=False
        rms=float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.
        if s['audio_agc']:
            desired=min(20.,.15/max(rms,.001)); self.agc=.9*self.agc+.1*desired
        else: self.agc=1.
        audio*=self.agc*s['audio_gain']*s['volume']
        if s['mute'] or not self.open: audio*=0
        peak=float(np.max(abs(audio))) if len(audio) else 0.
        pcm=np.clip(audio,-.98,.98)
        return (pcm*32767).astype('<i2').tobytes(),dict(squelch_open=self.open,audio_rms=rms,
            input_samples=len(iq),if_samples=len(x),audio_samples=len(pcm),
            audio_duration_s=len(pcm)/48000,resample_clock_error_samples=len(pcm)-round(len(iq)*48000/self.rate),
            silence_reason='MUTED' if s['mute'] else 'SQUELCH_CLOSED' if not self.open else 'LOW_DEMODULATED_ENERGY' if rms<.0001 else None,
            audio_peak=peak,limited_samples=int(np.count_nonzero(abs(audio)>.98)))


class LiveMonitor:
    def __init__(self, config):
        self.config=config; self.backend=RTLBackend(config); self.mutex=threading.RLock()
        self._stop=threading.Event(); self._ready=threading.Event(); self.thread=None
        self.process=None; self.job=None; self.selected=settings({}); self.signal_id=None
        # Last reception preferences are separate from explicit signal presets.
        # Opening an inventory signal still supplies its observed center.
        import json
        path=config.root/'runtime/monitor-sessions.json'
        try:
            if path.is_file() and path.stat().st_size<2_000_000:
                sessions=json.loads(path.read_text(encoding='utf8'))
                if isinstance(sessions,list) and sessions: self.selected=settings(sessions[-1]['settings'])
        except (OSError,ValueError,KeyError,TypeError): pass
        self.rows=deque(maxlen=48); self.audio=deque(maxlen=10); self.iq=deque(maxlen=20)
        self.sequence=0; self.epoch=0; self.state='idle'; self.error=None; self.last_heartbeat=0
        self.evidence=None; self.started=None; self.metrics={}; self.decode_thread=None
        self.optimize_thread=None; self.cancel_optimize=threading.Event(); self.optimization=None
        self.last_decode=0

    def status(self, include_waterfall=True, since=None):
        with self.mutex:
            return dict(active=bool(self.thread and self.thread.is_alive()),status=self.state,error=self.error,
                settings=dict(self.selected),signal_id=self.signal_id,sequence=self.sequence,epoch=self.epoch,
                started=self.started,latest=copy.deepcopy(self.rows[-1]) if self.rows else None,
                waterfall=copy.deepcopy([row for row in self.rows if since is None or row['sequence']>since]) if include_waterfall else [],
                waterfall_delta=since is not None,metrics=dict(self.metrics),
                evidence=copy.deepcopy(self.evidence),optimization=copy.deepcopy(self.optimization),
                calibration='User PPM correction; calibration accuracy unverified',audio_rate=48000)

    def heartbeat(self):
        self.last_heartbeat=time.monotonic()

    def stop(self, cancel_assist=True):
        if cancel_assist: self.cancel_optimize.set()
        self._stop.set()
        with self.mutex:
            p=self.process
            if p is not None and p.poll() is None:
                if self.job: self.job.close(); self.job=None
                elif os.name=='nt': p.kill()
                else:
                    try: os.killpg(p.pid,os_signal.SIGKILL)
                    except ProcessLookupError: pass
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(8)
            if self.thread.is_alive(): raise RuntimeError('Live receiver shutdown incomplete')
        with self.mutex:
            self.thread=None; self.audio.clear(); self.iq.clear()
            if self.state!='failed': self.state='stopped'

    def start(self, params, signal_id=None):
        selected=settings(params,self.selected); executable=self.backend.tool('rtl_sdr')
        self.stop(cancel_assist=False)
        with self.mutex:
            self.selected=selected; self.signal_id=signal_id if signal_id is not None else self.signal_id
            self.epoch+=1; self.rows.clear(); self.audio.clear(); self.iq.clear()
            self.evidence=None; self.metrics={}; self.error=None; self.state='starting'
            self.last_decode=time.monotonic()-18
            if signal_id is not None or self.started is None:
                self.started=datetime.now(timezone.utc).isoformat()
            self.heartbeat()
            self._stop.clear(); self._ready.clear()
            self.thread=threading.Thread(target=self._run,args=(executable,),daemon=True,name='rf-atlas-monitor')
            self.thread.start()
        if not self._ready.wait(8) or self.error:
            self.stop(); raise RuntimeError('Continuous receiver initialization failed')
        return self.status()

    def update(self, params):
        selected=settings(params,self.selected)
        hardware=('frequency_hz','sample_rate','gain','ppm')
        if any(selected[k]!=self.selected[k] for k in hardware): return self.start(selected)
        with self.mutex:
            self.selected=selected; self.epoch+=1; self.audio.clear()
        return self.status()

    def audio_packet(self, since, epoch):
        with self.mutex:
            packets=[p for p in self.audio if p[0]>since] if epoch==self.epoch else list(self.audio)[-1:]
            # Drop old backlog; latency is bounded to 300 ms server-side.
            packets=packets[-3:]
            return (b''.join(p[1] for p in packets),packets[-1][0] if packets else self.sequence,self.epoch)

    def _run(self, executable):
        try:
            with self._receiver_session():
                s=dict(self.selected); rate=s['sample_rate']; count=rate//10
                command=[str(executable),'-d',self.config.device,'-f',str(s['frequency_hz']),
                    '-s',str(rate),'-g',s['gain'],'-p',str(s['ppm']),'-b','16384','-']
                if sys.platform.startswith('linux'):
                    command=[sys.executable,str(Path(__file__).with_name('_linux_child.py')),str(os.getpid()),*command]
                with self.mutex:
                    self.job=WindowsJob() if os.name=='nt' else None
                    self.process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,bufsize=0,start_new_session=os.name!='nt',
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    if self.job: self.job.assign(self.process)
                    p=self.process
                diagnostics=deque(maxlen=32)
                def drain():
                    for line in iter(p.stderr.readline,b''):
                        diagnostics.append(line[:512].decode(errors='replace').lower())
                reader=threading.Thread(target=drain,daemon=True); reader.start()
                blocks_in=queue.Queue(maxsize=4)
                continuity=dict(acquisition_blocks=0,delivery_gaps=0,dropped_iq_blocks=0,
                    maximum_delivery_interval_s=0.,dsp_overruns=0)
                def offer(value):
                    try:blocks_in.put_nowait(value)
                    except queue.Full:
                        try:blocks_in.get_nowait()
                        except queue.Empty:pass
                        continuity['dropped_iq_blocks']+=1
                        blocks_in.put_nowait(value)
                def acquire_blocks():
                    pending=bytearray();previous=None
                    try:
                        while not self._stop.is_set():
                            raw=p.stdout.read(count*2-len(pending))
                            if not raw:break
                            pending.extend(raw)
                            if len(pending)<count*2:continue
                            received=time.perf_counter()
                            if previous is not None:
                                interval=received-previous
                                continuity['maximum_delivery_interval_s']=max(continuity['maximum_delivery_interval_s'],interval)
                                if interval>.15:continuity['delivery_gaps']+=1
                            previous=received;continuity['acquisition_blocks']+=1
                            offer((bytes(pending),received));pending=bytearray()
                    finally:offer(None)
                acquisition_reader=threading.Thread(target=acquire_blocks,daemon=True,name='rf-atlas-iq-reader')
                acquisition_reader.start()
                dsp=None; dsp_epoch=None; skip=3; last_frame=0
                last_delivery=time.monotonic()
                while not self._stop.is_set():
                    try:packet=blocks_in.get(timeout=1)
                    except queue.Empty:
                        if time.monotonic()-last_delivery>IQ_DELIVERY_TIMEOUT_S:
                            self.error='Receiver stopped delivering I/Q; check the existing receiver connection.'
                            raise RuntimeError('Receiver sample delivery timed out')
                        continue
                    if packet is None:raise RuntimeError('Receiver stream ended')
                    last_delivery=time.monotonic()
                    pending,received=packet
                    data=np.frombuffer(pending,dtype=np.uint8).astype(np.float32); pending=bytearray()
                    x=((data[::2]-127.5)+1j*(data[1::2]-127.5))/128
                    if any(t in ''.join(diagnostics) for t in ('failed to set','failed to open','no supported devices','pll not locked')):
                        raise RuntimeError('Receiver rejected acquisition settings')
                    if skip: skip-=1; continue
                    with self.mutex: current=dict(self.selected); epoch=self.epoch
                    if dsp_epoch!=epoch:
                        structural=('sample_rate','mode','filter_hz','offset_hz','audio_hz','deemphasis_us','dc_remove')
                        if dsp is None or any(dsp.s[k]!=current[k] for k in structural): dsp=AnalogDSP(current)
                        else: dsp.s=current
                        dsp_epoch=epoch
                    began=time.perf_counter()
                    n=4096; blocks=x[:len(x)//n*n].reshape(-1,n)
                    spectrum=np.mean(abs(np.fft.fftshift(np.fft.fft(blocks*np.hanning(n),axis=1),axes=1)/np.sum(np.hanning(n)))**2,axis=0)
                    power=10*np.log10(np.maximum(spectrum,1e-20)); freqs=np.fft.fftshift(np.fft.fftfreq(n,1/rate))+current['frequency_hz']
                    target=current['frequency_hz']+current['offset_hz']
                    # Meter uses the selected passband, not an adjacent peak.
                    channel=abs(freqs-target)<=current['filter_hz']/2
                    outside=~channel; floor=float(np.median(power[outside] if np.any(outside) else power))
                    strength=float(np.max(power[channel])); snr=max(0.,strength-floor)
                    pcm,audio_metrics=dsp.process(x,snr)
                    now=time.time(); elapsed=time.perf_counter()-began
                    if elapsed>.1:continuity['dsp_overruns']+=1
                    with self.mutex:
                        self.iq.append(x.copy()); self.sequence+=1
                        if epoch==self.epoch: self.audio.append((self.sequence,pcm))
                        self.metrics=dict(audio_metrics,snr_db=snr,strength_dbfs=strength,noise_dbfs=floor,
                            adc_clip_fraction=float(np.mean((data<=1)|(data>=254))),processing_s=elapsed,
                            block_s=.1,last_sample_utc=now,active_signal=snr>=10,
                            acquisition_queue_blocks=blocks_in.qsize(),input_age_s=max(0.,time.perf_counter()-received),
                            continuity=dict(continuity),continuity_basis='Host delivery timing; CU8 has no hardware sample timestamps',
                            source='LIVE_IQ',audio_buffer_s=sum(len(p[1]) for p in self.audio)/96000)
                        if now-last_frame>=.2:
                            groups=np.arange(n).reshape(1024,4); idx=groups[np.arange(1024),np.argmax(power[groups],axis=1)]
                            self.rows.append(dict(frequency_hz=freqs[idx].tolist(),power_db=power[idx].tolist(),
                                sequence=self.sequence,
                                noise_db=[floor]*1024,observed=now,peak_snr_db=snr,noise_estimate_db=floor,
                                sample_rate_hz=rate,actual_step_hz=rate/n,coverage_start_hz=float(freqs[0]),coverage_stop_hz=float(freqs[-1])))
                            last_frame=now
                        self.state='receiving'; self._ready.set()
        except Exception:
            if not self._stop.is_set():
                self.state='failed'
                self.error=self.error or 'Live receiver failed; check connection and compatible settings.'
        finally:
            with self.mutex:
                p=self.process
                if self.job: self.job.close(); self.job=None
                if p is not None:
                    if p.poll() is None:
                        if os.name=='nt': p.kill()
                        else:
                            try: os.killpg(p.pid,os_signal.SIGKILL)
                            except ProcessLookupError: pass
                    p.wait(timeout=5)
                    if 'acquisition_reader' in locals():acquisition_reader.join(1)
                    p.stdout.close(); p.stderr.close()
                self.process=None; self.audio.clear(); self.iq.clear()
                if self.state!='failed': self.state='stopped'
            self._ready.set()

    @contextmanager
    def _receiver_session(self):
        # Kill and reap the process before releasing hardware ownership,
        # including EOF, setup failures and parent shutdown.
        with self.backend.lock():
            try: yield
            finally:
                with self.mutex:
                    p=self.process
                    if self.job: self.job.close(); self.job=None
                    if p is not None:
                        if p.poll() is None:
                            if os.name=='nt': p.kill()
                            else:
                                try: os.killpg(p.pid,os_signal.SIGKILL)
                                except ProcessLookupError: pass
                        p.wait(timeout=5)

    def decode(self, retain_frames=False, retain_identifiers=False):
        if self.decode_thread and self.decode_thread.is_alive(): raise RuntimeError('Decoder already active')
        with self.mutex:
            if not self.iq: raise RuntimeError('Wait for live samples')
            x=np.concatenate(list(self.iq))[:4_000_000]; s=dict(self.selected); epoch=self.epoch
            if s['offset_hz']:
                x=x*np.exp(-2j*np.pi*s['offset_hz']*np.arange(len(x))/s['sample_rate'])
            self.last_decode=time.monotonic()
        def work():
            from .decoders import DecoderInput
            from .identification import identify,default_registry
            digest=hashlib.sha256(x.astype('<c8').tobytes()).hexdigest()
            try:
                from .recognition import classify
                analysis=classify(x[:1_000_000],s['sample_rate'],{'format':'cu8'})
                result=identify(DecoderInput(x,s['sample_rate'],s['frequency_hz']+s['offset_hz'],0,digest),analysis,registry=default_registry(config=self.config))
                from .pipeline import diagnose
                result['pipeline_diagnostics']=diagnose(result)
                # Use the same audited whitelist as persisted decoder evidence.
                from .entities import private_result
                result=private_result(result,retain_frames=retain_frames,retain_identifiers=retain_identifiers)
            except Exception: result={'protocol':'UNKNOWN','error':'Live decoder failed'}
            with self.mutex:
                if self.epoch==epoch: self.evidence=dict(result,source='TRANSIENT_LIVE_IQ',source_sha256=digest)
        self.decode_thread=threading.Thread(target=work,daemon=True,name='rf-atlas-live-decoder'); self.decode_thread.start()

    def save_preset(self):
        if not self.signal_id: raise ValueError('Select an inventory signal first')
        path=self.config.root/'runtime/reception-presets.json'
        import json
        presets=json.loads(path.read_text(encoding='utf8')) if path.is_file() else {}
        presets[self.signal_id]=dict(settings=self.selected,source='USER',saved=datetime.now(timezone.utc).isoformat())
        atomic_json(path,presets)

    def preset(self, signal_id):
        import json
        path=self.config.root/'runtime/reception-presets.json'
        if not path.is_file() or path.stat().st_size>2_000_000: return None
        return json.loads(path.read_text(encoding='utf8')).get(signal_id)

    def optimize(self, apply):
        """Evaluate three manual gains and three small offsets, then restore.

        The supervisor serializes changes and refuses to restart a stopped
        monitor. A recommendation is never a preset or protocol assertion.
        """
        if self.optimize_thread and self.optimize_thread.is_alive(): raise RuntimeError('Optimization already active')
        if not self.status()['active']: raise RuntimeError('Monitor inactive')
        previous=dict(self.selected); self.cancel_optimize.clear()
        self.optimization=dict(status='running',previous=previous,trials=[])
        def work():
            trials=[]; recommended=None
            try:
                candidates=[dict(previous,gain=g) for g in ('15.7','29.7','40.2')]
                for trial in candidates:
                    if self.cancel_optimize.is_set(): break
                    apply(trial)
                    values=[]; last=-1; deadline=time.monotonic()+1.2
                    while time.monotonic()<deadline and not self.cancel_optimize.wait(.1):
                        state=self.status()
                        if state['sequence']!=last and state['metrics']:
                            values.append(state['metrics']); last=state['sequence']
                    if values:
                        snr=float(np.median([v['snr_db'] for v in values])); clipping=max(v['adc_clip_fraction'] for v in values)
                        trials.append(dict(settings=trial,snr_db=snr,clip_fraction=clipping,
                            spread_db=float(np.ptp([v['snr_db'] for v in values])),score=snr-1000*clipping))
                if trials and not self.cancel_optimize.is_set():
                    best=max(trials,key=lambda t:t['score'])
                    if best['snr_db']>=10 and best['clip_fraction']<.01 and best['spread_db']<10:
                        for delta in (-1000,1000):
                            trial=dict(best['settings'],offset_hz=previous['offset_hz']+delta)
                            try: settings(trial)
                            except ValueError: continue
                            apply(trial); self.cancel_optimize.wait(.4)
                            v=self.status()['metrics']
                            if v: trials.append(dict(settings=trial,snr_db=v['snr_db'],clip_fraction=v['adc_clip_fraction'],
                                spread_db=0,score=v['snr_db']-1000*v['adc_clip_fraction']))
                        eligible=[t for t in trials if t['snr_db']>=10 and t['clip_fraction']<.01 and t['spread_db']<10]
                        chosen=max(eligible,key=lambda t:t['score'])
                        # Small differences cannot distinguish RF variation
                        # from a useful frequency correction. Preserve offset
                        # unless the measured score improves by at least 3 dB.
                        if chosen['settings']['offset_hz']!=previous['offset_hz'] and chosen['score']<best['score']+3:
                            chosen=best
                        recommended=dict(chosen['settings'],squelch_db=max(0,min(50,chosen['snr_db']-6)))
            except Exception:
                recommended=None
            finally:
                try: apply(previous,restoring=True)
                except Exception: pass
                with self.mutex:
                    self.optimization=dict(status='cancelled' if self.cancel_optimize.is_set() else 'complete',
                        previous=previous,recommended=recommended,trials=trials,
                        message='Measured recommendation; original settings restored. Filter and mode remain manual recommendations.' if recommended else 'Insufficient stable unclipped signal for a recommendation; original settings restored when monitoring remains active.')
        self.optimize_thread=threading.Thread(target=work,daemon=True,name='rf-atlas-reception-assist'); self.optimize_thread.start()
