"""Persistent bounded spectrum visits and metadata-only investigation scheduling."""
import hashlib
import json
import math
import statistics
from pathlib import Path
import time
import tomllib
import uuid

from .hardware import gain_value

RECEIVER_MIN_HZ = 500000
RECEIVER_MAX_HZ = 1766000000


def default_profiles():
    # Organizational US examples, not allocation/occupancy/protocol assertions.
    bands = [('hf',500000,30000000),('vhf',30000000,300000000),
             ('uhf',300000000,1000000000),('upper',1000000000,1766000000),
             ('amateur-us',144000000,148000000),('aviation',118000000,137000000),
             ('marine',156000000,163000000),('land-mobile',450000000,470000000),
             ('ism-us',902000000,928000000),('ads-b',1089000000,1091000000),
             ('satellite',137000000,138000000),('cellular-energy',824000000,894000000),
             ('am-broadcast',520000,1710000),('fm-broadcast',87900000,108000000),
             ('weather-us',162390000,162560000),('ism-433',433050000,434790000),
             ('ais',161950000,162050000),('amateur-uhf',420000000,450000000),
             ('sensors-315',314500000,315500000),('sensors-915',902000000,928000000),
             ('amateur-hf-80m',3500000,4000000),('amateur-hf-40m',7000000,7300000),
             ('amateur-hf-20m',14000000,14350000),('amateur-hf-10m',28000000,29700000),
             ('amateur-6m',50000000,54000000),('amateur-125cm',222000000,225000000),
             ('radiosonde-energy',400000000,406000000),('uat-energy',977500000,978500000)]
    narrow={'amateur-us','aviation','marine','land-mobile','satellite','weather-us','ism-433','ais','amateur-uhf','am-broadcast'}
    return [dict(id=name,start_hz=low,stop_hz=high,bin_hz=5000 if name in narrow else 25000,fine_bin_hz=1000,
                 integration_s=1,duration_s=3,gain='15.7',margin_db=6,revisit_s=3600,
                 capture_allowed=name not in {'satellite','cellular-energy','radiosonde-energy','uat-energy'},enabled=i<4)
            for i,(name,low,high) in enumerate(bands)]


def validate_profile(value):
    required=set(default_profiles()[0])
    if not isinstance(value,dict) or set(value)!=required:
        raise ValueError('Profile fields must match the documented profile schema')
    result=dict(value)
    if not isinstance(result['id'],str) or not result['id'] or len(result['id'])>64 or not all(c.isalnum() or c in '-_' for c in result['id']):
        raise ValueError('Invalid profile identifier')
    for key in ('start_hz','stop_hz','bin_hz','fine_bin_hz','integration_s','duration_s','revisit_s'):
        if type(result[key]) is not int: raise ValueError('Profile numeric settings require integers')
    if not RECEIVER_MIN_HZ<=result['start_hz']<result['stop_hz']<=RECEIVER_MAX_HZ:
        raise ValueError('Profile outside documented V4 tuning envelope')
    if not 100<=result['fine_bin_hz']<=result['bin_hz']<=1000000:
        raise ValueError('Invalid resolution range')
    if not 1<=result['integration_s']<=result['duration_s']<=30 or not 60<=result['revisit_s']<=30*86400:
        raise ValueError('Invalid profile timing')
    if result['margin_db'] not in (3,6,9,12) or type(result['margin_db']) is bool:
        raise ValueError('Invalid threshold margin')
    result['gain']=gain_value(str(result['gain']))
    if result['gain']=='auto' or float(result['gain'])==0: raise ValueError('Inventory requires manual gain')
    if type(result['capture_allowed']) is not bool or type(result['enabled']) is not bool:
        raise ValueError('Profile flags must be booleans')
    return result


