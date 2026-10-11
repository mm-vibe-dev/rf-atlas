"""Explicit pipeline outcomes; absent evidence is never protocol verification."""
import json


def diagnose(result):
    analysis=result.get('analysis',{});attempts=result.get('attempts',[]);candidates=result.get('candidates',[])
    invoked={a.get('decoder',{}).get('name') for a in attempts}
    reasons=[]
    if not analysis:reasons.append('NO_CLASSIFICATION_ATTEMPT')
    elif analysis.get('adc_amplitude_p95_counts',4)<4 or not analysis.get('signal_detected'):reasons.append('INSUFFICIENT_SIGNAL_QUALITY')
    if analysis and analysis.get('signal_class','UNKNOWN')=='UNKNOWN':reasons.append('CLASSIFICATION_ABSTAINED')
    if analysis.get('modulation','UNKNOWN')!='UNKNOWN':reasons.append('MODULATION_CANDIDATE_AVAILABLE')
    if not any(c['eligible'] for c in candidates):reasons.append('NO_AVAILABLE_COMPATIBLE_DECODER')
    exclusions=[reason for c in candidates for reason in c.get('reasons',[])]
    if any('unavailable' in reason for reason in exclusions):reasons.append('DECODER_UNAVAILABLE')
    if any('supported range' in reason or 'minimum duration' in reason for reason in exclusions):reasons.append('UNSUITABLE_DECODER_INPUT_EXCLUDED')
    routing=[]
    by_name={a.get('decoder',{}).get('name'):a for a in attempts}
    for c in candidates:
        state='INVOKED' if c['decoder'] in invoked else 'ATTEMPT_BUDGET_EXHAUSTED' if c['eligible'] else 'INCOMPATIBLE_OR_UNAVAILABLE'
        attempt=by_name.get(c['decoder'],{});e=attempt.get('evidence',{})
        routing.append(dict(decoder=c['decoder'],state=state,score=c.get('score'),reasons=c.get('reasons',[]),
            stages=dict(compatible=bool(c['eligible']),selected=bool(attempt),
                input_valid=True if attempt.get('input_sample_sha256') else None,
                synchronization=True if e.get('frames_with_sync',0)>0 else False if 'frames_with_sync' in e else None,
                framing=True if e.get('frames_with_sync_and_valid_checksum',0)>0 else None,
                checksum=True if e.get('frames_with_valid_checksum',0)>0 else False if 'frames_with_valid_checksum' in e else None,
                verified=bool(attempt.get('protocol_verified'))),
            frame_counts={k:e.get(k) for k in ('frames_examined','frames_with_sync','frames_with_valid_checksum','frames_with_sync_and_valid_checksum')}))
    if any(c['state']=='ATTEMPT_BUDGET_EXHAUSTED' for c in routing):reasons.append('COMPATIBLE_DECODER_NOT_INVOKED')
    outcomes=[a.get('outcome','DECODER_ERROR' if a.get('status')=='FAILED' else 'UNSPECIFIED') for a in attempts]
    if 'NO_SYNCHRONIZATION' in outcomes:reasons.append('SYNCHRONIZATION_FAILED')
    if 'CRC_FAILURE' in outcomes:reasons.append('FRAME_VERIFICATION_FAILED')
    if any(a.get('status')=='FAILED' and a.get('error','').startswith('ValueError:') for a in attempts):reasons.append('DECODER_INPUT_OR_RESULT_REJECTED')
    if 'UNSUPPORTED_PROTOCOL' in outcomes or 'UNSUPPORTED_PROTOCOL' in result.get('unknown_reasons',[]):reasons.append('UNSUPPORTED_PROTOCOL')
    if result.get('protocol_verified'):reasons.append('VERIFIED_PROTOCOL')
    hierarchy=[dict(item) for item in analysis.get('recognition_hierarchy',[])]
    for item in hierarchy:
        if item['stage']=='PROTOCOL':item.update(candidate=result.get('protocol','UNKNOWN'),verified=bool(result.get('protocol_verified')))
        elif item['stage']=='ENCRYPTION':
            protection=result.get('protection','UNKNOWN')
            item.update(candidate=protection.get('status','UNKNOWN') if isinstance(protection,dict) else protection,verified=False)
        elif result.get('protocol_verified') and item['stage'] in ('MODULATION_FAMILY','MODULATION'):
            item.update(waveform_candidate=item['candidate'],candidate=result.get('characterization',{}).get('signal_type','UNKNOWN') if item['stage']=='MODULATION_FAMILY' else result.get('modulation','UNKNOWN'),basis='VERIFIED_PROTOCOL',verified=True)
    return dict(version='pipeline-1.7rc1',states=reasons,routing=routing,decoder_outcomes=outcomes,
        recognition_hierarchy=hierarchy,family_decision=analysis.get('family_decision'),structural_features=analysis.get('structural_features'),
        classification_method=analysis.get('algorithm_version',analysis.get('basis','UNKNOWN')),
        classified_at=analysis.get('classification_timestamp'),score=analysis.get('confidence'),
        score_kind=analysis.get('confidence_kind','UNCALIBRATED'),protocol_support='VERIFIED' if result.get('protocol_verified') else 'UNDETERMINED',limitations='Decoder failures do not establish encryption, unsupported protocol or absence of a protocol. Spectrum-only history cannot recreate IQ.')


def signal_status(db,signal_id):
    row=db.execute('SELECT result,created FROM identification_runs WHERE signal_id=? ORDER BY created DESC LIMIT 1',(signal_id,)).fetchone()
    captures=db.execute('SELECT count(*) FROM captures WHERE signal_id=?',(signal_id,)).fetchone()[0]
    if not row:return dict(states=['NO_CLASSIFICATION_ATTEMPT'] if captures else ['NO_USABLE_IQ_RECORDING','NO_CLASSIFICATION_ATTEMPT'],registered_recordings=captures,reanalysis_registered=bool(captures),classified_at=None)
    result=json.loads(row[0]);status=result.get('pipeline_diagnostics') or diagnose(result)
    return dict(status,registered_recordings=captures,reanalysis_registered=bool(captures),classified_at=status.get('classified_at') or row[1])
