"""Grouped, stochastic structural challenge; construction truth, not field truth.

Freeze thresholds before holdout use. Prior 1.6 cases are regression only.
No generated data, model weights or private recordings ship in the package.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import numpy as np
from scipy import signal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rfatlas.recognition import classify
from rfatlas.datasets import audit_partitions,support_reliability
from tools.evaluate_recognition import metrics

LABELS=('AM','NFM','WFM','USB','LSB','CW','OOK','2FSK','4FSK','GFSK','MSK','GMSK','BPSK','QPSK','8PSK','QAM16','OFDM','OFDM_NO_CP','CARRIER','NOISE','CHIRP','PULSE','ADJACENT','IMPULSE')


def references(split):
    base={'development':17010000,'validation':17020000,'holdout':17090000}[split]
    for fi,label in enumerate(LABELS):
        for source in range(3):
            seed=base+fi*100+source;rng=np.random.default_rng(seed)
            rate=(160000,250000,1024000)[source];duration=(.24,.48,.32)[source];n=round(rate*duration)
            t=np.arange(n)/rate;baud=(1200,3500,8000)[source];sps=max(4,round(rate/baud));baud=rate/sps
            a=signal.sosfilt(signal.butter(4,2600,fs=rate,output='sos'),rng.normal(size=n))
            a/=max(np.max(abs(a)),1e-30)
            symbols=rng.integers(0,16,(n+sps-1)//sps);states=np.repeat(symbols,sps)[:n]
            if label=='AM':x=1+.75*a
            elif label in ('NFM','WFM'):x=np.exp(2j*np.pi*np.cumsum(a*(6500 if label=='NFM' else 45000))/rate)
            elif label in ('USB','LSB'):x=signal.hilbert(a);x=x if label=='USB' else x.conj()
            elif label=='CW':x=(signal.square(2*np.pi*(9+source*3)*t,duty=.4)>0).astype(complex)
            elif label=='OOK':x=(states%2).astype(complex)
            elif label in ('2FSK','4FSK','GFSK','MSK','GMSK'):
                freq=((states%4)*2-3 if label=='4FSK' else (states%2)*2-1).astype(float)
                deviation=baud/4 if label in ('MSK','GMSK') else baud*.8
                if label in ('GFSK','GMSK'):
                    taps=signal.windows.gaussian(4*sps+1,.35*sps);freq=signal.fftconvolve(freq,taps/taps.sum(),mode='same')
                x=np.exp(2j*np.pi*np.cumsum(freq*deviation)/rate)
            elif label in ('BPSK','QPSK','8PSK'):
                order={'BPSK':2,'QPSK':4,'8PSK':8}[label]
                x=np.exp(2j*np.pi*(states%order)/order)
                x=signal.sosfilt(signal.butter(4,baud*1.2,fs=rate,output='sos'),x)
            elif label=='QAM16':x=((states%4)*2-3+1j*((states//4)*2-3))/np.sqrt(10)
            elif label in ('OFDM','OFDM_NO_CP'):
                fft=(64,128,256)[source];cp=fft//(4,8,16)[source];count=(n+fft-1)//fft+1
                carriers=np.exp(2j*np.pi*rng.integers(0,4,(count,fft))/4)
                # Random data per symbol; a repeated symbol is not CP evidence.
                blocks=np.fft.ifft(carriers,axis=1)*np.sqrt(fft)
                x=(np.c_[blocks[:,-cp:],blocks] if label=='OFDM' else blocks).ravel()[7:7+n]
            elif label=='CARRIER':x=np.ones(n,complex)
            elif label=='NOISE':x=np.zeros(n,complex)
            elif label=='CHIRP':x=np.exp(2j*np.pi*(7000*t+25000*t*t/duration))
            elif label=='PULSE':x=((np.arange(n)%(round(rate*.017)))<round(rate*.001)).astype(complex)
            elif label=='ADJACENT':x=np.exp(2j*np.pi*np.cumsum(a*6000)/rate)+.9*np.exp(2j*np.pi*25000*t)
            else:
                x=np.zeros(n,complex);locations=rng.choice(n,100,replace=False);x[locations]=rng.normal(size=100)+1j*rng.normal(size=100)
            family='ANALOG' if label in ('AM','NFM','WFM','USB','LSB','CW') else 'DIGITAL' if label in LABELS[6:18] else 'CARRIER' if label=='CARRIER' else 'NOISE_CANDIDATE' if label=='NOISE' else 'UNKNOWN'
            offset=(-19000,31000,-55000)[source]
            for snr in (-3,10,25):
                power=max(float(np.mean(abs(x)**2)),.2)
                y=.12*(x*np.exp(2j*np.pi*offset*t)+np.sqrt(power/(2*10**(snr/10)))*(rng.normal(size=n)+1j*rng.normal(size=n)))
                c=dict(id=f'{split}-{label}-{source}-{snr}',family=label,truth=family,group=f'advanced-source-{seed}',partition={'development':'TRAINING','validation':'VALIDATION','holdout':'TEST'}[split],seed=seed,snr_db=snr,sample_rate=rate,duration_s=duration,offset_hz=offset,source='ORIGINAL_STOCHASTIC_STRUCTURE_GENERATOR_V1',split=split)
                yield c,np.asarray(y,np.complex64)


def evaluate(output,split,baseline=None,model=None):
    methods={'current':classify}
    if baseline:
        folder=Path(baseline)/'rfatlas'
        spec=importlib.util.spec_from_file_location('campaign17baseline',folder/'__init__.py',submodule_search_locations=[str(folder)])
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        from campaign17baseline.recognition import classify as old
        methods['rc2']=old
    if model:
        from functools import partial
        methods['existing-ml']=partial(classify,backend='ml',model_path=model)
    rows={k:[] for k in methods};manifest=[];start=time.perf_counter();cpu=time.process_time()
    for c,x in references(split):
        manifest.append(dict(c,recording_id=c['id'],sha256=hashlib.sha256(x.tobytes()).hexdigest(),samples=len(x),label_status='INDEPENDENT_LABEL'))
        for name,method in methods.items():
            begin=time.perf_counter();a=method(x,c['sample_rate'])
            prediction=a['signal_class']
            if name=='existing-ml':
                from tools.evaluate_recognition import truth
                prediction=truth(a['modulation']) if a['modulation']!='UNKNOWN' else 'UNKNOWN'
            rows[name].append(dict(case=c,truth=c['truth'],predicted=prediction,modulation=a['modulation'],score=a['confidence'],latency_ms=(time.perf_counter()-begin)*1000,evidence=a['evidence'],structure=a.get('structural_features')))
    report=dict(source='SYNTHETIC_CONSTRUCTION_ONLY',split=split,manifest=manifest,partition_audit=audit_partitions(manifest),rows=rows,methods={k:dict(metrics(v),support_reliability=support_reliability(v)) for k,v in rows.items()},wall_s=time.perf_counter()-start,cpu_s=time.process_time()-cpu,limitations=['No independent real-world analog/digital accuracy claim.','CW truth ANALOG remains strict; keyed-carrier fallback is not exact CW recognition.','No-CP OFDM is DIGITAL even though CP recognizer cannot identify it.','Chirp/pulse/adjacent/impulse have UNKNOWN family truth: their waveform alone does not establish analog/digital content.'])
    Path(output).write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:dict(accuracy=v['accuracy'],per_class=v['per_class'],noise_false_analog_digital_rate=v['noise_false_analog_digital_rate']) for k,v in report['methods'].items()}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--split',choices=['development','validation','holdout'],default='validation');p.add_argument('--baseline');p.add_argument('--model');a=p.parse_args();evaluate(a.output,a.split,a.baseline,a.model)
