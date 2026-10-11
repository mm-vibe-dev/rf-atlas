"""Isolated, finite optional decoder execution without logging payloads.

Output spools to temporary files instead of unbounded Python pipe buffers.
The aggregate quota is polled; a fast process can transiently exceed it on
disk between polls, but excess output is never read into memory or accepted.
"""
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from .platforms import WindowsJob


def run_decoder_process(args, timeout, max_output_bytes=1024 * 1024, *, cwd=None):
    """Return UTF-8 stdout/stderr, or reject timeout, quota and nonzero exit."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError('Decoder timeout must be in (0, 30] seconds')
    if type(max_output_bytes) is not int or not 0 < max_output_bytes <= 1024 * 1024:
        raise ValueError('Decoder output quota must be in (0, one MiB]')
    if not isinstance(args, (tuple, list)) or not args or any(not isinstance(arg, (str, os.PathLike)) for arg in args):
        raise ValueError('Decoder command requires a nonempty argument list')
    argv = [os.fspath(arg) for arg in args]
    if sys.platform.startswith('linux'):
        argv = [sys.executable, str(Path(__file__).with_name('_linux_child.py')), str(os.getpid()), *argv]
    process = None
    job = None
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            if os.name == 'nt':
                job = WindowsJob()
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       start_new_session=os.name != 'nt', shell=False, cwd=cwd)
            if job:
                job.assign(process)
            while True:
                size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                if size > max_output_bytes:
                    raise RuntimeError('Decoder output quota exceeded')
                status = process.poll()
                if status is not None:
                    break
                if time.monotonic() - started >= timeout:
                    raise TimeoutError('Optional decoder timed out')
                time.sleep(.005)
            if status:
                # Do not echo decoder output: it may contain private payloads.
                raise RuntimeError(f'Optional decoder failed with exit code {status}')
            # Stop any child processes before accepting the final quota check.
            if job:
                job.close()
                job = None
            elif os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
            if size > max_output_bytes:
                raise RuntimeError('Decoder output quota exceeded')
            stdout.seek(0)
            stderr.seek(0)
            return stdout.read(max_output_bytes).decode('utf-8', errors='replace'), stderr.read(max_output_bytes).decode('utf-8', errors='replace')
        except FileNotFoundError as exception:
            raise RuntimeError('Optional decoder executable or runtime is missing') from exception
        finally:
            if process is not None:
                if os.name == 'nt':
                    if process.poll() is None:
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
            if job:
                job.close()


def binary_provenance(executable):
    """Fingerprint an operator-installed binary without retaining its path."""
    import hashlib
    import shutil
    resolved=shutil.which(str(executable))
    if not resolved:return {'status':'UNAVAILABLE'}
    path=Path(resolved)
    try:
        if path.stat().st_size>512*1024*1024:return {'status':'FINGERPRINT_OVER_BUDGET'}
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        return dict(status='FINGERPRINTED',executable=path.name,sha256=digest.hexdigest(),bytes=path.stat().st_size)
    except OSError:return {'status':'FINGERPRINT_UNAVAILABLE'}
