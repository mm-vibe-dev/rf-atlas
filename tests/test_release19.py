"""Bounded inference, independent rectangular symbol fixtures and privacy."""
import hashlib
import binascii
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from rfatlas import ai
from rfatlas.demodulation import demodulate
from rfatlas.entities import private_result
from rfatlas.fec import decode_slot,decode_bptc
from rfatlas.messages import summarize_messages


class Release19Tests(unittest.TestCase):
    def model(self,root):
        path=Path(root)/'model.json'
        model=dict(schema=1,algorithm=ai.VERSION,families=ai.FAMILIES,modulations=ai.MODULATIONS,
            mean=[0]*28,scale=[1]*28,family_weights=np.zeros((29,5)).tolist(),
            modulation_weights=np.zeros((29,14)).tolist(),reject_score=.65,reject_margin=.15,score_temperature=.2)
        model['family_weights'][-1][1]=1;model['modulation_weights'][-1][7]=1
        raw=json.dumps(model).encode();path.write_bytes(raw);path.with_suffix('.sha256').write_text(hashlib.sha256(raw).hexdigest())
        return path

    def test_inference_contract_checksum_quality_and_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.model(root);analysis=dict(features=dict(samples=4096),signal_class='ANALOG',adc_amplitude_p95_counts=30)
            with patch.object(ai,'vector',return_value=np.zeros(28)):
                result=ai.infer(analysis,dict(sample_rate=48000,sha256='source'),path)
                self.assertEqual(result['predicted_family'],'DIGITAL');self.assertEqual(result['predicted_modulation'],'2FSK')
                self.assertTrue(result['disagreement']);self.assertFalse(result['protocol_verified'])
                analysis['adc_amplitude_p95_counts']=1
                self.assertEqual(ai.infer(analysis,dict(sample_rate=48000),path)['predicted_family'],'UNKNOWN')
                analysis['adc_amplitude_p95_counts']=30;analysis['structural_features']={'budget_exhausted':True}
                self.assertEqual(ai.infer(analysis,dict(sample_rate=48000),path)['predicted_family'],'UNKNOWN')
                path.write_bytes(path.read_bytes()+b' ')
                with self.assertRaises(ValueError):ai.load_model(path)
                future=ai.submit(analysis,dict(sample_rate=48000),path)
                self.assertEqual(future.result(timeout=2)['status'],'UNAVAILABLE')

    def test_malformed_numeric_and_no_unbounded_queue(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.model(root);model=json.loads(path.read_text());model['scale'][0]=0
            raw=json.dumps(model).encode();path.write_bytes(raw);path.with_suffix('.sha256').write_text(hashlib.sha256(raw).hexdigest())
            with self.assertRaises(ValueError):ai.load_model(path)
        self.assertTrue(ai._slot.acquire(blocking=False))
        try:self.assertIsNone(ai.submit({}, {}, 'missing'))
        finally:ai._slot.release()

    def test_independent_rectangular_symbols_offset_noise_gain_timing(self):
        # Natural phase/frequency labels, independently synthesized from states.
        for mode,order in [('OOK',2),('2FSK',2),('4FSK',4),('BPSK',2),('QPSK',4)]:
            for rate,sps in [(48000,40),(96000,80)]:
                rng=np.random.default_rng(919);states=rng.integers(0,order,240);rail=np.repeat(states,sps)
                if mode=='OOK':wave=rail.astype(complex)
                elif 'FSK' in mode:wave=np.exp(2j*np.pi*np.cumsum((rail*2-(order-1))*900)/rate)
                else:wave=np.exp(2j*np.pi*rail/order)
                wave=np.pad(wave,(sps//3,0),mode='edge');t=np.arange(len(wave))/rate
                wave=.27*wave*np.exp(2j*np.pi*170*t)+.0005*(rng.normal(size=len(wave))+1j*rng.normal(size=len(wave)))
                result=demodulate(wave,rate,mode,1200,frequency_offset_hz=170)
                recovered=np.asarray(result['symbols']);best=1.
                # Carrier ambiguity is unresolved; score a fixed global rotation only.
                for lag in range(-2,3):
                    for rotation in range(order if 'PSK' in mode else 1):
                        lo=max(3,-lag);hi=min(len(recovered)-3,len(states)-lag)
                        best=min(best,float(np.mean((recovered[lo:hi]+rotation)%order!=states[lo+lag:hi+lag])))
                self.assertLess(best,.02,(mode,rate,best));self.assertFalse(result['protocol_verified'])
                self.assertLessEqual(len(result['candidate_bits']),4096)

    def test_demodulation_invalid_and_privacy(self):
        with self.assertRaises(ValueError):demodulate(np.zeros(4096,complex),48000,'GMSK',1200)
        with self.assertRaises(ValueError):demodulate(np.zeros(4096,complex),48000,'QPSK',20000)
        source=dict(demodulation_candidates=[dict(symbols=[1],candidate_bits=[1],relative_reliability=[.1])],messages=[dict(fields={'source':'PRIVATE'},raw_hex='AA')])
        private=private_result(source)
        self.assertNotIn('candidate_bits',private['demodulation_candidates'][0]);self.assertEqual(private['messages'][0]['fields'],{})
        self.assertNotIn('raw_hex',private['messages'][0]);self.assertIn('raw_hex',source['messages'][0])

    def test_fec_limits_and_crc_separation(self):
        from rfatlas.dmr import golay_word
        bits=np.array([int(v) for v in f'{golay_word(0x73):020b}']);bits[[0,5,9]]^=1
        decoded=decode_slot(bits);self.assertEqual(decoded['fields']['color_code'],7)
        self.assertEqual(decoded['fields']['slot_bit_corrections'],3);self.assertFalse(decoded['crc_checked'])
        with self.assertRaises(ValueError):decode_bptc([0]*195)
        with self.assertRaises(ValueError):decode_slot([2]*20)
        self.assertFalse(decode_bptc([0]*196)['crc_checked'])
        # Independent systematic parity equations, not the decoder syndrome table.
        data=bytes.fromhex('BE100300000000000000')
        data+=(binascii.crc_hqx(data,0)^0xffff^0xa5a5).to_bytes(2,'big')
        payload=np.unpackbits(np.frombuffer(data,dtype=np.uint8));block=np.zeros((13,15),np.uint8)
        block[0,3:11]=payload[:8];block[1:9,:11]=payload[8:].reshape(8,11)
        rows=[[0,1,2,3,5,7,8],[1,2,3,4,6,8,9],[2,3,4,5,7,9,10],[0,1,2,4,6,7,10]]
        columns=[[0,1,3,5,6],[0,1,2,4,6,7],[0,1,2,3,5,7,8],[0,2,4,5,8]]
        for row in block[:9]:
            for k,indexes in enumerate(rows):row[11+k]=np.bitwise_xor.reduce(row[indexes])
        for column in block.T:
            for k,indexes in enumerate(columns):column[9+k]=np.bitwise_xor.reduce(column[indexes])
        encoded=np.zeros(196,np.uint8);encoded[np.arange(196)*181%196]=np.r_[0,block.ravel()]
        for damaged in ([],[17],[17,51,103]):
            bits=encoded.copy();bits[damaged]^=1;decoded=decode_bptc(bits)
            self.assertEqual(decoded['payload_hex'],data.hex());self.assertEqual(decoded['status'],'SYNDROMES_VALID')
        damaged=encoded.copy();damaged[np.random.default_rng(19).choice(196,60,replace=False)]^=1
        decoded=decode_bptc(damaged)
        self.assertTrue(decoded['status']=='UNCORRECTABLE' or decoded['payload_hex']!=data.hex())

    def test_messages_require_joint_validated_frame(self):
        frame=dict(status='VERIFIED',sync_valid=True,checksum_valid=True,parsed_fields={'opcode':62},raw_hex='AA',frame_length=1)
        result=dict(source_sha256='source',attempts=[dict(protocol_verified=True,protocol='DMR',decoder={'name':'native-dmr-bs-csbk','version':'1'},frames=[frame])])
        self.assertEqual(len(summarize_messages(result)),1)
        frame['checksum_valid']=False;self.assertEqual(summarize_messages(result),[])

    def test_optional_pipeline_preserves_dsp_and_verified_semantics(self):
        from rfatlas.identification import identify_samples,DecoderRegistry
        with tempfile.TemporaryDirectory() as root:
            registry=DecoderRegistry();registry.ai_model=self.model(root)
            rng=np.random.default_rng(19);iq=.1*(rng.normal(size=16000)+1j*rng.normal(size=16000))
            meta=dict(sample_rate=48000,center_hz=100000000,sha256=hashlib.sha256(iq.tobytes()).hexdigest(),sample_count=len(iq))
            result=identify_samples(iq,meta,registry)
            self.assertIn(result['ml_analysis']['status'],('COMPLETE','DEFERRED'))
            self.assertFalse(result['protocol_verified']);self.assertEqual(result['protocol'],'UNKNOWN')
            self.assertIn('signal_class',result['analysis'])

    def test_sanitized_doctor_skips_hardware(self):
        from rfatlas.config import Config
        from rfatlas.doctor import diagnose
        with tempfile.TemporaryDirectory() as root:
            report=diagnose(Config(Path(root)),receiver=False)
            self.assertEqual(report['receiver']['status'],'NOT_TESTED')
            self.assertNotIn(root,json.dumps(report));self.assertEqual(report['configuration']['status'],'PASS')


if __name__=='__main__':unittest.main()
