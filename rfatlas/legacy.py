"""Receive-only RF Atlas: portable survey and bounded IQ analysis."""
import argparse
import csv
import json
import logging
import re
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
import time
from types import SimpleNamespace
from datetime import datetime, timezone

import numpy as np
from scipy.ndimage import median_filter
from .diagnostics import noise_model, read_spectra, atomic_json

from .config import load_config
from .hardware import RTLBackend
try:
    CONFIG = load_config()
except (OSError,ValueError):
    from .config import Config,default_root
    CONFIG=Config(default_root())
ROOT = CONFIG.root
PROFILES = {'quick': (25000, 5), 'balanced': (5000, 10), 'deep': (1000, 20)}
from .hardware import gain_value


def positive_int(value):
    number=int(value)
    if number<=0: raise argparse.ArgumentTypeError('Value must be positive')
    return number


def now():
    return datetime.now(timezone.utc).isoformat()


def frequency(value):
    text = str(value).strip().upper()
    scale = {'K': 1e3, 'M': 1e6, 'G': 1e9}
    number = float(text[:-1]) * scale[text[-1]] if text[-1] in scale else float(text)
    if not np.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('Frequency must be positive and finite')
    return int(number)


def DeviceLock():
    from .platforms import DeviceLock as Lock
    return Lock(CONFIG.lock_root, 'rtl-sdr')


def command(args, timeout, diagnostic_path=None):
    from .platforms import run_process
    return run_process(args,timeout,diagnostic_path)


def database():
    from .storage import open_database
    return open_database(ROOT)


from .detection import detect


