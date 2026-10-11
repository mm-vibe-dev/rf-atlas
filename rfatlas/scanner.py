"""Bounded dashboard acquisition controller using the existing survey CLI."""
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from contextlib import closing

from .hardware import RTLBackend, gain_value
from .platforms import DeviceLock, WindowsJob


def scan_arguments(params, *, resume=False):
    """Build fixed receive-only arguments; never accept arbitrary CLI options."""
    allowed = {'start_hz', 'stop_hz', 'bin_hz', 'seconds', 'duration', 'gain', 'profile'}
    if not isinstance(params, dict) or set(params) - allowed:
        raise ValueError('Unknown scanner parameters')
    values = {'bin_hz': 1000, 'seconds': 1, 'duration': 10, 'gain': '15.7', 'profile': 'balanced', **params}
    if values['profile'] not in ('quick', 'balanced', 'deep'):
        raise ValueError('Unsupported survey profile')
    for key in ('start_hz', 'stop_hz', 'bin_hz', 'seconds', 'duration'):
        value = values.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value:
            raise ValueError('Scanner settings require finite integer values')
        values[key] = int(value)
    if not 500000 <= values['start_hz'] < values['stop_hz'] <= 1766000000:
        raise ValueError('Frequency range outside receiver limits')
    if not 100 <= values['bin_hz'] <= 1000000 or not 1 <= values['seconds'] <= 60:
        raise ValueError('Acquisition settings outside approved bounds')
    if not 1 <= values['duration'] <= 300 or values['duration'] < values['seconds']:
        raise ValueError('Duration must include at least one integration')
    regions = math.ceil((values['stop_hz'] - values['start_hz']) / 2000000)
    hf_regions=min(regions,max(0,math.ceil((min(values['stop_hz'],30000000)-values['start_hz'])/2000000)))
    budget=(regions-hf_regions)*(values['duration']+2)+hf_regions*(2*values['duration']+4*math.ceil(values['duration']/8))
    if budget > 300:
        raise ValueError('Reduce range or duration to keep the whole scan bounded')
    try:
        gain = gain_value(str(values['gain']))
    except Exception as error:
        raise ValueError('Unsupported tuner gain') from error
    argv = ['survey', *([] if resume else ['--new']), '--profile', values['profile'], '--start', str(values['start_hz']), '--stop', str(values['stop_hz']),
            '--bin-hz', str(values['bin_hz']), '--integration', str(values['seconds']),
            '--duration', str(values['duration']), '--gain', gain, '--margin', '6']
    return argv, budget+60


