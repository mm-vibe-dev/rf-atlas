"""Bounded historical measurement views; no receiver access or invented silence."""
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
from .timestamps import aware_utc, observation_utc, sql_utc


def utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def explorer_view(root, query):
    low = float(query.get('min_hz', ['500000'])[0])
    high = float(query.get('max_hz', ['1766000000'])[0])
    if not math.isfinite(low + high) or not 0 <= low < high <= 2000000000:
        raise ValueError('Invalid frequency range')
    since = utc(query.get('since', ['1970-01-01'])[0])
    until = utc(query.get('until', ['9999-01-01'])[0])
    if since > until:
        raise ValueError('Invalid time range')
    unknown = query.get('unknown', ['false'])[0]
    if unknown not in ('true', 'false'):
        raise ValueError('Invalid unknown filter')
    path = Path(root) / 'data' / 'rf_atlas.sqlite'
    deadline = time.monotonic() + 1.5
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if 'min_hz' not in query and 'max_hz' not in query:
            recent = db.execute('SELECT start_hz,stop_hz FROM scan_regions WHERE status=? ORDER BY completed DESC LIMIT 1', ('complete',)).fetchone()
            if recent and recent[0] < recent[1]:
                low,high = recent
        activity_filter=(f'AND EXISTS(SELECT 1 FROM spectral_observations o WHERE o.signal_id=signals.signal_id '
            f'AND julianday({observation_utc()}) BETWEEN julianday(?) AND julianday(?)) '
            if 'since' in query or 'until' in query else '')
        signals = [dict(r) for r in db.execute('''SELECT signal_id,center_frequency_hz,
            occupied_bandwidth_hz,frequency_uncertainty_hz,protocol,modulation,investigation_status
            FROM signals WHERE center_frequency_hz BETWEEN ? AND ? ''' +
            ("AND COALESCE(protocol,'UNKNOWN')='UNKNOWN' " if unknown == 'true' else '') +
            activity_filter+f'ORDER BY julianday({sql_utc("last_seen")}) DESC LIMIT 501',
            (low,high)+((since,until) if activity_filter else ()))]
        history = [dict(r) for r in db.execute(f'''SELECT observed,observed_original,observed_utc,timestamp_basis,peak_snr_db,noise_db,
            signal_id,gain_db,gain_mode,fft_resolution_hz,integration_s,sample_rate_hz
            FROM spectral_observations o WHERE center_hz BETWEEN ? AND ?
            AND julianday({observation_utc()}) BETWEEN julianday(?) AND julianday(?)
            ORDER BY julianday({observation_utc()}) DESC LIMIT 501''', (low, high, since, until))]
        visits = [dict(r) for r in db.execute('''SELECT r.start_hz,r.stop_hz,m.observed,
            m.valid,m.signal_count FROM inventory_measurements m JOIN inventory_regions r USING(region_id)
            WHERE r.stop_hz>? AND r.start_hz<? AND m.observed BETWEEN ? AND ?
            ORDER BY m.observed DESC LIMIT 501''', (low, high,
            datetime.fromisoformat(since).timestamp(), datetime.fromisoformat(until).timestamp()))]
        segments = [dict(r) for r in db.execute('''SELECT c.start_hz,c.stop_hz,c.status,
            c.completed,c.raw_path,s.settings,(SELECT COUNT(DISTINCT signal_id) FROM spectral_observations o
            WHERE o.run_id=c.run_id AND o.center_hz BETWEEN c.start_hz AND c.stop_hz) AS detections,
            (SELECT sample_rate_hz FROM spectral_observations o WHERE o.run_id=c.run_id
             AND sample_rate_hz IS NOT NULL LIMIT 1) AS sample_rate_hz
            FROM scan_regions c JOIN survey_runs s USING(run_id)
            WHERE c.stop_hz>? AND c.start_hz<? AND julianday(c.completed)
            BETWEEN julianday(?) AND julianday(?) ORDER BY c.completed DESC LIMIT 17''',
            (low, high, since, until))]
        timestamp_map={}
        for segment in segments[:16]:
            timestamp_map.update({(segment['raw_path'],r[0]):r[1] for r in db.execute(
                'SELECT observed_original,observed_utc FROM spectrum_times WHERE raw_path=? LIMIT 128',(segment['raw_path'],))})
    coverage = [{'start_hz': max(low,v['start_hz']), 'stop_hz': min(high,v['stop_hz']),
        'observed': v['observed'], 'status': 'INVALID' if not v['valid'] else
        'DETECTED' if v['signal_count'] else 'VALID_NO_DETECTION'} for v in visits[:500]]
    # Explicit gaps in the returned bounded view are unknown, never silence.
    occupancy = [{'observed':v['observed'],'observed_regions':int(bool(v['valid'])),
        'detected_regions':int(bool(v['valid'] and v['signal_count'])),
        'invalid_regions':int(not v['valid']), 'signal_count':v['signal_count'] if v['valid'] else None}
        for v in visits[:500]]
    spectrum = {'frequency_hz':[], 'power_db':[], 'noise_db':[], 'observed':None,
                'units':'relative dB (uncalibrated)', 'settings':{}}
    waterfall = {'frequency_hz':[], 'rows':[], 'units':spectrum['units']}
    raw_budget = 4 * 1024 * 1024
    for segment in segments[:16]:
        raw = Path(segment['raw_path'] or '')
        # Catalog references must still resolve within this local project's data.
        try:
            raw.resolve().relative_to((Path(root)/'data').resolve())
            size = raw.stat().st_size
            if segment['status'] != 'complete' or not 0 < size <= raw_budget:
                continue
            raw_budget -= size
            from .diagnostics import read_spectra, noise_model
            import numpy as np
            rows, report = read_spectra(raw)
            if report['rejected_rows'] or len(rows) > 128:
                continue
            ordered = sorted(rows,key=lambda r:r['low'])
            covered = segment['start_hz']
            for row in ordered:
                if row['low'] > covered+1:
                    break
                covered = max(covered,row['high'])
            if covered < segment['stop_hz']-1:
                continue
            # Older survey-only catalogs have no inventory visit rows. Valid raw
            # coverage is still useful, and requires the same contiguous checks.
            if not visits:
                coverage.append({'start_hz':max(low,segment['start_hz']),
                    'stop_hz':min(high,segment['stop_hz']),'observed':segment['completed'],
                    'status':'DETECTED' if segment['detections'] else 'VALID_NO_DETECTION'})
            for row in reversed(rows[-32:]):
                indices = np.flatnonzero((row['centers'] >= low) & (row['centers'] <= high))
                observed=aware_utc(row['observed']) or timestamp_map.get((segment['raw_path'],row['observed']))
                if not len(indices) or observed is None or not since <= observed <= until:
                    continue
                groups = np.array_split(indices,min(1024,len(indices)))
                frequencies = [float(np.mean(row['centers'][group])) for group in groups]
                powers = [float(np.max(row['power'][group])) for group in groups]
                settings = json.loads(segment['settings'] or '{}')
                safe_settings = {k:settings[k] for k in ('gain','bin_hz','integration','seconds','duration','margin') if k in settings}
                safe_settings['actual_step_hz'] = row['step']
                safe_settings['sample_rate_hz'] = segment['sample_rate_hz']
                safe_settings['display_bucket_width_hz'] = row['step']*max(map(len,groups))
                safe_settings['downsampling'] = 'Maximum power per contiguous bucket; median estimated noise'
                if not spectrum['frequency_hz']:
                    floor,_ = noise_model(row['power'],row['step'])
                    spectrum.update(frequency_hz=frequencies,power_db=powers,
                        noise_db=[float(np.median(floor[group])) for group in groups],observed=observed,settings=safe_settings)
                    waterfall['frequency_hz'] = frequencies
                if frequencies == waterfall['frequency_hz'] and safe_settings == spectrum['settings']:
                    waterfall['rows'].append({'observed':observed,'power_db':powers})
                if len(waterfall['rows']) >= 32:
                    break
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if len(waterfall['rows']) >= 32:
            break
    waterfall['rows'].sort(key=lambda r:r['observed'])
    cursor = low
    for a,b in sorted((v['start_hz'],v['stop_hz']) for v in coverage):
        if a > cursor:
            coverage.append({'start_hz':cursor,'stop_hz':a,'status':'NOT_SCANNED','observed':None})
        cursor = max(cursor,b)
    if cursor < high:
        coverage.append({'start_hz':cursor,'stop_hz':high,'status':'NOT_SCANNED','observed':None})
    return {'spectrum':spectrum,'waterfall':waterfall,'history':list(reversed(history[:500])),
        'coverage':coverage,'occupancy':list(reversed(occupancy)), 'signals':signals[:500],
        'bounds':{'min_hz':low,'max_hz':high,'since':since,'until':until},
        'limits':{'signals':500,'history':500,'visits':500,'waterfall_rows':32,'spectrum_bins':1024},
        'truncated':len(signals)>500 or len(history)>500 or len(visits)>500 or len(segments)>16,
        'limitations':['Coverage is time-filtered and bounded; unreturned coverage is unknown.',
            'Occupancy is the fraction of valid region visits with detections, not transmitter airtime.',
            'Power is uncalibrated; polling displays latest retained measurements, not a streaming receiver.',
            'Ambiguous legacy timestamps are excluded from UTC plots and time filters; originals remain in the catalog.',
            'Signal labels describe current catalog state, not reconstructed classifications at the selected date.',
            'Channel recurrence and matching frequency do not identify a transmitter.']}
