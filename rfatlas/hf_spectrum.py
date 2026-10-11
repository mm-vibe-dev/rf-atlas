"""Bounded V4 floating FFT measurements and nonfinite rtl_power fallback.

Two offset tunings cover each segment. Only measured interior FFT bins are
exported; neither the center notch nor the outer 20 percent is interpolated.
The existing driver performs HF conversion where needed; no direct sampling switch is used.
"""
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import tempfile

import numpy as np
from scipy import signal


def spectrum_rows(iq, rate, center, bin_hz):
    x = np.asarray(iq)
    if x.ndim != 1 or len(x) < 4096 or not np.isfinite(x).all():
        raise ValueError('Insufficient finite HF samples')
    nfft = max(128, min(32768, 2 ** math.ceil(math.log2(rate / bin_hz))))
    if len(x) < nfft:
        raise ValueError('Insufficient FFT samples')
    f, p = signal.welch(x, fs=rate, nperseg=nfft, return_onesided=False, detrend=False)
    order = np.argsort(f); f = f[order]; p = p[order]
    step = rate / nfft
    rows = []
    for lo, hi in ((-.4 * rate, -.025 * rate), (.025 * rate, .4 * rate)):
        keep = (f >= lo) & (f <= hi)
        centers = center + f[keep]
        powers = 10 * np.log10(np.maximum(p[keep], np.finfo(float).tiny))
        rows.append((float(centers[0]-step/2), float(centers[-1]+step/2), step, powers))
    return rows


def acquire(backend, path, low, high, bin_hz, duration, gain):
    if not 500000 <= low < high <= 1766000000 or high-low > 2000000:
        raise ValueError('Floating FFT backend requires a bounded receiver segment')
    if str(gain) == 'auto' or float(gain) <= 0:
        raise ValueError('Acquisition requires positive manual gain')
    if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=300:
        raise ValueError('HF duration must be in (0,300] seconds')
    rate = 2400000
    # For a 2 MHz segment these strips overlap and cover each other's DC notch.
    centers = [int(low+700000), int(low+1300000)]
    if centers[-1]>1766000000:centers=[int(high-1300000),int(high-700000)]
    rows = []
    diagnostics = []
    quality=[]
    with tempfile.TemporaryDirectory(prefix='rfatlas-hf-') as folder:
        for index, center in enumerate(centers):
            raw = Path(folder)/f'input{index}.cu8'
            remaining=duration;accumulated=None;chunk_index=0
            while remaining>0:
                chunk=min(8,remaining);count=round(rate*(chunk+.3))
                _, stderr = backend.run([backend.tool('rtl_sdr'), '-d', backend.selector,
                    '-f', str(center), '-s', str(rate), '-g', str(gain), '-n', str(count), str(raw)],
                    chunk+20, path.with_suffix(f'.hf{index}-{chunk_index}.command.json'))
                diagnostics.append(stderr)
                data=np.fromfile(raw,dtype=np.uint8)
                raw.unlink(missing_ok=True)
                if data.size!=count*2:raise ValueError('Incomplete bounded HF acquisition')
                data=data.reshape(-1,2)[round(.3*rate):]
                if np.all(data==data[0]):raise ValueError('Constant HF sample stream')
                endpoints=float(np.mean((data==0)|(data==255)))
                quality.append(dict(center_hz=center,adc_endpoint_fraction=endpoints,used_complex_samples=len(data)))
                if endpoints>.01:
                    from .diagnostics import atomic_json
                    atomic_json(path.with_suffix('.rejected.json'),dict(reason='ADC_CLIPPING',quality=quality))
                    from .measurement_errors import SpectrumMeasurementError
                    raise SpectrumMeasurementError('ADC_CLIPPING','HF measurement rejected: repeated ADC endpoints')
                data=data.astype(np.float32)
                iq=(data[:,0]-127.5+1j*(data[:,1]-127.5))/127.5
                current=spectrum_rows(iq,rate,center,bin_hz)
                if accumulated is None:
                    accumulated=[(lo,hi,step,10**(power/10)*chunk) for lo,hi,step,power in current]
                else:
                    for stored,measured in zip(accumulated,current):stored[3][:]+=10**(measured[3]/10)*chunk
                remaining-=chunk;chunk_index+=1
            rows.extend((lo,hi,step,10*np.log10(np.maximum(power/duration,np.finfo(float).tiny))) for lo,hi,step,power in accumulated)
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d,%H:%M:%S.%f+00:00').split(',')
    with Path(path).open('w', newline='', encoding='utf8') as stream:
        writer = csv.writer(stream)
        for start, stop, step, power in rows:
            writer.writerow([*stamp, start, stop, step, round(duration*rate), *power.tolist()])
    from .diagnostics import atomic_json
    atomic_json(path.with_suffix('.command.json'), dict(backend='V4_HF_FLOAT_WELCH' if high<=30000000 else 'V4_RF_FLOAT_WELCH',
        started=datetime.now(timezone.utc).isoformat(), stderr='Exact sample rate is: 2400000\n'+'\n'.join(diagnostics),
        quality=quality,settings=dict(rate=rate, centers_hz=centers, discard_s=.3, edge_crop=.2,
                      dc_exclusion_fraction=.025, duration_s=duration, gain=gain)))
