"""Private, opt-in recovered frame and protocol-address inventory persistence.

An entity is a repeated verified protocol identifier, not proof of physical
identity. Shared frequencies and FCC candidates never create or merge entities.
"""
import copy
import hashlib
import json
import re
from datetime import datetime, timezone


def ensure_evidence_schema(db):
    for statement in (
        '''CREATE TABLE IF NOT EXISTS recovered_frames(
          frame_id TEXT PRIMARY KEY, run_key TEXT NOT NULL REFERENCES identification_runs(run_key),
          signal_id TEXT NOT NULL REFERENCES signals(signal_id), observed TEXT NOT NULL,
          frequency_hz REAL, protocol TEXT NOT NULL, decoder TEXT NOT NULL, decoder_version TEXT NOT NULL,
          status TEXT NOT NULL, frame_length INTEGER, raw_hex TEXT, start_sample INTEGER,
          sync_valid INTEGER NOT NULL, checksum_valid INTEGER NOT NULL,
          sync_reference TEXT NOT NULL, checksum_reference TEXT NOT NULL, parsed_fields TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS entities(
          entity_id TEXT PRIMARY KEY, kind TEXT NOT NULL, protocol TEXT NOT NULL, model TEXT NOT NULL,
          identifier TEXT NOT NULL, category TEXT, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
          latest_fields TEXT NOT NULL, UNIQUE(kind,protocol,model,identifier))''',
        '''CREATE TABLE IF NOT EXISTS entity_observations(
          entity_id TEXT NOT NULL REFERENCES entities(entity_id), frame_id TEXT NOT NULL REFERENCES recovered_frames(frame_id),
          signal_id TEXT NOT NULL REFERENCES signals(signal_id), observed TEXT NOT NULL,
          frequency_hz REAL, fields TEXT NOT NULL, PRIMARY KEY(entity_id,frame_id))''',
        'CREATE INDEX IF NOT EXISTS frames_signal_time ON recovered_frames(signal_id,observed DESC)',
        'CREATE INDEX IF NOT EXISTS entity_last_seen ON entities(last_seen DESC)',
        'CREATE INDEX IF NOT EXISTS entity_observation_time ON entity_observations(entity_id,observed DESC)',
    ):
        db.execute(statement)


def private_result(result, *, retain_frames=False, retain_identifiers=False):
    """Return a copy whose storage matches independent operator preferences.

    Raw bytes can contain identifiers even when parsed identity retention is
    disabled. The UI must describe that implication of enabling raw retention.
    """
    if type(retain_frames) is not bool or type(retain_identifiers) is not bool:
        raise ValueError('Retention preferences must be boolean')
    saved = copy.deepcopy(result)
    # Symbol candidates can contain payload and identifiers just like raw bytes.
    if not retain_frames:
        for candidate in saved.get('demodulation_candidates',[]):
            for key in ('symbols','candidate_bits','relative_reliability'):candidate.pop(key,None)
    for message in saved.get('messages',[]):
        if not retain_identifiers:message['fields']={}
        if not retain_frames:message.pop('raw_hex',None)
    for attempt in saved.get('attempts', []):
        for frame in attempt.get('frames', []):
            if not retain_frames:
                frame['raw_hex'] = None
            if not retain_identifiers:
                frame['parsed_fields'] = {}
        if not retain_frames and not retain_identifiers:
            attempt['frames'] = []
    saved['retention'] = {'frames': retain_frames, 'identifiers': retain_identifiers}
    return saved


