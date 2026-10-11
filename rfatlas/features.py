"""Original conservative IQ feature extraction and waveform hypotheses."""
import numpy as np
from scipy import signal


def extract_features(iq,sample_rate):
    samples=np.asarray(iq,dtype=np.complex128)
    if samples.ndim!=1 or len(samples)<4096 or not np.isfinite(samples).all():
        raise ValueError('Need at least 4096 finite complex IQ samples')
    if not np.isfinite(sample_rate) or sample_rate<=0: raise ValueError('Invalid sample rate')
    envelope=np.abs(samples)
    power=float(np.mean(envelope**2))
    # Mean subtraction erases a carrier translated to baseband DC on replay.
    # Keep it measurable; a receiver DC spur remains an explicit alternative.
    f,psd=signal.welch(samples,fs=sample_rate,nperseg=4096,return_onesided=False,detrend=False)
    order=np.argsort(f);f=f[order];psd=psd[order]
    baseline=max(float(np.quantile(psd,.3)),1e-30)
    excess=np.maximum(psd-baseline,0)
    peak=int(np.argmax(psd));snr=float(10*np.log10(max(float(psd[peak])/baseline,1)))
    # Bound occupied-power integration to significant spectrum; noise tails
    # must not turn a narrow channel into full sample-rate bandwidth.
    weight=np.where(psd>max(baseline*4,float(psd.max())*.001),excess,0)
    if weight.sum()>0:
        c=np.cumsum(weight);lo=np.searchsorted(c,.005*c[-1]);hi=min(len(f)-1,np.searchsorted(c,.995*c[-1]))
        bandwidth=float(f[hi]-f[lo]+sample_rate/4096)
        centroid=float(np.average(f,weights=weight))
    else: bandwidth=0.;centroid=0.
    normalized=envelope/max(float(envelope.mean()),1e-15)
    env_cv=float(np.std(normalized))
    low=float(np.mean(envelope<np.quantile(envelope,.95)*.2))
    phase=np.angle(samples[1:]*np.conj(samples[:-1]))
    inst=phase*sample_rate/(2*np.pi)
    active=(envelope[1:]>np.quantile(envelope,.95)*.2)&(envelope[:-1]>np.quantile(envelope,.95)*.2)
    inst=inst[active]
    if len(inst)<100: inst=np.zeros(100)
    spread=float(np.percentile(inst,95)-np.percentile(inst,5))
    # Histogram peaks are a hypothesis only. No guessed symbol rate or
    # protocol is attached. Carrier offset is removed by centering the data.
    centered=inst-np.median(inst)
    bound=max(float(np.percentile(np.abs(centered),99)),sample_rate/4096)
    hist,edges=np.histogram(centered,bins=81,range=(-bound,bound))
    smoothed=np.convolve(hist,[.25,.5,.25],mode='same')
    peaks,_=signal.find_peaks(np.r_[0,smoothed,0],prominence=max(smoothed.max()*.12,2),distance=6)
    peaks=peaks-1
    levels=[float((edges[i]+edges[i+1])/2) for i in peaks]
    concentration=float(np.mean(np.min(np.abs(centered[:,None]-np.array(levels)[None,:]),axis=1)<max(bound*.1,sample_rate/4096))) if levels else 0.
    # Correct constant carrier offset before phase-order moments. Median
    # increments avoid letting sparse symbol transitions set the offset.
    cfo=float(np.median(inst))
    unit=samples/np.maximum(envelope,1e-15)*np.exp(-2j*np.pi*cfo*np.arange(len(samples))/sample_rate)
    reliable=envelope>np.quantile(envelope,.95)*.2
    moments=[float(abs(np.mean(unit[reliable]**order))) if reliable.any() else 0. for order in [2,4]]
    jumps=float(np.mean(np.abs(np.angle(np.exp(1j*(phase-2*np.pi*cfo/sample_rate))))>.7))
    clipping=float(np.mean((np.abs(samples.real)>=.98)|(np.abs(samples.imag)>=.98)))
    flatness=float(np.exp(np.mean(np.log(np.maximum(psd,1e-30))))/max(float(psd.mean()),1e-30))
    return {'peak_snr_db':snr,'total_power_dbfs':float(10*np.log10(max(power,1e-30))),'occupied_bandwidth_hz':bandwidth,'centroid_offset_hz':centroid,'envelope_cv':env_cv,'low_envelope_fraction':low,'instantaneous_frequency_span_hz':spread,'frequency_level_candidates_hz':levels,'frequency_level_concentration':concentration,'phase_order_moments':moments,'phase_jump_fraction':jumps,'carrier_offset_estimate_hz':cfo,'adc_rail_fraction':clipping,'spectral_flatness':flatness,'samples':len(samples),'sample_rate':sample_rate}


def classify(iq,sample_rate):
    features=extract_features(iq,sample_rate)
    result={'signal_detected':features['peak_snr_db']>=10 and features['spectral_flatness']<.65,'signal_class':'UNKNOWN','modulation':'UNKNOWN','protocol':'UNKNOWN','protection':'UNKNOWN','confidence':0.0,'confidence_kind':'UNCALIBRATED_HEURISTIC_SUPPORT','basis':'HEURISTIC','evidence':[],'features':features}
    snr=features['peak_snr_db'];cv=features['envelope_cv'];bw=features['occupied_bandwidth_hz'];span=features['instantaneous_frequency_span_hz'];levels=features['frequency_level_candidates_hz']
    if snr<10 or features['spectral_flatness']>.65 or bw<=0:
        result['evidence']=['Insufficient spectral contrast for a waveform hypothesis'];return result
    if features['adc_rail_fraction']>.01:
        result['evidence']=['ADC rail occupancy suggests clipping; modulation estimate rejected'];return result
    if bw<=sample_rate/4096*4 and cv<.15:
        result.update(modulation='CW',confidence=.7)
        result['evidence']=['Carrier-like narrow spectrum and stable envelope; DC spur remains an alternative']
    elif features['low_envelope_fraction']>.15 and cv>.5:
        result.update(modulation='OOK',confidence=.6)
        result['evidence']=['Frequent near-zero envelope states with elevated RF spectrum; burst gating can mimic OOK']
    elif cv<.2 and features['phase_jump_fraction']>.001 and max(features['phase_order_moments'])>.8:
        result.update(signal_class='DIGITAL',modulation='BPSK' if features['phase_order_moments'][0]>.8 else 'QPSK',confidence=.6)
        result['evidence']=['Carrier-corrected phase-order coherence with abrupt phase changes; unconfirmed PSK hypothesis']
    elif cv<.2 and len(levels) in (2,4) and features['frequency_level_concentration']>.7 and span>sample_rate/4096*8:
        result.update(signal_class='DIGITAL',modulation=f'{len(levels)}FSK',confidence=.65)
        result['evidence']=[f'{len(levels)} separated instantaneous-frequency histogram modes with stable envelope; unconfirmed FSK hypothesis']
    elif cv<.2 and span>sample_rate/4096*8:
        result.update(modulation='NFM' if bw<25000 else 'WFM',confidence=.45)
        result['evidence']=['Continuous frequency variation with stable envelope; analog versus digital is unresolved']
    elif .2<cv<.5 and span<sample_rate/4096*8:
        result.update(signal_class='ANALOG',modulation='AM',confidence=.5)
        result['evidence']=['Amplitude varies while instantaneous frequency stays near the carrier; unconfirmed AM hypothesis']
    else: result['evidence']=['RF energy present; features do not support a conservative modulation label']
    # No branch derives a service, protocol or protection state from frequency.
    return result
