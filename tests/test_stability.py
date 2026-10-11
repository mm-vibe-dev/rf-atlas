"""Fresh synthetic seed checks for slow-gating versus intrinsic modulation."""
import unittest
import numpy as np

from rfatlas.engine import classify
from rfatlas.recovery import trim_quiet_edges


class StabilityTests(unittest.TestCase):
    def waveform(self, family, duty, noise=.01):
        rate=128000;n=round(rate*1.2);t=np.arange(n)/rate
        rng=np.random.default_rng(918273)
        if family=='CW':x=np.ones(n,dtype=complex)
        elif family=='NFM':x=np.exp(1j*2.5*np.sin(2*np.pi*800*t))
        elif family=='OOK':
            symbols=rng.integers(0,2,n//64+1)
            x=symbols[np.arange(n)//64].astype(complex)
        else:
            symbols=rng.integers(0,2,n//64+1)
            x=np.exp(1j*np.pi*symbols[np.arange(n)//64])
        x*=np.exp(2j*np.pi*13000*t)
        begin,end=(.75,.80) if duty=='low' else (.10,.95)
        x*=((t>=begin*1.2)&(t<end*1.2))
        return .3*(x+noise*(rng.normal(size=n)+1j*rng.normal(size=n))),rate

    def test_cw_and_nfm_slow_gating_does_not_become_am(self):
        for family in ['CW','NFM']:
            for duty in ['low','high']:
                with self.subTest(family=family,duty=duty):
                    iq,rate=self.waveform(family,duty)
                    result=classify(iq,rate)
                    self.assertEqual(result['modulation'],family)
                    self.assertTrue(any(p['operation']=='stable transmission quiet-edge crop' and p['trimmed'] for p in result['preprocessing']))
                    self.assertGreater(result['recovery']['selected_start_sample'],0)
                    self.assertLess(result['recovery']['selected_stop_sample'],len(iq))

    def test_symbol_ook_and_weak_psk_edges_are_not_cropped(self):
        for family,noise in [('OOK',.01),('BPSK',.7)]:
            iq,rate=self.waveform(family,'high',noise)
            lo,hi,operation=trim_quiet_edges(iq,rate)
            self.assertFalse(operation['trimmed'])
            self.assertEqual((lo,hi),(0,len(iq)))

    def test_internal_quiet_gaps_preserve_burst_window_behavior(self):
        iq,rate=self.waveform('CW','high')
        iq[round(.4*rate):round(.6*rate)]=0
        lo,hi,operation=trim_quiet_edges(iq,rate)
        self.assertFalse(operation['trimmed'])
        self.assertEqual((lo,hi),(0,len(iq)))

    def test_continuous_carrier_and_short_edges_are_preserved(self):
        rate=128000;n=128000
        iq=np.exp(2j*np.pi*17000*np.arange(n)/rate)
        for quiet in [0,round(rate*.005)]:
            x=iq.copy();x[:quiet]=0;x[n-quiet:]=0
            lo,hi,operation=trim_quiet_edges(x,rate)
            self.assertFalse(operation['trimmed'])
            self.assertEqual((lo,hi),(0,n))


if __name__=='__main__':unittest.main()