class ScannerController:
    """One owned child; the child independently acquires the shared device lock.

    stdout/stderr are discarded here; the ordinary survey CLI retains its own
    private diagnostics. Public status never contains device identity or paths.
    """
    def __init__(self, config, config_path=None):
        self.config = config
        self.config_path = Path(config_path).resolve() if config_path else None
        self._lock = threading.RLock()
        self._process = None
        self._job = None
        self._timer = None
        self._snapshot = None
        self._started = None
        self._state = 'idle'
        self._return_code = None
        self._parameters = None

    def _progress(self):
        result = {'current_region': None, 'progress': None}
        path = self.config.root / 'data' / 'rf_atlas.sqlite'
        if not path.is_file() or not self._parameters:
            return result
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                run = db.execute('SELECT run_id,status FROM survey_runs WHERE start_hz=? AND stop_hz=? AND profile=? AND started>=? ORDER BY started DESC LIMIT 1',
                    (self._parameters['start_hz'], self._parameters['stop_hz'], self._parameters.get('profile', 'balanced'), self._started)).fetchone()
                if run:
                    rows = db.execute('SELECT start_hz,stop_hz,status FROM scan_regions WHERE run_id=? ORDER BY start_hz', (run[0],)).fetchall()
                    complete = sum(row[2] == 'complete' for row in rows)
                    result['progress'] = {'completed_regions': complete, 'total_regions': len(rows), 'fraction': complete / len(rows) if rows else 0,
                                          'meaning': 'Checkpointed regions; this is not completeness of signal discovery'}
                    pending = next((row for row in rows if row[2] != 'complete'), None)
                    if pending and self._process is not None:
                        result['current_region'] = {'start_hz': pending[0], 'stop_hz': pending[1], 'status': 'next or in progress'}
        except sqlite3.Error:
            pass
        return result

    def _release(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._job:
            self._job.close()
            self._job = None
        if self._snapshot:
            self._snapshot.unlink(missing_ok=True)
            self._snapshot = None

    def status(self):
        with self._lock:
            if self._process is not None and self._process.poll() is not None:
                self._return_code = self._process.returncode
                self._process = None
                if self._state == 'running':
                    self._state = 'complete' if self._return_code == 0 else 'failed'
                self._release()
            return {'supported': True, 'active': self._process is not None, 'resumable': self._state == 'paused',
                    'status': self._state, 'started': self._started,
                    'selected_profile': (self._parameters or {}).get('profile', 'balanced'),
                    **self._progress(),
                    'return_code': self._return_code, 'hardware': 'RTL-SDR (availability checked on start)'}

    def _configuration(self):
        if self.config_path:
            if not self.config_path.is_file():
                raise FileNotFoundError('Configuration unavailable')
            return self.config_path
        directory = self.config.root / 'runtime'
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix='scanner-', suffix='.toml', dir=directory)
        self._snapshot = Path(filename)
        quote = lambda value: json.dumps(str(value), ensure_ascii=True)
        text = ['[runtime]', 'root=' + quote(self.config.root),
                'max_capture_bytes=' + str(self.config.max_capture_bytes),
                'retention_bytes=' + str(self.config.retention_bytes), '[hardware]',
                'device=' + quote(self.config.device), '[tools]']
        text.extend(name + '=' + quote(value) for name, value in self.config.tools.items())
        text.append('[decoders]')
        text.extend(name+'='+quote(value) for name,value in self.config.decoders.items())
        with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
            file.write('\n'.join(text) + '\n')
        return self._snapshot

    def start(self, params, *, _resume=False):
        argv, timeout = scan_arguments(params, resume=_resume)
        with self._lock:
            if self.status()['active']:
                raise RuntimeError('An acquisition is already active')
            try:
                self._parameters = dict(params)
                if not _resume:
                    self._started = datetime.now(timezone.utc).isoformat()
                RTLBackend(self.config).tool('rtl_power')
            except RuntimeError as error:
                raise FileNotFoundError('Required acquisition tool unavailable') from error
            with DeviceLock(self.config.lock_root, 'rtl-sdr'):
                pass  # Child acquires the actual lock; racing CLI starts remain safe.
            try:
                config_path = self._configuration()
                command = [sys.executable, '-m', 'rfatlas', '--config', str(config_path), *argv]
                if sys.platform.startswith('linux'):
                    command = [sys.executable, str(Path(__file__).with_name('_linux_child.py')),
                               str(os.getpid()), *command]
                if os.name == 'nt':
                    self._job = WindowsJob()
                environment = os.environ.copy()
                environment['RF_ATLAS_HOME'] = str(self.config.root)
                environment['RF_ATLAS_LOCK_ROOT'] = str(self.config.lock_root)
                self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=os.name != 'nt',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    env=environment)
                if self._job:
                    self._job.assign(self._process)
                self._state = 'running'
                self._return_code = None
                self._timer = threading.Timer(timeout, self._expire)
                self._timer.daemon = True
                self._timer.start()
                return self.status()
            except BaseException:
                if self._process:
                    if self._process.poll() is None:
                        self._process.kill()
                    self.stop()
                self._release()
                raise

    def pause(self):
        with self._lock:
            if not self.status()['active']:
                raise RuntimeError('No survey is running')
            self.stop()
            self._state = 'paused'
            return self.status()

    def resume(self):
        with self._lock:
            if self.status()['active'] or self._state != 'paused' or self._parameters is None:
                raise RuntimeError('No paused survey is available')
            return self.start(self._parameters, _resume=True)

    def _expire(self):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self.stop()
                self._state = 'timed_out'

    def stop(self):
        with self._lock:
            self.status()
            process = self._process
            if process is not None and process.poll() is None:
                if os.name == 'nt':
                    if self._job:
                        self._job.close()
                        self._job = None
                    else:
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if os.name != 'nt':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait(timeout=3)
                self._return_code = process.returncode
                self._state = 'stopped'
            self._process = None
            if self._state == 'paused':
                self._state = 'stopped'
            self._release()
            return self.status()