def _identity(protocol, decoder, fields):
    """Only supported audited decoder identifiers may establish an entity."""
    if protocol=='AIS' and decoder=='native-ais-position' and isinstance(fields.get('mmsi'),str) and re.fullmatch('[0-9]{9}',fields['mmsi']):
        return 'station','AIS-MMSI',fields['mmsi']
    if protocol == 'ADS-B' and decoder == 'native-mode-s-df17' and isinstance(fields.get('icao24'), str) and re.fullmatch('[0-9A-F]{6}', fields['icao24']):
        return 'station', 'DF17', fields['icao24']
    if protocol == 'AX.25' and decoder == 'native-ax25-afsk1200-ui' and isinstance(fields.get('source'), str) and re.fullmatch('[A-Z0-9]{1,6}(?:-(?:[1-9]|1[0-5]))?', fields['source']):
        return 'station', 'UI-AFSK1200', fields['source']
    if protocol == 'RDS' and decoder == 'native-fm-rds' and isinstance(fields.get('pi'),str) and re.fullmatch('[0-9A-F]{4}',fields['pi']):
        return 'station','RDS-PI',fields['pi']
    if protocol in ('Fineoffset-WH2', 'Fineoffset-WH5','Fineoffset-WH24') and decoder in ('rtl_433-wh2', 'rtl_433-wh5','rtl_433-wh24'):
        if fields.get('model') == protocol and decoder == {'Fineoffset-WH2':'rtl_433-wh2','Fineoffset-WH5':'rtl_433-wh5','Fineoffset-WH24':'rtl_433-wh24'}[protocol] and type(fields.get('sensor_id')) is int and 0 <= fields['sensor_id'] <= 255:
            return 'device', protocol, str(fields['sensor_id'])
    return None


def persist_evidence(db, result, run_key, signal_id, observed):
    """Persist already privacy-filtered results within caller's transaction."""
    retention = result.get('retention', {})
    if not retention.get('frames') and not retention.get('identifiers'):
        return
    ensure_evidence_schema(db)
    for attempt_index, attempt in enumerate(result.get('attempts', [])):
        protocol = attempt.get('candidate_protocol', 'UNKNOWN')
        decoder = attempt.get('decoder', {})
        evidence = attempt.get('evidence', {})
        for index, frame in enumerate(attempt.get('frames', [])[:256]):
            frame_id = hashlib.sha256(f'{run_key}:{attempt_index}:{index}'.encode()).hexdigest()
            fields = frame.get('parsed_fields', {}) if retention.get('identifiers') else {}
            db.execute('INSERT OR IGNORE INTO recovered_frames VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (frame_id, run_key, signal_id, observed, result.get('frequency_hz'), protocol,
                 decoder.get('name', ''), decoder.get('version', ''), frame['status'], frame.get('frame_length'),
                 frame.get('raw_hex') if retention.get('frames') else None, frame.get('start_sample'),
                 int(frame['sync_valid']), int(frame['checksum_valid']), evidence.get('sync_reference', ''),
                 evidence.get('checksum_reference', ''), json.dumps(fields, sort_keys=True, allow_nan=False)))
            identity = _identity(protocol, decoder.get('name'), fields)
            if frame['status'] != 'VERIFIED' or not attempt.get('protocol_verified') or not identity:
                continue
            kind, model, identifier = identity
            entity_id = hashlib.sha256(json.dumps([kind, protocol, model, identifier], separators=(',', ':')).encode()).hexdigest()
            payload = json.dumps(fields, sort_keys=True, allow_nan=False)
            db.execute('''INSERT INTO entities VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(entity_id) DO UPDATE SET first_seen=MIN(entities.first_seen,excluded.first_seen),last_seen=MAX(entities.last_seen,excluded.last_seen),
              latest_fields=CASE WHEN excluded.last_seen>=entities.last_seen THEN excluded.latest_fields ELSE entities.latest_fields END''',
              (entity_id, kind, protocol, model, identifier, fields.get('category'), observed, observed, payload))
            db.execute('INSERT OR IGNORE INTO entity_observations VALUES(?,?,?,?,?,?)',
                       (entity_id, frame_id, signal_id, observed, result.get('frequency_hz'), payload))


