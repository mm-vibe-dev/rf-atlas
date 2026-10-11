"""Measured, bounded investigation diagnostics. No protocol guesses or bit retention.

Recipes describe useful work; only the audited decoder evidence contract verifies
a protocol. Diagnostics never change the baseline modulation classifier.
"""
import time
import numpy as np
from scipy import signal

VERSION = 'deep-1'
RECIPES = {
    'generic': ('Generic unknown investigation', (), ()),
    'analog': ('Analog voice investigation', ('AM', 'NFM', 'WFM', 'USB', 'LSB'), ()),
    'land-mobile': ('Digital land-mobile investigation', ('4FSK', 'GFSK'), ('DMR',)),
    'packet': ('Amateur packet investigation', ('AFSK', '2FSK'), ('AX.25',)),
    'ais': ('AIS investigation', ('GMSK', '2FSK'), ('AIS',)),
    'sensor': ('Environmental sensor investigation', ('OOK', 'ASK', '2FSK'), ('Fineoffset-WH2', 'Fineoffset-WH5', 'Fineoffset-WH24', 'Fineoffset-WH1050')),
    'broadcast': ('Broadcast investigation', ('WFM',), ('RDS',)),
}


def recipes():
    return [dict(id=k, name=v[0], modulations=list(v[1]), decoder_protocols=list(v[2]),
        version=VERSION, acquisition=dict(short_s=2, medium_s=8, targeted_s=20, maximum_s=30),
        stages=['quality', 'characterization', 'compatibility', 'bounded decoders', 'verification'],
        evidence_required='Joint synchronization and frame integrity from audited decoder',
        limits=dict(max_samples=4_000_000, max_attempts=4, max_seconds=30, parallel_decoders=1),
        stop_conditions=['cancelled', 'budget exhausted', 'attempts exhausted'],
        selection_is_protocol_evidence=False) for k,v in RECIPES.items()]


def capability(decoder):
    """Normalize the existing audited adapter declarations without adding support."""
    c = decoder.capabilities
    return dict(registry_version=VERSION, input_format=c.get('input_format', c.get('input', 'finite complex IQ')),
        sample_rate_hz=dict(minimum=c.get('sample_rate_min_hz'), maximum=c.get('sample_rate_max_hz')),
        acquisition_bandwidth_hz=c.get('expected_bandwidth_hz'),
        minimum_useful_duration_s=c.get('minimum_duration_s'),
        minimum_duration_known='minimum_duration_s' in c,
        expected_modulation=c['modulations'],
        dependencies=c.get('external_dependencies', [str(decoder.executable)] if hasattr(decoder,'executable') else []),
        supported_metadata=c.get('metadata', []), verification=c['evidence'],
        resources=dict(max_samples=4_000_000, parallelism=1, deadline='cooperative between finite attempts'),
        failure_conditions=['incompatible input', 'dependency unavailable', 'no synchronization', 'frame integrity failed'],
        limitations=c.get('limitations', 'Only declared adapter subset; no arbitrary protocol support.'))


def plan(analysis,recipe='generic'):
    if recipe not in RECIPES:raise ValueError('Unknown investigation recipe')
    modulation=analysis.get('modulation','UNKNOWN')
    recommended=[k for k,v in RECIPES.items() if modulation in v[1]]
    compatible=recipe=='generic' or modulation in RECIPES[recipe][1]
    return dict(version=VERSION,requested_recipe=recipe,selected_recipe=recipe if compatible else 'generic',
        compatible=compatible,recommended_recipes=recommended or ['generic'],
        reason='Observed modulation supports recipe selection, not a protocol.' if compatible else 'Requested recipe lacks compatible waveform evidence; generic fallback.',
        decoder_protocol_preference=list(RECIPES[recipe][2]) if compatible else [],
        acquisition_recommendation='short' if not analysis.get('signal_detected') else 'targeted' if analysis.get('activity',{}).get('behavior') in ('BURSTY','INTERMITTENT') else 'medium',
        stop_conditions=['cancelled','cooperative time budget','attempt budget','finite samples exhausted'])


