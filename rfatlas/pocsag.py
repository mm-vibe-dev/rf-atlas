"""Original strict POCSAG batch recognizer, ITU-R M.584-2 Annex 1.

No error correction, address extraction or message interpretation. Expose only
aggregate batch integrity/rate. A sync word alone never verifies a protocol.
"""
from fractions import Fraction
import numpy as np
from scipy import signal,ndimage
from .decoders import DecoderEvidence,DecoderInput,RecoveredFrame

SYNC=0x7CD215D8
IDLE=0x7A89C197
POLYNOMIAL=0x769
SYNC_BITS=np.unpackbits(np.frombuffer(SYNC.to_bytes(4,'big'),dtype=np.uint8)).astype(np.int8)


def valid_word(word):
    if type(word) is not int or not 0<=word<2**32:return False
    if word.bit_count()%2:return False
    remainder=word>>1
    for bit in range(30,9,-1):
        if remainder&(1<<bit):remainder^=POLYNOMIAL<<(bit-10)
    return remainder==0


def batches(bits):
    """Accept all 16 BCH+even-parity words after exact sync, with framing.

    Require a complete 576-bit alternating preamble immediately before sync,
    or a second fully valid adjacent batch to establish late-capture framing.
    Returned payloads contain no word bits, pager addresses or message text.
    """
    b=np.asarray(bits)
    if b.ndim!=1 or len(b)>120000 or b.dtype.kind not in 'biuf' or np.any((b!=0)&(b!=1)):raise ValueError('Invalid bounded paging bitstream')
    b=b.astype(np.int8)
    if len(b)<544:return [],0,0
    hits=np.flatnonzero(signal.correlate(b*2-1,SYNC_BITS*2-1,mode='valid',method='direct')==32)
    examined=0;valid={}
    for position in hits[:256]:
        p=int(position)
        if p+544>len(b):continue
        examined+=1
        words=[int.from_bytes(v.tobytes(),'big') for v in np.packbits(b[p+32:p+544]).reshape(16,4)]
        if all(valid_word(w) for w in words):valid[p]=sum(w==IDLE for w in words)
    accepted=[]
    for p,idles in valid.items():
        preamble=p>=576 and bool(np.all(b[p-575:p]!=b[p-576:p-1]))
        adjacent=p+544 in valid or p-544 in valid
        if preamble or adjacent:accepted.append(dict(bit_offset=p,idle_words=idles))
    return accepted,examined,len(valid)


class POCSAGDecoder:
    name='native-pocsag-strict-batch'
    version='1'
    capabilities=dict(protocols=['POCSAG'],modulations=['2FSK','FSK_LIKE','NFM'],
        frequency_hint_hz=(130000000,950000000),sample_rate_min_hz=24000,sample_rate_max_hz=2400000,
        minimum_duration_s=.45,expected_bandwidth_hz=16000,
        unmatched_priority_penalty=2,
        platforms=['Windows','Linux','Darwin'],offline_replay=True,external_dependencies=[],
        input='Centered 2-FSK IQ, 512/1200/2400 baud; complete preamble/batch or two adjacent valid batches',
        evidence=['Exact batch sync','16 direct BCH(31,21)+even-parity codewords','576-bit preamble or adjacent complete batch'],
        limitations='No BCH repair, message/address extraction, FLEX or encryption assessment; tested fixtures only, no live paging validation')

    def decode(self,recording):
        if not isinstance(recording,DecoderInput):raise ValueError('Expected validated DecoderInput')
        rate=recording.sample_rate
        if not 24000<=rate<=2400000 or rate!=int(rate):raise ValueError('Unsupported POCSAG sample rate')
        x=recording.iq[:4_000_000]
        fm=np.angle(x[1:]*x[:-1].conj())*rate/(2*np.pi)
        ratio=Fraction(19200,int(rate))
        fm=signal.resample_poly(fm,ratio.numerator,ratio.denominator)
        frames=[];examined=0;synced=0
        for baud in (512,1200,2400):
            sps=19200/baud
            if len(fm)/sps<1088:continue
            smooth=ndimage.uniform_filter1d(fm,size=max(1,round(sps*.6)))
            low,high=np.quantile(smooth,[.1,.9]);center=(low+high)/2
            if high-low<500:continue
            for phase in range(16):
                points=np.arange(sps*phase/16,len(smooth),sps)[:120000]
                bits=(np.interp(points,np.arange(len(smooth)),smooth)>center).astype(np.int8)
                for polarity in (bits,1-bits):
                    found,seen,integrity=batches(polarity);examined+=seen;synced+=seen
                    if found:
                        for batch in found[:64]:
                            sample=recording.start_sample+round((points[batch['bit_offset']]/19200)*rate)
                            frames.append(RecoveredFrame(None,sample,True,True,(('symbol_rate_hz',baud),('validated_codewords',16),('idle_codewords',batch['idle_words']))))
                        # One successful timing/polarity prevents duplicate
                        # evidence inflation across the search bank.
                        return DecoderEvidence('POCSAG',recording.sample_sha256,examined,synced,len(frames),len(frames),
                            'ITU-R M.584-2 Annex 1 exact SC and preamble/adjacent batch',
                            'ITU-R M.584-2 Annex 1 polynomial x10+x9+x8+x6+x5+x3+1; direct syndrome zero and even parity for all 16 words',
                            ('Strict batch subset; no error repair or message/address extraction.','Frame integrity is BCH+parity, not a CRC. No live validation claimed.'),tuple(frames))
        return DecoderEvidence('POCSAG',recording.sample_sha256,examined,synced,0,0,
            'ITU-R M.584-2 Annex 1 exact SC','BCH(31,21)+even parity on complete framed batch',
            ('No complete valid framed batch; failure does not establish protocol absence or encryption.',))
