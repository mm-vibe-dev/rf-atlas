"""Validated adapter contract shared by native and optional protocol decoders.

A modulation label cannot verify a protocol.
Adapters must report independent synchronization and frame integrity evidence.
"""
from dataclasses import dataclass
import hashlib
import math
import re
from typing import Protocol
import numpy as np


@dataclass(frozen=True)
class DecoderInput:
    iq: np.ndarray
    sample_rate: float
    frequency_hz: float
    start_sample: int = 0
    source_sha256: str | None = None

    def __post_init__(self):
        samples = np.asarray(self.iq)
        if samples.ndim != 1 or not np.iscomplexobj(samples) or not 4096 <= samples.size <= 32 * 1024 * 1024 or not np.isfinite(samples).all():
            raise ValueError('Decoder input requires bounded finite complex IQ')
        for value in (self.sample_rate, self.frequency_hz):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('Invalid decoder rate or frequency')
        if type(self.start_sample) is not int or self.start_sample < 0:
            raise ValueError('Invalid decoder sample offset')
        if self.source_sha256 is not None and (not isinstance(self.source_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.source_sha256)):
            raise ValueError('Invalid source SHA256')
        copied = np.array(samples, dtype='<c16', copy=True)
        copied.flags.writeable = False
        object.__setattr__(self, 'iq', copied)

    @property
    def sample_sha256(self):
        """Identity of the actual translated analysis input, not raw CU8 bytes."""
        return hashlib.sha256(self.iq.tobytes()).hexdigest()

    @classmethod
    def from_recording(cls, path, metadata=None, max_bytes=64 * 1024 * 1024):
        from .iq import read_recording
        iq, meta = read_recording(path, metadata, max_bytes)
        return cls(iq, meta['sample_rate'], meta.get('desired_frequency_hz', meta['center_hz']), source_sha256=meta['sha256'])


@dataclass(frozen=True)
class RecoveredFrame:
    """Actual decoder output, never arbitrary IQ represented as frame bytes.

    Parsed-only external events have raw_hex=None. Scalar fields keep this
    immutable and bounded; message text is represented only by actual bytes.
    """
    raw_hex: str | None = None
    start_sample: int | None = None
    sync_valid: bool = True
    checksum_valid: bool = True
    parsed_fields: tuple[tuple[str, str | int | float | bool | None], ...] = ()


@dataclass(frozen=True)
class DecoderEvidence:
    candidate_protocol: str
    input_sample_sha256: str
    frames_examined: int = 0
    frames_with_sync: int = 0
    frames_with_valid_checksum: int = 0
    frames_with_sync_and_valid_checksum: int = 0
    sync_reference: str = ''
    checksum_reference: str = ''
    notes: tuple[str, ...] = ()
    frames: tuple[RecoveredFrame, ...] = ()


class ProtocolDecoder(Protocol):
    name: str
    version: str

    def decode(self, recording: DecoderInput) -> DecoderEvidence:
        """Return evidence for exactly the supplied analysis input."""
        ...