def _rows(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _limit(value):
    if type(value) is not int or not 1 <= value <= 500:
        raise ValueError('Result limit must be 1..500')
    return value


def read_frames(db, signal_id=None, limit=100):
    limit = _limit(limit)
    where, args = (' WHERE f.signal_id=?', [signal_id]) if signal_id else ('', [])
    rows = _rows(db.execute('''SELECT f.*,r.source_sha256,r.created AS analyzed_at,
        CASE WHEN json_valid(r.result) THEN json_extract(r.result,'$.capture_timestamp') ELSE NULL END AS capture_timestamp
        FROM recovered_frames f JOIN identification_runs r USING(run_key)''' + where + ' ORDER BY f.observed DESC,f.frame_id LIMIT ?', args + [limit]))
    for row in rows:
        row['parsed_fields'] = json.loads(row['parsed_fields'])
        row['sync_valid'] = bool(row['sync_valid']); row['checksum_valid'] = bool(row['checksum_valid'])
        row['decoder'] = {'name': row['decoder'], 'version': row.pop('decoder_version')}
        row['timestamp_basis'] = 'CAPTURE_METADATA' if row['capture_timestamp'] else 'ANALYSIS_TIME_CAPTURE_TIME_UNAVAILABLE'
    return {'frames': rows, 'limit': limit, 'privacy_note': 'Frame and identifier retention is opt-in; missing bytes were not retained.'}


def _utc_filter(value):
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError('Entity time filter requires an ISO timestamp with offset or Z')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Entity time filter requires an ISO timestamp with offset or Z') from error
    if parsed.tzinfo is None:
        raise ValueError('Entity time filter requires an explicit UTC offset or Z')
    try:
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError) as error:
        raise ValueError('Entity time filter is outside the supported UTC range') from error


def list_entities(db, kind=None, protocol=None, q='', limit=100, *, category=None, first_since=None, last_until=None):
    limit = _limit(limit)
    if kind not in (None, 'device', 'station'):
        raise ValueError('Entity kind must be device or station')
    if not isinstance(q, str) or len(q) > 128 or any(value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 128) for value in (protocol, category)):
        raise ValueError('Invalid entity filter')
    first_since, last_until = _utc_filter(first_since), _utc_filter(last_until)
    if first_since and last_until and datetime.fromisoformat(first_since) > datetime.fromisoformat(last_until):
        raise ValueError('Entity first_since must not follow last_until')
    clauses, args = [], []
    if kind: clauses.append('kind=?'); args.append(kind)
    if protocol: clauses.append('protocol=?'); args.append(protocol)
    if category: clauses.append('category=?'); args.append(category)
    if first_since: clauses.append('julianday(first_seen)>=julianday(?)'); args.append(first_since)
    if last_until: clauses.append('julianday(last_seen)<=julianday(?)'); args.append(last_until)
    if q:
        clauses.append('(instr(lower(identifier),lower(?))>0 OR instr(lower(model),lower(?))>0 OR instr(lower(category),lower(?))>0)'); args.extend([q, q, q])
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    rows = _rows(db.execute('SELECT * FROM entities' + where + ' ORDER BY last_seen DESC,entity_id LIMIT ?', args + [limit]))
    for row in rows:
        row['latest_fields'] = json.loads(row['latest_fields'])
    return {'entities': rows, 'limit': limit, 'privacy_note': 'Verified protocol identifiers; not authenticated physical identity. Retention is opt-in.'}


def entity_detail(db, entity_id, limit=100):
    limit = _limit(limit)
    rows = _rows(db.execute('SELECT * FROM entities WHERE entity_id=?', (entity_id,)))
    if not rows: return None
    entity = rows[0]; entity['latest_fields'] = json.loads(entity['latest_fields'])
    entity['observations'] = _rows(db.execute('SELECT * FROM entity_observations WHERE entity_id=? ORDER BY observed DESC LIMIT ?', (entity_id, limit)))
    for observation in entity['observations']:
        observation['fields'] = json.loads(observation['fields'])
    entity['associated_signals'] = _rows(db.execute('SELECT signal_id,MIN(frequency_hz) AS frequency_min_hz,MAX(frequency_hz) AS frequency_max_hz,COUNT(*) AS observations FROM entity_observations WHERE entity_id=? GROUP BY signal_id ORDER BY observations DESC LIMIT ?', (entity_id, limit)))
    entity['verification_status'] = 'VERIFIED_PROTOCOL_IDENTIFIER'
    entity['limitation'] = 'Identifiers can collide, be reassigned or spoofed; no authenticated transmitter or physical identity claim.'
    entity['limit'] = limit
    return entity
