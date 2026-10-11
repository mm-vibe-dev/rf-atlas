"""Continuous opt-in discovery using bounded inventory CLI children.

The application supervisor arbitrates modes. This controller never races a
manual tuner deliberately, and every child still uses the shared device lock.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

from .hardware import RTLBackend
from .inventory import default_profiles, load_profiles, validate_profile
from .platforms import DeviceLock, WindowsJob
from .scanner import ScannerController


def survey_profiles(params, existing=None):
    """Validate browser settings and resolve editable organizational profiles."""
    allowed = {'mode', 'profiles', 'gain', 'integration_s', 'revisit_s'}
    if not isinstance(params, dict) or set(params) - allowed:
        raise ValueError('Unknown Auto Survey setting')
    mode = params.get('mode', 'smart')
    if mode not in ('quick', 'full', 'smart'):
        raise ValueError('Choose quick, full or smart survey')
    for key, low, high in [('integration_s', 1, 30), ('revisit_s', 60, 30 * 86400)]:
        if key in params and (type(params[key]) is not int or not low <= params[key] <= high):
            raise ValueError('Survey timing is outside supported bounds')
    profiles = existing if existing is not None else default_profiles()
    profiles = [validate_profile(p) for p in profiles]
    selected = params.get('profiles')
    if selected is not None:
        if not isinstance(selected, list) or not selected or len(selected) > 64 or any(not isinstance(p, str) for p in selected):
            raise ValueError('Survey profiles must be a nonempty list of identifiers')
        if len(set(selected)) != len(selected):
            raise ValueError('Duplicate survey profile')
    if mode == 'full':
        if selected is not None and set(selected) != {'hf', 'vhf', 'uhf', 'upper'}:
            raise ValueError('Full Spectrum requires the four receiver envelope profiles')
        selected = ['hf', 'vhf', 'uhf', 'upper']
    elif selected is None:
        selected = (['am-broadcast', 'fm-broadcast', 'aviation', 'marine', 'amateur-us',
                     'land-mobile', 'ism-us', 'ads-b', 'satellite', 'weather-us']
                    if mode == 'quick' else [p['id'] for p in profiles if p['enabled'] or p['id'] == 'ads-b'])
        selected = [p for p in selected if p in {v['id'] for v in profiles}]
    if not selected or set(selected) - {p['id'] for p in profiles}:
        raise ValueError('Selected survey profile is unavailable')
    output = []
    for profile in profiles:
        if profile['id'] not in selected:
            continue
        profile = dict(profile, enabled=True)
        if mode == 'full':
            canonical = next(p for p in default_profiles() if p['id'] == profile['id'])
            profile.update(start_hz=canonical['start_hz'], stop_hz=canonical['stop_hz'])
        for key in ('gain', 'integration_s', 'revisit_s'):
            if key in params:
                profile[key] = params[key]
        profile['duration_s'] = max(profile['duration_s'], profile['integration_s'])
        output.append(validate_profile(profile))
    return mode, output


def _save_profiles(path, profiles):
    lines = ['# Private Auto Survey selection; editable inventory profiles live in inventory.toml.']
    for profile in profiles:
        lines.append('\n[[regions]]')
        lines.extend(key + '=' + (str(value).lower() if type(value) is bool else json.dumps(value))
                     for key, value in profile.items())
    temporary = path.with_suffix('.partial')
    temporary.write_text('\n'.join(lines) + '\n', encoding='utf8')
    temporary.replace(path)


class _InventoryChild(ScannerController):
    def _progress(self):
        return {}

    def start(self, params, *, _resume=False):
        with self._lock:
            if self.status()['active']:
                raise RuntimeError('An acquisition is already active')
            try:
                RTLBackend(self.config).tool('rtl_power')
            except RuntimeError as error:
                raise FileNotFoundError('Required acquisition tool unavailable') from error
            with DeviceLock(self.config.lock_root, 'rtl-sdr'):
                pass
            self._parameters = params
            self._started = datetime.now(timezone.utc).isoformat()
            try:
                command = [sys.executable, '-m', 'rfatlas', '--config', str(self._configuration()),
                           'inventory-run', '--profiles', str(params['path']),
                           '--max-steps', '3', '--max-seconds', '180']
                if params['mode'] == 'smart':
                    command.append('--smart')
                if sys.platform.startswith('linux'):
                    command = [sys.executable, str(Path(__file__).with_name('_linux_child.py')),
                               str(os.getpid()), *command]
                if os.name == 'nt':
                    self._job = WindowsJob()
                environment = os.environ.copy()
                environment.update(RF_ATLAS_HOME=str(self.config.root), RF_ATLAS_LOCK_ROOT=str(self.config.lock_root))
                self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment,
                    start_new_session=os.name != 'nt',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if self._job:
                    self._job.assign(self._process)
                self._state = 'running'
                self._return_code = None
                self._timer = threading.Timer(195, self._expire)
                self._timer.daemon = True
                self._timer.start()
                return self.status()
            except BaseException:
                self.stop()
                self._release()
                raise


class AutoSurveyController:
    """Off until start; stop waits for the watcher and owned process tree."""
    def __init__(self, config, config_path=None, *, child=None, poll_seconds=.25):
        self.config = config
        self._child = child or _InventoryChild(config, config_path)
        self._lock = threading.RLock()
        self._event = threading.Event()
        self._thread = None
        self._state = 'idle'
        self._parameters = None
        self._selected = []
        self._cycles = 0
        self._error = None
        self._poll_seconds = poll_seconds

    def _progress(self):
        path = self.config.root / 'data' / 'rf_atlas.sqlite'
        result = {'total_regions': 0, 'visited_regions': 0, 'valid_regions': 0, 'visits': 0}
        if path.is_file():
            try:
                with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.25)) as db:
                    row = db.execute('SELECT count(*),sum(visit_count>0),sum(valid_count>0),sum(visit_count) FROM inventory_regions WHERE enabled=1').fetchone()
                    result = dict(zip(result, [v or 0 for v in row]))
            except sqlite3.Error:
                pass
        result['meaning'] = 'Checkpointed visits under recorded settings; valid coverage does not imply silence or complete discovery.'
        return result

    def status(self):
        with self._lock:
            state = self._child.status()
            return {'supported': True, 'active': self._state == 'running',
                    'acquiring': state['active'], 'status': self._state,
                    'resumable': self._state == 'paused', 'mode': (self._parameters or {}).get('mode', 'smart'),
                    'profiles': list(self._selected), 'cycles_completed': self._cycles,
                    'return_code': state.get('return_code'), 'error': self._error,
                    'progress': self._progress()}

    def start(self, params):
        with self._lock:
            if self._state == 'running' or (self._thread and self._thread.is_alive()):
                raise RuntimeError('Auto Survey is already enabled')
            editable = self.config.root / 'runtime' / 'inventory.toml'
            mode, profiles = survey_profiles(params, load_profiles(editable) if editable.is_file() else None)
            path = self.config.root / 'runtime' / 'auto-survey.toml'
            path.parent.mkdir(parents=True, exist_ok=True)
            _save_profiles(path, profiles)
            self._child.start({'path': path, 'mode': mode})
            self._parameters = dict(params, mode=mode)
            self._selected = [p['id'] for p in profiles]
            self._event.clear()
            self._state = 'running'
            self._error = None
            self._thread = threading.Thread(target=self._watch, args=(path, mode), name='rf-atlas-auto-survey', daemon=True)
            self._thread.start()
            return self.status()

    def _watch(self, path, mode):
        while not self._event.wait(self._poll_seconds):
            with self._lock:
                if self._event.is_set():
                    return
                state = self._child.status()
                if state['active']:
                    continue
                if state['status'] != 'complete':
                    self._state = 'failed'
                    self._error = 'Bounded acquisition failed; inspect local diagnostics and receiver availability.'
                    return
                self._cycles += 1
                # Avoid busy launches when every configured region is on cooldown.
            if self._event.wait(self._idle_delay()):
                return
            with self._lock:
                if self._event.is_set():
                    return
                try:
                    self._child.start({'path': path, 'mode': mode})
                except Exception:
                    self._state = 'failed'
                    self._error = 'Cannot resume bounded acquisition; check receiver and configured tools.'
                    return

    def _idle_delay(self):
        """Avoid launching idle children while all bounded work is on cooldown."""
        path=self.config.root/'data/rf_atlas.sqlite'
        if not path.is_file():return 1
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                due=db.execute('''SELECT min(due) FROM (
                    SELECT next_due AS due FROM inventory_regions WHERE enabled=1 AND status!='running'
                    UNION ALL SELECT next_eligible FROM investigation_jobs WHERE status='pending')''').fetchone()[0]
            return max(1,min(30,due-time.time())) if due is not None else 30
        except sqlite3.Error:return 1

    def stop(self):
        self._event.set()
        with self._lock:
            owned_child_active=self._child.status()['active']
            self._child.stop()
            self._state = 'stopped'
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError('Auto Survey shutdown has not completed')
        with self._lock:
            self._thread = None
            # A terminated child cannot finish its SQLite status transition.
            # Recover only after process-tree shutdown and scheduler ownership.
            if owned_child_active and (self.config.root/'data/rf_atlas.sqlite').is_file():
                from .inventory import InventoryWorker
                from .storage import open_database
                try:
                    with DeviceLock(self.config.lock_root,'inventory-scheduler'), closing(open_database(self.config.root)) as db:
                        InventoryWorker(db,self.config,None,None,None).recover()
                except RuntimeError:
                    self._error='Stopped; interrupted job recovery deferred until the scheduler is available.'
            return self.status()

    def pause(self):
        with self._lock:
            if self._state != 'running':
                raise RuntimeError('Auto Survey is not enabled')
        self.stop()
        with self._lock:
            self._state = 'paused'
            return self.status()

    def resume(self):
        with self._lock:
            if self._state != 'paused' or self._parameters is None:
                raise RuntimeError('No paused Auto Survey is available')
            params = dict(self._parameters)
        return self.start(params)

    close = stop
