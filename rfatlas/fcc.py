"""Optional, local-only normalized FCC license-assignment context.

This database is separate from the operational catalog. A frequency match is
licensing context and never evidence of transmitter identity or protocol.
"""
from datetime import date, datetime
from contextlib import closing
import math
from pathlib import Path
import sqlite3
from typing import Protocol


INTERPRETATION = 'License frequency match only; not proof of transmitter identity, authorization, activity or protocol.'


class FCCProvider(Protocol):
    def lookup(self, frequency_hz, tolerance_hz=0, *, include_location=False, limit=25): ...


def _number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'Invalid {name}')
    return float(value)


def _text(value, name, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f'Invalid {name}')
    return value.strip()


def ingest_assignments(path, rows, *, source, updated, max_rows=100000):
    """Atomically replace one source with validated normalized rows.

    Caller owns local input selection. No downloads, FCC-format inference,
    license-status inference, or radio-service band allocation expansion occurs.
    Failed imports retain the preceding source data. Input is streamed.
    """
    source = _text(source, 'source', 2048)
    updated = _text(updated, 'updated', 64)
    try:
        if 'T' in updated:
            datetime.fromisoformat(updated.replace('Z', '+00:00'))
        else:
            date.fromisoformat(updated)
    except ValueError:
        raise ValueError('updated must be an ISO date or timestamp') from None
    if type(max_rows) is not int or not 1 <= max_rows <= 1000000:
        raise ValueError('Invalid import row budget')
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target, timeout=2)) as db, db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise ValueError('Unsupported FCC context schema')
        if version == 0 and db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
            raise ValueError('FCC import requires a separate context database')
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS fcc_assignments(
            frequency_hz REAL NOT NULL, service TEXT NOT NULL, licensee TEXT NOT NULL,
            callsign TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL,
            updated TEXT NOT NULL, latitude REAL, longitude REAL, location TEXT)''')
        db.execute('CREATE INDEX IF NOT EXISTS fcc_frequency ON fcc_assignments(frequency_hz)')
        db.execute('PRAGMA user_version=1')
        db.execute('DELETE FROM fcc_assignments WHERE source=?', (source,))
        count = 0
        for row in rows:
            count += 1
            if count > max_rows:
                raise ValueError('FCC import row budget exceeded')
            required = {'frequency_hz', 'service', 'licensee', 'callsign', 'status'}
            optional = {'latitude', 'longitude', 'location'}
            if not isinstance(row, dict) or not required <= row.keys() or row.keys() - required - optional:
                raise ValueError('Invalid normalized assignment fields')
            frequency = _number(row['frequency_hz'], 1, 100000000000, 'assignment frequency')
            texts = [_text(row[key], key, 32 if key in ('service', 'callsign', 'status') else 512)
                     for key in ('service', 'licensee', 'callsign', 'status')]
            latitude, longitude = row.get('latitude'), row.get('longitude')
            if (latitude is None) != (longitude is None):
                raise ValueError('Location coordinates require both latitude and longitude')
            if latitude is not None:
                latitude = _number(latitude, -90, 90, 'latitude')
                longitude = _number(longitude, -180, 180, 'longitude')
            location = row.get('location')
            if location is not None:
                location = _text(location, 'location')
            db.execute('INSERT INTO fcc_assignments VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (frequency, *texts, source, updated, latitude, longitude, location))
    return count


class LocalFCCProvider:
    """Read-only indexed lookups, bounded output, and location off by default."""
    def __init__(self, path):
        self.path = Path(path)

    def lookup(self, frequency_hz, tolerance_hz=0, *, include_location=False, limit=25):
        frequency = _number(frequency_hz, 1, 100000000000, 'lookup frequency')
        tolerance = _number(tolerance_hz, 0, 1000000, 'frequency tolerance')
        if type(limit) is not int or not 1 <= limit <= 100 or type(include_location) is not bool:
            raise ValueError('Invalid FCC lookup options')
        result = {'available': False, 'matches': [], 'truncated': False, 'interpretation': INTERPRETATION}
        if not self.path.is_file():
            result['reason'] = 'Local FCC context database is not configured or missing'
            return result
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.25)) as db:
                db.row_factory = sqlite3.Row
                if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                    raise ValueError('Unsupported FCC context schema')
                columns = 'frequency_hz,service,licensee,callsign,status,source,updated'
                if include_location:
                    columns += ',latitude,longitude,location'
                rows = db.execute(f'''SELECT {columns} FROM fcc_assignments
                    WHERE frequency_hz BETWEEN ? AND ?
                    ORDER BY abs(frequency_hz-?),source,callsign LIMIT ?''',
                    (frequency-tolerance, frequency+tolerance, frequency, limit+1)).fetchall()
            result.update(available=True, matches=[dict(row) for row in rows[:limit]], truncated=len(rows) > limit)
        except (sqlite3.Error, ValueError):
            result['reason'] = 'Local FCC context database is unavailable or incompatible'
        return result
