"""Original bounded DMR base-station CSBK receiver (metadata only).

ETSI TS 102 361-1: 4FSK, BS data sync, Golay slot type, BPTC(196,96),
masked CRC-CCITT. No voice, CRC-guided bit search, or cross-frame combining.
Capacity Plus opcode/FID semantics are limited to the rest-channel nibble.
"""
from fractions import Fraction
import binascii
import numpy as np
from scipy import signal
from .decoders import DecoderEvidence, RecoveredFrame

SYNC_BITS = np.unpackbits(np.frombuffer(bytes.fromhex('DFF57D75DF5D'), dtype=np.uint8))
LEVELS = np.array([1., 3., -1., -3.])
SYNC = LEVELS[SYNC_BITS.reshape(-1,2) @ np.array([2,1])]
ROW = np.array([9,11,15,7,14,5,10,13,3,6,12,1,2,4,8])
COL = np.array([15,7,14,5,10,13,3,6,12,1,2,4,8])


def golay_word(info):
    word = int(info) << 11
    remainder = word
    for bit in range(18,10,-1):
        if remainder & (1 << bit):
            remainder ^= 0xC75 << (bit-11)
    value = word | remainder
    return (value << 1) | (value.bit_count() & 1)


GOLAY = tuple(golay_word(i) for i in range(256))


def slot_type(bits):
    value = int(''.join(str(int(b)) for b in bits), 2)
    distances = [(value ^ w).bit_count() for w in GOLAY]
    distance = min(distances)
    if distance > 3 or distances.count(distance) != 1:
        return None
    info = distances.index(distance)
    return dict(color_code=info >> 4, frame_type=info & 15, slot_bit_corrections=distance)


def _syndrome(bits, columns):
    return int(np.bitwise_xor.reduce(columns[np.asarray(bits, dtype=bool)], initial=0))


def bptc_decode(payload):
    data = np.asarray(payload, dtype=np.uint8)[np.arange(196)*181 % 196].copy()
    matrix = data[1:].reshape(13,15)
    corrections = 0
    for _ in range(5):
        changed = False
        for vectors, columns in ((matrix.T, COL), (matrix[:9], ROW)):
            for vector in vectors:
                syndrome = _syndrome(vector, columns)
                indexes = np.flatnonzero(columns == syndrome) if syndrome else []
                if len(indexes) == 1:
                    vector[indexes[0]] ^= 1; corrections += 1; changed = True
        if not changed:
            break
    valid = all(_syndrome(v,COL)==0 for v in matrix.T) and all(_syndrome(v,ROW)==0 for v in matrix[:9])
    bits = np.concatenate((matrix[0,3:11], matrix[1:9,:11].reshape(-1)))
    return np.packbits(bits).tobytes(), valid, corrections


def csbk_crc_valid(data):
    return len(data)==12 and (binascii.crc_hqx(data[:10],0) ^ 0xFFFF ^ 0xA5A5)==int.from_bytes(data[10:],'big')


def parsed_csbk(data):
    if not csbk_crc_valid(data):
        raise ValueError('Invalid DMR CSBK CRC')
    fields = dict(frame_type=3, opcode=data[0]&63, feature_id=data[1])
    if fields['opcode']==62 and fields['feature_id']==16:
        fields.update(system_variant='MOTOROLA_CAPACITY_PLUS', rest_channel=data[2]&15)
    return fields


def discriminator(iq, rate):
    sos=signal.butter(6,7000,fs=rate,output='sos')
    channel=signal.sosfiltfilt(sos,iq-np.mean(iq))
    fm=np.angle(channel[1:]*np.conj(channel[:-1]))*rate/(2*np.pi)
    fm=signal.sosfiltfilt(signal.butter(5,6000,fs=rate,output='sos'),fm)
    ratio=Fraction(48000/rate).limit_denominator(10000)
    if abs(rate*ratio.numerator/ratio.denominator-48000)>.01:
        raise ValueError('Unsupported DMR resampling ratio')
    return signal.resample_poly(fm,ratio.numerator,ratio.denominator)


