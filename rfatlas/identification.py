"""Bounded, evidence-driven protocol selection; modulation is only a hint."""
import hashlib
import json
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .decoders import DecoderInput, run_decoder
from .protocols import ModeSDecoder

ENGINE_VERSION = '1.9.0rc1'


class DecoderRegistry:
    def __init__(self):
        self.decoders = {}
        self.feedback = {}
        self.ai_model = None

    def register(self, decoder):
        if not isinstance(decoder.name,str) or not decoder.name or decoder.name in self.decoders:
            raise ValueError('Duplicate or empty decoder name')
        if not isinstance(decoder.version,str) or not decoder.version:
            raise ValueError('Decoder version is required')
        required = ('protocols', 'modulations', 'platforms', 'offline_replay', 'evidence')
        if not all(k in decoder.capabilities for k in required):
            raise ValueError('Incomplete decoder capabilities')
        self.decoders[decoder.name] = decoder

    def describe(self):
        from .deep_investigation import capability
        return [dict(name=d.name, version=d.version, available=self.available(d), investigation_contract=capability(d), **d.capabilities)
                for d in self.decoders.values()]

    @staticmethod
    def available(decoder):
        try:
            return platform.system() in decoder.capabilities['platforms'] and (
                not hasattr(decoder, 'available') or bool(decoder.available()))
        except Exception:
            return False


