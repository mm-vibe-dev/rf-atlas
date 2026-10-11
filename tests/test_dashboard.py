"""HTTP integration tests using temporary synthetic catalogs, never SDR hardware."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from rfatlas.dashboard import create_dashboard_server
from rfatlas.storage import open_database


class Controller:
    def __init__(self):
        self.active = False
        self.starts = []
        self.missing = False
        self.conflict = False

    def status(self):
        return {'supported': True, 'active': self.active, 'hardware': 'Synthetic controller'}

    def start(self, params):
        if self.missing:
            raise FileNotFoundError('private executable path')
        if self.conflict:
            raise RuntimeError('device ownership conflict')
        self.starts.append(params)
        self.active = True
        return self.status()

    def stop(self):
        self.active = False
        return self.status()

    def pause(self):
        if not self.active:
            raise RuntimeError('Not running')
        self.active = False
        return self.status() | {'resumable': True}

    def resume(self):
        self.active = True
        return self.status()


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'data').mkdir()
        with closing(open_database(root)) as db, db:
            for values in [('synthetic-a', 100000000, 'DIGITAL', '2FSK', 'EXAMPLE'),
                           ('synthetic-b', 101000000, 'ANALOG', 'NFM', 'UNKNOWN')]:
                db.execute('INSERT INTO signals(signal_id,center_frequency_hz,signal_class,modulation,protocol,first_seen,last_seen,occupied_bandwidth_hz,peak_snr_db,duty_cycle) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           values + ('2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z', 10000, 18, .2))
            db.execute('INSERT INTO captures(capture_id,signal_id,path,settings) VALUES(?,?,?,?)',
                       ('capture-a', 'synthetic-a', '/private/raw.cu8', json.dumps({'sample_rate': 250000, 'device_identity': 'PRIVATE', 'tool': '/private/tool'})))
            db.execute('INSERT INTO classifications(signal_id,created,label,confidence,evidence) VALUES(?,?,?,?,?)',
                       ('synthetic-a', '2026-01-02', '2FSK', .7, json.dumps({'kind': 'UNCALIBRATED_HEURISTIC_SUPPORT'})))
        image = root / 'diagnostic.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n')
        self.controller = Controller()
        self.server = create_dashboard_server(root, controller=self.controller,
            inventory_view=lambda: {'profiles': [{'name': 'Synthetic region'}], 'regions': [{'start_hz': 100000000, 'stop_hz': 101000000}], 'coverage': {'visited_hz': 1000000, 'valid_hz': 0}},
            queue_view=lambda: {'jobs': [{'signal_id': 'synthetic-a', 'status': 'cooldown', 'priority': 80, 'next_eligible': '2026-01-03'}], 'counts': {'cooldown': 1}},
            investigate=lambda signal_id: {'identifications': [{'protocol': 'UNKNOWN'}],
                                            'artifacts': [{'id': 'plot-a', 'label': 'Spectrum'}]},
            artifacts={'plot-a': image})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.token = self.request('GET', '/api/session')[1]['token']

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.addCleanup(conn.close)
        data = json.dumps(body) if body is not None else None
        actual_headers = {'Content-Type': 'application/json'}
        if hasattr(self, 'token'):
            actual_headers['X-RFAtlas-Token'] = self.token
        actual_headers.update(headers or {})
        conn.request(method, path, body=data, headers=actual_headers)
        response = conn.getresponse()
        output = response.read()
        if response.getheader('Content-Type', '').startswith('application/json'):
            output = json.loads(output)
        return response.status, output

    def test_catalog_filters_overview_and_investigation(self):
        status, data = self.request('GET', '/api/signals?known=true&min_hz=99000000&max_hz=100500000&signal_class=DIGITAL&protocol=EXAMPLE')
        self.assertEqual(status, 200)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['signals'][0]['signal_id'], 'synthetic-a')
        self.assertEqual(self.request('GET', '/api/signals?q=NFM')[1]['total'], 1)
        self.assertEqual(self.request('GET', '/api/signals?known=false')[1]['total'], 1)
        self.assertEqual(self.request('GET', '/api/overview')[1]['signal_count'], 2)
        status, detail = self.request('GET', '/api/signals/synthetic-a')
        self.assertEqual(status, 200)
        self.assertEqual(detail['recordings'][0]['metadata'], {'sample_rate': 250000})
        self.assertNotIn('PRIVATE', json.dumps(detail))
        self.assertNotIn('/private', json.dumps(detail))
        self.assertEqual(detail['classifications'][0]['evidence']['kind'], 'UNCALIBRATED_HEURISTIC_SUPPORT')
        self.assertEqual(detail['investigation']['identifications'][0]['protocol'], 'UNKNOWN')
        self.assertIn('investigation_status', detail['signal'])
        self.assertEqual(detail['investigations'], [])
        self.assertEqual(self.request('GET', '/api/signals/not-present')[0], 404)

    def test_filter_validation_parameterization_and_pagination(self):
        status, priorities = self.request('GET','/api/investigation-priorities?limit=1&recurrence=5')
        self.assertEqual(status,200);self.assertEqual(priorities['weights']['recurrence'],5)
        self.assertEqual(priorities['items'][0]['signal_id'],'synthetic-b')
        for query in ('limit=0','snr=nan','snr=-1','unknown=2'):
            self.assertEqual(self.request('GET','/api/investigation-priorities?'+query)[0],400)
        for query in ['min_hz=nan', 'max_hz=inf', 'limit=0', 'limit=1001', 'offset=-1', 'known=maybe']:
            self.assertEqual(self.request('GET', '/api/signals?' + query)[0], 400)
        self.assertEqual(self.request('GET', '/api/signals?protocol=%27%20OR%201%3D1--')[1]['total'], 0)
        self.assertEqual(len(self.request('GET', '/api/signals?limit=1&offset=1')[1]['signals']), 1)

    def test_inventory_queue_and_checkpoint_controls(self):
        status, inventory = self.request('GET', '/api/inventory')
        self.assertEqual(status, 200)
        self.assertEqual(inventory['coverage']['visited_hz'], 1000000)
        self.assertEqual(inventory['coverage']['valid_hz'], 0)
        self.assertEqual(self.request('GET', '/api/investigations')[1]['jobs'][0]['status'], 'cooldown')
        self.assertEqual(self.request('POST', '/api/scanner/pause', {})[0], 409)
        params = {'start_hz': 100000000, 'stop_hz': 101000000, 'profile': 'deep'}
        self.assertEqual(self.request('POST', '/api/scanner/start', params)[0], 200)
        self.assertEqual(self.controller.starts[-1]['profile'], 'deep')
        self.assertTrue(self.request('POST', '/api/scanner/pause', {})[1]['resumable'])
        self.assertTrue(self.request('POST', '/api/scanner/resume', {})[1]['active'])
        self.assertEqual(self.request('POST', '/api/scanner/pause', {'profile': 'quick'})[0], 400)
        self.assertEqual(self.request('POST', '/api/scanner/resume', {}, {'X-RFAtlas-Token': ''})[0], 403)
        self.request('POST', '/api/scanner/stop', {})
        self.assertEqual(self.request('POST', '/api/scanner/start', params | {'profile': 'shell'})[0], 400)

    def test_local_host_origin_and_csrf_protection(self):
        self.assertEqual(self.request('GET', '/api/session', headers={'Host': 'remote.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/session', headers={'Origin': 'https://remote.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/signals', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        params = {'start_hz': 100000000, 'stop_hz': 101000000}
        self.assertEqual(self.request('POST', '/api/scanner/start', params, {'X-RFAtlas-Token': ''})[0], 403)
        self.assertEqual(self.request('POST', '/api/scanner/start', params, {'Origin': 'http://remote.example'})[0], 403)
        origin = f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.request('POST', '/api/scanner/start', params, {'Origin': origin})[0], 200)

    def test_bounded_scanner_start_stop_and_single_owner(self):
        params = {'start_hz': 100000000, 'stop_hz': 101000000, 'gain': 'auto', 'duration': 10}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request('POST', '/api/scanner/start', params)[0], range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(len(self.controller.starts), 1)
        self.assertTrue(self.request('GET', '/api/scanner')[1]['active'])
        self.assertEqual(self.request('POST', '/api/scanner/stop', {})[0], 200)
        self.assertFalse(self.controller.active)
        self.controller.conflict = True
        self.assertEqual(self.request('POST', '/api/scanner/start', params)[0], 409)
        self.controller.conflict = False
        self.controller.missing = True
        status, data = self.request('POST', '/api/scanner/start', params)
        self.assertEqual(status, 503)
        self.assertNotIn('private', json.dumps(data))

    def test_scanner_rejects_unapproved_and_unbounded_inputs(self):
        base = {'start_hz': 100000000, 'stop_hz': 101000000}
        for invalid in [{'command': 'unexpected'}, {'duration': 301}, {'gain': 51},
                        {'bin_hz': 1}, {'start_hz': True}, {'stop_hz': 500000},
                        {'seconds': float('nan')}]:
            self.assertEqual(self.request('POST', '/api/scanner/start', base | invalid)[0], 400)
        self.assertEqual(self.request('POST', '/api/scanner/stop', {'anything': True})[0], 400)
        self.assertEqual(self.controller.starts, [])

    def test_artifact_allowlist_and_static_ui(self):
        status,body=self.request('GET','/assets/audio_clock.js')
        self.assertEqual(status,200);self.assertIn(b'RFAtlasAudioClock',body)
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'Signal catalog', body)
        self.assertIn(b'textContent', body)
        self.assertEqual(self.request('GET', '/artifacts/plot-a')[0], 200)
        for path in ['/artifacts/../../data/rf_atlas.sqlite', '/artifacts/%2e%2e%2fprivate', '/config.local.toml']:
            self.assertEqual(self.request('GET', path)[0], 404)
        with self.assertRaises(ValueError):
            create_dashboard_server(Path(self.temp.name), host='0.0.0.0')

    def test_missing_database_and_optional_controller(self):
        root = Path(self.temp.name) / 'empty'
        server = create_dashboard_server(root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            conn.request('GET', '/api/scanner')
            response = conn.getresponse()
            self.assertFalse(json.loads(response.read())['supported'])
            for endpoint in ('/api/inventory', '/api/investigations'):
                conn.request('GET', endpoint)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertFalse(json.loads(response.read())['supported'])
            conn.request('GET', '/api/signals')
            response = conn.getresponse()
            self.assertEqual(response.status, 503)
            response.read()
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
