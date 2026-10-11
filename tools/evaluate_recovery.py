"""Independent FFT-audio and pulse-shaped reference construction for rc2.

No private recordings, identifiers, downloaded data or learned parameters.
Splits group all SNR/offset variants of a source together.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
from scipy import signal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.evaluate_recognition import metrics
from rfatlas.recognition import classify

LABELS=('AM','NFM','WFM','USB','LSB','CW','OOK','2FSK','4FSK','GFSK','BPSK','QPSK','OFDM','CARRIER','NOISE','ARTIFACT')


def references(split):
    base={'development':940000,'validation':950000,'holdout':980000}[split]
    for i,label in enumerate(LABELS):
        for source in range(3):
            seed=base+i*100+source;rng=np.random.default_rng(seed)
            rate=250000;n=75000;t=np.arange(n)/rate
            # Construct independent colored audio directly in the FFT domain.
            f=np.fft.rfftfreq(n,1/rate);spectrum=rng.normal(size=len(f))+1j*rng.normal(size=len(f))
            spectrum*=((f>150)&(f<2600))/(1+(f/1100)**2)
            audio=np.fft.irfft(spectrum,n);audio/=max(np.max(abs(audio)),1e-15)
            symbols=rng.integers(0,4,(n+99)//100);index=np.arange(n)//100;states=symbols[index]
            if label=='AM':x=1+( .35+.2*source)*audio
            elif label in ('NFM','WFM'):x=np.exp(2j*np.pi*np.cumsum(audio*(5000 if label=='NFM' else 55000))/rate)
            elif label in ('USB','LSB'):
                x=signal.hilbert(audio);x=x if label=='USB' else x.conj()
            elif label=='CW':x=((np.floor(t*12)%3)!=0).astype(complex)
            elif label=='OOK':x=(states%2).astype(complex)
            elif label in ('2FSK','4FSK','GFSK'):
                v=(np.array([-1,1])[states%2] if label!='4FSK' else np.array([-3,-1,1,3])[states])*2000.
                if label=='GFSK':v=signal.fftconvolve(v,signal.windows.gaussian(101,18)/signal.windows.gaussian(101,18).sum(),mode='same')
                x=np.exp(2j*np.pi*np.cumsum(v)/rate)
            elif label in ('BPSK','QPSK'):
                x=np.exp(2j*np.pi*(states%(2 if label=='BPSK' else 4))/(2 if label=='BPSK' else 4))
                x=signal.sosfilt(signal.butter(3,7000,fs=rate,output='sos'),x)
            elif label=='OFDM':
                carriers=rng.choice([-1,1],(700,64))+1j*rng.choice([-1,1],(700,64));blocks=np.fft.ifft(carriers,axis=1)
                x=np.concatenate([blocks[:,-16:],blocks],axis=1).ravel()[:n]
                x=np.resize(x,n);x/=np.sqrt(np.mean(abs(x)**2))
            elif label=='CARRIER':x=np.ones(n,complex)
            elif label=='NOISE':x=np.zeros(n,complex)
            else:x=np.exp(2j*np.pi*900*t)+.6*np.exp(2j*np.pi*15900*t)
            family='ANALOG' if label in ('AM','NFM','WFM','USB','LSB','CW') else 'DIGITAL' if label in ('OOK','2FSK','4FSK','GFSK','BPSK','QPSK','OFDM') else 'CARRIER' if label=='CARRIER' else 'NOISE_CANDIDATE' if label=='NOISE' else 'UNKNOWN'
            for snr in (3,15,30):
                offset=(-17000,0,23000)[source];noise=rng.normal(size=n)+1j*rng.normal(size=n)
                power=max(np.mean(abs(x)**2),.2);y=.16*(x*np.exp(2j*np.pi*offset*t)+np.sqrt(power/2/10**(snr/10))*noise)
                c=dict(id=f'{split}-{label}-{source}-{snr}',group=f'fft-reference-{seed}',seed=seed,split=split,family=label,truth=family,snr_db=snr,sample_rate=rate,offset_hz=offset,duration_s=n/rate,provenance='ORIGINAL_FFT_AUDIO_PULSE_SHAPED_GENERATOR',label_basis='Construction; CW treated as keyed analog telegraphy; OFDM digital even when unsupported')
                yield c,np.asarray(y,np.complex64)


def evaluate(path,split):
    rows=[];manifest=[]
    for c,x in references(split):
        start=time.perf_counter();a=classify(x,c['sample_rate'])
        rows.append(dict(case=c,truth=c['truth'],predicted=a['signal_class'],modulation=a['modulation'],score=a['confidence'],latency_ms=(time.perf_counter()-start)*1000,evidence=a['evidence']))
        manifest.append(dict(c,sha256=hashlib.sha256(x.tobytes()).hexdigest(),dtype='complex64',samples=len(x)))
    m=metrics(rows);m['per_modulation']={label:dict(support=sum(r['case']['family']==label for r in rows),family_recall=float(np.mean([r['predicted']==r['truth'] for r in rows if r['case']['family']==label])),modulation_recall=float(np.mean([r['modulation']==label for r in rows if r['case']['family']==label]))) for label in LABELS}
    Path(path).write_text(json.dumps(dict(split=split,metrics=m,manifest=manifest,rows=rows),indent=2),encoding='utf8')
    print(json.dumps({k:m[k] for k in ('accuracy','per_class','abstention_rate','accepted_accuracy','noise_false_analog_digital_rate','digital_false_analog_rate')}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('--split',choices=['development','validation','holdout'],default='validation');args=p.parse_args();evaluate(args.output,args.split)
