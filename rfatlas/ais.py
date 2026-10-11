"""Original bounded AIS GMSK/HDLC receiver, ITU-R M.1371 type 1/2/3 subset.

Only 168-bit Class A position reports with valid FCS produce metadata. No
name lookup, vessel inference, error repair or payload text interpretation.
"""
from fractions import Fraction
import numpy as np
from scipy import signal
from .packet import ax25_fcs
from .decoders import DecoderEvidence,RecoveredFrame


def parsed_ais(frame):
    if len(frame)!=23 or ax25_fcs(frame[:-2])!=int.from_bytes(frame[-2:],'little'):
        raise ValueError('Invalid AIS position frame CRC/length')
    bits=np.unpackbits(np.frombuffer(frame[:-2],dtype=np.uint8))
    def uint(start,length):return int(''.join(str(int(b)) for b in bits[start:start+length]),2)
    def signed(start,length):
        value=uint(start,length)
        return value-(1<<length) if value&(1<<(length-1)) else value
    kind=uint(0,6);mmsi=uint(8,30)
    if kind not in (1,2,3) or not 1<=mmsi<=999999999:raise ValueError('Unsupported AIS message')
    fields=dict(message_type=kind,mmsi=f'{mmsi:09d}',repeat=uint(6,2),navigation_status=uint(38,4),category='marine-ais')
    lon=signed(61,28)/600000;lat=signed(89,27)/600000
    if abs(lon)<=180 and abs(lat)<=90:fields.update(longitude=lon,latitude=lat)
    speed=uint(50,10);course=uint(116,12)
    if speed<1023:fields['speed_over_ground_knots']=speed/10
    if course<3600:fields['course_over_ground_degrees']=course/10
    return fields


class AISDecoder:
    name='native-ais-position'
    version='1'
    capabilities=dict(protocols=['AIS'],modulations=['GMSK','2FSK'],platforms=['Windows','Linux','Darwin'],
        frequency_hint_hz=(156000000,163000000),offline_replay=True,evidence='9600 baud NRZI HDLC flags/stuffing + CRC-16/X25 + type 1/2/3 structure',
        input_format='centered complex IQ',sample_rate_min_hz=48000,sample_rate_max_hz=2400000,
        expected_bandwidth_hz=25000,minimum_duration_s=.03,metadata=['mmsi','message_type','latitude','longitude'],
        limitations='Fixed clock phases, single-channel 168-bit Class A position reports only')

    def decode(self, recording):
        rate=recording.sample_rate
        ratio=Fraction(48000/rate).limit_denominator(100000)
        x=signal.resample_poly(recording.iq[:min(len(recording.iq),round(rate*8))],ratio.numerator,ratio.denominator)
        fm=np.angle(x[1:]*x[:-1].conj());fm-=np.median(fm)
        filtered=np.convolve(fm,np.ones(3)/3,mode='same')
        flag=np.array([0,1,1,1,1,1,1,0],dtype=np.uint8)
        frames=[];seen=[];examined=sync=0
        for phase in range(5):
            levels=filtered[phase::5]>0
            bits=(levels[1:]==levels[:-1]).astype(np.uint8)
            if len(bits)<8:continue
            windows=np.lib.stride_tricks.sliding_window_view(bits,8)
            flags=np.flatnonzero(np.all(windows==flag,axis=1))
            for left,right in zip(flags[:-1],flags[1:]):
                raw=bits[left+8:right]
                if not 184<=len(raw)<=224:continue
                examined+=1;unstuffed=[];ones=0;malformed=False
                for bit in raw:
                    if ones==5:
                        if bit:malformed=True;break
                        ones=0;continue
                    unstuffed.append(bit);ones=ones+1 if bit else 0
                if malformed or ones==5 or len(unstuffed)!=184:continue
                sync+=1
                frame=np.packbits(np.array(unstuffed,dtype=np.uint8),bitorder='little').tobytes()
                try:fields=parsed_ais(frame)
                except ValueError:continue
                position=recording.start_sample+round((phase+(int(left)+9)*5)*rate/48000)
                if any(data==frame and abs(position-old)<rate/9600*4 for data,old in seen):continue
                seen.append((frame,position))
                frames.append(RecoveredFrame(frame.hex(),position,True,True,tuple(fields.items())))
                if len(frames)>=64:break
            if len(frames)>=64:break
        return DecoderEvidence('AIS',recording.sample_sha256,examined,sync,len(frames),len(frames),
            'ITU-R M.1371: 9600 baud NRZI, HDLC 7E flags and zero stuffing',
            'ISO/IEC 13239 CRC-16/X25, reflected 8408, init/xorout FFFF',
            ('Only type 1/2/3 168-bit position reports; MMSI identifies protocol address, not independently verified vessel identity.',),tuple(frames))
