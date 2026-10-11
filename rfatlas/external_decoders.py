"""Optional offline decoder adapters; no third-party decoder code is bundled."""
import json
import math
from pathlib import Path
import shutil
import tempfile

import numpy as np

from .decoders import DecoderEvidence, RecoveredFrame
from .decoder_process import run_decoder_process


class RTL433Decoder:
    """Audited subset: Fine Offset WH2 CRC-8/PWM, rtl_433 protocol 18.

    A JSON model label alone is insufficient. Only the audited model with the
    upstream CRC marker and typed, plausible fields contributes evidence.
    External output remains a trust boundary: use an operator-trusted binary.
    """
    name = 'rtl_433-wh2'
    version = '2'
    event_model = 'Fineoffset-WH2'
    protocol_number='18'
    minimum_temperature = -204.7
    maximum_temperature = 204.7
    sync_reference = 'rtl_433 protocol 18: 48-bit PWM frame, FF preamble and type nibble 4'
    protocols = ('Fineoffset-WH2',)
    modulation_families = ('OOK',)
    capabilities = {
        'protocols': protocols, 'modulations': modulation_families,
        'routing_group': 'weather-ook',
        'frequency_hint_hz': (250000000,960000000),
        'sample_rate_min_hz': 100000, 'sample_rate_max_hz': 2400000,
        'required_sample_rate_hz': None, 'expected_bandwidth_hz': 100000,
        'input': 'bounded complex IQ converted to CU8',
        'platforms': ('Windows', 'Linux'), 'external_dependencies': ('rtl_433',),
        'hardware': (), 'offline_replay': True,
        'evidence': ('48-bit WH2 structure/preamble/type', 'upstream CRC-8 0x31 validation'),
        'protection_metadata': False,
    }

    def __init__(self, executable='rtl_433', timeout=8, runner=None):
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError('Decoder timeout must be in (0, 30] seconds')
        self.executable = str(executable)
        self.timeout = float(timeout)
        self.runner = runner or run_decoder_process

    def available(self):
        return bool(shutil.which(self.executable))

    def parse_event(self,event):
        identity,humidity,temperature=(event.get(k) for k in ('id','humidity','temperature_C'))
        if type(identity) is not int or not 0<=identity<=255:return None
        if type(humidity) is not int or not 0<=humidity<=100:return None
        if type(temperature) not in (int,float) or not math.isfinite(temperature) or not self.minimum_temperature<=temperature<=self.maximum_temperature:return None
        return dict(model=self.event_model,sensor_id=identity,temperature_c=temperature,humidity=humidity,category='environmental-sensor')

    def provenance(self):
        from .decoder_process import binary_provenance
        return binary_provenance(self.executable)

    def decode(self, recording):
        if not self.available():
            raise RuntimeError('Optional rtl_433 executable is unavailable')
        if not 100000 <= recording.sample_rate <= 2400000:
            raise ValueError('Fine Offset adapter requires 100 kS/s through 2.4 MS/s')
        # The file contains the translated analysis input, centered on its
        # desired signal. Capture metadata and source digest remain external.
        x = recording.iq
        cu8 = np.empty((len(x), 2), dtype=np.uint8)
        cu8[:, 0] = np.clip(np.rint(x.real * 127.5 + 127.5), 0, 255)
        cu8[:, 1] = np.clip(np.rint(x.imag * 127.5 + 127.5), 0, 255)
        with tempfile.TemporaryDirectory(prefix='rfatlas-decode-') as directory:
            root = Path(directory)
            config = root / 'empty.conf'
            config.write_text('# RF Atlas offline-only configuration\n', encoding='utf-8')
            path = root / 'input.cu8'
            path.write_bytes(cu8.tobytes())
            # Explicit empty config suppresses implicit operator configuration
            # (including network outputs). File input never opens the SDR.
            # rtl_433 infers rate AND modulation from the entire path, including
            # random temporary directory names. Force both content and format,
            # plus the validated rate; -s alone does not override path inference.
            input_spec=f'cu8:iq:{round(recording.sample_rate)}sps:{path}'
            argv = [self.executable, '-c', str(config), '-R', self.protocol_number, '-r', input_spec,
                    '-s', str(round(recording.sample_rate)), '-F', 'json']
            stdout, _ = self.runner(argv, self.timeout)
        if len(stdout.encode('utf-8')) > 1024 * 1024:
            raise ValueError('Decoder output exceeds one MiB')
        valid = 0
        examined = 0
        frames = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            examined += 1
            try:
                event = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict) or event.get('model') != self.event_model or event.get('mic') != 'CRC':
                continue
            fields=self.parse_event(event)
            if fields is None:continue
            valid += 1
            if len(frames)<256:frames.append(RecoveredFrame(parsed_fields=tuple(fields.items())))
        return DecoderEvidence(
            candidate_protocol=self.event_model, input_sample_sha256=recording.sample_sha256,
            frames_examined=examined, frames_with_sync=valid,
            frames_with_valid_checksum=valid, frames_with_sync_and_valid_checksum=valid,
            sync_reference=self.sync_reference,
            checksum_reference=self.capabilities.get('checksum_reference','rtl_433 Fine Offset callback: CRC-8 polynomial 0x31, initial 0'),
            notes=('Audited model subset only; mic=CRC is upstream evidence, not a rechecked raw frame.',
                   'Counts represent decoder output events, not every searched RF frame.',
                   'No encryption or privacy metadata is supplied by this adapter.'), frames=tuple(frames))


