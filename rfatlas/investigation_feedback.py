"""Conservative operational feedback from audited frame evidence, never labels."""
import json


def history(db):
    stats={};runs=0
    for payload, in db.execute('SELECT result FROM identification_runs ORDER BY created DESC LIMIT 1000'):
        result=json.loads(payload);runs+=1
        for attempt in result.get('attempts',[])[:16]:
            name=attempt.get('decoder',{}).get('name')
            if not name:continue
            s=stats.setdefault(name,dict(attempts=0,verified_runs=0,failed_runs=0,elapsed_ms=0))
            s['attempts']+=1
            verified=bool(attempt.get('protocol_verified') and attempt.get('evidence',{}).get('frames_with_sync_and_valid_checksum',0)>0)
            s['verified_runs']+=int(verified);s['failed_runs']+=int(not verified)
            value=attempt.get('elapsed_ms',0)
            if type(value) in (int,float) and 0<=value<=120000:s['elapsed_ms']+=value
    return dict(version='feedback-1',history_limit=1000,observed_runs=runs,decoders=stats,
        limitations='Verified run counts are operational outcomes, not independent devices or classification accuracy. Only joint frame integrity evidence contributes to routing bonus.')
