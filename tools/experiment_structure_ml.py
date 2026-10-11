"""Synthetic-only class-balanced ridge experiment; never an application backend.

No executable weights/frameworks. Train on development source groups, choose
temperature on validation only; evaluate holdout after freezing the model.
Scores remain experimental and do not verify RF protocols or field accuracy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import tracemalloc
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.evaluate_advanced import references
from tools.evaluate_recognition import metrics,FAMILIES
from rfatlas.recognition import classify
from rfatlas.rfai import feature_vector
from rfatlas.datasets import support_reliability


def vector(a):
    s=a['structural_features'];cp=(s.get('cyclic_prefix') or {}).get('best') or {};phase=(s.get('phase_alphabet') or {}).get('models',[])
    values=list(feature_vector(a))+[s['spectral_entropy'],s['keying'].get('on_phase_coherence',0),s['keying'].get('low_high_power_ratio',1),cp.get('correlation',0),cp.get('contrast',0)]
    for order in (2,4,8):
        m=next((v for v in phase if v['order']==order),{})
        values.extend([m.get('half_min_moment',0),m.get('half_max_lower_moment',0),m.get('peak_fraction',0)])
    return np.asarray(values,float)


def collect(split):
    rows=[];x=[]
    for c,iq in references(split):
        a=classify(iq,c['sample_rate']);x.append(vector(a));rows.append(dict(case=c,truth=c['truth'],dsp_prediction=a['signal_class']))
    return np.asarray(x),rows


def probabilities(x,model,temperature=None):
    v=np.clip((x-np.asarray(model['mean']))/np.asarray(model['scale']),-8,8)
    logits=np.c_[v,np.ones(len(v))]@np.asarray(model['weights'])
    logits/=temperature or model['temperature'];logits-=logits.max(axis=1,keepdims=True)
    p=np.exp(logits);return p/p.sum(axis=1,keepdims=True)


def train(path):
    if Path(path).exists():raise FileExistsError('Model exists; choose a new experimental version')
    x,rows=collect('development');val,validation=collect('validation')
    y=np.asarray([FAMILIES.index(r['truth']) for r in rows]);target=np.eye(len(FAMILIES))[y]
    mean=x.mean(0);scale=np.maximum(x.std(0),.05);z=np.c_[np.clip((x-mean)/scale,-8,8),np.ones(len(x))]
    counts=np.bincount(y,minlength=len(FAMILIES));w=len(y)/(len(FAMILIES)*counts[y])
    ridge=np.eye(z.shape[1]);ridge[-1,-1]=.01
    beta=np.linalg.solve(z.T@(z*w[:,None])+ridge,z.T@(target*w[:,None]))
    model=dict(schema=1,algorithm='EXPERIMENTAL_STRUCTURE_RIDGE_1',labels=FAMILIES,mean=mean.tolist(),scale=scale.tolist(),weights=beta.tolist(),temperature=1.,reject_score=.65,reject_margin=.15,
        training_groups=sorted({r['case']['group'] for r in rows}),validation_groups=sorted({r['case']['group'] for r in validation}),limitations='Synthetic-only; validation temperature fit does not establish real-world probability calibration. Not integrated or enabled in RF Atlas.')
    truth=np.asarray([FAMILIES.index(r['truth']) for r in validation]);losses={}
    for temperature in (.25,.5,1.,2.):
        p=probabilities(val,model,temperature);losses[temperature]=float(-np.mean(np.log(np.maximum(p[np.arange(len(p)),truth],1e-12))))
    model['temperature']=min(losses,key=losses.get);model['validation_temperature_nll']=losses
    Path(path).write_text(json.dumps(model,indent=2),encoding='utf8');return model


def evaluate(path,split,output):
    source=Path(path)
    if source.stat().st_size>100000:raise ValueError('Experimental model budget exceeded')
    model=json.loads(source.read_text(encoding='utf8'))
    if model.get('schema')!=1 or model.get('algorithm')!='EXPERIMENTAL_STRUCTURE_RIDGE_1' or tuple(model['labels'])!=FAMILIES:raise ValueError('Invalid experimental contract')
    for name,shape in (('mean',(28,)),('scale',(28,)),('weights',(29,5))):
        value=np.asarray(model.get(name),float)
        if value.shape!=shape or not np.isfinite(value).all() or name=='scale' and np.any(value<=0):raise ValueError('Invalid ridge parameters')
    for name,lo,hi in (('temperature',.01,10),('reject_score',0,1),('reject_margin',0,1)):
        v=model.get(name)
        if type(v) not in (int,float) or not np.isfinite(v) or not lo<=v<=hi:raise ValueError('Invalid ridge score configuration')
    x,rows=collect(split)
    if split=='holdout' and any(r['case']['group'] in set(model['training_groups']+model['validation_groups']) for r in rows):raise ValueError('Holdout leakage')
    tracemalloc.start();cpu=time.process_time();start=time.perf_counter();p=probabilities(x,model);elapsed=time.perf_counter()-start;cpu=time.process_time()-cpu;peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    for row,scores in zip(rows,p):
        order=np.argsort(scores)[::-1];top,second=order[:2];accepted=scores[top]>=model['reject_score'] and scores[top]-scores[second]>=model['reject_margin']
        row.update(predicted=FAMILIES[top] if accepted else 'UNKNOWN',modulation='UNKNOWN',score=float(scores[top]),latency_ms=elapsed*1000/len(rows))
    result=dict(model_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),split=split,rows=rows,metrics=metrics(rows),support_reliability=support_reliability(rows),inference_batch_s=elapsed,inference_cpu_s=cpu,inference_peak_allocation_bytes=peak,limitations=model['limitations'])
    Path(output).write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(dict(split=split,accuracy=result['metrics']['accuracy'],accepted_accuracy=result['metrics']['accepted_accuracy'],abstention_rate=result['metrics']['abstention_rate'],inference_batch_s=elapsed)))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['train','evaluate']);p.add_argument('--model',required=True);p.add_argument('--split',choices=['validation','holdout'],default='validation');p.add_argument('--output',required=True);a=p.parse_args()
    if a.action=='train':train(a.model)
    evaluate(a.model,a.split,a.output)
