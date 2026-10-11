"""Bounded single-capture CU8 interchange; not a general SigMF reader."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

LIMIT = 64 * 1024 * 1024
DESCRIPTION_PREFIX = 'RF Atlas interchange v1: '
ACQUISITION_FIELDS = ('gain_db', 'antenna', 'device_identity', 'sample_rate_reported_hz',
                      'sample_rate_basis', 'bias_tee', 'created',
                      'acquisition_start', 'acquisition_end')


def _budget(max_bytes):
    if type(max_bytes) is not int or max_bytes < 8192:
        raise ValueError('Byte budget must be an integer of at least 8192')


def _number(value, name, minimum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(f'Invalid {name}')
    return value


def _timestamp(value, sigmf=False):
    if value is None:
        return None
    if not isinstance(value, str) or (sigmf and not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', value)):
        raise ValueError('Timestamp must be an ISO-8601 UTC timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid capture timestamp') from exc
    if parsed.tzinfo is None:
        raise ValueError('Capture timestamp needs a timezone')
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _validate_acquisition(metadata):
    """Validate the documented acquisition allowlist without promoting claims."""
    for key in ACQUISITION_FIELDS:
        if key not in metadata:
            continue
        value = metadata[key]
        if key in ('created', 'acquisition_start', 'acquisition_end'):
            _timestamp(value)
        elif key == 'gain_db':
            _number(value, key)
        elif key == 'sample_rate_reported_hz':
            if value is not None and _number(value, key) <= 0:
                raise ValueError('Reported sample rate must be positive')
        elif key == 'bias_tee':
            if type(value) is not bool:
                raise ValueError('Bias tee acquisition value must be boolean')
        elif not isinstance(value, str):
            raise ValueError(f'{key} acquisition value must be text')
    start = _timestamp(metadata.get('acquisition_start'))
    end = _timestamp(metadata.get('acquisition_end'))
    if start and end and datetime.fromisoformat(end) < datetime.fromisoformat(start):
        raise ValueError('Acquisition end precedes acquisition start')


def _hash(path, algorithm):
    digest = hashlib.new(algorithm)
    remaining = path.stat().st_size
    with path.open('rb') as source:
        while remaining:
            block = source.read(min(remaining, 1024 * 1024))
            if not block:
                raise ValueError('Source changed while hashing')
            digest.update(block)
            remaining -= len(block)
        if source.read(1):
            raise ValueError('Source grew while hashing')
    return digest.hexdigest()


def _pair(source, data_target, meta_target, meta, expected_size, expected_hash):
    """Never overwrite evidence; remove only newly created partial outputs."""
    if data_target.exists() or meta_target.exists():
        raise FileExistsError('Interchange destination already exists')
    data_target.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        with data_target.open('xb') as target:
            created.append(data_target)
            with source.open('rb') as original:
                remaining = expected_size
                while remaining:
                    block = original.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise ValueError('Source changed during interchange copy')
                    target.write(block)
                    remaining -= len(block)
                if original.read(1):
                    raise ValueError('Source grew during interchange copy')
        if _hash(data_target, 'sha256') != expected_hash:
            raise ValueError('Source changed during interchange copy')
        with meta_target.open('x', encoding='utf8') as target:
            created.append(meta_target)
            json.dump(meta, target, indent=2, allow_nan=False)
            target.write('\n')
    except Exception:
        for path in created:
            path.unlink()
        raise


def export_sigmf(recording, destination, metadata=None, max_bytes=LIMIT):
    """Validate native IQ and copy it into a standard SigMF CU8 file pair."""
    from .iq import read_recording
    _budget(max_bytes)
    source = Path(recording)
    _, meta = read_recording(source, metadata, max_bytes)
    _validate_acquisition(meta)
    timestamp = _timestamp(meta.get('created'))
    preserved = {key: meta[key] for key in ('desired_frequency_hz', 'tuning_offset_hz', 'sha256', *ACQUISITION_FIELDS) if key in meta}
    capture = {'core:sample_start': 0, 'core:frequency': meta['center_hz']}
    if timestamp:
        capture['core:datetime'] = timestamp
    document = {'global': {'core:datatype': 'cu8', 'core:sample_rate': meta['sample_rate'],
                'core:version': '1.2.6', 'core:num_channels': 1, 'core:recorder': 'RF Atlas',
                'core:sha512': _hash(source, 'sha512'),
                'core:description': DESCRIPTION_PREFIX + json.dumps(preserved, allow_nan=False)},
                'captures': [capture], 'annotations': []}
    base = Path(destination)
    if base.suffix in ('.sigmf-data', '.sigmf-meta'):
        base = base.with_suffix('')
    data_path = base.with_name(base.name + '.sigmf-data')
    meta_path = base.with_name(base.name + '.sigmf-meta')
    _pair(source, data_path, meta_path, document, meta['sample_count'] * 2, meta['sha256'])
    return data_path, meta_path


def import_sigmf(metadata_path, destination, desired_frequency_hz=None, max_bytes=LIMIT):
    """Import the supported SigMF subset into native, checksummed CU8 files.

    An absent upstream checksum is explicitly recorded, never described as
    verified source integrity. The newly copied native file always has SHA256.
    """
    path = Path(metadata_path)
    _budget(max_bytes)
    if path.suffix != '.sigmf-meta' or path.stat().st_size > 1024 * 1024:
        raise ValueError('Expected bounded .sigmf-meta file')
    with path.open('r', encoding='utf8') as source_meta:
        metadata_text = source_meta.read(1024 * 1024 + 1)
    if len(metadata_text) > 1024 * 1024:
        raise ValueError('Oversized SigMF metadata')
    doc = json.loads(metadata_text)
    if not isinstance(doc, dict) or not isinstance(doc.get('global'), dict):
        raise ValueError('Invalid SigMF document')
    glob = doc['global']; captures = doc.get('captures')
    if not isinstance(captures, list) or len(captures) != 1 or not isinstance(captures[0], dict):
        raise ValueError('Only one explicit SigMF capture is supported')
    cap = captures[0]
    if glob.get('core:datatype') != 'cu8' or type(glob.get('core:num_channels', 1)) is not int or glob.get('core:num_channels', 1) != 1:
        raise ValueError('Only single-channel cu8 is supported')
    if not isinstance(glob.get('core:version'), str) or not re.fullmatch(r'1\.\d+\.\d+', glob['core:version']):
        raise ValueError('Unsupported SigMF specification version')
    for key in ('core:offset', 'core:trailing_bytes'):
        if type(glob.get(key, 0)) is not int or glob.get(key, 0) != 0:
            raise ValueError('Non-conforming or offset datasets are unsupported')
    if glob.get('core:metadata_only', False) is not False or 'core:dataset' in glob:
        raise ValueError('Metadata-only or externally named datasets are unsupported')
    for key in ('core:sample_start', 'core:header_bytes'):
        if type(cap.get(key, 0)) is not int or cap.get(key, 0) != 0 or (key == 'core:sample_start' and key not in cap):
            raise ValueError('Capture must start at sample zero without headers')
    extensions = glob.get('core:extensions', [])
    if not isinstance(extensions, list) or any(not isinstance(e, dict) or e.get('optional') is not True
            or not isinstance(e.get('name'), str) or not isinstance(e.get('version'), str) for e in extensions):
        raise ValueError('Unknown required SigMF extensions are unsupported')
    if not isinstance(doc.get('annotations', []), list):
        raise ValueError('Invalid annotations array')
    rate = _number(glob.get('core:sample_rate'), 'sample rate', 1000)
    center = _number(cap.get('core:frequency'), 'center frequency', 1)
    timestamp = _timestamp(cap.get('core:datetime'), sigmf=True)
    source = path.with_suffix('.sigmf-data')
    size = source.stat().st_size
    if size < 8192 or size % 2 or size > max_bytes:
        raise ValueError('Incomplete, too short or oversized SigMF dataset')
    for annotation in doc.get('annotations', []):
        if not isinstance(annotation, dict):
            raise ValueError('Invalid annotation object')
        start = annotation.get('core:sample_start')
        if type(start) is not int or not 0 <= start < size // 2:
            raise ValueError('Invalid annotation sample offset')
        count = annotation.get('core:sample_count', size // 2 - start)
        if type(count) is not int or count <= 0 or start + count > size // 2:
            raise ValueError('Invalid annotation sample count')
    source_sha256 = _hash(source, 'sha256')
    source_hash = glob.get('core:sha512')
    if source_hash is not None and (not isinstance(source_hash, str) or not re.fullmatch('[0-9a-fA-F]{128}', source_hash) or source_hash.lower() != _hash(source, 'sha512')):
        raise ValueError('SigMF dataset SHA512 mismatch')
    preserved = {}
    description = glob.get('core:description', '')
    if isinstance(description, str) and description.startswith(DESCRIPTION_PREFIX):
        preserved = json.loads(description[len(DESCRIPTION_PREFIX):])
        if not isinstance(preserved, dict):
            raise ValueError('Invalid RF Atlas interchange description')
        if preserved.get('sha256') != source_sha256:
            raise ValueError('RF Atlas source SHA256 mismatch')
        _validate_acquisition(preserved)
        if 'created' in preserved and _timestamp(preserved['created']) != timestamp:
            raise ValueError('RF Atlas timestamp conflicts with SigMF capture timestamp')
    original_desired = preserved.get('desired_frequency_hz', center)
    original_offset = preserved.get('tuning_offset_hz', center - _number(original_desired, 'desired frequency'))
    if not math.isclose(_number(original_offset, 'tuning offset'), center - original_desired, rel_tol=0, abs_tol=1e-6):
        raise ValueError('Inconsistent RF Atlas tuning offset')
    desired = original_desired if desired_frequency_hz is None else desired_frequency_hz
    desired = _number(desired, 'desired frequency', 1)
    if abs(desired - center) >= rate / 2:
        raise ValueError('Desired frequency is outside capture bandwidth')
    meta = {'format': 'cu8', 'capture_id': str(uuid.uuid4()), 'center_hz': center,
            'desired_frequency_hz': desired, 'tuning_offset_hz': center - desired,
            'sample_rate': rate, 'sample_count': size // 2, 'created': timestamp,
            'sha256': source_sha256, 'source_format': 'SigMF',
            'source_integrity': 'SHA512 verified' if source_hash else 'No upstream SHA512 supplied; local SHA256 computed',
            'signal_id': None, 'source_sigmf_metadata': doc}
    for key in ACQUISITION_FIELDS:
        if key in preserved:
            meta[key] = preserved[key]
    target = Path(destination)
    if target.suffix != '.cu8':
        target = target.with_name(target.name + '.cu8')
    _pair(source, target, target.with_suffix('.json'), meta, size, source_sha256)
    return target, meta