def write_profiles(path):
    path=Path(path)
    if path.exists(): raise ValueError('Profile file already exists; edit it in place')
    path.parent.mkdir(parents=True,exist_ok=True)
    text=['# RF Atlas organizational profiles; no band-use or protocol guarantees.']
    for profile in default_profiles():
        text.append('\n[[regions]]')
        for key,value in profile.items():
            text.append(key+' = '+(str(value).lower() if type(value) is bool else json.dumps(value)))
    path.write_text('\n'.join(text)+'\n',encoding='utf8')


def load_profiles(path=None):
    if path is None: return [validate_profile(p) for p in default_profiles()]
    with Path(path).open('rb') as file: data=tomllib.load(file)
    if set(data)!={'regions'} or not isinstance(data['regions'],list) or not 1<=len(data['regions'])<=64:
        raise ValueError('Require 1..64 region profiles')
    profiles=[validate_profile(p) for p in data['regions']]
    if len({p['id'] for p in profiles})!=len(profiles): raise ValueError('Duplicate profile identifier')
    # Extend the original complete preset catalog in memory, preserving every
    # operator value and the existing file. Custom profile catalogs stay exact.
    defaults=default_profiles()
    if {p['id'] for p in profiles}=={p['id'] for p in defaults[:18]}:
        profiles.extend(dict(p,enabled=False) for p in defaults[18:])
    return profiles


def configure_regions(db,profiles,selected=None):
    created=0
    if selected and set(selected)-{p['id'] for p in profiles}: raise ValueError('Unknown inventory profile')
    planned=sum(math.ceil((p['stop_hz']-p['start_hz'])/2000000) for p in profiles if (p['id'] in selected if selected is not None else p['enabled']))
    if planned>5000:raise ValueError('Inventory configuration exceeds 5000 scheduled region budget')
    db.execute('UPDATE inventory_regions SET enabled=0')
    for profile in profiles:
        if selected is not None and profile['id'] not in selected: continue
        if selected is None and not profile['enabled']: continue
        for low in range(profile['start_hz'],profile['stop_hz'],2000000):
            high=min(low+2000000,profile['stop_hz'])
            settings=json.dumps(profile,sort_keys=True)
            identity=hashlib.sha256((settings+str(low)+':'+str(high)).encode()).hexdigest()
            created+=db.execute('INSERT OR IGNORE INTO inventory_regions(region_id,profile_id,start_hz,stop_hz,settings) VALUES(?,?,?,?,?)',
                               (identity,profile['id'],low,high,settings)).rowcount
            db.execute('UPDATE inventory_regions SET enabled=1 WHERE region_id=?',(identity,))
    db.commit()
    return created


def _rows(db,query,args=()):
    cursor=db.execute(query,args)
    fields=[c[0] for c in cursor.description]
    return [dict(zip(fields,row)) for row in cursor]


def _union_width(intervals,sort=True):
    total=0;end=None
    for low,high in (sorted(intervals) if sort else intervals):
        total+=max(0,high-max(low,end if end is not None else low))
        end=max(end or high,high)
    return total


def inventory_view(db,profiles=None):
    regions=_rows(db,'SELECT * FROM inventory_regions ORDER BY enabled DESC,start_hz,profile_id LIMIT 2000')
    for region in regions:
        region['quality']=json.loads(region['quality'] or '{}')
        region.pop('settings',None)
    visited=_union_width(db.execute('SELECT start_hz,stop_hz FROM inventory_regions WHERE visit_count>0 ORDER BY start_hz,stop_hz'),sort=False)
    valid=_union_width(db.execute('SELECT start_hz,stop_hz FROM inventory_regions WHERE valid_count>0 ORDER BY start_hz,stop_hz'),sort=False)
    counts={name:db.execute(query).fetchone()[0] for name,query in {
        'detected_signals':'SELECT count(*) FROM signals',
        'investigated_signals':'SELECT count(DISTINCT signal_id) FROM identification_runs',
        'modulation_classified_signals':"SELECT count(DISTINCT signal_id) FROM identification_runs WHERE json_extract(result,'$.modulation')!='UNKNOWN'",
        'protocol_verified_signals':"SELECT count(*) FROM signals WHERE protocol!='UNKNOWN'"}.items()}
    total=db.execute('SELECT count(*) FROM inventory_regions').fetchone()[0]
    from .coverage import accounting
    return {'coverage_accounting':accounting(db),'profiles':profiles or default_profiles(),'regions':regions,'total_regions':total,'regions_truncated':total>len(regions),
            'coverage':dict(visited_hz=visited,valid_hz=valid,receiver_span_hz=RECEIVER_MAX_HZ-RECEIVER_MIN_HZ,
                            ever_valid_fraction=valid/(RECEIVER_MAX_HZ-RECEIVER_MIN_HZ),**counts),
            'limitations':['Visited does not imply a valid measurement. Valid does not imply unused spectrum.',
                           'Coverage is historical union of visits, not simultaneous or complete reception.',
                           'A frequency catalog entry is a channel observation, not a transmitter identity.',
                           'Hopping, antenna response, DC artifacts and signals below resolution can hide transmissions.']}