def measure(iq, rate, analysis, frequency_hz):
    """Finite prefix measurements, not a calibrated RF power or symbol estimator.

    Run-duration timing is useful only for clean rectangular ASK/FSK. Gaussian
    shaping, analog crossings and noise can defeat it; candidates are approximate
    harmonically ambiguous diagnostics and never assert synchronization.
    """
    x=np.asarray(iq[:65536]); amp=np.abs(x); n=len(x)
    f=analysis.get('features', {})
    increments=np.angle(x[1:]*np.conj(x[:-1]))*rate/(2*np.pi)
    good=(amp[1:]>np.quantile(amp,.95)*.2)&(amp[:-1]>np.quantile(amp,.95)*.2)
    valid=increments[good]
    q=np.quantile(valid,[.05,.5,.95]) if len(valid)>100 else None
    blocks=[float(np.median(valid[a:a+1024])) for a in range(0,len(valid)-1023,1024)]
    _,psd=signal.welch(x,rate,nperseg=min(1024,n),return_onesided=False,detrend=False)
    p=psd/max(float(psd.sum()),1e-30)
    entropy=float(-np.sum(p*np.log(np.maximum(p,1e-30)))/np.log(len(p)))
    mod=analysis.get('modulation','UNKNOWN'); timing=[]; diagnostics={}
    if mod in ('OOK','ASK','2FSK','4FSK') and analysis.get('signal_detected') and len(valid)>100:
        if mod in ('OOK','ASK'):
            z=amp; centers=np.quantile(z,[.1,.9]); threshold=float(centers.mean())
            states=(z>threshold).astype(int)
        else:
            z=increments; centers=np.asarray(f.get('frequency_level_candidates_hz',[]))
            if len(centers) not in (2,4):centers=np.array([])
            centers=centers+float(np.median(valid))
            states=np.argmin(abs(z[:,None]-centers),axis=1) if len(centers) else np.zeros(len(z),int)
        edges=np.flatnonzero(np.diff(states)!=0)+1
        runs=np.diff(edges)
        runs=runs[runs>=3]
        if len(runs)>=24:
            # A run may span several symbols. Report a candidate plus its
            # integer-run residual, never exact baud or recovered payload.
            unit=float(np.quantile(runs,.2))
            ratios=runs/unit
            residual=float(np.median(abs(ratios-np.maximum(1,np.rint(ratios)))))
            if residual<.12 and rate/unit<rate/3:
                timing=[dict(approximate_rate_baud=float(rate/unit), resolution_baud=float(rate/(unit-1)-rate/(unit+1)),
                    integer_run_residual=residual, transitions=len(edges), stage='SYMBOL_TIMING_CANDIDATE',
                    limitations='Run-length candidate; integer multiples/submultiples and analog transitions remain alternatives. No symbol synchronization verified.')]
        diagnostics=dict(method='rectangular envelope/frequency nearest-level decisions',
            level_centers=centers.tolist(), transition_count=int(len(edges)),
            decisions_retained=False, applicable_to='Rectangular ASK/FSK only; shaped FSK/GMSK and PSK timing unsupported')
    activity=analysis.get('activity',{})
    events=activity.get('events',[])[:100]
    starts=[e['start_s'] for e in events]
    return dict(version=VERSION, sample_count=n, measured_duration_s=n/rate,
        representation='RAW_RECEIVED_IQ_TRANSLATED_TO_DESIRED_FREQUENCY',
        estimated_center_hz=frequency_hz+f.get('centroid_offset_hz',0),
        centroid_offset_hz=f.get('centroid_offset_hz'), occupied_bandwidth_hz=f.get('occupied_bandwidth_hz'),
        peak_to_psd_floor_db=f.get('peak_snr_db'), spectral_flatness=f.get('spectral_flatness'),
        spectral_entropy=entropy, amplitude=dict(mean=float(amp.mean()),std=float(amp.std()),p05=float(np.quantile(amp,.05)),p95=float(np.quantile(amp,.95))),
        instantaneous_frequency=dict(median_hz=float(q[1]),p05_hz=float(q[0]),p95_hz=float(q[2]),
            half_90_percent_span_hz=float((q[2]-q[0])/2)) if q is not None else None,
        block_frequency_median_std_hz=float(np.std(blocks)) if len(blocks)>1 else None,
        phase_increment_circular_coherence=float(abs(np.mean(np.exp(2j*np.pi*valid/rate)))) if len(valid) else None,
        symbol_rate_candidates=timing, digital_diagnostics=diagnostics,
        burst_durations_s=[e['duration_s'] for e in events],
        inter_burst_start_intervals_s=[b-a for a,b in zip(starts,starts[1:])],
        capture_active_fraction=activity.get('active_fraction_estimate'), activity_resolution_s=activity.get('resolution_s'),
        quality='UNCALIBRATED_FINITE_CAPTURE',
        limitations=['Peak-to-PSD-floor contrast is not calibrated channel SNR.',
            'Frequency spread includes modulation, noise and carrier drift; not a calibrated deviation estimate.',
            'Burst/duty estimates apply only to this acquisition; no occupancy claim between observations.',
            'Entropy does not establish encryption. No QAM/GMSK or generic PSK timing support claimed.'])


