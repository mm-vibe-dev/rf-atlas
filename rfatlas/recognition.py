"""Versioned conservative DSP refinement; the frozen engine remains reproducible."""
from copy import deepcopy
from datetime import datetime,timezone
import time
import numpy as np
from scipy import signal
from .engine import classify as frozen_classify

VERSION='rf-dsp-1.9.0rc1'


def multiplex_support(features):
    """Existing pilot/audio gates; RF frequency and decoder output are absent."""
    fm=features.get('discriminator',{})
    return (features.get('reliable_phase_fraction',0)>.95
            and features.get('envelope_cv',1)<.30
            and fm.get('pilot_19khz_fraction',0)>.005
            and fm.get('pilot_neighbor_ratio',0)>20
            and fm.get('multiplex_audio_fraction',0)>.35)


def wide_fm_channel(iq,rate,analysis,metadata=None):
    """Test one finite channel when broad noise has inflated occupied bandwidth.

    Corroboration in disjoint halves prevents a transient pilot-like peak from
    establishing a family. This is a waveform hypothesis, never RDS evidence.
    """
    x=np.asarray(iq)[:1_000_000];metadata=dict(metadata or {},channel_bandwidth_hz=200000)
    if rate<500000 or len(x)<32768:return dict(accepted=False,reason='Insufficient channel context')
    f=temporal_features(x,rate,analysis,metadata)
    if not multiplex_support(f):return dict(accepted=False,features=f,reason='Pilot/audio gates not met')
    halves=[temporal_features(part,rate,analysis,metadata) for part in np.array_split(x,2)]
    return dict(accepted=all(multiplex_support(part) for part in halves),features=f,halves=halves,
                channel_bandwidth_hz=200000,reason='Independent halves must corroborate multiplex structure')


def temporal_features(iq,rate,analysis,metadata=None,minimum_bandwidth_hz=12000):
    """Bounded channel-relative features; neither RF frequency nor identity is input."""
    from .recovery import isolate
    x=np.asarray(iq)[:1_000_000]
    metadata=metadata or {};raw=analysis['features']
    width=metadata.get('channel_bandwidth_hz') or raw['occupied_bandwidth_hz']
    if not 0<width<rate:return {}
    # A strong AM carrier can hide distributed audio sidebands below the
    # occupied-power threshold. Do not erase that audio before measuring it.
    analysis_width=max(width,min(minimum_bandwidth_hz,rate*.2))
    center=metadata.get('channel_center_offset_hz',raw['centroid_offset_hz'])
    y,_=isolate(x,rate,center,analysis_width)
    recovery=analysis.get('recovery',{})
    lo=max(64,int(recovery.get('selected_start_sample',64)))
    hi=min(len(y)-64,int(recovery.get('selected_stop_sample',len(y)-64)))
    y=y[lo:hi]
    if len(y)<4096:return {}
    envelope=abs(y);q10,q90=np.quantile(envelope,[.1,.9])
    phase=np.angle(y[1:]*np.conj(y[:-1]))*rate/(2*np.pi)
    mask=(envelope[1:]>q90*.25)&(envelope[:-1]>q90*.25)
    if mask.mean()<.75:return dict(reliable_phase_fraction=float(mask.mean()))
    # Welch's frequency axis remains in Hz, avoiding sample-rate-dependent thresholds.
    def concentration(v):
        f,p=signal.welch(v-np.mean(v),fs=rate,nperseg=min(len(v),max(256,round(rate*.032))))
        total=max(float(p.sum()),1e-30);k=int(np.argmax(p));peak=float(p[max(0,k-2):k+3].sum()/total)
        pilot=abs(f-19000)<65;neighbors=(f>18000)&(f<20000)&~pilot
        return dict(peak_fraction=peak,peak_hz=float(f[k]),audio_fraction=float(p[(f>=100)&(f<=6000)].sum()/total),
                    variance=float(np.var(v)),low_fraction=float(p[(f>=50)&(f<=3000)].sum()/total),
                    multiplex_audio_fraction=float(p[(f>=50)&(f<=15000)].sum()/total),
                    pilot_19khz_fraction=float(p[pilot].sum()/total),
                    pilot_neighbor_ratio=float(np.mean(p[pilot])/max(float(np.median(p[neighbors])),1e-30)) if pilot.any() and neighbors.any() else 0.)
    return dict(envelope=concentration(envelope),discriminator=concentration(phase),
        envelope_cv=float(envelope.std()/max(envelope.mean(),1e-15)),
        envelope_q10_q90=float(q10/max(q90,1e-15)),reliable_phase_fraction=float(mask.mean()),
        analysis_bandwidth_hz=float(analysis_width),duration_s=len(y)/rate)


