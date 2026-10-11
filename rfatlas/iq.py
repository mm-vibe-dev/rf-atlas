"""Bounded finite recordings and metadata-driven offline replay."""
from datetime import datetime,timezone
import hashlib
import json
import re
from pathlib import Path
import uuid
import numpy as np
from .diagnostics import atomic_json
from .features import classify


def checksum(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def read_recording(path,metadata=None,max_bytes=64*1024*1024,*,max_samples=None):
    path=Path(path)
    metadata=metadata or path.with_suffix('.json')
    if type(max_bytes) is not int or max_bytes<8192:raise ValueError('Invalid recording byte budget')
    with Path(metadata).open('rb') as f:document=f.read(65537)
    if len(document)>65536:raise ValueError('IQ metadata exceeds 64 KiB budget')
    meta=json.loads(document.decode('utf8'))
    if not isinstance(meta,dict):raise ValueError('IQ metadata must be an object')
    if meta.get('format')!='cu8': raise ValueError('Only interleaved unsigned 8-bit IQ (cu8) is supported')
    rate=meta.get('sample_rate');center=meta.get('center_hz');count=meta.get('sample_count')
    if type(rate) not in (int,float) or not np.isfinite(rate) or rate<1000:
        raise ValueError('Invalid sample-rate metadata')
    if type(center) not in (int,float) or not np.isfinite(center) or center<=0:
        raise ValueError('Invalid center-frequency metadata')
    size=path.stat().st_size
    if type(count) is not int or count<4096 or size!=count*2 or size>max_bytes:
        raise ValueError('Corrupt, incomplete or oversized IQ recording')
    # Hash the actual bounded bytes used by DSP, avoiding a second full-file
    # pass and a checksum/read race if the recording changes concurrently.
    if max_samples is None:
        raw=np.fromfile(path,dtype=np.uint8,count=count*2+1)
        digest=hashlib.sha256(raw).hexdigest();expected=count*2
    else:
        if type(max_samples) is not int or not 4096<=max_samples<=4_000_000:raise ValueError('Invalid prefix sample budget')
        expected=min(count,max_samples)*2
        with path.open('rb') as file:
            prefix=file.read(expected);hasher=hashlib.sha256(prefix);consumed=len(prefix)
            for block in iter(lambda:file.read(1024*1024),b''):hasher.update(block);consumed+=len(block)
        if consumed!=count*2:raise ValueError('IQ sample-count changed')
        raw=np.frombuffer(prefix,np.uint8);digest=hasher.hexdigest()
    if len(raw)!=expected or meta.get('sha256')!=digest:raise ValueError('IQ checksum or sample-count mismatch')
    # Derived in memory before offset rotation; never rewrite source metadata.
    meta['analysis_quality']={'adc_endpoint_fraction':float(np.mean((raw[::2]==0)|(raw[::2]==255)|(raw[1::2]==0)|(raw[1::2]==255))),
                              'basis':'Raw CU8 endpoint occupancy before frequency translation'}
    iq=(raw[::2].astype(np.float64)-127.5)/127.5+1j*(raw[1::2].astype(np.float64)-127.5)/127.5
    desired=meta.get('desired_frequency_hz',center)
    if type(desired) not in (int,float) or not np.isfinite(desired) or abs(desired-center)>=rate/2:
        raise ValueError('Desired signal lies outside capture bandwidth')
    offset=meta.get('tuning_offset_hz',center-desired)
    if type(offset) not in (int,float) or not np.isfinite(offset) or not np.isclose(offset,center-desired,rtol=0,atol=1e-6):
        raise ValueError('Inconsistent tuning-offset metadata')
    iq*=np.exp(2j*np.pi*(center-desired)*np.arange(len(iq))/rate)
    return iq,meta


def replay(path,metadata=None,max_bytes=64*1024*1024,channel_bandwidth_hz=None,backend='dsp',model_path=None,*,cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Replay cancelled')
    iq,meta=read_recording(path,metadata,max_bytes)
    return analyze_samples(iq,meta,channel_bandwidth_hz,backend,model_path,cancel_event=cancel_event),meta


def analyze_samples(iq,meta,channel_bandwidth_hz=None,backend='dsp',model_path=None,*,cancel_event=None):
    """Shared retained/transient IQ analysis; caller supplies validated IQ context."""
    if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Analysis cancelled')
    from .activity import analyze_activity
    activity=analyze_activity(iq,meta['sample_rate'],channel_bandwidth_hz)
    lo,hi=activity['selected_start_sample'],activity['selected_stop_sample']
    if backend=='baseline':
        result=classify(iq[lo:hi],meta['sample_rate'])
    else:
        from .recognition import classify as classify_engine
        # Small captures preserve the full context for recovery. Larger files
        # use the bounded activity window, capped at one million samples.
        if len(iq)<=1000000:lo,hi=0,len(iq)
        hi=min(hi,lo+1000000)
        evidence_meta={**meta,'model_path':model_path}
        if channel_bandwidth_hz is not None:
            evidence_meta.update(channel_bandwidth_hz=channel_bandwidth_hz,channel_center_offset_hz=0.)
        result=classify_engine(iq[lo:hi],meta['sample_rate'],evidence_meta,backend,model_path,cancel_event=cancel_event)
    # CU8 phase corners at only one or two ADC counts can mimic PSK.
    result['adc_amplitude_p95_counts']=float(np.quantile(np.abs(iq[lo:hi]),.95)*127.5)
    if result['adc_amplitude_p95_counts']<4:
        result.update(signal_class='UNKNOWN',modulation='UNKNOWN',confidence=0.0)
        result['evidence'].append('Fewer than four ADC counts at the 95th amplitude percentile; quantization can mimic phase states')
    result['activity']=activity
    result['analysis_start_s']=lo/meta['sample_rate']
    result['capture_id']=meta.get('capture_id')
    result['frequency_hz']=meta.get('desired_frequency_hz',meta['center_hz'])
    result['analyzed_duration_s']=result['features']['samples']/meta['sample_rate']
    result['capture_duration_s']=meta['sample_count']/meta['sample_rate']
    return result


def record(config,backend,frequency_hz,sample_rate,duration,gain,offset=25000,antenna='other',db=None,signal_id=None):
    if not np.isfinite(duration) or duration<=0 or duration>30: raise ValueError('Capture duration must be >0 and <=30 seconds')
    if sample_rate not in [250000,1024000,2048000,2400000]: raise ValueError('Use a supported configured rate: 250000, 1024000, 2048000, 2400000')
    if gain=='auto': raise ValueError('Recording requires explicit manual gain')
    from .hardware import gain_value
    gain=gain_value(str(gain))
    if float(gain)==0:raise ValueError('rtl_sdr gain zero enables AGC; use a positive manual gain')
    center=frequency_hz+offset
    if not 500000<=frequency_hz<=1766000000 or not 500000<=center<=1766000000 or abs(offset)>=sample_rate/2-10000:
        raise ValueError('Frequency/offset lies outside receiver or usable capture band')
    count=int(round(sample_rate*duration));size=count*2
    if count<4096 or size>config.max_capture_bytes: raise ValueError('Capture exceeds per-file budget or is too short')
    if signal_id is not None and (db is None or db.execute('SELECT 1 FROM signals WHERE signal_id=?',(signal_id,)).fetchone() is None):
        raise ValueError('Unknown signal ID; capture was not started')
    folder=config.root/'data/captures';folder.mkdir(parents=True,exist_ok=True)
    capture_id=str(uuid.uuid4());path=folder/f'{capture_id}.cu8';partial=path.with_suffix('.partial.cu8')
    args=[backend.tool('rtl_sdr'),'-d',backend.selector,'-f',str(center),'-s',str(sample_rate),'-g',gain,'-n',str(count),partial]
    with backend.lock():
        used=sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
        if used+size+16384>config.retention_bytes: raise ValueError('Capture retention budget exhausted; archive evidence manually')
        acquisition=backend.run(args,duration+30,path.with_suffix('.command.json'))
        if not partial.exists() or partial.stat().st_size!=size: raise ValueError('Receiver returned an incomplete recording; partial evidence retained')
        partial.replace(path)
        meta={'capture_id':capture_id,'format':'cu8','center_hz':center,'desired_frequency_hz':frequency_hz,'tuning_offset_hz':offset,'device_identity':backend.selector,'sample_rate':sample_rate,'sample_count':count,'gain_db':float(gain),'antenna':antenna,'created':datetime.now(timezone.utc).isoformat(),'sha256':checksum(path),'bias_tee':False,'signal_id':signal_id}
        reported=re.search(r'Exact sample rate is:\s*([\d.]+)',acquisition[1]) if isinstance(acquisition,tuple) else None
        meta['sample_rate_reported_hz']=float(reported[1]) if reported else None
        meta['sample_rate_basis']='configured nominal; reported tool rate retained separately'
        atomic_json(path.with_suffix('.json'),meta)
        if db is not None:
            with db:
                db.execute('INSERT OR IGNORE INTO antenna_profiles VALUES(?,?)',(antenna,'User named; response uncalibrated'))
                db.execute('INSERT INTO captures VALUES(?,?,?,?,?)',(capture_id,signal_id,antenna,str(path),json.dumps(meta)))
    return path,meta


def save_hypothesis(db,result,meta):
    capture=db.execute('SELECT signal_id FROM captures WHERE capture_id=?',(meta.get('capture_id'),)).fetchone()
    if capture is None or not capture[0]: raise ValueError('Recording must belong to a catalog signal to store a hypothesis')
    with db:
        db.execute('INSERT INTO classifications(signal_id,created,label,confidence,evidence) VALUES(?,?,?,?,?)',(capture[0],datetime.now(timezone.utc).isoformat(),json.dumps(result),result['confidence'],'HEURISTIC: '+'; '.join(result['evidence'])))
    # Heuristics do not replace confirmed catalog/decoder fields.