def queue_view(db):
    jobs=_rows(db,'SELECT job_id,signal_id,status,priority,reason,created,updated,next_eligible,attempts,result_key,error FROM investigation_jobs ORDER BY priority DESC,created LIMIT 500')
    counts={row[0]:row[1] for row in db.execute('SELECT status,count(*) FROM investigation_jobs GROUP BY status')}
    return {'jobs':jobs,'counts':counts}


def signal_fingerprint(signal):
    # Coarse changes trigger retries; minor FFT/bin jitter does not identify a new transmitter.
    from .deep_investigation import VERSION
    return hashlib.sha256(json.dumps([VERSION,round((signal.get('occupied_bandwidth_hz') or 0)/2000),
        round((signal.get('peak_snr_db') or 0)/6),signal.get('modulation'),signal.get('protocol')]).encode()).hexdigest()


def compare_spectra(previous,current):
    """Compare aligned saved power grids; never equate differing resolution/gain."""
    import numpy as np
    from .diagnostics import read_spectra
    paths=[Path(previous),Path(current)]
    if any(not p.is_file() or p.stat().st_size>4*1024*1024 for p in paths):
        return {'comparable':False,'reason':'Missing or oversized historical spectrum'}
    groups=[]
    for path in paths:
        try:rows,diagnostic=read_spectra(path)
        except (OSError,UnicodeError,ValueError):return {'comparable':False,'reason':'Historical spectrum cannot be read'}
        if diagnostic['rejected_rows'] or not rows or len(rows)>64:
            return {'comparable':False,'reason':'Invalid or over-budget historical spectrum'}
        keys={(row['low'],row['high'],row['step'],len(row['power'])) for row in rows}
        if len(keys)!=1 or sum(len(r['power']) for r in rows)>262144:
            return {'comparable':False,'reason':'Mixed or over-budget spectral grids'}
        groups.append((next(iter(keys)),np.median(np.stack([r['power'] for r in rows]),axis=0)))
    if groups[0][0]!=groups[1][0]:return {'comparable':False,'reason':'Spectral grids differ; no resampling implied'}
    delta=groups[1][1]-groups[0][1]
    return {'comparable':True,'bin_count':len(delta),'median_change_db':float(np.median(delta)),
            'p10_change_db':float(np.quantile(delta,.1)),'p90_change_db':float(np.quantile(delta,.9)),
            'maximum_increase_db':float(delta.max()),'meaning':'Difference in relative tool power on identical grids; gain/antenna comparability still required.'}