class RTL433WH5Decoder(RTL433Decoder):
    """Audited WH5 subset; deliberately separate from the WH2 model contract."""
    name = 'rtl_433-wh5'
    event_model = 'Fineoffset-WH5'
    minimum_temperature = -40.0
    maximum_temperature = 60.0
    sync_reference = 'rtl_433 protocol 18: 47-bit PWM frame, 7-bit FE preamble and type nibble 4; WH5 temperature encoding'
    protocols = ('Fineoffset-WH5',)
    capabilities = dict(RTL433Decoder.capabilities,
                        protocols=protocols,
                        evidence=('47-bit WH5 structure/preamble/type', 'upstream CRC-8 0x31 validation'))


class RTL433WH24Decoder(RTL433Decoder):
    """Optional audited WH24 weather telemetry subset, external protocol 78."""
    name='rtl_433-wh24'
    version='1'
    event_model='Fineoffset-WH24'
    protocol_number='78'
    sync_reference='rtl_433 protocol 78: AA preamble, 2DD4 sync, family 24, 17-byte payload'
    protocols=('Fineoffset-WH24',)
    capabilities=dict(RTL433Decoder.capabilities,protocols=protocols,modulations=('2FSK',),routing_group='weather-fsk',
        expected_bandwidth_hz=100000,minimum_duration_s=.03,
        checksum_reference='rtl_433 WH24 callback: CRC-8 31 init 00 over first 16 bytes equals zero; byte16 sum check',
        evidence=('2DD4 sync and family 24','CRC-8 and byte-sum validation'))

    def parse_event(self,event):
        identity=event.get('id');battery=event.get('battery_ok')
        if type(identity) is not int or not 0<=identity<=255:return None
        if type(battery) not in (int,float) or battery not in (0,1):return None
        fields=dict(model=self.event_model,sensor_id=identity,battery_ok=bool(battery),category='environmental-sensor')
        mappings={'temperature_C':('temperature_c',-40,60),'humidity':('humidity',0,100),
            'wind_dir_deg':('wind_direction_degrees',0,359),'wind_avg_m_s':('wind_average_m_s',0,200),
            'wind_max_m_s':('wind_gust_m_s',0,300),'rain_mm':('rain_mm',0,100000)}
        for key,(target,lo,hi) in mappings.items():
            if key not in event:continue
            value=event[key]
            if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:return None
            fields[target]=value
        return fields if any(k in fields for k in ('temperature_c','humidity','rain_mm','wind_average_m_s')) else None


class RTL433WH1050Decoder(RTL433Decoder):
    """Audited protocol 69 weather-data subset; clock packets are excluded."""
    name='rtl_433-wh1050'
    version='1'
    event_model='Fineoffset-WH1050'
    protocol_number='69'
    protocols=('Fineoffset-WH1050',)
    sync_reference='rtl_433 protocol 69: 79/80-bit PWM frame, FE/FF preamble, weather type 5'
    capabilities=dict(RTL433Decoder.capabilities,protocols=protocols,
        minimum_duration_s=.2,
        metadata=['model','sensor_id','battery_ok','temperature_c','humidity','wind_average_km_h','wind_gust_km_h','rain_mm'],
        checksum_reference='rtl_433 WH1050 callback: CRC-8 polynomial 31 init 00 over 9 payload bytes',
        evidence=('FE/FF preamble and 72-bit payload; weather type 5','upstream CRC-8 31 validation'))

    def parse_event(self,event):
        if type(event.get('msg_type')) is not int or event['msg_type']!=5:return None
        identity=event.get('id');battery=event.get('battery_ok')
        if type(identity) is not int or not 0<=identity<=255:return None
        if type(battery) not in (int,float) or battery not in (0,1):return None
        fields=dict(model=self.event_model,sensor_id=identity,battery_ok=bool(battery),category='environmental-sensor')
        mappings={'temperature_C':('temperature_c',-40,62.3),'humidity':('humidity',0,100),
                  'wind_avg_km_h':('wind_average_km_h',0,313),'wind_max_km_h':('wind_gust_km_h',0,313),
                  'rain_mm':('rain_mm',0,19661)}
        for key,(target,lo,hi) in mappings.items():
            value=event.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:return None
            fields[target]=value
        return fields