def ingest(db, run, path, region_low, region_high, margin=6):
    spectra,diagnostic=read_spectra(path)
    atomic_json(path.with_suffix('.parse.json'),diagnostic)
    from .measurement_errors import SpectrumMeasurementError,rejection_code
    if diagnostic['rejected_rows']: raise SpectrumMeasurementError(rejection_code(diagnostic),f"Rejected rtl_power rows; see {path.with_suffix('.parse.json')}")
    spans = []
    parsed = []
    for row in spectra:
        low,high,step=row['low'],row['high'],row['step']
        spans.append((low,high))
        from .detection import detect_survey
        parsed.append((row['observed'],detect_survey(low,step,row['power'],margin),step))
    # Validate real output covers the requested interval before checkpointing.
    cursor = region_low
    for low, high in sorted(spans):
        if low > cursor + 1: raise SpectrumMeasurementError('COVERAGE_GAP','Gap in returned spectrum coverage')
        cursor = max(cursor,high)
    if cursor < region_high-1: raise SpectrumMeasurementError('INCOMPLETE_COVERAGE','Returned spectrum does not cover requested range')
    settings=json.loads(db.execute('SELECT settings FROM survey_runs WHERE run_id=?',(run,)).fetchone()[0])
    command_path=path.with_suffix('.command.json')
    acquisition=json.loads(command_path.read_text(encoding='utf8')) if command_path.exists() else {}
    rate_match=re.search(r'Exact sample rate is:\s*([\d.]+)',acquisition.get('stderr',''))
    sample_rate=float(rate_match[1]) if rate_match else None
    effective_seconds=acquisition.get('settings',{}).get('duration_s',settings.get('seconds')) if acquisition.get('backend')=='V4_HF_FLOAT_WELCH' else settings.get('seconds')
    for row,(observed, peaks, step) in zip(spectra,parsed):
        from .timestamps import receiver_utc,sql_utc
        original_observed=observed
        observed,timestamp_basis=receiver_utc(observed,acquisition.get('started'))
        db.execute('INSERT OR IGNORE INTO spectrum_times VALUES(?,?,?,?,?)',(run,str(path),original_observed,observed,timestamp_basis))
        _,thresholds=noise_model(row['power'],step,margin)
        for peak in peaks:
            if not region_low <= peak['center'] <= region_high: continue
            if db.execute('SELECT 1 FROM spectral_observations WHERE run_id=? AND raw_path=? AND observed_original=? AND center_hz=?',(run,str(path),original_observed,peak['center'])).fetchone():continue
            bandwidth=peak['high']-peak['low']
            existing = db.execute('SELECT signal_id FROM signals WHERE abs(center_frequency_hz-?) <= ? AND (occupied_bandwidth_hz IS NULL OR occupied_bandwidth_hz BETWEEN ? AND ?) ORDER BY abs(center_frequency_hz-?) LIMIT 1', (peak['center'],max(step*2,bandwidth/2),max(step,bandwidth/3),max(step*3,bandwidth*3),peak['center'])).fetchone()
            signal = existing[0] if existing else str(uuid.uuid4())
            if not existing:
                db.execute('INSERT INTO signals(signal_id,center_frequency_hz,frequency_min_hz,frequency_max_hz,occupied_bandwidth_hz,first_seen,last_seen,observation_count,peak_snr_db) VALUES(?,?,?,?,?,?,?,0,?)',(signal,peak['center'],peak['low'],peak['high'],peak['high']-peak['low'],observed,observed,peak['peak']))
            db.execute('UPDATE signals SET frequency_uncertainty_hz=? WHERE signal_id=?',(max(step,bandwidth/2),signal))
            db.execute('INSERT INTO spectral_observations(run_id,signal_id,observed,start_hz,stop_hz,center_hz,peak_snr_db,average_snr_db,noise_db,raw_path) VALUES(?,?,?,?,?,?,?,?,?,?)',(run,signal,observed,peak['low'],peak['high'],peak['center'],peak['peak'],peak['average'],peak['noise'],str(path)))
            observation_id=db.execute('SELECT last_insert_rowid()').fetchone()[0]
            db.execute('UPDATE spectral_observations SET observed_original=?,observed_utc=?,timestamp_basis=? WHERE observation_id=?',(original_observed,observed,timestamp_basis,observation_id))
            settings=json.loads(db.execute('SELECT settings FROM survey_runs WHERE run_id=?',(run,)).fetchone()[0])
            gain=settings.get('gain')
            db.execute('UPDATE spectral_observations SET gain_db=?,gain_mode=? WHERE observation_id=last_insert_rowid()',(None if gain in (None,'auto') else float(gain),'auto' if gain=='auto' else ('manual' if gain is not None else 'unknown')))
            indices=(row['centers']>=peak['low'])&(row['centers']<peak['high'])
            evidence={'threshold_rule':'local median + max(margin, 4 scaled local deviations)','threshold_min_db':float(thresholds[indices].min()),'threshold_max_db':float(thresholds[indices].max()),'requested_bin_hz':settings.get('bin_hz'),'sample_rate_source':'tool stderr' if rate_match else 'unreported','integration_s':effective_seconds,'gain':gain}
            evidence['detector_method']=peak.get('method','ADAPTIVE_MEDIAN')
            evidence['noise_basis']='prominence trough; broad energy hypothesis' if peak.get('method') else 'adaptive local median'
            db.execute('UPDATE spectral_observations SET sample_rate_hz=?,fft_resolution_hz=?,integration_s=?,threshold_margin_db=?,measurement_settings=? WHERE observation_id=last_insert_rowid()',(sample_rate,step,effective_seconds,margin,json.dumps(evidence)))
            from .fingerprints import spectral_fingerprint,VERSION
            fingerprint=spectral_fingerprint(row,peak)
            if fingerprint is not None:
                fingerprint['gain_db']=None if gain in (None,'auto') else float(gain)
                db.execute('INSERT INTO observation_fingerprints VALUES(?,?,?,?)',(observation_id,signal,VERSION,json.dumps(fingerprint,allow_nan=False)))
            snrs=[x[0] for x in db.execute('SELECT peak_snr_db FROM spectral_observations WHERE signal_id=?',(signal,))]
            db.execute('UPDATE signals SET last_seen=CASE WHEN '+sql_utc('last_seen')+' IS NULL OR julianday(?)>=julianday(last_seen) THEN ? ELSE last_seen END, observation_count=?, peak_snr_db=?, median_snr_db=? WHERE signal_id=?',(observed,observed,len(snrs),max(snrs),float(np.median(snrs)),signal))
            if peak['dc']:
                db.execute('INSERT INTO artifacts(signal_id,kind,confidence,reason) VALUES(?,?,?,?)',(signal,'POSSIBLE_DC',.3,'Peak intersects output row midpoint; tuner-center verification requires offset revisit'))


def survey(db,args):
    from .survey import survey as scan
    backend = RTLBackend(CONFIG)
    backend.ingest = ingest
    return scan(db,args,ROOT,backend)


