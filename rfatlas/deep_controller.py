"""One bounded manual investigation; retained replay has its own cached worker."""
from contextlib import closing
import json
import threading
import time
import uuid
from pathlib import Path
from .hardware import RTLBackend
from .storage import open_database


class DeepController:
    def __init__(self,config,retention):
        self.config=config;self.retention=retention;self.thread=None;self.cancel_event=threading.Event()
        self.mutex=threading.Lock();self.current=None;self.result=None

    def status(self):
        with self.mutex:return dict(active=bool(self.thread and self.thread.is_alive()),current=self.current,result=self.result,
            limits=dict(max_acquisition_s=30,max_samples=4_000_000,parallel_decoders=1),
            privacy='Transient in-memory IQ; only privacy-filtered measurements and verification history are stored.')

    def start(self,options):
        if set(options)-{'signal_id','recipe','strategy','gain','sample_rate'}:raise ValueError('Unknown investigation option')
        from .deep_investigation import RECIPES
        recipe=options.get('recipe','generic');strategy=options.get('strategy','short');identity=options.get('signal_id')
        if recipe not in RECIPES or strategy not in ('short','medium','targeted','burst-watch'):raise ValueError('Invalid investigation plan')
        if not isinstance(identity,str) or not 1<=len(identity)<=128:raise ValueError('Invalid signal')
        from .hardware import gain_value
        gain=gain_value(str(options.get('gain','15.7')))
        if gain=='auto' or float(gain)==0:raise ValueError('Manual gain required')
        with closing(open_database(self.config.root)) as db:
            row=db.execute('SELECT center_frequency_hz,occupied_bandwidth_hz,protocol FROM signals WHERE signal_id=?',(identity,)).fetchone()
            if not row:raise ValueError('Signal not found')
        rate=options.get('sample_rate',next((r for r in (250000,1024000,2048000,2400000) if r>=max(250000,(row[1] or 0)*3)),2400000))
        if type(rate) is not int or rate not in (250000,1024000,2048000,2400000):raise ValueError('Unsupported rate')
        with self.mutex:
            if self.thread and self.thread.is_alive():raise RuntimeError('Investigation already running')
            self.current=uuid.uuid4().hex;self.result=dict(status='running',signal_id=identity,recipe=recipe,strategy=strategy)
            self.cancel_event.clear()
            self.thread=threading.Thread(target=self._run,args=(identity,row[0],row[2],recipe,strategy,rate,gain),daemon=True,name='rf-atlas-deep-investigate')
            self.thread.start()
        return self.status()

    def _run(self,identity,frequency,previous,recipe,strategy,rate,gain):
        from .adaptive_capture import acquire
        from .identification import identify_samples,store_identification,default_registry
        from .entities import private_result
        from .platforms import DeviceLock
        started=time.monotonic()
        try:
            with DeviceLock(self.config.lock_root,'deep-investigation'):
                iq,meta=acquire(self.config,RTLBackend(self.config),frequency,rate,gain,strategy,cancel_event=self.cancel_event)
                result=identify_samples(iq,meta,registry=default_registry(config=self.config),previous_protocol=previous,
                    cancel_event=self.cancel_event,recipe=recipe)
                result['acquisition']=meta['acquisition'];meta['signal_id']=identity
                keep=self.retention()
                with closing(open_database(self.config.root)) as db:
                    key=store_identification(db,result,meta,'transient:'+meta['sha256'],retain_frames=keep.get('retain_frames',False),retain_identifiers=keep.get('retain_identifiers',False))
                result=private_result(result,retain_frames=keep.get('retain_frames',False),retain_identifiers=keep.get('retain_identifiers',False))
                outcome=dict(status='complete',signal_id=identity,run_key=key,elapsed_s=time.monotonic()-started,result=result)
        except InterruptedError:outcome=dict(status='cancelled',signal_id=identity,elapsed_s=time.monotonic()-started)
        except Exception as error:outcome=dict(status='failed',signal_id=identity,error=type(error).__name__+': '+str(error)[:300],elapsed_s=time.monotonic()-started)
        with self.mutex:self.result=outcome

    def cancel(self):
        self.cancel_event.set();return self.status()

    def presets(self):
        path=self.config.root/'runtime/investigation-presets.json'
        if not path.exists():return []
        if path.stat().st_size>65536:raise ValueError('Preset file exceeds budget')
        values=json.loads(path.read_text(encoding='utf8'))
        if not isinstance(values,list) or len(values)>100 or any(not isinstance(v,dict) for v in values):raise ValueError('Invalid preset file')
        return values

    def save_preset(self,options):
        from .deep_investigation import RECIPES
        if set(options)!={'signal_id','recipe','strategy'}:raise ValueError('Invalid investigation preset')
        if options['recipe'] not in RECIPES or options['strategy'] not in ('short','medium','targeted','burst-watch'):raise ValueError('Invalid investigation preset')
        identity=options['signal_id']
        if not isinstance(identity,str) or not 1<=len(identity)<=128:raise ValueError('Invalid signal')
        with closing(open_database(self.config.root)) as db:
            if not db.execute('SELECT 1 FROM signals WHERE signal_id=?',(identity,)).fetchone():raise ValueError('Signal not found')
        from .diagnostics import atomic_json
        with self.mutex:
            values=[v for v in self.presets() if v.get('signal_id')!=identity]
            values.append(dict(options));values=values[-100:]
            atomic_json(self.config.root/'runtime/investigation-presets.json',values)
        return dict(presets=values,saved=True,acquisition_started=False)

    def close(self):
        self.cancel_event.set()
        if self.thread:self.thread.join(timeout=8)
