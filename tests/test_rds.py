"""Independently constructed RDS CRC blocks, multiplex and FM IQ."""
import unittest
import numpy as np
from rfatlas.rds import RDSDecoder, groups_from_bits, remainders, parsed_groups
from rfatlas.decoders import DecoderInput, run_decoder


def encode(data,offset):
    # Scalar long division, independent of the production vectorized checker.
    value=data<<10
    for shift in reversed(range(16)):
        if value&(1<<(shift+10)):value^=0x5B9<<shift
    return (data<<10)|(value^offset)


def fixture_bits():
    words=[]
    for repeat in range(3):
        for segment,text in enumerate((b'TE',b'ST',b'DE',b'MO')):
            # Four two-character PS segments, fixed PI, group 0A.
            for data,offset in zip((0x1234,segment,0,int.from_bytes(text[:2],'big')),(0xFC,0x198,0x168,0x1B4)):
                words.append(encode(data,offset))
    return np.array([int(bit) for word in words for bit in f'{word:026b}'],dtype=np.uint8)


def fixture_iq(bits,rate=228000):
    states=np.bitwise_xor.accumulate(np.r_[0,bits])
    half=np.repeat(2*states.astype(float)-1,96)
    manchester=np.column_stack((half.reshape(-1,96),-half.reshape(-1,96))).reshape(-1)
    t=np.arange(len(manchester))/rate
    mpx=.05*manchester*np.cos(2*np.pi*57000*t+.63)+.08*np.cos(2*np.pi*19000*t)+.25*np.sin(2*np.pi*700*t)
    return np.exp(1j*np.cumsum(mpx))


class RDSTests(unittest.TestCase):
    def test_wide_input_channelizes_target_before_adjacent_station_discrimination(self):
        from scipy.signal import resample_poly
        desired=resample_poly(fixture_iq(fixture_bits()),256,57)
        t=np.arange(len(desired))/1024000
        adjacent=2*np.exp(2j*np.pi*300000*t+2j*np.sin(2*np.pi*900*t))
        result=run_decoder(RDSDecoder(),DecoderInput(desired+adjacent,1024000,98000000))
        self.assertTrue(result['protocol_verified'])
        self.assertEqual({f['parsed_fields']['pi'] for f in result['frames']},{'1234'})
        adjacent_rds=desired*np.exp(2j*np.pi*300000*t)
        rejected=run_decoder(RDSDecoder(),DecoderInput(adjacent_rds,1024000,98000000))
        self.assertFalse(rejected['protocol_verified'])

    def test_offset_sequence_and_repeated_pi_required(self):
        bits=fixture_bits();groups=groups_from_bits(bits)
        self.assertEqual(len(groups),12)
        frames=parsed_groups(groups)
        self.assertEqual(dict(frames[0].parsed_fields)['pi'],'1234')
        self.assertIn('program_service',dict(frames[0].parsed_fields))
        self.assertEqual(dict(frames[0].parsed_fields)['program_service'],'TESTDEMO')
        self.assertEqual(len(bytes.fromhex(frames[0].raw_hex)),13)
        self.assertFalse(groups_from_bits(bits[:104]))
        damaged=bits.copy();damaged[::104]^=1
        self.assertFalse(groups_from_bits(damaged))
        reordered=bits.reshape(-1,26)[::-1].reshape(-1)
        self.assertFalse(groups_from_bits(reordered))

    def test_independent_fm_iq_recovery_and_noise_rejection(self):
        result=run_decoder(RDSDecoder(),DecoderInput(fixture_iq(fixture_bits()),228000,98000000))
        self.assertTrue(result['protocol_verified'])
        self.assertEqual(result['candidate_protocol'],'RDS')
        rng=np.random.default_rng(914)
        for x in (np.ones(200000,complex),rng.normal(size=200000)+1j*rng.normal(size=200000)):
            result=run_decoder(RDSDecoder(),DecoderInput(x,228000,98000000))
            self.assertFalse(result['protocol_verified'])
