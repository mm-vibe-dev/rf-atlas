"""Runtime ownership and live measurements using synthetic child processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from rfatlas.config import Config
from rfatlas.platforms import DeviceLock
from rfatlas.runtime import (DEFAULT_SETTINGS, LiveTuner, RuntimeSupervisor,
                             load_settings, tuner_settings, validate_settings)


class Controller:
    def __init__(self, name, calls):
        self.name=name; self.calls=calls; self.state='idle'; self.fail_pause=False
        self._settings=tuner_settings({})
    def status(self):
        if self.name=='scanner':
            return {'active':self.state=='running','status':self.state}
        return {'active':self.state=='running','status':self.state,'settings':dict(self._settings),
                'latest':None,'error':None}
    def start(self, params):
        self.calls.append((self.name,'start',params));self.state='running'
        return self.status()
    def stop(self):
        self.calls.append((self.name,'stop'));self.state='stopped'
        return self.status()
    def pause(self):
        if self.fail_pause: raise RuntimeError('Synthetic shutdown failed')
        self.calls.append((self.name,'pause'));self.state='paused'
        return self.status()
    def resume(self):
        self.calls.append((self.name,'resume'));self.state='running'
        return self.status()
    close=stop


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.config=Config(self.root,lock_root=self.root/'locks')
        self.calls=[]
        self.runtime=RuntimeSupervisor(self.config)
        self.runtime.tuner=Controller('tuner',self.calls)
        self.runtime.survey=Controller('survey',self.calls)
        self.runtime.scanner=Controller('scanner',self.calls)
        self.addCleanup(self.runtime.close)

    def test_fresh_startup_off_settings_persist_and_invalid_files_safe(self):
        self.assertEqual(load_settings(self.root),DEFAULT_SETTINGS)
        self.runtime.startup()
        self.assertEqual(self.calls,[])
        self.runtime.action('settings',{'startup_auto':True,'mode':'quick','restore_auto':False})
        loaded=load_settings(self.root)
        self.assertTrue(loaded['startup_auto']);self.assertFalse(loaded['restore_auto'])
        self.assertFalse(loaded['retain_frames']);self.assertFalse(loaded['retain_identifiers'])
        self.runtime.startup()
        self.assertEqual(self.calls[-1],('survey','start',{'mode':'quick'}))
        path=self.root/'runtime/app-settings.json'
        for bad in ['{invalid',json.dumps({'startup_auto':'true'}),json.dumps({'secret':'unsupported'})]:
            path.write_text(bad,encoding='utf8')
            self.assertEqual(load_settings(self.root),DEFAULT_SETTINGS)

    def test_selected_survey_settings_restore_only_with_explicit_startup(self):
        selected={'mode':'quick','profiles':['ads-b'],'gain':20.7,'integration_s':2,'revisit_s':120}
        self.runtime.action('survey/start',selected)
        self.runtime.close(); self.calls.clear()
        restored=RuntimeSupervisor(self.config)
        restored.tuner=Controller('tuner',self.calls)
        restored.survey=Controller('survey',self.calls)
        restored.scanner=Controller('scanner',self.calls)
        try:
            restored.startup();self.assertEqual(self.calls,[])
            restored.action('settings',{'startup_auto':True})
            restored.startup();self.assertEqual(self.calls[-1],('survey','start',selected))
            restored.action('settings',{'mode':'full'})
            restored.survey.stop();restored.startup()
            self.assertEqual(self.calls[-1],('survey','start',{'mode':'full'}))
        finally:restored.close()

    def test_invalid_tuning_and_survey_requests_preserve_owner(self):
        self.runtime.survey.state='running'
        for payload in [{'frequency_hz':499999},{'frequency_hz':1766000001},
                        {'frequency_hz':float('nan')},{'integration_s':True},
                        {'bandwidth_hz':20000,'bin_hz':10000},{'gain':'unsupported'},
                        {'gain':'auto'},{'shell':'arbitrary'}]:
            with self.assertRaises(ValueError):
                self.runtime.action('tuner/tune',payload)
            self.assertEqual(self.calls,[])
            self.assertEqual(self.runtime.status()['mode'],'survey')
        with self.assertRaises(ValueError):self.runtime.action('survey/start',{'mode':'unsupported'})
        self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):self.runtime.start({'start_hz':1,'stop_hz':2})
        self.assertEqual(self.calls,[])

    def test_survey_manual_switch_restore_and_operator_stop(self):
        self.runtime.action('survey/start',{'mode':'smart'})
        self.calls.clear()
        self.runtime.action('tuner/start',{'frequency_hz':100000000})
        self.assertEqual([c[:2] for c in self.calls],[('survey','pause'),('tuner','start')])
        self.assertEqual(self.runtime.status()['mode'],'tuner')
        self.calls.clear()
        self.runtime.action('tuner/stop',{})
        self.assertEqual(self.calls,[('tuner','stop'),('survey','resume')])
        self.runtime.action('tuner/start',{})
        self.runtime.action('survey/stop',{})
        self.calls.clear()
        self.runtime.action('tuner/stop',{})
        self.assertEqual(self.calls,[('tuner','stop')])

    def test_restore_disabled_and_shutdown_failure_never_launches_tuner(self):
        self.runtime.survey.state='running';self.runtime.survey.fail_pause=True
        with self.assertRaises(RuntimeError):self.runtime.action('tuner/start',{})
        self.assertEqual(self.calls,[])
        self.runtime.survey.fail_pause=False
        self.runtime.action('settings',{'restore_auto':False})
        self.runtime.action('tuner/start',{})
        self.calls.clear();self.runtime.action('tuner/stop',{})
        self.assertEqual(self.calls,[('tuner','stop')])
        self.assertEqual(self.runtime.survey.state,'paused')

    def test_scanner_transitions_and_strict_actions(self):
        params={'start_hz':100000000,'stop_hz':101000000}
        self.runtime.start(params)
        self.assertEqual([c[:2] for c in self.calls],[('tuner','stop'),('survey','stop'),('scanner','start')])
        self.calls.clear();self.runtime.action('tuner/start',{})
        self.assertEqual([c[:2] for c in self.calls],[('scanner','pause'),('tuner','start')])
        for action,payload in [('tuner/stop',{'extra':True}),('survey/stop',{'extra':True}),
                               ('settings',{'startup_auto':1}),('unknown',{}),('settings',[])]:
            with self.assertRaises(ValueError):self.runtime.action(action,payload)
        with self.assertRaises(RuntimeError):self.runtime.action('probe',{})
        self.runtime.close()
        self.assertEqual(self.runtime.status()['mode'],'idle')
        for bad in [{'mode':'bad'},{'retain_frames':'yes'},{'unexpected':False}]:
            with self.assertRaises(ValueError):validate_settings(bad)


class LiveTunerTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.config=Config(self.root,lock_root=self.root/'locks')
        self.tuner=LiveTuner(self.config)
        self.addCleanup(self.tuner.stop)
        self.real_popen=subprocess.Popen
        self.processes=[]

    def fake_tool(self, *, wrong_coverage=False, setup_error=False, block=False, edge_bins=0):
        # This fixture executes Python, never an SDR tool, with the same owned
        # process/job lifecycle as the receiver's subprocess.
        def launch(command, **kwargs):
            destination=command[-1]
            low,high,step=map(int,command[command.index('-f')+1].split(':'))
            if wrong_coverage:low,high=120000000,120200000
            low+=edge_bins*step;high-=edge_bins*step
            count=int((high-low)//step)
            source=("import pathlib,sys,time; " +
                    ("time.sleep(30); " if block else "") +
                    ("sys.stderr.write('Failed to set tuner gain\\n'); " if setup_error else "") +
                    "pathlib.Path(sys.argv[1]).write_text(sys.argv[2],encoding='utf8')")
            row=f'2026-01-01, 00:00:00, {low}, {high}, {step}, 1, '+','.join('-35' if n==count//2 else '-60' for n in range(count))+'\n'
            process=self.real_popen([sys.executable,'-c',source,str(destination),row],**kwargs)
            self.processes.append(process)
            return process
        return patch('rfatlas.runtime.subprocess.Popen',side_effect=launch)

    def wait_for(self, predicate, seconds=5):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            state=self.tuner.status()
            if predicate(state):return state
            time.sleep(.02)
        self.fail('Synthetic receiver did not reach expected state')

    def test_real_synthetic_child_live_measurements_and_clean_shutdown(self):
        with patch.object(self.tuner.backend,'tool',return_value=Path(sys.executable)),self.fake_tool():
            self.tuner.start({'frequency_hz':100000000})
            state=self.wait_for(lambda s:s['sequence']>=3)
            latest=state['latest']
            self.assertLessEqual(len(latest['frequency_hz']),1024)
            self.assertEqual(len(latest['power_db']),len(latest['noise_db']))
            self.assertGreater(latest['peak_snr_db'],20)
            self.assertIsNone(latest['sample_rate_hz'])
            self.assertEqual(self.tuner._rows.maxlen,32)
            self.tuner.stop()
            self.assertTrue(all(p.poll() is not None for p in self.processes))
            self.assertFalse(list((self.root/'runtime').glob('tuner-*')))
            with DeviceLock(self.config.lock_root,'rtl-sdr'):pass

    def test_cancel_blocked_child_releases_receiver(self):
        with patch.object(self.tuner.backend,'tool',return_value=Path(sys.executable)),self.fake_tool(block=True):
            self.tuner.start({})
            deadline=time.monotonic()+2
            while not self.processes and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(self.processes)
            started=time.monotonic();self.tuner.stop()
            self.assertLess(time.monotonic()-started,3)
            self.assertTrue(all(p.poll() is not None for p in self.processes))
            self.assertFalse(self.tuner.status()['active'])
            with DeviceLock(self.config.lock_root,'rtl-sdr'):pass

    def test_external_receiver_owner_prevents_child_launch(self):
        with patch.object(self.tuner.backend,'tool',return_value=Path(sys.executable)), \
             patch('rfatlas.runtime.subprocess.Popen') as launch, \
             DeviceLock(self.config.lock_root,'rtl-sdr'):
            with self.assertRaises(RuntimeError):self.tuner.start({})
            launch.assert_not_called()
        self.tuner.stop()
        with DeviceLock(self.config.lock_root,'rtl-sdr'):pass

    def test_unrelated_spectrum_and_zero_exit_setup_error_are_rejected(self):
        for params in [{'wrong_coverage':True},{'setup_error':True}]:
            with self.subTest(params=params),patch.object(self.tuner.backend,'tool',return_value=Path(sys.executable)),self.fake_tool(**params):
                self.tuner.start({})
                state=self.wait_for(lambda s:s['status']=='failed')
                self.assertIsNone(state['latest'])
                self.assertNotIn('Failed to set tuner gain',state['error'])
                self.tuner.stop()

    def test_cropped_edge_rounding_preserves_actual_bounds(self):
        for edge,valid in [(.5,True),(2,False)]:
            with self.subTest(edge=edge),patch.object(self.tuner.backend,'tool',return_value=Path(sys.executable)),self.fake_tool(edge_bins=edge):
                previous=self.tuner.status()['sequence']
                self.tuner.start({})
                state=self.wait_for(lambda s:s['sequence']>previous or s['status']=='failed')
                self.assertEqual(state['sequence']>previous,valid)
                if valid:
                    self.assertEqual(state['latest']['coverage_start_hz'],99900500)
                    self.assertEqual(state['latest']['coverage_stop_hz'],100099500)
                self.tuner.stop()

    @unittest.skipUnless(os.name=='nt','Windows Job assignment failure is Windows-specific')
    def test_job_assignment_failure_reaps_unassigned_child(self):
        class BrokenJob:
            def assign(self, process):raise OSError('Synthetic Job assignment failure')
            def close(self):pass
        with patch.object(self.tuner.backend,'tool',return_value=Path(sys.executable)), \
             patch('rfatlas.runtime.WindowsJob',return_value=BrokenJob()),self.fake_tool(block=True):
            try:self.tuner.start({})
            except RuntimeError:pass
            self.wait_for(lambda s:s['status']=='failed')
            deadline=time.monotonic()+3
            while any(p.poll() is None for p in self.processes) and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue(self.processes)
            self.assertTrue(all(p.poll() is not None for p in self.processes))
            self.tuner.stop()
            with DeviceLock(self.config.lock_root,'rtl-sdr'):pass


if __name__=='__main__':unittest.main()
