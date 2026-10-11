import json
import unittest

import numpy as np

from rfatlas.analog import analyze_analog


class AnalogTests(unittest.TestCase):
    def synthetic(self, mode, tone=1100., offset=1700.):
        rate = 250000
        t = np.arange(50000) / rate
        if mode == 'AM':
            x = (1 + .6 * np.cos(2 * np.pi * tone * t)).astype(complex)
        elif mode in ('NFM', 'WFM'):
            deviation = 2500. if mode == 'NFM' else 50000.
            x = np.exp(1j * deviation / tone * np.sin(2 * np.pi * tone * t))
        else:
            x = np.ones(len(t), dtype=complex)
        x *= np.exp(2j * np.pi * offset * t)
        return x, rate

    def test_bounded_synthetic_demodulation_and_no_protocol_claims(self):
        for mode in ('AM', 'NFM', 'WFM', 'CW'):
            with self.subTest(mode=mode):
                x, rate = self.synthetic(mode)
                original = x.copy()
                tone = 700. if mode == 'CW' else 1100.
                result = analyze_analog(x, rate, mode, frequency_offset_hz=1700, expected_test_tone_hz=tone)
                self.assertTrue(result['demodulated_test_signal'])
                self.assertEqual(result['protocol'], 'UNKNOWN')
                self.assertFalse(result['protocol_verified'])
                self.assertEqual(result['protection'], 'UNKNOWN')
                np.testing.assert_array_equal(x, original)
                json.dumps(result, allow_nan=False)
                self.assertNotIn('audio', result)

    def test_oracle_absence_and_wrong_tone(self):
        x, rate = self.synthetic('AM')
        self.assertIsNone(analyze_analog(x, rate, 'AM')['demodulated_test_signal'])
        self.assertFalse(analyze_analog(x, rate, 'AM', frequency_offset_hz=1700, expected_test_tone_hz=3000)['demodulated_test_signal'])

    def test_evidence_distinguishes_envelope_and_frequency_variation(self):
        metrics = {}
        for mode in ('AM', 'NFM', 'WFM', 'CW'):
            x, rate = self.synthetic(mode)
            metrics[mode] = analyze_analog(x, rate, mode, frequency_offset_hz=1700)['metrics']
        self.assertGreater(metrics['AM']['envelope_coefficient_of_variation'], .35)
        self.assertLess(metrics['NFM']['envelope_coefficient_of_variation'], .01)
        self.assertLess(metrics['CW']['frequency_deviation_rms_hz'], .01)
        self.assertGreater(metrics['NFM']['frequency_deviation_rms_hz'], 1700)
        self.assertGreater(metrics['WFM']['frequency_deviation_rms_hz'], 30000)
        x, rate = self.synthetic('AM')
        large = analyze_analog(x * 1e300, rate, 'AM')
        json.dumps(large, allow_nan=False)

    def test_noise_and_silence_do_not_recover_expected_tone(self):
        rng = np.random.default_rng(91642)
        for x in (np.zeros(20000, complex), rng.normal(size=20000) + 1j * rng.normal(size=20000)):
            for mode in ('AM', 'NFM', 'WFM', 'CW'):
                with self.subTest(mode=mode):
                    self.assertFalse(analyze_analog(x, 250000, mode, expected_test_tone_hz=1100)['demodulated_test_signal'])

    def test_reject_invalid_and_excessive_inputs(self):
        x, rate = self.synthetic('AM')
        for kwargs in ({'mode': 'DMR'}, {'mode': 'AM', 'frequency_offset_hz': float('nan')},
                       {'mode': 'AM', 'channel_bandwidth_hz': rate}, {'mode': 'AM', 'cw_beat_hz': True},
                       {'mode': 'AM', 'expected_test_tone_hz': 6000}):
            with self.assertRaises(ValueError):
                analyze_analog(x, rate, **kwargs)
        for data in (np.ones(1000, complex), np.ones(1000001, complex), np.ones(4096), np.full(4096, complex(float('nan'), 0))):
            with self.assertRaises(ValueError):
                analyze_analog(data, rate, 'AM')
        with self.assertRaises(ValueError):
            analyze_analog(x, 48000, 'WFM')


if __name__ == '__main__':
    unittest.main()
