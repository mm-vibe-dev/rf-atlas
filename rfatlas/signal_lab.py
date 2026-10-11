"""Bounded real retained-IQ plot arrays; trusted catalog IDs only."""
from contextlib import closing
from pathlib import Path
import sqlite3
import numpy as np
from scipy import signal


def view(root,signal_id):
    root=Path(root).resolve()
    if not isinstance(signal_id,str) or not 1<=len(signal_id)<=128:raise ValueError('Invalid signal')
    with closing(sqlite3.connect(root/'data/rf_atlas.sqlite')) as db:
        row=db.execute('SELECT capture_id,path FROM captures WHERE signal_id=? ORDER BY rowid DESC LIMIT 1',(signal_id,)).fetchone()
    if not row:return dict(available=False,reason='No retained IQ for this signal. Transient investigation measurements remain in its evidence history.')
    path=Path(row[1]).resolve()
    try:path.relative_to(root/'data/captures')
    except ValueError:raise ValueError('Recording outside capture directory') from None
    from .iq import read_recording
    iq,meta=read_recording(path,max_bytes=64*1024*1024,max_samples=65536)
    x=iq[:65536];rate=meta['sample_rate'];n=len(x)
    f,p=signal.welch(x,rate,nperseg=512,return_onesided=False,detrend=False)
    wf,t,psd=signal.spectrogram(x,rate,nperseg=256,noverlap=0,return_onesided=False,detrend=False)
    t=t[:128];psd=psd[:,:128]
    edges=np.linspace(0,n,257,dtype=int)
    amp=[float(np.mean(np.abs(x[a:b]))) for a,b in zip(edges,edges[1:])]
    inc=np.angle(x[1:]*np.conj(x[:-1]));freq=inc*rate/(2*np.pi)
    frequency=[float(np.median(freq[a:min(b,len(freq))])) for a,b in zip(edges,edges[1:])]
    phase=[float(np.angle(np.mean(np.exp(1j*inc[a:min(b,len(inc))])))) for a,b in zip(edges,edges[1:])]
    return dict(available=True,capture_id=row[0],source_sha256=meta['sha256'],sample_count=n,duration_s=n/rate,
        sample_rate_hz=rate,representation='Raw received IQ translated to desired frequency; no symbol or carrier synchronization',
        spectrum=dict(offset_hz=np.fft.fftshift(f).tolist(),relative_psd_db=(10*np.log10(np.maximum(np.fft.fftshift(p),1e-30))).tolist()),
        waterfall=dict(time_s=t.tolist(),offset_hz=np.fft.fftshift(wf).tolist(),relative_psd_db=(10*np.log10(np.maximum(np.fft.fftshift(psd,axes=0).T,1e-30))).tolist()),
        time_s=(edges[:-1]/rate).tolist(),amplitude_mean=amp,instantaneous_frequency_median_hz=frequency,phase_increment_circular_mean_rad=phase,
        limitations=['Only finite retained prefix; relative uncalibrated power.',
            'Instantaneous frequency/phase are raw measurements and unreliable at low envelope.',
            'No valid synchronized constellation or eye diagram is available.'])
