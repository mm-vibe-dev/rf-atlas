"""Public deterministic DSP and recording-integrity regression coverage."""
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from rfatlas.activity import analyze_activity
from rfatlas.features import classify
from rfatlas.synthetic import generate
from rfatlas.iq import checksum,read_recording,replay
from rfatlas.storage import open_database


class AnalysisTests(unittest.TestCase):
    def test_late_burst_window(self):
        activity=analyze_activity(generate('2FSK',duration=3,burst=True,snr_db=30),250000)
        self.assertEqual(activity['behavior'],'INTERMITTENT')
        self.assertGreater(activity['selected_start_sample']/250000,1.9)
        self.assertLess(activity['active_fraction_estimate'],.3)

    def test_short_burst(self):
        activity=analyze_activity(generate('4FSK',duration=.3,burst=True,snr_db=30),250000)
        self.assertEqual(activity['behavior'],'BURSTY')

    def test_channel_activity_and_invalid_bandwidth(self):
        x=generate('4FSK',duration=.3,burst=True,snr_db=15)
        activity=analyze_activity(x,250000,15000)
        self.assertEqual(activity['behavior'],'BURSTY')
        self.assertGreater(activity['selected_start_sample']/250000,.18)
        with self.assertRaises(ValueError):analyze_activity(x,250000,100000)
        self.assertEqual(analyze_activity(generate('NOISE'),250000,15000)['behavior'],'UNKNOWN')

    def test_cw_including_baseband_dc(self):
        for offset in [0,20000,-20000]:
            x=generate('CW',snr_db=None,offset=offset)
            self.assertEqual(classify(x,250000)['modulation'],'CW')
            self.assertEqual(analyze_activity(x,250000)['behavior'],'CONTINUOUS')

    def test_noise_false_positives(self):
        for seed in range(8):
            result=classify(generate('NOISE',seed=seed),250000)
            self.assertFalse(result['signal_detected'])
            self.assertEqual(result['modulation'],'UNKNOWN')
            self.assertEqual(result['protocol'],'UNKNOWN')
            self.assertEqual(result['protection'],'UNKNOWN')

    def test_clean_phase_and_frequency_modulation(self):
        for family in ['BPSK','QPSK','2FSK','4FSK']:
            result=classify(generate(family,snr_db=None),250000)
            self.assertEqual(result['modulation'],family)
            self.assertEqual(result['confidence_kind'],'UNCALIBRATED_HEURISTIC_SUPPORT')

    def test_clipping_rejected(self):
        result=classify(generate('QPSK',gain=2,clipping=True,snr_db=None),250000)
        self.assertTrue(result['signal_detected'])
        self.assertEqual(result['modulation'],'UNKNOWN')

    def test_offset_mapping_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic capture.cu8';n=25000;rate=250000
            # Desired signal sits below tuner center; replay must translate up.
            x=.4*np.exp(-2j*np.pi*20000*np.arange(n)/rate)
            raw=np.empty(n*2,dtype=np.uint8)
            raw[::2]=np.round(x.real*127.5+127.5);raw[1::2]=np.round(x.imag*127.5+127.5)
            path.write_bytes(raw.tobytes())
            meta={'format':'cu8','sample_rate':rate,'center_hz':100020000,'desired_frequency_hz':100000000,'tuning_offset_hz':20000,'sample_count':n,'sha256':checksum(path)}
            metadata=path.with_suffix('.json');metadata.write_text(json.dumps(meta))
            result,_=replay(path)
            self.assertAlmostEqual(result['features']['centroid_offset_hz'],0,delta=rate/4096)
            # A translated stable tone establishes a carrier, not CW keying.
            self.assertEqual(result['modulation'],'CARRIER')
            self.assertEqual(result['signal_class'],'CARRIER')
            meta['tuning_offset_hz']=-20000;metadata.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError,'offset'):read_recording(path)
            meta['tuning_offset_hz']=20000;metadata.write_text(json.dumps(meta))
            changed=bytearray(path.read_bytes());changed[0]^=1;path.write_bytes(changed)
            with self.assertRaisesRegex(ValueError,'checksum'):read_recording(path)
            path.write_bytes(changed[:-1])
            with self.assertRaisesRegex(ValueError,'incomplete'):read_recording(path)
            metadata.write_text('[]')
            with self.assertRaisesRegex(ValueError,'object'):read_recording(path)

    def test_quantized_phase_corners_are_not_psk_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'low-level.cu8'
            x=generate('QPSK',snr_db=None,gain=.005);raw=np.empty(len(x)*2,dtype=np.uint8)
            raw[::2]=np.round(x.real*127.5+127.5);raw[1::2]=np.round(x.imag*127.5+127.5)
            path.write_bytes(raw.tobytes())
            path.with_suffix('.json').write_text(json.dumps({'format':'cu8','sample_rate':250000,'center_hz':100000000,'sample_count':len(x),'sha256':checksum(path)}))
            result,_=replay(path)
            self.assertEqual(result['modulation'],'UNKNOWN')
            self.assertLess(result['adc_amplitude_p95_counts'],4)

    def test_additive_database_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data').mkdir()
            db=open_database(root)
            columns={row[1] for row in db.execute('PRAGMA table_info(spectral_observations)')}
            self.assertTrue({'sample_rate_hz','fft_resolution_hz','integration_s','threshold_margin_db','measurement_settings'}<=columns)
            db.close()
            db=open_database(root);db.close()


if __name__=='__main__':unittest.main()
