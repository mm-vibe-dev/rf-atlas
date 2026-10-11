"""Original synthetic frames, with independently constructed parity/waveforms."""
import unittest
import numpy as np

from rfatlas.decoders import DecoderInput, run_decoder
from rfatlas.protocols import ModeSDecoder, modes_crc24


def make_frame(seed=1, df=17):
    rng = np.random.default_rng(seed)
    payload = bytearray(rng.integers(0, 256, 11, dtype=np.uint8))
    payload[0] = (df << 3) | 5
    # Independent polynomial long division using integer bit lengths, rather
    # than the production decoder's bit-stream shift register.
    dividend = int.from_bytes(payload, 'big') << 24
    divisor = sum(1 << exponent for exponent in
                  [24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 10, 3, 0])
    while dividend.bit_length() >= divisor.bit_length():
        dividend ^= divisor << (dividend.bit_length() - divisor.bit_length())
    return bytes(payload) + dividend.to_bytes(3, 'big')


def ppm_iq(frame, rate=2400000, start=501, noise=.005, seed=11,
           preamble=True, truncated=False):
    rng = np.random.default_rng(seed)
    n = 4096
    t = (np.arange(n) - start + .5) / rate * 1000000
    envelope = np.zeros(n)
    if preamble:
        for pulse in [0., 1., 3.5, 4.5]:
            envelope[(t >= pulse) & (t < pulse + .5)] = .8
    for index, bit in enumerate(np.unpackbits(np.frombuffer(frame, dtype=np.uint8))):
        pulse = 8 + index + (.5 if bit == 0 else 0)
        envelope[(t >= pulse) & (t < pulse + .5)] = .8
    if truncated:
        envelope[t >= 60] = 0
    phase = np.exp(2j * np.pi * 47000 * np.arange(n) / rate)
    return envelope * phase + noise * (rng.normal(size=n) + 1j * rng.normal(size=n))


class NativeProtocolTests(unittest.TestCase):
    def test_independent_crc_vectors_and_single_bit_errors(self):
        for seed in range(12):
            frame = make_frame(seed)
            self.assertEqual(modes_crc24(frame), 0)
            for bit in range(112):
                damaged = bytearray(frame)
                damaged[bit // 8] ^= 1 << (7 - bit % 8)
                self.assertNotEqual(modes_crc24(damaged), 0)

    def test_actual_ppm_decode_rates_offsets_and_noise(self):
        decoder = ModeSDecoder()
        for rate in [2000000, 2400000, 4000000, 10000000]:
            for offset in [500, 501, 502, 503, 504]:
                with self.subTest(rate=rate, offset=offset):
                    frame = make_frame(offset)
                    recording = DecoderInput(ppm_iq(frame, rate, offset), rate, 1090000000, 123)
                    result = run_decoder(decoder, recording)
                    self.assertTrue(result['protocol_verified'])
                    self.assertEqual(result['protocol'], 'ADS-B')
                    records = decoder.frame_records(recording)
                    self.assertEqual(records[0]['hex'], frame.hex().upper())
                    self.assertLessEqual(abs(records[0]['start_sample']-(offset+123)), 1)
                    self.assertEqual(result['input_sample_sha256'], recording.sample_sha256)

    def test_noise_false_positive_measurement(self):
        accepted = 0
        for seed in range(100):
            rng = np.random.default_rng(seed)
            iq = rng.normal(size=4096) + 1j * rng.normal(size=4096)
            accepted += run_decoder(ModeSDecoder(), DecoderInput(iq, 2400000, 1090000000))['protocol_verified']
        self.assertEqual(accepted, 0)

    def test_valid_preamble_random_payload_does_not_verify_protocol(self):
        # Deliberately harder than Gaussian noise: exactly correct timing and
        # preamble plus random DF17-looking payload/parity without a checksum.
        accepted = 0
        for seed in range(100):
            rng = np.random.default_rng(seed)
            frame = bytearray(rng.integers(0, 256, 14, dtype=np.uint8))
            frame[0] = (17 << 3) | 5
            self.assertNotEqual(modes_crc24(frame), 0)
            result = run_decoder(ModeSDecoder(), DecoderInput(ppm_iq(bytes(frame)), 2400000, 1090000000))
            self.assertGreater(result['evidence']['frames_with_sync'], 0)
            accepted += result['protocol_verified']
        self.assertEqual(accepted, 0)

    def test_invalid_crc_false_sync_truncation_and_wrong_df(self):
        good = make_frame(20)
        bad = bytearray(good)
        bad[-1] ^= 1
        for iq in [ppm_iq(bytes(bad)), ppm_iq(good, preamble=False),
                   ppm_iq(good, truncated=True), ppm_iq(make_frame(20, df=18)),
                   np.ones(4096, dtype=complex)]:
            self.assertFalse(run_decoder(ModeSDecoder(), DecoderInput(iq, 2400000, 1090000000))['protocol_verified'])

    def test_incompatible_input_and_budgets(self):
        iq = ppm_iq(make_frame())
        with self.assertRaises(ValueError):
            ModeSDecoder().decode(DecoderInput(iq, 250000, 1090000000))
        # Frequency is context only for offline framing; integrity is decisive.
        self.assertGreater(ModeSDecoder().decode(DecoderInput(iq, 2400000, 100000000)).frames_with_sync_and_valid_checksum,0)
        for kwargs in [{'max_candidates': 0}, {'max_frames': True}]:
            with self.assertRaises(ValueError):
                ModeSDecoder(**kwargs)

    def test_frame_cannot_borrow_checksum_from_second_transmission(self):
        frame = make_frame(8)
        first = ppm_iq(frame, start=500, noise=0, truncated=True)
        second = ppm_iq(frame, start=1400, noise=0, preamble=False)
        recording = DecoderInput(first+second, 2400000, 1090000000)
        self.assertFalse(run_decoder(ModeSDecoder(), recording)['protocol_verified'])

    def test_duplicate_frames_are_not_counted_as_timing_trials(self):
        frame = make_frame(6)
        iq = ppm_iq(frame, start=500, noise=0) + ppm_iq(frame, start=1700, noise=0)
        result = run_decoder(ModeSDecoder(), DecoderInput(iq, 2400000, 1090000000))
        self.assertEqual(result['evidence']['frames_with_sync_and_valid_checksum'], 2)


if __name__ == '__main__':
    unittest.main()
