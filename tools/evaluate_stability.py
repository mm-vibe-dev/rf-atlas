"""Reproduce frozen-reference versus fresh-seed DSP stability evaluation.

The reference must be a trusted local RF Atlas commit: its original Python
source is executed. No third-party code or RF recording is downloaded.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rfatlas import corpus
from rfatlas.engine import classify
from rfatlas.diagnostics import atomic_json
from evaluate_engine import summarize


def evaluate(output,seed_offset=7770000,reference_commit='fcf6e06'):
    if type(seed_offset) is not int or not 1<=seed_offset<=1000000000:
        raise ValueError('Seed offset must be a positive bounded integer')
    if not isinstance(reference_commit,str) or not re.fullmatch('[a-fA-F0-9]{7,40}',reference_commit):
        raise ValueError('Reference must name a trusted local commit hash')
    hashes={};rows=[]
    seed_base=corpus.SPLIT_SEEDS['holdout']
    try:
        corpus.SPLIT_SEEDS['holdout']=seed_base+seed_offset
        configs=corpus.cases('holdout',2)
    finally:
        corpus.SPLIT_SEEDS['holdout']=seed_base
    with tempfile.TemporaryDirectory(prefix='rfatlas-frozen-reference-') as temporary:
        package=Path(temporary)/'rfatlas_frozen_reference'
        package.mkdir();(package/'__init__.py').write_text('',encoding='utf-8')
        for name in ('engine.py','recovery.py','features.py'):
            source=subprocess.check_output(['git','show',f'{reference_commit}:rfatlas/{name}'],cwd=ROOT)
            (package/name).write_bytes(source)
            hashes[name]={'baseline':hashlib.sha256(source).hexdigest(),
                          'current':hashlib.sha256((ROOT/'rfatlas'/name).read_bytes()).hexdigest()}
        spec=importlib.util.spec_from_file_location('rfatlas_frozen_reference',package/'__init__.py',submodule_search_locations=[str(package)])
        module=importlib.util.module_from_spec(spec)
        sys.modules[spec.name]=module;spec.loader.exec_module(module)
        from rfatlas_frozen_reference.engine import classify as old_classify
        try:
            for index,c in enumerate(configs):
                x=corpus.generate_case(c)
                for name,function in [('baseline_0.4',old_classify),('dsp',classify)]:
                    started=time.perf_counter();result=function(x,c['sample_rate'])
                    rows.append(dict(case_id=c['id'],family=c['family'],target=c['target'],
                                     snr_db=c['snr_db'],profile=c['profile'],seed=c['seed'],engine=name,
                                     predicted=result['modulation'],signal_detected=bool(result['signal_detected']),
                                     runtime_ms=(time.perf_counter()-started)*1000,configuration=c))
                if (index+1)%60==0:print(f'fresh: completed {index+1} cases',flush=True)
        finally:
            for name in list(sys.modules):
                if name=='rfatlas_frozen_reference' or name.startswith('rfatlas_frozen_reference.'):
                    del sys.modules[name]
    summary={name:summarize(rows,name) for name in ['baseline_0.4','dsp']}
    report=dict(reference_commit=reference_commit,seed_offset=seed_offset,
                evaluation_policy='Frozen repair; unseen regenerated acquisition parameters and IQ; no further tuning',
                source_hashes=hashes,engines=summary,rows=rows)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    atomic_json(output/'fresh-results.json',report)
    print(json.dumps({name:{k:v for k,v in s.items() if k not in ['confusion_matrix','per_class']} for name,s in summary.items()},indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('reports/0.5-stability'))
    parser.add_argument('--seed-offset',type=int,default=7770000)
    parser.add_argument('--reference-commit',default='fcf6e06')
    args=parser.parse_args()
    evaluate(args.output,args.seed_offset,args.reference_commit)
