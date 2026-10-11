"""Original non-operational AX.25 fixtures; independent FCS and modulation."""
import binascii
import unittest

import numpy as np

from rfatlas.decoders import DecoderInput, run_decoder
from rfatlas.packet import AX25AFSK1200Decoder, ax25_fcs


def reference_fcs(payload):
    reflected = bytes(int(f'{byte:08b}'[::-1], 2) for byte in payload)
    forward = binascii.crc_hqx(reflected, 0xffff)
    return int(f'{forward:016b}'[::-1], 2) ^ 0xffff


def frame(payload=b'>SYNTHETIC TEST ONLY', addresses=2, control=3, pid=0xf0):
    data = bytearray()
    for index in range(addresses):
        # Test-only invented tokens, never copied from an operational packet.
        data.extend(byte << 1 for byte in f'TEST{index}'.encode().ljust(6, b' '))
        data.append(0x60 | int(index == addresses - 1))
    data.extend((control, pid))
    data.extend(payload)
    return bytes(data) + reference_fcs(data).to_bytes(2, 'little')


def afsk_iq(data, rate=48000, start=241, noise=.002, seed=12,
            flags=True, baud=1200, carrier=700, invert=False):
    bits = []
    ones = 0
    for bit in np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder='little'):
        bits.append(int(bit))
        ones = ones + 1 if bit else 0
        if ones == 5:
            bits.append(0)
            ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    if flags:
        bits = flag * 16 + bits + flag * 4
    else:
        bits = [0] * 128 + bits + [0] * 32
    level = int(invert)
    tones = []
    for bit in bits:
        if bit == 0:
            level ^= 1
        tones.append(1200 if level else 2200)
    length = int(np.ceil(len(tones) * rate / baud))
    index = np.minimum((np.arange(length) * baud / rate).astype(int), len(tones) - 1)
    audio = np.sin(2 * np.pi * np.cumsum(np.array(tones)[index]) / rate)
    frequency = np.r_[np.zeros(start), audio * 2500, np.zeros(200)] + carrier
    iq = .7 * np.exp(2j * np.pi * np.cumsum(frequency) / rate)
    if len(iq) < 4096:
        iq = np.pad(iq, (0, 4096 - len(iq)), constant_values=.7)
    rng = np.random.default_rng(seed)
    return iq + noise * (rng.normal(size=len(iq)) + 1j * rng.normal(size=len(iq)))


class PacketTests(unittest.TestCase):
    def decode(self, iq, rate=48000):
        return run_decoder(AX25AFSK1200Decoder(), DecoderInput(iq, rate, 145000000))

    def test_known_crc_and_independent_fcs(self):
        self.assertEqual(ax25_fcs(b'123456789'), 0x906e)
        for seed in range(25):
            data = np.random.default_rng(seed).integers(0, 256, 200, dtype=np.uint8).tobytes()
            self.assertEqual(ax25_fcs(data), reference_fcs(data))

    def test_valid_iq_rates_offsets_stuffing_and_nrzi_polarity(self):
        for rate in (24000, 48000, 250000, 1024000):
            for start in (241, 258):
                with self.subTest(rate=rate, start=start):
                    recording = DecoderInput(afsk_iq(frame(b'\xff' * 30), rate, start,
                                                     invert=bool(start % 2)), rate, 145000000, 100)
                    result = run_decoder(AX25AFSK1200Decoder(), recording)
                    self.assertTrue(result['protocol_verified'])
                    self.assertEqual(result['protocol'], 'AX.25')
                    self.assertEqual(result['protection'], 'UNKNOWN')
                    records = AX25AFSK1200Decoder().frame_records(recording)
                    self.assertEqual(len(records), 1)
                    self.assertEqual(set(records[0]), {'start_sample', 'frame_bytes', 'address_count', 'control', 'pid', 'fcs_valid'})

    def test_invalid_crc_sync_structure_and_rate(self):
        good = frame()
        bad = bytearray(good)
        bad[-1] ^= 1
        for data, flags in ((bytes(bad), True), (good, False),
                            (frame(addresses=1), True), (frame(control=0x13), True),
                            (frame(pid=0xcc), True), (frame(payload=b''), True)):
            with self.subTest(data=data, flags=flags):
                self.assertFalse(self.decode(afsk_iq(data, flags=flags))['protocol_verified'])
        self.assertFalse(self.decode(afsk_iq(good, baud=2400))['protocol_verified'])
        self.assertFalse(self.decode(afsk_iq(good)[:4500])['protocol_verified'])
        malformed = bytearray(good[:-2])
        malformed[0] = ord('!') << 1
        malformed.extend(reference_fcs(malformed).to_bytes(2, 'little'))
        self.assertFalse(self.decode(afsk_iq(malformed))['protocol_verified'])
        self.assertFalse(self.decode(afsk_iq(frame(payload=b'x' * 257)))['protocol_verified'])

    def test_identical_later_frames_not_collapsed_and_budget_is_enforced(self):
        one = afsk_iq(frame())
        recording = DecoderInput(np.r_[one, one], 48000, 145000000)
        result = run_decoder(AX25AFSK1200Decoder(), recording)
        self.assertEqual(result['evidence']['frames_with_sync_and_valid_checksum'], 2)
        late = DecoderInput(np.r_[np.ones(48000), one], 48000, 145000000)
        result = run_decoder(AX25AFSK1200Decoder(max_symbols=1000), late)
        self.assertFalse(result['protocol_verified'])
        self.assertIn('budget_exhausted=True', result['evidence']['notes'][-1])

    def test_false_sync_crc_corpus(self):
        accepted = 0
        for seed in range(50):
            payload = np.random.default_rng(seed).integers(0, 256, 30, dtype=np.uint8).tobytes()
            damaged = bytearray(frame(payload))
            damaged[-1] ^= 1 << (seed % 8)
            result = self.decode(afsk_iq(damaged, seed=seed))
            self.assertGreater(result['evidence']['frames_with_sync'], 0)
            accepted += result['protocol_verified']
        self.assertEqual(accepted, 0)

    def test_noise_and_other_modulations(self):
        accepted = 0
        for seed in range(50):
            rng = np.random.default_rng(seed)
            n = 12000
            for iq in (rng.normal(size=n) + 1j * rng.normal(size=n),
                       np.exp(2j * np.pi * 1200 * np.arange(n) / 48000),
                       rng.choice([-1., 1.], n).astype(complex)):
                accepted += self.decode(iq)['protocol_verified']
        self.assertEqual(accepted, 0)

    def test_budgets_and_input_gates(self):
        for kwargs in ({'max_symbols': 0}, {'max_frames': 0}, {'max_symbols': True}):
            with self.assertRaises(ValueError):
                AX25AFSK1200Decoder(**kwargs)
        iq = afsk_iq(frame())
        for rate, frequency in ((12000, 145000000),):
            with self.assertRaises(ValueError):
                AX25AFSK1200Decoder().decode(DecoderInput(iq, rate, frequency))
        self.assertTrue(run_decoder(AX25AFSK1200Decoder(),DecoderInput(iq,48000,450000000))['protocol_verified'])
        result = run_decoder(AX25AFSK1200Decoder(max_symbols=1000), DecoderInput(iq, 48000, 145000000))
        self.assertIn('symbol_limit=1000', result['evidence']['notes'][-1])


if __name__ == '__main__':
    unittest.main()