def classify(iq,sample_rate,metadata=None,backend='dsp',model_path=None,*,analysis_level='deep',max_seconds=.2,cancel_event=None):
    """DSP is default. Legacy ML stays opt-in and never verifies a protocol."""
    started=time.perf_counter();metadata=dict(metadata or {})
    if model_path:metadata['model_path']=str(model_path)
    old=frozen_classify(iq,sample_rate,metadata,backend='dsp' if backend in ('dsp16','rfai') else backend)
    result=deepcopy(old)
    result['algorithm_version']=VERSION if backend in ('dsp','dsp16','rfai') else 'frozen-'+backend
    result['classification_timestamp']=datetime.now(timezone.utc).isoformat()
    result['input_sample_rate_hz']=sample_rate
    if backend not in ('dsp','dsp16','rfai'):
        result['classification_latency_ms']=(time.perf_counter()-started)*1000;return result
    result['prior_dsp']=dict(modulation=old['modulation'],signal_class=old['signal_class'],confidence=old['confidence'],evidence=old['evidence'])
    raw=old['features'];recovery=old.get('recovery',{})
    blocked=not old['signal_detected'] or any('ADC' in e or 'CU8' in e for e in old['evidence'])
    if not blocked:
        f=temporal_features(iq,sample_rate,old,metadata);result['recognition_features']=f
        width=recovery.get('selected_raw_occupied_bandwidth_hz',raw['occupied_bandwidth_hz'])
        env=f.get('envelope',{});fm=f.get('discriminator',{});modes=recovery.get('frequency_modes',{})
        am_audio=(f.get('reliable_phase_fraction',0)>.95 and .10<f.get('envelope_cv',0)<.48
            and env.get('low_fraction',0)>.70 and f.get('envelope_q10_q90',0)>.25
            and modes.get('span_hz',float('inf'))<sample_rate/4096*8)
        if am_audio and old['modulation'] in ('AM','CW','UNKNOWN'):
            result.update(signal_class='ANALOG',modulation='AM',confidence=.65)
            result['evidence']=['Continuous audio-band envelope variation with limited carrier-relative phase deviation; AM candidate']
        elif width<=sample_rate/4096*5 and f.get('envelope_cv',1)<.25:
            result.update(signal_class='CARRIER',modulation='CARRIER',confidence=.65)
            result['evidence']=['Narrow stable carrier candidate; DC spur and unmodulated emissions remain alternatives; keying is unverified']
        elif f.get('reliable_phase_fraction',0)>.9:
            env=f['envelope'];fm=f['discriminator'];modes=recovery.get('frequency_modes',{})
            # Preserve the already validated tonal rule on its original
            # analysis window. The AM sideband window is not a replacement
            # for a narrow FM channel at low SNR.
            legacy=temporal_features(iq,sample_rate,old,metadata,0) if raw['occupied_bandwidth_hz']<12000 else f
            legacy_fm=legacy.get('discriminator',{})
            tonal_fm=(legacy.get('reliable_phase_fraction',0)>.9 and legacy_fm.get('peak_fraction',0)>.50
                and 100<legacy_fm.get('peak_hz',0)<6000 and legacy_fm.get('audio_fraction',0)>.70
                and legacy.get('envelope_cv',1)<.28 and modes.get('concentration',1)<.55
                and modes.get('span_hz',0)>sample_rate/4096*8)
            smooth_fm=(50<fm['peak_hz']<6000 and fm['audio_fraction']>.85
                and f['envelope_cv']<.28 and modes.get('concentration',1)<.55
                and len(modes.get('modes_hz',[]))==1
                and modes.get('span_hz',0)>sample_rate/4096*8)
            multiplex_fm=(raw['occupied_bandwidth_hz']>80000 and modes.get('concentration',1)<.55
                and multiplex_support(f))
            if not multiplex_fm and raw['occupied_bandwidth_hz']>240000 and sample_rate>=500000 and old['modulation'] in ('NFM','WFM','UNKNOWN','AM'):
                channel=wide_fm_channel(iq,sample_rate,old,metadata)
                result['wide_fm_channel_features']=channel
                multiplex_fm=channel['accepted'] and modes.get('concentration',1)<.55
            if old['modulation'] in ('NFM','WFM','UNKNOWN','AM') and multiplex_fm:
                result.update(signal_class='ANALOG',modulation='WFM',confidence=.65)
                result['evidence']=['Wide continuous-envelope FM with audio-band discriminator energy and narrow 19 kHz pilot-like component; multiplex candidate only, no verified digital subcarrier']
            elif old['modulation'] in ('NFM','WFM','UNKNOWN','AM') and (tonal_fm or smooth_fm):
                result.update(signal_class='ANALOG',modulation='NFM' if raw['occupied_bandwidth_hz']<25000 else 'WFM',confidence=.65)
                result['evidence']=['Continuous envelope and audio-band discriminator concentration; discrete FSK state evidence is insufficient; analog FM candidate only']
            elif old['modulation']=='AM' and not am_audio and not (env['peak_fraction']>.45 and env['audio_fraction']>.70
                    and f['envelope_q10_q90']>.20 and modes.get('span_hz',float('inf'))<sample_rate/4096*8):
                result['strict_analog_diagnostic']='ABSTAIN'
                result['quality']['warnings'].append('AM lacks corroborating limited phase deviation; strict rejection is experimental because stochastic audio validation lost recall')
        if result['modulation']=='AM' and env and env['audio_fraction']<.20:
            result.update(signal_class='UNKNOWN',modulation='UNKNOWN',confidence=0.)
            result['evidence']=['Envelope variation outside tested audio band; multiple tones or interference remain alternatives']
    elif not old['signal_detected'] and raw['spectral_flatness']>.8:
        result['signal_class']='NOISE_CANDIDATE'
    from .advanced_dsp import characterize_structure
    structure=characterize_structure(iq,sample_rate,old,metadata,level=analysis_level,max_seconds=max_seconds,cancel_event=cancel_event)
    result['structural_features']=structure
    # A flat full-band emission may fail spectral detection while retaining CP
    # structure. Acquisition quality remains a separate, mandatory gate.
    x=np.asarray(iq)
    endpoints=metadata.get('analysis_quality',{}).get('adc_endpoint_fraction',
        float(np.mean(np.isclose(abs(x.real),1,rtol=0,atol=1e-8)|np.isclose(abs(x.imag),1,rtol=0,atol=1e-8))))
    quality_blocked=endpoints>.01 or any('ADC' in e or 'CU8' in e for e in old['evidence']) or (metadata.get('format')=='cu8' and np.quantile(abs(x),.95)*127.5<4)
    cp=(structure.get('cyclic_prefix') or {}).get('best')
    if cp and cp['accepted'] and not quality_blocked:
        result.update(signal_detected=True,signal_class='DIGITAL',modulation='OFDM_LIKE',confidence=.65)
        result['quality']['status']='TENTATIVE'
        result['evidence']=['Periodic prefix/tail correlation corroborated in two disjoint halves; OFDM-like structure candidate only']
    elif (structure.get('phase_alphabet') or {}).get('candidate') and not quality_blocked:
        result.update(signal_class='DIGITAL',modulation=structure['phase_alphabet']['candidate'],confidence=.65)
        result['quality']['status']='TENTATIVE'
        result['evidence']=['Carrier-corrected high-envelope phase alphabet and weak lower-order moment corroborated in two halves; PSK hypothesis only']
    elif result['signal_class'] in ('CARRIER','ANALOG','UNKNOWN') and structure['keying']['accepted'] and structure['keying'].get('on_phase_coherence',0)>.995 and raw['occupied_bandwidth_hz']<2000 and not quality_blocked:
        result.update(signal_class='CARRIER',modulation='KEYED_CARRIER',confidence=.65)
        result['evidence']=['Narrow carrier with repeated slow on/off intervals before activity trimming; CW and slow OOK remain alternatives']
    if backend=='rfai':
        try:
            if not model_path:raise ValueError('No optional RF model selected')
            from .rfai import predict
            prediction=predict(result,model_path);result['rfai_prediction']=prediction
            if not blocked:
                label=prediction['modulation'];kind='ANALOG' if label in ('AM','NFM','WFM') else 'DIGITAL' if label in ('OOK','2FSK','4FSK','BPSK','QPSK') else 'CARRIER' if label=='CW' else 'UNKNOWN'
                result.update(modulation='UNKNOWN' if label=='NOISE' else label,signal_class=kind,confidence=prediction['score'] if prediction['accepted'] else 0.,backend='rfai',algorithm_version=prediction['algorithm'],confidence_kind=prediction['confidence_kind'])
                result['evidence']=['Experimental synthetic-only RF prototype hypothesis; no protocol verification and no calibrated confidence']
        except (ValueError,OSError,KeyError,TypeError) as error:
            result['quality']['warnings'].append('Optional RF AI unavailable ('+type(error).__name__+'); DSP fallback')
    result['family_decision']=dict(candidate=result['signal_class'],modulation=result['modulation'],
        verified=False,quality=result['quality']['status'],evidence=list(result['evidence']),
        duration_s=len(iq[:1_000_000])/sample_rate,
        conflicting_evidence=list(result['quality'].get('warnings',[])),
        bandwidth_hz=raw['occupied_bandwidth_hz'],frequency_offset_hz=raw['centroid_offset_hz'],
        confidence_kind='UNCALIBRATED_HEURISTIC_SUPPORT',support=result['confidence'],
        limitation='Waveform evidence only; USB/LSB need a carrier reference. Keying and CP structure do not uniquely identify CW or an OFDM protocol.')
    result['recognition_hierarchy']=[
        dict(stage='RF_DETECTION',candidate=bool(result['signal_detected']),basis='SPECTRAL_OR_STRUCTURAL_EVIDENCE'),
        dict(stage='CHARACTERIZATION',candidate=result['quality']['status'],basis='ACQUISITION_AND_DSP'),
        dict(stage='MODULATION_FAMILY',candidate=result['signal_class'],verified=False),
        dict(stage='MODULATION',candidate=result['modulation'],verified=False),
        dict(stage='PROTOCOL',candidate='UNKNOWN',verified=False),
        dict(stage='ENCRYPTION',candidate='UNKNOWN',verified=False)]
    result['classification_latency_ms']=(time.perf_counter()-started)*1000
    return result