def enqueue_investigations(db,profile,now=None,limit=100,cooldown_s=3600,run_id=None):
    now=time.time() if now is None else now
    if type(limit) is not int or not 1<=limit<=500: raise ValueError('Queue limit must be 1..500')
    pending=db.execute("SELECT count(*) FROM investigation_jobs WHERE status IN ('pending','running','cancel_requested')").fetchone()[0]
    fresh=" AND EXISTS(SELECT 1 FROM spectral_observations o WHERE o.signal_id=signals.signal_id AND o.run_id=?)" if run_id is not None else ''
    params=(profile['start_hz'],profile['stop_hz'])+((run_id,) if run_id is not None else ())+(min(5000,limit*10),)
    signals=_rows(db,"SELECT * FROM signals WHERE center_frequency_hz BETWEEN ? AND ?"+fresh+" ORDER BY CASE WHEN protocol IS NULL OR protocol='UNKNOWN' THEN 0 ELSE 1 END,occupied_bandwidth_hz DESC,peak_snr_db DESC LIMIT ?",params)
    added=0
    def decision(signal,reason,job=None):
        db.execute('INSERT INTO investigation_decisions(run_id,signal_id,profile_id,created,reason,job_id) VALUES(?,?,?,?,?,?)',
                   (run_id,signal['signal_id'],profile['id'],now,reason,job))
    for signal in signals:
        identity=signal['signal_id'];fingerprint=signal_fingerprint(signal)
        if (signal.get('peak_snr_db') or 0)<10:
            decision(signal,'BELOW_QUALITY_THRESHOLD');continue
        if not profile['capture_allowed']:
            decision(signal,'PROFILE_CAPTURE_DISABLED');continue
        prior=db.execute('SELECT fingerprint,status,updated FROM investigation_jobs WHERE signal_id=? ORDER BY created DESC LIMIT 1',(identity,)).fetchone()
        if prior and prior[1] in ('pending','running','cancel_requested'):
            decision(signal,'ALREADY_QUEUED');continue
        if prior and now-prior[2]<cooldown_s:
            decision(signal,'COOLDOWN');continue
        changed=prior is not None and prior[0]!=fingerprint
        if signal['protocol'] not in (None,'UNKNOWN') and not changed:
            decision(signal,'VERIFIED_UNCHANGED');continue
        if prior and prior[1]=='complete' and not changed and now-prior[2]<21600:
            decision(signal,'UNKNOWN_UNCHANGED_RETRY_LATER');continue
        snr=signal.get('peak_snr_db') or 0
        if snr<10:continue
        bandwidth=signal.get('occupied_bandwidth_hz') or 0
        frequency=signal['center_frequency_hz'];fm=87500000<=frequency<=108000000
        rate=2400000 if 1089000000<=frequency<=1091000000 else next((r for r in (250000,1024000,2048000,2400000) if r>=max(1024000 if fm else 250000,bandwidth*3)),None)
        if rate is None:
            decision(signal,'BANDWIDTH_EXCEEDS_CAPTURE');continue
        # Suppress redundant FM follow-ups, never merge catalog channels or identities.
        # 100 kHz radius stays below normal US adjacent-channel spacing (200 kHz).
        if fm and db.execute('''SELECT 1 FROM investigation_jobs j JOIN signals s USING(signal_id)
                WHERE abs(s.center_frequency_hz-?)<100000 AND json_extract(j.settings,'$.sample_rate')>=1024000
                AND (j.status IN ('pending','running','cancel_requested') OR (j.status='complete' AND j.updated>?)) LIMIT 1''',
                (frequency,now-cooldown_s)).fetchone():
            decision(signal,'FM_PASSBAND_ALREADY_INVESTIGATED');continue
        if pending+added>=limit:
            decision(signal,'QUEUE_CAPACITY');continue
        priority=10+(40 if not prior else 0)+(20 if signal['modulation'] in (None,'UNKNOWN') else 0)+(20 if signal['protocol'] in (None,'UNKNOWN') else 0)+(15 if changed else 0)
        if 0<(signal.get('duty_cycle') or 0)<.8:priority+=10
        reason=('Changed spectral evidence' if changed else 'First bounded investigation' if not prior else 'Scheduled retry for new burst evidence')+'; sufficient spectral contrast; decoder-compatible capture'
        duration=2 if fm or rate>250000 else 12 if 314000000<=frequency<=316000000 or 433000000<=frequency<=435000000 or 902000000<=frequency<=928000000 else 8
        job=str(uuid.uuid4());settings=json.dumps(dict(sample_rate=rate,duration=duration,gain=profile['gain'],capture_allowed=True,profile_id=profile['id'],profile_settings=json.dumps(profile,sort_keys=True)))
        db.execute('INSERT INTO investigation_jobs VALUES(?,?,?,?,?,?,?,?,0,?,?,NULL,NULL)',
                   (job,identity,'pending',priority,reason,now,now,now,fingerprint,settings))
        db.execute('INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,?,?)',(job,now,'pending',reason))
        db.execute("UPDATE signals SET investigation_status='pending' WHERE signal_id=?",(identity,));added+=1
        decision(signal,'ENQUEUED',job)
    db.commit();return added


