"""Metric definitions distinguish abstention from forced-choice predictions."""
import importlib.util
from pathlib import Path
import unittest

_spec = importlib.util.spec_from_file_location('evaluate_engine_metrics',
    Path(__file__).resolve().parents[1] / 'tools' / 'evaluate_engine.py')
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


class ClassificationMetricsTests(unittest.TestCase):
    def test_correct_noise_abstention_is_not_accepted_prediction(self):
        pairs = [('AM', 'AM'), ('AM', 'NFM'), ('BPSK', 'UNKNOWN'), ('NOISE', 'UNKNOWN')]
        rows = [dict(engine='dsp', target='UNKNOWN' if family == 'NOISE' else family,
                     family=family, predicted=prediction, signal_detected=family != 'NOISE',
                     snr_db=12, runtime_ms=1.) for family, prediction in pairs]
        result = _module.summarize(rows, 'dsp')
        self.assertEqual(result['accuracy'], .5)
        self.assertEqual(result['selective_accuracy'], .5)
        self.assertEqual(result['coverage'], .5)
        self.assertEqual(result['rejection_rate'], .5)
        self.assertEqual(result['correct_and_accepted_fraction'], .25)
        self.assertEqual(result['correct_and_accepted_cases'], 1)
        self.assertIsNone(result['forced_choice_accuracy'])
        self.assertEqual(result['per_class']['AM']['precision'], 1.)
        self.assertEqual(result['per_class']['AM']['recall'], .5)

    def test_empty_subset_does_not_invent_zero_accuracy(self):
        result = _module.summarize([], 'dsp')
        for name in ['accuracy', 'coverage', 'rejection_rate', 'selective_accuracy',
                     'correct_and_accepted_fraction', 'forced_choice_accuracy',
                     'noise_false_label_rate', 'signal_rejection_rate']:
            self.assertIsNone(result[name])

    def test_all_abstentions_have_zero_coverage_and_nullable_selectivity(self):
        row = dict(engine='dsp', target='AM', family='AM', predicted='UNKNOWN',
                   signal_detected=True, snr_db=12, runtime_ms=1.)
        result = _module.summarize([row], 'dsp')
        self.assertEqual(result['coverage'], 0.)
        self.assertEqual(result['rejection_rate'], 1.)
        self.assertEqual(result['correct_and_accepted_fraction'], 0.)
        self.assertIsNone(result['selective_accuracy'])
        self.assertIsNone(result['noise_false_label_rate'])


if __name__ == '__main__':
    unittest.main()
