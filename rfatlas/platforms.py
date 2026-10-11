"""The only OS-specific device locking and subprocess integration boundary."""
import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
from datetime import datetime,timezone
from .diagnostics import atomic_json


class DeviceLock:
    def __init__(self, directory, identity='rtl-sdr'):
        self.directory=Path(directory)
        self.path=self.directory/(hashlib.sha256(identity.encode()).hexdigest()[:24]+'.lock')

    def __enter__(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b')
        try:
            if os.fstat(self.file.fileno()).st_size==0: self.file.write(b'0');self.file.flush()
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise RuntimeError('Receiver busy: another RF Atlas stage owns the device') from error
        return self

    def __exit__(self,*args):
        try:
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        finally: self.file.close()


class WindowsJob:
    def __init__(self):
        from ctypes import wintypes
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        k=self.kernel
        k.CreateJobObjectW.restype=wintypes.HANDLE
        k.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        k.CloseHandle.argtypes=[wintypes.HANDLE]
        class Basic(ctypes.Structure):
            _fields_=[('times',ctypes.c_longlong*2),('flags',wintypes.DWORD),('min',ctypes.c_size_t),('max',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class Limits(ctypes.Structure):
            _fields_=[('basic',Basic),('io',ctypes.c_ulonglong*6),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.handle=k.CreateJobObjectW(None,None);limits=Limits();limits.basic.flags=0x2000
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        if not k.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error=ctypes.get_last_error();self.close();raise ctypes.WinError(error)

    def assign(self,process):
        from ctypes import wintypes
        if not self.kernel.AssignProcessToJobObject(self.handle,wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle: self.kernel.CloseHandle(self.handle);self.handle=None


def run_process(args,timeout,diagnostic_path=None):
    argv=[str(a) for a in args];started=datetime.now(timezone.utc).isoformat()
    logging.info('command %s',json.dumps(argv))
    job=None;process=None;stdout=b'';stderr=b'';error=None
    try:
        if os.name=='nt': job=WindowsJob()
        launch=argv
        if sys.platform.startswith('linux'):
            launch=[sys.executable,str(Path(__file__).with_name('_linux_child.py')),str(os.getpid()),*argv]
        process=subprocess.Popen(launch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,start_new_session=os.name!='nt')
        if job: job.assign(process)
        stdout,stderr=process.communicate(timeout=timeout)
        if process.returncode: raise RuntimeError(f'{Path(argv[0]).name} failed ({process.returncode}): {stderr.decode(errors="replace")}')
        return stdout.decode(errors='replace'),stderr.decode(errors='replace')
    except FileNotFoundError as exception:
        error=str(exception)
        raise RuntimeError(f'Executable or required runtime is missing: {argv[0]}; check configured tools and existing drivers') from exception
    except BaseException as exception:
        error=f'{type(exception).__name__}: {exception}';raise
    finally:
        if process is not None and process.poll() is None:
            if os.name=='nt': process.kill()
            else:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            stdout,stderr=process.communicate()
        if job: job.close()
        result={'command':argv,'return_code':process.returncode if process else None,'stdout':stdout.decode(errors='replace'),'stderr':stderr.decode(errors='replace'),'error':error,'started':started,'ended':datetime.now(timezone.utc).isoformat()}
        logging.info('command result %s',json.dumps(result))
        if diagnostic_path: atomic_json(diagnostic_path,result)
