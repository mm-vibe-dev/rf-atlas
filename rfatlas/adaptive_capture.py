"""Receive-only, finite in-memory acquisitions under the global RTL lock.

Raw IQ never goes to disk. A bounded ring keeps the latest contiguous samples;
burst watch uses relative whole-band block energy and cannot detect a signal
already active during its initial baseline. It is not protocol recognition.
"""
from collections import deque
from datetime import datetime,timezone
import hashlib
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import numpy as np
from .hardware import gain_value
from .platforms import WindowsJob


def acquire(config,backend,frequency_hz,sample_rate=250000,gain='15.7',strategy='short',*,cancel_event=None,window_s=None):
    durations={'short':2,'medium':8,'targeted':20,'burst-watch':20}
    if strategy not in durations:raise ValueError('Unknown acquisition strategy')
    seconds=durations[strategy] if window_s is None else window_s
    if type(seconds) not in (int,float) or not np.isfinite(seconds) or not .1<=seconds<=30:raise ValueError('Invalid acquisition window')
    if type(sample_rate) is not int or sample_rate not in (250000,1024000,2048000,2400000):raise ValueError('Invalid acquisition rate')
    if type(frequency_hz) not in (int,float) or not np.isfinite(frequency_hz) or not 500000<=frequency_hz<=1766000000:raise ValueError('Invalid frequency')
    gain=gain_value(str(gain))
    if gain=='auto' or float(gain)==0:raise ValueError('Positive manual gain required')
    offset=25000 if frequency_hz+25000<=1766000000 else -25000
    count=int(round(sample_rate*seconds)); maximum=min(4_000_000,config.max_capture_bytes//2)
    if count<4096 or maximum<4096:raise ValueError('Invalid sample budget')
    if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Acquisition cancelled')
    command=[str(backend.tool('rtl_sdr')),'-d',backend.selector,'-f',str(int(round(frequency_hz+offset))),
        '-s',str(sample_rate),'-g',gain,'-b','16384','-n',str(count),'-']
    if sys.platform.startswith('linux'):
        command=[sys.executable,str(Path(__file__).with_name('_linux_child.py')),str(os.getpid()),*command]
    chunks=queue.Queue(maxsize=8);diagnostics=deque(maxlen=32);ring=deque();retained=0;received=0;trigger=None;baseline=[]
    process=None;job=None;started=time.monotonic();reader=None;stderr_reader=None;stop=threading.Event()
    with backend.lock():
        try:
            job=WindowsJob() if os.name=='nt' else None
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                bufsize=0,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if job:job.assign(process)
            def read():
                try:
                    while not stop.is_set():
                        block=process.stdout.read(16384)
                        if not block:break
                        while not stop.is_set():
                            try:chunks.put(block,timeout=.1);break
                            except queue.Full:pass
                finally:
                    while not stop.is_set():
                        try:chunks.put(None,timeout=.1);break
                        except queue.Full:pass
            def drain():
                for line in iter(process.stderr.readline,b''):diagnostics.append(line[:512].decode(errors='replace').lower())
            reader=threading.Thread(target=read,daemon=True);stderr_reader=threading.Thread(target=drain,daemon=True)
            reader.start();stderr_reader.start()
            pending=b''
            while received<count:
                if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Acquisition cancelled')
                if time.monotonic()-started>seconds+10:raise TimeoutError('Bounded receiver acquisition timeout')
                try:block=chunks.get(timeout=.1)
                except queue.Empty:continue
                if block is None:break
                block=pending+block;pending=block[len(block)//2*2:];block=block[:len(block)//2*2]
                block=block[:(count-received)*2]
                received+=len(block)//2;ring.append(block);retained+=len(block)
                while retained>maximum*2:
                    excess=retained-maximum*2
                    if len(ring[0])<=excess:retained-=len(ring.popleft())
                    else:ring[0]=ring[0][excess:];retained-=excess
                if strategy=='burst-watch' and block:
                    values=np.frombuffer(block,np.uint8).astype(float)-127.5
                    power=float(np.mean(values**2))
                    if received/sample_rate<=.25:baseline.append(power)
                    elif trigger is None and baseline and power>4*max(float(np.median(baseline)),1):trigger=received
                    if trigger is not None and received-trigger>=min(maximum//2,int(sample_rate*2)):break
            if received<count and not (strategy=='burst-watch' and trigger is not None):
                raise RuntimeError('Receiver returned incomplete IQ')
        finally:
            stop.set()
            if process is not None:
                if process.poll() is None:
                    if os.name=='nt':process.kill()
                    else:
                        try:os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                process.wait(timeout=5)
                if reader:reader.join(2)
                if stderr_reader:stderr_reader.join(2)
                process.stdout.close();process.stderr.close()
            if job:job.close()
    errors=('failed to set','no supported devices','failed to open','pll not locked','failed to enable')
    if any(e in line for line in diagnostics for e in errors):raise RuntimeError('Receiver setup failed')
    raw=b''.join(ring)
    if len(raw)<8192:raise ValueError('Insufficient transient IQ')
    a=np.frombuffer(raw,np.uint8);iq=(a[::2].astype(float)-127.5)/127.5+1j*(a[1::2].astype(float)-127.5)/127.5
    iq*=np.exp(2j*np.pi*offset*np.arange(len(iq))/sample_rate)
    meta=dict(format='cu8',sha256=hashlib.sha256(raw).hexdigest(),sample_rate=sample_rate,
        center_hz=int(round(frequency_hz+offset)),desired_frequency_hz=int(round(frequency_hz)),tuning_offset_hz=offset,
        sample_count=len(iq),gain_db=float(gain),device_identity=backend.selector,created=datetime.now(timezone.utc).isoformat(),
        analysis_quality=dict(adc_endpoint_fraction=float(np.mean((a[::2]==0)|(a[::2]==255)|(a[1::2]==0)|(a[1::2]==255)))),
        acquisition=dict(strategy=strategy,requested_window_s=seconds,wall_s=time.monotonic()-started,
            acquired_samples=received,acquisition_duration_s=received/sample_rate,analysis_duration_s=len(iq)/sample_rate,
            analysis_start_sample=received-len(iq),stored_recording_duration_s=0,raw_persisted=False,
            burst_trigger_sample=trigger,burst_watch_limitations='Relative whole-band energy; first 0.25 s baseline; continuous signals and narrow bursts may be missed.'))
    return iq,meta
