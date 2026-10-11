import tempfile
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

from rfatlas.decoders import DecoderEvidence, DecoderInput
from rfatlas.identification import (DecoderRegistry, SelectionPolicy, identify,
    protection_status, store_identification, signal_history)
from rfatlas.storage import open_database


class FakeDecoder:
    name = 'fixture'
    version = '1'
    capabilities = {'protocols': ['FIXTURE'], 'modulations': ['PPM'],
        'platforms': ['Windows', 'Linux'], 'offline_replay': True,
        'frequency_min_hz': 100, 'frequency_max_hz': 1000,
        'sample_rate_min_hz': 1000, 'evidence': ['sync', 'CRC']}

    def decode(self, recording):
        return DecoderEvidence('FIXTURE', recording.sample_sha256, 1, 1, 1, 1, 'sync', 'CRC')


class IdentificationTests(unittest.TestCase):
    def setUp(self):
        self.recording = DecoderInput(np.ones(8192, dtype=complex), 10000., 500.)
        self.registry = DecoderRegistry(); self.registry.register(FakeDecoder())

    def test_registration_and_policy(self):
        with self.assertRaises(ValueError): self.registry.register(FakeDecoder())
        with self.assertRaises(ValueError): SelectionPolicy(max_attempts=0)
        self.assertTrue(self.registry.describe()[0]['available'])

    def test_verified_evidence_overrides_wrong_modulation(self):
        result = identify(self.recording, {'modulation': 'NFM'}, self.registry)
        self.assertEqual(result['protocol'], 'FIXTURE')
        self.assertEqual(result['protection']['status'], 'UNKNOWN')

    def test_frequency_rate_unavailable_and_unknown(self):
        other = DecoderInput(self.recording.iq, 10000., 2000.)
        result = identify(other, {'modulation': 'PPM'}, self.registry)
        self.assertEqual(result['protocol'], 'UNKNOWN')
        self.assertFalse(result['attempts'])
        self.registry.decoders['fixture'].available = lambda: False
        self.assertFalse(identify(self.recording, {}, self.registry)['candidates'][0]['eligible'])

    def test_failure_timeout_and_bad_evidence_contained(self):
        decoder = self.registry.decoders['fixture']
        for exception in (RuntimeError('failure'), TimeoutError('timeout')):
            def fail(recording, error=exception): raise error
            decoder.decode = fail
            result = identify(self.recording, {}, self.registry)
            self.assertEqual(result['protocol'], 'UNKNOWN')
            self.assertEqual(result['attempts'][0]['status'], 'FAILED')
        decoder.decode = lambda recording: DecoderEvidence('FIXTURE', '0'*64, 1, 1, 1, 1, 'sync', 'CRC')
        self.assertEqual(identify(self.recording, {}, self.registry)['protocol'], 'UNKNOWN')

    def test_sample_budget_and_previous_history(self):
        result = identify(self.recording, {}, self.registry, SelectionPolicy(max_samples=4096), 'FIXTURE')
        self.assertEqual(result['sample_count'], 4096)
        self.assertIn('previously verified protocol', result['candidates'][0]['hints'])

    def test_protection_requires_explicit_validated_fields(self):
        self.assertEqual(protection_status({'digital': True})['status'], 'UNKNOWN')
        field = {'field': 'privacy', 'reference': 'fixture spec', 'indicator': True}
        self.assertEqual(protection_status(field)['status'], 'INDICATED')
        field['field_validated'] = True
        self.assertEqual(protection_status(field, protocol_verified=True)['status'], 'VERIFIED_BY_PROTOCOL_METADATA')
        field['indicator'] = False
        self.assertEqual(protection_status(field, protocol_verified=True)['status'], 'NOT_INDICATED')

    def test_additive_history_deduplication_and_unknown_does_not_erase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = open_database(root)
            db.execute("INSERT INTO signals(signal_id) VALUES('synthetic')"); db.commit()
            result = identify(self.recording, {}, self.registry)
            key = store_identification(db, result, {'signal_id': 'synthetic'}, 'fixture.cu8')
            self.assertEqual(key, store_identification(db, result, {'signal_id': 'synthetic'}, 'fixture.cu8'))
            result['protocol'] = 'UNKNOWN'; result['protocol_verified'] = False
            store_identification(db, result, {'signal_id': 'synthetic'}, 'fixture.cu8')
            self.assertEqual(len(signal_history(db, 'synthetic')['identifications']), 2)
            self.assertEqual(db.execute('SELECT protocol FROM signals').fetchone()[0], 'FIXTURE')
            db.close(); db = open_database(root)
            self.assertEqual(db.execute('SELECT count(*) FROM identification_runs').fetchone()[0], 2)
            db.close()

    def test_conflicting_verified_protocol_appends_without_replacing_catalog_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            db=open_database(Path(directory))
            db.execute("INSERT INTO signals(signal_id) VALUES('synthetic')");db.commit()
            first=identify(self.recording,{},self.registry)
            store_identification(db,first,{'signal_id':'synthetic'},'first.cu8')
            original=tuple(db.execute('select protocol,modulation,signal_class,protection,evidence,last_verified,decoder_version from signals').fetchone())
            second=dict(first,protocol='SECOND',verified_protocols=['SECOND'],modulation='OTHER',characterization={'signal_type':'MIXED'})
            key=store_identification(db,second,{'signal_id':'synthetic'},'second.cu8')
            self.assertEqual(tuple(db.execute('select protocol,modulation,signal_class,protection,evidence,last_verified,decoder_version from signals').fetchone()),original)
            saved=json.loads(db.execute('select result from identification_runs where run_key=?',(key,)).fetchone()[0])
            self.assertEqual(saved['catalog_protocol_conflict']['previous_protocol'],'FIXTURE')
            self.assertEqual(saved['protocol'],'SECOND');self.assertEqual(db.execute('select count(*) from identification_runs').fetchone()[0],2)
            self.assertEqual(store_identification(db,second,{'signal_id':'synthetic'},'second.cu8'),key)
            db.close()

    def test_actual_native_selection_and_checksummed_offline_cli(self):
        from tests.test_protocol_native import ppm_iq, make_frame
        from rfatlas.identification import default_registry
        from rfatlas.protocols import ModeSDecoder
        iq=ppm_iq(make_frame(123))
        recording=DecoderInput(iq,2400000.,1090000000.)
        result=identify(recording,{'modulation':'NFM'},default_registry())
        self.assertEqual(result['protocol'],'ADS-B')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'synthetic.cu8'
            raw=np.empty((len(iq),2),dtype=np.uint8)
            raw[:,0]=np.clip(np.rint(iq.real*127.5+127.5),0,255)
            raw[:,1]=np.clip(np.rint(iq.imag*127.5+127.5),0,255)
            path.write_bytes(raw.tobytes())
            path.with_suffix('.json').write_text(json.dumps({'format':'cu8','sample_rate':2400000,
                'center_hz':1090000000,'sample_count':len(iq),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))
            config=root/'config.toml'; config.write_text('[runtime]\nroot='+json.dumps(str(root))+'\n')
            completed=subprocess.run([sys.executable,'-m','rfatlas','--config',str(config),'identify',str(path)],capture_output=True,text=True,timeout=20)
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertEqual(json.loads(completed.stdout)['protocol'],'ADS-B')
            from rfatlas.identification import identify_recording
            with self.assertRaises(ValueError): identify_recording(path,max_bytes=8191)


if __name__ == '__main__': unittest.main()
