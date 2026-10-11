"""Read-only discovery snapshots and measured, portable host health."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import ctypes
import json
import os
import shutil
import sqlite3
import threading
import time

from .timestamps import observation_utc
OBSERVED_UTC=observation_utc()

CATEGORY_SQL = """CASE WHEN s.protocol='ADS-B' THEN 'Aircraft'
 WHEN s.protocol='AX.25' THEN 'Packet Radio'
 WHEN s.protocol='RDS' THEN 'Broadcast Systems'
 WHEN s.protocol IN ('Fineoffset-WH2','Fineoffset-WH5') THEN 'Environmental Sensors'
 WHEN COALESCE(s.protocol,'UNKNOWN')!='UNKNOWN' THEN 'Other verified protocols'
 ELSE 'Unknown / Other' END"""


def utc(value):
    if not isinstance(value,str) or len(value)>64: raise ValueError('Invalid UTC timestamp')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('Explicit timezone required')
    return parsed.astimezone(timezone.utc).isoformat()


class HealthMonitor:
    """Host CPU/RAM, volume usage and bounded recording-directory usage."""
    def __init__(self,root,retention_bytes=None):
        self.root=Path(root);self.retention_bytes=retention_bytes
        self._lock=threading.Lock();self._previous=None;self._cached=None;self._last=0

    def _host(self):
        if os.name=='nt':
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            values=[wintypes.FILETIME() for _ in range(3)]
            if not kernel.GetSystemTimes(*[ctypes.byref(v) for v in values]): raise OSError('CPU unavailable')
            idle,kernel_ticks,user=[(v.dwHighDateTime<<32)+v.dwLowDateTime for v in values]
            class Memory(ctypes.Structure):
                _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD)]+[(k,ctypes.c_ulonglong) for k in ('total','available','total_page','available_page','total_virtual','available_virtual','extended')]
            memory=Memory();memory.length=ctypes.sizeof(memory)
            if not kernel.GlobalMemoryStatusEx(ctypes.byref(memory)): raise OSError('RAM unavailable')
            return (idle,kernel_ticks+user),memory.total,memory.total-memory.available
        fields=Path('/proc/stat').read_text().splitlines()[0].split()[1:9]
        values=[int(v) for v in fields]
        memory={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
        return (values[3]+values[4],sum(values)),memory['MemTotal'],memory['MemTotal']-memory['MemAvailable']

    def sample(self):
        with self._lock:
            now=time.monotonic()
            if self._cached is not None and now-self._last<3:return dict(self._cached)
            result=dict(cpu_percent=None,ram_total_bytes=None,ram_used_bytes=None,ram_percent=None,
                        disk_total_bytes=None,disk_used_bytes=None,recording_bytes=None,
                        recording_limit_bytes=self.retention_bytes,recording_scan_truncated=False,
                        basis='Host CPU/RAM and runtime volume; CPU requires two samples. Recording usage includes metadata and partial files.')
            try:
                ticks,total,used=self._host()
                if self._previous and ticks[1]>self._previous[1]:
                    result['cpu_percent']=max(0,min(100,100*(1-(ticks[0]-self._previous[0])/(ticks[1]-self._previous[1]))))
                self._previous=ticks
                result.update(ram_total_bytes=total,ram_used_bytes=used,ram_percent=used/total*100)
            except (OSError,KeyError,ValueError):pass
            try:
                disk=shutil.disk_usage(self.root);result.update(disk_total_bytes=disk.total,disk_used_bytes=disk.used)
            except OSError:pass
            try:
                folder=self.root/'data/captures';used=0;entries=0
                if folder.exists():
                    for directory,dirs,files in os.walk(folder,followlinks=False):
                        dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
                        for name in files:
                            entries+=1
                            if entries>10000:break
                            file=Path(directory)/name
                            if not file.is_symlink():used+=file.stat().st_size
                        if entries>10000:break
                result['recording_scan_truncated']=entries>10000
                result['recording_bytes']=None if entries>10000 else used
            except OSError:pass
            self._cached=result;self._last=now
            return dict(result)


class DiscoveryView:
    def __init__(self,root,started=None,health=None):
        self.root=Path(root);self.path=self.root/'data/rf_atlas.sqlite'
        self.started=started or datetime.now(timezone.utc).isoformat()
        self.health=health or HealthMonitor(root)
        # Session membership follows insertion order, independently of legacy
        # rtl_power wall-clock timestamps (which carry no timezone).
        self._baseline=0
        if self.path.is_file():
            with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
                self._baseline=db.execute('SELECT COALESCE(max(observation_id),0) FROM spectral_observations').fetchone()[0]

    def _scope(self,query):
        allowed={'session','since','until','limit','category','compare'}
        if set(query)-allowed or any(len(v)!=1 for v in query.values()):raise ValueError('Unknown or repeated filter')
        session=query.get('session',['all'])[0]
        if len(session)>128:raise ValueError('Invalid session')
        since=utc(query['since'][0]) if query.get('since') else None
        until=utc(query['until'][0]) if query.get('until') else None
        if since and until and datetime.fromisoformat(since)>=datetime.fromisoformat(until):raise ValueError('Invalid time range')
        limit=int(query.get('limit',['60'])[0])
        if not 1<=limit<=200:raise ValueError('Invalid feed limit')
        category=query.get('category',[''])[0]
        if len(category)>100:raise ValueError('Invalid category')
        clauses=[];args=[]
        if session=='live':clauses.append('o.observation_id>?');args.append(self._baseline)
        elif session!='all':clauses.append('o.run_id=?');args.append(session)
        if since:clauses.append(f'julianday({OBSERVED_UTC})>=julianday(?)');args.append(since)
        if until:clauses.append(f'julianday({OBSERVED_UTC})<julianday(?)');args.append(until)
        return session,since,until,limit,category,(' WHERE '+' AND '.join(clauses) if clauses else ''),args

    def snapshot(self,query):
        session,since,until,limit,category,where,args=self._scope(query)
        deadline=time.monotonic()+1.5
        with closing(sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
            db.row_factory=sqlite3.Row
            db.set_progress_handler(lambda:time.monotonic()>deadline,1000)
            db.execute('BEGIN')
            schema_version=db.execute('PRAGMA user_version').fetchone()[0]
            if session not in ('all','live') and not db.execute('SELECT 1 FROM survey_runs WHERE run_id=?',(session,)).fetchone():raise ValueError('Unknown session')
            base=' FROM spectral_observations o JOIN signals s USING(signal_id)'
            categories=[dict(row) for row in db.execute('SELECT '+CATEGORY_SQL+f''' AS category,count(*) AS detections,count(DISTINCT o.signal_id) AS unique_signals,
                count(DISTINCT CASE WHEN COALESCE(s.protocol,'UNKNOWN')!='UNKNOWN' THEN o.signal_id END) AS verified,
                count(DISTINCT CASE WHEN COALESCE(s.protocol,'UNKNOWN')='UNKNOWN' THEN o.signal_id END) AS unknown,
                max(julianday({OBSERVED_UTC})) AS last_jd,group_concat(DISTINCT s.protocol) AS signal_types'''+base+where+' GROUP BY category ORDER BY detections DESC,category',args)]
            for row in categories:
                row['last_seen']=db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ',?)",(row.pop('last_jd'),)).fetchone()[0]
                row['signal_types']=sorted((row['signal_types'] or 'UNKNOWN').split(','))
            totals=dict(db.execute('SELECT count(*) AS detections,count(DISTINCT o.signal_id) AS unique_signals,count(DISTINCT CASE WHEN COALESCE(s.protocol,\'UNKNOWN\')!=\'UNKNOWN\' THEN s.protocol END) AS verified_protocols'+base+where,args).fetchone())
            feedwhere=where;feedargs=list(args)
            if category:feedwhere+=(' AND ' if where else ' WHERE ')+CATEGORY_SQL+'=?';feedargs.append(category)
            recent=[dict(row) for row in db.execute('SELECT o.observation_id,o.signal_id,o.run_id,o.observed,o.observed_utc,o.observed_original,o.timestamp_basis,o.center_hz AS frequency_hz,o.peak_snr_db,s.modulation,s.protocol,s.signal_class,'+CATEGORY_SQL+' AS category'+base+feedwhere+f' ORDER BY julianday({OBSERVED_UTC}) DESC,o.observation_id DESC LIMIT ?',feedargs+[limit])]
            for row in recent:
                from .timestamps import aware_utc
                row['observed_utc']=row['observed_utc'] or aware_utc(row['observed'])
                row['timestamp_basis']=row['timestamp_basis'] or ('EXPLICIT_OFFSET' if row['observed_utc'] else 'LEGACY_TIMEZONE_UNKNOWN')
                row['verification']='VERIFIED_PROTOCOL' if row['protocol'] and row['protocol']!='UNKNOWN' else 'MODULATION_CANDIDATE' if row['modulation'] and row['modulation']!='UNKNOWN' else 'RF_DETECTED_UNKNOWN'
                row['identifiers']=[dict(r) for r in db.execute('''SELECT DISTINCT e.kind,e.protocol,e.identifier,e.entity_id FROM entity_observations eo JOIN entities e USING(entity_id)
                    WHERE eo.signal_id=? AND julianday(eo.observed)=julianday(?) LIMIT 4''',(row['signal_id'],row['observed_utc']))]
            sessions=[dict(row) for row in db.execute('''SELECT run_id,started,ended,status,profile,start_hz,stop_hz FROM survey_runs ORDER BY started DESC LIMIT 100''')]
            queue={row[0]:row[1] for row in db.execute('SELECT status,count(*) FROM investigation_jobs GROUP BY status')}
            region=db.execute("SELECT start_hz,stop_hz,status,quality FROM inventory_regions WHERE enabled=1 AND status='running' ORDER BY last_visited DESC LIMIT 1").fetchone()
            quality=db.execute('SELECT quality,status,last_visited FROM inventory_regions WHERE enabled=1 AND last_visited IS NOT NULL ORDER BY last_visited DESC LIMIT 1').fetchone()
            coverage=dict(db.execute('SELECT count(*) AS total_regions,sum(visit_count>0) AS visited_regions,sum(valid_count>0) AS valid_regions FROM inventory_regions WHERE enabled=1').fetchone())
            timeline=[dict(row) for row in db.execute(f"SELECT CAST((julianday({OBSERVED_UTC})-2440587.5)*86400/300 AS INTEGER)*300 AS timestamp,count(*) AS detections,count(DISTINCT o.signal_id) AS unique_signals"+base+where+' GROUP BY timestamp HAVING timestamp IS NOT NULL ORDER BY timestamp DESC LIMIT 288',args)]
            comparison=None
            if query.get('compare',[''])[0]:
                other=query['compare'][0]
                _,_,_,_,_,cw,ca=self._scope({**query,'session':[other],'compare':['']})
                if other not in ('all','live') and not db.execute('SELECT 1 FROM survey_runs WHERE run_id=?',(other,)).fetchone():raise ValueError('Unknown comparison session')
                ct=dict(db.execute('SELECT count(*) AS detections,count(DISTINCT o.signal_id) AS unique_signals'+base+cw,ca).fetchone())
                comparison=dict(session=other,totals=ct,delta={k:totals[k]-ct[k] for k in ct},basis='Selected minus comparison under the same UTC time window; observation counts are not exposure-normalized.')
            counts={row[0]:row[1] for row in db.execute('SELECT kind,count(*) FROM entities GROUP BY kind')}
        return dict(scope=dict(session=session,since=since,until=until,interval='[since, until)',feed_category=category),
            comparison=comparison,totals=totals,categories=categories,recent=recent,sessions=sessions,queue=queue,coverage=coverage,timeline=list(reversed(timeline)),
            current_region=dict(region) if region else None,measurement_quality=dict(quality) if quality else None,
            entities=dict(devices=counts.get('device',0),stations=counts.get('station',0),basis='All retained verified protocol identifiers; not time-window filtered physical-device counts.'),
            database=dict(state='available',schema_version=schema_version),health=self.health.sample(),
            notes=['Categories and verification use current catalog evidence for the selected observation window, not reconstructed historical classification.',
                   'Unique signals are catalog channels, not physical transmitters. Detection counts are stored observation rows.',
                   'Legacy timestamps without an offset are ambiguous and excluded from UTC filters and timeline bins. All-time history preserves original values. Current app membership uses insertion order.',
                   'Only identifiers with an equal capture/observation timestamp are attached to events. Later identifiers remain in investigation history.',
                   'Coverage and investigation queue describe current runtime state; historical selection does not control the receiver.'])