def run_decoder(decoder: ProtocolDecoder, recording: DecoderInput):
    """Validate adapter evidence before exposing a verified protocol result.

    This verifies the evidence contract, not the adapter implementation. Only
    audited adapters with independent vector tests should be trusted in use.
    Exceptions propagate; an adapter failure must not become a verified label.
    """
    if not isinstance(recording, DecoderInput):
        raise ValueError('Expected validated DecoderInput')
    if any(not isinstance(getattr(decoder, key, None), str) or not getattr(decoder, key).strip() for key in ('name', 'version')):
        raise ValueError('Decoder name and version are required')
    input_digest = recording.sample_sha256
    evidence = decoder.decode(recording)
    if not isinstance(evidence, DecoderEvidence):
        raise ValueError('Decoder must return DecoderEvidence')
    if not isinstance(evidence.candidate_protocol, str) or not evidence.candidate_protocol.strip():
        raise ValueError('Protocol candidate must be a nonempty string')
    if recording.sample_sha256 != input_digest:
        raise ValueError('Decoder modified analysis input')
    if evidence.input_sample_sha256 != input_digest:
        raise ValueError('Decoder evidence refers to a different analysis input')
    counts = (evidence.frames_examined, evidence.frames_with_sync,
              evidence.frames_with_valid_checksum, evidence.frames_with_sync_and_valid_checksum)
    if any(type(value) is not int or value < 0 for value in counts):
        raise ValueError('Frame evidence counts must be nonnegative integers')
    examined, sync, checksum, joint = counts
    if max(sync, checksum) > examined or joint > min(sync, checksum) or joint < sync + checksum - examined:
        raise ValueError('Inconsistent frame verification counts')
    if any(not isinstance(value, str) for value in (evidence.sync_reference, evidence.checksum_reference)):
        raise ValueError('Verification references must be text')
    if not isinstance(evidence.notes, tuple) or any(not isinstance(value, str) for value in evidence.notes):
        raise ValueError('Decoder notes must be a tuple of text entries')
    verified = joint > 0
    if verified and (evidence.candidate_protocol.strip().upper() == 'UNKNOWN' or not evidence.sync_reference.strip() or not evidence.checksum_reference.strip()):
        raise ValueError('Verified frames need protocol, synchronization and checksum references')
    if not isinstance(evidence.frames, tuple) or len(evidence.frames) > 256:
        raise ValueError('Recovered frames must be a bounded immutable tuple')
    frames = []
    for frame in evidence.frames:
        if not isinstance(frame, RecoveredFrame) or type(frame.sync_valid) is not bool or type(frame.checksum_valid) is not bool:
            raise ValueError('Invalid recovered frame contract')
        if frame.raw_hex is not None and (not isinstance(frame.raw_hex, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,4096}', frame.raw_hex)):
            raise ValueError('Invalid recovered frame hexadecimal bytes')
        if frame.start_sample is not None and (type(frame.start_sample) is not int or not recording.start_sample <= frame.start_sample < recording.start_sample + len(recording.iq)):
            raise ValueError('Recovered frame sample offset outside analysis input')
        if not isinstance(frame.parsed_fields, tuple) or len(frame.parsed_fields) > 32:
            raise ValueError('Parsed fields must be a bounded immutable tuple')
        fields = {}
        for pair in frame.parsed_fields:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError('Invalid parsed field')
            key, value = pair
            if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', key) or key in fields:
                raise ValueError('Invalid or duplicate parsed field name')
            if value is not None and type(value) not in (str, int, float, bool):
                raise ValueError('Parsed fields must contain scalar values')
            if (isinstance(value, str) and len(value) > 256 or
                type(value) is int and not -(2**63) <= value < 2**63 or
                type(value) is float and not math.isfinite(value)):
                raise ValueError('Unbounded or nonfinite parsed field')
            fields[key] = value
        status = 'VERIFIED' if frame.sync_valid and frame.checksum_valid else 'CANDIDATE' if frame.sync_valid else 'INVALID'
        if status == 'VERIFIED' and frame.raw_hex is not None and evidence.candidate_protocol in ('ADS-B', 'AX.25', 'DMR', 'AIS'):
            # Independently gate exposed bytes as well as aggregate counts.
            # Parsed-only external events cannot be rechecked without bytes.
            if evidence.candidate_protocol == 'ADS-B':
                from .protocols import parsed_df17
                checked_fields = parsed_df17(bytes.fromhex(frame.raw_hex))
            elif evidence.candidate_protocol=='DMR':
                from .dmr import parsed_csbk,csbk_crc_valid
                if not csbk_crc_valid(bytes.fromhex(frame.raw_hex)):raise ValueError('Invalid exposed DMR CRC')
                checked_fields=dict(fields,**parsed_csbk(bytes.fromhex(frame.raw_hex)))
            elif evidence.candidate_protocol=='AIS':
                from .ais import parsed_ais
                checked_fields=parsed_ais(bytes.fromhex(frame.raw_hex))
            else:
                from .packet import parsed_ax25
                checked_fields = parsed_ax25(bytes.fromhex(frame.raw_hex))
            if fields != checked_fields:
                raise ValueError('Parsed fields do not match recovered frame bytes')
        frames.append({'schema':1,'protocol':evidence.candidate_protocol,
                       'source_sha256':recording.source_sha256,'sample_rate_hz':recording.sample_rate,
                       'frequency_hz':recording.frequency_hz,'decoder':decoder.name,'decoder_version':decoder.version,
                       'sync_reference':evidence.sync_reference,'checksum_reference':evidence.checksum_reference,
                       'correction':{'method':'DMR_GOLAY_AND_BPTC' if evidence.candidate_protocol=='DMR' else 'NONE_CLAIMED',
                                     'slot_bit_corrections':fields.get('slot_bit_corrections') if evidence.candidate_protocol=='DMR' else None,
                                     'bptc_correction_operations':fields.get('bptc_corrections') if evidence.candidate_protocol=='DMR' else None},
                       'raw_hex': frame.raw_hex.upper() if frame.raw_hex else None,
                       'frame_length': len(frame.raw_hex)//2 if frame.raw_hex else None,
                       'start_sample': frame.start_sample, 'sync_valid': frame.sync_valid,
                       'checksum_valid': frame.checksum_valid, 'status': status, 'parsed_fields': fields})
    if sum(f['status'] == 'VERIFIED' for f in frames) > joint or len(frames) > examined:
        raise ValueError('Recovered frame evidence exceeds validated frame counts')
    return {'protocol': evidence.candidate_protocol if verified else 'UNKNOWN',
            'candidate_protocol': evidence.candidate_protocol, 'protocol_verified': verified,
            'outcome':'DIAGNOSTIC_UNVERIFIED' if getattr(decoder,'capabilities',{}).get('diagnostic_only') else 'VERIFIED_PROTOCOL' if verified else 'CRC_FAILURE' if sync else 'NO_SYNCHRONIZATION',
            'protection': 'UNKNOWN', 'confidence': None,
            'decoder': {'name': decoder.name, 'version': decoder.version},
            'external_provenance':decoder.provenance() if hasattr(decoder,'provenance') else None,
            'input_sample_sha256': recording.sample_sha256, 'source_sha256': recording.source_sha256,
            'start_sample': recording.start_sample, 'sample_count': len(recording.iq),
            'frames': frames,
            'evidence': {'frames_examined': examined, 'frames_with_sync': sync,
                         'frames_with_valid_checksum': checksum,
                         'frames_with_sync_and_valid_checksum': joint,
                         'sync_reference': evidence.sync_reference,
                         'checksum_reference': evidence.checksum_reference, 'notes': list(evidence.notes)}}
