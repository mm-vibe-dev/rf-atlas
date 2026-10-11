"""Optional bounded local plots from cataloged, checksummed recordings."""
import hashlib
from pathlib import Path


def prepare_plots(db, root, limit=8):
    """Generate latest-capture PNGs at startup, never accept browser paths."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return {}, {}
    import numpy as np
    from scipy import signal
    from .iq import read_recording
    artifacts, by_signal = {}, {}
    directory = Path(root) / 'runtime' / 'dashboard-plots'
    directory.mkdir(parents=True, exist_ok=True)
    rows = db.execute('SELECT signal_id,path FROM captures WHERE signal_id IS NOT NULL ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
    for signal_id, path in rows:
        if signal_id in by_signal:
            continue
        try:
            iq, meta = read_recording(Path(path))
            iq = iq[:min(len(iq), 262144)]
            rate = meta['sample_rate']
            frequencies, power = signal.welch(iq, rate, nperseg=1024, return_onesided=False)
            f, t, spectrum = signal.spectrogram(iq, rate, nperseg=512, noverlap=256, return_onesided=False)
            identifier = hashlib.sha256((meta['sha256']+'dashboard-v1').encode()).hexdigest()
            target = directory / (identifier+'.png')
            fig, axes = plt.subplots(2, 1, figsize=(8, 5), constrained_layout=True)
            axes[0].plot(np.fft.fftshift(frequencies)/1000, 10*np.log10(np.maximum(np.fft.fftshift(power), 1e-30)))
            axes[0].set(xlabel='Offset from desired frequency (kHz)', ylabel='Relative PSD (dB/Hz)')
            axes[1].pcolormesh(t, np.fft.fftshift(f)/1000, 10*np.log10(np.maximum(np.fft.fftshift(spectrum, axes=0), 1e-30)), shading='auto')
            axes[1].set(xlabel='Time in capture prefix (s)', ylabel='Offset (kHz)')
            fig.savefig(target, dpi=110); plt.close(fig)
            artifacts[identifier] = target
            by_signal[signal_id] = [{'id': identifier, 'label': 'Capture prefix spectrum and spectrogram; relative, uncalibrated power'}]
        except (ValueError, OSError, KeyError):
            continue
    return artifacts, by_signal
