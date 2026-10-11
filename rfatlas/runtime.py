"""Single local receiver supervisor and bounded live rtl_power measurements."""
from collections import deque
import copy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
from .autosurvey import AutoSurveyController
from .diagnostics import atomic_json, read_spectra, noise_model
from .hardware import RTLBackend, RTL_V4L_GAINS, gain_value
from .platforms import WindowsJob
from .scanner import ScannerController

DEFAULT_SETTINGS = dict(startup_auto=False, restore_auto=True, retain_frames=False,
                        retain_identifiers=False, local_alerts=False, ai_enabled=False, mode='smart')


def load_settings(root):
    path = Path(root) / 'runtime' / 'app-settings.json'
    if not path.is_file():
        return dict(DEFAULT_SETTINGS)
    try:
        return validate_settings(json.loads(path.read_text(encoding='utf8')))
    except (ValueError, OSError, TypeError):
        return dict(DEFAULT_SETTINGS)


def validate_settings(params):
    if not isinstance(params, dict) or set(params) - set(DEFAULT_SETTINGS):
        raise ValueError('Unknown application setting')
    result = dict(DEFAULT_SETTINGS, **params)
    if result['mode'] not in ('quick', 'full', 'smart') or any(type(result[k]) is not bool for k in DEFAULT_SETTINGS if k != 'mode'):
        raise ValueError('Invalid application setting')
    return result


def tuner_settings(params, previous=None):
    defaults = dict(frequency_hz=100000000, bandwidth_hz=200000, bin_hz=1000,
                    gain='15.7', integration_s=1)
    if not isinstance(params, dict) or set(params) - set(defaults):
        raise ValueError('Unknown tuner setting')
    values = dict(defaults, **(previous or {})); values.update(params)
    for key, low, high in [('frequency_hz', 500000, 1766000000),
                          ('bandwidth_hz', 20000, 2000000), ('bin_hz', 100, 100000),
                          ('integration_s', 1, 5)]:
        value = values[key]
        if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value or not low <= value <= high:
            raise ValueError('Tuner setting outside receiver limits')
        values[key] = int(value)
    if values['bandwidth_hz'] / values['bin_hz'] < 16:
        raise ValueError('Choose finer bins for this bandwidth')
    try: values['gain'] = gain_value(str(values['gain']))
    except Exception as error: raise ValueError('Unsupported manual gain') from error
    if values['gain'] == 'auto':
        raise ValueError('Choose a manual gain for comparable live measurements')
    return values