def default_registry(rtl433=None, dsd_fme=None, config=None):
    from .external_decoders import RTL433Decoder,RTL433WH5Decoder,RTL433WH24Decoder,RTL433WH1050Decoder
    import os
    optional=config.decoders if config is not None else {}
    rtl433=rtl433 or optional.get('rtl_433') or os.environ.get('RFATLAS_RTL433')
    dsd_fme=dsd_fme or optional.get('dsd_fme')
    registry = DecoderRegistry()
    if config is not None:
        from .ai import enabled_model
        registry.ai_model = enabled_model(config.root)
    registry.register(ModeSDecoder())
    from .dmr import DMRDecoder
    registry.register(DMRDecoder())
    from .pocsag import POCSAGDecoder
    registry.register(POCSAGDecoder())
    from .ais import AISDecoder
    registry.register(AISDecoder())
    import os
    from .dsd import DSDFMEDiagnostic
    registry.register(DSDFMEDiagnostic(dsd_fme or os.environ.get('RFATLAS_DSD_FME','dsd-fme')))
    registry.register(RTL433Decoder(rtl433 or 'rtl_433'))
    registry.register(RTL433WH5Decoder(rtl433 or 'rtl_433'))
    registry.register(RTL433WH24Decoder(rtl433 or 'rtl_433'))
    registry.register(RTL433WH1050Decoder(rtl433 or 'rtl_433'))
    from .packet import AX25AFSK1200Decoder
    registry.register(AX25AFSK1200Decoder())
    from .rds import RDSDecoder
    registry.register(RDSDecoder())
    if config is not None and (config.root/'data/rf_atlas.sqlite').is_file():
        import sqlite3
        from contextlib import closing
        from .investigation_feedback import history
        with closing(sqlite3.connect((config.root/'data/rf_atlas.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            registry.feedback=history(db)['decoders']
    return registry


@dataclass(frozen=True)
class SelectionPolicy:
    max_attempts: int = 4
    max_samples: int = 4_000_000
    max_seconds: float = 30

    def __post_init__(self):
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 16:
            raise ValueError('Attempt budget must be 1..16')
        if type(self.max_samples) is not int or not 4096 <= self.max_samples <= 32*1024*1024:
            raise ValueError('Invalid sample budget')
        if type(self.max_seconds) not in (int,float) or not __import__('math').isfinite(self.max_seconds) or not 0 < self.max_seconds <= 120:
            raise ValueError('Invalid decoder time budget')


def rank_candidates(candidates):
    """Keep a bounded attempt budget from being consumed by related adapters."""
    remaining=list(candidates);output=[];used={}
    while remaining:
        best=min(remaining,key=lambda c:(-c['eligible'],-(c['score']-2*used.get(c.get('routing_group',c['decoder']),0)),c['decoder']))
        group=best.get('routing_group',best['decoder']);best['routing_score']=best['score']-2*used.get(group,0)
        hint='bounded related-adapter diversity penalty; eligibility preserved'
        if used.get(group,0) and hint not in best['hints']:best['hints'].append(hint)
        used[group]=used.get(group,0)+1;remaining.remove(best);output.append(best)
    return output


def select_candidates(registry, recording, analysis, previous_protocol=None):
    candidates = []
    bandwidth = analysis.get('features', {}).get('occupied_bandwidth_hz')
    activity = analysis.get('activity', {})
    for decoder in registry.decoders.values():
        cap = decoder.capabilities
        reasons = []
        for value, prefix in ((recording.frequency_hz, 'frequency'), (recording.sample_rate, 'sample_rate')):
            if value < cap.get(prefix+'_min_hz', 0) or value > cap.get(prefix+'_max_hz', float('inf')):
                reasons.append(prefix+' outside supported range')
        minimum=cap.get('minimum_duration_s',0)
        if len(recording.iq)/recording.sample_rate<minimum:reasons.append('insufficient data for minimum duration')
        if not registry.available(decoder):
            reasons.append('decoder unavailable on this platform')
        if not cap['offline_replay']:
            reasons.append('offline replay unsupported')
        score = -10 if cap.get('diagnostic_only') else 0
        hints = []
        hint=cap.get('frequency_hint_hz')
        if hint and hint[0]<=recording.frequency_hz<=hint[1]:
            score+=1;hints.append('band context only; not verification')
        elif hint:
            score-=1;hints.append('outside typical band context; remains eligible')
        if analysis.get('modulation') in cap['modulations']:
            score += 4; hints.append('modulation agreement')
            successes=registry.feedback.get(decoder.name,{}).get('verified_runs',0)
            if successes>=3:
                score+=1;hints.append('bounded historical joint-frame verification bonus; not protocol evidence')
        elif any(c.get('modulation') in cap['modulations'] and c.get('eligible',True)
                 and c.get('support',c.get('score',0))>=.25 for c in analysis.get('candidates',[])):
            score += 2; hints.append('compatible alternative modulation hypothesis; not verification')
        elif cap.get('unmatched_priority_penalty'):
            score -= cap['unmatched_priority_penalty']
            hints.append('strict subset fallback deprioritized without compatible modulation evidence')
        if previous_protocol in cap['protocols']:
            score += 4; hints.append('previously verified protocol')
        expected = cap.get('expected_bandwidth_hz')
        if bandwidth and expected and .1*expected <= bandwidth <= 2*expected:
            score += 1; hints.append('bandwidth compatible')
        if activity.get('events'):
            score += 1; hints.append('activity boundaries available')
        candidates.append({'decoder': decoder.name, 'protocols': cap['protocols'], 'eligible': not reasons,
                           'routing_group':cap.get('routing_group',decoder.name),
                           'score': score, 'reasons': reasons, 'hints': hints,
                           'activity_behavior': activity.get('behavior', 'UNKNOWN')})
    return rank_candidates(candidates)


def protection_status(metadata=None, *, protocol_verified=False):
    """Require explicit, documented validated protocol fields, never RF appearance."""
    if not metadata or not metadata.get('field') or not metadata.get('reference'):
        return {'status': 'UNKNOWN', 'evidence': []}
    indicator = metadata.get('indicator')
    if type(indicator) is not bool:
        return {'status': 'UNKNOWN', 'evidence': []}
    status = 'INDICATED' if indicator else 'UNKNOWN'
    if protocol_verified and metadata.get('field_validated') is True:
        status = 'VERIFIED_BY_PROTOCOL_METADATA' if indicator else 'NOT_INDICATED'
    return {'status': status, 'evidence': [metadata],
            'limitation': 'Describes the protocol field; does not prove encryption of every payload.'}


def identify(recording, analysis, registry=None, policy=None, previous_protocol=None, cancel_event=None,recipe='generic'):
    registry = registry or default_registry()
    policy = policy or SelectionPolicy()
    # Analyze a deterministic bounded prefix; whole recording digest stays separate.
    bounded = DecoderInput(recording.iq[:policy.max_samples], recording.sample_rate,
                           recording.frequency_hz, recording.start_sample, recording.source_sha256)
    candidates = select_candidates(registry, bounded, analysis, previous_protocol)
    from .deep_investigation import plan
    investigation_plan=plan(analysis,recipe)
    for candidate in candidates:
        if any(p in investigation_plan['decoder_protocol_preference'] for p in candidate['protocols']):
            candidate['score']+=2;candidate['hints'].append('compatible recipe preference; not protocol proof')
    candidates=rank_candidates(candidates)
    attempts = []
    verified = []
    started=time.monotonic()
    for candidate in candidates:
        if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Reanalysis cancelled')
        if not candidate['eligible'] or len(attempts) >= policy.max_attempts:
            continue
        if time.monotonic()-started>=policy.max_seconds:break
        decoder = registry.decoders[candidate['decoder']]
        attempt_started=time.monotonic()
        try:
            result = run_decoder(decoder, bounded)
            if result['protocol_verified'] and result['protocol'] not in decoder.capabilities['protocols']:
                raise ValueError('Decoder returned an undeclared protocol')
            attempts.append(dict(status='COMPLETE', **result))
            if result['protocol_verified']:
                verified.append(result['protocol'])
        except Exception as error:
            attempts.append({'decoder': {'name': decoder.name, 'version': decoder.version},
                             'status': 'FAILED', 'error': type(error).__name__+': '+str(error)[:500]})
        attempt=attempts[-1]
        evidence=attempt.get('evidence',{})
        attempt.update(input_compatibility=candidate,elapsed_ms=(time.monotonic()-attempt_started)*1000,
            synchronization='OBSERVED' if evidence.get('frames_with_sync',0) else 'NOT_OBSERVED',
            frame_validation='VERIFIED' if attempt.get('protocol_verified') else 'NOT_VERIFIED',
            failure_reason=None if attempt.get('protocol_verified') else attempt.get('error',attempt.get('outcome','NO_VALID_FRAME')))
    protocols = sorted(set(verified))
    protocol = protocols[0] if len(protocols) == 1 else 'UNKNOWN'
    validated_modulations = {registry.decoders[a['decoder']['name']].capabilities['modulations'][0]
                             for a in attempts if a.get('protocol_verified') and a['protocol'] == protocol}
    modulation = next(iter(validated_modulations)) if len(validated_modulations) == 1 else analysis.get('modulation','UNKNOWN')
    unknown_reasons=[]
    if protocol=='UNKNOWN':
        if len(protocols)>1:unknown_reasons.append('CONFLICTING_VERIFIED_PROTOCOLS')
        elif not attempts:unknown_reasons.append('NO_AVAILABLE_COMPATIBLE_DECODER')
        else:
            if any(a['status']=='FAILED' for a in attempts):unknown_reasons.append('DECODER_FAILURE')
            if any(a['status']=='COMPLETE' for a in attempts):unknown_reasons.append('NO_VALID_SYNC_AND_CHECKSUM_FRAME')
        if analysis.get('adc_amplitude_p95_counts',4)<4:unknown_reasons.append('INSUFFICIENT_ADC_AMPLITUDE')
    from .characterization import characterize
    characterization=characterize(analysis,protocols,verified_modulation=modulation if len(validated_modulations)==1 else None)
    return {'engine_version': ENGINE_VERSION, 'investigation_plan':investigation_plan,'protocol': protocol,'characterization':characterization,
            'unknown_reasons':unknown_reasons,
            'future_decoder_eligibility':[dict(decoder=c['decoder'],eligible=c['eligible'],reasons=c['reasons']) for c in candidates],
            'modulation': modulation, 'modulation_basis': 'VALIDATED_PROTOCOL' if len(validated_modulations)==1 else 'HEURISTIC',
            'protocol_verified': protocol != 'UNKNOWN', 'verified_protocols': protocols,
            'protection': protection_status(), 'analysis': analysis, 'candidates': candidates,
            'attempts': attempts, 'source_sha256': recording.source_sha256,
            'input_sample_sha256': bounded.sample_sha256,
            'sample_count': len(bounded.iq), 'sample_rate_hz': bounded.sample_rate,
            'frequency_hz': bounded.frequency_hz,
            'limitations': ['Decoder sample budget is a prefix; later transmissions may be missed.',
                            'No compatible successful decoder means UNKNOWN, not protocol absence.',
                            'Conflicting validated protocols remain UNKNOWN; all evidence is retained.']}


def identify_recording(path, metadata=None, registry=None, policy=None, previous_protocol=None, channel_bandwidth_hz=None, max_bytes=64*1024*1024, *, backend='dsp',model_path=None,cancel_event=None,recipe='generic'):
    from .iq import read_recording
    iq,meta=read_recording(path,metadata,max_bytes)
    return identify_samples(iq,meta,registry,policy,previous_protocol,channel_bandwidth_hz,backend=backend,model_path=model_path,cancel_event=cancel_event,recipe=recipe),meta


def identify_samples(iq, meta, registry=None, policy=None, previous_protocol=None, channel_bandwidth_hz=None, *, backend='dsp',model_path=None,cancel_event=None,recipe='generic'):
    """Same evidence pipeline for checked recordings and transient managed IQ."""
    from .iq import analyze_samples
    from .deep_investigation import RECIPES,measure,summarize
    if recipe not in RECIPES:raise ValueError('Unknown investigation recipe')
    started=time.monotonic()
    policy = policy or SelectionPolicy()
    if cancel_event is not None and cancel_event.is_set():raise InterruptedError('Reanalysis cancelled')
    analysis=analyze_samples(iq,meta,channel_bandwidth_hz,backend,model_path,cancel_event=cancel_event)
    registry = registry or default_registry()
    ai_future = None
    if registry.ai_model is not None:
        from .ai import submit
        ai_future = submit(analysis,meta,registry.ai_model)
    recording = DecoderInput(iq[:policy.max_samples], meta['sample_rate'],
        meta.get('desired_frequency_hz',meta['center_hz']), source_sha256=meta['sha256'])
    from .fingerprints import iq_fingerprint
    analysis['fingerprint']=iq_fingerprint(recording.iq[:4_000_000],recording.sample_rate)
    activity=analysis.get('activity',{})
    events=activity.get('events',[])
    starts=[event['start_s'] for event in events]
    analysis['fingerprint']['activity']=dict(behavior=activity.get('behavior'),
        duty_cycle_estimate=activity.get('active_fraction_estimate'),resolution_s=activity.get('resolution_s'),
        burst_durations_s=[event['duration_s'] for event in events[:100]],
        inter_event_intervals_s=[b-a for a,b in zip(starts,starts[1:])][:100],
        basis=activity.get('method'),limitations='Finite-capture energy events; intervals alone do not establish periodicity or protocol airtime.')
    from .analog import ANALOG_MODES,analyze_analog
    if analysis.get('modulation') in ANALOG_MODES:
        lo=analysis.get('activity',{}).get('selected_start_sample',0)
        segment=recording.iq[lo:lo+1000000]
        if len(segment)>=4096:
            try:
                analysis['analog_diagnostics']=analyze_analog(segment,recording.sample_rate,analysis['modulation'],
                    frequency_offset_hz=analysis.get('features',{}).get('centroid_offset_hz',0))
            except ValueError as error:analysis['analog_diagnostics']={'status':'UNAVAILABLE','reason':str(error)}
    result=identify(recording, analysis, registry, policy, previous_protocol,cancel_event,recipe)
    measurements=measure(recording.iq,recording.sample_rate,analysis,recording.frequency_hz)
    # One measured ASK/FSK timing hypothesis; candidates never bypass framing.
    timing=measurements.get('symbol_rate_candidates',[])
    if timing and analysis.get('modulation') in ('OOK','2FSK','4FSK') and analysis.get('adc_amplitude_p95_counts',4)>=4:
        from .demodulation import demodulate
        try:
            candidate=demodulate(recording.iq,recording.sample_rate,analysis['modulation'],float(timing[0]['approximate_rate_baud']),
                frequency_offset_hz=float(analysis.get('features',{}).get('centroid_offset_hz',0)))
            candidate['symbol_rate_basis']='MEASURED_RUN_LENGTH_HYPOTHESIS'
            result['demodulation_candidates']=[candidate]
        except ValueError as error:
            result['demodulation_candidates']=[dict(status='UNAVAILABLE',reason=str(error),protocol_verified=False)]
    result['deep_investigation']=summarize(result,measurements,recipe=recipe,elapsed_s=time.monotonic()-started)
    from .pipeline import diagnose
    result['pipeline_diagnostics']=diagnose(result)
    if registry.ai_model is not None:
        from concurrent.futures import TimeoutError
        try:
            result['ml_analysis']=ai_future.result(timeout=.05) if ai_future else dict(status='BUSY',dsp_preserved=True)
        except TimeoutError:
            result['ml_analysis']=dict(status='DEFERRED',dsp_preserved=True,reason='Inference exceeded identification join budget')
    from .messages import summarize_messages
    result['messages']=summarize_messages(result)
    return result


def store_identification(db, result, metadata, path, *, retain_frames=False, retain_identifiers=False):
    from .entities import private_result, persist_evidence
    signal_id = metadata.get('signal_id')
    if not signal_id or not db.execute('SELECT 1 FROM signals WHERE signal_id=?', (signal_id,)).fetchone():
        raise ValueError('Capture must reference an existing signal to store history')
    result = private_result(result, retain_frames=retain_frames, retain_identifiers=retain_identifiers)
    previous_protocol=db.execute('SELECT protocol FROM signals WHERE signal_id=?',(signal_id,)).fetchone()[0]
    conflict=bool(result['protocol_verified'] and previous_protocol not in (None,'UNKNOWN',result['protocol']))
    if conflict:
        result['catalog_protocol_conflict']=dict(previous_protocol=previous_protocol,observed_protocol=result['protocol'],resolution='HISTORY_RETAINED_AGGREGATE_PRESERVED')
    capture_timestamp = metadata.get('created')
    if isinstance(capture_timestamp, str):
        try:
            captured = datetime.fromisoformat(capture_timestamp.replace('Z', '+00:00'))
            if captured.tzinfo is not None:
                result['capture_timestamp'] = captured.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            pass
    payload = json.dumps(result, sort_keys=True, allow_nan=False)
    def stable(value):
        if isinstance(value,dict):return {k:stable(v) for k,v in value.items() if k not in ('classification_timestamp','classification_latency_ms','classified_at','load_ms','elapsed_ms','elapsed_s','timestamp','inference_duration_ms')}
        if isinstance(value,list):return [stable(v) for v in value]
        return value
    key = hashlib.sha256((signal_id+str(path)+json.dumps(stable(result),sort_keys=True,allow_nan=False)).encode()).hexdigest()
    created = datetime.now(timezone.utc).isoformat()
    with db:
        inserted = db.execute('INSERT OR IGNORE INTO identification_runs VALUES(?,?,?,?,?,?,?)',
                             (key, signal_id, created, result['source_sha256'], ENGINE_VERSION, str(path), payload)).rowcount
        if inserted: db.execute('UPDATE signals SET classifier_version=? WHERE signal_id=?',(ENGINE_VERSION,signal_id))
        if inserted and not conflict and (result['protocol_verified'] or previous_protocol in (None,'UNKNOWN')):
            db.execute("UPDATE signals SET signal_class=?,modulation=? WHERE signal_id=? AND (protocol IS NULL OR protocol='UNKNOWN' OR protocol=?)",(result.get('characterization',{}).get('signal_type','UNKNOWN'),result['modulation'],signal_id,result['protocol']))
        if inserted: persist_evidence(db, result, key, signal_id, result.get('capture_timestamp') or created)
        if inserted and result['protocol_verified'] and not conflict:
            db.execute("UPDATE signals SET protocol=?,modulation=?,protection=?,evidence=? WHERE signal_id=? AND (protocol IS NULL OR protocol='UNKNOWN' OR protocol=?)",
                       (result['protocol'], result['modulation'], result['protection']['status'], payload, signal_id,result['protocol']))
            versions=json.dumps([a['decoder'] for a in result['attempts'] if a.get('protocol_verified')])
            db.execute('UPDATE signals SET last_verified=?,decoder_version=? WHERE signal_id=? AND protocol=?',(created,versions,signal_id,result['protocol']))
    return key


def signal_history(db, signal_id):
    results=[dict(json.loads(row[0]),stored_at=row[1]) for row in db.execute(
        'SELECT result,created FROM identification_runs WHERE signal_id=? ORDER BY created DESC LIMIT 1000',(signal_id,))]
    durations=[];activity=[];seen=set()
    for result in results:
        source=result.get('source_sha256') or result.get('input_sample_sha256')
        if source in seen:continue
        seen.add(source)
        measured=result.get('analysis',{}).get('activity',{})
        durations.extend(e['duration_s'] for e in measured.get('events',[]) if isinstance(e.get('duration_s'),(float,int)))
        activity.append({'stored_at':result['stored_at'],'source_sha256':source,'active_fraction_estimate':measured.get('active_fraction_estimate'),
                         'behavior':measured.get('behavior'),'resolution_s':measured.get('resolution_s')})
    return {'identifications':results,'activity_history':activity,
            'transmission_duration_statistics':{'events':len(durations),'minimum_s':min(durations) if durations else None,
                'maximum_s':max(durations) if durations else None,'mean_s':sum(durations)/len(durations) if durations else None,
                'basis':'Energy-event durations, not verified protocol airtime; unique recording digests only.'},
            'history_limit':1000}
