"""Optional DSD-FME offline diagnostics; console labels never verify frames.

No executable is distributed. Input is a bounded temporary mono 48 kHz FM
discriminator WAV, output is null, and hardware/trunking/call recording options
are never passed. Native DMR independently supplies verification.
"""
from pathlib import Path
import math
import re
import shutil
import tempfile
import wave
import numpy as np
from .decoder_process import run_decoder_process
from .decoders import DecoderEvidence
from .dmr import discriminator


class DSDFMEDiagnostic:
    name='optional-dsd-fme-diagnostic'
    version='1'
    capabilities=dict(protocols=['DMR'],modulations=['4FSK'],platforms=['Windows','Linux'],
        offline_replay=True,evidence='Diagnostic only: console sync labels lack per-frame integrity contract',
        sample_rate_min_hz=48000,sample_rate_max_hz=2400000,expected_bandwidth_hz=12500,
        diagnostic_only=True,input_format='temporary mono signed PCM16 WAV 48000 Hz',
        minimum_duration_s=.06,external_dependencies=['operator-trusted dsd-fme'],metadata=[])

    def __init__(self, executable='dsd-fme', timeout=10, runner=None):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=30:
            raise ValueError('Invalid DSD timeout')
        self.executable=str(executable);self.timeout=timeout;self.runner=runner or run_decoder_process

    def available(self):return bool(shutil.which(self.executable))

    def provenance(self):
        from .decoder_process import binary_provenance
        return binary_provenance(self.executable)

    def decode(self, recording):
        if not self.available():raise RuntimeError('Optional DSD-FME unavailable')
        fm=discriminator(recording.iq,recording.sample_rate)
        pcm=np.rint(np.clip(fm/6000,-.98,.98)*32767).astype('<i2')
        with tempfile.TemporaryDirectory(prefix='rfatlas-dsd-') as directory:
            path=Path(directory)/'input.wav'
            with wave.open(str(path),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(48000);output.writeframes(pcm.tobytes())
            stdout,stderr=self.runner([self.executable,'-fs','-i',str(path),'-s','48000','-o','null'],self.timeout,cwd=directory)
        # Count console sync indications only. Never store private log lines.
        clean=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',stdout+'\n'+stderr)
        indications=len(re.findall(r'(?i)(?:sync[^\n]{0,30}DMR|DMR[^\n]{0,30}sync)',clean))
        return DecoderEvidence('DMR',recording.sample_sha256,indications,indications,0,0,
            'DSD-FME console synchronization indications (diagnostic only)','',
            ('No audited per-frame CRC output: external console labels never promote protocol verification.',
             'Offline file input only; no SDR, trunking, network, speech files or retained console payloads.'))
