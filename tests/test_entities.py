"""Original synthetic RF frames exercise private evidence and entity lifecycle."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rfatlas.decoders import DecoderInput, RecoveredFrame, run_decoder
from rfatlas.entities import ensure_evidence_schema, entity_detail, list_entities, read_frames
from rfatlas.identification import DecoderRegistry, identify, store_identification
from rfatlas.packet import AX25AFSK1200Decoder
from rfatlas.protocols import ModeSDecoder, parsed_df17
from rfatlas.storage import open_database
from tests.test_packet import afsk_iq, frame as ax_frame
from tests.test_protocol_native import make_frame, ppm_iq


class EntityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = open_database(Path(self.temporary.name))
        ensure_evidence_schema(self.db)
        self.db.execute("INSERT INTO signals(signal_id) VALUES('synthetic-a')")
        self.db.execute("INSERT INTO signals(signal_id) VALUES('synthetic-b')")
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.temporary.cleanup()

    def result(self, seed=1, frequency=1090000000):
        registry = DecoderRegistry(); registry.register(ModeSDecoder())
        return identify(DecoderInput(ppm_iq(make_frame(seed)), 2400000, frequency), {}, registry)

    def test_actual_native_bytes_and_source_bound_fields(self):
        result = self.result()
        recovered = result['attempts'][0]['frames'][0]
        self.assertEqual(bytes.fromhex(recovered['raw_hex']), make_frame(1))
        self.assertEqual(recovered['frame_length'], 14)
        self.assertEqual(recovered['status'], 'VERIFIED')
        self.assertEqual(recovered['parsed_fields']['icao24'], make_frame(1)[1:4].hex().upper())
        self.assertNotIn('latitude', recovered['parsed_fields'])
        raw = ax_frame(b'>SYNTHETIC TEST ONLY')
        recording = DecoderInput(afsk_iq(raw), 48000, 145000000)
        recovered = run_decoder(AX25AFSK1200Decoder(), recording)['frames'][0]
        self.assertEqual(bytes.fromhex(recovered['raw_hex']), raw)
        self.assertEqual(recovered['parsed_fields']['source'], 'TEST1')
        self.assertNotIn('message', recovered['parsed_fields'])

    def test_default_storage_has_no_recovered_bytes_or_identifiers(self):
        result = self.result()
        before = json.dumps(result, sort_keys=True)
        store_identification(self.db, result, {'signal_id': 'synthetic-a'}, 'synthetic.cu8')
        self.assertEqual(json.dumps(result, sort_keys=True), before)
        self.assertEqual(read_frames(self.db)['frames'], [])
        self.assertEqual(list_entities(self.db)['entities'], [])
        saved = self.db.execute('SELECT result FROM identification_runs').fetchone()[0]
        self.assertNotIn(result['attempts'][0]['frames'][0]['raw_hex'], saved)
        self.assertNotIn('icao24', saved)
        self.assertNotIn('icao24', self.db.execute('SELECT evidence FROM signals WHERE signal_id=?', ('synthetic-a',)).fetchone()[0])

    def test_retention_independent_options_and_idempotence(self):
        result = self.result()
        kwargs = dict(retain_identifiers=True)
        key = store_identification(self.db, result, {'signal_id': 'synthetic-a', 'created': '2026-01-02T10:00:00-05:00'}, 'synthetic.cu8', **kwargs)
        self.assertEqual(key, store_identification(self.db, result, {'signal_id': 'synthetic-a', 'created': '2026-01-02T10:00:00-05:00'}, 'synthetic.cu8', **kwargs))
        recovered = read_frames(self.db, 'synthetic-a')['frames'][0]
        self.assertIsNone(recovered['raw_hex'])
        self.assertEqual(recovered['observed'], '2026-01-02T15:00:00+00:00')
        entities = list_entities(self.db, kind='station')['entities']
        self.assertEqual(len(entities), 1)
        detail = entity_detail(self.db, entities[0]['entity_id'])
        self.assertEqual(len(detail['observations']), 1)
        self.assertEqual(detail['associated_signals'][0]['signal_id'], 'synthetic-a')
        store_identification(self.db, self.result(2), {'signal_id': 'synthetic-a'}, 'synthetic2.cu8', retain_frames=True)
        recovered = read_frames(self.db)['frames'][0]
        self.assertIsNotNone(recovered['raw_hex'])
        self.assertEqual(recovered['parsed_fields'], {})
        self.assertEqual(len(list_entities(self.db)['entities']), 1)

    def test_same_frequency_does_not_merge_different_identifiers(self):
        for seed in (1, 2):
            store_identification(self.db, self.result(seed), {'signal_id': 'synthetic-a'}, f'synthetic{seed}.cu8', retain_identifiers=True)
        self.assertEqual(len(list_entities(self.db)['entities']), 2)
        # Same protocol address at another observed frequency is one entity.
        store_identification(self.db, self.result(1, 1090100000), {'signal_id': 'synthetic-b'}, 'synthetic-other.cu8', retain_identifiers=True)
        entities = list_entities(self.db)['entities']
        self.assertEqual(len(entities), 2)
        address = make_frame(1)[1:4].hex().upper()
        selected = list_entities(self.db, q=address)['entities'][0]
        self.assertEqual(len(entity_detail(self.db, selected['entity_id'])['associated_signals']), 2)
        self.assertEqual(list_entities(self.db, q="' OR 1=1 --")['entities'], [])

    def test_optin_ax25_source_and_environmental_event_without_fake_bytes(self):
        registry = DecoderRegistry(); registry.register(AX25AFSK1200Decoder())
        result = identify(DecoderInput(afsk_iq(ax_frame()), 48000, 145000000), {}, registry)
        store_identification(self.db, result, {'signal_id': 'synthetic-a'}, 'synthetic.ax25', retain_identifiers=True)
        self.assertEqual(list_entities(self.db, kind='station')['entities'][0]['identifier'], 'TEST1')
        from rfatlas.external_decoders import RTL433Decoder
        decoder = RTL433Decoder(runner=lambda argv, timeout: (json.dumps({'model': 'Fineoffset-WH2', 'mic': 'CRC', 'id': 7, 'humidity': 34, 'temperature_C': 12.5}), ''))
        decoder.available = lambda: True
        registry = DecoderRegistry(); registry.register(decoder)
        result = identify(DecoderInput(np.ones(4096, complex), 250000, 433000000), {}, registry)
        self.assertIsNone(result['attempts'][0]['frames'][0]['raw_hex'])
        store_identification(self.db, result, {'signal_id': 'synthetic-b'}, 'synthetic.sensor', retain_identifiers=True)
        device = list_entities(self.db, kind='device')['entities'][0]
        self.assertEqual(device['latest_fields']['temperature_c'], 12.5)
        self.assertEqual(device['identifier'], '7')
        self.assertEqual(list_entities(self.db, protocol='AX.25')['entities'][0]['identifier'], 'TEST1')

    def test_contract_rejects_bad_raw_frames_and_unbounded_fields(self):
        recording = DecoderInput(ppm_iq(make_frame()), 2400000, 1090000000)
        original = ModeSDecoder().decode(recording)
        class Adapter:
            name = 'test-adapter'; version = '1'
            def decode(self, recording): return self.evidence
        adapter = Adapter()
        good = original.frames[0]
        for invalid in (replace(good, raw_hex='00'), replace(good, raw_hex='xx'),
                        replace(good, start_sample=999999), replace(good, parsed_fields=(('icao24', 'wrong'),)),
                        replace(good, parsed_fields=(('number', float('nan')),)),
                        replace(good, parsed_fields=(('message', 'x' * 257),))):
            adapter.evidence = replace(original, frames=(invalid,))
            with self.subTest(frame=invalid), self.assertRaises(ValueError):
                run_decoder(adapter, recording)
        damaged = bytearray(make_frame()); damaged[-1] ^= 1
        with self.assertRaises(ValueError): parsed_df17(damaged)
        for kwargs in ({'kind': 'bad'}, {'limit': 0}, {'q': 'x' * 129}):
            with self.assertRaises(ValueError): list_entities(self.db, **kwargs)
        with self.assertRaises(ValueError): read_frames(self.db, limit=True)
        self.assertIsNone(entity_detail(self.db, 'missing'))

    def test_category_and_utc_time_search(self):
        for seed, timestamp in ((1,'2026-01-02T10:00:00-05:00'),(2,'2026-01-03T15:00:00Z')):
            store_identification(self.db, self.result(seed), {'signal_id': 'synthetic-a', 'created': timestamp}, f'synthetic{seed}.cu8', retain_identifiers=True)
        self.assertEqual(len(list_entities(self.db, q='AIRCRAFT')['entities']), 2)
        self.assertEqual(len(list_entities(self.db, category='aircraft', first_since='2026-01-02T15:00:00Z', last_until='2026-01-02T16:00:00+01:00')['entities']), 1)
        self.assertEqual(len(list_entities(self.db, first_since='2026-01-03T14:00:00+00:00')['entities']), 1)
        self.assertEqual(list_entities(self.db, category="aircraft' OR 1=1 --")['entities'], [])
        for kwargs in ({'first_since':'2026-01-01'}, {'last_until':'2026-01-01T12:00:00'}, {'first_since':'invalid'}, {'first_since':7}, {'category':7}, {'first_since':'2026-01-04T00:00:00Z','last_until':'2026-01-03T00:00:00Z'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                list_entities(self.db, **kwargs)


if __name__ == '__main__':
    unittest.main()
