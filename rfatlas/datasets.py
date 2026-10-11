"""Private recording manifests and split audits, never inferred ground truth.

Caller supplies independent labels. Catalog predictions and frequency context
are not labels. Manifests contain acquisition metadata/hashes, no decoded data.
"""
from pathlib import Path
import hashlib
import math

PARTITIONS=('TRAINING','VALIDATION','TEST','REAL_WORLD_HOLDOUT','UNASSIGNED')
LABEL_BASES=('CONSTRUCTION','EXTERNAL_VERIFICATION','OPERATOR_INDEPENDENT')


def recording_entry(path,metadata,*,group,partition='UNASSIGNED',label=None):
    path=Path(path)
    if not isinstance(group,str) or not 1<=len(group)<=256:raise ValueError('A capture-session/source group is required')
    if partition not in PARTITIONS:raise ValueError('Invalid dataset partition')
    rate=metadata.get('sample_rate');count=metadata.get('sample_count')
    if type(rate) not in (int,float) or not math.isfinite(rate) or rate<1000 or type(count) is not int or count<4096:
        raise ValueError('Invalid recording sample metadata')
    if metadata.get('format')!='cu8' or path.stat().st_size!=count*2:raise ValueError('Invalid CU8 recording size/format')
    with path.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    if sha!=metadata.get('sha256'):raise ValueError('Recording checksum mismatch')
    label=dict(label or {})
    if label and (label.get('basis') not in LABEL_BASES or not isinstance(label.get('source'),str) or not label['source'].strip()):
        raise ValueError('Ground truth requires an independent basis and explicit source')
    if label and label.get('confidence') not in ('VERIFIED','STRONG','WEAK'):raise ValueError('Invalid label confidence')
    # Never copy arbitrary decoder payloads, station identities or annotations.
    return dict(recording_id=metadata.get('capture_id',sha),source=str(path),sha256=sha,
        group=group,partition=partition,format='cu8',samples=count,sample_rate_hz=rate,duration_s=count/rate,
        frequency_hz=metadata.get('desired_frequency_hz',metadata.get('center_hz')),
        receiver_configuration={k:metadata[k] for k in ('center_hz','gain','gain_db','device_identity','bias_tee','sample_rate_reported_hz','tuning_offset_hz','antenna') if k in metadata},
        ground_truth={k:label[k] for k in ('family','modulation','protocol','basis','source','confidence') if k in label},
        label_status='INDEPENDENT_LABEL' if label else 'UNLABELED',
        quality=metadata.get('analysis_quality',{}))


def audit_partitions(entries):
    """Any duplicate/source/session group spanning named splits is leakage.

    Group captures conservatively before using this audit; a different file
    hash alone does not establish an independent emitter/session.
    """
    if not isinstance(entries,list) or len(entries)>100000:raise ValueError('Manifest entry budget exceeded')
    groups={};hashes={};duplicates=[];identities=set();seen_hashes=set()
    for e in entries:
        if e.get('partition') not in PARTITIONS or not e.get('group') or not e.get('sha256'):raise ValueError('Incomplete partition provenance')
        identity=e.get('recording_id')
        if identity in identities:raise ValueError('Duplicate recording ID')
        identities.add(identity)
        split=e['partition']
        if split!='UNASSIGNED':
            groups.setdefault(e['group'],set()).add(split)
            hashes.setdefault(e['sha256'],set()).add(split)
        if e['sha256'] in seen_hashes:duplicates.append(identity)
        seen_hashes.add(e['sha256'])
    leaks=[dict(kind=kind,key=k,partitions=sorted(v)) for kind,values in (('SOURCE_GROUP',groups),('DUPLICATE_CONTENT',hashes)) for k,v in values.items() if len(v)>1]
    return dict(valid=not leaks,leakage=leaks,duplicate_recordings=duplicates,count=len(entries),
        independently_labeled=sum(e.get('label_status')=='INDEPENDENT_LABEL' for e in entries),
        limitation='Split audit validates declared groups, not independence of undocumented capture sessions or emitters.')


def support_reliability(rows,bins=10):
    """Report score-vs-correctness; heuristic support is not a posterior.

    ECE/Brier here diagnose mismatch; they do not calibrate or certify scores.
    """
    if type(bins) is not int or not 2<=bins<=50:raise ValueError('Invalid score bin count')
    accepted=[r for r in rows if r['predicted']!='UNKNOWN']
    if not accepted:return dict(count=0,bins=[],ece=None,brier=None,calibrated=False)
    buckets=[[] for _ in range(bins)]
    for r in accepted:
        score=r['score']
        if type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:raise ValueError('Invalid support score')
        buckets[min(bins-1,int(score*bins))].append((score,int(r['predicted']==r['truth'])))
    summary=[];ece=0.;brier=0.
    for i,values in enumerate(buckets):
        if not values:continue
        score=sum(v[0] for v in values)/len(values);accuracy=sum(v[1] for v in values)/len(values)
        summary.append(dict(lower=i/bins,upper=(i+1)/bins,count=len(values),mean_support=score,accuracy=accuracy))
        ece+=len(values)*abs(score-accuracy);brier+=sum((a-b)**2 for a,b in values)
    return dict(count=len(accepted),bins=summary,ece=ece/len(accepted),brier=brier/len(accepted),calibrated=False,
        limitation='Diagnostic reliability of uncalibrated heuristic support on this dataset only; no probability guarantee.')
