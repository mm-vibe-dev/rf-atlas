"""Synthetic RF frames and output-boundary tests; no captured communications."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from rfatlas.decoders import DecoderInput, run_decoder
from rfatlas.external_decoders import RTL433Decoder, RTL433WH5Decoder


def wh2_iq(corrupt=False, false_sync=False, seed=7):
    """Author-generated WH2 frame: id18, 21 C, 50% humidity, CRC8 0x31."""
    payload = bytearray.fromhex('4120d232')
    remainder = 0
    for byte in payload:
        remainder ^= byte
        for _ in range(8):
            remainder = ((remainder << 1) ^ (0x31 if remainder & 128 else 0)) & 255
    frame = bytes([0x00 if false_sync else 0xff]) + payload + bytes([remainder ^ int(corrupt)])
    bits = np.unpackbits(np.frombuffer(frame, dtype=np.uint8))
    parts = [np.zeros(2500)]
    for bit in bits:
        parts.extend([np.full(125 if bit else 375, .75), np.zeros(250)])
    parts.append(np.zeros(2500))
    envelope = np.concatenate(parts)
    iq = envelope * np.exp(2j * np.pi * 10000 * np.arange(len(envelope)) / 250000)
    rng = np.random.default_rng(seed)
    iq += .005 * (rng.normal(size=len(iq)) + 1j * rng.normal(size=len(iq)))
    return DecoderInput(iq, 250000, 433920000)


def wh5_iq(corrupt=False, false_sync=False, seed=17):
    """Original WH5 synthetic: invented id, temperature offset encoding."""
    payload = bytes([0x43, 0x72, 0x62, 55])  # id55, 21 C (610), humidity55
    # Independent integer polynomial division rather than CRC shift register.
    dividend = int.from_bytes(payload, 'big') << 8
    while dividend.bit_length() >= 9:
        dividend ^= 0x131 << (dividend.bit_length() - 9)
    data = payload + bytes([dividend ^ int(corrupt)])
    bits = np.r_[np.full(7, int(not false_sync), dtype=np.uint8),
                 np.unpackbits(np.frombuffer(data, dtype=np.uint8))]
    parts = [np.zeros(2500)]
    for bit in bits:
        parts.extend([np.full(125 if bit else 375, .75), np.zeros(250)])
    parts.append(np.zeros(2500))
    envelope = np.concatenate(parts)
    iq = envelope * np.exp(2j * np.pi * 10000 * np.arange(len(envelope)) / 250000)
    rng = np.random.default_rng(seed)
    iq += .005 * (rng.normal(size=len(iq)) + 1j * rng.normal(size=len(iq)))
    return DecoderInput(iq, 250000, 433920000)


class ExternalDecoderTests(unittest.TestCase):
    def test_wh5_is_separate_audited_subset(self):
        valid = dict(model='Fineoffset-WH5', mic='CRC', id=55, temperature_C=21, humidity=55)
        with patch('rfatlas.external_decoders.shutil.which', return_value='trusted'):
            for event, expected in ((valid, True), (dict(valid, model='Fineoffset-WH2'), False),
                                    (dict(valid, mic='CHECKSUM'), False), (dict(valid, temperature_C=80), False),
                                    (dict(valid, humidity=True), False)):
                decoder = RTL433WH5Decoder(runner=lambda *args: (json.dumps(event), ''))
                result = run_decoder(decoder, wh5_iq())
                self.assertEqual(result['protocol_verified'], expected)
        self.assertEqual(RTL433WH5Decoder.capabilities['protocols'], ('Fineoffset-WH5',))

    @unittest.skipUnless(os.environ.get('RFATLAS_RTL433'), 'Optional rtl_433 integration binary not configured')
    def test_wh5_real_binary_and_wh2_isolation(self):
        decoder = RTL433WH5Decoder(os.environ['RFATLAS_RTL433'])
        self.assertTrue(run_decoder(decoder, wh5_iq())['protocol_verified'])
        self.assertFalse(run_decoder(decoder, wh5_iq(corrupt=True))['protocol_verified'])
        self.assertFalse(run_decoder(decoder, wh5_iq(false_sync=True))['protocol_verified'])
        self.assertFalse(run_decoder(decoder, wh2_iq())['protocol_verified'])
        self.assertFalse(run_decoder(RTL433Decoder(os.environ['RFATLAS_RTL433']), wh5_iq())['protocol_verified'])
        for seed in range(8):
            rng = np.random.default_rng(seed + 110)
            noise = .2 * (rng.normal(size=50000) + 1j * rng.normal(size=50000))
            self.assertFalse(run_decoder(decoder, DecoderInput(noise, 250000, 433920000))['protocol_verified'])

    def test_empty_config_file_input_and_cleanup(self):
        seen = []
        def runner(argv, timeout):
            spec=argv[argv.index('-r') + 1]
            self.assertTrue(spec.startswith('cu8:iq:250000sps:'))
            seen.append(Path(spec.split(':',3)[3]))
            self.assertTrue(seen[-1].exists())
            config = Path(argv[argv.index('-c') + 1])
            self.assertEqual(config.parent, seen[-1].parent)
            self.assertNotIn('-d', argv)
            self.assertEqual(argv[argv.index('-R') + 1], '18')
            return json.dumps({'model': 'Fineoffset-WH2', 'mic': 'CRC', 'id': 18,
                               'temperature_C': 21.0, 'humidity': 50}), ''
        with patch('rfatlas.external_decoders.shutil.which', return_value='trusted'):
            result = run_decoder(RTL433Decoder(runner=runner), wh2_iq())
        self.assertEqual(result['protocol'], 'Fineoffset-WH2')
        self.assertEqual(result['protection'], 'UNKNOWN')
        self.assertFalse(seen[-1].exists())

    def test_unverified_json_rejected(self):
        accepted = dict(model='Fineoffset-WH2', mic='CRC', id=18, temperature_C=21, humidity=50)
        variations = [dict(accepted, mic='CHECKSUM'), dict(accepted, model='Fineoffset-WH5'),
                      dict(accepted, id=True), dict(accepted, humidity=150),
                      dict(accepted, temperature_C=float('nan')), {'model': 'Fineoffset-WH2'}]
        output = '\n'.join(map(json.dumps, variations)) + '\n[]\nnot json'
        with patch('rfatlas.external_decoders.shutil.which', return_value='trusted'):
            result = run_decoder(RTL433Decoder(runner=lambda *args: (output, '')), wh2_iq())
        self.assertFalse(result['protocol_verified'])

    @unittest.skipUnless(os.environ.get('RFATLAS_RTL433'),'Optional real rtl_433 binary unavailable')
    def test_real_binary_ignores_rate_and_am_tokens_in_temporary_path(self):
        from contextlib import nullcontext
        import tempfile
        with tempfile.TemporaryDirectory(prefix='9k_am_') as folder:
            with patch('rfatlas.external_decoders.tempfile.TemporaryDirectory',return_value=nullcontext(folder)):
                for klass,fixture in ((RTL433Decoder,wh2_iq),(RTL433WH5Decoder,wh5_iq)):
                    decoder=klass(os.environ['RFATLAS_RTL433'])
                    self.assertTrue(run_decoder(decoder,fixture())['protocol_verified'])
                    self.assertFalse(run_decoder(decoder,fixture(corrupt=True))['protocol_verified'])

    def test_missing_binary_and_invalid_timeout(self):
        with patch('rfatlas.external_decoders.shutil.which', return_value=None):
            self.assertFalse(RTL433Decoder().available())
            with self.assertRaises(RuntimeError):
                RTL433Decoder().decode(wh2_iq())
        for timeout in (0, 31, float('nan')):
            with self.assertRaises(ValueError):
                RTL433Decoder(timeout=timeout)

    @unittest.skipUnless(os.environ.get('RFATLAS_RTL433'), 'Optional rtl_433 integration binary not configured')
    def test_real_binary_valid_corrupt_false_sync_and_noise(self):
        decoder = RTL433Decoder(os.environ['RFATLAS_RTL433'])
        self.assertTrue(run_decoder(decoder, wh2_iq())['protocol_verified'])
        self.assertFalse(run_decoder(decoder, wh2_iq(corrupt=True))['protocol_verified'])
        self.assertFalse(run_decoder(decoder, wh2_iq(false_sync=True))['protocol_verified'])
        rng = np.random.default_rng(41)
        for _ in range(8):
            x = .2 * (rng.normal(size=50000) + 1j * rng.normal(size=50000))
            self.assertFalse(run_decoder(decoder, DecoderInput(x, 250000, 433920000))['protocol_verified'])


if __name__ == '__main__':
    unittest.main()
