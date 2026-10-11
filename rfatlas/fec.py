"""Selected DMR error correction with explicit limits; CRC is a separate gate."""
import numpy as np


def _bits(value, size):
    a=np.asarray(value)
    if a.shape!=(size,) or not np.isin(a,[0,1]).all():raise ValueError('Exact binary codeword required')
    return a.astype(np.uint8)


def decode_slot(bits):
    from .dmr import slot_type
    result=slot_type(_bits(bits,20))
    return dict(method='DMR_GOLAY_20_8',status='CORRECTED_OR_VALID' if result else 'UNCORRECTABLE',
        maximum_correctable_bits=3,fields=result,crc_checked=False,
        limitations='Unique nearest codeword within three bits; protocol CRC remains required.')


def decode_bptc(bits):
    from .dmr import bptc_decode
    payload,valid,flips=bptc_decode(_bits(bits,196))
    return dict(method='DMR_BPTC_196_96',status='SYNDROMES_VALID' if valid else 'UNCORRECTABLE',
        payload_hex=payload.hex() if valid else None,correction_operations=flips,
        maximum_iterations=5,crc_checked=False,
        limitations='Correction operations are not the true channel error count; a valid CSBK CRC is still mandatory.')