def summarize(result, measurements, *, recipe='generic', elapsed_s=0):
    if recipe not in RECIPES:raise ValueError('Unknown investigation recipe')
    a=result['analysis']; attempts=result['attempts']; compatible=[c for c in result['candidates'] if c['eligible']]
    if result['protocol_verified']:status='VERIFIED_PROTOCOL'
    elif a.get('adc_amplitude_p95_counts',4)<4 or not a.get('signal_detected'):status='INSUFFICIENT_SIGNAL'
    elif a.get('features',{}).get('adc_rail_fraction',0)>.01:status='RECEIVER_ARTIFACT'
    elif not compatible:status='NO_COMPATIBLE_DECODER'
    elif any(t['status']=='FAILED' for t in attempts):status='DECODER_FAILED'
    elif a.get('modulation','UNKNOWN')!='UNKNOWN':status='MODULATION_ESTIMATED'
    elif a.get('signal_class')=='ANALOG':status='ANALOG_CANDIDATE'
    elif a.get('signal_class')=='DIGITAL':status='DIGITAL_CANDIDATE'
    elif attempts:status='ADDITIONAL_CAPTURE'
    else:status='NOT_YET_INVESTIGATED'
    recommendation=('No additional acquisition required for this verified result.' if result['protocol_verified'] else
        'Acquire stronger evidence with explicit manual gain and suitable antenna.' if status=='INSUFFICIENT_SIGNAL' else
        'Reduce manual gain and compare an offset acquisition; clipping/spurs remain hypotheses.' if status=='RECEIVER_ARTIFACT' else
        'Install/configure a suitable offline decoder, or retain UNKNOWN.' if status=='NO_COMPATIBLE_DECODER' else
        'Inspect decoder execution failure before acquiring more samples.' if status=='DECODER_FAILED' else
        'A bounded longer or burst-targeted acquisition may catch a complete frame; unchanged IQ analysis will not add evidence.')
    return dict(version=VERSION, recipe=recipe, triage=status, elapsed_s=elapsed_s,
        decisions=[dict(stage='QUALITY', basis=a.get('quality',{})),
            dict(stage='CHARACTERIZATION', basis=measurements),
            dict(stage='COMPATIBILITY', eligible=len(compatible), ineligible=len(result['candidates'])-len(compatible)),
            dict(stage='DECODERS', attempted=len(attempts), verified_protocols=result['verified_protocols']),
            dict(stage='NEXT_EVIDENCE', reason=recommendation)],
        next_evidence=recommendation, modulation_is_protocol_evidence=False,
        stages=dict(detected=bool(a.get('signal_detected')), digital_candidate=a.get('signal_class')=='DIGITAL',
            modulation_candidate=a.get('modulation','UNKNOWN')!='UNKNOWN', symbol_timing_candidate=bool(measurements['symbol_rate_candidates']),
            protocol_sync=any(t.get('evidence',{}).get('frames_with_sync',0)>0 for t in attempts), valid_frame=result['protocol_verified']))
