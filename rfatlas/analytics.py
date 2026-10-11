"""Optional, offline analytics. SQLite remains the authoritative live catalog."""
from contextlib import closing, contextmanager
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time


class AnalyticsUnavailable(RuntimeError):
    """The optional analytics dependency is not installed."""


def _duckdb():
    try:
        return importlib.import_module('duckdb')
    except ImportError as error:
        raise AnalyticsUnavailable('Optional analytics require: pip install "rf-atlas[analytics]"') from error


def _readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _budget(timeout_s, cancel=None):
    if not math.isfinite(timeout_s) or not 0 < timeout_s <= 3600:
        raise ValueError('timeout_s must be finite and in (0,3600]')
    deadline = time.monotonic() + timeout_s
    def check():
        if (cancel and cancel()) or time.monotonic() >= deadline:
            raise TimeoutError('Analytics operation cancelled or deadline exceeded')
    return check


def _publish(temporary, destination):
    # An atomic hard link refuses overwrites on Windows and Linux.
    os.link(temporary, destination)
    Path(temporary).unlink()


def snapshot_catalog(source, destination, *, timeout_s=30, cancel=None):
    """Consistent local SQLite backup, including committed WAL pages, never overwrite."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Snapshot destination must be new and differ from source')
    destination.parent.mkdir(parents=True, exist_ok=True)
    check = _budget(timeout_s, cancel)
    handle, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=destination.parent)
    os.close(handle)
    try:
        with closing(_readonly(source)) as live, closing(sqlite3.connect(temporary)) as backup:
            check()
            # Pin a WAL read snapshot so frequent commits cannot repeatedly
            # restart the incremental backup and starve completion.
            live.execute('BEGIN')
            live.execute('SELECT count(*) FROM sqlite_master').fetchone()
            live.backup(backup, pages=256, progress=lambda *_: check(), sleep=.01)
            live.rollback()
            backup.execute('PRAGMA journal_mode=DELETE')
            version = backup.execute('PRAGMA user_version').fetchone()[0]
            if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Snapshot integrity check failed')
        check()
        digest = _sha256(temporary)
        _publish(temporary, destination)
        return {'snapshot_sha256': digest, 'schema_version': version,
                'bytes': destination.stat().st_size, 'consistent_backup': True}
    finally:
        Path(temporary).unlink(missing_ok=True)


# Explicit projection excludes recording paths, payloads, location, settings and IDs
# belonging to external transmitters. Internal signal IDs remain local join keys.
_TABLES = {
    'signals': ('signal_id VARCHAR, frequency_hz DOUBLE, bandwidth_hz DOUBLE, modulation VARCHAR, protocol VARCHAR, last_seen DOUBLE',
        "SELECT signal_id,center_frequency_hz,occupied_bandwidth_hz,coalesce(modulation,'UNKNOWN'),coalesce(protocol,'UNKNOWN'),cast(strftime('%s',last_seen) AS REAL) FROM signals ORDER BY signal_id"),
    'visits': ('region_id VARCHAR, observed DOUBLE, valid INTEGER, signal_count BIGINT, start_hz DOUBLE, stop_hz DOUBLE',
        'SELECT m.region_id,m.observed,m.valid,m.signal_count,r.start_hz,r.stop_hz FROM inventory_measurements m JOIN inventory_regions r USING(region_id) ORDER BY m.measurement_id'),
    'regions': ('region_id VARCHAR, start_hz DOUBLE, stop_hz DOUBLE, enabled INTEGER',
        'SELECT region_id,start_hz,stop_hz,enabled FROM inventory_regions ORDER BY region_id'),
    'observations': ('signal_id VARCHAR, observed DOUBLE, snr_db DOUBLE, noise_db DOUBLE, gain_db DOUBLE, gain_mode VARCHAR, resolution_hz DOUBLE',
        "SELECT signal_id,cast(strftime('%s',observed) AS REAL),peak_snr_db,noise_db,gain_db,gain_mode,fft_resolution_hz FROM spectral_observations ORDER BY observation_id"),
    'jobs': ('signal_id VARCHAR, created DOUBLE, status VARCHAR',
        'SELECT signal_id,created,status FROM investigation_jobs ORDER BY job_id'),
    'identifications': ('signal_id VARCHAR, observed DOUBLE, protocol VARCHAR, verified INTEGER, attempts BIGINT',
        """SELECT signal_id,cast(strftime('%s',created) AS REAL),
        CASE WHEN json_valid(result) THEN json_extract(result,'$.protocol') ELSE 'UNKNOWN' END,
        CASE WHEN json_valid(result) THEN CASE WHEN json_extract(result,'$.protocol_verified')=1 THEN 1 ELSE 0 END ELSE 0 END,
        CASE WHEN json_valid(result) THEN coalesce(json_array_length(result,'$.attempts'),0) ELSE 0 END
        FROM identification_runs ORDER BY run_key"""),
    'entity_discoveries': ('kind VARCHAR, protocol VARCHAR, category VARCHAR, first_seen DOUBLE, last_seen DOUBLE',
        "SELECT kind,protocol,category,cast(strftime('%s',first_seen) AS REAL),cast(strftime('%s',last_seen) AS REAL) FROM entities ORDER BY entity_id"),
    'entity_activity': ('kind VARCHAR, protocol VARCHAR, observed DOUBLE, observations BIGINT',
        "SELECT e.kind,e.protocol,cast(strftime('%s',o.observed) AS REAL),count(*) FROM entity_observations o JOIN entities e USING(entity_id) GROUP BY e.kind,e.protocol,o.observed ORDER BY o.observed,e.kind,e.protocol"),
}

_OPTIONAL_SOURCES = {'entity_discoveries': {'entities'}, 'entity_activity': {'entities', 'entity_observations'}}


@contextmanager
def _connection(path, *, read_only=False, timeout_s=30):
    module = _duckdb()
    connection = module.connect(str(path), read_only=read_only, config={
        'threads': '2', 'memory_limit': '256MB', 'max_temp_directory_size': '512MB',
        'autoinstall_known_extensions': 'false', 'autoload_known_extensions': 'false'})
    timer = threading.Timer(timeout_s, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        yield connection
    finally:
        timer.cancel()
        connection.close()


def build_analytics(snapshot, destination, *, timeout_s=120, cancel=None):
    """Stream bounded batches from a detached snapshot into a new DuckDB file."""
    _duckdb()  # Fail before touching output if the optional extra is missing.
    snapshot, destination = Path(snapshot).resolve(), Path(destination).resolve()
    if snapshot == destination or destination.exists():
        raise ValueError('Analytics destination must be new')
    check = _budget(timeout_s, cancel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.analytics-', dir=destination.parent)
    os.close(handle)
    Path(temporary).unlink()
    counts = {}
    try:
        check()
        with closing(_readonly(snapshot)) as source, _connection(temporary, timeout_s=timeout_s) as target:
            if source.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'delete':
                raise ValueError('Build requires a detached DELETE-journal snapshot; run analytics snapshot first')
            source.execute('BEGIN')
            target.execute('BEGIN TRANSACTION')
            source_tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name, (columns, query) in _TABLES.items():
                check()
                target.execute(f'CREATE TABLE {name} ({columns})')
                counts[name] = 0
                if not _OPTIONAL_SOURCES.get(name, set()) <= source_tables:
                    continue  # Older snapshots remain usable with empty entity projections.
                from .timestamps import sql_utc,observation_utc
                if name=='observations':
                    known={row[1] for row in source.execute('PRAGMA table_info(spectral_observations)')}
                    canonical=observation_utc('spectral_observations') if 'observed_utc' in known else sql_utc('observed')
                    query=query.replace("strftime('%s',observed)",f"strftime('%s',{canonical})")
                elif name=='signals':query=query.replace("strftime('%s',last_seen)",f"strftime('%s',{sql_utc('last_seen')})")
                cursor = source.execute(query)
                while rows := cursor.fetchmany(256):
                    check()
                    placeholders = '(' + ','.join('?' for _ in rows[0]) + ')'
                    target.execute(f'INSERT INTO {name} VALUES ' + ','.join([placeholders] * len(rows)),
                                   [value for row in rows for value in row])
                    counts[name] += len(rows)
            manifest = {'format_version': 1, 'snapshot_sha256': _sha256(snapshot),
                        'schema_version': source.execute('PRAGMA user_version').fetchone()[0],
                        'duckdb_version': _duckdb().__version__, 'rows': counts,
                        'excluded': ['recordings', 'payloads', 'locations', 'settings', 'external identifiers'],
                        'timestamp_policy':'Explicit UTC/offset values only; ambiguous legacy values project as NULL'}
            target.execute('CREATE TABLE manifest (value VARCHAR)')
            target.execute('INSERT INTO manifest VALUES (?)', [json.dumps(manifest, sort_keys=True)])
            target.execute('COMMIT')
            target.execute('CHECKPOINT')
        check()
        _publish(temporary, destination)
        return manifest
    finally:
        Path(temporary).unlink(missing_ok=True)
        Path(temporary + '.wal').unlink(missing_ok=True)


def query_analytics(path, start, end, *, bucket_s=3600, limit=500, timeout_s=30):
    """Return bounded structural aggregates; [start,end) uses UTC Unix seconds."""
    if not all(math.isfinite(v) for v in (start, end, bucket_s)) or end <= start or bucket_s < 1:
        raise ValueError('Require finite start < end and bucket_s >= 1')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
        raise ValueError('limit must be an integer in [1,2000]')
    _budget(timeout_s)
    # Avoid truncating time: widen buckets to cover the entire requested interval.
    bucket_s = max(float(bucket_s), (end-start) / limit)
    with _connection(path, read_only=True, timeout_s=timeout_s) as db:
        def rows(sql, parameters=()):
            result = db.execute(sql, parameters)
            names = [item[0] for item in result.description]
            return [dict(zip(names, row)) for row in result.fetchall()]
        occupancy = rows('''SELECT floor((observed-?)/?) AS bucket, count(*) AS attempted,
            sum(CASE WHEN valid=1 THEN 1 ELSE 0 END) AS valid,
            sum(CASE WHEN valid=1 AND signal_count>0 THEN 1 ELSE 0 END) AS detected,
            sum(CASE WHEN valid=1 AND signal_count=0 THEN 1 ELSE 0 END) AS no_detection,
            avg(CASE WHEN valid=1 THEN CASE WHEN signal_count>0 THEN 1.0 ELSE 0.0 END END) AS detection_visit_fraction
            FROM visits WHERE observed>=? AND observed<? GROUP BY bucket ORDER BY bucket LIMIT ?''',
            [start, bucket_s, start, end, limit])
        active = rows('''SELECT s.signal_id,s.frequency_hz,count(*) AS observations,max(o.snr_db) AS peak_snr_db
            FROM observations o JOIN signals s USING(signal_id) WHERE o.observed>=? AND o.observed<?
            GROUP BY s.signal_id,s.frequency_hz ORDER BY observations DESC,s.signal_id LIMIT ?''', [start,end,limit])
        noise = rows('''SELECT floor((observed-?)/?) AS bucket,gain_db,gain_mode,resolution_hz,
            count(noise_db) AS observations,avg(noise_db) AS mean_relative_noise_db
            FROM observations WHERE observed>=? AND observed<?
            GROUP BY bucket,gain_db,gain_mode,resolution_hz ORDER BY bucket,gain_mode,gain_db,resolution_hz LIMIT ?''',
            [start,bucket_s,start,end,limit+1])
        classification = rows('''SELECT modulation,protocol,count(*) AS signals FROM signals
            WHERE signal_id IN (SELECT signal_id FROM observations WHERE observed>=? AND observed<?)
            GROUP BY modulation,protocol ORDER BY signals DESC,modulation,protocol LIMIT ?''',[start,end,limit])
        unknown = rows('''SELECT count(*) AS observed_channels,
            sum(CASE WHEN modulation='UNKNOWN' THEN 1 ELSE 0 END) AS unknown_modulation,
            sum(CASE WHEN protocol='UNKNOWN' THEN 1 ELSE 0 END) AS unknown_protocol
            FROM signals WHERE signal_id IN
            (SELECT signal_id FROM observations WHERE observed>=? AND observed<?)''',[start,end])[0]
        jobs = rows('SELECT status,count(*) AS jobs FROM jobs WHERE created>=? AND created<? GROUP BY status ORDER BY status LIMIT ?', [start,end,limit])
        bandwidth = rows('''SELECT floor(log2(greatest(bandwidth_hz,1))) AS log2_hz_bin,count(*) AS signals
            FROM signals WHERE bandwidth_hz IS NOT NULL AND signal_id IN
            (SELECT signal_id FROM observations WHERE observed>=? AND observed<?)
            GROUP BY log2_hz_bin ORDER BY log2_hz_bin LIMIT ?''',[start,end,limit])
        revisits = rows('''SELECT region_id,count(*) AS attempts,sum(CASE WHEN valid=1 THEN 1 ELSE 0 END) AS valid,
            min(observed) AS first_visit,max(observed) AS last_visit FROM visits WHERE observed>=? AND observed<?
            GROUP BY region_id ORDER BY attempts DESC,region_id LIMIT ?''',[start,end,limit])
        verification = rows('''SELECT protocol,count(*) AS investigations,sum(verified) AS verified,
            sum(attempts) AS decoder_attempts FROM identifications WHERE observed>=? AND observed<?
            GROUP BY protocol ORDER BY investigations DESC,protocol LIMIT ?''',[start,end,limit])
        changes = rows('''SELECT floor((observed-?)/?) AS bucket,count(*) AS observations,
            count(DISTINCT signal_id) AS distinct_channels,avg(snr_db) AS mean_snr_db
            FROM observations WHERE observed>=? AND observed<? GROUP BY bucket ORDER BY bucket LIMIT ?''',
            [start,bucket_s,start,end,limit])
        tables = {r[0] for r in db.execute('SHOW TABLES').fetchall()}
        discoveries = rows('''SELECT kind,protocol,category,count(*) AS new_identifiers FROM entity_discoveries
            WHERE first_seen>=? AND first_seen<? GROUP BY kind,protocol,category
            ORDER BY new_identifiers DESC,kind,protocol,category LIMIT ?''', [start,end,limit]) if 'entity_discoveries' in tables else []
        discovery_history = rows('''SELECT floor((first_seen-?)/?) AS bucket,kind,count(*) AS new_identifiers
            FROM entity_discoveries WHERE first_seen>=? AND first_seen<? GROUP BY bucket,kind
            ORDER BY bucket,kind LIMIT ?''', [start,bucket_s,start,end,limit]) if 'entity_discoveries' in tables else []
        entity_activity = rows('''SELECT kind,protocol,sum(observations) AS verified_identifier_observations
            FROM entity_activity WHERE observed>=? AND observed<? GROUP BY kind,protocol
            ORDER BY verified_identifier_observations DESC,kind,protocol LIMIT ?''', [start,end,limit]) if 'entity_activity' in tables else []
        intervals = db.execute('''SELECT DISTINCT r.start_hz,r.stop_hz FROM regions r
            JOIN visits v USING(region_id) WHERE v.valid=1 AND v.observed>=? AND v.observed<?
            ORDER BY r.start_hz,r.stop_hz''',[start,end])
        valid_hz = 0
        right = None
        while batch := intervals.fetchmany(256):
            for lo, hi in batch:
                valid_hz += max(0, hi-max(lo, right if right is not None else lo))
                right = max(hi, right if right is not None else hi)
        manifest = json.loads(db.execute('SELECT value FROM manifest').fetchone()[0])
    return {'manifest': manifest, 'start':start, 'end':end, 'bucket_s':bucket_s,
            'occupancy':occupancy, 'active_frequencies':active, 'noise_trends':noise[:limit],
            'noise_truncated':len(noise)>limit, 'classification':classification,
            'unknown_statistics':unknown,
            'investigations':jobs, 'bandwidth_distribution':bandwidth, 'revisits':revisits,
            'decoder_verification':verification, 'historical_detection_changes':changes,
            'entity_discovery_statistics':discoveries, 'entity_discovery_history':discovery_history,
            'entity_observation_statistics':entity_activity,
            'coverage':{'valid_observed_hz':valid_hz},
            'limitations':['No missing bucket means silence; unvisited spectrum is NOT_SCANNED.',
                'Detection visit fraction is not transmitter airtime or time occupancy.',
                'Classifications and bandwidth are the snapshot catalog state, not reconstructed past state.',
                'Relative noise is not calibrated dBm; gain and resolution groups stay separate.',
                'Entity statistics require opt-in identifier retention; protocol identifiers are not authenticated physical devices.',
                'Bounded ranked lists may omit lower-ranked groups.']}


def export_parquet(path, destination, *, timeout_s=120):
    """Export fixed projected tables locally; refuse existing directories."""
    _budget(timeout_s)
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Parquet export destination must be new')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.parquet-', dir=destination.parent))
    try:
        with _connection(path, read_only=True, timeout_s=timeout_s) as db:
            for name in (*_TABLES, 'manifest'):
                output = str(temporary / (name + '.parquet')).replace("'", "''")
                db.execute(f"COPY (SELECT * FROM {name}) TO '{output}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        # Directory rename is atomic. Destination existence was checked above.
        temporary.rename(destination)
        return {'tables':list(_TABLES), 'directory':str(destination), 'local_only':True}
    finally:
        if temporary.exists():
            for item in temporary.iterdir():
                item.unlink()
            temporary.rmdir()
