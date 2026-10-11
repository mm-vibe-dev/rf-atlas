"""Bounded cross-channel feature comparison, with no identity or protocol inference."""
import json
import math

VERSION='similarity-1.6'


def vector(analysis):
    f=analysis.get('features',{});p=analysis.get('fingerprint',{})
    if not analysis.get('signal_detected') or analysis.get('adc_amplitude_p95_counts',4)<4:return None
    values=[f.get('occupied_bandwidth_hz'),f.get('envelope_cv'),f.get('spectral_flatness'),f.get('instantaneous_frequency_span_hz')]
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in values):return None
    bw,cv,flat,span=values
    # Explicit physical scales, no learned fit and no center-frequency input.
    return [math.log1p(bw)/3,min(cv,2),flat*2,math.log1p(span)/3]


def score(a,b):
    if a is None or b is None:return None
    return math.exp(-sum(abs(x-y) for x,y in zip(a,b))/len(a))


def search(db,signal_id,limit=10):
    if type(limit) is not int or not 1<=limit<=30:raise ValueError('Invalid similarity limit')
    row=db.execute('SELECT result FROM identification_runs WHERE signal_id=? ORDER BY created DESC LIMIT 1',(signal_id,)).fetchone()
    query=vector(json.loads(row[0]).get('analysis',{})) if row else None
    if query is None:return dict(version=VERSION,status='INSUFFICIENT_IQ_FEATURES',matches=[],physical_identity='UNPROVEN')
    rows=db.execute('''SELECT i.signal_id,i.result FROM identification_runs i WHERE i.rowid IN
        (SELECT max(rowid) FROM identification_runs GROUP BY signal_id) AND i.signal_id!=? ORDER BY i.created DESC LIMIT 1000''',(signal_id,)).fetchall()
    matches=[]
    for sid,raw in rows:
        r=json.loads(raw);similarity=score(query,vector(r.get('analysis',{})))
        if similarity is not None:matches.append(dict(signal_id=sid,score=similarity,protocol_evidence=r.get('protocol','UNKNOWN'),modulation_hypothesis=r.get('modulation','UNKNOWN')))
    matches.sort(key=lambda m:(-m['score'],m['signal_id']))
    return dict(version=VERSION,status='EXPERIMENTAL_FEATURE_COMPARISON' if matches else 'NO_COMPARABLE_MATCH',matches=matches[:limit],candidate_limit=1000,
        validation='Poor family discrimination on independent synthetic holdout; even high scores frequently match different families. Research diagnostic only.',
        score_kind='UNCALIBRATED_FEATURE_SIMILARITY',physical_identity='UNPROVEN',limitations='Similar features never verify protocol, common emitter, geographic position or identity. Noise, settings and interference can produce false matches.')
