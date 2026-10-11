"""Deterministic block-energy activity with explicit resolution and uncertainty."""
import numpy as np
from .features import extract_features


def analyze_activity(iq,sample_rate,channel_bandwidth_hz=None):
    samples=np.asarray(iq)
    if samples.ndim!=1 or len(samples)<4096 or not np.isfinite(samples).all() or not np.isfinite(sample_rate) or sample_rate<=0:
        raise ValueError('Invalid IQ activity input')
    block=max(256,int(round(sample_rate*.01)))
    starts=np.arange(0,len(samples),block)
    energy=np.array([float(np.mean(np.abs(samples[a:a+block])**2)) for a in starts])
    floor=max(float(np.quantile(energy,.2)),1e-30)
    excess=10*np.log10(np.maximum(energy,1e-30)/floor)
    active=excess>6
    if channel_bandwidth_hz is not None:
        from scipy import signal
        if not np.isfinite(channel_bandwidth_hz) or not 0<channel_bandwidth_hz<sample_rate/3:
            raise ValueError('Channel bandwidth must be positive and below one third of sample rate')
        block=4096;starts=np.arange(0,len(samples)-block+1,block)
        f,_,psd=signal.spectrogram(samples,fs=sample_rate,nperseg=block,noverlap=0,return_onesided=False,detrend=False)
        channel=np.abs(f)<channel_bandwidth_hz/2
        guards=(np.abs(f)>channel_bandwidth_hz)&(np.abs(f)<channel_bandwidth_hz*1.5)
        if not channel.any() or not guards.any():raise ValueError('Channel bandwidth is below FFT resolution')
        energy=psd[channel].mean(axis=0);noise=np.maximum(psd[guards].mean(axis=0),1e-30)
        excess=10*np.log10(np.maximum(energy,1e-30)/noise);floor=float(noise.mean());active=excess>6
    # Uniform power is ambiguous until a representative spectrum supports RF.
    if not active.any() and channel_bandwidth_hz is None:
        features=extract_features(samples[:min(len(samples),int(max(4096,sample_rate*.5)))],sample_rate)
        present=features['peak_snr_db']>=10 and features['spectral_flatness']<.65
        if present:active[:]=True
        behavior='CONTINUOUS' if present else 'UNKNOWN'
    else:behavior='CONTINUOUS' if active.all() else ('INTERMITTENT' if active.any() else 'UNKNOWN')
    changes=np.diff(np.r_[False,active,False].astype(int))
    events=[]
    for a,b in zip(np.flatnonzero(changes==1),np.flatnonzero(changes==-1)):
        lo=int(starts[a]);hi=min(len(samples),int(b*block))
        events.append({'start_sample':lo,'stop_sample':hi,'start_s':lo/sample_rate,'duration_s':(hi-lo)/sample_rate})
    if behavior=='INTERMITTENT' and events and max(e['duration_s'] for e in events)<.1:behavior='BURSTY'
    duty=sum(e['stop_sample']-e['start_sample'] for e in events)/len(samples)
    selected=max(events,key=lambda e:float(np.mean(np.abs(samples[e['start_sample']:e['stop_sample']])**2))) if events else None
    lo=selected['start_sample'] if selected else 0
    hi=min(selected['stop_sample'] if selected else len(samples),lo+int(max(4096,sample_rate*2)))
    if hi-lo<4096:
        lo=max(0,min(lo,len(samples)-4096));hi=min(len(samples),max(hi,lo+4096))
    return {'behavior':behavior,'method':'channel versus guard PSD' if channel_bandwidth_hz is not None else 'relative whole-band block energy','channel_bandwidth_hz':channel_bandwidth_hz,'energy_activity_detected':bool(events),'active_fraction_estimate':duty,'resolution_s':block/sample_rate,'noise_reference_power':floor,'threshold_excess_db':6,'events':events,'selected_start_sample':lo,'selected_stop_sample':hi,'block_times_s':(starts/sample_rate).tolist(),'block_excess_db':excess.tolist(),'limitations':'Energy estimate, not protocol airtime; guard interference, uniform noise and sub-block bursts remain ambiguous; whole-band energy can miss narrow bursts'}