def export(db):
    db.row_factory=sqlite3.Row
    records=[dict(r) for r in db.execute('SELECT * FROM signals ORDER BY center_frequency_hz')]
    path=ROOT/'exports'/'signals.json'; temp=path.with_suffix('.tmp');temp.write_text(json.dumps(records,indent=2),encoding='utf8');os.replace(temp,path)
    path=ROOT/'exports'/'signals.csv';temp=path.with_suffix('.tmp')
    with temp.open('w',newline='',encoding='utf8') as file:
        fields=[r[1] for r in db.execute('PRAGMA table_info(signals)')]
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(records)
    os.replace(temp,path)
    return records


def main():
    global CONFIG, ROOT
    if '--version' in sys.argv[1:]:
        from . import __version__
        print(__version__);return 0
    config_parser=argparse.ArgumentParser(add_help=False)
    config_parser.add_argument('--config')
    preliminary,remaining=config_parser.parse_known_args()
    try:
        CONFIG=load_config(preliminary.config);ROOT=CONFIG.root
    except (ValueError,OSError) as error:
        if 'doctor' in remaining:
            print(json.dumps(dict(schema=1,summary='FAIL',configuration=dict(status='FAIL',error_class=type(error).__name__,remedy='Review the selected private TOML configuration; no paths or values exported.'))));return 1
        print(f'Configuration failed: {error}',file=sys.stderr);return 1
    for folder in ['data/spectra','logs','exports']: (ROOT/folder).mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=ROOT/'logs'/'rf-atlas.log',level=logging.INFO,encoding='utf8',format='%(asctime)s %(levelname)s %(message)s')
    parser=argparse.ArgumentParser(description=__doc__,parents=[config_parser]);sub=parser.add_subparsers(dest='cmd',required=True)
    doctor_parser=sub.add_parser('doctor',help='Sanitized diagnostics with finite real sample delivery test')
    doctor_parser.add_argument('--skip-receiver',action='store_true')
    sub.add_parser('list');sub.add_parser('export')
    ai_train=sub.add_parser('ai-train',help='Train an original bounded synthetic CPU model locally')
    ai_train.add_argument('--max-seconds',type=float,default=300)
    ai_infer=sub.add_parser('ai-infer',help='Optional RF model inference on managed recording IQ')
    ai_infer.add_argument('recording',type=Path);ai_infer.add_argument('--metadata',type=Path)
    demod=sub.add_parser('demodulate',help='Diagnostic bit candidates; never protocol verification')
    demod.add_argument('recording',type=Path);demod.add_argument('--metadata',type=Path)
    demod.add_argument('--mode',choices=['OOK','2FSK','4FSK','BPSK','QPSK'],required=True)
    demod.add_argument('--baud',type=float,required=True);demod.add_argument('--offset-hz',type=float,default=0)
    analytics_parser=sub.add_parser('analytics',help='Optional detached local analytics; SQLite remains operational')
    analytics_sub=analytics_parser.add_subparsers(dest='analytics_action',required=True)
    snapshot_parser=analytics_sub.add_parser('snapshot');snapshot_parser.add_argument('destination',type=Path)
    build_parser=analytics_sub.add_parser('build');build_parser.add_argument('snapshot',type=Path);build_parser.add_argument('destination',type=Path)
    query_parser=analytics_sub.add_parser('query');query_parser.add_argument('database',type=Path)
    query_parser.add_argument('--start',type=float,required=True,help='Inclusive UTC Unix seconds');query_parser.add_argument('--end',type=float,required=True,help='Exclusive UTC Unix seconds')
    query_parser.add_argument('--bucket-seconds',type=int,default=3600);query_parser.add_argument('--limit',type=int,default=500)
    parquet_parser=analytics_sub.add_parser('parquet');parquet_parser.add_argument('database',type=Path);parquet_parser.add_argument('destination',type=Path)
    fcc_parser=sub.add_parser('fcc-lookup',help='Optional offline normalized licensing assignments; no identity inference')
    fcc_parser.add_argument('database',type=Path);fcc_parser.add_argument('frequency',type=frequency)
    fcc_parser.add_argument('--tolerance-hz',type=float,default=0);fcc_parser.add_argument('--include-location',action='store_true')
    decoder_parser=sub.add_parser('decoders',help='List protocol capabilities and availability')
    decoder_parser.add_argument('--rtl433')
    identify_parser=sub.add_parser('identify',help='Offline bounded protocol identification')
    identify_parser.add_argument('recording',type=Path)
    identify_parser.add_argument('--metadata',type=Path)
    identify_parser.add_argument('--rtl433',help='Optional externally installed rtl_433 executable')
    identify_parser.add_argument('--store',action='store_true')
    identify_parser.add_argument('--retain-frames',action='store_true')
    identify_parser.add_argument('--retain-identifiers',action='store_true')
    identify_parser.add_argument('--signal-id',help='Explicit catalog association for stored offline history')
    identify_parser.add_argument('--max-samples',type=int,default=4000000)
    identify_parser.add_argument('--channel-bandwidth',type=float)
    reanalyze_parser=sub.add_parser('reanalyze',help='Offline reidentify catalog captures after decoder updates')
    reanalyze_parser.add_argument('--signal-id')
    reanalyze_parser.add_argument('--rtl433')
    reanalyze_parser.add_argument('--limit',type=int,default=100)
    reanalyze_parser.add_argument('--max-seconds',type=float,default=600)
    reanalyze_parser.add_argument('--force',action='store_true')
    reanalyze_parser.add_argument('--backend',choices=['dsp','ml','hybrid','rfai'],default='dsp')
    reanalyze_parser.add_argument('--model',type=Path)
    serve_parser=sub.add_parser('serve',help='Localhost catalog dashboard')
    serve_parser.add_argument('--port',type=int,default=8765)
    app_parser=sub.add_parser('app',help='Unified local receiver application')
    app_parser.add_argument('--port',type=int,default=8765)
    app_parser.add_argument('--open-browser',action='store_true')
    inventory_init=sub.add_parser('inventory-init',help='Write editable inventory profiles and prepare region checkpoints')
    inventory_init.add_argument('--profiles',type=Path)
    inventory_init.add_argument('--profile',action='append')
    inventory_status=sub.add_parser('inventory-status',help='Coverage stages and investigation queue')
    inventory_status.add_argument('--profiles',type=Path)
    inventory_run=sub.add_parser('inventory-run',help='Bounded resumable survey/investigation worker')
    inventory_run.add_argument('--profiles',type=Path)
    inventory_run.add_argument('--profile',action='append')
    inventory_run.add_argument('--max-steps',type=int,default=1)
    inventory_run.add_argument('--max-seconds',type=float,default=120)
    inventory_run.add_argument('--survey-only',action='store_true')
    inventory_run.add_argument('--smart',action='store_true')
    inventory_run.add_argument('--rtl433')
    inventory_cancel=sub.add_parser('investigation-cancel')
    inventory_cancel.add_argument('job_id')
    report_parser=sub.add_parser('discovery-report',help='Private machine-readable discovery performance report')
    report_parser.add_argument('--since',required=True)
    report_parser.add_argument('--until',required=True)
    report_parser.add_argument('--profile',default='selected window')
    report_parser.add_argument('--output',required=True,type=Path)
    analog_parser=sub.add_parser('analog',help='Bounded offline analog hypothesis diagnostics; no audio output')
    analog_parser.add_argument('recording',type=Path)
    analog_parser.add_argument('--metadata',type=Path)
    analog_parser.add_argument('--mode',choices=['AM','NFM','WFM','CW'],required=True)
    capture=sub.add_parser('record',help='Finite receive-only IQ recording')
    capture.add_argument('--frequency',required=True,type=frequency)
    capture.add_argument('--sample-rate',type=int,default=250000)
    capture.add_argument('--duration',type=float,default=10)
    capture.add_argument('--gain',type=gain_value,default='15.7')
    capture.add_argument('--offset',type=int,default=25000)
    capture.add_argument('--antenna',default='other')
    capture.add_argument('--signal-id')
    replay_parser=sub.add_parser('replay',help='Offline IQ features and heuristic modulation; no receiver access')
    replay_parser.add_argument('recording',help='cu8 path or local capture UUID')
    replay_parser.add_argument('--metadata',type=Path)
    replay_parser.add_argument('--channel-bandwidth',type=float,help='Optional channel activity bandwidth in Hz around the desired frequency; guards at 1 to 1.5 times this width')
    replay_parser.add_argument('--store',action='store_true',help='Append heuristic history for a capture associated with a signal')
    replay_parser.add_argument('--backend',choices=['dsp','baseline','ml','hybrid'],default='dsp')
    replay_parser.add_argument('--model',type=Path,help='Optional locally trained NPZ model; no weights are downloaded')
    sigmf_export=sub.add_parser('sigmf-export',help='Copy verified CU8 into a bounded SigMF pair')
    sigmf_export.add_argument('recording',type=Path);sigmf_export.add_argument('destination',type=Path)
    sigmf_export.add_argument('--metadata',type=Path)
    sigmf_import=sub.add_parser('sigmf-import',help='Import a supported single-capture CU8 SigMF pair')
    sigmf_import.add_argument('metadata',type=Path);sigmf_import.add_argument('destination',type=Path)
    sigmf_import.add_argument('--frequency',type=frequency,help='Explicit desired analysis frequency within capture')
    show=sub.add_parser('show');show.add_argument('signal_id')
    scan=sub.add_parser('survey');scan.add_argument('--start',type=frequency,default=CONFIG.survey_start_hz);scan.add_argument('--stop',type=frequency,default=CONFIG.survey_stop_hz);scan.add_argument('--profile',choices=PROFILES,default='balanced');scan.add_argument('--antenna',default='other');scan.add_argument('--new',action='store_true',help='Start a fresh run rather than resume an interrupted matching run')
    scan.add_argument('--gain',default='15.7',type=gain_value,help='Manual tuner gain in dB (default 15.7); auto is diagnostic only')
    scan.add_argument('--integration',type=int,choices=range(1,61),metavar='1..60',help='Override integration seconds')
    scan.add_argument('--duration',type=positive_int,help='Observe each region for this many seconds with repeated spectra')
    scan.add_argument('--bin-hz',type=positive_int,help='Override maximum bin width')
    scan.add_argument('--margin',type=float,choices=[3,6,9,12],default=6,help='Minimum adaptive threshold margin in dB')
    scan.add_argument('--diagnostic-target',type=frequency,help='Generate diagnostic plots/metrics with a validation marker; never a classification hint')
    args=parser.parse_args(None if remaining else ['app']);db=database()
    try:
        if args.cmd=='ai-train':
            from .training import train
            print(json.dumps(train(ROOT/'runtime/models/ai-model.json',args.max_seconds),indent=2))
        elif args.cmd in ('ai-infer','demodulate'):
            from .iq import read_recording,analyze_samples
            samples,meta=read_recording(args.recording,args.metadata,CONFIG.max_capture_bytes)
            if args.cmd=='ai-infer':
                from .ai import infer
                result=infer(analyze_samples(samples,meta),meta,ROOT/'runtime/models/ai-model.json')
            else:
                from .demodulation import demodulate
                result=demodulate(samples[:4000000],meta['sample_rate'],args.mode,args.baud,frequency_offset_hz=args.offset_hz)
            print(json.dumps(result,indent=2,allow_nan=False))
        elif args.cmd=='discovery-report':
            from .performance import discovery_report
            result=discovery_report(ROOT,args.since,args.until,profile=args.profile)
            with args.output.open('x',encoding='utf-8') as destination:
                json.dump(result,destination,indent=2,allow_nan=False)
            print('Private discovery report saved')
        elif args.cmd=='analytics':
            from .analytics import snapshot_catalog,build_analytics,query_analytics,export_parquet
            if args.analytics_action=='snapshot':result=snapshot_catalog(ROOT/'data/rf_atlas.sqlite',args.destination)
            elif args.analytics_action=='build':result=build_analytics(args.snapshot,args.destination)
            elif args.analytics_action=='query':result=query_analytics(args.database,args.start,args.end,bucket_s=args.bucket_seconds,limit=args.limit)
            else:result=export_parquet(args.database,args.destination)
            print(json.dumps(result,indent=2,allow_nan=False))
        elif args.cmd=='fcc-lookup':
            from .fcc import LocalFCCProvider
            print(json.dumps(LocalFCCProvider(args.database).lookup(args.frequency,args.tolerance_hz,include_location=args.include_location),indent=2,allow_nan=False))
        elif args.cmd in ('inventory-init','inventory-status','inventory-run','investigation-cancel'):
            from .inventory import (load_profiles,write_profiles,configure_regions,inventory_view,queue_view,InventoryWorker,cancel_job)
            from .platforms import DeviceLock as SchedulerLock
            if args.cmd=='investigation-cancel':
                print(json.dumps({'cancelled':cancel_job(db,args.job_id)}))
            else:
                profile_path=args.profiles or ROOT/'runtime/inventory.toml'
                if args.cmd=='inventory-init':
                    if not profile_path.exists():write_profiles(profile_path)
                    profiles=load_profiles(profile_path)
                    with SchedulerLock(CONFIG.lock_root,'inventory-scheduler'):
                        configure_regions(db,profiles,args.profile)
                    print(json.dumps({'profiles':str(profile_path),'inventory':inventory_view(db,profiles)},indent=2))
                elif args.cmd=='inventory-status':
                    profiles=load_profiles(profile_path if profile_path.is_file() else None)
                    print(json.dumps({'inventory':inventory_view(db,profiles),'investigations':queue_view(db)},indent=2))
                else:
                    from .iq import record
                    from .identification import identify_recording,default_registry,store_identification
                    profiles=load_profiles(profile_path if profile_path.is_file() else None)
                    def scan_region(region,profile):
                        options=SimpleNamespace(start=region['start_hz'],stop=region['stop_hz'],profile='quick',antenna='other',new=False,
                            integration=profile['integration_s'],duration=profile['duration_s'],gain=profile['gain'],margin=profile['margin_db'],
                            bin_hz=profile['fine_bin_hz'] if region['stage']=='fine' else profile['bin_hz'],diagnostic_target=None)
                        return survey(db,options)
                    def capture_job(job,settings):
                        freq=db.execute('SELECT center_frequency_hz FROM signals WHERE signal_id=?',(job['signal_id'],)).fetchone()[0]
                        from .adaptive_capture import acquire
                        class Cancellation:
                            def is_set(self):return db.execute('SELECT status FROM investigation_jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0]=='cancel_requested'
                        samples,meta=acquire(CONFIG,RTLBackend(CONFIG),int(round(freq)),settings['sample_rate'],settings['gain'],
                            'medium',window_s=settings['duration'],cancel_event=Cancellation())
                        return samples,dict(meta,signal_id=job['signal_id'])
                    def analyze_job(path,meta,job):
                        previous=db.execute('SELECT protocol FROM signals WHERE signal_id=?',(job['signal_id'],)).fetchone()[0]
                        from .identification import identify_samples
                        class Cancellation:
                            def is_set(self):return db.execute('SELECT status FROM investigation_jobs WHERE job_id=?',(job['job_id'],)).fetchone()[0]=='cancel_requested'
                        result=identify_samples(path,meta,registry=default_registry(args.rtl433,config=CONFIG),previous_protocol=previous,cancel_event=Cancellation())
                        result['acquisition']=meta['acquisition']
                        from .runtime import load_settings
                        settings=load_settings(ROOT)
                        return store_identification(db,result,meta,'transient:'+meta['sha256'],retain_frames=settings['retain_frames'],retain_identifiers=settings['retain_identifiers'])
                    worker=InventoryWorker(db,CONFIG,scan_region,capture_job,analyze_job)
                    print(json.dumps(worker.run(args.max_steps,args.max_seconds,not args.survey_only,profiles,args.profile,smart=args.smart),indent=2))
        elif args.cmd=='analog':
            from .iq import read_recording
            from .analog import analyze_analog
            samples,meta=read_recording(args.recording,args.metadata,CONFIG.max_capture_bytes)
            print(json.dumps(analyze_analog(samples[:1000000],meta['sample_rate'],args.mode),indent=2,allow_nan=False))
        elif args.cmd=='decoders':
            from .identification import default_registry
            print(json.dumps(default_registry(args.rtl433,config=CONFIG).describe(),indent=2))
        elif args.cmd=='identify':
            from .identification import default_registry,identify_recording,SelectionPolicy,store_identification
            result,meta=identify_recording(args.recording,args.metadata,default_registry(args.rtl433,config=CONFIG),SelectionPolicy(max_samples=args.max_samples),channel_bandwidth_hz=args.channel_bandwidth,max_bytes=CONFIG.max_capture_bytes)
            if args.signal_id: meta=dict(meta,signal_id=args.signal_id)
            if args.store: store_identification(db,result,meta,args.recording,retain_frames=args.retain_frames,retain_identifiers=args.retain_identifiers)
            from .entities import private_result
            result=private_result(result,retain_frames=args.retain_frames,retain_identifiers=args.retain_identifiers)
            print(json.dumps(result,indent=2,allow_nan=False))
        elif args.cmd=='reanalyze':
            from .reanalysis import ReanalysisController
            from .identification import default_registry
            controller=ReanalysisController(CONFIG,registry=default_registry(args.rtl433,config=CONFIG))
            controller.start(dict(signal_id=args.signal_id,unknown_only=not bool(args.signal_id),limit=args.limit,max_seconds=args.max_seconds,force=args.force,backend=args.backend),model_path=args.model)
            try:
                while controller.status()['active']:time.sleep(.25)
            except KeyboardInterrupt:
                controller.cancel()
                while controller.status()['active']:time.sleep(.25)
            print(json.dumps(controller.results(),indent=2))
        elif args.cmd in ('serve','app'):
            from .dashboard import create_dashboard_server
            from .identification import signal_history
            from .runtime import RuntimeSupervisor
            from .investigation import prepare_plots
            from .inventory import inventory_view,queue_view,load_profiles
            artifacts,signal_plots=prepare_plots(db,ROOT)
            def investigate(signal_id):
                connection=database()
                try:
                    result=signal_history(connection,signal_id)
                    result['artifacts']=signal_plots.get(signal_id,[])
                    return result
                finally: connection.close()
            def inventory_callback():
                connection=database()
                try:
                    profile_path=ROOT/'runtime/inventory.toml'
                    return inventory_view(connection,load_profiles(profile_path if profile_path.is_file() else None))
                finally:connection.close()
            def queue_callback():
                connection=database()
                try:return queue_view(connection)
                finally:connection.close()
            controller=RuntimeSupervisor(CONFIG,preliminary.config)
            from .recording_actions import recording_action
            server=create_dashboard_server(ROOT,port=args.port,controller=controller,investigate=investigate,artifacts=artifacts,inventory_view=inventory_callback,queue_view=queue_callback,
                recording_action=lambda capture_id,action:recording_action(ROOT,capture_id,action),runtime=controller)
            print(f'Dashboard: http://127.0.0.1:{server.server_port}',flush=True)
            controller.startup()
            if getattr(args,'open_browser',False):
                import webbrowser
                webbrowser.open(f'http://127.0.0.1:{server.server_port}')
            try: server.serve_forever()
            finally: controller.close();server.server_close()
        elif args.cmd=='doctor':
            from .doctor import diagnose
            result=diagnose(CONFIG,receiver=not args.skip_receiver)
            print(json.dumps(result,indent=2,allow_nan=False))
            if result.get('summary')=='FAIL':return 1
        elif args.cmd=='survey': survey(db,args);export(db)
        elif args.cmd=='record':
            from .iq import record
            path,meta=record(CONFIG,RTLBackend(CONFIG),args.frequency,args.sample_rate,args.duration,args.gain,args.offset,args.antenna,db,args.signal_id)
            print(json.dumps({'path':str(path),'metadata':meta},indent=2))
        elif args.cmd=='replay':
            from .iq import replay,save_hypothesis
            candidate=Path(args.recording)
            if not candidate.is_file():
                try: capture_id=str(uuid.UUID(args.recording))
                except ValueError: raise ValueError('Recording not found; supply a cu8 path or capture UUID')
                candidate=ROOT/'data/captures'/f'{capture_id}.cu8'
            result,meta=replay(candidate,args.metadata,CONFIG.max_capture_bytes,args.channel_bandwidth,args.backend,args.model)
            if args.store: save_hypothesis(db,result,meta)
            print(json.dumps(result,indent=2,allow_nan=False))
        elif args.cmd=='sigmf-export':
            from .sigmf import export_sigmf
            paths=export_sigmf(args.recording,args.destination,args.metadata,CONFIG.max_capture_bytes)
            print(json.dumps({'data':str(paths[0]),'metadata':str(paths[1])}))
        elif args.cmd=='sigmf-import':
            from .sigmf import import_sigmf
            path,meta=import_sigmf(args.metadata,args.destination,args.frequency,CONFIG.max_capture_bytes)
            print(json.dumps({'path':str(path),'metadata':meta}))
        elif args.cmd in ('list','export'): print(json.dumps(export(db),indent=2))
        elif args.cmd=='show':
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM signals WHERE signal_id=?',(args.signal_id,)).fetchone()
            if row is None: raise ValueError('Unknown signal ID')
            print(json.dumps(dict(row),indent=2))
    except KeyboardInterrupt: print('Interrupted; repeat the same survey command to resume.',file=sys.stderr);return 130
    except Exception as error: logging.exception('stage %s failed',args.cmd);print(f'{args.cmd} failed: {error}',file=sys.stderr);return 1
    finally: db.close()
    return 0


if __name__=='__main__': sys.exit(main())
