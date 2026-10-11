"""Frozen synthetic timing diagnostic challenge; no field accuracy claims.

Run after freezing the implementation. Construction labels are evaluation-only;
the application receives the ordinary blind DSP classification, never truth.
No generated samples or model weights are retained.
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
from rfatlas.recognition import classify
from rfatlas.deep_investigation import measure


def evaluate():
    rows=[];started=time.monotonic();tracemalloc.start()
    for kind in ('OOK','2FSK','4FSK','AM','NFM','CARRIER','NOISE'):
        for source,(rate,baud,cfo) in enumerate(((48000,1200,3700),(250000,2400,-19000),(1024000,4800,31000))):
            for snr in (0,12,30):
                seed=17022000+1000*('OOK','2FSK','4FSK','AM','NFM','CARRIER','NOISE').index(kind)+source
                rng=np.random.default_rng(seed);n=min(65536,int(rate*.4));sps=round(rate/baud);actual_baud=rate/sps;t=np.arange(n)/rate
                states=np.repeat(rng.integers(0,4,(n+sps-1)//sps),sps)[:n]
                if kind=='OOK':x=(states%2).astype(complex)
                elif kind in ('2FSK','4FSK'):
                    levels=(states%2*2-1) if kind=='2FSK' else (states*2-3)
                    x=np.exp(2j*np.pi*np.cumsum(levels*actual_baud*.75)/rate)
                elif kind=='AM':x=(1+.6*np.cos(2*np.pi*733*t)).astype(complex)
                elif kind=='NFM':x=np.exp(2j*np.pi*np.cumsum(4500*np.sin(2*np.pi*733*t))/rate)
                elif kind=='CARRIER':x=np.ones(n,complex)
                else:x=rng.normal(size=n)+1j*rng.normal(size=n)
                x=x*np.exp(2j*np.pi*cfo*t)
                noise=rng.normal(size=n)+1j*rng.normal(size=n)
                x=x+noise*np.sqrt(np.mean(abs(x)**2)/(2*10**(snr/10)))
                x=x*.7/max(abs(x.real).max(),abs(x.imag).max(),1e-30)
                # Match ADC quantization without assigning model-generated labels.
                x=(np.rint(x.real*127.5+127.5)-127.5)/127.5+1j*(np.rint(x.imag*127.5+127.5)-127.5)/127.5
                began=time.monotonic();a=classify(x,rate,{'desired_frequency_hz':471234567,'center_hz':471234567,'format':'cu8'})
                m=measure(x,rate,a,471234567);candidates=m['symbol_rate_candidates']
                expected=actual_baud if kind in ('OOK','2FSK','4FSK') else None
                rows.append(dict(kind=kind,source_group=f'{kind}-{source}',seed=seed,snr_db=snr,rate_hz=rate,
                    expected_baud=expected,classified_modulation=a['modulation'],classified_family=a['signal_class'],
                    timing_candidates=candidates,within_10_percent=bool(expected and any(abs(c['approximate_rate_baud']/expected-1)<.1 for c in candidates)),
                    elapsed_ms=(time.monotonic()-began)*1000,input_sha256=hashlib.sha256(x.tobytes()).hexdigest()))
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    digital=[r for r in rows if r['expected_baud'] is not None];negative=[r for r in rows if r['expected_baud'] is None]
    accepted=[r for r in digital if r['timing_candidates']]
    return dict(scope='63 synthetic construction-truth cases, 21 source groups; SNR variants share their source. No training, threshold tuning, field accuracy or symbol synchronization claim.',
        source_sha256={name:hashlib.sha256((Path(__file__).resolve().parents[1]/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for name in ('rfatlas/deep_investigation.py','rfatlas/recognition.py','rfatlas/advanced_dsp.py')},
        timing=dict(digital_cases=len(digital),candidates=len(accepted),within_10_percent=sum(r['within_10_percent'] for r in digital),
            abstentions=len(digital)-len(accepted),negative_cases=len(negative),negative_timing_candidates=sum(bool(r['timing_candidates']) for r in negative)),
        wall_s=time.monotonic()-started,python_peak_allocation_bytes=peak,cases=rows,
        limitations=['Conditional diagnostic availability depends on the unchanged DSP classifier.',
            'Integer run harmonics and analog threshold crossings remain alternatives.',
            'This timing estimator does not recover protocol frames or calibrated symbol synchronization.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path);args=parser.parse_args()
    if args.output.exists():raise SystemExit('Output already exists; use a new versioned evaluation path')
    result=evaluate();args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf8')
    print(json.dumps(result['timing'],indent=2))
