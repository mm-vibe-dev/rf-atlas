"""Bounded, reproducible DSP primitives; original input arrays are never modified."""
import numpy as np
from scipy import signal,ndimage


def validate_iq(iq,sample_rate,max_samples=1000000):
    if isinstance(sample_rate,(bool,np.bool_)) or not isinstance(sample_rate,(int,float,np.integer,np.floating)) or not np.isfinite(sample_rate) or not 1000<=sample_rate<=100000000:
        raise ValueError('Sample rate must be a finite real number from 1000 to 100000000 Hz')
    x=np.asarray(iq)
    if x.ndim!=1 or not np.iscomplexobj(x) or not 4096<=len(x)<=max_samples or not np.isfinite(x).all():
        raise ValueError('Need 4096 to 1000000 finite complex IQ samples')
    return x.astype(np.complex128,copy=True)


def compensate_carrier(iq,sample_rate,offset_hz):
    if not np.isfinite(offset_hz) or abs(offset_hz)>=sample_rate/2:raise ValueError('Carrier correction outside Nyquist interval')
    return np.asarray(iq)*np.exp(-2j*np.pi*offset_hz*np.arange(len(iq))/sample_rate)


def isolate(iq,sample_rate,center_hz,bandwidth_hz):
    if not np.isfinite(bandwidth_hz) or bandwidth_hz<=0 or bandwidth_hz>=sample_rate:raise ValueError('Invalid channel bandwidth')
    x=compensate_carrier(iq,sample_rate,center_hz)
    cutoff=min(sample_rate*.45,max(bandwidth_hz*.8,sample_rate/512))
    sos=signal.butter(6,cutoff,fs=sample_rate,output='sos')
    return signal.sosfiltfilt(sos,x),{'operation':'zero-phase Butterworth channel isolation','order':6,'center_hz':float(center_hz),'cutoff_hz':float(cutoff),'edge_discard_samples':64}


def burst_window(iq,sample_rate):
    """Only trim strong slow energy gating; retain rapid envelope modulation."""
    block=max(32,round(sample_rate*.002))
    starts=np.arange(0,len(iq),block)
    energy=np.array([np.mean(abs(iq[s:s+block])**2) for s in starts])
    floor=max(float(np.quantile(energy,.2)),1e-30)
    active=energy>floor*4
    if not active.any() or active.mean()>.8:return 0,len(iq),{'operation':'burst window','trimmed':False,'resolution_s':block/sample_rate}
    # Bridge <=10 ms gaps, which otherwise split OOK symbols.
    bridge=max(2,int(np.ceil(sample_rate*.01/block))+1)
    active=ndimage.binary_closing(np.pad(active,(bridge,bridge)),structure=np.ones(bridge))[bridge:-bridge]
    changes=np.diff(np.r_[False,active,False].astype(int))
    groups=list(zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)))
    if not groups:return 0,len(iq),{'operation':'burst window','trimmed':False,'resolution_s':block/sample_rate}
    a,b=max(groups,key=lambda pair:energy[pair[0]:pair[1]].sum())
    lo=int(a*block);hi=min(len(iq),int(b*block))
    if hi-lo<4096 or hi-lo>len(iq)*.8:return 0,len(iq),{'operation':'burst window','trimmed':False,'resolution_s':block/sample_rate}
    return lo,hi,{'operation':'burst window','trimmed':True,'start_sample':lo,'stop_sample':hi,'resolution_s':block/sample_rate,'threshold_power_ratio':4}


def trim_quiet_edges(iq,sample_rate):
    """Crop a single stable-envelope transmission bordered by long quiet edges.

    Require >=20 ms quiet-edge evidence, no internal quiet blocks, and stable
    instantaneous envelope after a 4 ms guard. Rapid OOK and weak/noisy phase
    signals fail the stability gate. This handles high-duty recordings whose
    20th-percentile power is already signal, rather than a noise estimate.
    """
    n=len(iq);block=max(32,round(sample_rate*.002))
    operation={'operation':'stable transmission quiet-edge crop','trimmed':False,
               'resolution_s':block/sample_rate}
    if n<4096+round(sample_rate*.04):return 0,n,operation
    starts=np.arange(0,n,block)
    energy=np.array([np.mean(abs(iq[s:s+block])**2) for s in starts])
    reference=float(np.quantile(energy,.99))
    if reference<=1e-20:return 0,n,operation
    active=np.flatnonzero(energy>reference*.5)
    if not len(active):return 0,n,operation
    a,b=int(active[0]),int(active[-1])+1
    # Allow transition blocks next to the signal, but quiet evidence itself
    # must persist for 20 ms and be at least 13 dB below interior energy.
    edge_blocks=max(1,int(np.ceil(.02*sample_rate/block)))
    leading=a>=edge_blocks+2 and np.all(energy[:a-2]<reference*.05)
    trailing=len(energy)-b>=edge_blocks+2 and np.all(energy[b+2:]<reference*.05)
    if not (leading or trailing):return 0,n,operation
    if np.quantile(energy[a:b],.1)<reference*.5:return 0,n,operation
    guard=round(sample_rate*.004)
    lo=a*block+guard if leading else 0
    hi=min(n,b*block)-guard if trailing else n
    if hi-lo<4096:return 0,n,operation
    envelope=abs(iq[lo:hi]);cv=float(envelope.std()/max(envelope.mean(),1e-20))
    if cv>.18:return 0,n,operation
    operation.update(trimmed=True,start_sample=int(lo),stop_sample=int(hi),
                     stable_envelope_cv=cv,quiet_edge_power_ratio=.05,
                     minimum_quiet_edge_s=.02,guard_s=.004)
    return int(lo),int(hi),operation


