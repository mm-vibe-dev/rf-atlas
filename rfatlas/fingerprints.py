"""Original bounded signal characteristics; similarity is never identity."""
import json
import math
import numpy as np
from scipy import signal

VERSION='1.2.0'


def spectral_statistics(power):
    p=np.asarray(power,float)
    if p.ndim!=1 or not 3<=len(p)<=262144 or not np.isfinite(p).all() or np.any(p<0):
        raise ValueError('Bounded finite spectrum required')
    if not np.any(p):return dict(spectral_entropy=None,spectral_flatness=None,spectral_concentration=None,
        noise_floor_variation_db=None,spectral_status='NO_MEASURABLE_POWER')
    p=np.maximum(p,1e-30)
    normalized=p/p.sum()
    return dict(spectral_entropy=float(-np.sum(normalized*np.log(normalized))/np.log(len(p))),
                spectral_flatness=float(np.exp(np.mean(np.log(p)))/np.mean(p)),
                spectral_concentration=float(np.sort(normalized)[-max(1,len(p)//100):].sum()),
                noise_floor_variation_db=float(np.quantile(10*np.log10(p),.4)-np.quantile(10*np.log10(p),.1)))


def spectral_fingerprint(row,peak):
    centers=np.asarray(row['centers']);power=np.asarray(row['power'])
    selected=(centers>=peak['low']-row['step']*3)&(centers<=peak['high']+row['step']*3)
    values=power[selected]
    if len(values)<4:return None
    shape=np.interp(np.linspace(0,len(values)-1,32),np.arange(len(values)),values)
    shape=(shape-np.median(shape))/max(float(np.ptp(shape)),6)
    return dict(version=VERSION,source='SPECTRAL_OBSERVATION',resolution_hz=row['step'],
                center_hz=peak['center'],bandwidth_hz=peak['high']-peak['low'],snr_db=peak['peak'],
                shape=shape.tolist(),**spectral_statistics(10**((values-values.max())/10)),
                limitations='Integrated FFT shape; burst duration, CFO and transmitter identity unavailable.')


def iq_fingerprint(iq,rate):
    x=np.asarray(iq,complex)
    if x.ndim!=1 or len(x)<4096 or len(x)>4_000_000 or not np.isfinite(x).all() or not math.isfinite(rate) or rate<=0:
        raise ValueError('Invalid bounded IQ fingerprint input')
    # Full-capture activity remains in the existing activity analysis. A prefix
    # limits this additional characterization cost independently of recording size.
    x=x[:262144];env=abs(x)
    f,p=signal.welch(x,fs=rate,nperseg=2048,return_onesided=False,detrend=False)
    inst=np.angle(x[1:]*x[:-1].conj())*rate/(2*np.pi)
    active=(env[1:]>np.quantile(env,.95)*.2)&(env[:-1]>np.quantile(env,.95)*.2)
    inst=inst[active]
    return dict(version=VERSION,source='IQ_PREFIX',samples=len(x),sample_rate=rate,
                amplitude_cv=float(env.std()/max(env.mean(),1e-15)),
                carrier_offset_hz=float(np.median(inst)) if len(inst) else None,
                instantaneous_frequency_iqr_hz=float(np.ptp(np.quantile(inst,[.25,.75]))) if len(inst) else None,
                **spectral_statistics(p),limitations='CFO is relative to the desired tuning frequency; not calibrated oscillator error. Entropy never establishes encryption.')


def similarity(a,b):
    """Resolution and shape gates; score has no probabilistic interpretation."""
    if a.get('source')!='SPECTRAL_OBSERVATION' or b.get('source')!=a.get('source'):return None
    if a.get('version')!=b.get('version'):return None
    if a.get('gain_db')!=b.get('gain_db'):return None
    for key in ('resolution_hz','bandwidth_hz'):
        x,y=a.get(key,0),b.get(key,0)
        if x<=0 or y<=0 or max(x,y)/min(x,y)>(1.05 if key=='resolution_hz' else 1.3):return None
    if abs(a['center_hz']-b['center_hz'])>max(a['resolution_hz'],a['bandwidth_hz']*.15):return None
    if abs(a['snr_db']-b['snr_db'])>6:return None
    x=np.asarray(a['shape']);y=np.asarray(b['shape'])
    if x.shape!=(32,) or y.shape!=(32,) or not np.isfinite(x).all() or not np.isfinite(y).all():return None
    error=float(np.sqrt(np.mean((x-y)**2)))
    return max(0,1-error) if error<=.12 else None


def history(db,signal_id,limit=80):
    rows=db.execute('''SELECT f.observation_id,f.features,o.run_id,o.observed,o.observed_utc,o.timestamp_basis
        FROM observation_fingerprints f JOIN spectral_observations o USING(observation_id)
        WHERE f.signal_id=? ORDER BY f.observation_id DESC LIMIT ?''',(signal_id,limit)).fetchall()
    if not rows:return dict(status='UNAVAILABLE',similar_observations=[],physical_identity='UNPROVEN')
    latest=json.loads(rows[0][1]);matches=[];centers=[latest['center_hz']]
    for row in rows[1:]:
        score=similarity(latest,json.loads(row[1]))
        if score is not None:
            centers.append(json.loads(row[1])['center_hz'])
            matches.append(dict(observation_id=row[0],run_id=row[2],observed=row[4] or row[3],timestamp_basis=row[5],score=score))
    visits={r['run_id'] for r in matches if r['run_id'] and r['run_id']!=rows[0][2]}
    features=[json.loads(r[1]) for r in rows]
    snrs=[r['snr_db'] for r in features]
    bandwidths=[r['bandwidth_hz'] for r in features]
    decisions=[dict(reason=r[0],created=r[1],profile_id=r[2]) for r in db.execute(
        'SELECT reason,created,profile_id FROM investigation_decisions WHERE signal_id=? ORDER BY decision_id DESC LIMIT 20',(signal_id,))] if db.execute("SELECT 1 FROM sqlite_master WHERE name='investigation_decisions'").fetchone() else []
    return dict(status='LIKELY_RECURRING_PATTERN' if len(visits)>=2 else 'SIMILAR_CHARACTERISTICS' if matches else 'NO_COMPARABLE_MATCH',
                observation_summary=dict(samples=len(rows),distinct_visits=len({r[2] for r in rows}),
                    snr_min_db=min(snrs),snr_max_db=max(snrs),snr_latest_minus_oldest_db=snrs[0]-snrs[-1],
                    bandwidth_min_hz=min(bandwidths),bandwidth_max_hz=max(bandwidths),
                    basis='Mixed historical settings can affect these descriptive ranges; no continuous occupancy estimate.'),
                investigation_decisions=decisions,
                latest=latest,similar_observations=matches,independent_matching_visits=len(visits),physical_identity='UNPROVEN',
                center_frequency_spread_hz=float(np.ptp(centers)),center_frequency_std_hz=float(np.std(centers)),
                basis='Same catalog channel, compatible resolution and shape. Similar emitters may collide; verified identifiers are separate evidence.')
