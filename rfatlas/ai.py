"""Optional original CPU RF ridge inference. JSON weights cannot execute code.

Uses bounded measured I/Q features, never frequency allocations or protocol
labels. Uncalibrated scores are hypotheses; DSP and verified evidence remain.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
import numpy as np
from .rfai import feature_vector

VERSION='rf-ridge-1.9-v2'
FAMILIES=('ANALOG','DIGITAL','CARRIER','NOISE_CANDIDATE','UNKNOWN')
MODULATIONS=('AM','NFM','WFM','USB','LSB','CW','OOK','2FSK','4FSK','GFSK','GMSK','BPSK','QPSK','UNKNOWN')
_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='rf-atlas-ai')
_slot=threading.BoundedSemaphore(1)


def vector(a):
    s=a.get('structural_features',{});cp=(s.get('cyclic_prefix') or {}).get('best') or {};phase=(s.get('phase_alphabet') or {}).get('models',[])
    values=list(feature_vector(a))+[s.get('spectral_entropy',0),s.get('keying',{}).get('on_phase_coherence',0),s.get('keying',{}).get('low_high_power_ratio',1),cp.get('correlation',0),cp.get('contrast',0)]
    for order in (2,4,8):
        m=next((v for v in phase if v['order']==order),{})
        values.extend([m.get('half_min_moment',0),m.get('half_max_lower_moment',0),m.get('peak_fraction',0)])
    result=np.asarray(values,float)
    if result.shape!=(28,) or not np.isfinite(result).all():raise ValueError('Invalid RF feature representation')
    return result


def load_model(path):
    path=Path(path)
    if not path.is_file() or path.stat().st_size>131072:raise ValueError('Bounded local RF model required')
    raw=path.read_bytes()
    if len(raw)>131072:raise ValueError('RF model grew beyond budget')
    digest=hashlib.sha256(raw).hexdigest();manifest=path.with_suffix('.sha256')
    if not manifest.is_file() or manifest.stat().st_size>128 or manifest.read_text().strip()!=digest:raise ValueError('RF model checksum does not match local approval manifest')
    model=json.loads(raw)
    if not isinstance(model,dict):raise ValueError('RF model must be an object')
    if model.get('schema')!=1 or model.get('algorithm')!=VERSION or tuple(model.get('families',[]))!=FAMILIES or tuple(model.get('modulations',[]))!=MODULATIONS:raise ValueError('Unsupported RF model contract')
    for key,shape in [('mean',(28,)),('scale',(28,)),('family_weights',(29,5)),('modulation_weights',(29,14))]:
        value=np.asarray(model.get(key),float)
        if value.shape!=shape or not np.isfinite(value).all() or np.max(abs(value))>1e6 or key=='scale' and np.any(value<=0):raise ValueError('Invalid RF model numeric parameters')
        model[key]=value
    for key in ('reject_score','reject_margin','score_temperature'):
        if type(model.get(key)) not in (int,float) or not np.isfinite(model[key]) or not 0<=model[key]<=1:raise ValueError('Invalid RF rejection gate')
    if model['score_temperature']<.01:raise ValueError('Invalid score temperature')
    return model,digest


def infer(analysis,metadata,path):
    began=time.perf_counter();model,digest=load_model(path);z=np.r_[np.clip((vector(analysis)-model['mean'])/model['scale'],-8,8),1.]
    def head(weights,labels):
        logits=z@weights/max(model['score_temperature'],.01);logits-=logits.max();scores=np.exp(logits);scores/=scores.sum();order=np.argsort(scores)[::-1];top,second=order[:2]
        accepted=scores[top]>=model['reject_score'] and scores[top]-scores[second]>=model['reject_margin']
        return labels[top] if accepted else 'UNKNOWN',dict(zip(labels,map(float,scores)))
    family,fscores=head(model['family_weights'],FAMILIES);modulation,mscores=head(model['modulation_weights'],MODULATIONS)
    incomplete=bool(analysis.get('structural_features',{}).get('budget_exhausted'))
    low_quality=analysis.get('adc_amplitude_p95_counts',4)<4 or analysis.get('features',{}).get('adc_rail_fraction',0)>.01 or incomplete
    if low_quality:family=modulation='UNKNOWN'
    if family in ('UNKNOWN','NOISE_CANDIDATE','CARRIER'):modulation='UNKNOWN'
    expected='ANALOG' if modulation in ('AM','NFM','WFM','USB','LSB','CW') else 'DIGITAL'
    if modulation!='UNKNOWN' and expected!=family:modulation='UNKNOWN'
    return dict(status='COMPLETE',model_name='RF Atlas original CPU structure ridge',model_version=VERSION,model_sha256=digest,
        representation='28 bounded spectral/envelope/phase/structural I/Q measurements',sample_rate_hz=metadata['sample_rate'],
        capture_duration_s=metadata.get('sample_count',analysis['features']['samples'])/metadata['sample_rate'],
        source_sha256=metadata.get('sha256'),signal_quality=dict(adc_amplitude_p95_counts=analysis.get('adc_amplitude_p95_counts'),quality_rejected=low_quality,incomplete_structural_bank=incomplete),
        predicted_family=family,predicted_modulation=modulation,family_scores=fscores,modulation_scores=mscores,
        confidence_calibration='UNCALIBRATED_SYNTHETIC_ONLY',inference_duration_ms=(time.perf_counter()-began)*1000,
        timestamp=datetime.now(timezone.utc).isoformat(),dsp_family=analysis.get('signal_class','UNKNOWN'),
        disagreement=family!=analysis.get('signal_class','UNKNOWN'),protocol_verified=False,
        limitations=['Synthetic training; real receiver distribution shift unproven.','Scores and demodulation candidates never verify protocols.'])


def enabled_model(root):
    root=Path(root);settings=root/'runtime/app-settings.json'
    try:
        if settings.is_file() and settings.stat().st_size<=4096:
            value=json.loads(settings.read_text())
            if isinstance(value,dict) and value.get('ai_enabled') is True:return root/'runtime/models/ai-model.json'
    except (ValueError,OSError):pass
    return None


def submit(analysis,metadata,path):
    if not _slot.acquire(blocking=False):return None
    def work():
        try:return infer(analysis,metadata,path)
        except Exception as error:return dict(status='UNAVAILABLE',reason=type(error).__name__+': '+str(error)[:160],dsp_preserved=True)
        finally:_slot.release()
    try:return _pool.submit(work)
    except Exception:_slot.release();raise


def status(root):
    path=Path(root)/'runtime/models/ai-model.json';enabled=enabled_model(root) is not None
    try:
        _,digest=load_model(path)
        return dict(enabled=enabled,available=True,model_version=VERSION,model_sha256=digest,experimental=True)
    except Exception:return dict(enabled=enabled,available=False,experimental=True,remedy='Run rf-atlas ai-train to create a local synthetic model; DSP continues normally.')
