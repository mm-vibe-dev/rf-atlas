"""Grouped synthetic CPU model benchmark; no real-world accuracy inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rfatlas.training import references
from rfatlas.recognition import classify
from rfatlas.ai import infer,load_model
from tools.evaluate_recognition import metrics


def evaluate(model_path,split):
    model,digest=load_model(model_path);rows={k:[] for k in ('dsp','optional_ai','agreement_hybrid')};groups=set();began=time.monotonic()
    for case,iq in references(split):
        assert case['group'] not in model['training_groups'],'Training/evaluation group leakage'
        case=dict(case,input_sha256=hashlib.sha256(np.asarray(iq,dtype='<c8').tobytes()).hexdigest())
        groups.add(case['group']);started=time.monotonic();a=classify(iq,case['sample_rate']);elapsed=(time.monotonic()-started)*1000
        a['adc_amplitude_p95_counts']=float(np.quantile(abs(iq),.95)*128)
        ml=infer(a,dict(sample_rate=case['sample_rate'],sample_count=len(iq)),model_path)
        for method in rows:
            predicted=(a['signal_class'] if method=='dsp' else ml['predicted_family'] if method=='optional_ai'
                else a['signal_class'] if a['signal_class']==ml['predicted_family'] else 'UNKNOWN')
            rows[method].append(dict(case=case,truth=case['truth'],predicted=predicted,
                modulation=a['modulation'] if method=='dsp' else ml['predicted_modulation'],
                latency_ms=elapsed+(ml['inference_duration_ms'] if method!='dsp' else 0)))
    return dict(schema=1,split=split,source_groups=sorted(groups),model_sha256=digest,
        manifest_sha256=hashlib.sha256(json.dumps([r['case'] for r in rows['dsp']],sort_keys=True).encode()).hexdigest(),
        methods={k:metrics(v) for k,v in rows.items()},rows=rows,wall_s=time.monotonic()-began,
        limitations=['Construction truth from one original generator family; SNR variants share source groups.',
            'Uncalibrated synthetic scores; holdout is not independent real emitter/session evidence.',
            'Agreement-only hybrid is an evaluation comparison, not the application default.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--split',choices=['validation','holdout'],default='holdout');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=evaluate(args.model,args.split);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False));print(json.dumps({k:v['accuracy'] for k,v in report['methods'].items()}))
