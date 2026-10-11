"""Authoritative SQLite schema; additive migrations preserve existing catalogs."""
import sqlite3

def open_database(root):
    (root / 'data').mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'data' / 'rf_atlas.sqlite')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
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
    ''')
    columns={r[1] for r in db.execute('PRAGMA table_info(spectral_observations)')}
    if 'gain_db' not in columns: db.execute('ALTER TABLE spectral_observations ADD COLUMN gain_db REAL')
    if 'gain_mode' not in columns: db.execute('ALTER TABLE spectral_observations ADD COLUMN gain_mode TEXT')
    for name,kind in [('sample_rate_hz','REAL'),('fft_resolution_hz','REAL'),('integration_s','REAL'),('threshold_margin_db','REAL'),('measurement_settings','TEXT')]:
        if name not in columns:db.execute(f'ALTER TABLE spectral_observations ADD COLUMN {name} {kind}')
    db.execute("UPDATE spectral_observations SET gain_mode=CASE WHEN json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain')='auto' THEN 'auto' ELSE 'manual' END, gain_db=CASE WHEN json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain')='auto' THEN NULL ELSE CAST(json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain') AS REAL) END WHERE gain_mode IS NULL AND json_extract((SELECT settings FROM survey_runs WHERE survey_runs.run_id=spectral_observations.run_id),'$.gain') IS NOT NULL")
    db.commit()
    return db
