"""Independent corpus and optional small-model safety checks (no RF fixtures)."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile
import io
import numpy as np
from rfatlas.corpus import cases, stress_cases, generate_case, SPLIT_SEEDS
from rfatlas import ml


class IndependentCorpusTests(unittest.TestCase):
    def test_split_seeds_disjoint_and_bounded(self):
        groups=[{c['seed'] for c in cases(s)} for s in SPLIT_SEEDS]
        self.assertEqual([len(x) for x in groups],[360]*3)
        for i in range(3):
            for j in range(i):self.assertFalse(groups[i]&groups[j])
        with self.assertRaises(ValueError):cases('unknown')

    def test_development_generation_reproducible_and_independent(self):
        case=cases('development',1)[0]
        x=generate_case(case)
        self.assertTrue(np.array_equal(x,generate_case(case)))
        self.assertGreaterEqual(len(x),4096)
        self.assertEqual(x.dtype,np.complex64)
        self.assertTrue(np.isfinite(x).all())
        changed=dict(case,seed=case['seed']+1)
        self.assertFalse(np.array_equal(x,generate_case(changed)))

    def test_all_development_waveforms_valid(self):
        for c in cases('development',1)[::6]:
            x=generate_case(c)
            self.assertTrue(np.isfinite(x).all())
            self.assertEqual(ml.feature_vector(x,c['sample_rate']).shape,(26,))

    def test_training_rejects_validation(self):
        with self.assertRaises(ValueError):ml.train(cases('validation',1),'unused.npz')

    def test_long_and_variable_duty_stress(self):
        configs=stress_cases('development')
        self.assertEqual(len(configs),30)
        self.assertEqual({c['duty_fraction'] for c in configs},{.05,.85,1.})
        self.assertFalse({c['seed'] for c in configs}&{c['seed'] for c in cases('development',6)})
        c=configs[1];self.assertEqual(len(generate_case(c)),153600)

    def test_model_missing_and_numeric_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model.npz'
            with self.assertRaises(FileNotFoundError):ml.load_model(path)
            np.savez(path,schema=1,labels=np.array(ml.LABELS),mean=np.zeros(26),scale=np.zeros(26),
                w1=np.zeros((26,2)),b1=np.zeros(2),w2=np.zeros((2,10)),b2=np.zeros(10),
                rejection_score=.55,rejection_margin=.12)
            with self.assertRaises(ValueError):ml.load_model(path)
            header=io.BytesIO()
            np.lib.format.write_array_header_1_0(header,{'descr':'<f8','fortran_order':False,'shape':(10**12,)})
            with zipfile.ZipFile(path,'w') as archive:archive.writestr('w1.npy',header.getvalue())
            with self.assertRaises(ValueError):ml.load_model(path)
            np.savez(path,unrelated=np.zeros(2))
            with self.assertRaises(ValueError):ml.load_model(path)
            path.write_bytes(b'not an archive')
            with self.assertRaises(ValueError):ml.load_model(path)
            with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('oversized.npy',bytes(5_000_001))
            with self.assertRaises(ValueError):ml.load_model(path)

    def test_bounded_training_and_inference(self):
        # Tiny smoke experiment verifies actual training/inference operations;
        # performance claims use the separate disjoint benchmark, not this test.
        subset=cases('development',1)[::18]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'model.npz';report=ml.train(subset,path,max_seconds=20,epochs=3)
            self.assertEqual(report['examples'],len(subset))
            self.assertLess(report['model_bytes'],100000)
            c=subset[0];result=ml.classify(generate_case(c),c['sample_rate'],path)
            self.assertIn(result['modulation'],ml.LABELS)
            self.assertEqual(result['confidence_kind'],'UNCALIBRATED_MODEL_SCORE')
            self.assertEqual(result['protocol'],'UNKNOWN')

    def test_metric_rejection_and_noise_are_separate(self):
        path=Path(__file__).resolve().parents[1]/'tools'/'evaluate_engine.py'
        spec=importlib.util.spec_from_file_location('evaluate_engine_tests',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        rows=[{'engine':'dsp','family':'AM','target':'AM','predicted':'AM','signal_detected':True,'snr_db':12,'runtime_ms':1},
            {'engine':'dsp','family':'NOISE','target':'UNKNOWN','predicted':'UNKNOWN','signal_detected':False,'snr_db':12,'runtime_ms':1},
            {'engine':'dsp','family':'NFM','target':'NFM','predicted':'UNKNOWN','signal_detected':True,'snr_db':12,'runtime_ms':1}]
        m=module.summarize(rows,'dsp')
        self.assertAlmostEqual(m['accuracy'],2/3)
        self.assertAlmostEqual(m['rejection_rate'],2/3)
        self.assertEqual(m['signal_rejection_rate'],.5)
        self.assertEqual(m['accepted_prediction_accuracy'],1)
        self.assertEqual(m['noise_false_detection_rate'],0)


if __name__=='__main__':unittest.main()
