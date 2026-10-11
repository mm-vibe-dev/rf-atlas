"""Transparent retained-IQ investigation ranking; no suspicion or identity score."""
import math

DEFAULT_WEIGHTS=dict(recording=4.,recurrence=2.,snr=1.,unresolved=3.,digital=2.,noise_penalty=4.)


def weights(options=None):
    if options is None:return dict(DEFAULT_WEIGHTS)
    if not isinstance(options,dict) or set(options)-set(DEFAULT_WEIGHTS):raise ValueError('Unknown priority weight')
    result=dict(DEFAULT_WEIGHTS,**options)
    if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=20 for v in result.values()):raise ValueError('Priority weights must be finite from 0 to 20')
    return result


def score(row,selected=None):
    w=weights(selected)
    def bounded(value,low,high):
        try:v=float(value)
        except (TypeError,ValueError):v=low
        return min(high,max(low,v)) if math.isfinite(v) else low
    count=bounded(row.get('observation_count'),0,1000000)
    snr=bounded(row.get('peak_snr_db'),0,40)/40
    components=dict(recording=w['recording']*bool(row.get('recording_count')),
        recurrence=w['recurrence']*min(math.log1p(count)/math.log(101),1),snr=w['snr']*snr,
        unresolved=w['unresolved']*(row.get('modulation') in (None,'UNKNOWN')),
        digital=w['digital']*(row.get('signal_class')=='DIGITAL'),
        noise_penalty=-w['noise_penalty']*(row.get('signal_class')=='NOISE_CANDIDATE'))
    return dict(score=sum(components.values()),components=components,
        limitation='Investigation utility only; UNKNOWN is not suspicious. Recurrence correlates catalog observations, not transmitter identity.')


def queue(db,*,limit=100,selected=None):
    if type(limit) is not int or not 1<=limit<=500:raise ValueError('Priority queue limit must be 1..500')
    selected=weights(selected)
    # Bounded catalog query; no nested per-signal observation scans.
    rows=db.execute("""SELECT s.signal_id,s.center_frequency_hz,s.observation_count,s.peak_snr_db,
        s.signal_class,s.modulation,s.last_seen,count(c.capture_id) recording_count
        FROM signals s LEFT JOIN captures c USING(signal_id)
        WHERE coalesce(s.protocol,'UNKNOWN')='UNKNOWN'
        GROUP BY s.signal_id ORDER BY s.last_seen DESC,s.signal_id LIMIT 10000""").fetchall()
    names=('signal_id','frequency_hz','observation_count','peak_snr_db','signal_class','modulation','last_seen','recording_count')
    values=[dict(zip(names,r)) for r in rows]
    for row in values:row.update(score(row,selected))
    values.sort(key=lambda r:(-r['score'],r['signal_id']))
    return dict(items=values[:limit],weights=selected,catalog_candidates=len(values),catalog_cap=10000,
        limitation='Read-only investigation ordering; no automatic capture. At most 10000 most recent UNKNOWN-protocol catalog rows ranked.')


def observation_history(rows):
    """Summarize measured centers and times without inventing occupancy."""
    valid=[r for r in rows if type(r.get('center_hz')) in (int,float) and math.isfinite(r['center_hz'])]
    centers=[r['center_hz'] for r in valid]
    # Timezone-unknown legacy values are deliberately excluded from hour bins.
    from .timestamps import aware_utc
    from datetime import datetime
    hours={str(h):0 for h in range(24)};timed=0
    stamps=[]
    for r in valid:
        stamp=aware_utc(r.get('observed_utc') or r.get('observed'))
        if stamp:
            stamps.append(stamp)
            hours[str(datetime.fromisoformat(stamp.replace('Z','+00:00')).hour)]+=1;timed+=1
    widths=[r['stop_hz']-r['start_hz'] for r in valid if type(r.get('start_hz')) in (int,float) and type(r.get('stop_hz')) in (int,float) and r['stop_hz']>=r['start_hz']]
    return dict(observations=len(rows),valid_centers=len(centers),utc_timed_observations=timed,hour_counts_utc=hours,
        sampled_first_observation=min(stamps) if stamps else None,sampled_last_observation=max(stamps) if stamps else None,
        sampled_bandwidth_min_hz=min(widths) if widths else None,sampled_bandwidth_max_hz=max(widths) if widths else None,
        monitoring_status='RECORDED_OBSERVATIONS_ONLY; gaps have unknown receiver coverage',
        center_span_hz=max(centers)-min(centers) if centers else None,
        limitation='Finite sampled observations; center span includes tuning/bin uncertainty. Hour counts are not occupancy, drift velocity or transmitter identity.')