class LiveTuner:
    """One lock for the whole session; cancellable finite CLI acquisitions.

    Measurements stay in bounded memory. A private temporary CSV is removed on
    stop. This is an integrating spectrum receiver, not an audio demodulator.
    """
    def __init__(self, config):
        self.config = config
        self.backend = RTLBackend(config)
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._process = None
        self._job = None
        self._state = 'idle'
        self._error = None
        self._settings = tuner_settings({})
        self._rows = deque(maxlen=32)
        self._sequence = 0

    def status(self):
        with self._mutex:
            return dict(active=bool(self._thread and self._thread.is_alive()), status=self._state,
                        error=self._error, settings=dict(self._settings), sequence=self._sequence,
                        latest=copy.deepcopy(self._rows[-1]) if self._rows else None,
                        waterfall=copy.deepcopy(list(self._rows)))

    def _kill(self):
        with self._mutex:
            process, job = self._process, self._job
            if process is not None and process.poll() is None:
                if job:
                    job.close(); self._job = None
                elif os.name == 'nt':
                    process.kill()
                else:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                process.wait(timeout=5)

    def stop(self):
        self._stop.set()
        self._kill()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=8)
            if thread.is_alive():
                raise RuntimeError('Receiver shutdown has not completed')
        with self._mutex:
            self._thread = None
            self._state = 'stopped'
        return self.status()

    def start(self, params):
        values = tuner_settings(params, self._settings)
        executable = self.backend.tool('rtl_power')
        self.stop()
        with self._mutex:
            self._settings = values
            self._rows.clear()
            self._error = None
            self._state = 'starting'
            self._stop.clear(); self._ready.clear()
            self._thread = threading.Thread(target=self._run, args=(executable,), daemon=True, name='rf-atlas-live-tuner')
            self._thread.start()
        if not self._ready.wait(3):
            self.stop()
            raise RuntimeError('Receiver initialization timed out')
        if self._error:
            raise RuntimeError(self._error)
        return self.status()

    def _run(self, executable):
        directory = self.config.root / 'runtime'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with self.backend.lock(), tempfile.TemporaryDirectory(prefix='tuner-', dir=directory) as temporary:
                self._state = 'receiving'; self._ready.set()
                path = Path(temporary) / 'spectrum.csv'
                while not self._stop.is_set():
                    s = self._settings
                    low = max(500000, s['frequency_hz'] - s['bandwidth_hz']//2)
                    high = min(1766000000, s['frequency_hz'] + s['bandwidth_hz']//2)
                    command = [str(executable), '-d', self.config.device, '-f', f"{low}:{high}:{s['bin_hz']}",
                               '-i', str(s['integration_s']), '-1', '-c', '20%', '-g', s['gain'], str(path)]
                    if sys.platform.startswith('linux'):
                        command = [sys.executable, str(Path(__file__).with_name('_linux_child.py')), str(os.getpid()), *command]
                    # Spool bounded-duration diagnostics rather than retaining identifiers in status.
                    with tempfile.TemporaryFile() as errors:
                        with self._mutex:
                            if self._stop.is_set(): break
                            self._job = WindowsJob() if os.name == 'nt' else None
                            self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=errors, start_new_session=os.name != 'nt',
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                            if self._job:
                                try: self._job.assign(self._process)
                                except BaseException:
                                    self._process.kill(); self._process.wait(timeout=5)
                                    self._job.close(); self._job=None; self._process=None
                                    raise
                            process = self._process
                        try:
                            code = process.wait(timeout=s['integration_s'] + 15)
                        except subprocess.TimeoutExpired:
                            self._kill()
                            raise RuntimeError('Receiver acquisition timed out')
                        finally:
                            with self._mutex:
                                if self._job: self._job.close()
                                self._job = None; self._process = None
                        if self._stop.is_set(): break
                        if code != 0 or not path.is_file() or path.stat().st_size > 4*1024*1024:
                            raise RuntimeError('Receiver acquisition failed; check connection and existing SDR tools')
                        errors.seek(0)
                        stderr=errors.read(65536).decode('utf8',errors='replace').lower()
                        if any(message in stderr for message in ('failed to set tuner gain','failed to enable manual gain','failed to set sample rate','no supported devices found','failed to open rtlsdr')):
                            raise RuntimeError('Receiver setup failed')
                        rows, report = read_spectra(path)
                        if report['rejected_rows'] or not rows or len(rows) > 128:
                            raise RuntimeError('Receiver returned invalid spectrum data')
                        if sum(len(row['power']) for row in rows)>100000:
                            raise RuntimeError('Spectrum exceeds live display budget')
                        cursor=low
                        ordered=sorted(rows,key=lambda row:row['low'])
                        for index,row in enumerate(ordered):
                            # rtl_power rounds cropped edges to FFT bins. Only
                            # outer edges may differ by one measured bin; an
                            # internal gap still invalidates the measurement.
                            tolerance=max(1,row['step']) if index==0 else 1
                            if row['low']>cursor+tolerance: raise RuntimeError('Gap in spectrum coverage')
                            cursor=max(cursor,row['high'])
                        if cursor<high-max(1,ordered[-1]['step']): raise RuntimeError('Incomplete spectrum coverage')
                        frequencies=[]; powers=[]; floors=[]
                        for row in rows:
                            floor, _ = noise_model(row['power'], row['step'])
                            frequencies.extend(row['centers']); powers.extend(row['power']); floors.extend(floor)
                        order = np.argsort(frequencies)
                        x=np.asarray(frequencies)[order]; y=np.asarray(powers)[order]; n=np.asarray(floors)[order]
                        # Peak-preserving buckets keep the browser/memory bounded.
                        groups = np.array_split(np.arange(len(x)), min(1024,len(x)))
                        idx = [g[np.argmax(y[g])] for g in groups]
                        latest = dict(frequency_hz=x[idx].tolist(), power_db=y[idx].tolist(), noise_db=n[idx].tolist(),
                            observed=rows[-1]['observed'], peak_snr_db=float(np.max(y-n)),
                            noise_estimate_db=float(np.median(n)), actual_step_hz=rows[0]['step'], sample_rate_hz=None,
                            coverage_start_hz=ordered[0]['low'],coverage_stop_hz=cursor)
                        with self._mutex:
                            self._rows.append(latest); self._sequence += 1
                    path.unlink(missing_ok=True)
                    self._stop.wait(.1)
        except Exception:
            with self._mutex:
                self._state = 'failed'
                self._error = 'Receiver unavailable or acquisition failed. Check connection, tool paths and other receiver applications.'
        finally:
            self._kill()
            with self._mutex:
                if self._job: self._job.close()
                self._job=None; self._process=None
            self._ready.set()


class RuntimeSupervisor:
    def __init__(self, config, config_path=None):
        self.config=config
        from datetime import datetime,timezone
        self.started=datetime.now(timezone.utc).isoformat();self._started_clock=time.monotonic()
        self.tuner=LiveTuner(config)
        from .monitor import LiveMonitor
        self.monitor=LiveMonitor(config)
        self.survey=AutoSurveyController(config, config_path)
        self.scanner=ScannerController(config, config_path)
        self.settings=load_settings(config.root)
        from .reanalysis import ReanalysisController
        self.reanalysis=ReanalysisController(config,lambda:dict(self.settings))
        from .deep_controller import DeepController
        self.deep=DeepController(config,lambda:dict(self.settings))
        self._lock=threading.RLock()
        self._restore=False
        self._monitor_restore=False
        self._monitor_scanner=False
        self._closing=threading.Event()
        self._lease_thread=threading.Thread(target=self._monitor_lease,daemon=True,name='rf-atlas-monitor-lease')
        self._lease_thread.start()
        from .identification import default_registry
        self._decoder_capabilities=default_registry(config=config).describe()
        self.receiver=dict(status='unchecked', model='RTL-SDR Blog V4 compatible')

    def status(self):
        with self._lock:
            tuner=self.tuner.status(); survey=self.survey.status(); scanner=self.scanner.status(); monitor=self.monitor.status(include_waterfall=False)
            if tuner['latest'] and tuner['active']:
                self.receiver['status']='available'; self.receiver.pop('error',None)
            if monitor['latest'] and monitor['active']:
                self.receiver['status']='available'; self.receiver.pop('error',None)
            if tuner['status']=='failed': self.receiver.update(status='unavailable',error=tuner['error'])
            if monitor['status']=='failed': self.receiver.update(status='unavailable',error=monitor['error'])
            from . import __version__
            from .ai import status as ai_status
            return dict(scanner,version=__version__,started=self.started,uptime_s=time.monotonic()-self._started_clock,scanner=scanner, monitor=monitor, mode='investigate' if self.deep.status()['active'] else 'monitor' if monitor['active'] else 'tuner' if tuner['active'] else 'survey' if survey['active'] or scanner['active'] else 'idle',
                ai=ai_status(self.config.root),deep_investigation=self.deep.status(),recording_limits=dict(max_capture_bytes=self.config.max_capture_bytes,retention_bytes=self.config.retention_bytes),decoders=self._decoder_capabilities,receiver=dict(self.receiver), tuner=tuner, survey=survey, settings=dict(self.settings),
                unsupported_protocols=['P25','NXDN','D-STAR','C4FM','M17','UAT','ACARS','FT8','FT4','WSPR','RTTY','PSK31','radiosonde','weather-satellite','NRSC-5'],
                capabilities=dict(min_hz=500000,max_hz=1766000000,gains=RTL_V4L_GAINS,
                                  sample_rates=[250000,1024000,2048000,2400000]))

    def action(self, action, payload):
        with self._lock:
            if not isinstance(payload,dict): raise ValueError('JSON object required')
            if action=='deep/start':
                if self.status()['mode']!='idle':raise RuntimeError('Stop acquisition before deep investigation')
                return self.deep.start(payload)
            if action=='deep/cancel':
                if payload:raise ValueError('Empty object required')
                return self.deep.cancel()
            if action=='deep/preset':return self.deep.save_preset(payload)
            if self.deep.status()['active'] and action in ('tuner/start','tuner/tune','survey/start','survey/resume','monitor/start'):
                raise RuntimeError('Cancel or complete deep investigation before starting acquisition')
            if action=='reanalysis/start':return self.reanalysis.start(payload)
            if action=='reanalysis/cancel':
                if payload:raise ValueError('Empty object required')
                return self.reanalysis.cancel()
            if action.startswith('monitor/'):
                return self._monitor_action(action[8:],payload)
            if action in ('tuner/start','tuner/tune'):
                tuner_settings(payload, self.tuner._settings)  # Validate before interrupting acquisition.
                self._restore=self._restore or self._monitor_restore
                self._end_monitor(restore=False)
                if self.survey.status()['active']:
                    self.survey.pause(); self._restore=True
                if self.scanner.status()['active']: self.scanner.pause()
                self.tuner.start(payload)
                self.receiver['status']='checking'
            elif action=='tuner/stop':
                if payload: raise ValueError('Empty object required')
                self.tuner.stop()
                if self._restore and self.settings['restore_auto']:
                    self.survey.resume()
                self._restore=False
            elif action=='survey/start':
                from .autosurvey import survey_profiles
                editable=self.config.root/'runtime/inventory.toml'
                from .inventory import load_profiles
                survey_profiles(payload,load_profiles(editable) if editable.is_file() else None)
                self._end_monitor(restore=False)
                self.tuner.stop(); self.scanner.stop(); self._restore=False
                self.survey.start(payload)
                self.settings['mode']=payload.get('mode','smart'); self._save()
                atomic_json(self.config.root/'runtime/survey-settings.json',dict(payload,mode=self.settings['mode']))
            elif action=='survey/pause':
                if payload: raise ValueError('Empty object required')
                self.survey.pause();self._restore=False
            elif action=='survey/resume':
                if payload: raise ValueError('Empty object required')
                if not self.survey.status().get('resumable',self.survey.status().get('status')=='paused'): raise RuntimeError('No paused survey')
                self._end_monitor(restore=False)
                self.tuner.stop();self.scanner.stop();self._restore=False;self.survey.resume()
            elif action=='survey/stop':
                if payload: raise ValueError('Empty object required')
                self._restore=False; self.survey.stop()
                self._monitor_restore=False
            elif action=='settings':
                self.settings=validate_settings(dict(self.settings,**payload)); self._save()
            elif action=='probe':
                if payload: raise ValueError('Empty object required')
                if self.status()['mode']!='idle': raise RuntimeError('Stop acquisition before checking receiver')
                try:
                    backend=RTLBackend(self.config)
                    with backend.lock(): backend.run([backend.tool('rtl_test'),'-d',self.config.device,'-t'],20)
                    self.receiver=dict(status='available',model='RTL-SDR Blog V4 compatible',message='Configured local receiver opened successfully.')
                except Exception:
                    self.receiver=dict(status='unavailable',model='RTL-SDR Blog V4 compatible',error='Receiver check failed. Check existing tool paths, USB connection and competing applications.')
            else: raise ValueError('Unknown runtime action')
            return self.status()

    def _monitor_action(self, action, payload):
        from .monitor import settings
        if action=='start':
            import sqlite3
            from contextlib import closing
            if set(payload)!={'signal_id'} or not isinstance(payload['signal_id'],str):
                raise ValueError('Inventory signal required')
            if self.monitor.optimize_thread and self.monitor.optimize_thread.is_alive():
                raise RuntimeError('Finish or cancel reception optimization first')
            with closing(sqlite3.connect((self.config.root/'data/rf_atlas.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                row=db.execute('SELECT center_frequency_hz,occupied_bandwidth_hz FROM signals WHERE signal_id=?',(payload['signal_id'],)).fetchone()
            if row is None: raise ValueError('Signal not found')
            frequency=int(round(row[0])); mode='WFM' if 87500000<=frequency<=108000000 else 'AM' if frequency<2000000 or 118000000<=frequency<=137000000 else 'NFM'
            values=settings(dict(frequency_hz=frequency,mode=mode,offset_hz=0),self.monitor.selected)
            preset=self.monitor.preset(payload['signal_id'])
            if preset: values=settings(preset['settings'])
            self._monitor_restore=self._monitor_restore or self._restore; self._restore=False
            self.tuner.stop()
            if self.survey.status()['active']: self.survey.pause(); self._monitor_restore=True
            if self.scanner.status()['active']: self.scanner.pause(); self._monitor_scanner=True
            try: self.monitor.start(values,payload['signal_id'])
            except Exception:
                self._end_monitor(restore=True); raise
        elif action=='settings':
            if not self.monitor.status()['active']: raise RuntimeError('Monitor inactive')
            if self.monitor.optimization and self.monitor.optimization.get('status')=='running':
                raise RuntimeError('Cancel optimization before changing reception')
            self.monitor.update(payload); self.monitor.heartbeat()
        else:
            if payload: raise ValueError('Empty object required')
            if action=='stop': self._end_monitor(restore=True)
            elif action=='heartbeat': self.monitor.heartbeat()
            elif action=='preset': self.monitor.save_preset()
            elif action=='decode': self.monitor.decode(self.settings['retain_frames'],self.settings['retain_identifiers'])
            elif action=='optimize':
                def apply(selected,restoring=False):
                    with self._lock:
                        if not self.monitor.status()['active'] or (self.monitor.cancel_optimize.is_set() and not restoring):
                            raise RuntimeError('Monitoring ended or optimization cancelled')
                        self.monitor.update(selected)
                self.monitor.optimize(apply)
            elif action=='optimize-cancel': self.monitor.cancel_optimize.set()
            elif action in ('optimize-apply','optimize-restore'):
                result=self.monitor.optimization or {}
                selected=result.get('recommended' if action=='optimize-apply' else 'previous')
                if not selected or result.get('status')=='running': raise RuntimeError('No completed recommendation')
                self.monitor.update(selected)
            else: raise ValueError('Unknown monitoring action')
        return self.status()

    def _end_monitor(self, restore):
        if self.monitor.status()['active'] or self._monitor_restore or self._monitor_scanner:
            snapshot=self.monitor.status(); self.monitor.stop()
            if snapshot['started']:
                path=self.config.root/'runtime/monitor-sessions.json'
                try:
                    sessions=json.loads(path.read_text(encoding='utf8')) if path.is_file() and path.stat().st_size<2_000_000 else []
                    if not isinstance(sessions,list): sessions=[]
                except (ValueError,OSError): sessions=[]
                from datetime import datetime,timezone
                sessions.append(dict(signal_id=snapshot['signal_id'],started=snapshot['started'],ended=datetime.now(timezone.utc).isoformat(),
                    settings=snapshot['settings'],metrics=snapshot['metrics'],protocol=(snapshot['evidence'] or {}).get('protocol','UNKNOWN')))
                atomic_json(path,sessions[-100:])
            if restore and self.settings['restore_auto']:
                if self._monitor_restore: self.survey.resume()
                elif self._monitor_scanner: self.scanner.resume()
            self._monitor_restore=False; self._monitor_scanner=False

    def _monitor_lease(self):
        while not self._closing.wait(1):
            try:
                with self._lock:
                    state=self.monitor.status(include_waterfall=False)
                    if (state['active'] and time.monotonic()-self.monitor.last_heartbeat>30) or (state['status']=='failed' and (self._monitor_restore or self._monitor_scanner)):
                        self._end_monitor(restore=True)
                    elif state['active'] and state['settings']['decode_enabled'] and time.monotonic()-self.monitor.last_decode>=20 and len(self.monitor.iq)>=10:
                        if not (self.monitor.decode_thread and self.monitor.decode_thread.is_alive()) and not (self.monitor.optimize_thread and self.monitor.optimize_thread.is_alive()):
                            self.monitor.decode(self.settings['retain_frames'],self.settings['retain_identifiers'])
            except Exception:
                self.receiver['error']='Monitoring stopped; automatic survey restoration unavailable.'

    def _save(self):
        atomic_json(self.config.root/'runtime/app-settings.json',self.settings)

    def startup(self):
        self.action('probe',{})
        if self.settings['startup_auto']:
            try:
                path=self.config.root/'runtime/survey-settings.json'
                selected=json.loads(path.read_text(encoding='utf8')) if path.is_file() and path.stat().st_size<=4096 else {}
                if not isinstance(selected,dict) or selected.get('mode')!=self.settings['mode']: selected={'mode':self.settings['mode']}
                self.action('survey/start',selected)
            except Exception:
                self.receiver['error']='Startup survey unavailable. Check receiver and configured tools.'

    # Existing bounded Scanner controls also pass through this owner.
    def start(self, params):
        from .scanner import scan_arguments
        scan_arguments(params)
        with self._lock:
            self._end_monitor(restore=False)
            self.tuner.stop(); self.survey.stop(); self._restore=False
            return self.scanner.start(params)

    def stop(self):
        with self._lock: return self.scanner.stop()

    def pause(self):
        with self._lock: return self.scanner.pause()

    def resume(self):
        with self._lock:
            self._end_monitor(restore=False)
            self.tuner.stop(); self.survey.stop(); self._restore=False
            return self.scanner.resume()

    def close(self):
        self._closing.set()
        self.reanalysis.close()
        self.deep.close()
        with self._lock:
            self._end_monitor(restore=False)
            self._restore=False
            self.tuner.stop(); self.survey.close(); self.scanner.stop()
