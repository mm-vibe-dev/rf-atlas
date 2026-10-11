import csv
from pathlib import Path
import tempfile
import unittest
import numpy as np
from rfatlas.detection import detect
from rfatlas.diagnostics import read_spectra,noise_model


class DetectionTests(unittest.TestCase):
    def test_noise_grouping_and_adaptive_floor(self):
        power=np.linspace(-80,-40,400);power[100:105]+=15
        peaks=detect(100000000,1000,power)
        self.assertEqual(len(peaks),1);self.assertEqual(peaks[0]['high']-peaks[0]['low'],5000)
        self.assertTrue(100100000<=peaks[0]['center']<=100105000)
        self.assertEqual(detect(0,1000,np.full(100,-60)),[])

    def test_invalid_rows_and_nonfinite(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.csv'
            values=[-60]*100;values[5]='-nan(ind)';values[6]='inf'
            with path.open('w',newline='') as file:csv.writer(file).writerow(['2026-01-01','12:00:00',100000000,100100000,1000,100,*values])
            rows,report=read_spectra(path)
            self.assertFalse(rows);self.assertEqual(report['rejected_bins'],2)
            path.write_text('incomplete,row\n');self.assertTrue(read_spectra(path)[1]['rejected_rows'])

    def test_fft_bin_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.csv'
            with path.open('w',newline='') as file:csv.writer(file).writerow(['2026-01-01','12:00:00',99900454,100099546,976.04,100,*([-60]*207)])
            rows,report=read_spectra(path)
            self.assertEqual(len(rows),1);self.assertEqual(report['duplicate_tail_bins'],1)
            self.assertEqual(rows[0]['centers'][103],100000000)

    def test_threshold_sensitivity(self):
        power=np.full(200,-60.);power[100:104]=-52
        floor,threshold=noise_model(power,1000)
        self.assertEqual(floor[101],-60);self.assertEqual(threshold[101],-54)
        self.assertEqual(len(detect(0,1000,power,6)),1);self.assertFalse(detect(0,1000,power,9))


if __name__=='__main__':unittest.main()
