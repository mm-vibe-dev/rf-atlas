"""Structured, hardware-independent classification with optional local backends."""
from copy import deepcopy
from typing import Protocol
import numpy as np
from scipy import signal,ndimage
from .features import extract_features,classify as baseline_classify
from .recovery import validate_iq,isolate,burst_window,trim_quiet_edges,phase_recovery,estimate_symbol_timing


class ClassifierBackend(Protocol):
    """Future local adapters consume normalized IQ and explicit acquisition evidence."""
    def classify(self,iq,sample_rate,metadata=None)->dict: ...


def _mode_features(frequency,sample_rate):
    v=frequency[np.isfinite(frequency)][::max(1,len(frequency)//16000)]
    lo,hi=np.quantile(v,[.01,.99]);span=max(hi-lo,sample_rate/4096)
    hist,edges=np.histogram(np.clip(v,lo,hi),bins=97,range=(lo-span*.03,hi+span*.03))
    h=ndimage.gaussian_filter1d(hist.astype(float),1.)
    peaks,_=signal.find_peaks(h,prominence=max(float(h.max())*.15,2),distance=10)
    centers=(edges[:-1]+edges[1:])/2
    modes=centers[peaks]
    if len(modes) in (2,4):
        spacing=float(np.min(np.diff(modes)))
        distance=np.min(abs(v[:,None]-modes[None,:]),axis=1)
        concentration=float(np.mean(distance<spacing*.16))
    else:spacing=0.;concentration=0.
    return {'modes_hz':modes.tolist(),'concentration':concentration,'spacing_hz':spacing,'span_hz':float(np.quantile(v,.95)-np.quantile(v,.05))}


def _dsp(x,rate,metadata):
    raw=extract_features(x,rate)
    result={'signal_detected':raw['peak_snr_db']>=10 and raw['spectral_flatness']<.65,'signal_class':'UNKNOWN','modulation':'UNKNOWN','protocol':'UNKNOWN','protection':'UNKNOWN','confidence':0.,'confidence_kind':'UNCALIBRATED_HEURISTIC_SUPPORT','basis':'DSP_RECOVERY','features':raw,'candidates':[],'evidence':[],'preprocessing':[],'symbol_rate_hz':None,'quality':{'status':'REJECTED','warnings':[]},'backend':'dsp'}
    def candidate(label,support,evidence,signal_class='UNKNOWN',eligible=True):
        result['candidates'].append({'modulation':label,'support':support,'support_kind':'UNCALIBRATED_HEURISTIC_SUPPORT','evidence':evidence,'signal_class':signal_class,'eligible':eligible})
    if not result['signal_detected']:
        result['evidence']=['Insufficient spectral contrast'];return result
    endpoints=float(np.mean(np.isclose(abs(x.real),1,rtol=0,atol=1e-8)|np.isclose(abs(x.imag),1,rtol=0,atol=1e-8)))
    endpoints=float(metadata.get('analysis_quality',{}).get('adc_endpoint_fraction',endpoints))
    result['quality']['adc_endpoint_fraction']=endpoints
    result['quality']['adc_near_rail_fraction']=raw['adc_rail_fraction']
    if endpoints>.01:
        result['evidence']=['ADC repeated endpoints suggest clipping; acquisition rejected'];return result
    if raw['adc_rail_fraction']>.01:result['quality']['warnings'].append('Near-rail occupancy without repeated endpoints; clipping is unconfirmed')
    if metadata.get('format')=='cu8' and np.quantile(abs(x),.95)*127.5<4:
        result['evidence']=['Insufficient CU8 amplitude resolution; quantization phase corners are ambiguous'];return result
    # A measured channel bandwidth is optional; otherwise retain measured PSD support.
    width=float(metadata.get('channel_bandwidth_hz') or raw['occupied_bandwidth_hz'])
    if not np.isfinite(width) or width<=0 or width>=rate:raise ValueError('Invalid analysis channel bandwidth')
    center=float(metadata.get('channel_center_offset_hz',raw['centroid_offset_hz']))
    y,operation=isolate(x,rate,center,width);result['preprocessing'].append(operation)
    edge_lo,edge_hi,operation=trim_quiet_edges(y,rate);result['preprocessing'].append(operation)
    edge_trimmed=operation['trimmed']
    y=y[edge_lo:edge_hi]
    lo,hi,operation=burst_window(y,rate);result['preprocessing'].append(operation)
    y=y[lo:hi]
    if len(y)>128:y=y[64:-64]
    envelope=abs(y);q10,q90=np.quantile(envelope,[.1,.9])
    cv=float(envelope.std()/max(envelope.mean(),1e-15));tail=float(q10/max(q90,1e-15))
    f,p=signal.welch(envelope-envelope.mean(),fs=rate,nperseg=min(4096,len(y)),detrend=False)
    k=int(np.argmax(p));periodicity=float(p[max(0,k-1):k+2].sum()/max(p.sum(),1e-30))
    phase=np.angle(y[1:]*np.conj(y[:-1]));frequency=phase*rate/(2*np.pi)
    reliable=(envelope[1:]>q90*.2)&(envelope[:-1]>q90*.2)
    smooth=max(1,min(31,int(rate/max(width*3,rate/32))))
    frequency=ndimage.median_filter(frequency,size=smooth)
    modes=_mode_features(frequency[reliable] if reliable.sum()>100 else frequency,rate)
    phase_models=[]
    for order in (2,4):
        corrected,model=phase_recovery(y,rate,order)
        env=abs(corrected);unit=corrected/np.maximum(env,1e-15)
        model['moment']=float(abs(np.mean(unit**order)))
        model['lower_moment']=float(abs(np.mean(unit**2)))
        model['first_moment']=float(abs(np.mean(unit)))
        rotation=np.angle(np.mean(unit**order))/order
        states=np.mod(np.rint((np.angle(unit)-rotation)*order/(2*np.pi)).astype(int),order)
        model['phase_state_occupancy']=(np.bincount(states,minlength=order)/len(states)).tolist()
        lag=max(2,min(16,int(rate/max(width*4,1))))
        model['phase_plateau_fraction']=float(np.mean(abs(np.angle(corrected[lag:]*np.conj(corrected[:-lag])))<.12))
        phase_models.append(model)
    recovered={'envelope_cv':cv,'envelope_q10_q90':tail,'envelope_periodicity':periodicity,'frequency_modes':modes,'phase_models':phase_models,'selected_start_sample':edge_lo+lo+64,'selected_stop_sample':edge_lo+hi-64,'smoothing_samples':smooth,'dc_magnitude':float(abs(np.mean(x)))}
    result['recovery']=recovered
    # Measure the original unfiltered selected IQ when slow gating was removed:
    # gating edges broaden a carrier's whole-record PSD. Filtering alone must
    # never supply the narrow-bandwidth evidence used to infer CW.
    carrier_width=extract_features(x[edge_lo+lo:edge_lo+hi],rate)['occupied_bandwidth_hz'] if edge_trimmed else raw['occupied_bandwidth_hz']
    recovered['selected_raw_occupied_bandwidth_hz']=carrier_width
    # A carrier's residual mean is retained: blindly removing it erases true CW.
    if carrier_width<=rate/4096*5 and cv<.25:
        candidate('CW',.8,['Narrow occupied spectrum and stable filtered envelope; DC spur remains possible'])
    elif cv>.2 and tail<.2 and periodicity<.6:
        candidate('OOK',.7,['Separated low/high envelope occupancy with nonperiodic gating; burst edges can mimic OOK'],'DIGITAL')
    elif cv>.15 and periodicity>.35:
        candidate('AM',.65,['Coherent envelope variation and carrier-like spectrum; AM hypothesis'],'ANALOG')
    else:
        p2,p4=phase_models
        strong=[m for m in phase_models if m['peak_fraction']>.2 and m['moment']>.55 and m['first_moment']<.6
                and m['phase_plateau_fraction']>.55 and sum(v>.1 for v in m['phase_state_occupancy'])>=m['order']]
        if strong and cv<.45:
            best=strong[0]
            label='BPSK' if best['order']==2 else 'QPSK'
            # The carrier estimator is only applied as part of this PSK hypothesis.
            candidate(label,.75,[f"Order-{best['order']} carrier line and corrected phase coherence; PSK assumption"],'DIGITAL')
            recovered['carrier_offset_hz']=center+best['offset_hz']
            corrected,_=phase_recovery(y,rate,best['order'])
            timing=estimate_symbol_timing(corrected,rate)
            recovered['timing']=timing;result['symbol_rate_hz']=timing['symbol_rate_hz']
        elif len(modes['modes_hz']) in (2,4) and modes['concentration']>.65 and cv<.35:
            label=f"{len(modes['modes_hz'])}FSK"
            candidate(label,.7,['Persistent separated frequency states after filtering; discrete FSK hypothesis'],'DIGITAL')
            timing=estimate_symbol_timing(frequency,rate);recovered['timing']=timing;result['symbol_rate_hz']=timing['symbol_rate_hz']
        elif cv<.3 and modes['span_hz']>rate/4096*8:
            candidate('NFM' if raw['occupied_bandwidth_hz']<25000 else 'WFM',.45,['Smooth frequency variation; analog versus digital remains unresolved'])
        elif cv>.15 and tail>.2 and modes['span_hz']<rate/4096*8:
            candidate('AM',.5,['Continuous amplitude variation with limited frequency deviation'],'ANALOG')
    if len(modes['modes_hz']) in (2,4) and modes['concentration']>.3:
        label=f"{len(modes['modes_hz'])}FSK"
        if not any(c['modulation']==label for c in result['candidates']):candidate(label,.25,['Weak frequency-state evidence; FM remains an alternative'],'DIGITAL',False)
    eligible=[c for c in result['candidates'] if c['eligible']]
    if eligible:
        chosen=eligible[0]
        result.update(modulation=chosen['modulation'],signal_class=chosen['signal_class'],confidence=chosen['support'],evidence=chosen['evidence'])
        result['quality']['status']='TENTATIVE'
    else:result['evidence']=['RF energy present; recovery evidence does not support a waveform label']
    result['quality']['warnings']+=['Heuristic hypotheses; neither protocol nor protection inferred','Carrier and symbol estimates have modulation assumptions; harmonics and aliases possible','Multiple tones/interferers can mimic amplitude modulation; no single-emitter proof']
    return result


def combine(dsp,other):
    """Conservative disagreement rejection; agreement never increases support."""
    result=deepcopy(dsp);result['backend']='hybrid'
    result['confidence_kind']='UNCALIBRATED_AGREEMENT_SUPPORT'
    result['engines']={'dsp':dsp,'ml':other}
    result['candidates']=deepcopy(dsp.get('candidates',[]))+deepcopy(other.get('candidates',[]))
    if dsp['modulation']==other['modulation'] and dsp['modulation']!='UNKNOWN':
        result['confidence']=min(dsp['confidence'],other.get('confidence',0.))
        result['evidence']=dsp['evidence']+['Independent backend agrees; agreement is not proof or calibration']
    else:
        result.update(modulation='UNKNOWN',signal_class='UNKNOWN',confidence=0.)
        result['quality']['status']='REJECTED'
        result['evidence']=['Classifier disagreement or rejection; alternatives retained as candidates']
    return result


def classify(iq,sample_rate,metadata=None,backend='dsp'):
    """Use complex normalized IQ; optional metadata is acquisition evidence, never labels."""
    if backend not in ('dsp','baseline','ml','hybrid'):raise ValueError('Unknown classifier backend')
    metadata=dict(metadata or {});x=validate_iq(iq,sample_rate)
    if backend=='baseline':
        result=baseline_classify(x,sample_rate)
        result.update(backend='baseline',candidates=[],symbol_rate_hz=None,preprocessing=[],quality={'status':'TENTATIVE' if result['modulation']!='UNKNOWN' else 'REJECTED','warnings':['Preserved feature-only baseline path']})
        return result
    dsp=_dsp(x,sample_rate,metadata)
    if backend=='dsp':return dsp
    if not metadata.get('model_path'):
        dsp['quality']['warnings'].append('Optional model unavailable; DSP fallback');return dsp
    try:
        from .ml import classify as ml_classify
        prediction=ml_classify(x,sample_rate,metadata['model_path'])
    except (ImportError,OSError,ValueError) as error:
        dsp['quality']['warnings'].append(f'Optional model unavailable or incompatible ({type(error).__name__}); DSP fallback');return dsp
    # Optional models cannot override acquisition-quality rejection.
    if not dsp['signal_detected'] or any('ADC' in e or 'CU8' in e for e in dsp['evidence']):return dsp
    if backend=='hybrid':return combine(dsp,prediction)
    prediction['backend']='ml'
    prediction.setdefault('quality',{'status':'TENTATIVE' if prediction['modulation']!='UNKNOWN' else 'REJECTED','warnings':['Experimental optional model; uncalibrated outputs']})
    prediction.setdefault('symbol_rate_hz',None);prediction.setdefault('preprocessing',[])
    for candidate in prediction.get('candidates',[]):candidate.setdefault('support_kind','UNCALIBRATED_MODEL_SCORE')
    return prediction
