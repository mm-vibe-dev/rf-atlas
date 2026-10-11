"""Independent, bounded synthetic RF corpus; no original generator dependencies.

Labels are synthetic construction truth, not claims about recorded signals.
"""
import numpy as np
from scipy import signal

FAMILIES = ('AM', 'NFM', 'WFM', 'OOK', '2FSK', '4FSK', 'BPSK', 'QPSK', 'CW', 'NOISE')
SPLIT_SEEDS = {'development': 110000, 'validation': 220000, 'holdout': 330000}
PROFILES = ('clean', 'offset_drift', 'burst', 'low_gain', 'clipped', 'quantized')


def cases(split='development', seeds_per_cell=2):
    """Return saved-config-ready cases; generating metadata does not evaluate IQ."""
    if split not in SPLIT_SEEDS or not 1 <= seeds_per_cell <= 20:
        raise ValueError('Unknown split or invalid bounded seed count')
    result = []
    for fi, family in enumerate(FAMILIES):
        for si, snr in enumerate((0, 12, 28)):
            for pi, profile in enumerate(PROFILES):
                for repeat in range(seeds_per_cell):
                    seed = SPLIT_SEEDS[split] + fi * 2000 + si * 500 + pi * 50 + repeat
                    rng = np.random.default_rng(seed)
                    rate = int(rng.choice((128000, 256000, 384000)))
                    duration = float(rng.choice((.064, .128, .24)))
                    result.append({'id': f'{split}-{seed}', 'split': split, 'seed': seed,
                        'family': family, 'target': 'UNKNOWN' if family == 'NOISE' else family,
                        'snr_db': snr, 'profile': profile, 'sample_rate': rate,
                        'duration_s': duration, 'symbol_rate': float(rng.choice((1200, 2400, 4000, 6000))),
                        'timing_offset': float(rng.random()), 'phase': float(rng.uniform(-np.pi, np.pi)),
                        'carrier_hz': float(rng.uniform(-18000, 18000)),
                        'deviation_hz': float(rng.uniform(1500, 4200)),
                        'am_depth': float(rng.uniform(.4, .85)),
                        'audio_hz': float(rng.uniform(350, 1800))})
    return result


def generate_case(case):
    """Integrate frequency trajectories and independently pulse-shape symbols."""
    rng = np.random.default_rng(case['seed'])
    rate = case['sample_rate']; n = round(rate * case['duration_s'])
    t = np.arange(n) / rate
    family = case['family']; baud = case['symbol_rate']; sps = rate / baud
    index = np.floor(np.arange(n) / sps + case['timing_offset']).astype(int)
    symbols = rng.integers(0, 4, index[-1] + 2)
    # Windowed raised-cosine FIR, normalized at DC. Symbols are generated at
    # fractional timing positions; filtering is deliberately unlike the old
    # rectangular integer-repeat waveform generator.
    width = max(3, round(sps * .45))
    pulse = signal.windows.hann(width); pulse /= pulse.sum()
    if family == 'CW':
        x = np.ones(n, complex)
    elif family == 'AM':
        audio = .8 * np.cos(2*np.pi*case['audio_hz']*t) + .2*np.cos(2*np.pi*case['audio_hz']*1.73*t + .4)
        x = (1 + case['am_depth']*audio).astype(complex)
    elif family in ('NFM', 'WFM'):
        audio = .72*np.sin(2*np.pi*case['audio_hz']*t) + .28*np.sin(2*np.pi*case['audio_hz']*1.43*t + .9)
        deviation = case['deviation_hz'] if family == 'NFM' else 32000 + case['deviation_hz']
        x = np.exp(2j*np.pi*np.cumsum(audio*deviation)/rate)
    elif family == 'OOK':
        envelope = signal.convolve((symbols[index] % 2).astype(float), pulse, mode='same')
        x = envelope.astype(complex)
    elif family in ('2FSK', '4FSK'):
        levels = np.array((-1, 1)) if family == '2FSK' else np.array((-3, -1, 1, 3))
        frequency = levels[symbols[index] % len(levels)] * case['deviation_hz']
        frequency = signal.convolve(frequency, pulse, mode='same')
        x = np.exp(2j*np.pi*np.cumsum(frequency)/rate)
    elif family in ('BPSK', 'QPSK'):
        order = 2 if family == 'BPSK' else 4
        states = np.exp(2j*np.pi*(symbols[index] % order)/order)
        x = signal.convolve(states, pulse, mode='same')
    elif family == 'NOISE':
        x = np.zeros(n, complex)
    else:
        raise ValueError('Unsupported synthetic family')
    reference = max(float(np.mean(abs(x)**2)), .2)
    profile = case['profile']
    carrier = case['carrier_hz']
    drift = 0 if profile != 'offset_drift' else rng.uniform(-3500, 3500)
    x *= np.exp(1j*(case['phase'] + 2*np.pi*(carrier*t + drift*t*t/(2*case['duration_s']))))
    if profile == 'burst':
        # Two unequal bursts, with at least one late event.
        gate = ((t > .18*t[-1]) & (t < .34*t[-1])) | ((t > .71*t[-1]) & (t < .94*t[-1]))
        x *= gate
    elif profile in ('duty_low','duty_high'):
        start,stop=(.75,.80) if profile=='duty_low' else (.10,.95)
        x *= (t>=start*case['duration_s']) & (t<stop*case['duration_s'])
    sigma = np.sqrt(reference/10**(case['snr_db']/10)/2)
    if family == 'NOISE': sigma = .35
    x += sigma*(rng.normal(size=n)+1j*rng.normal(size=n))
    x *= .035 if profile == 'low_gain' else (.95 if profile == 'clipped' else .28)
    if profile in ('clipped', 'quantized'):
        x = np.clip(x.real, -1, 1) + 1j*np.clip(x.imag, -1, 1)
    if profile == 'quantized':
        bits = 4 if case['seed'] % 2 else 8
        levels = 2**(bits-1)-1
        x = np.round(x.real*levels)/levels + 1j*np.round(x.imag*levels)/levels
    return np.asarray(x, np.complex64)


def stress_cases(split='development'):
    """Supplemental long/duty-cycle cases; original 360 configs stay unchanged."""
    base=[c for c in cases(split,1) if c['snr_db']==28 and c['profile']=='clean']
    result=[]
    for fi,c in enumerate(base):
        for pi,profile in enumerate(('long','duty_low','duty_high')):
            seed=SPLIT_SEEDS[split]+40000+fi*100+pi
            result.append(dict(c,id=f'{split}-stress-{seed}',seed=seed,profile=profile,
                sample_rate=128000,duration_s=1.2,
                duty_fraction=1. if profile=='long' else (.05 if profile=='duty_low' else .85)))
    return result
