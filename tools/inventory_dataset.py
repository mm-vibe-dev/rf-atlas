"""Create a new private, checksummed recording manifest from a read-only catalog.

Optional labels JSON maps capture IDs to independent label documents with
group/partition overrides. Prior classifier predictions are never imported.
Existing output files are never overwritten.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rfatlas.datasets import recording_entry,audit_partitions


def inventory(catalog,output,labels=None):
    path=Path(catalog).resolve();target=Path(output)
    if target.exists():raise FileExistsError('Dataset manifest already exists; select a new versioned output')
    label_map={}
    if labels:
        source=Path(labels)
        if source.stat().st_size>2_000_000:raise ValueError('Label document budget exceeded')
        label_map=json.loads(source.read_text(encoding='utf8'))
        if not isinstance(label_map,dict) or any(not isinstance(k,str) or not isinstance(v,dict) for k,v in label_map.items()):raise ValueError('Labels must map recording IDs to independent documents')
    entries=[];rejected=[]
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        count=db.execute('SELECT count(*) FROM captures').fetchone()[0]
        if count>10000:raise ValueError('Recording inventory budget exceeded')
        for identity,raw,settings in db.execute('SELECT capture_id,path,settings FROM captures ORDER BY capture_id'):
            try:
                p=Path(raw);meta=json.loads(settings);label=label_map.get(identity,{})
                # A shared directory is a deliberately conservative session
                # group when acquisition session metadata is unavailable.
                group=label.get('group') or 'directory-'+hashlib.sha256(str(p.resolve().parent).encode()).hexdigest()
                entry=recording_entry(p,meta,group=group,partition=label.get('partition','UNASSIGNED'),label={k:v for k,v in label.items() if k not in ('group','partition')})
                entry['recording_id']=identity;entry['group_basis']='EXPLICIT_LABEL_DOCUMENT' if label.get('group') else 'CONSERVATIVE_SHARED_DIRECTORY'
                entries.append(entry)
            except (ValueError,OSError,TypeError) as e:rejected.append(dict(recording_id=identity,error=type(e).__name__,reason=str(e)[:200]))
    report=dict(version=1,source='READ_ONLY_CATALOG_INVENTORY',entries=entries,rejected=rejected,audit=audit_partitions(entries),limitations=['Unlabeled by default; catalog predictions and frequency context are not independent truth.','Declared session grouping must be reviewed before supervised training.','Rejections are preserved; no source recordings are changed.'])
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf8') as f:json.dump(report,f,indent=2)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('catalog');p.add_argument('output');p.add_argument('--labels');a=p.parse_args();r=inventory(a.catalog,a.output,a.labels);print(json.dumps(dict(recordings=len(r['entries']),rejected=len(r['rejected']),audit=r['audit'])))
