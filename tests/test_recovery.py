"""Recovery mathematics and conservative classifier contracts, generated IQ only."""
import unittest
import json
import numpy as np
from rfatlas.synthetic import generate
from rfatlas.recovery import compensate_carrier,phase_recovery,estimate_symbol_timing,matched_filter,recover_symbols,validate_iq,isolate,burst_window
from rfatlas.engine import classify,combine


class RecoveryTests(unittest.TestCase):
    def test_carrier_correction_and_original_preserved(self):
        x=generate('BPSK',snr_db=30,offset=19000);original=x.copy()
        corrected,estimate=phase_recovery(x,250000,2)
        self.assertAlmostEqual(estimate['offset_hz'],19000,delta=5)
        self.assertGreater(estimate['peak_fraction'],.7)
        self.assertGreater(abs(np.mean((corrected/abs(corrected))**2)),.9)
        np.testing.assert_array_equal(original,x)
        tone=generate('CW',snr_db=None,offset=-18000)
        np.testing.assert_allclose(compensate_carrier(tone,250000,-18000),.5,atol=1e-10)

    def test_symbol_grid_and_matched_filter(self):
        rng=np.random.default_rng(771)
        states=np.repeat(rng.integers(0,2,1000),40)
        estimate=estimate_symbol_timing(states,200000)
        self.assertAlmostEqual(estimate['symbol_rate_hz'],5000,delta=1)
        for width in (50,100,200):
            estimate=estimate_symbol_timing(np.repeat(rng.integers(0,2,200),width),200000)
            self.assertAlmostEqual(estimate['symbol_rate_hz'],200000/width,delta=1)
        self.assertIsNone(estimate_symbol_timing(np.ones(1000),200000)['symbol_rate_hz'])
        filtered=matched_filter(np.ones(4096,complex),40)
        np.testing.assert_allclose(filtered[40:-40],1)
        with self.assertRaises(ValueError):matched_filter(filtered,0)
        x=np.repeat(rng.choice([-1,1],500),40).astype(complex)
        symbols,operation=recover_symbols(x,200000,5000)
        self.assertGreater(len(symbols),400)
        self.assertGreater(np.mean(abs(symbols)>.9),.95)
        self.assertFalse(operation['tracking'])
        symbols,operation=recover_symbols(np.ones(10000,complex),200000,5000)
        self.assertEqual(operation['status'],'UNKNOWN')
        self.assertEqual(len(symbols),0)

    def test_clean_families(self):
        for family in ['AM','NFM','WFM','OOK','2FSK','4FSK','BPSK','QPSK','CW']:
            result=classify(generate(family,snr_db=30),250000)
            self.assertEqual(result['modulation'],family,family)
            self.assertEqual(result['protocol'],'UNKNOWN')
            self.assertEqual(result['protection'],'UNKNOWN')
            self.assertIn('preprocessing',result)
            json.dumps(result,allow_nan=False)

    def test_noise_clipping_and_quantization_gates(self):
        for seed in range(10):
            result=classify(generate('NOISE',seed=seed),250000)
            self.assertEqual(result['modulation'],'UNKNOWN')
            self.assertFalse(result['signal_detected'])
        self.assertEqual(classify(generate('4FSK',gain=2,clipping=True),250000)['modulation'],'UNKNOWN')
        result=classify(generate('QPSK',gain=.005),250000,{'format':'cu8'})
        self.assertEqual(result['modulation'],'UNKNOWN')
        t=np.arange(30000)/250000
        self.assertEqual(classify(.99*np.exp(2j*np.pi*5000*t),250000)['modulation'],'CW')

    def test_offsets_and_low_snr_are_measured(self):
        for family in ['OOK','2FSK','4FSK','BPSK','QPSK']:
            for offset in [-20000,20000]:
                result=classify(generate(family,snr_db=15,offset=offset),250000)
                self.assertIn(result['modulation'],[family,'UNKNOWN'])
        low=classify(generate('2FSK',snr_db=5),250000)
        self.assertEqual(low['confidence_kind'],'UNCALIBRATED_HEURISTIC_SUPPORT')

    def test_residual_carrier_fm_is_not_psk(self):
        t=np.arange(30000)/250000
        for audio,deviation in [(1000,250),(1000,500),(1000,1000),(5000,1000),(5000,2000)]:
            x=.4*np.exp(1j*deviation/audio*np.sin(2*np.pi*audio*t))
            self.assertNotIn(classify(x,250000)['modulation'],['BPSK','QPSK'])

    def test_late_burst_boundary(self):
        x=generate('4FSK',duration=.3,burst=True,snr_db=30)
        lo,hi,operation=burst_window(x,250000)
        self.assertTrue(operation['trimmed'])
        self.assertGreater(lo/250000,.18)
        self.assertLess(hi/250000,.27)

    def test_invalid_samples_rates_and_filters(self):
        for rate in [0,-1,float('nan'),True,'250000',250000+1j,1e308]:
            with self.assertRaises(ValueError):classify(np.ones(4096,complex),rate)
        for x in [np.ones(4096),np.ones(50,complex),np.full(4096,complex(np.nan,0)),np.ones((64,64),complex)]:
            with self.assertRaises(ValueError):classify(x,250000)
        with self.assertRaises(ValueError):classify(np.ones(4096,complex),250000,backend='other')
        with self.assertRaises(ValueError):isolate(np.ones(4096,complex),250000,0,300000)

    def test_optional_backend_fallback_and_hybrid_disagreement(self):
        x=generate('BPSK',snr_db=30)
        for backend in ['ml','hybrid']:
            result=classify(x,250000,{'model_path':'missing-model.npz'},backend)
            self.assertEqual(result['backend'],'dsp')
            self.assertTrue(any('fallback' in w for w in result['quality']['warnings']))
        first=classify(x,250000);second=dict(first,modulation='QPSK')
        result=combine(first,second)
        self.assertEqual(result['modulation'],'UNKNOWN')
        self.assertGreaterEqual(len(result['candidates']),2)
        self.assertEqual(first['modulation'],'BPSK')


if __name__=='__main__':unittest.main()
