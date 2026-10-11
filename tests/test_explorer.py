"""Measurement provenance, coverage and offline HTTP boundary regression."""
from contextlib import closing
from pathlib import Path
import http.client
import json
import tempfile
import threading
import unittest
from rfatlas.storage import open_database
from rfatlas.explorer import explorer_view
from rfatlas.dashboard import create_dashboard_server
from rfatlas.recording_actions import recording_action


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with closing(open_database(self.root)) as db, db:
            db.execute("INSERT INTO signals(signal_id,center_frequency_hz,protocol,modulation) VALUES('s',100050000,'UNKNOWN','UNKNOWN')")
            db.execute("INSERT INTO survey_runs(run_id,status,settings) VALUES('r','complete','{\"gain\":15.7}')")
            db.execute("INSERT INTO inventory_regions(region_id,start_hz,stop_hz) VALUES('a',100000000,100100000)")
            for valid,count,observed in [(1,0,1767225600),(1,1,1767225660),(0,99,1767225720)]:
                db.execute('INSERT INTO inventory_measurements(region_id,observed,valid,signal_count) VALUES(?,?,?,?)',('a',observed,valid,count))
            db.execute("INSERT INTO spectral_observations(signal_id,center_hz,observed,peak_snr_db,noise_db) VALUES('s',100050000,'2026-01-01T00:00:00Z',12,-60)")
        self.query = {'min_hz':['100000000'],'max_hz':['100200000']}

    def test_visit_states_and_unknown_gaps(self):
        view = explorer_view(self.root,self.query)
        self.assertEqual({r['status'] for r in view['coverage']}, {'NOT_SCANNED','INVALID','DETECTED','VALID_NO_DETECTION'})
        invalid = next(r for r in view['occupancy'] if r['invalid_regions'])
        self.assertIsNone(invalid['signal_count'])
        self.assertEqual(view['spectrum']['power_db'],[])
        self.assertNotIn('dBm', view['spectrum']['units'])

    def test_time_filter_and_validation(self):
        view = explorer_view(self.root, self.query | {'since':['2026-01-02']})
        self.assertEqual(view['history'],[])
        self.assertEqual(view['occupancy'],[])
        for query in ({'min_hz':['nan']},{'unknown':['yes']},{'since':['bad']}, {'since':['2027-01-01'],'until':['2026-01-01']}):
            with self.assertRaises(ValueError): explorer_view(self.root,query)

    def test_retained_spectrum_provenance_and_bounds(self):
        raw = self.root/'data'/'spectrum.csv'
        raw.write_text('2026-01-01,00:00:00Z,100000000,100100000,10000,100,'+','.join(str(-60+i) for i in range(10))+'\n')
        with closing(open_database(self.root)) as db, db:
            db.execute('INSERT INTO scan_regions VALUES(?,?,?,?,?,?)',('r',100000000,100100000,'complete','2026-01-01T00:00:00Z',str(raw)))
        view=explorer_view(self.root,{})
        self.assertEqual(view['bounds']['max_hz'],100100000)
        self.assertEqual(len(view['spectrum']['power_db']),10)
        self.assertEqual(view['spectrum']['settings']['gain'],15.7)
        self.assertNotIn(str(self.root),json.dumps(view))
        powers=[-60]*2048;powers[1]=-10
        raw.write_text('2026-01-01,00:00:00Z,100000000,100100000,'+str(100000/2048)+',100,'+','.join(map(str,powers))+'\n')
        view=explorer_view(self.root,{})
        self.assertEqual(len(view['spectrum']['power_db']),1024)
        self.assertEqual(max(view['spectrum']['power_db']),-10)
        raw.write_text('invalid')
        self.assertEqual(explorer_view(self.root,{})['waterfall']['rows'],[])

    def test_http_security_and_recording_actions(self):
        calls=[]
        server=create_dashboard_server(self.root,recording_action=lambda cid,action:calls.append((cid,action)) or {'ok':True})
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(method,path,body=None,headers=None):
            client=http.client.HTTPConnection('127.0.0.1',server.server_port)
            try:
                client.request(method,path,body,headers or {});response=client.getresponse()
                return response.status,response.read()
            finally:client.close()
        self.assertEqual(request('GET','/api/explorer')[0],200)
        self.assertEqual(request('GET','/assets/../storage.py')[0],404)
        self.assertEqual(request('POST','/api/recordings/a/replay','{}',{'Content-Type':'application/json'})[0],403)
        token=json.loads(request('GET','/api/session')[1])['token']
        status,_=request('POST','/api/recordings/a/replay','{}',{'Content-Type':'application/json','X-RFAtlas-Token':token})
        self.assertEqual(status,200);self.assertEqual(calls,[('a','replay')])

    def test_recording_paths_are_not_browser_input(self):
        with closing(open_database(self.root)) as db, db:
            db.execute('INSERT INTO captures(capture_id,path) VALUES(?,?)',('outside',str(self.root/'outside.cu8')))
        for capture in ('outside','missing','../outside'):
            with self.assertRaises(ValueError):recording_action(self.root,capture,'replay')

    def test_replay_and_reanalysis_preserve_original_measurements(self):
        from rfatlas.iq import checksum
        folder=self.root/'data'/'captures';folder.mkdir()
        path=folder/'synthetic.cu8';path.write_bytes(b'\x80\x80'*8192)
        path.with_suffix('.json').write_text(json.dumps({'format':'cu8','sample_rate':250000,
            'center_hz':100050000,'sample_count':8192,'sha256':checksum(path)}))
        with closing(open_database(self.root)) as db,db:
            db.execute('INSERT INTO captures(capture_id,signal_id,path,settings) VALUES(?,?,?,?)',('synthetic','s',str(path),'{}'))
            before=db.execute('SELECT * FROM spectral_observations').fetchall()
        self.assertEqual(recording_action(self.root,'synthetic','replay')['result']['modulation'],'UNKNOWN')
        result=recording_action(self.root,'synthetic','reanalyze')
        self.assertFalse(result['result']['protocol_verified'])
        with closing(open_database(self.root)) as db:
            self.assertEqual(before,db.execute('SELECT * FROM spectral_observations').fetchall())
            self.assertEqual(db.execute('SELECT count(*) FROM identification_runs').fetchone()[0],1)

    def test_request_worker_overload_is_rejected(self):
        from rfatlas.dashboard import BoundedHTTPServer
        from unittest.mock import Mock,patch
        server=BoundedHTTPServer(('127.0.0.1',0),__import__('http.server',fromlist=['BaseHTTPRequestHandler']).BaseHTTPRequestHandler)
        self.addCleanup(server.server_close)
        for _ in range(16):server.slots.acquire(False)
        request=Mock()
        with patch.object(server,'shutdown_request') as shutdown:
            server.process_request(request,('127.0.0.1',1))
            self.assertIn(b'503',request.sendall.call_args.args[0]);shutdown.assert_called_once_with(request)
        for _ in range(16):server.slots.release()


if __name__=='__main__':unittest.main()
