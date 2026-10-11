"""CLI recovery/interchange works offline with private temporary runtime roots."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from rfatlas.engine import classify
from rfatlas.synthetic import generate


class EngineCLITests(unittest.TestCase):
    def test_replay_and_sigmf_cli_without_hardware(self):
        with tempfile.TemporaryDirectory(prefix='rf atlas ') as folder:
            root=Path(folder);config=root/'config.toml'
            config.write_text('[runtime]\nroot="./private runtime"\n',encoding='utf8')
            source=root/'synthetic source.cu8';x=generate('CW',snr_db=None,offset=10000)
            raw=np.empty(len(x)*2,np.uint8);raw[::2]=np.round(x.real*127.5+127.5);raw[1::2]=np.round(x.imag*127.5+127.5)
            source.write_bytes(raw.tobytes())
            source.with_suffix('.json').write_text(json.dumps({'format':'cu8','center_hz':100000000,'desired_frequency_hz':100010000,'tuning_offset_hz':-10000,'sample_rate':250000,'sample_count':len(x),'sha256':hashlib.sha256(raw.tobytes()).hexdigest()}))
            env=dict(os.environ)
            env.pop('RF_ATLAS_HOME',None);env.pop('RF_ATLAS_CONFIG',None)
            def run(*args):
                completed=subprocess.run([sys.executable,'-m','rfatlas','--config',str(config),*map(str,args)],capture_output=True,text=True,env=env,timeout=30)
                self.assertEqual(completed.returncode,0,completed.stderr)
                return json.loads(completed.stdout)
            result=run('replay',source,'--backend','ml','--model',root/'missing.npz')
            self.assertEqual(result['backend'],'dsp');self.assertEqual(result['modulation'],'CW')
            exported=run('sigmf-export',source,root/'standard capture')
            imported=run('sigmf-import',exported['metadata'],root/'round trip')
            result=run('replay',imported['path'],'--backend','baseline')
            self.assertEqual(result['modulation'],'CW')
            self.assertEqual(source.read_bytes(),Path(imported['path']).read_bytes())

    def test_missing_optional_module_falls_back(self):
        with patch.dict(sys.modules,{'rfatlas.ml':None}):
            result=classify(generate('4FSK'),250000,{'model_path':'requested.npz'},'ml')
        self.assertEqual(result['backend'],'dsp')
        self.assertEqual(result['modulation'],'4FSK')


if __name__=='__main__':unittest.main()
