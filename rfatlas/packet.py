"""Receive-only AX.25 UI / Bell 202 AFSK1200 subset, independently authored.

This bounded offline decoder accepts shifted AX.25 addresses, UI control and
no-layer-3 PID. It does not infer APRS application semantics or retain payloads.
"""
from fractions import Fraction
import hashlib

import numpy as np
from scipy.signal import resample_poly

from .decoders import DecoderEvidence, DecoderInput, RecoveredFrame


def parsed_ax25(frame):
    """Parse validated address fields, without interpreting application text."""
    count = _ui_structure(frame)
    if count is None or ax25_fcs(frame[:-2]) != int.from_bytes(frame[-2:], 'little'):
        raise ValueError('Parsed AX.25 fields require valid structure and FCS')
    addresses = []
    for index in range(count):
        address = frame[index*7:index*7+7]
        callsign = bytes(c >> 1 for c in address[:6]).decode('ascii').rstrip()
        ssid = (address[6] >> 1) & 15
        addresses.append(callsign + (f'-{ssid}' if ssid else ''))
    return {'source': addresses[1], 'destination': addresses[0],
            'address_count': count, 'control': 'UI', 'pid': 'no-layer-3',
            'information_bytes': len(frame) - count*7 - 4, 'category': 'amateur-station'}


def ax25_fcs(data: bytes) -> int:
    """CRC-16/X-25: reflected 0x1021, init/xorout FFFF (check 906E)."""
    remainder = 0xffff
    for byte in data:
        remainder ^= byte
        for _ in range(8):
            remainder = (remainder >> 1) ^ (0x8408 if remainder & 1 else 0)
    return remainder ^ 0xffff


def _ui_structure(frame):
    """Validate address extension chain and UI/F0; return address count."""
    for count in range(1, 11):
        start = (count - 1) * 7
        if start + 7 > len(frame) - 4:
            return None
        address = frame[start:start + 7]
        chars = bytes(byte >> 1 for byte in address[:6])
        # Six upper-case alphanumeric characters, padded only on the right.
        name = chars.rstrip(b' ')
        if not name or any(byte & 1 for byte in address[:6]):
            return None
        if any(not (48 <= c <= 57 or 65 <= c <= 90) for c in name):
            return None
        if chars != name.ljust(6, b' ') or address[6] & 0x60 != 0x60:
            return None
        if address[6] & 1:
            end = count * 7
            if count < 2 or frame[end:end + 2] != b'\x03\xf0':
                return None
            # AX.25 information is bounded to 256 bytes for this subset.
            if not 1 <= len(frame) - end - 4 <= 256:
                return None
            return count
    return None


