"""Original deterministic baseband fixtures; no real transmissions or captures."""
import numpy as np

FAMILIES=['AM','NFM','WFM','OOK','2FSK','4FSK','BPSK','QPSK','CW','NOISE']


def generate(family,rate=250000,duration=.12,snr_db=30,offset=0,gain=.5,clipping=False,burst=False,seed=1):
    rng=np.random.default_rng(seed);n=int(round(rate*duration));t=np.arange(n)/rate
    if family=='CW':x=np.ones(n,complex)
    elif family=='AM':x=(1+.6*np.cos(2*np.pi*1000*t))*np.exp(2j*np.pi*5000*t)
    elif family in ['NFM','WFM']:
        deviation=2500 if family=='NFM' else 30000
        x=np.exp(2j*np.pi*np.cumsum(deviation*np.sin(2*np.pi*1000*t))/rate)
    elif family=='OOK':x=np.repeat(rng.integers(0,2,(n+199)//200),200)[:n]*np.exp(2j*np.pi*5000*t)
    elif family in ['2FSK','4FSK']:
        values=[-2500,2500] if family=='2FSK' else [-3750,-1250,1250,3750]
        width=max(2,round(rate/4800));frequencies=np.repeat(rng.choice(values,(n+width-1)//width),width)[:n]
        x=np.exp(2j*np.pi*np.cumsum(frequencies)/rate)
    elif family in ['BPSK','QPSK']:
        width=max(2,round(rate/4800));order=2 if family=='BPSK' else 4
        x=np.exp(2j*np.pi*np.repeat(rng.integers(0,order,(n+width-1)//width),width)[:n]/order)
    elif family=='NOISE':x=np.zeros(n,complex)
    else:raise ValueError('Unsupported synthetic family')
    reference=max(float(np.mean(np.abs(x)**2)),1.)
    if burst:x*=((t>=duration*.65)&(t<duration*.85))
    x*=np.exp(2j*np.pi*offset*t)
    sigma=0 if snr_db is None else np.sqrt(reference/(2*10**(snr_db/10)))
    if family=='NOISE':sigma=.3
    x=gain*(x+sigma*(rng.normal(size=n)+1j*rng.normal(size=n)))
    if clipping:x=np.clip(x.real,-1,1)+1j*np.clip(x.imag,-1,1)
    return x
