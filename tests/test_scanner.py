"""Scanner ownership tests with injected fake children; no hardware launched."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rfatlas.config import Config
from rfatlas.platforms import DeviceLock
from rfatlas.scanner import ScannerController, scan_arguments


class FakeProcess:
    def __init__(self):
        self.pid = 99999999
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9

    def wait(self, timeout):
        self.returncode = self.returncode if self.returncode is not None else -15
        return self.returncode


class FakeJob:
    def __init__(self):
        self.process = None
        self.closed = False

    def assign(self, process):
        self.process = process

    def close(self):
        self.closed = True
        if self.process is not None:
            self.process.kill()


class ScannerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.config = Config(root, lock_root=root / 'locks', tools={'rtl_power': 'synthetic-tool'})
        self.params = {'start_hz': 100000000, 'stop_hz': 101000000, 'duration': 10, 'gain': 15.7}
        self.controller = ScannerController(self.config)
        self.process = FakeProcess()
        self.job = FakeJob()
        self.tool = patch('rfatlas.scanner.RTLBackend.tool', return_value=Path('synthetic-tool')).start()
        self.popen = patch('rfatlas.scanner.subprocess.Popen', return_value=self.process).start()
        patch('rfatlas.scanner.WindowsJob', return_value=self.job).start()
        patch('rfatlas.scanner.os.killpg', create=True).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.controller.stop)

    def test_fixed_arguments_and_total_budget(self):
        args, timeout = scan_arguments(self.params)
        self.assertEqual(args[0:2], ['survey', '--new'])
        self.assertEqual(args[-2:], ['--margin', '6'])
        self.assertLessEqual(timeout, 360)
        for change in [{'duration': 0}, {'duration': 301}, {'duration': 1, 'seconds': 2},
                       {'start_hz': 100000000.1}, {'gain': 1.01}, {'seconds': True},
                       {'stop_hz': 1766000000, 'duration': 10}, {'extra': 'shell argument'}]:
            with self.assertRaises(ValueError):
                scan_arguments(self.params | change)

    def test_start_stop_and_snapshot_cleanup(self):
        state = self.controller.start(self.params)
        self.assertTrue(state['active'])
        self.assertEqual(state['status'], 'running')
        command = self.popen.call_args.args[0]
        self.assertIn('rfatlas', command)
        self.assertIn('survey', command)
        snapshot = self.controller._snapshot
        self.assertTrue(snapshot.is_file())
        environment = self.popen.call_args.kwargs['env']
        self.assertEqual(environment['RF_ATLAS_LOCK_ROOT'], str(self.config.lock_root))
        self.assertEqual(environment['RF_ATLAS_HOME'], str(self.config.root))
        self.assertNotIn('synthetic-tool', str(state))
        with self.assertRaises(RuntimeError):
            self.controller.start(self.params)
        self.assertEqual(self.popen.call_count, 1)
        self.assertEqual(self.controller.stop()['status'], 'stopped')
        self.assertFalse(snapshot.exists())
        self.assertFalse(self.controller.status()['active'])

    def test_missing_tools_and_external_device_owner(self):
        self.tool.side_effect = RuntimeError('missing executable')
        with self.assertRaises(FileNotFoundError):
            self.controller.start(self.params)
        self.popen.assert_not_called()
        self.tool.side_effect = None
        with DeviceLock(self.config.lock_root, 'rtl-sdr'):
            with self.assertRaises(RuntimeError):
                self.controller.start(self.params)
        self.popen.assert_not_called()

    def test_pause_resume_preserves_parameters_and_checkpoints(self):
        params = self.params | {'profile': 'deep', 'bin_hz': 2500}
        with self.assertRaises(RuntimeError):
            self.controller.resume()
        self.controller.start(params)
        self.assertIn('--new', self.popen.call_args.args[0])
        self.assertEqual(self.controller.pause()['status'], 'paused')
        self.assertTrue(self.controller.status()['resumable'])
        self.process.returncode = None
        self.controller.resume()
        argv = self.popen.call_args.args[0]
        self.assertNotIn('--new', argv)
        self.assertEqual(argv[argv.index('--profile') + 1], 'deep')
        self.assertEqual(argv[argv.index('--bin-hz') + 1], '2500')
        self.controller.pause()
        self.assertEqual(self.controller.stop()['status'], 'stopped')
        with self.assertRaises(RuntimeError):
            self.controller.resume()

    def test_progress_reads_matching_run_and_separates_completion(self):
        from rfatlas.storage import open_database
        from contextlib import closing
        self.controller.start(self.params)
        with closing(open_database(self.config.root)) as db, db:
            db.execute("INSERT INTO antenna_profiles VALUES('synthetic','test')")
            db.execute("INSERT INTO survey_runs VALUES('synthetic','9999',NULL,'running',100000000,101000000,'balanced','synthetic','{}')")
            db.execute("INSERT INTO scan_regions VALUES('synthetic',100000000,100500000,'complete','9999',NULL)")
            db.execute("INSERT INTO scan_regions VALUES('synthetic',100500000,101000000,'pending',NULL,NULL)")
        state = self.controller.status()
        self.assertEqual(state['selected_profile'], 'balanced')
        self.assertEqual(state['progress']['fraction'], .5)
        self.assertEqual(state['current_region']['start_hz'], 100500000)
        self.assertIn('not completeness', state['progress']['meaning'])

    def test_child_completion_failure_and_deadline(self):
        self.controller.start(self.params)
        self.process.returncode = 0
        self.assertEqual(self.controller.status()['status'], 'complete')
        self.assertEqual(self.controller.stop()['status'], 'complete')
        self.assertFalse(self.controller.status()['active'])
        self.process.returncode = None
        self.controller.start(self.params)
        self.process.returncode = 1
        self.assertEqual(self.controller.status()['status'], 'failed')
        self.process.returncode = None
        self.controller.start(self.params)
        self.controller._expire()
        self.assertEqual(self.controller.status()['status'], 'timed_out')
        self.assertFalse(self.controller.status()['active'])

    def test_launch_failure_releases_private_snapshot(self):
        self.popen.side_effect = OSError('launch failed')
        with self.assertRaises(OSError):
            self.controller.start(self.params)
        self.assertIsNone(self.controller._snapshot)
        self.assertFalse(list((self.config.root / 'runtime').glob('scanner-*.toml')))


if __name__ == '__main__':
    unittest.main()
