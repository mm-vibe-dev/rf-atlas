import sys
from pathlib import Path
import tempfile
import time
import unittest

from rfatlas.decoder_process import run_decoder_process


class DecoderProcessTests(unittest.TestCase):
    def run_python(self, source, timeout=2, **kwargs):
        return run_decoder_process([sys.executable, '-c', source], timeout, **kwargs)

    def test_success_separate_streams(self):
        out, error = self.run_python('import sys; print("valid"); print("diagnostic", file=sys.stderr)')
        self.assertEqual(out.strip(), 'valid')
        self.assertEqual(error.strip(), 'diagnostic')

    def test_timeout_reaps_child(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.run_python('import time; time.sleep(10)', timeout=.1)
        self.assertLess(time.monotonic() - started, 3)

    def test_timeout_terminates_descendant(self):
        with tempfile.TemporaryDirectory(prefix='rfatlas-child-test-') as directory:
            marker = Path(directory) / 'survived.txt'
            child_source = f'import time;from pathlib import Path;time.sleep(.6);Path({str(marker)!r}).write_text("orphan")'
            parent_source = f'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",{child_source!r}]);time.sleep(10)'
            with self.assertRaises(TimeoutError):
                self.run_python(parent_source, timeout=.2)
            time.sleep(.7)
            self.assertFalse(marker.exists())

    def test_stdout_and_stderr_quota(self):
        for descriptor in (1, 2):
            with self.subTest(descriptor=descriptor), self.assertRaisesRegex(RuntimeError, 'quota'):
                self.run_python(f'import os; os.write({descriptor}, b"x"*2000000)')
        with self.assertRaisesRegex(RuntimeError, 'quota'):
            self.run_python('import os; os.write(1,b"x"*600000); os.write(2,b"x"*600000)')

    def test_nonzero_does_not_expose_payload(self):
        with self.assertRaises(RuntimeError) as result:
            self.run_python('import sys; print("PRIVATE_PAYLOAD",file=sys.stderr);sys.exit(3)')
        self.assertNotIn('PRIVATE_PAYLOAD', str(result.exception))

    def test_invalid_bounds_and_missing_executable(self):
        for timeout in (0, 31, float('nan'), True):
            with self.assertRaises(ValueError):
                self.run_python('pass', timeout)
        with self.assertRaises(ValueError):
            self.run_python('pass', max_output_bytes=1024*1024+1)
        with self.assertRaises(RuntimeError):
            run_decoder_process(['rfatlas-nonexistent-decoder-3a1c'], 1)


if __name__ == '__main__':
    unittest.main()
