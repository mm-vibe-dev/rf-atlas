"""Before/after comparison; preserve frozen generators and separate split provenance."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from rfatlas.corpus import cases,generate_case
from rfatlas.engine import classify as frozen
from rfatlas.recognition import classify
from rfatlas.characterization import characterize

FAMILIES=('ANALOG','DIGITAL','CARRIER','NOISE_CANDIDATE','UNKNOWN')


def truth(family):
    return 'ANALOG' if family in ('AM','NFM','WFM') else 'DIGITAL' if family in ('OOK','2FSK','4FSK','BPSK','QPSK','GFSK') else 'CARRIER' if family=='CW' else 'NOISE_CANDIDATE' if family=='NOISE' else 'UNKNOWN'


def metrics(rows):
    matrix={a:{b:0 for b in FAMILIES} for a in FAMILIES}
    for r in rows:matrix[r['truth']][r['predicted']]+=1
    per={}
    for a in FAMILIES:
        actual=sum(matrix[a].values());accepted=sum(matrix[b][a] for b in FAMILIES);correct=matrix[a][a]
        precision=correct/accepted if accepted else 0;recall=correct/actual if actual else 0
        per[a]=dict(precision=precision,recall=recall,f1=2*precision*recall/(precision+recall) if precision+recall else 0,support=actual)
    accepted=[r for r in rows if r['predicted']!='UNKNOWN'];durations=[r['latency_ms'] for r in rows]
    noise=[r for r in rows if r['truth']=='NOISE_CANDIDATE'];digital=[r for r in rows if r['truth']=='DIGITAL']
    return dict(count=len(rows),confusion_matrix=matrix,per_class=per,accuracy=sum(r['truth']==r['predicted'] for r in rows)/len(rows),balanced_accuracy=float(np.mean([v['recall'] for v in per.values() if v['support']])),macro_f1=float(np.mean([v['f1'] for v in per.values() if v['support']])),abstention_rate=1-len(accepted)/len(rows),accepted_accuracy=sum(r['truth']==r['predicted'] for r in accepted)/len(accepted) if accepted else None,noise_false_analog_digital_rate=sum(r['predicted'] in ('ANALOG','DIGITAL') for r in noise)/len(noise) if noise else None,digital_false_analog_rate=sum(r['predicted']=='ANALOG' for r in digital)/len(digital) if digital else None,modulation_accuracy=sum(r['modulation']==r['case']['family'] for r in rows if r['case']['family']!='NOISE')/max(1,sum(r['case']['family']!='NOISE' for r in rows)),latency_ms=dict(mean=float(np.mean(durations)),p95=float(np.quantile(durations,.95))),by_snr={str(snr):dict(accuracy=float(np.mean([r['truth']==r['predicted'] for r in rows if r['case']['snr_db']==snr])),cases=sum(r['case']['snr_db']==snr for r in rows)) for snr in sorted({r['case']['snr_db'] for r in rows})})


def independent_cases(split):
    """Different stochastic audio/symbol synthesis; no frozen-generator reuse.

    Synthetic independence only; not independent over-air emitters or sessions.
    """
    from scipy import signal
    seedbase={'development':810000,'validation':820000,'holdout':830000}[split]
    for fi,family in enumerate(('AM','NFM','WFM','2FSK','4FSK','BPSK','QPSK','OOK','CW','NOISE','SSB','QAM','OFDM','INTERFERENCE')):
        for si,snr in enumerate((0,12,28)):
            for repeat in range(3):
                seed=seedbase+fi*1000+si*20+repeat;rng=np.random.default_rng(seed);rate=int(rng.choice([160000,240000]));n=round(rate*.16);t=np.arange(n)/rate
                audio=signal.sosfilt(signal.butter(4,2200,fs=rate,output='sos'),rng.normal(size=n));audio/=max(np.std(audio)*2.5,1e-12);audio=np.tanh(audio)
                baud=int(rng.choice([1800,3600,7200]));symbols=rng.integers(0,4,int(n*baud/rate)+2);indices=np.minimum((t*baud).astype(int),len(symbols)-1)
                if family=='AM':x=1+.65*audio
                elif family in ('NFM','WFM'):x=np.exp(2j*np.pi*np.cumsum(audio*(3300 if family=='NFM' else 35000))/rate)
                elif family in ('2FSK','4FSK'):
                    levels=np.array([-1,1]) if family=='2FSK' else np.array([-3,-1,1,3]);x=np.exp(2j*np.pi*np.cumsum(levels[symbols[indices]%len(levels)]*1800)/rate)
                elif family in ('BPSK','QPSK'):x=np.exp(2j*np.pi*(symbols[indices]%(2 if family=='BPSK' else 4))/(2 if family=='BPSK' else 4))
                elif family=='OOK':x=symbols[indices]%2
                elif family=='CW':x=np.ones(n)
                elif family=='NOISE':x=np.zeros(n)
                elif family=='SSB':x=signal.hilbert(audio)
                elif family=='QAM':x=(rng.choice([-3,-1,1,3],len(symbols))[indices]+1j*rng.choice([-3,-1,1,3],len(symbols))[indices])/3
                elif family=='OFDM':x=np.tile(np.fft.ifft(rng.choice([-1,1],64)+1j*rng.choice([-1,1],64)),(n+63)//64)[:n]*4
                else:x=np.exp(2j*np.pi*1500*t)+.7*np.exp(2j*np.pi*13000*t)
                offset=rng.uniform(-21000,21000);x=x*np.exp(2j*np.pi*(offset*t+.3))
                power=max(float(np.mean(abs(x)**2)),.2);noise=np.sqrt(power/(2*10**(snr/10)))*(rng.normal(size=n)+1j*rng.normal(size=n));x=.18*(x+noise)
                if repeat==1:x*=.12
                elif repeat==2:x=(np.round(np.clip(x.real,-1,1)*127)+1j*np.round(np.clip(x.imag,-1,1)*127))/127
                c=dict(id=f'{split}-independent-{seed}',split=split,seed=seed,family=family,snr_db=snr,profile='independent-audio-symbols-'+str(repeat),sample_rate=rate,duration_s=.16)
                yield c,np.asarray(x,np.complex64)


def evaluate(output,split='validation',model=None,prototype=None,independent=False):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);rows={k:[] for k in ['frozen','dsp16']+(['existing-ml','legacy-hybrid'] if model else [])+(['rf-prototype'] if prototype else [])};started=time.perf_counter();cpu=time.process_time()
    source=independent_cases(split) if independent else ((c,generate_case(c)) for c in cases(split,2))
    for case,x in source:
        for method,values in rows.items():
            begin=time.perf_counter()
            if method=='frozen':a=frozen(x,case['sample_rate']);a['signal_class']=characterize(a)['signal_type'];a['signal_class']='CARRIER' if a['modulation']=='CW' else a['signal_class']
            else:a=classify(x,case['sample_rate'],backend='rfai' if method=='rf-prototype' else 'ml' if method=='existing-ml' else 'hybrid' if method=='legacy-hybrid' else 'dsp',model_path=prototype if method=='rf-prototype' else model)
            # The old ML exposes only modulation; this mapping is an explicit hypothesis.
            predicted=truth(a['modulation']) if method=='existing-ml' and a['modulation']!='UNKNOWN' else a.get('signal_class','UNKNOWN')
            values.append(dict(case=case,truth=truth(case['family']),predicted=predicted,modulation=a['modulation'],score=a['confidence'],latency_ms=(time.perf_counter()-begin)*1000,evidence=a['evidence']))
    report=dict(source='INDEPENDENT_SYNTHETIC_GENERATOR' if independent else 'FROZEN_SYNTHETIC_CORPUS',split=split,methods={k:metrics(v) for k,v in rows.items()},wall_s=time.perf_counter()-started,process_cpu_s=time.process_time()-cpu,rows=rows,limitations=['Synthetic construction truth is not real-world accuracy.','New seed splits of the frozen generator are not independent emitter sessions.','Carrier is reported separately; original 1.5 four-class benchmark remains unchanged.','Optional models are uncalibrated and experimental; no synthetic result verifies a protocol.'])
    output.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps({k:{p:v[p] for p in ['accuracy','abstention_rate','accepted_accuracy','noise_false_analog_digital_rate']} for k,v in report['methods'].items()}));return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--split',choices=['development','validation','holdout'],default='validation');p.add_argument('--model');p.add_argument('--prototype');p.add_argument('--independent',action='store_true');a=p.parse_args();evaluate(a.output,a.split,a.model,a.prototype,a.independent)
