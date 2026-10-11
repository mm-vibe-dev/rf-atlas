"""Opt-in original NumPy experiments. No downloaded weights or third-party code.

Development seeds only train; validation and holdout are separately generated.
This script never changes the production classifier or frozen benchmarks.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import tracemalloc
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.signal import welch
from rfatlas.corpus import cases,generate_case
from rfatlas.corpus import SPLIT_SEEDS
from rfatlas.ml import LABELS,feature_vector,train,classify as ml_classify
from rfatlas.engine import classify
from rfatlas.fingerprints import iq_fingerprint
from evaluate_engine import summarize


def representation(x):
    x=x[:4096];x=x/max(float(np.sqrt(np.mean(abs(x)**2))),1e-12)
    phase=np.angle(x[1:]*x[:-1].conj())
    _,p=welch(x,nperseg=256,return_onesided=False,detrend=False)
    arrays=(x.real,x.imag,10*np.log10(np.maximum(np.fft.fftshift(p),1e-12)),abs(x),phase)
    return np.stack([np.array([np.mean(v) for v in np.array_split(a,64)]) for a in arrays],axis=1)


def improved(x,rate):
    fp=iq_fingerprint(x,rate)
    return np.r_[feature_vector(x,rate),[fp[k] for k in ('spectral_entropy','spectral_flatness',
        'spectral_concentration','noise_floor_variation_db')],fp['carrier_offset_hz']/rate,
        fp['instantaneous_frequency_iqr_hz']/rate]


def fit(X,y,cnn=False,steps=400,max_seconds=120):
    rng=np.random.default_rng(120619);mean=X.mean(axis=(0,1) if cnn else 0)
    scale=np.maximum(X.std(axis=(0,1) if cnn else 0),1e-4)
    X=np.clip((X-mean)/scale,-8,8);target=np.eye(10)[y]
    if cnn:
        params=[rng.normal(0,.08,(12,5,5)),np.zeros(12),rng.normal(0,.08,(48,10)),np.zeros(10)]
    else:params=[rng.normal(0,.1,(X.shape[1],40)),np.zeros(40),rng.normal(0,.1,(40,10)),np.zeros(10)]
    first=[np.zeros_like(p) for p in params];second=[np.zeros_like(p) for p in params]
    begin=time.perf_counter()
    for step in range(1,steps+1):
        if time.perf_counter()-begin>max_seconds:break
        batch=rng.choice(len(X),min(60,len(X)),replace=False);xb=X[batch];yb=target[batch]
        a,b,c,d=params
        if cnn:
            windows=np.lib.stride_tricks.sliding_window_view(xb,5,axis=1)
            z=np.einsum('ntck,fck->ntf',windows,a,optimize=True)+b
            active=np.maximum(z,0);h=active.reshape(len(xb),4,15,12).mean(2).reshape(len(xb),48)
        else:h=np.tanh(xb@a+b)
        logits=h@c+d;probs=np.exp(logits-logits.max(1,keepdims=True));probs/=probs.sum(1,keepdims=True)
        delta=(probs-yb)/len(xb);dh=delta@c.T
        if cnn:
            dz=np.repeat(dh.reshape(len(xb),4,1,12)/15,15,axis=2).reshape(z.shape)*(z>0)
            da=np.einsum('ntf,ntck->fck',dz,windows,optimize=True);db=dz.sum((0,1))
        else:
            dz=dh*(1-h*h);da=xb.T@dz;db=dz.sum(0)
        grads=[da+1e-4*a,db,h.T@delta+1e-4*c,delta.sum(0)]
        for index,gradient in enumerate(grads):
            first[index]=.9*first[index]+.1*gradient;second[index]=.999*second[index]+.001*gradient*gradient
            params[index]-=.008*(first[index]/(1-.9**step))/(np.sqrt(second[index]/(1-.999**step))+1e-8)
    return dict(mean=mean,scale=scale,params=params,cnn=cnn,steps=step,seconds=time.perf_counter()-begin)


def predict(model,X):
    x=np.clip((X-model['mean'])/model['scale'],-8,8);a,b,c,d=model['params']
    if model['cnn']:
        w=np.lib.stride_tricks.sliding_window_view(x[None],5,axis=1)
        active=np.maximum(np.einsum('ntck,fck->ntf',w,a,optimize=True)+b,0)
        h=active.reshape(1,4,15,12).mean(2).reshape(48)
    else:h=np.tanh(x@a+b)
    logits=h@c+d;scores=np.exp(logits-logits.max());scores/=scores.sum()
    top=np.argsort(scores)[::-1]
    return LABELS[top[0]] if scores[top[0]]>=.55 and scores[top[0]]-scores[top[1]]>=.12 else 'UNKNOWN'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    # Fresh seed namespace, distinct from all frozen historical corpus seeds.
    for split in SPLIT_SEEDS:SPLIT_SEEDS[split]+=12_000_000
    tracemalloc.start();begin=time.perf_counter();training=cases('development',1)
    baseline=train(training,args.output/'existing-ml.npz',max_seconds=120,epochs=400)
    features=[];representations=[];labels=[];entropy={}
    for case in training:
        x=generate_case(case);features.append(improved(x,case['sample_rate']));representations.append(representation(x));labels.append(LABELS.index(case['target']))
        if case['profile']=='clean' and case['snr_db']==28:entropy[case['family']]=iq_fingerprint(x,case['sample_rate'])
    models={'features':fit(np.asarray(features),labels),'cnn':fit(np.asarray(representations),labels,cnn=True)}
    rows=[];splits={}
    for split in ('validation','holdout'):
        split_rows=[]
        for case in cases(split,1):
            x=generate_case(case)
            for backend in ('dsp','ml','features','cnn'):
                start=time.perf_counter()
                if backend=='dsp':result=classify(x,case['sample_rate']);predicted=result['modulation'];detected=result['signal_detected']
                elif backend=='ml':result=ml_classify(x,case['sample_rate'],args.output/'existing-ml.npz');predicted=result['modulation'];detected=result['signal_detected']
                else:
                    v=improved(x,case['sample_rate']) if backend=='features' else representation(x)
                    predicted=predict(models[backend],v)
                    from rfatlas.features import extract_features
                    f=extract_features(x,case['sample_rate']);detected=f['peak_snr_db']>=10 and f['spectral_flatness']<.65
                    if not detected or f['adc_rail_fraction']>.01:predicted='UNKNOWN'
                row=dict(case_id=case['id'],split=split,family=case['family'],target=case['target'],snr_db=case['snr_db'],engine=backend,
                    predicted=predicted,signal_detected=bool(detected),runtime_ms=(time.perf_counter()-start)*1000)
                split_rows.append(row)
        rows.extend(split_rows);splits[split]={backend:summarize(split_rows,backend) for backend in ('dsp','ml','features','cnn')}
        print(split,{k:round(v['accuracy'],3) for k,v in splits[split].items()},flush=True)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    report=dict(source='SYNTHETIC_ONLY',split_seeds=dict(SPLIT_SEEDS),training_cases=len(training),evaluation=splits,entropy_clean_snr28=entropy,
        experiments={k:{key:model[key] for key in ('steps','seconds')} for k,model in models.items()},
        numpy_parameter_bytes={k:sum(p.nbytes for p in model['params'])+model['mean'].nbytes+model['scale'].nbytes for k,model in models.items()},
        seconds=time.perf_counter()-begin,python_traced_peak_bytes=peak,rows=rows,
        limitations=['Independent random seeds, shared synthetic family generators; no over-the-air generalization claim.',
            'No tuning on validation/holdout. Fixed .55 score / .12 margin, existing DSP detection/rail gate.',
            'CNN: five 64-bin views (I,Q,FFT,envelope,instantaneous phase), 12 trainable width-5 kernels, four mean-pool regions and softmax.',
            'Python-traced memory excludes some native allocations and is the whole experiment peak, not per-backend RSS.',
            'CNN timings include representation and detection gate. Optional experiments never replace default DSP.'])
    (args.output/'recognition.json').write_text(json.dumps(report,indent=2,allow_nan=False))


if __name__=='__main__':main()
