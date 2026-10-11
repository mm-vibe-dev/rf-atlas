"""Independent structural evidence and adverse-condition regression fixtures."""
import threading
import unittest
import numpy as np
from scipy import signal
from rfatlas.advanced_dsp import cyclic_prefix,slow_keying
from rfatlas.recognition import classify


def multicarrier(seed=117,fft=128,cp=16,snr=12,offset=17000,rate=250000,prefix=True):
    rng=np.random.default_rng(seed)
    bins=np.exp(1j*np.pi/2*rng.integers(0,4,(420,fft)))
    blocks=np.fft.ifft(bins,axis=1)*np.sqrt(fft)
    x=np.c_[blocks[:,-cp:],blocks].ravel() if prefix else blocks.ravel()
    x=x[13:];t=np.arange(len(x))/rate
    x=.13*x*np.exp(2j*np.pi*offset*t)
    noise=.13/np.sqrt(2*10**(snr/10))*(rng.normal(size=len(x))+1j*rng.normal(size=len(x)))
    return x+noise


class StructureTests(unittest.TestCase):
    def test_shaped_phase_alphabets_at_offset_and_no_am_qam_promotion(self):
        rng=np.random.default_rng(813);rate=250000;n=100000;t=np.arange(n)/rate
        for order,label in ((2,'BPSK'),(4,'QPSK'),(8,'8PSK')):
            with self.subTest(order=order):
                x=np.repeat(np.exp(2j*np.pi*rng.integers(0,order,1250)/order),80)
                x=signal.sosfilt(signal.butter(4,3750,fs=rate,output='sos'),x)
                x=.17*x*np.exp(2j*np.pi*23000*t)+.001*(rng.normal(size=n)+1j*rng.normal(size=n))
                a=classify(x,rate)
                self.assertEqual(a['modulation'],label);self.assertEqual(a['signal_class'],'DIGITAL')
                self.assertEqual(a['protocol'],'UNKNOWN')
        am=.2*(1+.5*np.cos(2*np.pi*900*t))*np.exp(2j*np.pi*23000*t)
        self.assertNotEqual(classify(am,rate)['signal_class'],'DIGITAL')
        # Equal sidebands (suppressed-carrier analog) have a binary phase
        # alphabet too. Do not mistake that statistic alone for BPSK.
        for audio in (np.cos(2*np.pi*900*t),signal.sosfilt(signal.butter(4,2200,fs=rate,output='sos'),rng.normal(size=n))):
            a=classify(.2*audio*np.exp(2j*np.pi*23000*t),rate)
            self.assertIsNone((a['structural_features']['phase_alphabet'] or {}).get('candidate'))

    def test_cp_with_offset_noise_and_arbitrary_capture_phase(self):
        for fft,cp,rate,offset in ((64,16,160000,-23000),(128,16,250000,17000),(256,32,1024000,55000)):
            with self.subTest(fft=fft):
                a=classify(multicarrier(fft=fft,cp=cp,rate=rate,offset=offset),rate)
                self.assertEqual((a['signal_class'],a['modulation']),('DIGITAL','OFDM_LIKE'))
                b=a['structural_features']['cyclic_prefix']['best']
                self.assertEqual(b['fft_samples'],fft);self.assertEqual(b['prefix_samples'],cp)
                self.assertEqual(a['protocol'],'UNKNOWN');self.assertEqual(a['protection'],'UNKNOWN')
                self.assertFalse(a['family_decision']['verified'])

    def test_noise_tone_periodic_interference_and_no_cp_do_not_promote(self):
        rng=np.random.default_rng(491);n=65536;t=np.arange(n)/250000
        fixtures=[rng.normal(size=n)+1j*rng.normal(size=n),
            .2*np.exp(2j*np.pi*19000*t),
            .2*np.exp(2j*np.pi*9000*t)+.15*np.exp(2j*np.pi*17000*t),
            np.resize(rng.normal(size=80)+1j*rng.normal(size=80),n),
            signal.sosfilt(signal.butter(4,.25,output='sos'),rng.normal(size=n)+1j*rng.normal(size=n)),
            multicarrier(prefix=False)]
        for i,x in enumerate(fixtures):
            with self.subTest(i=i):
                self.assertFalse(cyclic_prefix(x,250000)['best']['accepted'])

    def test_clipping_and_cu8_resolution_gate_structural_promotion(self):
        x=multicarrier()
        for data,meta in ((x,{'analysis_quality':{'adc_endpoint_fraction':.02}}),(x*.01,{'format':'cu8'})):
            a=classify(data,250000,meta)
            self.assertNotEqual(a['modulation'],'OFDM_LIKE')

    def test_slow_keying_preserves_ambiguity_and_does_not_decode_content(self):
        rate=250000;t=np.arange(rate)/rate
        x=.2*(np.sin(2*np.pi*7*t)>0)*np.exp(2j*np.pi*12000*t)
        a=classify(x,rate)
        self.assertTrue(a['structural_features']['keying']['accepted'])
        self.assertEqual(a['modulation'],'KEYED_CARRIER');self.assertEqual(a['signal_class'],'CARRIER')
        self.assertIn('OOK',' '.join(a['evidence']));self.assertEqual(a['protocol'],'UNKNOWN')
        self.assertFalse(slow_keying(.2*np.exp(2j*np.pi*12000*t),rate)['accepted'])

    def test_cancellation_fast_level_and_budget_validation(self):
        x=multicarrier();event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):cyclic_prefix(x,250000,cancel_event=event)
        self.assertIsNone(classify(x,250000,analysis_level='fast')['structural_features']['cyclic_prefix'])
        for budget in (True,float('nan'),0,3):
            with self.subTest(budget=budget),self.assertRaises(ValueError):cyclic_prefix(x,250000,max_seconds=budget)

    def test_structural_evidence_requires_repetition_in_both_halves(self):
        x=multicarrier();rng=np.random.default_rng(429)
        x[len(x)//2:]=.13*(rng.normal(size=len(x)-len(x)//2)+1j*rng.normal(size=len(x)-len(x)//2))
        self.assertFalse(cyclic_prefix(x,250000)['best']['accepted'])
