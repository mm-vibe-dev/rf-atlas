"""Local bounded, cancellable retained-IQ reanalysis with persistent provenance."""
from pathlib import Path
from contextlib import closing,ExitStack
import hashlib
import json
import sqlite3
import shutil
import threading
import time
import uuid


def ensure_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS reanalysis_jobs(job_id TEXT PRIMARY KEY,created REAL,updated REAL,status TEXT,options TEXT,progress TEXT);
    CREATE TABLE IF NOT EXISTS reanalysis_items(job_id TEXT REFERENCES reanalysis_jobs(job_id),capture_id TEXT REFERENCES captures(capture_id),status TEXT,reason TEXT,run_key TEXT,PRIMARY KEY(job_id,capture_id));
    CREATE TABLE IF NOT EXISTS reanalysis_cache(signal_id TEXT,source_sha256 TEXT,algorithm_key TEXT,run_key TEXT,PRIMARY KEY(signal_id,source_sha256,algorithm_key));
    CREATE INDEX IF NOT EXISTS captures_signal ON captures(signal_id);
    ''')


def algorithm_key(registry,backend='dsp',model_path=None):
    """Versions plus actual local implementation/optional binary/model hashes."""
    from .identification import ENGINE_VERSION
    files=['recognition.py','advanced_dsp.py','engine.py','features.py','recovery.py','activity.py','iq.py','identification.py','deep_investigation.py','characterization.py','analog.py','dmr.py','ais.py','rds.py','packet.py','pocsag.py','protocols.py','external_decoders.py','dsd.py','ml.py','pipeline.py','reanalysis.py']
    if backend=='rfai':files.append('rfai.py')
    files.append('investigation_feedback.py')
    files.extend(['ai.py','demodulation.py','messages.py','fec.py'])
    feedback=sorted(name for name,values in registry.feedback.items() if values.get('verified_runs',0)>=3)
    payload=dict(engine=ENGINE_VERSION,backend=backend,routing_feedback=feedback,files={n:hashlib.sha256((Path(__file__).parent/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in files},decoders=registry.describe())
    if registry.ai_model is not None:
        from .ai import load_model as load_ai_model
        try:payload['optional_ai_sha256']=load_ai_model(registry.ai_model)[1]
        except (ValueError,OSError):payload['optional_ai_sha256']='UNAVAILABLE'
    if model_path:
        if backend=='rfai':from .rfai import load_model
        else:from .ml import load_model
        load_model(model_path)  # Enforce non-executable bounded NPZ contract before hashing.
        with open(model_path,'rb') as file:payload['model_sha256']=hashlib.file_digest(file,'sha256').hexdigest()
    for decoder in registry.decoders.values():
        for attr in ('executable','path'):
            p=getattr(decoder,attr,None)
            resolved=shutil.which(str(p)) if isinstance(p,(str,Path)) else None
            if resolved:
                with open(resolved,'rb') as file:payload.setdefault('external_sha256',{})[decoder.name]=hashlib.file_digest(file,'sha256').hexdigest()
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def recording_context(metadata):
    center=metadata.get('center_hz');desired=metadata.get('desired_frequency_hz',center)
    return dict(format=metadata.get('format'),sample_rate=metadata.get('sample_rate'),
        center_hz=center,desired_frequency_hz=desired,
        tuning_offset_hz=metadata.get('tuning_offset_hz',center-desired),
        sample_count=metadata.get('sample_count'),sha256=metadata.get('sha256'))


class ReanalysisController:
    def __init__(self,config,retention=None,registry=None):
        self.config=config;self.retention=retention or (lambda:dict(retain_frames=False,retain_identifiers=False))
        self.registry=registry
        self.cancel_event=threading.Event();self.lock=threading.Lock();self.thread=None;self.current=None
        from .storage import open_database
        with closing(open_database(config.root)) as db:
            from .platforms import DeviceLock
            try:
                with DeviceLock(config.root/'runtime','offline-reanalysis'):
                    db.execute("UPDATE reanalysis_jobs SET status='interrupted',updated=? WHERE status='running'",(time.time(),));db.commit()
            except RuntimeError:pass  # Another process still owns its bounded worker.

    def status(self):
        with closing(sqlite3.connect(self.config.root/'data/rf_atlas.sqlite',timeout=2)) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute('SELECT job_id,created,updated,status,progress FROM reanalysis_jobs ORDER BY created DESC LIMIT 20')]
        for row in rows:row['progress']=json.loads(row['progress'])
        return dict(active=bool(self.thread and self.thread.is_alive()),current=self.current,jobs=rows,cancellation='Between bounded classification/decoder stages; already running decoder finishes its bounded attempt.')

    def start(self,options=None,*,model_path=None):
        options=dict(options or {})
        if set(options)-{'signal_id','unknown_only','limit','max_seconds','force','backend','recipe'}:raise ValueError('Unknown reanalysis option')
        from .deep_investigation import RECIPES
        if options.get('recipe','generic') not in RECIPES:raise ValueError('Unknown investigation recipe')
        signal_id=options.get('signal_id');limit=options.get('limit',100);seconds=options.get('max_seconds',600)
        if signal_id is not None and (not isinstance(signal_id,str) or not 1<=len(signal_id)<=128):raise ValueError('Invalid signal')
        if type(limit) is not int or not 1<=limit<=100 or type(seconds) not in (int,float) or not 1<=seconds<=900:raise ValueError('Reanalysis budget exceeded')
        if any(type(options[k]) is not bool for k in ('unknown_only','force') if k in options):raise ValueError('Boolean option required')
        backend=options.get('backend','dsp')
        if backend not in ('dsp','ml','hybrid','rfai'):raise ValueError('Unsupported analysis backend')
        if backend!='dsp' and model_path is None:raise ValueError('Optional ML needs an explicit locally validated model')
        from .identification import default_registry
        registry=self.registry or default_registry(config=self.config);key=hashlib.sha256((algorithm_key(registry,backend,model_path)+options.get('recipe','generic')).encode()).hexdigest()
        with self.lock,ExitStack() as cleanup:
            if self.thread and self.thread.is_alive():raise RuntimeError('Reanalysis already running')
            from .platforms import DeviceLock
            lease=cleanup.enter_context(DeviceLock(self.config.root/'runtime','offline-reanalysis'))
            where=['c.signal_id IS NOT NULL'];args=[]
            if signal_id:where.append('c.signal_id=?');args.append(signal_id)
            if options.get('unknown_only',not bool(signal_id)):where.append("coalesce(s.protocol,'UNKNOWN')='UNKNOWN'")
            with closing(sqlite3.connect(self.config.root/'data/rf_atlas.sqlite')) as db:
                # Rank recorded evidence before applying the work budget. Only
                # existing analysis evidence is used; RF frequency is not truth.
                priority="""CASE WHEN json_extract(i.result,'$.analysis.quality.status')='TENTATIVE' THEN 1 ELSE 0 END DESC,
                    CASE WHEN coalesce(json_extract(i.result,'$.analysis.modulation'),'UNKNOWN')='UNKNOWN' THEN 1 ELSE 0 END DESC,
                    CASE WHEN json_extract(i.result,'$.analysis.signal_class')='DIGITAL' THEN 1 ELSE 0 END DESC,
                    CASE WHEN coalesce(json_array_length(json_extract(i.result,'$.attempts')),0)=0 THEN 1 ELSE 0 END DESC,
                    (SELECT count(*) FROM identification_runs h WHERE h.signal_id=c.signal_id AND json_extract(h.result,'$.protocol')='UNKNOWN') DESC,
                    coalesce(json_extract(i.result,'$.analysis.features.peak_snr_db'),0) DESC,c.rowid DESC"""
                rows=db.execute('SELECT c.capture_id,c.signal_id,c.path,c.settings,s.protocol FROM captures c JOIN signals s USING(signal_id) LEFT JOIN identification_runs i ON i.rowid=(SELECT h.rowid FROM identification_runs h WHERE h.signal_id=c.signal_id ORDER BY h.created DESC,h.rowid DESC LIMIT 1) WHERE '+' AND '.join(where)+' ORDER BY '+priority+' LIMIT ?',args+[limit]).fetchall()
                self.current=uuid.uuid4().hex;now=time.time()
                progress=dict(total=len(rows),completed=0,skipped=0,failed=0,verified=0,characterized=0)
                db.execute('INSERT INTO reanalysis_jobs VALUES(?,?,?,?,?,?)',(self.current,now,now,'running',json.dumps(dict(options,algorithm_key=key)),json.dumps(progress)));db.commit()
            self.cancel_event.clear()
            self.thread=threading.Thread(target=self._run,args=(self.current,rows,progress,key,registry,seconds,options.get('force',False),backend,model_path,lease,options.get('recipe','generic')),daemon=True,name='rf-atlas-offline-reanalysis');self.thread.start()
            cleanup.pop_all()  # The worker finally block now owns this independent offline lease.
        return self.status()

    def cancel(self):
        self.cancel_event.set();return self.status()

    def results(self):
        """Preserve the CLI list contract, including explicit failed/skipped items."""
        with closing(sqlite3.connect(self.config.root/'data/rf_atlas.sqlite')) as db:
            rows=db.execute('SELECT i.status,i.reason,i.run_key,r.result FROM reanalysis_items i LEFT JOIN identification_runs r USING(run_key) WHERE i.job_id=? ORDER BY i.rowid',(self.current,)).fetchall()
        return [dict(json.loads(payload) if payload else {},run_key=key,reanalysis_status=status,reanalysis_reason=reason) for status,reason,key,payload in rows]

    def _run(self,identity,rows,progress,key,registry,seconds,force,backend,model_path,lease,recipe):
        from .storage import open_database
        from .identification import identify_recording,store_identification
        db=None;started=time.monotonic();state='complete'
        try:
            db=open_database(self.config.root)
            for capture_id,signal_id,path,raw,previous in rows:
                if self.cancel_event.is_set():state='cancelled';break
                if time.monotonic()-started>=seconds:state='budget_exhausted';break
                run_key=None;reason=None
                try:
                    metadata=json.loads(raw);source=metadata.get('sha256')
                    if not Path(path).is_file():raise FileNotFoundError('NO_USABLE_IQ_RECORDING')
                    sidecar=Path(path).with_suffix('.json')
                    if sidecar.stat().st_size>65536:raise ValueError('CAPTURE_METADATA_BUDGET_EXCEEDED')
                    current=json.loads(sidecar.read_text(encoding='utf8'))
                    context=recording_context(metadata)
                    if recording_context(current)!=context or current.get('signal_id') not in (None,signal_id):raise ValueError('CAPTURE_METADATA_CHANGED')
                    context_sha=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()
                    cache_key=hashlib.sha256((key+context_sha).encode()).hexdigest()
                    cached=db.execute('SELECT run_key FROM reanalysis_cache WHERE signal_id=? AND source_sha256=? AND algorithm_key=?',(signal_id,source,cache_key)).fetchone()
                    if cached and not force:
                        # Integrity is still checked before a cache hit can be reported.
                        from .iq import checksum
                        if checksum(path)!=source:raise ValueError('CAPTURE_INTEGRITY_FAILURE')
                        status='skipped';reason='UNCHANGED_RECORDING_AND_ALGORITHMS';progress['skipped']+=1;run_key=cached[0]
                    else:
                        result,meta=identify_recording(Path(path),registry=registry,previous_protocol=previous,max_bytes=self.config.max_capture_bytes,backend=backend,model_path=model_path,cancel_event=self.cancel_event,recipe=recipe)
                        if meta.get('signal_id') not in (None,signal_id) or meta.get('sha256')!=source:raise ValueError('Capture association or integrity changed')
                        # Legacy imported sidecars may lack an association; the catalog
                        # supplies it without rewriting the original recording metadata.
                        meta=dict(meta,signal_id=signal_id)
                        result['reanalysis']=dict(job_id=identity,algorithm_key=key,input_context_sha256=context_sha,original_capture_id=capture_id,source='RETAINED_RECORDING_REPLAY',historical=True)
                        keep=self.retention();run_key=store_identification(db,result,meta,Path(path),retain_frames=keep.get('retain_frames',False),retain_identifiers=keep.get('retain_identifiers',False))
                        db.execute('INSERT OR REPLACE INTO reanalysis_cache VALUES(?,?,?,?)',(signal_id,source,cache_key,run_key))
                        status='complete';progress['completed']+=1
                        progress['verified']+=int(result['protocol_verified']);progress['characterized']+=int(result.get('characterization',{}).get('signal_type','UNKNOWN')!='UNKNOWN')
                except InterruptedError:state='cancelled';break
                except Exception as e:
                    status='failed';reason=type(e).__name__+': '+str(e)[:200];progress['failed']+=1
                with db:
                    db.execute('INSERT INTO reanalysis_items VALUES(?,?,?,?,?)',(identity,capture_id,status,reason,run_key))
                    progress['elapsed_s']=time.monotonic()-started
                    db.execute('UPDATE reanalysis_jobs SET updated=?,progress=? WHERE job_id=?',(time.time(),json.dumps(progress),identity))
        except Exception as e:
            state='failed';progress['error']=type(e).__name__
        finally:
            try:
                if db is not None:
                    with db:db.execute('UPDATE reanalysis_jobs SET status=?,updated=?,progress=? WHERE job_id=?',(state,time.time(),json.dumps(progress),identity))
            finally:
                if db is not None:db.close()
                lease.__exit__(None,None,None)

    def close(self):
        self.cancel_event.set()
        if self.thread:self.thread.join(timeout=.2)