def sync_hits(fm):
    template=SYNC-SYNC.mean(); energy=float(template@template)
    hits=[]
    for phase in range(10):
        symbols=fm[phase::10]
        if len(symbols)<132:continue
        sums=np.convolve(symbols,np.ones(24),mode='valid')
        squares=np.convolve(symbols**2,np.ones(24),mode='valid')
        numerator=signal.correlate(symbols,template,mode='valid',method='direct')
        denominator=np.sqrt(np.maximum(squares-sums*sums/24,0)*energy)
        corr=np.divide(numerator,denominator,out=np.zeros_like(numerator),where=denominator>1e-12)
        scale=numerator/energy
        for index in np.flatnonzero((abs(corr)>=.9)&(abs(scale)>=400)&(abs(scale)<=900)):
            hits.append((phase+int(index)*10,float(abs(corr[index])),phase,int(index)))
    clustered=[]
    for hit in sorted(hits):
        if clustered and hit[0]-clustered[-1][0]<=20:
            if hit[1]>clustered[-1][1]:clustered[-1]=hit
        else:clustered.append(hit)
    return clustered[:1024]


class DMRDecoder:
    name='native-dmr-bs-csbk'
    version='1'
    capabilities=dict(protocols=['DMR'],modulations=['4FSK'],platforms=['Windows','Linux','Darwin'],
        frequency_hint_hz=(136000000,960000000),offline_replay=True,evidence='BS data sync + unique Golay <=3 + converged BPTC + CSBK CRC; no CRC repair',
        sample_rate_min_hz=48000,sample_rate_max_hz=2400000,expected_bandwidth_hz=12500,
        input_format='complex IQ',minimum_duration_s=.06,metadata=['color_code','frame_type','opcode','feature_id','rest_channel'],
        limitations='BS CSBK only; timeslot/LCN, voice, privacy flags and other variants are not decoded')

    def _center(self, recording, offset=0):
        iq=recording.iq
        if offset:iq=iq*np.exp(-2j*np.pi*offset*np.arange(len(iq))/recording.sample_rate)
        fm=discriminator(iq,recording.sample_rate)
        hits=sync_hits(fm); frames=[]; valid_count=0; synced=0
        for position,corr,phase,index in hits:
            symbols=fm[phase::10]
            if index<54 or index+78>len(symbols):continue
            observed=symbols[index:index+24]
            scale=float((observed-observed.mean())@(SYNC-SYNC.mean())/np.sum((SYNC-SYNC.mean())**2))
            residual_offset=float(observed.mean()-scale*SYNC.mean())
            burst=symbols[index-54:index+78]
            dibits=np.argmin(abs((burst[:,None]-residual_offset)/scale-LEVELS),axis=1)
            bits=np.column_stack((dibits>>1,dibits&1)).astype(np.uint8).reshape(-1)
            errors=int(np.count_nonzero(bits[108:156]!=SYNC_BITS))
            if errors>3:continue
            synced+=1
            slot=slot_type(np.r_[bits[98:108],bits[156:166]])
            if slot is None or slot['frame_type']!=3:continue
            data,fec_ok,corrections=bptc_decode(np.r_[bits[:98],bits[166:]])
            if not fec_ok or not csbk_crc_valid(data):continue
            valid_count+=1
            fields=dict(parsed_csbk(data),**{k:v for k,v in slot.items() if k!='frame_type'},
                sync_bit_errors=errors,bptc_corrections=corrections,symbol_rate_hz=4800,channel_offset_hz=float(offset))
            if len(frames)<256:
                sample=recording.start_sample+round((position-540)*recording.sample_rate/48000)
                frames.append(RecoveredFrame(data.hex(),sample,True,True,tuple(fields.items())))
        return len(hits),synced,valid_count,tuple(frames)

    def decode(self, recording):
        examined,synced,valid_count,frames=self._center(recording)
        offsets=[0.]
        if not valid_count:
            f,p=signal.welch(recording.iq[:131072],fs=recording.sample_rate,nperseg=4096,return_onesided=False,detrend=False)
            peak=f[int(np.argmax(p))]
            keep=abs(f-peak)<=7000
            offset=float(np.average(f[keep],weights=p[keep]))
            if 1000<abs(offset)<recording.sample_rate*.4:
                offsets.append(offset)
                a,b,c,frames=self._center(recording,offset)
                examined+=a;synced+=b;valid_count+=c
        return DecoderEvidence('DMR',recording.sample_sha256,examined,synced,valid_count,valid_count,
            'ETSI TS 102 361-1 BS data synchronization DFF57D75DF5D',
            'ETSI TS 102 361-1 CSBK CRC-CCITT complement and A5A5 mask',
            ('Only converged BPTC and direct CRC accepted; no checksum-guided repairs or combining.',
             'Capacity Plus fields describe transmitted control metadata, not physical identity or current activity.',
             'Internal measured channel offset hypotheses (Hz): '+str(offsets)),tuple(frames))
