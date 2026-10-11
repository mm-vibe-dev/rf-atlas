"""Reproduce non-finite CLI rows without accepting replacement RF power."""
from pathlib import Path
import tempfile
import unittest
from rfatlas.diagnostics import read_spectra
from rfatlas.measurement_errors import SpectrumMeasurementError,rejection_code
from rfatlas.legacy import ingest


class MeasurementRejectionTests(unittest.TestCase):
    def test_windows_negative_nan_rejects_entire_original_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'spectrum.csv'
            values=['-40.0']*16;values[4]='-nan(ind)'
            text='2026-01-01, 12:00:00, 100000000, 100016000, 1000, 100, '+', '.join(values)+'\n'
            path.write_text(text)
            rows,report=read_spectra(path)
            self.assertEqual(rows,[])
            self.assertEqual(rejection_code(report),'NONFINITE_OR_MALFORMED_POWER')
            with self.assertRaises(SpectrumMeasurementError) as error:
                ingest(None,'unused',path,100000000,100016000)
            self.assertEqual(error.exception.code,'NONFINITE_OR_MALFORMED_POWER')
            self.assertEqual(path.read_text(),text)
            self.assertTrue(path.with_suffix('.parse.json').is_file())

    def test_empty_output_has_distinct_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'spectrum.csv';path.write_text('')
            rows,report=read_spectra(path)
            self.assertEqual(rows,[]);self.assertEqual(rejection_code(report),'EMPTY_SPECTRUM')


if __name__=='__main__':unittest.main()