def cancel_job(db,job_id,now=None):
    now=time.time() if now is None else now
    with db:
        changed=db.execute("UPDATE investigation_jobs SET status=CASE WHEN status='running' THEN 'cancel_requested' ELSE 'cancelled' END,updated=? WHERE job_id=? AND status IN ('pending','running')",(now,job_id)).rowcount
        if changed:
            signal_id,status=db.execute('SELECT signal_id,status FROM investigation_jobs WHERE job_id=?',(job_id,)).fetchone()
            db.execute('UPDATE signals SET investigation_status=? WHERE signal_id=?',(status,signal_id))
            db.execute("INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,?,'Operator cancellation requested')",(job_id,now,status))
    return bool(changed)


class InventoryWorker:
    """Single scheduler owner; each bounded backend operation owns the RTL lock."""
    def __init__(self,db,config,scan,capture,analyze,clock=time.time):
        self.db=db;self.config=config;self.scan=scan;self.capture=capture;self.analyze=analyze;self.clock=clock

    def recover(self):
        now=self.clock()
        with self.db:
            recovering=self.db.execute("SELECT job_id,signal_id,status FROM investigation_jobs WHERE status IN ('running','cancel_requested')").fetchall()
            self.db.execute("UPDATE inventory_regions SET status='pending' WHERE status='running'")
            self.db.execute("UPDATE investigation_jobs SET status='pending',updated=?,next_eligible=? WHERE status='running'",(now,now+60))
            self.db.execute("UPDATE investigation_jobs SET status='cancelled',updated=? WHERE status='cancel_requested'",(now,))
            for job,signal_id,old_status in recovering:
                status='cancelled' if old_status=='cancel_requested' else 'pending'
                self.db.execute('UPDATE signals SET investigation_status=? WHERE signal_id=?',(status,signal_id))
                self.db.execute('INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,?,?)',(job,now,status,'Recovered after prior worker stopped'))

    def step_scan(self,region):
        db=self.db;now=self.clock();profile=json.loads(region['settings'])
        with db:db.execute("UPDATE inventory_regions SET status='running',last_visited=?,visit_count=visit_count+1 WHERE region_id=?",(now,region['region_id']))
        try:
            run=self.scan(region,profile)
            measured=db.execute("SELECT status FROM survey_runs WHERE run_id=?",(run,)).fetchone()
            if not measured or measured[0]!='complete':raise ValueError('Survey did not complete valid acquisition')
            from .diagnostics import read_spectra
            segments=db.execute('SELECT start_hz,stop_hz,status,raw_path FROM scan_regions WHERE run_id=? ORDER BY start_hz',(run,)).fetchall()
            cursor=region['start_hz']
            actual_steps=[];acquisition_backends=[];effective_integrations=[];fallback_rejections=[]
            if not segments:raise ValueError('No checkpointed measurement coverage')
            for low,high,status,path in segments:
                if status!='complete' or low>cursor or not path or not Path(path).is_file() or Path(path).stat().st_size>16*1024*1024:
                    raise ValueError('Missing, incomplete or over-budget measurement coverage')
                rows,diagnostic=read_spectra(Path(path))
                if diagnostic['rejected_rows'] or not rows or len(rows)>128 or sum(len(r['power']) for r in rows)>1000000:
                    raise ValueError('Invalid or over-budget measurement rows')
                actual_steps.extend(row['step'] for row in rows)
                metadata_path=Path(path).with_suffix('.command.json')
                metadata=json.loads(metadata_path.read_text(encoding='utf8')) if metadata_path.is_file() else {}
                method=metadata.get('backend','RTL_POWER');acquisition_backends.append(method)
                effective_integrations.append(metadata.get('settings',{}).get('duration_s',profile['integration_s']) if method in ('V4_HF_FLOAT_WELCH','V4_RF_FLOAT_WELCH') else profile['integration_s'])
                if metadata.get('fallback'):fallback_rejections.append(metadata['fallback'])
                covered=low
                for row in sorted(rows,key=lambda r:r['low']):
                    if row['low']>covered+1:raise ValueError('Gap in measurement coverage')
                    covered=max(covered,row['high'])
                if covered<high-1:raise ValueError('Incomplete spectrum coverage')
                cursor=max(cursor,high)
            if cursor<region['stop_hz']:raise ValueError('Incomplete region checkpoints')
            observations=_rows(db,'SELECT * FROM spectral_observations WHERE run_id=?',(run,))
            count=len({o['signal_id'] for o in observations})
            previous=db.execute('SELECT signal_count FROM inventory_measurements WHERE region_id=? AND valid=1 ORDER BY observed DESC LIMIT 1',(region['region_id'],)).fetchone()
            comparison={'previous_signal_count':previous[0] if previous else None,'current_signal_count':count,
                        'signal_count_change':count-previous[0] if previous else None,
                        'meaning':'Counts compare detections under recorded settings, not transmitter identity or absence.'}
            old_path=db.execute('SELECT raw_path FROM scan_regions WHERE run_id=? AND status=? LIMIT 1',(region.get('last_run'),'complete')).fetchone()
            new_path=db.execute('SELECT raw_path FROM scan_regions WHERE run_id=? AND status=? LIMIT 1',(run,'complete')).fetchone()
            previous_quality=json.loads(region.get('quality') or '{}')
            resolution=profile['fine_bin_hz'] if region['stage']=='fine' else profile['bin_hz']
            if old_path and new_path and previous_quality.get('gain_db')==float(profile['gain']) and previous_quality.get('requested_bin_hz')==resolution and previous_quality.get('integration_s')==profile['integration_s']:
                try:comparison['spectrum']=compare_spectra(old_path[0],new_path[0])
                except (OSError,ValueError,TypeError,UnicodeError):comparison['spectrum']={'comparable':False,'reason':'Historical spectrum unavailable'}
            else:comparison['spectrum']={'comparable':False,'reason':'No matching prior acquisition settings/raw spectrum'}
            quality={'valid':True,'resolution_hz':statistics.median(actual_steps),'requested_bin_hz':resolution,
                     'actual_resolution_min_hz':min(actual_steps),'actual_resolution_max_hz':max(actual_steps),
                     'gain_db':float(profile['gain']),'integration_s':statistics.median(effective_integrations),'requested_integration_s':profile['integration_s'],'acquisition_backends':acquisition_backends,'fallback_rejections':fallback_rejections,'diagnostic':'Validated measured spectrum rows and coverage; receiver setup errors rejected',
                     'raw_references':'survey_runs/scan_regions retain original measurements',
                     'scheduler_reason':region.get('_selection_reason','Explicit bounded scan')}
            # One finer revisit after a coarse detection; then normal revisits.
            next_stage='fine' if count and region['stage']=='coarse' else 'coarse'
            due=now+1 if next_stage=='fine' else now+profile['revisit_s']
            with db:
                db.execute("UPDATE inventory_regions SET status='valid',stage=?,next_due=?,last_valid=?,valid_count=valid_count+1,signal_count=?,last_run=?,quality=? WHERE region_id=?",(next_stage,due,now,count,run,json.dumps(quality),region['region_id']))
                db.execute('INSERT INTO inventory_measurements(region_id,observed,run_id,valid,signal_count,comparison,quality) VALUES(?,?,?,1,?,?,?)',(region['region_id'],now,run,count,json.dumps(comparison),json.dumps(quality)))
            enqueue_investigations(db,profile,self.clock(),run_id=run)
        except BaseException as error:
            db.rollback()
            with db:
                failures=db.execute('SELECT count(*) FROM inventory_measurements WHERE region_id=? AND valid=0 AND measurement_id>COALESCE((SELECT max(measurement_id) FROM inventory_measurements WHERE region_id=? AND valid=1),0)',(region['region_id'],region['region_id'])).fetchone()[0]
                retry_s=min(3600,60*2**min(failures,6))
                failure_code=getattr(error,'code','ACQUISITION_OR_VALIDATION_FAILURE')
                db.execute("UPDATE inventory_regions SET status='failed',next_due=?,quality=? WHERE region_id=?",(now+retry_s,json.dumps({'valid':False,'error':type(error).__name__,'failure_code':failure_code,'retry_s':retry_s}),region['region_id']))
                db.execute('INSERT INTO inventory_measurements(region_id,observed,valid,quality) VALUES(?,?,0,?)',(region['region_id'],now,json.dumps({'error':type(error).__name__,'failure_code':failure_code})))
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise

    def step_investigate(self,job):
        db=self.db;now=self.clock();settings=json.loads(job['settings'])
        with db:
            claimed=db.execute("UPDATE investigation_jobs SET status='running',attempts=attempts+1,updated=? WHERE job_id=? AND status='pending'",(now,job['job_id'])).rowcount
            if not claimed:return
            db.execute("UPDATE signals SET investigation_status='running' WHERE signal_id=?",(job['signal_id'],))
            db.execute("INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,'running','Bounded acquisition and analysis started')",(job['job_id'],now))
        try:
            if not settings.get('capture_allowed') or not db.execute('SELECT 1 FROM inventory_regions WHERE enabled=1 AND profile_id=? AND settings=?',(settings.get('profile_id'),settings.get('profile_settings'))).fetchone():
                raise ValueError('Capture permission is absent or profile has changed')
            path,meta=self.capture(job,settings)
            with db:db.execute("INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,'acquired','Bounded recording acquired; analysis follows')",(job['job_id'],self.clock()))
            if db.execute('SELECT status FROM investigation_jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0]=='cancel_requested':
                with db:
                    db.execute("UPDATE investigation_jobs SET status='cancelled',updated=? WHERE job_id=?",(self.clock(),job['job_id']))
                    db.execute("UPDATE signals SET investigation_status='cancelled' WHERE signal_id=?",(job['signal_id'],))
                    db.execute("INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,'cancelled','Cancelled after bounded acquisition')",(job['job_id'],self.clock()))
                return
            key=self.analyze(path,meta,job)
            status='cancelled' if db.execute('SELECT status FROM investigation_jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0]=='cancel_requested' else 'complete'
            error=None
        except BaseException as exception:
            key=None;status='cancelled' if isinstance(exception,InterruptedError) else 'failed';error=type(exception).__name__+': '+str(exception)[:200]
            if isinstance(exception,(KeyboardInterrupt,SystemExit)):
                status='pending'
            with db:
                db.execute('UPDATE investigation_jobs SET status=?,updated=?,next_eligible=?,error=? WHERE job_id=?',(status,self.clock(),self.clock()+3600,error,job['job_id']))
                db.execute('INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,?,?)',(job['job_id'],self.clock(),status,error))
                db.execute('UPDATE signals SET investigation_status=? WHERE signal_id=?',(status,job['signal_id']))
            if isinstance(exception,(KeyboardInterrupt,SystemExit)):raise
            return
        with db:
            db.execute('UPDATE investigation_jobs SET status=?,updated=?,result_key=?,error=? WHERE job_id=?',(status,self.clock(),key,error,job['job_id']))
            db.execute('INSERT INTO investigation_events(job_id,created,status,detail) VALUES(?,?,?,?)',(job['job_id'],self.clock(),status,'Bounded analysis stored; protocol may remain UNKNOWN'))
            db.execute('UPDATE signals SET investigation_status=? WHERE signal_id=?',(status,job['signal_id']))

    def _due_region(self,now,smart=False):
        full=self.db.execute("SELECT count(DISTINCT profile_id) FROM inventory_regions WHERE enabled=1 AND profile_id IN ('hf','vhf','uhf','upper')").fetchone()[0]==4
        if full:
            rows=_rows(self.db,"SELECT * FROM inventory_regions WHERE enabled=1 AND status!='running' AND next_due<=? ORDER BY visit_count,next_due,start_hz LIMIT 1",(now,))
            if rows:rows[0]['_selection_reason']='Full-range least-visited coverage before follow-up'
            return rows
        if not smart:
            return _rows(self.db,"SELECT * FROM inventory_regions WHERE enabled=1 AND status!='running' AND next_due<=? ORDER BY CASE WHEN stage='fine' THEN 0 ELSE 1 END,next_due,start_hz LIMIT 1",(now,))
        # Persisted measurement count makes exploration survive child/app restarts.
        # At least every third due choice explores the least visited region.
        count=self.db.execute('SELECT count(*) FROM inventory_measurements').fetchone()[0]
        exploration=count%3==0
        if exploration:
            order='visit_count,next_due,start_hz'
        else:
            order="CASE WHEN r.stage='fine' THEN 0 ELSE 1 END,CASE WHEN r.valid_count=0 THEN 0 ELSE 1 END,COALESCE((SELECT max(CASE WHEN s.protocol IS NULL OR s.protocol='UNKNOWN' THEN 20 ELSE 0 END + CASE WHEN s.modulation IS NULL OR s.modulation='UNKNOWN' THEN 10 ELSE 0 END + CASE WHEN s.duty_cycle>0 AND s.duty_cycle<0.8 THEN 10 ELSE 0 END + CASE WHEN s.investigation_status IN ('pending','failed') THEN 10 ELSE 0 END) FROM signals s WHERE s.center_frequency_hz>=r.start_hz AND s.center_frequency_hz<r.stop_hz),0) DESC,r.signal_count DESC,r.next_due,r.start_hz"
        rows=_rows(self.db,"SELECT r.* FROM inventory_regions r WHERE r.enabled=1 AND r.status!='running' AND r.next_due<=? ORDER BY "+order+' LIMIT 1',(now,))
        if rows:rows[0]['_selection_reason']='Least-visited exploration (every third choice)' if exploration else 'Fine follow-up, unmeasured region, unknown/intermittent activity, due time'
        return rows

    def run(self,max_steps=1,max_seconds=120,investigate=True,profiles=None,selected=None,smart=False):
        from .platforms import DeviceLock
        if type(max_steps) is not int or not 1<=max_steps<=100 or type(max_seconds) not in (int,float) or not math.isfinite(max_seconds) or not 1<=max_seconds<=1800:
            raise ValueError('Require 1..100 steps and 1..1800 seconds')
        done=0;deadline=time.monotonic()+max_seconds
        with DeviceLock(self.config.lock_root,'inventory-scheduler'):
            if profiles is not None:configure_regions(self.db,profiles,selected)
            self.recover()
            while done<max_steps:
                now=self.clock()
                jobs=_rows(self.db,"SELECT * FROM investigation_jobs WHERE status='pending' AND next_eligible<=? ORDER BY priority+max(0,?-created)/60.0 DESC,created LIMIT 1",(now,now)) if investigate else []
                regions=self._due_region(now,smart)
                if jobs and (done%3==2 or not regions or (done%2==1 and now-jobs[0]['created']>=60)):
                    if deadline-time.monotonic()<65:break
                    self.step_investigate(jobs[0])
                elif regions:
                    needed=json.loads(regions[0]['settings'])['duration_s']+65
                    if deadline-time.monotonic()<needed:break
                    self.step_scan(regions[0])
                else:break
                done+=1
        return {'steps_completed':done,'bounded':True,'queue':queue_view(self.db)['counts']}
