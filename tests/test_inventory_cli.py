"""Software-only subprocess checks; configured receiver tools cannot exist."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from rfatlas.platforms import DeviceLock


class InventoryCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.root = self.folder / 'runtime'
        self.locks = self.folder / 'locks'
        self.config = self.folder / 'config.toml'
        self.config.write_text('[runtime]\nroot = '+json.dumps(str(self.root))+
            '\n[tools]\nrtl_power = "rf-atlas-deliberately-missing-test-tool"\n'
            'rtl_sdr = "rf-atlas-deliberately-missing-test-tool"\n', encoding='utf8')
        self.environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
            RF_ATLAS_HOME=str(self.root), RF_ATLAS_LOCK_ROOT=str(self.locks))

    def tearDown(self):
        self.temporary.cleanup()

    def cli(self, *arguments):
        return subprocess.run([sys.executable, '-m', 'rfatlas', '--config', str(self.config), *arguments],
            cwd=self.folder, env=self.environment, capture_output=True, text=True,
            timeout=20, check=False)

    def test_inventory_init_status_and_tiny_budget_never_access_hardware(self):
        result = self.cli('inventory-init', '--profile', 'ads-b')
        self.assertEqual(result.returncode, 0, result.stderr)
        initial = json.loads(result.stdout)
        self.assertEqual(len(initial['inventory']['regions']), 1)
        self.assertEqual(initial['inventory']['coverage']['visited_hz'], 0)
        self.assertTrue(Path(initial['profiles']).is_file())
        result = self.cli('inventory-status')
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual(status['inventory']['coverage']['valid_hz'], 0)
        for extra in ((), ('--survey-only',)):
            result = self.cli('inventory-run', '--profile', 'ads-b', '--max-seconds', '1', *extra)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['steps_completed'], 0)
        self.assertEqual(list((self.root/'data'/'spectra').glob('*')), [])
        self.assertFalse((self.root/'data'/'captures').exists())

    def test_inventory_cli_rejects_invalid_profile_and_scheduler_conflict(self):
        result = self.cli('inventory-init', '--profile', 'missing-profile')
        self.assertNotEqual(result.returncode, 0)
        with DeviceLock(self.locks, 'inventory-scheduler'):
            result = self.cli('inventory-run', '--max-seconds', '1', '--survey-only')
            self.assertNotEqual(result.returncode, 0)

    def test_analog_cli_synthetic_fixture_retains_unknown_protocol(self):
        rate = 250000
        t = np.arange(50000) / rate
        x = .35 * (1 + .6 * np.cos(2*np.pi*1100*t)) * np.exp(2j*np.pi*1700*t)
        raw = np.empty(len(x)*2, dtype=np.uint8)
        raw[::2] = np.clip(np.round(x.real*127.5+127.5), 0, 255).astype(np.uint8)
        raw[1::2] = np.clip(np.round(x.imag*127.5+127.5), 0, 255).astype(np.uint8)
        path = self.folder/'synthetic.cu8'
        path.write_bytes(raw.tobytes())
        metadata = {'format': 'cu8', 'sample_rate': rate, 'center_hz': 100000000,
                    'sample_count': len(x), 'sha256': hashlib.sha256(raw.tobytes()).hexdigest()}
        path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf8')
        result = self.cli('analog', str(path), '--mode', 'AM')
        self.assertEqual(result.returncode, 0, result.stderr)
        analysis = json.loads(result.stdout)
        self.assertEqual(analysis['modulation_hypothesis'], 'AM')
        self.assertEqual(analysis['protocol'], 'UNKNOWN')
        self.assertFalse(analysis['protocol_verified'])
        self.assertIsNone(analysis['demodulated_test_signal'])
        self.assertAlmostEqual(analysis['metrics']['dominant_tone_hz'], 1100, delta=50)
        self.assertFalse(list(self.folder.rglob('*.wav')))

    def test_automatic_ax25_verification_overrides_wrong_modulation_hypothesis(self):
        from rfatlas.decoders import DecoderInput
        from rfatlas.identification import identify
        from tests.test_packet import afsk_iq, frame
        analysis = {'modulation': 'AM', 'features': {'occupied_bandwidth_hz': 12000},
                    'activity': {'events': [{'start_s': 0., 'stop_s': .2}]}}
        data = frame()
        good = DecoderInput(afsk_iq(data), 48000, 145000000)
        result = identify(good, analysis)
        self.assertEqual(result['protocol'], 'AX.25')
        self.assertTrue(result['protocol_verified'])
        self.assertEqual(result['modulation'], 'AFSK')
        bad = bytearray(data)
        bad[-1] ^= 1
        rejected = identify(DecoderInput(afsk_iq(bytes(bad)), 48000, 145000000), analysis)
        self.assertEqual(rejected['protocol'], 'UNKNOWN')
        self.assertFalse(rejected['protocol_verified'])


if __name__ == '__main__':
    unittest.main()
