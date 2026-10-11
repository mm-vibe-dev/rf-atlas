"""Original bounded synthetic RF training; no private recordings or downloads."""
import hashlib,json,time
from pathlib import Path
import numpy as np
from scipy import signal
from .ai import VERSION,FAMILIES,MODULATIONS,vector
from .recognition import classify

LABELS=('AM','NFM','WFM','USB','LSB','CW','OOK','2FSK','4FSK','GFSK','MSK','GMSK','BPSK','QPSK','8PSK','QAM16','OFDM','OFDM_NO_CP','CARRIER','NOISE','CHIRP','PULSE','ADJACENT','IMPULSE')


def references(split):
    base={'development':19110000,'validation':19120000,'holdout':19199000}[split]
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


def train(path,max_seconds=300):
    path=Path(path)
    if path.exists():raise FileExistsError('Model exists; preserve it or choose a new runtime root')
    if not 1<=max_seconds<=600:raise ValueError('Training deadline must be 1..600 seconds')
    began=time.monotonic();rows=[];features=[]
    for case,iq in references('development'):
        analysis=classify(iq,case['sample_rate'],analysis_level='forensic',max_seconds=2)
        if analysis.get('structural_features',{}).get('budget_exhausted'):raise TimeoutError('Incomplete structural bank; refusing nondeterministic partial training features')
        features.append(np.round(vector(analysis),12));rows.append(case)
        if time.monotonic()-began>max_seconds:raise TimeoutError('Bounded synthetic training deadline exceeded')
    x=np.asarray(features);mean=x.mean(0);scale=np.maximum(x.std(0),.05);z=np.c_[np.clip((x-mean)/scale,-8,8),np.ones(len(x))]
    def fit(labels,targets):
        y=np.asarray([labels.index(t) for t in targets]);counts=np.bincount(y,minlength=len(labels));weights=len(y)/(len(labels)*counts[y]);ridge=np.eye(29);ridge[-1,-1]=.01
        return np.round(np.linalg.solve(z.T@(z*weights[:,None])+ridge,z.T@(np.eye(len(labels))[y]*weights[:,None])),12).tolist()
    model=dict(schema=1,algorithm=VERSION,families=FAMILIES,modulations=MODULATIONS,mean=mean.tolist(),scale=scale.tolist(),family_weights=fit(FAMILIES,[r['truth'] for r in rows]),modulation_weights=fit(MODULATIONS,[r['family'] if r['family'] in MODULATIONS else 'UNKNOWN' for r in rows]),reject_score=.65,reject_margin=.15,score_temperature=.2,
        training_groups=sorted({r['group'] for r in rows}),training_manifest_sha256=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),training_seed_base=19110000,license='MIT; original project synthetic generator and derived weights',calibration='UNCALIBRATED',limitations='Synthetic-only construction truth; no private IQ training or broad field validation.')
    raw=json.dumps(model,sort_keys=True,allow_nan=False).encode();path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);digest=hashlib.sha256(raw).hexdigest();path.with_suffix('.sha256').write_text(digest+'\n');return dict(model_sha256=digest,examples=len(rows),groups=len(model['training_groups']),seconds=time.monotonic()-began,bytes=len(raw))
