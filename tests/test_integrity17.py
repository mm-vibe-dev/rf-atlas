import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from rfatlas.iq import read_recording


class IntegrityTests(unittest.TestCase):
    def fixture(self,d):
        p=Path(d)/'source.cu8';p.write_bytes(b'\x80\x81'*4096)
        p.with_suffix('.json').write_text(json.dumps(dict(format='cu8',sample_rate=250000,center_hz=100000000,sample_count=4096,sha256=hashlib.sha256(p.read_bytes()).hexdigest())),encoding='utf8')
        return p

    def test_hash_matches_consumed_buffer_even_if_file_changes_after_size_check(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.fixture(d);original=np.fromfile
            def mutate(*args,**kwargs):
                p.write_bytes(b'\x80\x82'*4096)
                return original(*args,**kwargs)
            with patch('rfatlas.iq.np.fromfile',side_effect=mutate),self.assertRaises(ValueError):read_recording(p)

    def test_metadata_is_bounded_before_json_and_growth_is_not_consumed_unbounded(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.fixture(d);p.with_suffix('.json').write_bytes(b' '*65537)
            with self.assertRaisesRegex(ValueError,'metadata'):read_recording(p)
        with tempfile.TemporaryDirectory() as d:
            p=self.fixture(d);original=np.fromfile
            def grow(*args,**kwargs):
                with p.open('ab') as f:f.write(b'\x80\x81'*4096)
                self.assertEqual(kwargs['count'],8193)
                return original(*args,**kwargs)
            with patch('rfatlas.iq.np.fromfile',side_effect=grow),self.assertRaises(ValueError):read_recording(p)
