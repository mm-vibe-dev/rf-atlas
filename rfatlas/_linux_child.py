"""Linux parent-death guard; exec preserves PID and process group."""
import ctypes
import os
import signal
import sys

parent=int(sys.argv[1])
libc=ctypes.CDLL(None,use_errno=True)
if libc.prctl(1,signal.SIGKILL,0,0,0)!=0:
    raise OSError(ctypes.get_errno(),'Unable to establish receiver parent-death guard')
if os.getppid()!=parent: raise SystemExit('Owner exited before receiver launch')
os.execvp(sys.argv[2],sys.argv[2:])
