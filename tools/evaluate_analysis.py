"""Reproducible synthetic benchmark against the committed original classifier."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import subprocess
import numpy as np
from rfatlas.synthetic import generate,FAMILIES
from rfatlas.features import classify
from rfatlas.activity import analyze_activity
from rfatlas.diagnostics import atomic_json


def evaluate(output):
    baseline_source=subprocess.check_output(['git','show','bd31fc8:rfatlas/features.py'],text=True)
    scope={};exec(compile(baseline_source,'committed-baseline','exec'),scope)
    cases=[]
    variants={'base':{},'offset_minus':{'offset':-20000},'offset_plus':{'offset':20000},'low_gain':{'gain':.05},'clipped':{'gain':2.,'clipping':True},'rate_low':{'rate':125000},'rate_high':{'rate':500000},'short':{'duration':.025},'late_burst':{'duration':.3,'burst':True}}
    for family in FAMILIES:
        for snr in [None,5,15,30]:
            for variant,changes in variants.items():
                for seed in [17,29]:
                    params={'family':family,'snr_db':snr,'seed':seed,**changes};x=generate(**params);rate=changes.get('rate',250000)
                    activity=analyze_activity(x,rate);lo,hi=activity['selected_start_sample'],activity['selected_stop_sample']
                    result=classify(x[lo:hi],rate);old=scope['classify'](x,rate)
                    target='UNKNOWN' if family=='NOISE' else family
                    cases.append({'family':family,'target':target,'snr_db':snr,'variant':variant,'seed':seed,'parameters':params,'predicted':result['modulation'],'baseline_predicted':old['modulation'],'correct':result['modulation']==target,'baseline_correct':old['modulation']==target,'signal_detected':result['signal_detected'],'confidence_kind':result['confidence_kind'],'evidence':result['evidence'],'features':result['features'],'activity':{k:v for k,v in activity.items() if k not in ['block_times_s','block_excess_db']}})
    labels=FAMILIES[:-1]+['UNKNOWN']
    matrix={a:{b:0 for b in labels} for a in labels}
    for c in cases:matrix[c['target']][c['predicted']]+=1
    summaries={}
    for family in FAMILIES:
        subset=[c for c in cases if c['family']==family]
        summaries[family]={'cases':len(subset),'accuracy':float(np.mean([c['correct'] for c in subset])),'baseline_accuracy':float(np.mean([c['baseline_correct'] for c in subset])),'unknown_rate':float(np.mean([c['predicted']=='UNKNOWN' for c in subset]))}
    by_snr={str(snr):{'accuracy':float(np.mean([c['correct'] for c in cases if c['snr_db']==snr])),'baseline_accuracy':float(np.mean([c['baseline_correct'] for c in cases if c['snr_db']==snr]))} for snr in [None,5,15,30]}
    noise=[c for c in cases if c['family']=='NOISE']
    report={'baseline_commit':'bd31fc8','cases':cases,'summaries':summaries,'by_snr':by_snr,'labels':labels,'confusion_matrix':matrix,'accuracy':float(np.mean([c['correct'] for c in cases])),'baseline_accuracy':float(np.mean([c['baseline_correct'] for c in cases])),'noise_false_label_rate':float(np.mean([c['predicted']!='UNKNOWN' for c in noise])),'noise_false_detection_rate':float(np.mean([c['signal_detected'] for c in noise])),'design':'720 one-factor-at-a-time cases, two fixed seeds; no train/test optimization or exhaustive factorial coverage; None SNR means noiseless'}
    atomic_json(output/'classification-results.json',report)
    report['by_variant']={v:{'accuracy':float(np.mean([c['correct'] for c in cases if c['variant']==v])),'baseline_accuracy':float(np.mean([c['baseline_correct'] for c in cases if c['variant']==v])),'unknown_rate':float(np.mean([c['predicted']=='UNKNOWN' for c in cases if c['variant']==v]))} for v in variants}
    report['unknown_rate']=float(np.mean([c['predicted']=='UNKNOWN' for c in cases]))
    signals=[c for c in cases if c['family']!='NOISE']
    report['wrong_nonunknown_label_rate']=float(np.mean([not c['correct'] and c['predicted']!='UNKNOWN' for c in signals]))
    atomic_json(output/'classification-results.json',report)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    data=np.array([[matrix[a][b] for b in labels] for a in labels])
    fig,ax=plt.subplots(figsize=(10,8));im=ax.imshow(data,cmap='Blues')
    ax.set_xticks(range(len(labels)),labels,rotation=45);ax.set_yticks(range(len(labels)),labels);ax.set(xlabel='Predicted hypothesis',ylabel='Synthetic ground truth',title='Phase 1.3 synthetic confusion matrix (counts)')
    for i in range(len(labels)):
        for j in range(len(labels)):ax.text(j,i,str(data[i,j]),ha='center',va='center',color='white' if data[i,j]>data.max()/2 else 'black')
    fig.colorbar(im,ax=ax);fig.tight_layout();fig.savefig(output/'confusion-matrix.png',dpi=150);plt.close(fig)
    print(json.dumps({k:report[k] for k in ['accuracy','baseline_accuracy','noise_false_label_rate','noise_false_detection_rate','summaries']},indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('reports/synthetic-phase13'));args=parser.parse_args();evaluate(args.output)
