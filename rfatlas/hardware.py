"""Receive-only RTL CLI backend; future backends share this interface."""
import os
from pathlib import Path
import shutil
from typing import Protocol
from .platforms import DeviceLock,run_process


class ReceiverBackend(Protocol):
    selector: str
    def tool(self,name): ...
    def lock(self): ...
    def run(self,args,timeout,diagnostic_path=None): ...


class RTLBackend:
    def __init__(self,config): self.config=config;self.selector=config.device

    def tool(self,name):
        if name not in {'rtl_power','rtl_sdr','rtl_test'}: raise ValueError('Unsupported receive-only tool')
        configured=self.config.tools.get(name,name+('.exe' if os.name=='nt' else ''))
        path=shutil.which(configured)
        if path is None: raise RuntimeError(f'{name} unavailable: configure its installed executable or put it on PATH. No drivers/software were changed.')
        return Path(path)

    def lock(self):
        # Conservative global RTL lock avoids index/serial aliases bypassing
        # ownership for the single local receiver.
        return DeviceLock(self.config.lock_root,'rtl-sdr')

    def run(self,args,timeout,diagnostic_path=None):
        stdout,stderr=run_process(args,timeout,diagnostic_path)
        if any(message in stderr.lower() for message in ['failed to set tuner gain','failed to enable manual gain','failed to set sample rate','no supported devices found','failed to open rtlsdr','pll not locked','failed to set center frequency','failed to tune']):
            raise RuntimeError('Receiver setup failed despite tool exit status; inspect preserved stderr')
        return stdout,stderr


# Gains enumerated from the currently validated RTL-SDR Blog V4L.
import argparse
RTL_V4L_GAINS = [0.0,0.9,1.4,2.7,3.7,7.7,8.7,12.5,14.4,15.7,16.6,19.7,20.7,22.9,25.4,28.0,29.7,32.8,33.8,36.4,37.2,38.6,40.2,42.1,43.4,43.9,44.5,48.0,49.6]


def gain_value(value):
    if value == 'auto': return value
    try: gain=float(value)
    except ValueError: raise argparse.ArgumentTypeError('Gain must be a supported dB value or auto')
    if gain not in RTL_V4L_GAINS: raise argparse.ArgumentTypeError('Unsupported gain; use an enumerated rtl_test gain such as 15.7')
    return str(gain)
