"""Hierarchical characterization without changing the comparable DSP engine."""


def characterize(analysis, verified_protocols=(),verified_modulation=None):
    protocols=set(verified_protocols)
    modulation=analysis.get('modulation','UNKNOWN')
    evidence=list(analysis.get('evidence',[]))
    support=analysis.get('confidence',0.)
    kind='UNKNOWN'; basis='DSP_HYPOTHESIS'
    if len(protocols)>1:
        basis='CONFLICTING_PROTOCOLS'
        evidence.append('Conflicting verified protocols require review; no single RF family asserted.')
    elif 'RDS' in protocols:
        kind='ANALOG';modulation='WFM';basis='VERIFIED_PROTOCOL'
        evidence.append('Verified RDS metadata in an analog wideband FM multiplex; audio intelligibility is separate.')
    elif protocols:
        kind='DIGITAL';basis='VERIFIED_PROTOCOL'
        modulation=verified_modulation or modulation
        evidence.append('Synchronization and frame integrity verified for '+', '.join(sorted(protocols)))
    elif analysis.get('signal_detected') and analysis.get('signal_class') in ('ANALOG','DIGITAL','CARRIER'):
        kind=analysis['signal_class']
    elif analysis.get('features',{}).get('spectral_flatness',0)>.8 and not analysis.get('signal_detected'):
        kind='NOISE_CANDIDATE';evidence.append('Flat spectrum without sufficient contrast; interference and weak signals remain alternatives.')
    return dict(signal_type=kind,modulation=modulation,basis=basis,heuristic_support=support,
        metadata_protocols=['RDS'] if 'RDS' in protocols else [],
        blind_signal_type=analysis.get('signal_class','UNKNOWN'),
        blind_modulation=analysis.get('modulation','UNKNOWN'),
        confidence_kind='UNCALIBRATED_HEURISTIC_SUPPORT',evidence=evidence,
        abstained=kind=='UNKNOWN',limitations=['Finite recording only; waveform candidates do not verify a protocol.',
            'Carrier/CW, SSB, QAM and OFDM families may require abstention. No ML promotion.'])
