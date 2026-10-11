"""Channel-relative FM evidence with independently constructed counterexamples."""
import unittest
import numpy as np
from scipy.signal import butter,sosfilt
from rfatlas.recognition import wide_fm_channel
from rfatlas.characterization import characterize


class MultiplexChannelTests(unittest.TestCase):
    def test_two_halves_require_audio_and_pilot_structure(self):
        rate=1024000;n=262144;t=np.arange(n)/rate;rng=np.random.default_rng(180100)
        audio=sosfilt(butter(4,5000,fs=rate,output='sos'),rng.normal(size=n))
        audio/=max(abs(audio));pilot=.09*np.cos(2*np.pi*19000*t)
        noise=.09*(rng.normal(size=n)+1j*rng.normal(size=n))
        analysis={'features':{'occupied_bandwidth_hz':500000,'centroid_offset_hz':0}}
        def fm(m):return .35*np.exp(2j*np.pi*65000*np.cumsum(m)/rate)+noise
        good=wide_fm_channel(fm(.65*audio+pilot),rate,analysis)
        self.assertTrue(good['accepted']);self.assertEqual(len(good['halves']),2)
        transient=pilot.copy();transient[n//2:]=0
        self.assertFalse(wide_fm_channel(fm(.65*audio+transient),rate,analysis)['accepted'])
        self.assertFalse(wide_fm_channel(fm(pilot),rate,analysis)['accepted'])
        self.assertFalse(wide_fm_channel(noise,rate,analysis)['accepted'])
        symbols=np.repeat(rng.choice([-3,-1,1,3],n//128),128)*1800
        digital=.35*np.exp(2j*np.pi*np.cumsum(symbols)/rate)+noise
        self.assertFalse(wide_fm_channel(digital,rate,analysis)['accepted'])

    def test_protocol_interpretation_keeps_blind_result(self):
        analysis=dict(signal_class='DIGITAL',modulation='4FSK',confidence=.4)
        result=characterize(analysis,['RDS'])
        self.assertEqual((result['signal_type'],result['modulation']),('ANALOG','WFM'))
        self.assertEqual((result['blind_signal_type'],result['blind_modulation']),('DIGITAL','4FSK'))
        self.assertEqual(result['metadata_protocols'],['RDS'])
        self.assertEqual(analysis['signal_class'],'DIGITAL')
        digital=characterize(dict(signal_class='UNKNOWN',modulation='UNKNOWN'),['DMR'],verified_modulation='4FSK')
        self.assertEqual(digital['modulation'],'4FSK');self.assertEqual(digital['blind_modulation'],'UNKNOWN')
        self.assertEqual(characterize(analysis,['RDS','DMR'])['signal_type'],'UNKNOWN')


if __name__=='__main__':unittest.main()
