"""Bounded offline analog diagnostics; aggregate metrics, never audio output.

Mode is a caller-supplied hypothesis. A synthetic expected tone is a test
oracle, not permission to infer a communications protocol from real RF.
"""
import math

import numpy as np
from scipy import signal

from .recovery import validate_iq, compensate_carrier


ANALOG_MODES = ('AM', 'NFM', 'WFM', 'CW')
VERSION = '1.0.0'


def _finite_real(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite real number')
    return float(value)


def analyze_analog(iq, sample_rate, mode, *, frequency_offset_hz=0,
                   expected_test_tone_hz=None, channel_bandwidth_hz=None,
                   cw_beat_hz=700):
    """Return AM envelope/FM phase-difference/CW beat diagnostics.

    Accept 4096..1000000 finite complex samples. All filtering and demodulation
    are local and transient. No playback, persistence, protocol verification,
    or automatic voice/message interpretation occurs here.
    """
    x = validate_iq(iq, sample_rate)
    sample_rate = float(sample_rate)
    if mode not in ANALOG_MODES:
        raise ValueError('Unsupported analog hypothesis')
    offset = _finite_real(frequency_offset_hz, 'Frequency offset')
    if abs(offset) >= sample_rate / 2:
        raise ValueError('Frequency offset outside Nyquist interval')
    widths = {'AM': 12000., 'NFM': 16000., 'WFM': 200000., 'CW': 3000.}
    width = _finite_real(widths[mode] if channel_bandwidth_hz is None else channel_bandwidth_hz, 'Channel bandwidth')
    if not 0 < width < sample_rate:
        raise ValueError('Channel bandwidth must be positive and below sample rate')
    beat = _finite_real(cw_beat_hz, 'CW beat')
    if not 0 < beat < sample_rate * .4:
        raise ValueError('CW beat outside supported passband')
    expected = None
    if expected_test_tone_hz is not None:
        expected = _finite_real(expected_test_tone_hz, 'Expected synthetic tone')
        if not 0 < expected < min(15000., sample_rate * .4):
            raise ValueError('Expected tone outside diagnostic passband')
    # Scaling preserves envelope ratios and phase while preventing overflow
    # on otherwise finite, uncalibrated caller input.
    scale = float(max(np.max(abs(x.real)), np.max(abs(x.imag))))
    if scale:
        x /= scale
    x = compensate_carrier(x, sample_rate, offset)
    cutoff = min(width / 2, sample_rate * .45)
    sos = signal.butter(6, cutoff, fs=sample_rate, output='sos')
    x = signal.sosfiltfilt(sos, x)[128:-128]
    amplitude = abs(x)
    mean_amplitude = float(np.mean(amplitude))
    phase_frequency = np.angle(x[1:] * np.conjugate(x[:-1])) * sample_rate / (2 * np.pi)
    carrier = float(np.median(phase_frequency))
    warnings = []
    if mean_amplitude < 1e-12:
        warnings.append('No measurable input amplitude')
    if mode == 'AM':
        demodulated = amplitude - mean_amplitude
        operation = 'DC-removed magnitude envelope'
    elif mode in ('NFM', 'WFM'):
        demodulated = phase_frequency - carrier
        operation = 'conjugate-product phase discriminator; median carrier removed'
        warnings.append('FM discriminator requires adequate SNR; no stereo or broadcast subcarrier verification')
    else:
        t = np.arange(len(x)) / sample_rate
        demodulated = np.real(x * np.exp(2j * np.pi * beat * t))
        operation = 'explicit local beat oscillator; real component'
    audio_cutoff = min(15000. if mode == 'WFM' else 5000., sample_rate * .4)
    demodulated = signal.sosfiltfilt(signal.butter(4, audio_cutoff, fs=sample_rate, output='sos'), demodulated)[128:-128]
    demodulated -= np.mean(demodulated)
    frequency, psd = signal.welch(demodulated, fs=sample_rate, nperseg=min(8192, len(demodulated)))
    selected = (frequency > 0) & (frequency <= audio_cutoff)
    power = float(np.sum(psd[selected]))
    peak = float(frequency[np.flatnonzero(selected)[np.argmax(psd[selected])]]) if power > 1e-25 else None
    resolution = float(frequency[1] - frequency[0])
    concentration = None
    verified_test = None
    if expected is not None:
        if expected >= audio_cutoff:
            raise ValueError('Expected tone exceeds selected mode diagnostic bandwidth')
        tolerance = max(3 * resolution, expected * .015)
        concentration = float(np.sum(psd[selected & (abs(frequency - expected) <= tolerance)]) / power) if power > 1e-25 else 0.
        verified_test = bool(peak is not None and abs(peak - expected) <= tolerance and concentration >= .7 and mean_amplitude >= 1e-12)
    return {
        'analyzer': {'name': 'offline-analog-diagnostics', 'version': VERSION},
        'modulation_hypothesis': mode, 'hypothesis_source': 'caller supplied; not independently classified',
        'protocol': 'UNKNOWN', 'protocol_verified': False, 'protection': 'UNKNOWN',
        'demodulated_test_signal': verified_test,
        'test_oracle': 'explicit synthetic expected tone' if expected is not None else None,
        'sample_count': len(iq), 'sample_rate_hz': sample_rate,
        'channel_bandwidth_hz': width, 'frequency_offset_hz': offset,
        'operation': operation, 'edge_discard_samples': 256,
        'amplitude_units': 'peak I/Q component normalized; uncalibrated',
        'metrics': {'envelope_coefficient_of_variation': float(np.std(amplitude) / max(mean_amplitude, 1e-30)),
                    'residual_carrier_hz': carrier,
                    'frequency_deviation_rms_hz': float(np.std(phase_frequency)),
                    'carrier_coherence': float(abs(np.mean(x)) / max(mean_amplitude, 1e-30)),
                    'demodulated_rms': float(np.sqrt(np.mean(demodulated ** 2))),
                    'dominant_tone_hz': peak, 'tone_resolution_hz': resolution,
                    'expected_test_tone_hz': expected, 'expected_tone_power_fraction': concentration},
        'warnings': warnings,
        'limitations': ['Demodulation metrics are modulation evidence only.',
                       'Bandwidth and tuning frequency do not establish protocol identity.',
                       'Synthetic tone recovery does not validate real communications content.'],
    }
