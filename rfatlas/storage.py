"""Authoritative SQLite schema; additive migrations preserve existing catalogs."""
import sqlite3
import uuid

def open_database(root):
    (root / 'data').mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'data' / 'rf_atlas.sqlite')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    version=db.execute('PRAGMA user_version').fetchone()[0]
    if version>11:
        db.close();raise ValueError('Catalog schema is newer than this RF Atlas release')
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'signals' in tables and version<11:
        backup_dir=root/'data'/'backups';backup_dir.mkdir(exist_ok=True)
        release='1.2' if version<8 else '1.3' if version<9 else '1.5' if version<10 else '1.6'
        backup=sqlite3.connect(backup_dir/f'catalog-before-{release}-{uuid.uuid4().hex}.sqlite')
        try: db.backup(backup)
        finally: backup.close()
    db.executescript('''
    CREATE TABLE IF NOT EXISTS antenna_profiles(name TEXT PRIMARY KEY, description TEXT);
    CREATE TABLE IF NOT EXISTS survey_runs(run_id TEXT PRIMARY KEY, started TEXT, ended TEXT, status TEXT, start_hz INTEGER, stop_hz INTEGER, profile TEXT, antenna TEXT REFERENCES antenna_profiles(name), settings TEXT);
    CREATE TABLE IF NOT EXISTS scan_regions(run_id TEXT REFERENCES survey_runs(run_id), start_hz INTEGER, stop_hz INTEGER, status TEXT, completed TEXT, raw_path TEXT, PRIMARY KEY(run_id,start_hz));
    CREATE TABLE IF NOT EXISTS signals(signal_id TEXT PRIMARY KEY, center_frequency_hz REAL, frequency_min_hz REAL, frequency_max_hz REAL, occupied_bandwidth_hz REAL, first_seen TEXT, last_seen TEXT, observation_count INTEGER, peak_snr_db REAL, median_snr_db REAL, duty_cycle REAL, signal_class TEXT DEFAULT 'UNKNOWN', modulation TEXT DEFAULT 'UNKNOWN', protocol TEXT DEFAULT 'UNKNOWN', protection TEXT DEFAULT 'UNKNOWN', confidence REAL DEFAULT 0, evidence TEXT DEFAULT 'Spectral detection only; no decoder evidence');
    CREATE TABLE IF NOT EXISTS spectral_observations(observation_id INTEGER PRIMARY KEY, run_id TEXT REFERENCES survey_runs(run_id), signal_id TEXT REFERENCES signals(signal_id), observed TEXT, start_hz REAL, stop_hz REAL, center_hz REAL, peak_snr_db REAL, average_snr_db REAL, noise_db REAL, raw_path TEXT);
    CREATE TABLE IF NOT EXISTS captures(capture_id TEXT PRIMARY KEY, signal_id TEXT REFERENCES signals(signal_id), antenna TEXT REFERENCES antenna_profiles(name), path TEXT, settings TEXT);
    CREATE TABLE IF NOT EXISTS classifications(classification_id INTEGER PRIMARY KEY, signal_id TEXT REFERENCES signals(signal_id), created TEXT, label TEXT, confidence REAL, evidence TEXT);
    CREATE TABLE IF NOT EXISTS decoder_results(result_id INTEGER PRIMARY KEY, capture_id TEXT REFERENCES captures(capture_id), classification_id INTEGER REFERENCES classifications(classification_id), decoder TEXT, raw_output TEXT);
    CREATE TABLE IF NOT EXISTS signal_events(event_id INTEGER PRIMARY KEY, signal_id TEXT REFERENCES signals(signal_id), created TEXT, kind TEXT, evidence TEXT);
    CREATE TABLE IF NOT EXISTS artifacts(artifact_id INTEGER PRIMARY KEY, signal_id TEXT REFERENCES signals(signal_id), kind TEXT, confidence REAL, reason TEXT);
    CREATE TABLE IF NOT EXISTS identification_runs(run_key TEXT PRIMARY KEY, signal_id TEXT REFERENCES signals(signal_id), created TEXT, source_sha256 TEXT, engine_version TEXT, recording_path TEXT, result TEXT);
    CREATE INDEX IF NOT EXISTS identification_signal ON identification_runs(signal_id,created);
    CREATE TABLE IF NOT EXISTS inventory_regions(region_id TEXT PRIMARY KEY, profile_id TEXT, start_hz INTEGER, stop_hz INTEGER, settings TEXT, stage TEXT DEFAULT 'coarse', status TEXT DEFAULT 'pending', next_due REAL DEFAULT 0, last_visited REAL, last_valid REAL, visit_count INTEGER DEFAULT 0, valid_count INTEGER DEFAULT 0, signal_count INTEGER DEFAULT 0, last_run TEXT, quality TEXT, enabled INTEGER DEFAULT 1);
    CREATE INDEX IF NOT EXISTS inventory_due ON inventory_regions(next_due,status);
    CREATE TABLE IF NOT EXISTS inventory_measurements(measurement_id INTEGER PRIMARY KEY, region_id TEXT REFERENCES inventory_regions(region_id), observed REAL, run_id TEXT REFERENCES survey_runs(run_id), valid INTEGER, signal_count INTEGER, comparison TEXT, quality TEXT);
    CREATE TABLE IF NOT EXISTS investigation_jobs(job_id TEXT PRIMARY KEY, signal_id TEXT REFERENCES signals(signal_id), status TEXT, priority INTEGER, reason TEXT, created REAL, updated REAL, next_eligible REAL, attempts INTEGER DEFAULT 0, fingerprint TEXT, settings TEXT, result_key TEXT, error TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS investigation_pending_signal ON investigation_jobs(signal_id) WHERE status IN ('pending','running');
    CREATE INDEX IF NOT EXISTS investigation_ready ON investigation_jobs(status,next_eligible,priority);
    CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied TEXT DEFAULT CURRENT_TIMESTAMP, description TEXT);
    CREATE TABLE IF NOT EXISTS investigation_events(event_id INTEGER PRIMARY KEY, job_id TEXT REFERENCES investigation_jobs(job_id), created REAL, status TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS investigation_decisions(decision_id INTEGER PRIMARY KEY,
        run_id TEXT REFERENCES survey_runs(run_id),signal_id TEXT REFERENCES signals(signal_id),
        profile_id TEXT,created REAL,reason TEXT,job_id TEXT);
    CREATE INDEX IF NOT EXISTS decision_time ON investigation_decisions(created);
    CREATE TABLE IF NOT EXISTS acquisition_timings(run_id TEXT REFERENCES survey_runs(run_id),
        start_hz INTEGER,recorded TEXT,acquisition_s REAL,processing_s REAL,requested_dwell_s REAL,
        valid INTEGER,PRIMARY KEY(run_id,start_hz));
    ''')
    columns={r[1] for r in db.execute('PRAGMA table_info(spectral_observations)')}
    if 'gain_db' not in columns: db.execute('ALTER TABLE spectral_observations ADD COLUMN gain_db REAL')
    if 'gain_mode' not in columns: db.execute('ALTER TABLE spectral_observations ADD COLUMN gain_mode TEXT')
    for name,kind in [('sample_rate_hz','REAL'),('fft_resolution_hz','REAL'),('integration_s','REAL'),('threshold_margin_db','REAL'),('measurement_settings','TEXT')]:
        if name not in columns:db.execute(f'ALTER TABLE spectral_observations ADD COLUMN {name} {kind}')
    signal_columns={r[1] for r in db.execute('PRAGMA table_info(signals)')}
    if 'enabled' not in {r[1] for r in db.execute('PRAGMA table_info(inventory_regions)')}:
        db.execute('ALTER TABLE inventory_regions ADD COLUMN enabled INTEGER DEFAULT 1')
    for name,kind in [('frequency_uncertainty_hz','REAL'),('investigation_status','TEXT'),('last_verified','TEXT'),('classifier_version','TEXT'),('decoder_version','TEXT')]:
        if name not in signal_columns: db.execute(f'ALTER TABLE signals ADD COLUMN {name} {kind}')
    db.execute('CREATE INDEX IF NOT EXISTS signals_frequency ON signals(center_frequency_hz)')
    db.execute('CREATE INDEX IF NOT EXISTS observations_signal_time ON spectral_observations(signal_id,observed)')
    db.execute('CREATE INDEX IF NOT EXISTS observations_recent ON spectral_observations(observed DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS inventory_measurement_time ON inventory_measurements(observed DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS scan_completed ON scan_regions(completed DESC)')
    db.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES(6,'Add persistent spectrum visits and investigation scheduling')")
    from .entities import ensure_evidence_schema
    ensure_evidence_schema(db)
    db.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES(7,'Add opt-in recovered frames and verified protocol identifiers')")
    from .timestamps import aware_utc
    columns={r[1] for r in db.execute('PRAGMA table_info(spectral_observations)')}
    for name in ('observed_original','observed_utc','timestamp_basis'):
        if name not in columns:db.execute(f'ALTER TABLE spectral_observations ADD COLUMN {name} TEXT')
    if db.execute('PRAGMA user_version').fetchone()[0]<8:
        # Preserve original strings. No host timezone is applied to history.
        for identity,observed in db.execute('SELECT observation_id,observed FROM spectral_observations'):
            normalized=aware_utc(observed)
            db.execute('UPDATE spectral_observations SET observed_original=?,observed_utc=?,timestamp_basis=? WHERE observation_id=?',
                       (observed,normalized,'LEGACY_EXPLICIT_OFFSET' if normalized else 'LEGACY_TIMEZONE_UNKNOWN',identity))
    db.execute('''CREATE TABLE IF NOT EXISTS observation_fingerprints(
        observation_id INTEGER PRIMARY KEY REFERENCES spectral_observations(observation_id),
        signal_id TEXT NOT NULL REFERENCES signals(signal_id),version TEXT NOT NULL,features TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS fingerprint_signal ON observation_fingerprints(signal_id,observation_id DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS observation_utc_time ON spectral_observations(observed_utc)')
    db.execute('''CREATE TABLE IF NOT EXISTS spectrum_times(
        run_id TEXT REFERENCES survey_runs(run_id),raw_path TEXT,observed_original TEXT,
        observed_utc TEXT NOT NULL,timestamp_basis TEXT NOT NULL,
        PRIMARY KEY(run_id,raw_path,observed_original))''')
    db.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES(8,'Preserve raw timestamps, explicit new UTC observations and spectral fingerprints')")
    db.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES(9,'Record investigation eligibility decisions and acquisition timing without changing RF history')")
    db.execute('CREATE TABLE IF NOT EXISTS signal_annotations(signal_id TEXT PRIMARY KEY REFERENCES signals(signal_id),value TEXT NOT NULL,updated TEXT NOT NULL)')
    db.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES(10,'Private operator annotations separate from protocol evidence')")
    from .reanalysis import ensure_schema
    ensure_schema(db)
    db.execute("INSERT OR IGNORE INTO schema_migrations(version,description) VALUES(11,'Bounded retained-IQ reanalysis, immutable results and versioned cache')")
    db.execute('PRAGMA user_version=11')
    db.execute("UPDATE spectral_observations SET gain_mode=CASE WHEN json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain')='auto' THEN 'auto' ELSE 'manual' END, gain_db=CASE WHEN json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain')='auto' THEN NULL ELSE CAST(json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain') AS REAL) END WHERE gain_mode IS NULL AND json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain') IS NOT NULL")
    db.commit()
    return db
