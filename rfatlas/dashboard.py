"""Local-only dashboard with read-only catalog queries and injected scanner control.

No hardware is opened here. A controller must enforce the application's device
lock, process bounds, and approved acquisition settings in addition to these
HTTP checks. Artifact identifiers are explicit allowlist keys, never paths.
"""
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


SIGNAL_COLUMNS = ('signal_id', 'center_frequency_hz', 'frequency_min_hz',
                  'frequency_max_hz', 'occupied_bandwidth_hz', 'first_seen',
                  'last_seen', 'observation_count', 'peak_snr_db', 'median_snr_db',
                  'duty_cycle', 'signal_class', 'modulation', 'protocol',
                  'protection', 'confidence', 'evidence')
CAPTURE_KEYS = ('sample_rate', 'sample_rate_hz', 'center_hz', 'desired_frequency_hz',
                'duration_s', 'actual_duration_s', 'gain', 'gain_db', 'sample_rate_reported_hz', 'sample_rate_basis', 'format', 'sha256',
                'tuning_offset_hz', 'sample_count', 'created', 'engine_version')


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return value


class BoundedHTTPServer(ThreadingHTTPServer):
    """Cap request workers and idle sockets independently of acquisition."""
    daemon_threads = True
    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()


PROTOCOL_RF={'RDS':('ANALOG','WFM'),'DMR':('DIGITAL','4FSK'),'AX.25':('DIGITAL','AFSK'),
             'AIS':('DIGITAL','GMSK'),'ADS-B':('DIGITAL','PPM'),'POCSAG':('DIGITAL','2FSK'),
             'Fineoffset-WH2':('DIGITAL','OOK'),'Fineoffset-WH5':('DIGITAL','OOK'),
             'Fineoffset-WH24':('DIGITAL','2FSK'),'Fineoffset-WH1050':('DIGITAL','OOK')}


def protocol_column(column):
    index=0 if column=='signal_class' else 1
    return 'CASE protocol '+''.join("WHEN '"+p+"' THEN '"+value[index]+"' " for p,value in PROTOCOL_RF.items())+'ELSE '+column+' END'


def inventory_signal(row):
    item=dict(row);known=PROTOCOL_RF.get(item.get('protocol'))
    if known:
        item['stored_characterization']={key:item.get(key) for key in ('signal_class','modulation')}
        item['signal_class'],item['modulation']=known
        item['rf_family_basis']='VERIFIED_PROTOCOL_INTERPRETATION'
    else:item['rf_family_basis']='STORED_WAVEFORM_HYPOTHESIS'
    item['metadata_protocols']=['RDS'] if item.get('protocol')=='RDS' else []
    return item


