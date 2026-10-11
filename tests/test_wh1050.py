"""Original weather fixtures, never captured sensor identities or RF content."""
import json
import os
import unittest
from unittest.mock import patch
import numpy as np
from rfatlas.decoders import DecoderInput,run_decoder
from rfatlas.external_decoders import RTL433WH1050Decoder


def weather_iq(corrupt=False,false_sync=False):
    # Invented id 0x37, 21 C, 55% humidity, wind and cumulative rain.
    payload=bytes([0x53,0x72,0x62,55,3,5,0,20])
    dividend=int.from_bytes(payload,'big')<<8
    while dividend.bit_length()>=9:dividend^=0x131<<(dividend.bit_length()-9)
    frame=bytes([0 if false_sync else 255])+payload+bytes([dividend^int(corrupt)])
    parts=[np.zeros(3000)]
    for bit in np.unpackbits(np.frombuffer(frame,dtype=np.uint8)):
        parts.extend([np.full(136 if bit else 381,.75),np.zeros(250)])
    parts.append(np.zeros(3000));envelope=np.concatenate(parts)
    x=envelope*np.exp(2j*np.pi*10000*np.arange(len(envelope))/250000)
    rng=np.random.default_rng(18069);x+=.003*(rng.normal(size=len(x))+1j*rng.normal(size=len(x)))
    return DecoderInput(x,250000,433920000)


class Weather1050Tests(unittest.TestCase):
    def test_typed_weather_contract_rejects_other_models_clock_and_bad_fields(self):
        valid=dict(model='Fineoffset-WH1050',mic='CRC',msg_type=5,id=55,battery_ok=1,
                   temperature_C=21,humidity=55,wind_avg_km_h=3.672,wind_max_km_h=6.12,rain_mm=6)
        cases=[(valid,True),(dict(valid,model='TFA-303151'),False),(dict(valid,msg_type=6),False),
               (dict(valid,mic='CHECKSUM'),False),(dict(valid,id=True),False),
               (dict(valid,temperature_C=float('nan')),False),(dict(valid,humidity=101),False),
               ({k:v for k,v in valid.items() if k!='rain_mm'},False)]
        with patch('rfatlas.external_decoders.shutil.which',return_value='trusted'):
            for event,expected in cases:
                with self.subTest(event=event):
                    d=RTL433WH1050Decoder(runner=lambda *args:(json.dumps(event),''))
                    self.assertEqual(run_decoder(d,weather_iq())['protocol_verified'],expected)

    @unittest.skipUnless(os.environ.get('RFATLAS_RTL433'),'Optional rtl_433 integration binary not configured')
    def test_real_binary_positive_and_integrity_negatives(self):
        d=RTL433WH1050Decoder(os.environ['RFATLAS_RTL433'])
        self.assertTrue(run_decoder(d,weather_iq())['protocol_verified'])
        self.assertFalse(run_decoder(d,weather_iq(corrupt=True))['protocol_verified'])
        self.assertFalse(run_decoder(d,weather_iq(false_sync=True))['protocol_verified'])


if __name__=='__main__':unittest.main()
