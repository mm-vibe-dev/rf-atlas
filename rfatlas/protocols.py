"""Independently authored, receive-only Mode S DF17 pulse/CRC decoder.

Only direct-parity DF17 extended squitters are supported. No parity recovery,
error correction, location reconstruction, or identity database is used.
"""
import numpy as np

from .decoders import DecoderEvidence, DecoderInput, RecoveredFrame


def parsed_df17(frame):
    """Parse direct DF17 address and identification only after CRC validation.

    ICAO addresses can be reassigned or spoofed: this identifies a transmitted
    protocol address, not authenticated ownership or a unique physical aircraft.
    """
    if len(frame) != 14 or frame[0] >> 3 != 17 or modes_crc24(frame):
        raise ValueError('DF17 parsed fields require a valid complete frame')
    fields = {'downlink_format': 17, 'type_code': frame[4] >> 3,
              'icao24': frame[1:4].hex().upper(), 'category': 'aircraft'}
    if 1 <= fields['type_code'] <= 4:
        encoded = int.from_bytes(frame[5:11], 'big')
        codes = [(encoded >> shift) & 63 for shift in range(42, -1, -6)]
        if all(1 <= c <= 26 or 48 <= c <= 57 or c == 32 for c in codes):
            callsign = ''.join(chr(c + 64) if c <= 26 else chr(c) for c in codes).strip()
            if callsign: fields['callsign'] = callsign
    return fields


def modes_crc24(frame: bytes) -> int:
    """MSB-first polynomial remainder, including a received parity field.

The full generator is x^24 + ... + x^12 + x^10 + x^3 + 1.
Append three zero bytes to an 11-byte payload to compute its parity.
"""
    remainder = 0
    for byte in frame:
        for shift in range(7, -1, -1):
            remainder = (remainder << 1) | ((byte >> shift) & 1)
            if remainder & (1 << 24):
                remainder ^= 0x1FFF409
    return remainder


class ModeSDecoder:
    name = 'native-mode-s-df17'
    version = '1.0.1'
    capabilities = {
        'protocols': ['ADS-B'], 'modulations': ['PPM', 'OOK', 'ASK'],
        'frequency_hint_hz': (1089000000,1091000000),
        'sample_rate_min_hz': 2000000, 'sample_rate_max_hz': 10000000,
        'expected_bandwidth_hz': 2000000,
        'input': 'finite normalized complex IQ; 120 us complete frame',
        'platforms': ['Windows', 'Linux'], 'external_dependencies': [],
        'hardware': 'none for replay; receiver covering 1090 MHz for live input',
        'offline_replay': True,
        'evidence': ['four-pulse preamble', '112-bit PPM', 'DF17', 'CRC24'],
    }

    def __init__(self, max_candidates=10000, max_frames=256):
        if type(max_candidates) is not int or not 1 <= max_candidates <= 100000:
            raise ValueError('Invalid candidate budget')
        if type(max_frames) is not int or not 1 <= max_frames <= 4096:
            raise ValueError('Invalid frame budget')
        self.max_candidates = max_candidates
        self.max_frames = max_frames

    def _scan(self, recording):
        if not isinstance(recording, DecoderInput):
            raise ValueError('Expected validated DecoderInput')
        rate = recording.sample_rate
        if not 2000000 <= rate <= 10000000:
            raise ValueError('Mode S DF17 requires 2 to 10 MHz sample rate')
        power = np.abs(recording.iq) ** 2
        floor = float(np.median(power))
        # An absolute amplitude threshold is deliberately avoided: gain changes
        # must not manufacture protocol evidence. A preamble still needs both
        # quiet gaps and balanced strong pulses after this candidate filter.
        threshold = max(floor * 12, float(np.quantile(power, .95)) * .15, 1e-15)
        above = power > threshold
        edges = np.flatnonzero(above & ~np.r_[False, above[:-1]])
        cumulative = np.r_[0., np.cumsum(power)]

        def integral(positions):
            indices = np.floor(positions).astype(np.int64)
            fractions = positions - indices
            return cumulative[indices] + fractions * power[np.minimum(indices, len(power)-1)]

        def cells(start, count):
            bounds = start + np.arange(count + 1) * rate / 2000000
            return np.diff(integral(bounds)) / (rate / 2000000)

        pulse = np.array([0, 2, 7, 9])
        quiet = np.array([1, 3, 4, 5, 6, 8, 10, 11, 12, 13, 14, 15])
        records = []
        synced = 0
        examined = 0
        last_end = -1.
        budget_exhausted = len(edges) > self.max_candidates
        for edge in edges[:self.max_candidates]:
            if edge < last_end:
                continue
            if edge + 120e-6 * rate + 1 >= len(power):
                continue
            examined += 1
            saw_sync = False
            for fraction in (0., -.25, -.5, -.75):
                start = float(edge) + fraction
                if start < 0:
                    continue
                preamble = cells(start, 16)
                high = float(np.mean(preamble[pulse]))
                low = float(np.max(preamble[quiet]))
                if high <= max(floor * 5, 1e-15) or np.min(preamble[pulse]) < high * .45 or low > high * .45:
                    continue
                saw_sync = True
                data = cells(start + 8e-6 * rate, 224).reshape(112, 2)
                stronger = np.max(data, axis=1)
                weaker = np.min(data, axis=1)
                # Erasures cannot be silently filled with zeros or repaired.
                if np.any(stronger < high * .25) or np.any(stronger-weaker < high * .15):
                    continue
                bits = (data[:, 0] > data[:, 1]).astype(np.uint8)
                frame = np.packbits(bits).tobytes()
                if frame[0] >> 3 != 17 or modes_crc24(frame) != 0:
                    continue
                records.append({'hex': frame.hex().upper(),
                                'start_sample': recording.start_sample + int(round(start)),
                                'downlink_format': 17, 'type_code': frame[4] >> 3,
                                'crc24_remainder': 0, 'preamble_valid': True})
                last_end = start + 120e-6 * rate
                break
            synced += int(saw_sync)
            if len(records) >= self.max_frames:
                budget_exhausted = True
                break
        return records, examined, synced, budget_exhausted

    def frame_records(self, recording):
        """Return validated frames only; caller decides private persistence."""
        return self._scan(recording)[0]

    def decode(self, recording):
        records, examined, synced, limited = self._scan(recording)
        notes = ('DF17 only; no error correction; metadata only; protection UNKNOWN',
                 f'candidate_limit={self.max_candidates}; frame_limit={self.max_frames}; budget_exhausted={limited}')
        return DecoderEvidence('ADS-B', recording.sample_sha256,
                               examined, synced, len(records), len(records),
                               'Mode S 8 us preamble: 0.5 us pulses at 0, 1, 3.5, 4.5 us; 112-bit PPM; DF17',
                               'CRC24 polynomial 0x1FFF409; entire 112-bit frame remainder zero', notes,
                               tuple(RecoveredFrame(r['hex'], r['start_sample'], parsed_fields=tuple(parsed_df17(bytes.fromhex(r['hex'])).items())) for r in records[:256]))