class CatalogView:
    """Fresh read-only connection per request, also safe across HTTP threads."""
    def __init__(self, root):
        self.path = Path(root) / 'data' / 'rf_atlas.sqlite'

    def connect(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True,
                             timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def signals(self, query):
        from .timestamps import sql_utc
        clauses, args = [], []
        for name, operator in [('min_hz', '>='), ('max_hz', '<=')]:
            if name in query:
                value = float(query[name][0])
                if not math.isfinite(value) or value < 0:
                    raise ValueError('Frequency filters must be finite and nonnegative')
                clauses.append('center_frequency_hz ' + operator + ' ?')
                args.append(value)
        for key in ('protocol', 'signal_class', 'modulation'):
            if query.get(key, [''])[0]:
                column=protocol_column(key) if key in ('signal_class','modulation') else key
                clauses.append(column + ' = ? COLLATE NOCASE')
                args.append(query[key][0])
        family=query.get('family',[''])[0]
        if family:
            from .band_context import FAMILIES
            if family not in FAMILIES:raise ValueError('Unknown band-context family')
            intervals=FAMILIES[family]
            clauses.append('('+' OR '.join('center_frequency_hz BETWEEN ? AND ?' for _ in intervals)+')')
            args.extend(value for interval in intervals for value in interval)
        from datetime import datetime
        for key,column,operator in [('first_since','first_seen','>='),('last_until','last_seen','<=')]:
            if query.get(key,[''])[0]:
                value=query[key][0]
                parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                if parsed.tzinfo is None: raise ValueError('Explicit timezone required')
                from .timestamps import sql_utc
                clauses.append('julianday('+sql_utc(column)+') '+operator+' julianday(?)'); args.append(value)
        known = query.get('known', [''])[0]
        analysis_filter=query.get('analysis_state',[''])[0]
        if analysis_filter not in ('','unclassified','recorded'):raise ValueError('Unknown analysis filter')
        if analysis_filter=='unclassified':clauses.append('NOT EXISTS(SELECT 1 FROM identification_runs i WHERE i.signal_id=signals.signal_id)')
        if analysis_filter=='recorded':clauses.append('EXISTS(SELECT 1 FROM captures c WHERE c.signal_id=signals.signal_id)')
        if known not in ('', 'true', 'false'):
            raise ValueError('known must be true or false')
        if known:
            clauses.append("COALESCE(protocol,'UNKNOWN') " + ('!=' if known == 'true' else '=') + " 'UNKNOWN'")
        if query.get('q', [''])[0]:
            clauses.append("(signal_id LIKE ? OR protocol LIKE ? OR modulation LIKE ? OR printf('%.6f',center_frequency_hz/1000000.0) LIKE ? OR EXISTS "
                           '(SELECT 1 FROM signal_annotations a WHERE a.signal_id=signals.signal_id AND '
                           '(json_extract(a.value,\'$.name\') LIKE ? OR json_extract(a.value,\'$.tags\') LIKE ?)))')
            args.extend(['%' + query['q'][0][:100] + '%'] * 6)
        orders={'recent':'julianday('+sql_utc('last_seen')+') DESC',
                'frequency':'center_frequency_hz ASC','protocol':'protocol COLLATE NOCASE ASC',
                'favorite':"COALESCE((SELECT json_extract(a.value,'$.favorite') FROM signal_annotations a WHERE a.signal_id=signals.signal_id),0) DESC"}
        order=query.get('sort',['recent'])[0]
        if order not in orders:raise ValueError('Invalid signal sort')
        limit = int(query.get('limit', ['200'])[0])
        offset = int(query.get('offset', ['0'])[0])
        if not 1 <= limit <= 1000 or not 0 <= offset <= 1000000:
            raise ValueError('Invalid page bounds')
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with closing(self.connect()) as db:
            count = db.execute('SELECT COUNT(*) FROM signals' + where, args).fetchone()[0]
            rows = db.execute('SELECT ' + ','.join(SIGNAL_COLUMNS) + ' FROM signals' + where +
                              ' ORDER BY '+orders[order]+',signal_id LIMIT ? OFFSET ?',
                              args + [limit, offset]).fetchall()
        from .annotations import read as read_annotation
        output=[]
        with closing(self.connect()) as db:
            for row in rows:
                item=inventory_signal(row)
                item['annotations']=read_annotation(db,item['signal_id'])
                from .pipeline import signal_status
                item['analysis_status']=signal_status(db,item['signal_id'])
                from .band_context import contexts
                item['band_context']=contexts(item['center_frequency_hz']) if item['center_frequency_hz'] else []
                output.append(item)
        return {'signals': output, 'total': count,
                'limit': limit, 'offset': offset}

    def overview(self):
        with closing(self.connect()) as db:
            count = db.execute('SELECT COUNT(*) FROM signals').fetchone()[0]
            coverage = [dict(r) for r in db.execute(
                'SELECT start_hz,stop_hz,status,completed FROM scan_regions ORDER BY completed DESC LIMIT 100')]
            recent = [dict(r) for r in db.execute(
                'SELECT observed,center_hz,peak_snr_db,average_snr_db,signal_id FROM spectral_observations ORDER BY observation_id DESC LIMIT 200')]
        return {'signal_count': count, 'coverage': coverage, 'activity_history': recent,
                'strength_unit': 'SNR dB (relative to estimated noise; not calibrated power)'}

    def detail(self, signal_id):
        with closing(self.connect()) as db:
            existing = {row[1] for row in db.execute('PRAGMA table_info(signals)')}
            extra = [name for name in ('frequency_uncertainty_hz', 'investigation_status',
                     'last_verified', 'classifier_version', 'decoder_version') if name in existing]
            signal = db.execute('SELECT ' + ','.join([*SIGNAL_COLUMNS, *extra]) +
                                ' FROM signals WHERE signal_id=?', (signal_id,)).fetchone()
            if signal is None:
                return None
            observations = [dict(r) for r in db.execute(
                'SELECT observation_id,observed,observed_original,observed_utc,timestamp_basis,center_hz,start_hz,stop_hz,peak_snr_db,average_snr_db,noise_db FROM spectral_observations WHERE signal_id=? ORDER BY observation_id DESC LIMIT 200', (signal_id,))]
            from .fingerprints import history
            fingerprint = history(db, signal_id)
            classifications = [dict(r) for r in db.execute(
                'SELECT classification_id,created,label,confidence,evidence FROM classifications WHERE signal_id=? ORDER BY classification_id DESC LIMIT 100', (signal_id,))]
            events = [dict(r) for r in db.execute(
                'SELECT created,kind,evidence FROM signal_events WHERE signal_id=? ORDER BY event_id DESC LIMIT 100', (signal_id,))]
            jobs = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='investigation_jobs'").fetchone():
                jobs = [dict(r) for r in db.execute('SELECT job_id,status,priority,reason,created,updated,next_eligible,attempts,result_key,error FROM investigation_jobs WHERE signal_id=? ORDER BY created DESC LIMIT 100', (signal_id,))]
            captures = []
            for row in db.execute('SELECT capture_id,settings FROM captures WHERE signal_id=? LIMIT 100', (signal_id,)):
                settings = _json_value(row['settings'])
                captures.append({'capture_id': row['capture_id'], 'metadata':
                                 {k: settings[k] for k in CAPTURE_KEYS if k in settings}
                                 if isinstance(settings, dict) else {}})
        associated=[]
        with closing(self.connect()) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_observations'").fetchone():
                associated=[dict(row) for row in db.execute('SELECT DISTINCT e.entity_id,e.kind,e.protocol,e.model,e.identifier,e.category FROM entities e JOIN entity_observations o USING(entity_id) WHERE o.signal_id=? LIMIT 100',(signal_id,))]
        for row in classifications + events:
            row['evidence'] = _json_value(row['evidence'])
        from .annotations import read as read_annotation
        with closing(self.connect()) as db:annotation=read_annotation(db,signal_id)
        display_signal=inventory_signal(signal)
        from .pipeline import signal_status
        from .similarity import search
        with closing(self.connect()) as db:
            analysis_status=signal_status(db,signal_id);similar_signals=search(db,signal_id)
        from .priorities import observation_history
        with closing(self.connect()) as db:
            latest=db.execute('SELECT result FROM identification_runs WHERE signal_id=? ORDER BY created DESC LIMIT 1',(signal_id,)).fetchone()
            from .investigation_feedback import history
            feedback=history(db)
        deep=_json_value(latest[0]).get('deep_investigation') if latest else None
        latest_result=_json_value(latest[0]) if latest else {}
        return {'messages':latest_result.get('messages',[]),'ml_analysis':latest_result.get('ml_analysis'),'investigation_feedback':feedback,'deep_investigation':deep,'observation_summary':observation_history(observations),'analysis_status':analysis_status,'similar_signals':similar_signals,'annotations':annotation,'signal': display_signal, 'observations': observations,
                'classifications': classifications, 'events': events, 'recordings': captures,
                'investigations': jobs, 'associated_entities':associated, 'fingerprint':fingerprint}


def _scan_parameters(payload):
    allowed = {'start_hz', 'stop_hz', 'bin_hz', 'seconds', 'duration', 'gain', 'profile'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('Unknown acquisition parameters')
    if not {'start_hz', 'stop_hz'} <= set(payload):
        raise ValueError('start_hz and stop_hz are required')
    for key, value in payload.items():
        if key == 'profile':
            if value not in ('quick', 'balanced', 'deep'):
                raise ValueError('Unsupported survey profile')
            continue
        if key == 'gain' and value == 'auto':
            continue
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Acquisition parameters must be finite numbers')
    if not 500000 <= payload['start_hz'] < payload['stop_hz'] <= 1766000000:
        raise ValueError('Frequency range outside receiver limits')
    if not 100 <= payload.get('bin_hz', 1000) <= 1000000:
        raise ValueError('bin_hz outside approved bounds')
    if not 1 <= payload.get('seconds', 1) <= 60 or not 1 <= payload.get('duration', 10) <= 300:
        raise ValueError('Scan durations outside approved bounds')
    gain = payload.get('gain', 'auto')
    if gain != 'auto' and not 0 <= gain <= 50:
        raise ValueError('Gain outside approved bounds')
    return payload


def create_dashboard_server(root, host='127.0.0.1', port=0, *, controller=None,
                            investigate=None, artifacts=None, inventory_view=None,
                            queue_view=None, recording_action=None, runtime=None):
    """Return a server; caller owns serve_forever/shutdown/server_close.

    ``controller.status/start/stop`` return JSON dictionaries. ``investigate``
    receives a signal ID and returns a JSON-safe dictionary of identification
    history and artifact IDs, without absolute paths, raw logs or configuration.
    ``artifacts`` maps opaque IDs to already approved diagnostic PNG files.
    """
    if host != '127.0.0.1':
        raise ValueError('Dashboard binds to IPv4 loopback only')
    catalog = CatalogView(root)
    from .discovery import DiscoveryView,HealthMonitor
    discovery=DiscoveryView(root,started=getattr(runtime,'started',None),health=HealthMonitor(root,getattr(getattr(runtime,'config',None),'retention_bytes',None)))
    token = secrets.token_urlsafe(32)
    action_lock = threading.Lock()
    artifact_map = {str(k): Path(v).resolve() for k, v in (artifacts or {}).items()}
    html = (Path(__file__).parent / 'web_assets' / 'index.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            try: super().handle()
            except ConnectionError: pass  # Browser closure is handled by the monitor lease.

        def log_message(self, *args):
            pass  # No request paths or private observations in runtime logs.

        def reply(self, status, body, mime='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False, default=str).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            alternate = f'localhost:{self.server.server_port}'
            if self.client_address[0] != '127.0.0.1' or self.headers.get('Host') not in (expected, alternate):
                self.reply(403, {'error': 'Localhost Host required'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin not in ('http://' + expected, 'http://' + alternate):
                self.reply(403, {'error': 'Local origin required'})
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.reply(403, {'error': 'Cross-site requests rejected'})
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            try:
                if path == '/':
                    return self.reply(200, html, 'text/html; charset=utf-8')
                if path == '/api/session':
                    return self.reply(200, {'token': token})
                if path == '/api/monitor':
                    if runtime is None: return self.reply(503,{'error':'Runtime unavailable'})
                    query=parse_qs(parsed.query)
                    if set(query)-{'since'} or any(len(v)!=1 for v in query.values()): raise ValueError('Invalid monitor cursor')
                    since=int(query.get('since',['0'])[0])
                    if since<0: raise ValueError('Invalid monitor cursor')
                    return self.reply(200,runtime.monitor.status(since=since))
                if path == '/api/monitor/audio':
                    if runtime is None: return self.reply(503,{'error':'Runtime unavailable'})
                    query=parse_qs(parsed.query)
                    if set(query)-{'since','epoch'} or any(len(v)!=1 for v in query.values()): raise ValueError('Invalid audio cursor')
                    since=int(query.get('since',['0'])[0]); epoch=int(query.get('epoch',['-1'])[0])
                    pcm,sequence,current=runtime.monitor.audio_packet(since,epoch)
                    self.send_response(200)
                    for key,value in {'Content-Type':'application/octet-stream','Content-Length':str(len(pcm)),
                        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                        'X-Audio-Sequence':str(sequence),'X-Audio-Epoch':str(current),'X-Audio-Rate':'48000'}.items(): self.send_header(key,value)
                    self.end_headers(); self.wfile.write(pcm); return
                if path == '/api/discovery':
                    return self.reply(200,discovery.snapshot(parse_qs(parsed.query)))
                if path == '/api/discovery-report':
                    from .performance import discovery_report,compare_reports
                    from datetime import datetime,timedelta,timezone
                    query=parse_qs(parsed.query)
                    if set(query)-{'since','until','compare'} or any(len(v)!=1 for v in query.values()):
                        raise ValueError('Unsupported report parameter')
                    until=query.get('until',[datetime.now(timezone.utc).isoformat()])[0]
                    since=query.get('since',[(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()])[0]
                    report=discovery_report(root,since,until)
                    if query.get('compare',['false'])[0] not in ('true','false'):raise ValueError('Invalid comparison flag')
                    if query.get('compare',['false'])[0]=='true':
                        start=datetime.fromisoformat(report['started']);end=datetime.fromisoformat(report['ended'])
                        previous=discovery_report(root,(start-(end-start)).isoformat(),start.isoformat())
                        report['comparison']=compare_reports(previous,report)
                    return self.reply(200,report)
                if path == '/api/local-alerts':
                    query=parse_qs(parsed.query)
                    if set(query)-{'after'} or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid alert query')
                    from .runtime import load_settings
                    from .local_alerts import changes
                    with closing(catalog.connect()) as db:
                        enabled=load_settings(root)['local_alerts']
                        result=changes(db,int(query['after'][0]) if enabled and 'after' in query else None)
                    return self.reply(200,dict(result,enabled=enabled))
                if path == '/api/signals':
                    return self.reply(200, catalog.signals(parse_qs(parsed.query)))
                if path.startswith('/api/signal-lab/'):
                    from .signal_lab import view
                    return self.reply(200,view(root,path[len('/api/signal-lab/'):]))
                if path == '/api/investigation-recipes':
                    from .deep_investigation import recipes
                    return self.reply(200,dict(recipes=recipes(),presets=runtime.deep.presets() if runtime else []))
                if path == '/api/investigation-priorities':
                    from .priorities import queue
                    query=parse_qs(parsed.query)
                    if set(query)-{'limit','recording','recurrence','snr','unresolved','digital','noise_penalty'} or any(len(v)!=1 for v in query.values()):raise ValueError('Invalid priority query')
                    limit=int(query.pop('limit',['100'])[0]);selected={k:float(v[0]) for k,v in query.items()}
                    with closing(catalog.connect()) as db:result=queue(db,limit=limit,selected=selected)
                    return self.reply(200,result)
                if path == '/api/overview':
                    return self.reply(200, catalog.overview())
                if path == '/api/explorer':
                    from .explorer import explorer_view
                    return self.reply(200, explorer_view(root, parse_qs(parsed.query)))
                if path.startswith('/assets/'):
                    name = path[len('/assets/'):]
                    allowed = {'uPlot.iife.min.js':'application/javascript',
                               'uPlot.min.css':'text/css', 'explorer.js':'application/javascript',
                               'explorer.css':'text/css','radio.js':'application/javascript',
                               'radio.css':'text/css','console.js':'application/javascript','console.css':'text/css','monitor.js':'application/javascript','signal_lab.js':'application/javascript','audio_clock.js':'application/javascript','experience.js':'application/javascript','uPlot.LICENSE':'text/plain'}
                    if name in allowed:
                        return self.reply(200, (Path(__file__).parent/'web_assets'/name).read_bytes(), allowed[name])
                    return self.reply(404, {'error':'Not found'})
                if path == '/api/inventory':
                    return self.reply(200, inventory_view() if inventory_view else
                                      {'supported': False, 'regions': [], 'status': 'Inventory service unavailable'})
                if path == '/api/investigations':
                    return self.reply(200, queue_view() if queue_view else
                                      {'supported': False, 'jobs': [], 'status': 'Investigation queue unavailable'})
                if path == '/api/runtime':
                    return self.reply(200, runtime.status() if runtime else {'mode':'idle','receiver':{'status':'unchecked'}})
                if path == '/api/reanalysis':
                    return self.reply(200,runtime.reanalysis.status() if runtime else {'active':False,'jobs':[],'status':'Unavailable'})
                if path == '/api/frames' or path == '/api/entities' or path.startswith('/api/entities/'):
                    from .entities import read_frames, list_entities, entity_detail
                    with closing(sqlite3.connect((Path(root)/'data/rf_atlas.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                        query=parse_qs(parsed.query)
                        allowed={'signal_id','limit'} if path=='/api/frames' else {'kind','protocol','q','limit','category','first_since','last_until'} if path=='/api/entities' else set()
                        if set(query)-allowed or any(len(v)!=1 for v in query.values()): raise ValueError('Invalid filter')
                        limit=int(query.get('limit',['100'])[0])
                        if path=='/api/frames': result=read_frames(db,signal_id=query.get('signal_id',[None])[0],limit=limit)
                        elif path=='/api/entities': result=list_entities(db,kind=query.get('kind',[None])[0],protocol=query.get('protocol',[None])[0],q=query.get('q',[''])[0],limit=limit,category=query.get('category',[None])[0],first_since=query.get('first_since',[None])[0],last_until=query.get('last_until',[None])[0])
                        else: result=entity_detail(db,path[len('/api/entities/'):])
                    return self.reply(200 if result is not None else 404,result or {'error':'Entity not found'})
                if path == '/api/scanner':
                    return self.reply(200, controller.status() if controller else
                                      {'supported': False, 'active': False, 'status': 'Scanner controls unavailable'})
                if path.startswith('/api/signals/'):
                    signal_id = path[len('/api/signals/'):]
                    detail = catalog.detail(signal_id)
                    if detail is None:
                        return self.reply(404, {'error': 'Signal not found'})
                    if investigate:
                        detail['investigation'] = investigate(signal_id)
                    for recording in detail['recordings']:
                        recording['supports_actions'] = recording_action is not None
                    return self.reply(200, detail)
                if path.startswith('/artifacts/'):
                    artifact = artifact_map.get(path[len('/artifacts/'):])
                    if artifact and artifact.suffix.lower() == '.png' and artifact.is_file() and artifact.stat().st_size <= 16 * 1024 * 1024:
                        return self.reply(200, artifact.read_bytes(), 'image/png')
                self.reply(404, {'error': 'Not found'})
            except (ValueError, TypeError, OverflowError):
                self.reply(400, {'error': 'Invalid request parameters'})
            except (sqlite3.Error, OSError):
                self.reply(503, {'error': 'Catalog or artifact unavailable'})
            except Exception:
                self.reply(500, {'error': 'Investigation service failed'})

        def do_POST(self):
            if not self.local_request():
                return
            if not secrets.compare_digest(self.headers.get('X-RFAtlas-Token', '').encode('utf-8'), token.encode('ascii')):
                return self.reply(403, {'error': 'Session token required'})
            path = urlsplit(self.path).path
            if path.startswith('/api/signals/') and path.endswith('/annotations'):
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=16384 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('JSON required')
                    value=json.loads(self.rfile.read(size))
                    identity=unquote(path[len('/api/signals/'):-len('/annotations')])
                    if not identity or '/' in identity or len(identity)>128:raise ValueError('Invalid signal')
                    from .annotations import save
                    with closing(sqlite3.connect(catalog.path,timeout=2)) as db:
                        db.execute('PRAGMA foreign_keys=ON')
                        result=save(db,identity,value)
                    return self.reply(200,result)
                except (ValueError,TypeError):return self.reply(400,{'error':'Invalid annotation'})
                except sqlite3.Error:return self.reply(503,{'error':'Annotation database unavailable'})
            if path.startswith('/api/runtime/'):
                if runtime is None: return self.reply(503,{'error':'Runtime unavailable'})
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=4096 or self.headers.get('Content-Type','').split(';')[0]!='application/json': raise ValueError('JSON required')
                    payload=json.loads(self.rfile.read(size))
                    if path=='/api/runtime/monitor/heartbeat':
                        if payload!={}: raise ValueError('Empty object required')
                        runtime.monitor.heartbeat()
                        return self.reply(200,{'ok':True})
                    if not action_lock.acquire(blocking=False): return self.reply(409,{'error':'Another control action is active'})
                    try: result=runtime.action(path[len('/api/runtime/'):],payload)
                    finally: action_lock.release()
                    return self.reply(200,result)
                except (ValueError,TypeError): return self.reply(400,{'error':'Invalid runtime settings; check receiver limits and selected profile'})
                except (RuntimeError,OSError): return self.reply(409,{'error':'Receiver unavailable or busy. Check existing tools and other receiver applications.'})
                except Exception: return self.reply(503,{'error':'Runtime action failed; acquisition is unavailable'})
            if path.startswith('/api/recordings/'):
                parts = path.split('/')
                if len(parts) != 5 or parts[4] not in ('replay','reanalyze'):
                    return self.reply(404, {'error':'Not found'})
                if recording_action is None:
                    return self.reply(503, {'error':'Offline recording actions unavailable'})
                try:
                    if self.headers.get('Content-Type','').split(';')[0] != 'application/json' or int(self.headers.get('Content-Length','0')) != 2 or self.rfile.read(2) != b'{}':
                        raise ValueError('Empty JSON object required')
                    if not action_lock.acquire(blocking=False):
                        return self.reply(409, {'error':'Another action is active'})
                    try:
                        result = recording_action(parts[3],parts[4])
                    finally:
                        action_lock.release()
                    return self.reply(200,result)
                except (ValueError,TypeError):
                    return self.reply(400,{'error':'Invalid recording request'})
                except (OSError,RuntimeError,sqlite3.Error):
                    return self.reply(503,{'error':'Recording unavailable or outside analysis budget'})
                except Exception:
                    return self.reply(500,{'error':'Offline analysis failed'})
            if path not in ('/api/scanner/start', '/api/scanner/stop', '/api/scanner/pause', '/api/scanner/resume'):
                return self.reply(404, {'error': 'Not found'})
            if controller is None:
                return self.reply(503, {'error': 'Scanner controls unavailable'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    raise ValueError('Invalid request size')
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('JSON required')
                payload = json.loads(self.rfile.read(size))
                with action_lock:
                    if path.endswith('/start'):
                        payload = _scan_parameters(payload)
                        if controller.status().get('active'):
                            return self.reply(409, {'error': 'An acquisition is already active'})
                        result = controller.start(payload)
                    else:
                        if payload != {}:
                            raise ValueError('Control takes no acquisition parameters')
                        action = path.rsplit('/', 1)[-1]
                        callback = getattr(controller, action, None)
                        if callback is None:
                            return self.reply(503, {'error': 'Control unavailable'})
                        result = callback()
                self.reply(200, result)
            except (ValueError, TypeError, json.JSONDecodeError):
                self.reply(400, {'error': 'Invalid acquisition request'})
            except FileNotFoundError:
                self.reply(503, {'error': 'Required acquisition tool unavailable'})
            except RuntimeError:
                self.reply(409, {'error': 'Device busy or acquisition unavailable'})
            except Exception:
                self.reply(500, {'error': 'Acquisition controller failed'})

    server = BoundedHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
