"""Original, bounded FM RDS receive-only subset. No error correction or text inference.

Requires two adjacent complete groups with repeated PI and all eight CRC/offset
checks. RDS PI is a broadcast-system identifier, never a physical-transmitter ID.
"""
from fractions import Fraction
import numpy as np
from scipy.signal import firwin, lfilter, resample_poly
from .decoders import DecoderEvidence, RecoveredFrame

OFFSETS=(0xFC,0x198,0x168,0x1B4)


def remainders(words):
    """Polynomial division by x^10+x^8+x^7+x^5+x^4+x^3+1 (0x5B9)."""
    remainder=np.array(words,dtype=np.uint32,copy=True)
    for bit in range(25,9,-1):
        remainder^=((remainder>>bit)&1)*np.uint32(0x5B9<<(bit-10))
    return remainder


def groups_from_bits(bits):
    bits=np.asarray(bits,dtype=np.uint8)
    if bits.ndim!=1 or len(bits)>10000 or np.any(bits>1):raise ValueError('Invalid bounded RDS bits')
    if len(bits)<208:return []
    words=np.lib.stride_tricks.sliding_window_view(bits,26) @ (1<<np.arange(25,-1,-1,dtype=np.uint32))
    checks=remainders(words);groups={}
    for start in np.flatnonzero(checks==OFFSETS[0]):
        if start+104>len(bits):continue
        indices=start+np.arange(4)*26;w=words[indices];data=w>>10
        version_b=bool(int(data[1])&0x800)
        expected=np.array([0xFC,0x198,0x350 if version_b else 0x168,0x1B4])
        if not np.array_equal(checks[indices],expected):continue
        if version_b and data[2]!=data[0]:continue
        groups[int(start)]=dict(start=int(start),words=w,data=data)
    accepted=set()
    for start,group in groups.items():
        following=groups.get(start+104)
        if following is not None and following['data'][0]==group['data'][0]:
            accepted.update((start,start+104))
    return [groups[start] for start in sorted(accepted)]


def parsed_groups(groups):
    names={}
    for group in groups:
        a,b,c,d=map(int,group['data'])
        if b>>12==0:
            text=bytes([d>>8,d&255])
            if all(32<=ch<=126 for ch in text):
                key=(a,b&3,text.decode('ascii'));names[key]=names.get(key,0)+1
    result=[]
    for group in groups:
        a,b,c,d=map(int,group['data'])
        fields=dict(pi=f'{a:04X}',group_type=f'{b>>12}{"B" if b&0x800 else "A"}',category='broadcast-system')
        segments=[]
        for index in range(4):
            choices=[text for (pi,segment,text),count in names.items() if pi==a and segment==index and count>=2]
            if len(choices)!=1:break
            segments.append(choices[0])
        if len(segments)==4:fields['program_service']=''.join(segments)
        raw=sum(int(word)<<(26*(3-index)) for index,word in enumerate(group['words']))
        result.append(RecoveredFrame(raw.to_bytes(13,'big').hex().upper(),None,parsed_fields=tuple(fields.items())))
    return tuple(result[:64])


class RDSDecoder:
    name='native-fm-rds'
    version='1.3.0'
    capabilities=dict(protocols=['RDS'],modulations=['WFM'],platforms=['Windows','Linux','Darwin'],
        offline_replay=True,evidence='Two adjacent complete CRC/offset-valid groups with repeated PI',
        frequency_hint_hz=(87500000,108000000),
        sample_rate_min_hz=171000,sample_rate_max_hz=2400000,expected_bandwidth_hz=200000)

    def decode(self,recording):
        x=recording.iq
        if not 171000<=recording.sample_rate<=2400000:raise ValueError('RDS input rate unsupported')
        if len(x)>4_000_000:raise ValueError('RDS sample budget exceeded')
        # Wide follow-ups can contain adjacent stations. Channelize the already
        # translated target before nonlinear FM discrimination, preserving the
        # multiplex at 228 kS/s (114 kHz complex channel half-bandwidth).
        ratio=Fraction(228000,int(recording.sample_rate))
        if recording.sample_rate>=500000:
            input_power=float(np.mean(np.abs(x)**2))
            x=resample_poly(x,ratio.numerator,ratio.denominator)
            if float(np.mean(np.abs(x[64:-64])**2))<input_power*.001:
                return DecoderEvidence('RDS',recording.sample_sha256,
                    notes=('Target channel power more than 30 dB below wide input; insufficient channel evidence.',))
            mpx=np.angle(x[1:]*x[:-1].conj())
        else:
            mpx=resample_poly(np.angle(x[1:]*x[:-1].conj()),ratio.numerator,ratio.denominator)
        t=np.arange(len(mpx))/228000
        mixed=mpx*np.exp(-2j*np.pi*57000*t)
        base=resample_poly(lfilter(firwin(129,3000,fs=228000),[1],mixed),1,12)
        # Squaring removes BPSK sign. Smooth phase tracks small subcarrier drift.
        squared=lfilter(firwin(257,100,fs=19000),[1],base*base)
        aligned=base[128:-128]*np.exp(-.5j*np.unwrap(np.angle(squared[256:]))) if len(base)>256 else np.array([])
        # Align the delayed phase estimate with its corresponding baseband sample.
        best=[];examined=0
        for phase in range(16):
            usable=(len(aligned)-phase)//16
            if usable<209:continue
            chunks=aligned[phase:phase+usable*16].reshape(-1,16)
            states=(np.real(chunks[:,:8].sum(1)-chunks[:,8:].sum(1))>0).astype(np.uint8)
            # Both differential polarities search complete 104-bit group windows.
            # Verification still requires an adjacent pair, not a single group.
            bits=states[1:]^states[:-1];examined+=2*max(0,len(bits)-103)
            for candidate in (bits,1-bits):
                found=groups_from_bits(candidate[:10000])
                if len(found)>len(best):best=found
        frames=parsed_groups(best)
        return DecoderEvidence('RDS',recording.sample_sha256,examined,len(frames),len(frames),len(frames),
            '57 kHz BPSK, 1187.5 bit/s differential Manchester; sequential A/B/C or C-prime/D offsets; repeated PI',
            '26-bit blocks: CRC-10 polynomial 0x5B9; offsets FC/198/168 (350 C-prime)/1B4; no correction',
            ('Fixed symbol rate and bounded timing search; weak signals or large clock errors can remain UNKNOWN.',
             'PI identifies a broadcast system. PS requires all four segments repeated consistently twice. Radio text not supported.'),frames)
