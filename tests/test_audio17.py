import unittest
import numpy as np
from scipy import signal
from rfatlas.audio_dsp import StreamingResampler


class PolyphaseTests(unittest.TestCase):
    def test_arbitrary_boundaries_match_independent_causal_convolution(self):
        rng=np.random.default_rng(8182);x=rng.normal(size=5432)
        for up,down in ((24,25),(3,16),(1,5),(2,3),(1,1)):
            with self.subTest(up=up,down=down):
                h=np.ones(1) if up==down else signal.firwin(12*max(up,down)+1,1/max(up,down))*up
                expanded=np.zeros(len(x)*up);expanded[::up]=x
                truth=signal.lfilter(h,[1],expanded)[::down]
                converter=StreamingResampler(h,up,down);blocks=[];start=0
                while start<len(x):
                    size=int(rng.integers(1,411));blocks.append(converter.process(x[start:start+size]));start+=size
                actual=np.concatenate(blocks)
                np.testing.assert_allclose(actual,truth,atol=2e-14,rtol=2e-14)
                self.assertEqual(len(converter.process([])),0)
                self.assertLessEqual(len(converter.filters),down)

    def test_impulse_and_dc_gain_preserve_causal_delay_without_edge_reset(self):
        h=signal.firwin(301,1/25)*24
        converter=StreamingResampler(h,24,25)
        y=np.concatenate([converter.process(np.ones(5000)) for _ in range(3)])
        self.assertEqual(len(y),14400)
        self.assertLess(abs(np.mean(y[100:])-1),.001)
        self.assertLess(np.max(abs(y[100:])),1.01)

    def test_malformed_audio_rejected_before_filter_allocation(self):
        converter=StreamingResampler([1],1,1)
        for x in ([float('nan')],np.zeros((2,2))):
            with self.assertRaises(ValueError):converter.process(x)
