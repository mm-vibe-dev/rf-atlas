"""Timed-out measurements stay distinct from independently acquired retries."""
import csv
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import closing
from rfatlas.storage import open_database
from rfatlas.survey import survey
from rfatlas import legacy
from rfatlas.platforms import DeviceLock


class SpectrumTimeoutTests(unittest.TestCase):
    def test_timeout_preserves_partial_and_retries_finite_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data/spectra').mkdir(parents=True)
            class Backend:
                selector='fixture';ingest=staticmethod(legacy.ingest)
                def tool(self,name):return name
                def lock(self):return DeviceLock(root/'locks')
                def run(self,args,timeout,diagnostic):
                    self.deadline=timeout;Path(args[-1]).write_text('incomplete measurement')
                    Path(diagnostic).write_text(json.dumps(dict(error='TimeoutExpired',stderr='fixture receiver diagnostic')))
                    raise subprocess.TimeoutExpired(args,timeout)
            def replacement(backend,path,low,high,step,duration,gain):
                with path.open('w',newline='',encoding='utf8') as f:csv.writer(f).writerow(['2026-01-01','12:00:01+00:00',low,high,step,100,*([-60]*80)])
                path.with_suffix('.command.json').write_text(json.dumps(dict(backend='V4_RF_FLOAT_WELCH',settings=dict(duration_s=duration))))
            backend=Backend();args=SimpleNamespace(start=100000000,stop=102000000,profile='quick',antenna='fixture',new=True,integration=1,duration=3,gain='15.7',margin=6)
            with closing(open_database(root)) as db,patch('rfatlas.hf_spectrum.acquire',side_effect=replacement) as retry:
                run=survey(db,args,root,backend);self.assertEqual(backend.deadline,18);self.assertEqual(retry.call_count,1)
                accepted=Path(db.execute('SELECT raw_path FROM scan_regions WHERE run_id=?',(run,)).fetchone()[0]);meta=json.loads(accepted.with_suffix('.command.json').read_text());failure=meta['fallback']
                self.assertEqual(failure['reason'],'RTL_POWER_TIMEOUT')
                self.assertEqual(Path(failure['partial_spectrum']).read_text(),'incomplete measurement')
                self.assertIn('TimeoutExpired',Path(failure['failed_command']).read_text())
                self.assertEqual(db.execute('SELECT valid FROM acquisition_timings WHERE run_id=?',(run,)).fetchone()[0],1)


if __name__=='__main__':unittest.main()
