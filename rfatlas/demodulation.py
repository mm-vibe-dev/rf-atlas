"""Finite digital symbol/bit hypotheses, independent of protocol verification.

Selected rectangular ASK/FSK/PSK paths. Baud is an explicit hypothesis, never
inferred exact timing. No arbitrary encoding search, payload or FEC guessing.
"""
from fractions import Fraction
import hashlib
import math
import numpy as np
from scipy.signal import resample_poly

VERSION='digital-demod-1'
MODES=('OOK','2FSK','4FSK','BPSK','QPSK')


def capabilities():
    return [dict(modulation=m,input_format='finite complex IQ',sample_rate_min_hz=4000,
        samples_per_symbol_minimum=4,symbol_rate_hz=[50,100000],bandwidth='Operator-selected isolated channel; no adjacent-signal separation claimed',
        carrier_recovery='Median discriminator center' if 'FSK' in m else 'Mth-power FFT; ambiguity unresolved' if 'PSK' in m else 'Envelope only',
        timing_recovery='8 bounded integrate-and-dump phase hypotheses; explicit baud',
        output='Candidate symbols, binary mapping and relative reliability',max_input_samples=262144,max_output_symbols=2048,
        validation='GENERATED_RECTANGULAR_FIXTURES',limitations='No generic pulse-shaping, GMSK, QAM or protocol/frame verification') for m in MODES]


def demodulate(iq,rate,mode,baud,*,frequency_offset_hz=0,max_symbols=2048):
    x=np.asarray(iq)
    if mode not in MODES or x.ndim!=1 or not np.iscomplexobj(x) or not 4096<=len(x)<=4_000_000 or not np.isfinite(x).all():raise ValueError('Finite compatible complex IQ required')
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (rate,baud,frequency_offset_hz)) or not 50<=baud<=100000 or rate<baud*4 or abs(frequency_offset_hz)>=rate/2:raise ValueError('Unsupported timing/frequency hypothesis')
    if type(max_symbols) is not int or not 16<=max_symbols<=2048:raise ValueError('Bounded symbol output required')
    x=x[:262144];source=hashlib.sha256(np.asarray(x,dtype='<c16').tobytes()).hexdigest();t=np.arange(len(x))/rate;x=x*np.exp(-2j*np.pi*frequency_offset_hz*t)
    ratio=Fraction(float(baud*8/rate)).limit_denominator(4096)
    if max(ratio.numerator,ratio.denominator)>4096:raise ValueError('Resampling ratio exceeds budget')
    y=resample_poly(x,ratio.numerator,ratio.denominator);actual_rate=rate*ratio.numerator/ratio.denominator
    if abs(actual_rate/(baud*8)-1)>.0001:raise ValueError('Timing ratio cannot be represented accurately')
    cfo=0.;order={'OOK':2,'2FSK':2,'4FSK':4,'BPSK':2,'QPSK':4}[mode]
    if mode.endswith('PSK'):
        normalized=y/np.maximum(abs(y),1e-12);n=min(len(y),32768);spectrum=abs(np.fft.fft(normalized[:n]**order*np.hanning(n)))
        cfo=float(np.fft.fftfreq(n,1/actual_rate)[int(np.argmax(spectrum))]/order);y*=np.exp(-2j*np.pi*cfo*np.arange(len(y))/actual_rate)
        rotation=np.angle(np.mean((y/np.maximum(abs(y),1e-12))**order))/order;y*=np.exp(-1j*rotation)
        values=y
    elif 'FSK' in mode:
        values=np.angle(y[1:]*np.conj(y[:-1]))*actual_rate/(2*np.pi);cfo=float(np.median(values));values=values-cfo
    else:values=abs(y)
    best=None
    for phase in range(8):
        count=min(max_symbols,(len(values)-phase)//8)
        if count<16:continue
        blocks=values[phase:phase+count*8].reshape(-1,8);symbols=blocks.mean(1)
        if mode.endswith('PSK'):
            centers=np.exp(2j*np.pi*np.arange(order)/order);norm=symbols/np.maximum(abs(symbols),1e-12);dist=abs(norm[:,None]-centers)
        else:
            centers=np.quantile(symbols,np.linspace(.1,.9,order))
            for _ in range(4):
                labels=np.argmin(abs(symbols[:,None]-centers),axis=1);centers=np.array([np.mean(symbols[labels==k]) if np.any(labels==k) else centers[k] for k in range(order)])
            centers.sort();dist=abs(symbols[:,None]-centers)
        decisions=np.argmin(dist,axis=1);scale=max(float(np.mean(abs(centers)**2)),1e-12);error=float(np.mean(dist[np.arange(count),decisions]**2)/scale)
        if best is None or error<best[0]:best=(error,phase,decisions,dist,centers)
    if best is None:raise ValueError('Insufficient candidate symbols')
    error,phase,decisions,dist,centers=best;width=1 if order==2 else 2;bits=((decisions[:,None]>>np.arange(width-1,-1,-1))&1).astype(np.uint8).ravel();sorted_distance=np.sort(dist,axis=1)
    reliability=(sorted_distance[:,1]-sorted_distance[:,0])/np.maximum(sorted_distance[:,1],1e-12)
    return dict(version=VERSION,status='BIT_CANDIDATE',modulation=mode,source_prefix_sha256=source,sample_rate_hz=rate,
        requested_symbol_rate_hz=baud,symbol_rate_basis='EXPLICIT_HYPOTHESIS',frequency_offset_hz=frequency_offset_hz+cfo,
        timing_phase_samples_at_8sps=phase,timing_quality='UNVERIFIED',normalized_decision_error=error,
        symbols=decisions.tolist(),candidate_bits=bits.tolist(),relative_reliability=reliability.tolist(),
        mapping='Natural binary, MSB first; FSK levels ascending; PSK phases counterclockwise',
        polarity_and_phase_ambiguity='UNRESOLVED',synchronization='NOT_PROTOCOL_VERIFIED',protocol_verified=False,
        chain=['explicit frequency translation','bounded polyphase resampling','envelope/discriminator/Mth-power carrier hypothesis','eight timing phases','integrate and dump','natural binary candidates'],
        limitations=['Only isolated rectangular fixture paths validated.','Baud, bit polarity, PSK phase and coding hypotheses require independent protocol framing.','Relative decision margins are not calibrated likelihoods; arbitrary bits are not messages.'])
