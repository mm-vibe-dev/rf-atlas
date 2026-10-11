"""Interchange and adapter tests use only generated, public-safe IQ."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from rfatlas.decoders import DecoderInput, DecoderEvidence, run_decoder
from rfatlas.iq import read_recording
from rfatlas.sigmf import export_sigmf, import_sigmf


class InteropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rf atlas interchange ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'native.cu8'
        np.random.default_rng(91).integers(60, 190, 32768, dtype=np.uint8).tofile(self.source)
        self.meta = {'format': 'cu8', 'sample_rate': 250000, 'center_hz': 100025000,
                     'desired_frequency_hz': 100000000, 'tuning_offset_hz': 25000,
                     'sample_count': 16384, 'created': '2026-01-01T12:00:00+00:00',
                     'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.source.with_suffix('.json').write_text(json.dumps(self.meta), encoding='utf8')

    def exported(self):
        return export_sigmf(self.source, self.root / 'exchange')

    def test_roundtrip_preserves_raw_samples_and_tuning(self):
        data, metadata = self.exported()
        restored, meta = import_sigmf(metadata, self.root / 'restored')
        self.assertEqual(data.read_bytes(), self.source.read_bytes())
        self.assertEqual(restored.read_bytes(), self.source.read_bytes())
        self.assertEqual(meta['tuning_offset_hz'], 25000)
        self.assertEqual(meta['created'], self.meta['created'])
        self.assertEqual(meta['source_integrity'], 'SHA512 verified')
        self.assertEqual(meta['sha256'], self.meta['sha256'])
        np.testing.assert_array_equal(read_recording(restored)[0], read_recording(self.source)[0])

    def test_full_acquisition_allowlist_roundtrips_without_analysis_state(self):
        acquisition = {'gain_db': 12.5, 'antenna': 'synthetic fixture',
                       'device_identity': 'synthetic-receiver-A', 'sample_rate_reported_hz': 249999.25,
                       'sample_rate_basis': 'configured nominal; tool estimate retained', 'bias_tee': False,
                       'created': '2026-01-01T07:00:00-05:00',
                       'acquisition_start': '2026-01-01T06:59:59-05:00',
                       'acquisition_end': '2026-01-01T07:00:00-05:00'}
        self.meta.update(acquisition, analysis_quality={'transient': True})
        self.source.with_suffix('.json').write_text(json.dumps(self.meta))
        _, metadata = self.exported()
        document = json.loads(metadata.read_text())
        self.assertEqual(document['captures'][0]['core:datetime'], '2026-01-01T12:00:00Z')
        self.assertNotIn('analysis_quality', document['global']['core:description'])
        restored, imported = import_sigmf(metadata, self.root / 'acquisition')
        for key, value in acquisition.items():
            self.assertEqual(imported[key], value)
        self.assertNotIn('analysis_quality', imported)
        self.assertEqual(restored.read_bytes(), self.source.read_bytes())

    def test_acquisition_values_rejected_before_import_write(self):
        _, metadata = self.exported()
        original = json.loads(metadata.read_text())
        invalid = [('device_identity', 123), ('gain_db', float('inf')),
                   ('sample_rate_reported_hz', 0), ('sample_rate_reported_hz', True),
                   ('sample_rate_reported_hz', float('nan')), ('sample_rate_basis', []),
                   ('bias_tee', 1), ('antenna', {}), ('acquisition_start', 'not-a-timestamp'),
                   ('created', '2026-02-01T00:00:00Z')]
        from rfatlas.sigmf import DESCRIPTION_PREFIX
        for key, value in invalid:
            document = json.loads(json.dumps(original))
            preserved = json.loads(document['global']['core:description'][len(DESCRIPTION_PREFIX):])
            preserved[key] = value
            document['global']['core:description'] = DESCRIPTION_PREFIX + json.dumps(preserved)
            metadata.write_text(json.dumps(document))
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                import_sigmf(metadata, self.root / 'bad-acquisition')
            self.assertFalse((self.root / 'bad-acquisition.cu8').exists())

    def test_source_integrity_and_no_overwrite(self):
        data, metadata = self.exported()
        with self.assertRaises(FileExistsError):
            export_sigmf(self.source, self.root / 'exchange')
        data.write_bytes(data.read_bytes()[:-2] + b'XX')
        with self.assertRaisesRegex(ValueError, 'SHA512'):
            import_sigmf(metadata, self.root / 'bad')
        self.assertFalse((self.root / 'bad.cu8').exists())

    def test_external_metadata_without_hash_or_timestamp_is_explicit(self):
        _, metadata = self.exported()
        document = json.loads(metadata.read_text())
        document['global'].pop('core:sha512')
        document['global'].pop('core:description')
        document['captures'][0].pop('core:datetime')
        metadata.write_text(json.dumps(document))
        restored, meta = import_sigmf(metadata, self.root / 'external', desired_frequency_hz=100000000)
        self.assertIsNone(meta['created'])
        self.assertIn('No upstream', meta['source_integrity'])
        self.assertEqual(meta['tuning_offset_hz'], 25000)
        self.assertEqual(len(read_recording(restored)[0]), 16384)

    def test_unsupported_metadata_rejected_before_copy(self):
        _, metadata = self.exported()
        original = json.loads(metadata.read_text())
        mutations = [
            lambda d: d['captures'].append(dict(d['captures'][0])),
            lambda d: d['global'].update({'core:datatype': 'cf32_le'}),
            lambda d: d['global'].update({'core:num_channels': True}),
            lambda d: d['global'].update({'core:sample_rate': float('nan')}),
            lambda d: d['global'].update({'core:dataset': '../elsewhere'}),
            lambda d: d['global'].update({'core:extensions': [{'name': 'unknown', 'optional': False}]}),
            lambda d: d['captures'][0].update({'core:sample_start': 1}),
            lambda d: d['captures'][0].update({'core:header_bytes': 2}),
            lambda d: d['captures'][0].update({'core:datetime': '2026-01-01T00:00:00'}),
        ]
        for mutate in mutations:
            document = json.loads(json.dumps(original)); mutate(document)
            metadata.write_text(json.dumps(document))
            with self.subTest(document=document), self.assertRaises(ValueError):
                import_sigmf(metadata, self.root / 'bad')
            self.assertFalse((self.root / 'bad.cu8').exists())

    def test_truncated_oversized_and_offset_rejected(self):
        data, metadata = self.exported()
        with self.assertRaises(ValueError):
            import_sigmf(metadata, self.root / 'bad', max_bytes=8192)
        with self.assertRaises(ValueError):
            import_sigmf(metadata, self.root / 'bad', desired_frequency_hz=100900000)
        data.write_bytes(data.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            import_sigmf(metadata, self.root / 'bad')

    def test_invalid_native_timestamp_and_checksum_rejected(self):
        self.meta['created'] = 'unparseable'
        self.source.with_suffix('.json').write_text(json.dumps(self.meta))
        with self.assertRaises(ValueError):
            self.exported()
        self.meta['created'] = '2026-01-01T00:00:00Z'
        self.meta['sha256'] = '0' * 64
        self.source.with_suffix('.json').write_text(json.dumps(self.meta))
        with self.assertRaises(ValueError):
            self.exported()

    def test_annotations_are_archived_and_never_promoted_to_protocol(self):
        _, metadata = self.exported()
        document = json.loads(metadata.read_text())
        document['annotations'] = [{'core:sample_start': 0, 'core:sample_count': 4096,
                                    'core:comment': 'UNTRUSTED protocol claim'}]
        metadata.write_text(json.dumps(document))
        _, meta = import_sigmf(metadata, self.root / 'annotated')
        self.assertEqual(meta['source_sigmf_metadata']['annotations'], document['annotations'])
        self.assertNotIn('protocol', meta)
        document['annotations'][0]['core:sample_count'] = 999999
        metadata.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            import_sigmf(metadata, self.root / 'bad')

    def test_decoder_contract_binds_evidence_to_analysis_samples(self):
        recording = DecoderInput.from_recording(self.source)
        self.assertFalse(recording.iq.flags.writeable)
        self.assertEqual(recording.source_sha256, self.meta['sha256'])
        self.assertEqual(recording.frequency_hz, 100000000)
        base = DecoderEvidence('SYNTHETIC_TEST_PROTOCOL', recording.sample_sha256)

        class TestAdapter:
            name = 'test-only adapter'; version = '1'
            evidence = base
            def decode(self, recording):
                return self.evidence

        adapter = TestAdapter()
        result = run_decoder(adapter, recording)
        self.assertEqual(result['protocol'], 'UNKNOWN')
        adapter.evidence = replace(base, frames_examined=2, frames_with_sync=1,
                                   frames_with_valid_checksum=1)
        self.assertFalse(run_decoder(adapter, recording)['protocol_verified'])
        adapter.evidence = replace(base, frames_examined=2, frames_with_sync=1,
                                   frames_with_valid_checksum=1, frames_with_sync_and_valid_checksum=1,
                                   sync_reference='test vector sync', checksum_reference='test vector checksum')
        result = run_decoder(adapter, recording)
        self.assertTrue(result['protocol_verified'])
        self.assertEqual(result['protection'], 'UNKNOWN')
        for invalid in [replace(adapter.evidence, input_sample_sha256='0' * 64),
                        replace(adapter.evidence, sync_reference=''),
                        replace(adapter.evidence, frames_with_sync=3),
                        replace(adapter.evidence, frames_examined=True),
                        replace(adapter.evidence, frames_with_sync_and_valid_checksum=2)]:
            adapter.evidence = invalid
            with self.subTest(evidence=invalid), self.assertRaises(ValueError):
                run_decoder(adapter, recording)

    def test_decoder_cannot_change_samples_to_match_its_evidence(self):
        recording = DecoderInput.from_recording(self.source)
        class MutatingAdapter:
            name = 'untrusted test adapter'; version = '1'
            def decode(self, recording):
                recording.iq.flags.writeable = True
                recording.iq[0] += 1
                return DecoderEvidence('TEST', recording.sample_sha256)
        with self.assertRaisesRegex(ValueError, 'modified'):
            run_decoder(MutatingAdapter(), recording)

    def test_decoder_input_rejects_invalid_samples_and_rates(self):
        for samples, rate in [(np.ones(4096), 250000), (np.ones(5, complex), 250000),
                              (np.full(4096, complex(float('nan'), 0)), 250000),
                              (np.ones(4096, complex), float('inf'))]:
            with self.assertRaises(ValueError):
                DecoderInput(samples, rate, 100000000)


if __name__ == '__main__':
    unittest.main()