class AX25AFSK1200Decoder:
    name = 'native-ax25-afsk1200-ui'
    version = '1.0.0'
    capabilities = {
        'protocols': ['AX.25'], 'modulations': ['AFSK', 'NFM', '2FSK'],
        'frequency_hint_hz': (130000000,175000000),
        'sample_rate_min_hz': 24000, 'sample_rate_max_hz': 2400000,
        'expected_bandwidth_hz': 16000,
        'input': 'centered narrowband FM complex IQ; Bell 202 1200/2200 Hz at 1200 baud',
        'platforms': ['Windows', 'Linux'], 'external_dependencies': [],
        'hardware': 'none for offline replay', 'offline_replay': True,
        'evidence': ['HDLC flags and bit stuffing', 'AX.25 shifted address/UI/F0 structure', 'CRC-16/X-25'],
        'protection_metadata': False,
    }

    def __init__(self, max_symbols=24000, max_frames=64):
        if type(max_symbols) is not int or not 1000 <= max_symbols <= 120000:
            raise ValueError('Invalid symbol budget')
        if type(max_frames) is not int or not 1 <= max_frames <= 1024:
            raise ValueError('Invalid frame budget')
        self.max_symbols = max_symbols
        self.max_frames = max_frames

    def _scan(self, recording, include_bytes=False):
        if not isinstance(recording, DecoderInput):
            raise ValueError('Expected validated DecoderInput')
        rate = recording.sample_rate
        if not 24000 <= rate <= 2400000:
            raise ValueError('AX.25 AFSK1200 requires 24 kS/s to 2.4 MS/s')
        # Bound work before resampling; no implicit extension past the budget.
        limit = min(len(recording.iq), int(rate * (self.max_symbols + 2) / 1200))
        ratio = Fraction(24000 / rate).limit_denominator(100000)
        x = resample_poly(recording.iq[:limit], ratio.numerator, ratio.denominator)
        audio = np.angle(x[1:] * x[:-1].conj())
        audio -= np.median(audio)
        n = np.arange(len(audio))
        energies = []
        for frequency in (1200, 2200):
            mixed = audio * np.exp(-2j * np.pi * frequency * n / 24000)
            summed = np.r_[0j, np.cumsum(mixed)]
            energies.append(np.abs(summed[20:] - summed[:-20]) ** 2)
        tones = energies[1] > energies[0]
        flag = np.array([0, 1, 1, 1, 1, 1, 1, 0], dtype=np.uint8)
        examined = synced = 0
        records = []
        seen = set()
        limited = limit < len(recording.iq)
        # Fixed-rate clock hypotheses; deliberately no error correction or
        # sample-clock tracking. Frame FCS independently gates every result.
        for phase in range(20):
            levels = tones[phase::20][:self.max_symbols]
            bits = (levels[1:] == levels[:-1]).astype(np.uint8)
            if len(bits) < 8:
                continue
            windows = np.lib.stride_tricks.sliding_window_view(bits, 8)
            flags = np.flatnonzero(np.all(windows == flag, axis=1))
            for left, right in zip(flags[:-1], flags[1:]):
                raw = bits[left + 8:right]
                if not 144 <= len(raw) <= 3200:
                    continue
                examined += 1
                unstuffed = []
                ones = 0
                malformed = False
                for bit in raw:
                    if ones == 5:
                        if bit:
                            malformed = True
                            break
                        ones = 0
                        continue
                    unstuffed.append(bit)
                    ones = ones + 1 if bit else 0
                if malformed or ones == 5 or len(unstuffed) % 8 or len(unstuffed) < 144:
                    continue
                frame = np.packbits(np.array(unstuffed, dtype=np.uint8), bitorder='little').tobytes()
                count = _ui_structure(frame)
                if count is None:
                    continue
                synced += 1
                if ax25_fcs(frame[:-2]) != int.from_bytes(frame[-2:], 'little'):
                    continue
                # Deduplicate clock hypotheses for one physical frame without
                # collapsing an identical later transmission.
                position = int((phase + (left + 9) * 20) * rate / 24000)
                digest = hashlib.sha256(frame).digest()
                if any(key == digest and abs(position - old) < rate / 1200 * 3 for key, old in seen):
                    continue
                seen.add((digest, position))
                records.append({'start_sample': recording.start_sample + position,
                                'frame_bytes': len(frame), 'address_count': count,
                                'control': 'UI', 'pid': 'no-layer-3', 'fcs_valid': True})
                if include_bytes:
                    records[-1]['hex'] = frame.hex().upper()
                if len(records) >= self.max_frames:
                    limited = True
                    return records, examined, synced, limited
        return records, examined, synced, limited

    def frame_records(self, recording):
        """Validated structural metadata only; no addresses or information."""
        return self._scan(recording)[0]

    def decode(self, recording):
        records, examined, synced, limited = self._scan(recording, include_bytes=True)
        return DecoderEvidence(
            'AX.25', recording.sample_sha256, examined, synced, len(records), len(records),
            'HDLC 0x7E flags, zero stuffing, NRZI; 2..10 shifted AX.25 addresses; UI 0x03 / PID 0xF0',
            'CRC-16/X-25: reflected polynomial 0x8408, init/xorout FFFF, little-endian FCS',
            ('VHF Bell 202 AFSK1200 narrowband FM UI/F0 subset only; no APRS semantic claim.',
             'Actual frame bytes are transient; persistence requires explicit retention; protection UNKNOWN.',
             f'20 fixed-rate clock phases; symbol_limit={self.max_symbols}; frame_limit={self.max_frames}; budget_exhausted={limited}'),
            tuple(RecoveredFrame(r['hex'], r['start_sample'], parsed_fields=tuple(parsed_ax25(bytes.fromhex(r['hex'])).items())) for r in records[:256]))
