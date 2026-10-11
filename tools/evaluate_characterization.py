"""Reproducible hierarchy evaluation over the unchanged validation corpus."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import tracemalloc
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from rfatlas.corpus import cases,generate_case
from rfatlas.engine import classify
from rfatlas.characterization import characterize


def evaluate(output):
    rows=[];started=time.perf_counter();cpu_start=time.process_time();tracemalloc.start()
    analog={'AM','NFM','WFM'};digital={'OOK','2FSK','4FSK','BPSK','QPSK'}
    labels=['ANALOG','DIGITAL','NOISE_CANDIDATE','UNKNOWN']
    matrix={a:{b:0 for b in labels} for a in labels}
    for case in cases('validation',2):
        x=generate_case(case);begin=time.perf_counter();analysis=classify(x,case['sample_rate'],backend='dsp')
        result=characterize(analysis)
        target='ANALOG' if case['family'] in analog else 'DIGITAL' if case['family'] in digital else 'NOISE_CANDIDATE' if case['family']=='NOISE' else 'UNKNOWN'
        predicted=result['signal_type'];matrix[target][predicted]+=1
        rows.append(dict(case=case,truth=target,predicted=predicted,modulation=analysis['modulation'],
            heuristic_support=result['heuristic_support'],evidence=result['evidence'],latency_ms=(time.perf_counter()-begin)*1000))
    # Supplemental unsupported sideband/interference cases, kept out of the
    # legacy comparison matrix. Construction truth does not train thresholds.
    supplemental=[]
    rate=250000;n=np.arange(32768);rng=np.random.default_rng(927)
    for name in ('USB','LSB','QAM','INTERFERENCE'):
        for snr in (0,12,28):
            if name in ('USB','LSB'):
                sign=1 if name=='USB' else -1
                x=sum(np.exp(2j*np.pi*sign*f*n/rate) for f in (400,900,1800))/3
            elif name=='QAM':
                constellation=rng.choice((-3,-1,1,3),2048)+1j*rng.choice((-3,-1,1,3),2048)
                x=np.repeat(constellation,16)/5
            else:x=np.exp(2j*np.pi*1000*n/rate)+.7*np.exp(2j*np.pi*12000*n/rate)
            sigma=np.sqrt(np.mean(abs(x)**2)/10**(snr/10)/2)
            x=x+sigma*(rng.normal(size=len(x))+1j*rng.normal(size=len(x)))
            result=characterize(classify(x,rate,backend='dsp'))
            supplemental.append(dict(construction=name,snr_db=snr,result=result,
                limitation='Unsupported/ambiguous family: any accepted modulation is a false specific-family claim.'))
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    sources={name:hashlib.sha256((Path(__file__).resolve().parents[1]/'rfatlas'/name).read_bytes()).hexdigest()
        for name in ('engine.py','features.py','recovery.py','corpus.py','synthetic.py','characterization.py')}
    total=len(rows);accepted=[r for r in rows if r['predicted']!='UNKNOWN']
    noise=[r for r in rows if r['truth']=='NOISE_CANDIDATE']
    report=dict(source='SYNTHETIC_VALIDATION_ONLY',cases=total,confusion_matrix=matrix,
        accuracy=sum(r['truth']==r['predicted'] for r in rows)/total,
        abstention_rate=sum(r['predicted']=='UNKNOWN' for r in rows)/total,
        accepted_accuracy=sum(r['truth']==r['predicted'] for r in accepted)/len(accepted) if accepted else None,
        noise_false_analog_digital_rate=sum(r['predicted'] in ('ANALOG','DIGITAL') for r in noise)/len(noise),
        modulation_accuracy=sum(r['modulation']==r['case']['target'] for r in rows)/total,
        per_class_accuracy={a:matrix[a][a]/sum(matrix[a].values()) if sum(matrix[a].values()) else None for a in labels},
        per_modulation_accuracy={a:sum(r['modulation']==r['case']['target'] for r in rows if r['case']['family']==a)/sum(r['case']['family']==a for r in rows) for a in {r['case']['family'] for r in rows}},
        latency_ms=dict(mean=float(np.mean([r['latency_ms'] for r in rows])),p95=float(np.percentile([r['latency_ms'] for r in rows],95))),
        wall_s=time.perf_counter()-started,process_cpu_s=time.process_time()-cpu_start,
        python_traced_peak_bytes=peak,source_sha256=sources,rows=rows,supplemental=supplemental,
        limitations=['No live discovery counts. No calibrated probabilities. Related impairment cases are not independent.',
            'Mixed class requires verified protocol evidence and is tested separately with RDS vectors.',
            'CW/carriers remain uncertain; SSB, QAM and OFDM recognition is not claimed.',
            'Memory measurement covers Python-traced allocations, not total native/process RSS.'])
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','supplemental')},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    evaluate(parser.parse_args().output)