def phase_recovery(iq,sample_rate,order):
    """Mth-power carrier estimator for PSK hypotheses only, not a general PLL."""
    x=np.asarray(iq)[:32768];amplitude=abs(x)
    unit=x/np.maximum(amplitude,np.quantile(amplitude,.3)*.5+1e-15)
    powered=unit**order;window=np.hanning(len(x));spectrum=abs(np.fft.fft(powered*window))**2
    k=int(np.argmax(spectrum));left=np.log(spectrum[(k-1)%len(x)]+1e-30);mid=np.log(spectrum[k]+1e-30);right=np.log(spectrum[(k+1)%len(x)]+1e-30)
    delta=float(np.clip(.5*(left-right)/(left-2*mid+right),-.5,.5)) if abs(left-2*mid+right)>1e-12 else 0.
    bin_hz=sample_rate/len(x);wrapped=(k+delta+len(x)/2)%len(x)-len(x)/2
    offset=wrapped*bin_hz/order
    corrected=compensate_carrier(iq,sample_rate,offset)
    coherent=float(spectrum[[(k-1)%len(x),k,(k+1)%len(x)]].sum()/max(spectrum.sum(),1e-30))
    return corrected,{'order':order,'offset_hz':float(offset),'peak_fraction':coherent,'ambiguity_interval_hz':sample_rate/order,'method':'Mth-power FFT with log-parabolic interpolation; PSK assumption'}


def estimate_symbol_timing(values,sample_rate):
    """Periodic transition-grid fit. Return UNKNOWN when no credible grid exists."""
    v=np.asarray(values)
    if len(v)<256 or not np.isfinite(v).all():return {'symbol_rate_hz':None,'support':0.,'method':'transition grid'}
    derivative=abs(np.diff(v));positive=derivative[derivative>max(float(np.max(derivative))*1e-6,1e-12)]
    scale=float(np.quantile(positive,.9)) if len(positive) else 0.
    if scale<1e-12:return {'symbol_rate_hz':None,'support':0.,'method':'transition grid'}
    peaks,_=signal.find_peaks(derivative,height=scale*.4,distance=3)
    if len(peaks)<12:return {'symbol_rate_hz':None,'support':0.,'method':'transition grid'}
    gaps=np.diff(peaks);candidates=np.unique(np.round(np.quantile(gaps,[.1,.2,.3,.4,.5])).astype(int))
    best=(0.,None,None)
    for period in candidates:
        if not 4<=period<=sample_rate/100:continue
        phase=np.mean(np.exp(2j*np.pi*peaks/period));support=float(abs(phase))
        if support>best[0]:best=(support,int(period),float(np.angle(phase)*period/(2*np.pi)%period))
    support,period,phase=best
    return {'symbol_rate_hz':float(sample_rate/period) if period and support>.75 else None,'samples_per_symbol':period if support>.75 else None,'timing_phase_sample':phase if support>.75 else None,'support':support,'method':'transition grid; integer-sample estimate, harmonics possible'}


def matched_filter(iq,samples_per_symbol):
    """Explicit rectangular-pulse hypothesis; never silently assume RRC parameters."""
    if type(samples_per_symbol) is not int or not 2<=samples_per_symbol<=4096:raise ValueError('Invalid rectangular matched-filter length')
    return signal.convolve(iq,np.ones(samples_per_symbol)/samples_per_symbol,mode='same',method='auto')


def recover_symbols(iq,sample_rate,symbol_rate_hz,pulse_shape='rectangular'):
    """Known-rate feed-forward timing search, with explicit pulse assumptions.

    This is not a blind clock-tracking loop. Fractional timing is interpolated;
    a flat eye metric returns no symbols rather than inventing a timing phase.
    """
    x=validate_iq(iq,sample_rate)
    if type(symbol_rate_hz) not in (int,float) or not np.isfinite(symbol_rate_hz) or not 100<=symbol_rate_hz<=sample_rate/4:
        raise ValueError('Known symbol rate must imply at least four samples/symbol')
    if pulse_shape!='rectangular':raise ValueError('Only an explicitly assumed rectangular pulse is supported')
    period=sample_rate/symbol_rate_hz
    filtered=matched_filter(x,int(round(period)))
    phases=np.linspace(0,period,32,endpoint=False)
    def sample(phase):
        points=np.arange(period*2+phase,len(filtered)-period*2,period)
        return np.interp(points,np.arange(len(filtered)),filtered.real)+1j*np.interp(points,np.arange(len(filtered)),filtered.imag)
    energies=np.array([np.mean(abs(sample(p))**2) for p in phases])
    contrast=float((energies.max()-energies.min())/max(energies.mean(),1e-30))
    phase=float(phases[int(np.argmax(energies))])
    operation={'operation':'known-rate rectangular matched filter and 32-phase timing search','symbol_rate_hz':symbol_rate_hz,'timing_phase_sample':phase,'samples_per_symbol':float(period),'eye_metric_contrast':contrast,'tracking':False}
    if contrast<.02:operation['status']='UNKNOWN';return np.empty(0,complex),operation
    operation['status']='HYPOTHESIS';return sample(phase),operation
