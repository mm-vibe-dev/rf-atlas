"""Real localhost runtime/frame/catalog endpoints with synthetic RF evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from rfatlas.config import Config
from rfatlas.dashboard import CatalogView, create_dashboard_server
from rfatlas.decoders import DecoderInput
from rfatlas.identification import DecoderRegistry, identify, store_identification
from rfatlas.protocols import ModeSDecoder
from rfatlas.runtime import RuntimeSupervisor
from rfatlas.storage import open_database
from tests.test_protocol_native import make_frame, ppm_iq


class RuntimeHTTPTests(unittest.TestCase):
    def test_deep_investigation_and_lab_routes_validate_and_remain_receive_only(self):
        self.assertEqual(self.request('GET','/api/investigation-recipes')[0],200)
        self.assertFalse(self.request('GET','/api/signal-lab/unknown')[1]['available'])
        self.assertEqual(self.request('POST','/api/runtime/deep/start',{'signal_id':'missing'})[0],400)
        self.assertEqual(self.request('POST','/api/runtime/deep/start',{'signal_id':'missing'},headers={'X-RFAtlas-Token':'invalid'})[0],403)
        self.assertEqual(self.request('POST','/api/runtime/deep/cancel',{})[0],200)
        self.assertEqual(self.request('GET','/api/runtime')[1]['mode'],'idle')
        with closing(open_database(self.root)) as db:
            db.execute("INSERT INTO signals(signal_id,center_frequency_hz) VALUES('preset',471234567)");db.commit()
        preset=dict(signal_id='preset',recipe='generic',strategy='burst-watch')
        saved=self.request('POST','/api/runtime/deep/preset',preset)
        self.assertEqual(saved[0],200);self.assertFalse(saved[1]['acquisition_started'])
        self.assertEqual(self.request('GET','/api/investigation-recipes')[1]['presets'],[preset])
        self.assertEqual(self.request('POST','/api/runtime/deep/preset',dict(preset,strategy='indefinite'))[0],400)

    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        with closing(open_database(self.root)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],11)
        self.runtime=RuntimeSupervisor(Config(self.root,lock_root=self.root/'locks'))
        self.addCleanup(self.runtime.close)
        self.server=create_dashboard_server(self.root,runtime=self.runtime,controller=self.runtime)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start();self.addCleanup(self.close_server)
        self.token=self.request('GET','/api/session')[1]['token']

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)

    def test_offline_reanalysis_routes_keep_receiver_idle_and_validate_budget(self):
        self.assertEqual(self.request('GET','/api/reanalysis')[0],200)
        self.assertEqual(self.request('POST','/api/runtime/reanalysis/start',{'limit':101})[0],400)
        self.assertEqual(self.request('POST','/api/runtime/reanalysis/start',{},headers={'X-RFAtlas-Token':'invalid'})[0],403)
        self.assertEqual(self.request('POST','/api/runtime/reanalysis/start',{'signal_id':'no-recording'})[0],200)
        self.runtime.reanalysis.thread.join(timeout=5)
        state=self.request('GET','/api/reanalysis')[1]
        self.assertFalse(state['active']);self.assertEqual(state['jobs'][0]['progress']['total'],0)
        self.assertEqual(state['jobs'][0]['status'],'complete')
        self.assertFalse(self.runtime.monitor.status()['active'])
        self.assertEqual(self.request('POST','/api/runtime/reanalysis/cancel',{})[0],200)

    def test_campaign_report_endpoint_comparison_and_parameter_validation(self):
        status,data=self.request('GET','/api/discovery-report?since=2026-01-01T00:00:00Z&until=2026-01-01T01:00:00Z&compare=true')
        self.assertEqual(status,200);self.assertEqual(data['report_schema'],2)
        self.assertEqual(data['observations'],0)
        self.assertIn('Descriptive comparison',data['comparison']['basis'])
        for query in ('since=invalid','compare=maybe','unexpected=true','since=a&since=b'):
            self.assertEqual(self.request('GET','/api/discovery-report?'+query)[0],400)

    def test_incremental_monitor_rows_and_bounded_binary_pcm(self):
        monitor=self.runtime.monitor
        monitor.rows.extend([dict(sequence=10,power_db=[-40]),dict(sequence=20,power_db=[-30])])
        monitor.sequence=20
        status,data=self.request('GET','/api/monitor?since=15')
        self.assertEqual(status,200);self.assertTrue(data['waterfall_delta'])
        self.assertEqual([row['sequence'] for row in data['waterfall']],[20])
        self.assertEqual(self.request('GET','/api/runtime')[1]['monitor']['waterfall'],[])
        for query in ('since=-1','since=bad','unexpected=1'):
            self.assertEqual(self.request('GET','/api/monitor?'+query)[0],400)
        for seq in range(11,21):monitor.audio.append((seq,bytes([seq,0])*4800))
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            c.request('GET','/api/monitor/audio?since=0&epoch=0')
            response=c.getresponse();data=response.read()
            self.assertEqual(response.status,200)
            self.assertEqual(response.getheader('X-Audio-Sequence'),'20')
            self.assertEqual(response.getheader('X-Audio-Rate'),'48000')
            self.assertEqual(len(data),28800)
            self.assertEqual(data[:2],bytes([18,0]))
        finally:c.close()

    def test_heartbeat_does_not_compete_with_receiver_commands(self):
        with patch.object(self.runtime,'action',side_effect=AssertionError('Receiver command not needed')):
            status,result=self.request('POST','/api/runtime/monitor/heartbeat',{})
            self.assertEqual(status,200);self.assertEqual(result,{'ok':True})
            self.assertGreater(self.runtime.monitor.last_heartbeat,0)
            self.assertEqual(self.request('POST','/api/runtime/monitor/heartbeat',{},headers={'X-RFAtlas-Token':''})[0],403)

    def request(self,method,path,body=None,headers=None,raw=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            content=raw if raw is not None else json.dumps(body).encode() if body is not None else None
            actual={'Content-Type':'application/json'}
            if hasattr(self,'token'):actual['X-RFAtlas-Token']=self.token
            actual.update(headers or {})
            connection.request(method,path,body=content,headers=actual)
            response=connection.getresponse();data=response.read()
            return response.status,json.loads(data)
        finally:connection.close()

    def test_empty_schema_and_runtime_default_are_real_database_results(self):
        self.assertEqual(self.request('GET','/api/runtime')[1]['mode'],'idle')
        self.assertFalse(self.request('GET','/api/runtime')[1]['settings']['startup_auto'])
        self.assertEqual(self.request('GET','/api/frames')[1]['frames'],[])
        self.assertEqual(self.request('GET','/api/entities')[1]['entities'],[])
        self.assertEqual(self.request('GET','/api/signals')[1]['total'],0)
        self.assertEqual(self.request('GET','/api/entities/missing')[0],404)

    def test_runtime_csrf_local_origin_and_malformed_bodies(self):
        endpoint='/api/runtime/settings'
        for headers in [{'X-RFAtlas-Token':''},{'Host':'remote.example'},
                        {'Origin':'https://remote.example'},{'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.request('POST',endpoint,{},headers)[0],403)
        for raw,headers in [(b'{invalid',{}),(b'[]',{}),(b'null',{}),(b'\xff',{}),
                            (b'{}',{'Content-Type':'text/plain'}),
                            (b'{}',{'Content-Length':'-1'}),
                            (b'{}',{'Content-Length':'invalid'}),
                            (b'x'*4097,{}),(b'',{})]:
            with self.subTest(raw=raw[:30],headers=headers):
                self.assertEqual(self.request('POST',endpoint,headers=headers,raw=raw)[0],400)
        self.assertFalse(self.runtime.settings['retain_frames'])
        self.assertEqual(self.request('POST','/api/runtime/unknown',{})[0],400)
        self.assertEqual(self.request('POST',endpoint,{'retain_frames':True})[0],200)
        self.assertTrue(self.request('GET','/api/runtime')[1]['settings']['retain_frames'])

    def test_missing_tool_conflict_is_sanitized_and_invalid_gain_is_400(self):
        endpoint='/api/runtime/tuner/start'
        with patch('rfatlas.runtime.RTLBackend.tool',side_effect=RuntimeError('private executable path and identity')) as tool:
            status,body=self.request('POST',endpoint,{'frequency_hz':100000000})
            self.assertEqual(status,409)
            self.assertNotIn('private',json.dumps(body));self.assertNotIn('identity',json.dumps(body))
            self.assertEqual(self.request('POST',endpoint,{'gain':'unsupported'})[0],400)
            self.assertEqual(tool.call_count,1)
        self.assertEqual(self.request('GET','/api/runtime')[1]['mode'],'idle')

    def test_runtime_control_actions_are_serialized(self):
        entered=threading.Event();release=threading.Event()
        original=self.runtime.action
        def slow(action,payload):
            entered.set()
            if not release.wait(2):raise RuntimeError('Synthetic test timed out')
            return original(action,payload)
        with patch.object(self.runtime,'action',side_effect=slow),ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(self.request,'POST','/api/runtime/settings',{'restore_auto':False})
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.request('POST','/api/runtime/settings',{})[0],409)
            finally:release.set()
            self.assertEqual(first.result()[0],200)

    def add_verified(self,seed,signal_id,timestamp,*,retain=False):
        registry=DecoderRegistry();registry.register(ModeSDecoder())
        result=identify(DecoderInput(ppm_iq(make_frame(seed)),2400000,1090000000),{},registry)
        self.assertEqual(result['protocol'],'ADS-B')
        with closing(open_database(self.root)) as db,db:
            db.execute('INSERT OR IGNORE INTO signals(signal_id,center_frequency_hz,modulation,protocol,first_seen,last_seen) VALUES(?,?,?,?,?,?)',
                       (signal_id,1090000000,'PPM','UNKNOWN',timestamp,timestamp))
            store_identification(db,result,{'signal_id':signal_id,'created':timestamp},f'synthetic-{seed}-{retain}.cu8',retain_frames=retain,retain_identifiers=retain)

    def test_actual_native_crc_frames_optin_associations_and_utc_filters(self):
        self.add_verified(1,'synthetic-a','2026-01-02T10:00:00-05:00')
        self.assertEqual(self.request('GET','/api/frames')[1]['frames'],[])
        self.assertEqual(self.request('GET','/api/entities')[1]['entities'],[])
        self.add_verified(1,'synthetic-a','2026-01-02T10:00:00-05:00',retain=True)
        self.add_verified(2,'synthetic-b','2026-01-03T15:00:00Z',retain=True)
        frames=self.request('GET','/api/frames?signal_id=synthetic-a')[1]['frames']
        self.assertEqual(len(frames),1)
        self.assertEqual(bytes.fromhex(frames[0]['raw_hex']),make_frame(1))
        self.assertEqual(frames[0]['frame_length'],14)
        self.assertTrue(frames[0]['sync_valid']);self.assertTrue(frames[0]['checksum_valid'])
        self.assertEqual(frames[0]['status'],'VERIFIED')
        self.assertEqual(frames[0]['observed'],'2026-01-02T15:00:00+00:00')
        self.assertNotIn('recording_path',frames[0])
        query=urlencode({'kind':'station','protocol':'ADS-B','category':'aircraft',
                         'first_since':'2026-01-02T16:00:00+01:00','last_until':'2026-01-02T15:00:00Z'})
        status,entities=self.request('GET','/api/entities?'+query)
        self.assertEqual(status,200);self.assertEqual(len(entities['entities']),1)
        entity_id=entities['entities'][0]['entity_id']
        detail=self.request('GET','/api/entities/'+entity_id)[1]
        self.assertEqual(detail['associated_signals'][0]['signal_id'],'synthetic-a')
        self.assertEqual(detail['verification_status'],'VERIFIED_PROTOCOL_IDENTIFIER')
        signal=self.request('GET','/api/signals/synthetic-a')[1]
        self.assertEqual(signal['associated_entities'][0]['entity_id'],entity_id)
        self.assertEqual(len(self.request('GET','/api/entities')[1]['entities']),2)

    def test_frame_entity_filters_reject_bad_bounds_and_timezones(self):
        for endpoint in ['/api/frames?limit=0','/api/frames?limit=1001','/api/frames?limit=no',
                         '/api/frames?unknown=x','/api/frames?signal_id=a&signal_id=b',
                         '/api/entities?kind=unknown','/api/entities?limit=0',
                         '/api/entities?first_since=2026-01-01',
                         '/api/entities?last_until=2026-01-01T12:00:00',
                         '/api/entities?first_since=invalid',
                         '/api/entities?first_since=2026-01-04T00:00:00Z&last_until=2026-01-03T00:00:00Z',
                         '/api/entities/missing?limit=1']:
            with self.subTest(endpoint=endpoint):self.assertEqual(self.request('GET',endpoint)[0],400)


class CatalogTimeFilterTests(unittest.TestCase):
    def test_modulation_search_uses_timezone_instants_and_parameterized_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with closing(open_database(root)) as db,db:
                for identity,modulation,timestamp in [('a','NFM','2026-01-02T10:00:00-05:00'),
                                                     ('b','2FSK','2026-01-03T15:00:00Z')]:
                    db.execute('INSERT INTO signals(signal_id,center_frequency_hz,modulation,first_seen,last_seen) VALUES(?,?,?,?,?)',
                               (identity,100000000,modulation,timestamp,timestamp))
            catalog=CatalogView(root)
            result=catalog.signals({'modulation':['nfm'],'first_since':['2026-01-02T16:00:00+01:00'],'last_until':['2026-01-02T15:00:00Z']})
            self.assertEqual(result['total'],1);self.assertEqual(result['signals'][0]['signal_id'],'a')
            self.assertEqual(catalog.signals({'modulation':["NFM' OR 1=1 --"]})['total'],0)
            for query in [{'first_since':['2026-01-01']},{'last_until':['2026-01-01T12:00:00']},
                          {'first_since':['invalid']}]:
                with self.subTest(query=query),self.assertRaises(ValueError):catalog.signals(query)


if __name__=='__main__':unittest.main()
