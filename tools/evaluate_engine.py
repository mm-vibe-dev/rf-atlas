"""Streaming baseline/DSP/optional ML comparison with explicit untouched split."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tracemalloc
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from rfatlas.corpus import cases, stress_cases, generate_case, FAMILIES
from rfatlas.diagnostics import atomic_json

LABELS=FAMILIES[:-1]+('UNKNOWN',)


def wilson(successes,total):
    if not total:return [0.,1.]
    z=1.959964;p=successes/total;den=1+z*z/total
    center=(p+z*z/(2*total))/den
    delta=z*np.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return [max(0.,float(center-delta)),min(1.,float(center+delta))]


def summarize(rows, engine):
    selected=[r for r in rows if r['engine']==engine];total=len(selected)
    matrix={a:{b:0 for b in LABELS} for a in LABELS}
    for r in selected:matrix[r['target']][r['predicted']]+=1
    per={}
    for a in LABELS:
        tp=matrix[a][a];n=sum(matrix[a].values());pred=sum(matrix[b][a] for b in LABELS)
        precision=tp/pred if pred else 0.;recall=tp/n if n else 0.
        per[a]={'cases':n,'accuracy':recall,'precision':precision,'recall':recall,
            'f1':2*precision*recall/(precision+recall) if precision+recall else 0.,'accuracy_wilson95':wilson(tp,n)}
    accepted=[r for r in selected if r['predicted']!='UNKNOWN'];noise=[r for r in selected if r['family']=='NOISE']
    correct=sum(r['predicted']==r['target'] for r in selected)
    accepted_correct=sum(r['predicted']==r['target'] for r in accepted)
    signals=[r for r in selected if r['family']!='NOISE']
    return {'cases':total,'accuracy':correct/total if total else None,'accuracy_wilson95':wilson(correct,total),
        'accuracy_definition':'Legacy all-case exact-label accuracy; UNKNOWN is correct for NOISE and incorrect for known modulation families. This is not forced-choice accuracy.',
        'forced_choice_accuracy':None,
        'forced_choice_accuracy_explanation':'No forced labels were collected. The predictor can abstain; an UNKNOWN output cannot be retrospectively replaced by its uncollected best known-family prediction.',
        'coverage':len(accepted)/total if total else None,
        'selective_accuracy':accepted_correct/len(accepted) if accepted else None,
        'correct_and_accepted_fraction':accepted_correct/total if total else None,
        'accepted_cases':len(accepted),'correct_and_accepted_cases':accepted_correct,
        'rejected_cases':total-len(accepted),
        'macro_f1':float(np.mean([v['f1'] for v in per.values()])), 'per_class':per,'confusion_matrix':matrix,
        'rejection_rate':1-len(accepted)/total if total else None,'signal_rejection_rate':sum(r['predicted']=='UNKNOWN' for r in signals)/len(signals) if signals else None,
        'accepted_prediction_accuracy':accepted_correct/len(accepted) if accepted else None,
        'accepted_accuracy_wilson95':wilson(accepted_correct,len(accepted)),
        'noise_false_label_rate':sum(r['predicted']!='UNKNOWN' for r in noise)/len(noise) if noise else None,
        'noise_false_detection_rate':sum(r['signal_detected'] for r in noise)/len(noise) if noise else None,
        'noise_false_detection_wilson95':wilson(sum(r['signal_detected'] for r in noise),len(noise)),
        'by_snr':{str(snr):{'cases':len(group),'accuracy':sum(r['predicted']==r['target'] for r in group)/len(group)}
            for snr in sorted({r['snr_db'] for r in selected},key=str) if (group:=[r for r in selected if r['snr_db']==snr])},
        'runtime_ms':{'mean':float(np.mean([r['runtime_ms'] for r in selected])) if selected else None,
            'p95':float(np.percentile([r['runtime_ms'] for r in selected],95)) if selected else None}}


def baseline_functions():
    source=subprocess.check_output(['git','show','f8502e3:rfatlas/features.py'],text=True)
    namespace={};exec(compile(source,'trusted-local-baseline','exec'),namespace)
    activity_source=subprocess.check_output(['git','show','f8502e3:rfatlas/activity.py'],text=True)
    activity_source=activity_source.replace('from .features import extract_features','')
    exec(compile(activity_source,'trusted-local-baseline-activity','exec'),namespace)
    def baseline(x,rate):
        activity=namespace['analyze_activity'](x,rate)
        return namespace['classify'](x[activity['selected_start_sample']:activity['selected_stop_sample']],rate)
    return baseline


def original_cases():
    from rfatlas.synthetic import generate,FAMILIES as original_families
    variants={'base':{},'offset_minus':{'offset':-20000},'offset_plus':{'offset':20000},'low_gain':{'gain':.05},'clipped':{'gain':2.,'clipping':True},'rate_low':{'rate':125000},'rate_high':{'rate':500000},'short':{'duration':.025},'late_burst':{'duration':.3,'burst':True}}
    for family in original_families:
        for snr in (None,5,15,30):
            for variant,changes in variants.items():
                for seed in (17,29):
                    c={'id':f'{family}-{snr}-{variant}-{seed}','family':family,'target':'UNKNOWN' if family=='NOISE' else family,'snr_db':snr,'profile':variant,'seed':seed,'sample_rate':changes.get('rate',250000),'split':'original'}
                    yield c,generate(family=family,snr_db=snr,seed=seed,**changes)


def evaluate(split,output,model_path=None,seeds_per_cell=2,original=False,backends=None,stress=False):
    from rfatlas.engine import classify
    baseline=baseline_functions();rows=[];tracemalloc.start();started=time.perf_counter()
    root=Path(__file__).resolve().parents[1]
    source_hashes={name:hashlib.sha256((root/'rfatlas'/name).read_bytes()).hexdigest()
        for name in ('engine.py','recovery.py','features.py','ml.py','corpus.py','synthetic.py')}
    engines=list(backends) if backends else ['baseline','dsp']+(['ml','hybrid'] if model_path else [])
    if not engines or any(e not in ('baseline','dsp','ml','hybrid') for e in engines):raise ValueError('Invalid benchmark backends')
    if any(e in ('ml','hybrid') for e in engines):
        if not model_path:raise ValueError('ML benchmark requires an explicit model, not fallback-only results')
        from rfatlas.ml import load_model
        load_model(model_path)
    if original and stress:raise ValueError('Choose original or independent stress corpus')
    display_split='original' if original else split+('-stress' if stress else '')
    items=original_cases() if original else ((c,generate_case(c)) for c in (stress_cases(split) if stress else cases(split,seeds_per_cell)))
    for index,(c,x) in enumerate(items):
        for engine in engines:
            begin=time.perf_counter()
            result=baseline(x,c['sample_rate']) if engine=='baseline' else classify(x,c['sample_rate'],metadata={'model_path':str(model_path)} if model_path else None,backend=engine)
            rows.append({'case_id':c['id'],'family':c['family'],'target':c['target'],'snr_db':c['snr_db'],
                'profile':c['profile'],'seed':c['seed'],'engine':engine,'predicted':result['modulation'],
                'signal_detected':bool(result.get('signal_detected',False)),'runtime_ms':(time.perf_counter()-begin)*1000,
                'configuration':c,'evidence':result.get('evidence',[]),'confidence_kind':result.get('confidence_kind')})
        if (index+1)%60==0:print(f'{display_split}: completed {index+1} cases',flush=True)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    summaries={e:summarize(rows,e) for e in engines}
    report={'baseline_commit':'f8502e3','split':'original' if original else split+('-stress' if stress else ''),'generator':'original-preserved' if original else 'rfatlas.corpus independent pulse-shaped v1',
        'engines':summaries,'rows':rows,'wall_seconds':time.perf_counter()-started,
        'source_sha256_at_start':source_hashes,
        'model_sha256':hashlib.sha256(Path(model_path).read_bytes()).hexdigest() if model_path else None,
        'python_tracemalloc_peak_bytes':peak,'memory_caveat':'Python-traced allocations, not total RSS/native BLAS or process peak; cases streamed',
        'interval_caveat':'Wilson binomial intervals are descriptive; related impairment cases are not statistically independent',
        'holdout_policy':'Holdout is explicit CLI only; never used for training or threshold selection'}
    output=Path(output);output.mkdir(parents=True,exist_ok=True);atomic_json(output/'results.json',report)
    for engine,s in summaries.items():
        with (output/f'confusion-{engine}.tsv').open('w',encoding='utf-8') as f:
            f.write('truth\\prediction\t'+'\t'.join(LABELS)+'\n')
            for a in LABELS:f.write(a+'\t'+'\t'.join(str(s['confusion_matrix'][a][b]) for b in LABELS)+'\n')
    print(json.dumps({e:{k:v for k,v in s.items() if k not in ('confusion_matrix','per_class')} for e,s in summaries.items()},indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--split',choices=('development','validation','holdout'),default='development')
    p.add_argument('--output',type=Path,default=Path('reports/engine-development'));p.add_argument('--model',type=Path)
    p.add_argument('--train-model',type=Path);p.add_argument('--seeds-per-cell',type=int,default=2);p.add_argument('--original',action='store_true')
    p.add_argument('--backends',nargs='+',choices=('baseline','dsp','ml','hybrid'))
    p.add_argument('--stress',action='store_true',help='Thirty supplemental long/5%%/85%% duty cases')
    a=p.parse_args()
    if a.train_model:
        from rfatlas.ml import train
        training=train(cases('development',a.seeds_per_cell),a.train_model)
        a.output.mkdir(parents=True,exist_ok=True);atomic_json(a.output/'training.json',training);print(json.dumps(training,indent=2));a.model=a.train_model
    evaluate(a.split,a.output,a.model,a.seeds_per_cell,a.original,a.backends,a.stress)
