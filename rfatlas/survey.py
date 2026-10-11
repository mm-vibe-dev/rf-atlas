"""Checkpointed scanner; backend owns receiver operations."""
import json,os,uuid,time,subprocess
from datetime import datetime,timezone

def now(): return datetime.now(timezone.utc).isoformat()

PROFILES = {'quick': (25000, 5), 'balanced': (5000, 10), 'deep': (1000, 20)}

def survey(db,args,root,backend):
    if args.stop <= args.start or args.start < 500000 or args.stop > 1766000000:
        raise ValueError('Require 500k <= start < stop <= 1766M')
    bin_hz, seconds = PROFILES[args.profile]
    seconds = args.integration if args.integration is not None else seconds
    bin_hz=getattr(args,'bin_hz',None) or bin_hz
    duration=getattr(args,'duration',None)
    margin=getattr(args,'margin',6)
    settings = json.dumps({'bin_hz':bin_hz,'seconds':seconds,'duration':duration,'margin':margin,'crop':.2,'gain':args.gain,'bias_tee':False,'device':backend.selector,'hf_backend':'FLOAT_WELCH' if args.stop<=30000000 else None},sort_keys=True)
    db.execute('INSERT OR IGNORE INTO antenna_profiles VALUES(?,?)',(args.antenna,'User-named; reception coverage is not calibrated'))
    prior = None if args.new else db.execute("SELECT run_id FROM survey_runs WHERE start_hz=? AND stop_hz=? AND profile=? AND antenna=? AND settings=? AND status != 'complete' ORDER BY started DESC LIMIT 1",(args.start,args.stop,args.profile,args.antenna,settings)).fetchone()
    run = prior[0] if prior else str(uuid.uuid4())
    if not prior:
        db.execute('INSERT INTO survey_runs VALUES(?,?,NULL,?,?,?,?,?,?)',(run,now(),'pending',args.start,args.stop,args.profile,args.antenna,settings))
        for low in range(args.start,args.stop,2000000):
            db.execute('INSERT INTO scan_regions VALUES(?,?,?,?,NULL,NULL)',(run,low,min(low+2000000,args.stop),'pending'))
    db.commit()
    try:
        with backend.lock():
            db.execute("UPDATE survey_runs SET status='running' WHERE run_id=?",(run,));db.commit()
            for low,high in db.execute("SELECT start_hz,stop_hz FROM scan_regions WHERE run_id=? AND status!='complete' ORDER BY start_hz",(run,)).fetchall():
                path=root/'data'/'spectra'/f'{run}-{low}-{uuid.uuid4().hex[:8]}.csv'; temp=path.with_suffix('.partial.csv')
                print(f'Survey {low/1e6:.4f}-{high/1e6:.4f} MHz ({args.profile})',flush=True)
                temp.unlink(missing_ok=True)
                cmd = [backend.tool('rtl_power'),'-d',backend.selector,'-f',f'{max(500000,low-bin_hz*2)}:{min(1766000000,high+bin_hz*2)}:{bin_hz}','-i',str(seconds),'-1','-c','20%']
                if duration:
                    cmd.remove('-1');cmd += ['-e',str(duration)]
                if args.gain != 'auto': cmd += ['-g',args.gain]
                acquisition_start=time.perf_counter();acquisition_s=None;processing_start=None;valid=False
                try:
                    if high<=30000000:
                        from .hf_spectrum import acquire
                        acquire(backend,temp,low,high,bin_hz,duration or seconds,args.gain)
                        temp.with_suffix('.command.json').replace(path.with_suffix('.command.json'))
                    else:
                        deadline=(duration or seconds)+15
                        try:
                            backend.run(cmd+[temp],deadline,path.with_suffix('.command.json'))
                        except subprocess.TimeoutExpired:
                            # Keep the failed command and any partial rows distinct
                            # from the independent, finite floating-point retry.
                            failed=path.with_suffix('.timeout.command.json')
                            if path.with_suffix('.command.json').exists():path.with_suffix('.command.json').replace(failed)
                            partial=path.with_suffix('.timeout.csv')
                            if temp.exists():temp.replace(partial)
                            from .hf_spectrum import acquire
                            from .diagnostics import atomic_json
                            acquire(backend,temp,low,high,bin_hz,duration or seconds,args.gain)
                            metadata=json.loads(temp.with_suffix('.command.json').read_text(encoding='utf8'))
                            metadata['fallback']=dict(reason='RTL_POWER_TIMEOUT',failed_command=str(failed) if failed.exists() else None,
                                partial_spectrum=str(partial) if partial.exists() else None,deadline_s=deadline)
                            atomic_json(path.with_suffix('.command.json'),metadata)
                            temp.with_suffix('.command.json').unlink()
                        from .diagnostics import read_spectra,atomic_json
                        _,diagnostic=read_spectra(temp)
                        if any(row.get('bins') for row in diagnostic['rejected_rows']):
                            # Preserve the failed acquisition before an independent
                            # floating FFT retry; never fill or interpolate bad bins.
                            rejected=path.with_suffix('.rejected.csv')
                            temp.replace(rejected)
                            path.with_suffix('.command.json').replace(rejected.with_suffix('.command.json'))
                            diagnostic['source_partial_path']=diagnostic['path'];diagnostic['path']=str(rejected)
                            atomic_json(rejected.with_suffix('.parse.json'),diagnostic)
                            from .hf_spectrum import acquire
                            acquire(backend,temp,low,high,bin_hz,duration or seconds,args.gain)
                            metadata=json.loads(temp.with_suffix('.command.json').read_text(encoding='utf8'))
                            metadata['fallback']=dict(reason='NONFINITE_OR_MALFORMED_POWER',rejected_spectrum=str(rejected),rejected_diagnostic=str(rejected.with_suffix('.parse.json')))
                            atomic_json(path.with_suffix('.command.json'),metadata)
                            temp.with_suffix('.command.json').unlink()
                    acquisition_s=time.perf_counter()-acquisition_start
                    processing_start=time.perf_counter()
                    os.replace(temp,path)
                    with db:
                        backend.ingest(db,run,path,low,high,margin)
                        db.execute("UPDATE scan_regions SET status='complete',completed=?,raw_path=? WHERE run_id=? AND start_hz=?",(now(),str(path),run,low))
                    valid=True
                finally:
                    acquired=acquisition_s if acquisition_s is not None else time.perf_counter()-acquisition_start
                    processed=time.perf_counter()-processing_start if processing_start is not None else 0
                    with db:db.execute('INSERT OR REPLACE INTO acquisition_timings VALUES(?,?,?,?,?,?,?)',
                        (run,low,now(),acquired,processed,duration or seconds,int(valid)))
                if getattr(args,'diagnostic_target',None) is not None:
                    from .diagnostics import survey_diagnostics
                    survey_diagnostics(path,args.diagnostic_target,root/'reports/diagnostics',margin)
            db.execute("UPDATE survey_runs SET status='complete',ended=? WHERE run_id=?",(now(),run));db.commit()
    except BaseException:
        db.rollback();db.execute("UPDATE survey_runs SET status='interrupted' WHERE run_id=?",(run,));db.commit();raise
    print('Completed run',run)
    return run
