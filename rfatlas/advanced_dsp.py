"""Bounded structural evidence, without protocol or transmitter inference.

CP correlation uses repeated prefix/tail samples, normalized per phase and
checked on two disjoint halves. Tones and repeated entire waveforms must not
pass simply because they correlate at every phase.
"""
import time
import numpy as np
from scipy import signal
from .recovery import validate_iq

VERSION = 'structure-1'


def _check(cancel_event, deadline):
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('Structural analysis cancelled')
    return time.perf_counter() < deadline


def cyclic_prefix(iq, sample_rate, *, max_seconds=.15, cancel_event=None):
    """Search a finite power-of-two FFT/CP bank; no resampling or decoding.

    Lag phase gives CFO only modulo sample_rate/FFT size. It is deliberately
    not returned as an absolute carrier estimate.
    """
    if type(max_seconds) not in (int,float) or not np.isfinite(max_seconds) or not .001 <= max_seconds <= 2:
        raise ValueError('CP budget must be between .001 and 2 seconds')
    x=validate_iq(iq,sample_rate)[:65536]
    deadline=time.perf_counter()+max_seconds;best=None;tested=0;exhausted=False
    power=abs(x)**2
    for lag in (32,64,128,256,512,1024,2048):
        for divisor in (4,8,16,32):
            if not _check(cancel_event,deadline):exhausted=True;break
            cp=lag//divisor;period=lag+cp
            count=(len(x)-lag)//period
            if count<32:continue
            n=count*period
            product=(x[:n]*x[lag:lag+n].conj()).reshape(count,period)
            left=power[:n].reshape(count,period);right=power[lag:lag+n].reshape(count,period)
            def curve(a,b,c):
                # Circular CP window lets a capture start at any symbol phase.
                def window(v):
                    v=np.r_[v,v[:cp-1]];s=np.r_[0,np.cumsum(v)]
                    return s[cp:]-s[:-cp]
                z=window(a.mean(axis=0));den=np.sqrt(window(b.mean(axis=0))*window(c.mean(axis=0)))
                return np.minimum(abs(z)/np.maximum(den,1e-30),1.)
            whole=curve(product,left,right);k=int(np.argmax(whole));outside=float(np.median(whole))
            halves=[curve(product[s],left[s],right[s])[k] for s in (slice(None,count//2),slice(count//2,None))]
            peak=float(whole[k]);repeat=float(min(halves));contrast=peak-outside
            accepted=peak>.45 and repeat>.35 and outside<.20 and contrast>.35
            candidate=dict(fft_samples=lag,prefix_samples=cp,symbol_samples=period,
                symbol_rate_hz=sample_rate/period,correlation=peak,half_min_correlation=repeat,
                background_correlation=outside,contrast=contrast,symbols=count,accepted=bool(accepted))
            tested+=1
            if best is None or contrast>best['contrast']:best=candidate
        if exhausted:break
    return dict(version=VERSION,best=best,tested=tested,budget_exhausted=exhausted,
        samples=len(x),limitation='Finite FFT/CP bank; oversampling, multipath and other repeated bursts may defeat or mimic CP. OFDM-like only; no protocol verification.')


def slow_keying(iq,sample_rate):
    """Observe slow on/off intervals before activity selection erases them.

    Keying is not proof of Morse/CW: slow OOK and intermittent interference
    remain alternatives. Never use this measurement to identify content.
    """
    x=np.asarray(iq)[:1_000_000];block=max(16,round(sample_rate*.002))
    count=len(x)//block
    if count<20:return dict(accepted=False,reason='Insufficient slow-envelope duration')
    energy=np.mean(abs(x[:count*block].reshape(count,block))**2,axis=1)
    low,high=np.quantile(energy,[.1,.9]);ratio=float(low/max(high,1e-30))
    active=energy>(low+high)/2
    edges=np.flatnonzero(np.diff(active.astype(int)))+1
    lengths=np.diff(np.r_[0,edges,count])*block/sample_rate
    interior=lengths[1:-1]
    sample_active=np.repeat(active,block)
    lag=16
    mask=sample_active[lag:]&sample_active[:-lag]
    pair=x[lag:len(sample_active)]*x[:len(sample_active)-lag].conj()
    floor=np.sqrt(high)*.5
    mask &= (abs(x[lag:len(sample_active)])>floor)&(abs(x[:len(sample_active)-lag])>floor)
    unit=pair/np.maximum(abs(pair),1e-30)
    coherence=float(abs(np.mean(unit[mask]))) if mask.any() else 0.
    accepted=ratio<.12 and len(edges)>=3 and len(interior)>=2 and np.min(interior)>=.012 and .10<active.mean()<.9
    return dict(accepted=bool(accepted),low_high_power_ratio=ratio,transition_count=len(edges),
        on_phase_coherence=coherence,
        active_fraction=float(active.mean()),intervals_s=lengths[:128].tolist(),resolution_s=block/sample_rate,
        limitation='Slow on/off structure only; CW, slow OOK and interference are not uniquely distinguishable.')


def phase_alphabet(iq,sample_rate,centroid,*,deadline,cancel_event=None):
    """Carrier-corrected phase alphabet on high-envelope samples.

    Mth-power FFT peak alone is insufficient: AM/carriers also have it.
    Require a strong Mth moment, weak lower moment and both-half agreement.
    Only bounded 2/4/8-PSK hypotheses; QAM is deliberately not inferred.
    """
    from .recovery import phase_recovery,compensate_carrier
    x=np.asarray(iq)[:65536];out=[]
    env=abs(x);q60,q95=np.quantile(env,[.60,.95]);upper_ratio=float(q60/max(q95,1e-30))
    _,psd=signal.welch(x,fs=sample_rate,nperseg=min(8192,len(x)),return_onesided=False)
    line_fraction=float(np.sort(psd)[-6:].sum()/max(psd.sum(),1e-30))
    for order in (2,4,8):
        if not _check(cancel_event,deadline):break
        _,model=phase_recovery(x,sample_rate,order)
        interval=sample_rate/order
        offset=model['offset_hz']+round((centroid-model['offset_hz'])/interval)*interval
        y=compensate_carrier(x,sample_rate,offset);env=abs(y);unit=y/np.maximum(env,1e-30)
        mask=env>=np.quantile(env,.5)
        lower=order//2
        moments=[]
        for part in (slice(None,len(x)//2),slice(len(x)//2,None)):
            u=unit[part][mask[part]]
            if len(u)>=64:moments.append((float(abs(np.mean(u**order))),float(abs(np.mean(u**lower)))))
            else:moments.append((0.,1.))
        high=min(v[0] for v in moments);low=max(v[1] for v in moments)
        accepted=model['peak_fraction']>.30 and high>.75 and low<.30 and upper_ratio>.72 and line_fraction<.60
        out.append(dict(order=order,carrier_offset_hz=offset,peak_fraction=model['peak_fraction'],
            half_min_moment=high,half_max_lower_moment=low,accepted=bool(accepted)))
        if accepted:break
    return dict(models=out,upper_envelope_ratio=upper_ratio,spectral_line_fraction=line_fraction,candidate=next(({2:'BPSK',4:'QPSK',8:'8PSK'}[m['order']] for m in out if m['accepted']),None),
        limitation='Phase alphabet plus stable upper envelope, excluding dominant spectral lines; clipped analog audio can remain ambiguous. Pulse shaping/fading/alias/adjacent emitters can cause abstention or false hypotheses.')


def characterize_structure(iq,sample_rate,analysis,metadata=None,*,level='deep',max_seconds=.2,cancel_event=None):
    """Fast diagnostics, conditional deep CP bank, bounded forensic bank.

    Caller may choose fast to omit CP. Forensic increases CP time allowance,
    never the sample cap or search bank. Cancellation is between bank entries.
    """
    if level not in ('fast','deep','forensic'):raise ValueError('Unknown DSP analysis level')
    if type(max_seconds) not in (int,float) or not np.isfinite(max_seconds) or not .001<=max_seconds<=2:
        raise ValueError('Invalid structural processing budget')
    start=time.perf_counter();deadline=start+max_seconds
    if not _check(cancel_event,deadline):raise TimeoutError('Structural processing budget expired')
    x=validate_iq(iq,sample_rate);raw=analysis['features'];metadata=metadata or {}
    scale=max(float(np.max(abs(x.real))),float(np.max(abs(x.imag))))
    if scale:x/=scale
    f,p=signal.welch(x,fs=sample_rate,nperseg=min(2048,len(x)),return_onesided=False)
    p=np.maximum(p,1e-30);prob=p/p.sum()
    entropy=float(-np.sum(prob*np.log(prob))/np.log(len(prob)))
    neg=float(p[f<0].sum());pos=float(p[f>0].sum())
    out=dict(version=VERSION,level=level,samples=len(x),spectral_entropy=entropy,
        sideband_power_ratio_db=float(10*np.log10(max(pos,1e-30)/max(neg,1e-30))),
        sideband_reference='Explicit channel center required for USB/LSB; DC-relative asymmetry alone is ambiguous',
        keying=slow_keying(x,sample_rate),cyclic_prefix=None,phase_alphabet=None,progress=dict(completed=1,total=1),
        limitations=['Structural hypotheses are uncalibrated; no encryption, protocol or transmitter identity is inferred.'])
    if level!='fast' and raw['spectral_flatness']>.35 and analysis['modulation'] in ('UNKNOWN','OFDM_LIKE'):
        remaining=deadline-time.perf_counter()
        if remaining>=.001:
            out['progress']['total']=2
            out['cyclic_prefix']=cyclic_prefix(x,sample_rate,max_seconds=min(remaining,.15 if level=='deep' else 2),cancel_event=cancel_event)
            out['progress']['completed']=2
    if level!='fast' and analysis['signal_detected'] and analysis['modulation'] in ('AM','UNKNOWN') and raw['occupied_bandwidth_hz']>500 and _check(cancel_event,deadline):
        out['progress']['total']+=1
        out['phase_alphabet']=phase_alphabet(x,sample_rate,raw['centroid_offset_hz'],deadline=deadline,cancel_event=cancel_event)
        out['progress']['completed']+=1
    _check(cancel_event,deadline)
    out['elapsed_ms']=(time.perf_counter()-start)*1000
    out['budget_exhausted']=time.perf_counter()>=deadline or bool((out['cyclic_prefix'] or {}).get('budget_exhausted'))
    return out
