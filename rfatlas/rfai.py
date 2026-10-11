"""Optional original CPU RF prototype experiment; non-executable JSON parameters."""
from pathlib import Path
import hashlib
import json
import time
import numpy as np

VERSION='rf-prototype-1'
LABELS=('AM','NFM','WFM','OOK','2FSK','4FSK','BPSK','QPSK','CW','NOISE')


def feature_vector(analysis):
    f=analysis['features'];r=analysis.get('recovery',{});t=analysis.get('recognition_features',{})
    modes=r.get('frequency_modes',{});phase=r.get('phase_models',[])
    return np.asarray([np.log1p(f['occupied_bandwidth_hz']),f['envelope_cv'],f['spectral_flatness'],
        np.log1p(f['instantaneous_frequency_span_hz']),r.get('envelope_cv',f['envelope_cv']),r.get('envelope_q10_q90',0),
        r.get('envelope_periodicity',0),modes.get('concentration',0),
        phase[0].get('moment',0) if phase else 0,phase[-1].get('moment',0) if phase else 0,
        t.get('envelope',{}).get('peak_fraction',0),t.get('discriminator',{}).get('peak_fraction',0),
        t.get('discriminator',{}).get('audio_fraction',0),t.get('envelope',{}).get('audio_fraction',0)],float)


def train(rows,path):
    if not rows or len(rows)>10000 or any(r['case']['split']!='development' for r in rows):raise ValueError('Bounded development-only training required')
    x=np.asarray([feature_vector(r['after']) for r in rows]);y=[r['case']['family'] for r in rows]
    if set(y)!=set(LABELS):raise ValueError('Balanced class coverage required')
    mean=x.mean(0);scale=np.maximum(x.std(0),.05);x=np.clip((x-mean)/scale,-8,8)
    centers=np.asarray([x[np.asarray(y)==label].mean(0) for label in LABELS])
    variance=np.asarray([np.maximum(x[np.asarray(y)==label].var(0),.35) for label in LABELS])
    data=dict(schema=1,algorithm=VERSION,labels=LABELS,mean=mean.tolist(),scale=scale.tolist(),centers=centers.tolist(),variance=variance.tolist(),rejection_score=.65,rejection_margin=.15,
        training_config_sha256=hashlib.sha256(json.dumps([r['case'] for r in rows],sort_keys=True).encode()).hexdigest(),limitations='Synthetic development only; uncalibrated scores and unvalidated receiver distribution shift.')
    Path(path).write_text(json.dumps(data),encoding='utf8');return data


def load_model(path):
    path=Path(path)
    if path.stat().st_size>100000:raise ValueError('RF model too large')
    model=json.loads(path.read_text(encoding='utf8'))
    if model.get('schema')!=1 or model.get('algorithm')!=VERSION or tuple(model.get('labels',[]))!=LABELS:raise ValueError('Unsupported RF model contract')
    for name,shape in [('mean',(14,)),('scale',(14,)),('centers',(10,14)),('variance',(10,14))]:
        value=np.asarray(model.get(name),dtype=float)
        if value.shape!=shape or not np.isfinite(value).all() or name in ('scale','variance') and np.any(value<=0):raise ValueError('Invalid RF model parameters')
        model[name]=value
    for name in ['rejection_score','rejection_margin']:
        if type(model.get(name)) not in (int,float) or not 0<=model[name]<=1:raise ValueError('Invalid rejection rule')
    return model


def predict(analysis,path):
    start=time.perf_counter();model=load_model(path);load_ms=(time.perf_counter()-start)*1000
    x=np.clip((feature_vector(analysis)-model['mean'])/model['scale'],-8,8)
    with np.errstate(over='ignore',invalid='ignore',divide='ignore'):
        distance=np.mean((model['centers']-x)**2/model['variance'],axis=1)
    if not np.isfinite(distance).all():raise ValueError('RF model distances exceed numeric budget')
    scores=np.exp(-(distance-distance.min())*2);scores/=scores.sum();order=np.argsort(scores)[::-1];top,runner=order[:2]
    accepted=scores[top]>=model['rejection_score'] and scores[top]-scores[runner]>=model['rejection_margin']
    label=LABELS[top] if accepted else 'UNKNOWN'
    with open(path,'rb') as file:model_sha256=hashlib.file_digest(file,'sha256').hexdigest()
    return dict(modulation=label,score=float(scores[top]),accepted=bool(accepted),algorithm=VERSION,load_ms=load_ms,
        confidence_kind='UNCALIBRATED_PROTOTYPE_SCORE',model_sha256=model_sha256,candidates=[dict(modulation=LABELS[i],score=float(scores[i])) for i in order[:3]])
