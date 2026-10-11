"""Synthetic-only discovery controls; never assert fingerprints identify emitters."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from rfatlas.fingerprints import similarity,VERSION
from rfatlas.detection import detect,detect_survey
from rfatlas.rds import RDSDecoder
from rfatlas.decoders import DecoderInput,run_decoder


def benchmark():
    rng=np.random.default_rng(12082);begin=time.perf_counter()
    feature=dict(version=VERSION,source='SPECTRAL_OBSERVATION',resolution_hz=1000,
        center_hz=100000000,bandwidth_hz=10000,snr_db=20,gain_db=15.7)
    positive=negative=collision=0
    for index in range(500):
        shape=rng.uniform(-.5,.5,32);a=dict(feature,shape=shape.tolist())
        positive+=similarity(a,dict(feature,shape=(shape+rng.normal(0,.02,32)).tolist())) is not None
        negative+=similarity(a,dict(feature,shape=rng.uniform(-.5,.5,32).tolist())) is not None
        collision+=similarity(a,dict(a)) is not None
    x=np.arange(2000);broad=weak=noise=0
    for index in range(100):
        power=-70+rng.normal(0,.3,2000)
        noise+=bool(detect_survey(0,1000,power))
        broad+=bool(detect_survey(0,1000,power+25*np.exp(-((x-800)/150)**2)))
        weak+=bool(detect_survey(0,1000,power+5*np.exp(-((x-800)/150)**2)))
    false_decodes=0
    for index in range(30):
        iq=rng.normal(size=160000)+1j*rng.normal(size=160000)
        false_decodes+=run_decoder(RDSDecoder(),DecoderInput(iq,228000,98000000))['protocol_verified']
    return dict(source='SYNTHETIC_ONLY',seed=12082,seconds=time.perf_counter()-begin,
        fingerprint=dict(perturbed_same_characteristics_matches=positive,distinct_random_shapes_false_matches=negative,
            indistinguishable_different_emitters_collisions=collision,pairs_per_condition=500,
            limitation='Random-shape negatives are easy controls, not a field false-match estimate. Indistinguishable emitters collide 100%; no physical identity is assigned.'),
        detector=dict(noise_false_candidates=noise,broad_candidates=broad,weak_broad_candidates=weak,cases_per_condition=100,
            limitation='5 dB broad energy intentionally remains below 9 dB prominence; weak and between-visit bursts can be missed.'),
        rds=dict(noise_false_protocols=false_decodes,independent_noise_cases=30))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    with args.output.open('x',encoding='utf-8') as destination:json.dump(benchmark(),destination,indent=2)
