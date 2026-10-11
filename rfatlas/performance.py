"""Private machine-readable discovery reports, computed without receiver access."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
from .discovery import utc
from .timestamps import observation_utc


def compare_reports(previous,current):
    keys=('throughput_hz_per_wall_second','candidate_to_completed_investigation_rate',
          'observations','verified_protocol_instances','devices_identified','stations_identified')
    return dict(previous_window=dict(started=previous['started'],ended=previous['ended']),
        values={key:dict(previous=previous.get(key),current=current.get(key)) for key in keys},
        basis='Descriptive comparison only. Different RF activity, dwell, gain, resolution, ranges and antenna conditions can change yield; no speed or sensitivity gain is inferred.')


def discovery_report(root,since,until,*,profile='selected window',hardware=None,
                     health=(),initial_database_bytes=None):
    since,until=utc(since),utc(until)
    start,end=map(datetime.fromisoformat,(since,until));seconds=(end-start).total_seconds()
    if seconds<=0:raise ValueError('Positive report interval required')
    path=Path(root)/'data/rf_atlas.sqlite';deadline=time.monotonic()+10
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
        db.row_factory=sqlite3.Row
        db.set_progress_handler(lambda:time.monotonic()>deadline,1000);db.execute('BEGIN')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        epoch=(start.timestamp(),end.timestamp());args=(since,until)
        visits=[dict(row) for row in db.execute('''SELECT m.region_id,r.profile_id,r.start_hz,r.stop_hz,m.run_id,
            m.observed,m.valid,m.signal_count,m.quality FROM inventory_measurements m
            JOIN inventory_regions r USING(region_id) WHERE m.observed>=? AND m.observed<? ORDER BY m.observed LIMIT 10001''',epoch)]
        visits.extend(dict(row) for row in db.execute('''SELECT NULL AS region_id,s.profile AS profile_id,
            c.start_hz,c.stop_hz,c.run_id,cast(strftime('%s',c.completed) AS REAL) AS observed,1 AS valid,
            (SELECT count(*) FROM spectral_observations o WHERE o.run_id=c.run_id AND o.center_hz>=c.start_hz AND o.center_hz<c.stop_hz) AS signal_count,
            s.settings AS quality FROM scan_regions c JOIN survey_runs s USING(run_id)
            WHERE c.status='complete' AND julianday(c.completed)>=julianday(?) AND julianday(c.completed)<julianday(?)
            AND NOT EXISTS(SELECT 1 FROM inventory_measurements m WHERE m.run_id=c.run_id) ORDER BY c.completed LIMIT 10001''',args))
        truncated=len(visits)>10000;visits=visits[:10000]
        for row in visits:row['quality']=json.loads(row['quality'] or '{}')
        scope=f'julianday({observation_utc()})>=julianday(?) AND julianday({observation_utc()})<julianday(?)'
        counts=dict(db.execute(f'''SELECT count(*) AS observations,count(DISTINCT o.signal_id) AS unique_candidate_signals,
            count(DISTINCT CASE WHEN o.peak_snr_db>=10 THEN o.signal_id END) AS quality_qualified_candidate_signals,
            count(DISTINCT CASE WHEN COALESCE(s.protocol,'UNKNOWN')='UNKNOWN' THEN o.signal_id END) AS unknown_signals
            FROM spectral_observations o JOIN signals s USING(signal_id) WHERE {scope}''',args).fetchone())
        revisited=db.execute(f'''SELECT count(*) FROM (SELECT o.signal_id,min(o.observation_id) AS first_selected
            FROM spectral_observations o WHERE {scope} GROUP BY o.signal_id) selected WHERE EXISTS(
            SELECT 1 FROM spectral_observations old WHERE old.signal_id=selected.signal_id AND old.observation_id<selected.first_selected)''',args).fetchone()[0]
        jobs=dict(db.execute('''SELECT count(*) AS completed,avg(updated-created) AS average_latency_s
            FROM investigation_jobs WHERE status='complete' AND updated>=? AND updated<?''',epoch).fetchone())
        backlog={row[0]:row[1] for row in db.execute('SELECT status,count(*) FROM investigation_jobs GROUP BY status')}
        cohort=dict(db.execute('''SELECT count(*) AS enqueued,
            sum(status='complete') AS complete,sum(status='failed') AS failed,
            sum(status='cancelled') AS cancelled,sum(status IN ('pending','running','cancel_requested')) AS outstanding
            FROM investigation_jobs WHERE created>=? AND created<?''',epoch).fetchone())
        cohort={k:v or 0 for k,v in cohort.items()}
        investigated=db.execute(f'''SELECT count(DISTINCT o.signal_id) FROM spectral_observations o
            WHERE {scope} AND EXISTS(SELECT 1 FROM investigation_jobs j WHERE j.signal_id=o.signal_id
            AND j.status='complete' AND j.updated>=? AND j.updated<?)''',args+epoch).fetchone()[0]
        decision_counts={row[0]:row[1] for row in db.execute('''SELECT reason,count(*) FROM investigation_decisions
            WHERE created>=? AND created<? GROUP BY reason''',epoch)} if 'investigation_decisions' in tables else {}
        decision_latest={row[0]:row[1] for row in db.execute('''SELECT reason,count(*) FROM investigation_decisions
            WHERE decision_id IN (SELECT max(decision_id) FROM investigation_decisions WHERE created>=? AND created<?
            GROUP BY signal_id) GROUP BY reason''',epoch)} if 'investigation_decisions' in tables else {}
        timings=dict(db.execute('''SELECT count(*) AS measured_segments,sum(acquisition_s) AS acquisition_process_s,
            sum(processing_s) AS parsing_detection_database_s,sum(requested_dwell_s) AS requested_dwell_s,
            sum(valid) AS valid_segments FROM acquisition_timings WHERE julianday(recorded)>=julianday(?)
            AND julianday(recorded)<julianday(?)''',args).fetchone()) if 'acquisition_timings' in tables else dict(
                measured_segments=0,acquisition_process_s=None,parsing_detection_database_s=None,requested_dwell_s=None,valid_segments=None)
        queue_wait=db.execute('''SELECT avg(e.created-j.created),max(e.created-j.created) FROM investigation_jobs j
            JOIN investigation_events e USING(job_id) WHERE e.status='running' AND e.created>=? AND e.created<?''',epoch).fetchone()
        work_times=db.execute('''SELECT sum(j.updated-(SELECT max(e.created) FROM investigation_events e
            WHERE e.job_id=j.job_id AND e.status='running')),
            sum((SELECT max(e.created) FROM investigation_events e WHERE e.job_id=j.job_id AND e.status='acquired')-
                (SELECT max(e.created) FROM investigation_events e WHERE e.job_id=j.job_id AND e.status='running'))
            FROM investigation_jobs j WHERE j.status IN ('complete','failed','cancelled') AND j.updated>=? AND j.updated<?''',epoch).fetchone()
        attempts={};protocols={}
        results=db.execute('SELECT signal_id,result FROM identification_runs WHERE julianday(created)>=julianday(?) AND julianday(created)<julianday(?) LIMIT 10001',args).fetchall()
        truncated=truncated or len(results)>10000
        attempted_signals=set();modulation_signals=set();verified_signals=set()
        for row in results[:10000]:
            result=json.loads(row['result']);protocol=result.get('protocol','UNKNOWN')
            if result.get('modulation','UNKNOWN')!='UNKNOWN':modulation_signals.add(row['signal_id'])
            if result.get('attempts'):attempted_signals.add(row['signal_id'])
            if protocol!='UNKNOWN' and result.get('protocol_verified') is True:
                protocols[protocol]=protocols.get(protocol,0)+1;verified_signals.add(row['signal_id'])
            for attempt in result.get('attempts',[]):
                status=attempt.get('status','UNSPECIFIED');attempts[status]=attempts.get(status,0)+1
        entities={row[0]:row[1] for row in db.execute('''SELECT e.kind,count(DISTINCT e.entity_id)
            FROM entities e JOIN entity_observations o USING(entity_id) WHERE julianday(o.observed)>=julianday(?)
            AND julianday(o.observed)<julianday(?) GROUP BY e.kind''',args)}
        capture_protocols={row[0]:row[1] for row in db.execute('''SELECT json_extract(result,'$.protocol'),
            count(DISTINCT COALESCE(source_sha256,run_key)) FROM identification_runs
            WHERE json_extract(result,'$.protocol_verified')=1 AND json_extract(result,'$.protocol')!='UNKNOWN'
            AND julianday(json_extract(result,'$.capture_timestamp'))>=julianday(?)
            AND julianday(json_extract(result,'$.capture_timestamp'))<julianday(?)
            GROUP BY json_extract(result,'$.protocol')''',args)}
        analyzed_captures=db.execute('''SELECT count(DISTINCT COALESCE(source_sha256,run_key)) FROM identification_runs
            WHERE julianday(json_extract(result,'$.capture_timestamp'))>=julianday(?)
            AND julianday(json_extract(result,'$.capture_timestamp'))<julianday(?)''',args).fetchone()[0]
        ambiguous=db.execute('SELECT count(*) FROM spectral_observations o WHERE '+observation_utc()+' IS NULL').fetchone()[0]
        catalog_signals=db.execute('SELECT count(*) FROM signals').fetchone()[0]
        database_bytes=db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]
    valid=[row for row in visits if row['valid']];gross=sum(row['stop_hz']-row['start_hz'] for row in valid)
    cursor=None;unique=0
    for low,high in sorted((row['start_hz'],row['stop_hz']) for row in valid):
        unique+=max(0,high-max(low,cursor if cursor is not None else low));cursor=max(high,cursor or high)
    samples=list(health)[:10000]
    def metric(key):
        values=[row[key] for row in samples if isinstance(row.get(key),(float,int))]
        return {'mean':sum(values)/len(values),'max':max(values),'samples':len(values)} if values else None
    def ratio(n,d):return n/d if d else None
    failure_codes={}
    for row in visits:
        if not row['valid']:
            code=row['quality'].get('failure_code','LEGACY_UNCLASSIFIED_FAILURE')
            failure_codes[code]=failure_codes.get(code,0)+1
    return dict(report_schema=2,source='CATALOG_EVIDENCE',started=since,ended=until,seconds=seconds,profile=profile,
        hardware=hardware or {'status':'Not supplied; per-visit acquisition settings retained'},frequency_ranges_visited=visits,
        acquisitions=len(visits),valid_acquisitions=len(valid),invalid_measurements=len(visits)-len(valid),
        acquisition_failures=sum(not r['valid'] and r['quality'].get('error') not in ('ValueError','SpectrumMeasurementError',None) for r in visits),
        measurement_failure_codes=failure_codes,
        successfully_measured_hz_gross=gross,successfully_measured_hz_unique=unique,throughput_hz_per_wall_second=gross/seconds,
        **counts,previously_known_signals_revisited=revisited,new_candidate_signals=counts['unique_candidate_signals']-revisited,
        investigations=jobs,protocols_verified=protocols,decoder_attempts=attempts,devices_identified=entities.get('device',0),
        investigation_cohort=cohort,investigated_candidate_signals=investigated,
        catalog_signal_records_at_report=catalog_signals,
        distinct_signals_analyzed=len({r['signal_id'] for r in results[:10000]}),
        candidate_to_completed_investigation_rate=ratio(investigated,counts['unique_candidate_signals']),
        investigation_completion_rate=ratio(cohort['complete'],cohort['enqueued']),
        decoder_attempt_rate=ratio(len(attempted_signals),len({r['signal_id'] for r in results[:10000]})),
        protocol_verification_rate=ratio(len(verified_signals),len({r['signal_id'] for r in results[:10000]})),
        verified_protocol_instances=sum(protocols.values()),distinct_verified_protocols=len(protocols),
        verified_captures_by_protocol=capture_protocols,verified_capture_instances=sum(capture_protocols.values()),
        analyzed_capture_instances=analyzed_captures,capture_protocol_verification_rate=ratio(sum(capture_protocols.values()),analyzed_captures),
        modulation_hypothesis_signals=len(modulation_signals),
        investigation_decision_events=decision_counts,latest_candidate_decisions=decision_latest,
        queue_wait_s=dict(mean=queue_wait[0],max=queue_wait[1]),timings=timings,
        investigation_work_s=work_times[0],investigation_capture_s=work_times[1],
        investigation_wall_fraction=ratio(work_times[0],seconds) if work_times[0] is not None else None,
        measured_acquisition_processes_per_second=ratio(timings['measured_segments'],seconds),
        invalid_measurement_rate=ratio(len(visits)-len(valid),len(visits)),
        observations_per_valid_mhz=ratio(counts['observations'],gross/1e6),
        stations_identified=entities.get('station',0),queue_backlog_at_report=backlog,host_cpu_percent=metric('cpu_percent'),
        host_ram_used_bytes=metric('ram_used_bytes'),database_bytes=database_bytes,
        database_growth_bytes=None if initial_database_bytes is None else database_bytes-initial_database_bytes,
        truncated=truncated,legacy_ambiguous_observations_excluded=ambiguous,
        limitations=['Local report contains private RF observations; review before publishing.',
            'Gross bandwidth counts revisits; unique bandwidth unions valid ranges. Neither implies continuous reception.',
            'Bursts between visits may be missed. No detection never establishes an unused frequency.',
            'Unknown counts use current catalog state. Candidates and fingerprints are not physical transmitters.',
            'Latency includes queue waiting, capture and analysis. Host health is not process RSS.',
            'Decision events repeat on revisits; latest decisions count channels once. Historical decisions are not reconstructed.',
            'Acquisition process time includes startup, tuning, settling, transfer and FFT; those stages cannot be separated by rtl_power.',
            'Measured processes are not exact tuner-hop counts. Analysis instances may include offline replays; completed jobs count acquisitions.',
            'Capture-window counts deduplicate source digests across analysis revisions and include later verification of those recordings.',
            'Only retained verified identifiers count as entities; privacy settings can leave those counts zero.'])
