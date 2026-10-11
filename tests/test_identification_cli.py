"""Offline CLI association/reanalysis with an original synthetic valid frame."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from rfatlas.storage import open_database
from tests.test_protocol_native import make_frame, ppm_iq


class IdentificationCLITests(unittest.TestCase):
    def test_explicit_association_and_catalog_reanalysis_are_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='rf atlas cli ') as directory:
            root = Path(directory)
            iq = ppm_iq(make_frame(271))
            raw = np.column_stack((iq.real, iq.imag))
            path = root / 'original-synthetic.cu8'
            path.write_bytes(np.clip(np.rint(raw * 127.5 + 127.5), 0, 255).astype(np.uint8).tobytes())
            metadata = {'format': 'cu8', 'sample_rate': 2400000, 'center_hz': 1090000000,
                        'sample_count': len(iq), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            path.with_suffix('.json').write_text(json.dumps(metadata), encoding='utf-8')
            config = root / 'config.toml'
            config.write_text('[runtime]\nroot=' + json.dumps(str(root)) + '\n', encoding='utf-8')
            with closing(open_database(root)) as db, db:
                db.execute('INSERT INTO signals(signal_id,protocol) VALUES(?,?)', ('synthetic-cli', 'UNKNOWN'))
                db.execute('INSERT INTO captures(capture_id,signal_id,path,settings) VALUES(?,?,?,?)',
                           ('synthetic-capture', 'synthetic-cli', str(path), json.dumps(metadata)))
            environment = {**os.environ, 'RF_ATLAS_HOME': str(root), 'RF_ATLAS_LOCK_ROOT': str(root / 'locks')}

            def cli(*args):
                completed = subprocess.run([sys.executable, '-m', 'rfatlas', '--config', str(config), *args],
                    capture_output=True, text=True, timeout=30, env=environment)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return json.loads(completed.stdout)

            for _ in range(2):
                result = cli('identify', str(path), '--signal-id', 'synthetic-cli', '--store')
                self.assertEqual(result['protocol'], 'ADS-B')
                self.assertTrue(result['protocol_verified'])
            with closing(open_database(root)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM identification_runs').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT protocol FROM signals WHERE signal_id=?', ('synthetic-cli',)).fetchone()[0], 'ADS-B')
            first = cli('reanalyze', '--signal-id', 'synthetic-cli')
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]['protocol'], 'ADS-B')
            self.assertIn('run_key', first[0])
            with closing(open_database(root)) as db:
                count = db.execute('SELECT COUNT(*) FROM identification_runs').fetchone()[0]
                self.assertEqual(db.execute('SELECT signal_id FROM identification_runs WHERE run_key=?',
                                           (first[0]['run_key'],)).fetchone()[0], 'synthetic-cli')
            second = cli('reanalyze', '--signal-id', 'synthetic-cli')
            self.assertEqual(second[0]['run_key'], first[0]['run_key'])
            with closing(open_database(root)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM identification_runs').fetchone()[0], count)
            # The association belongs to catalog history; source metadata stays intact.
            self.assertNotIn('signal_id', json.loads(path.with_suffix('.json').read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
