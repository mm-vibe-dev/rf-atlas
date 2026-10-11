# Offline analog characterization and classifier preservation

`rfatlas.analog.analyze_analog` adds independently authored, MIT-licensed
offline diagnostics for AM, NFM, WFM and CW. It accepts an explicit modulation
hypothesis and 4096 through one million finite complex samples. It performs
channel filtering and transient demodulation, then returns aggregate metrics
only. It creates no audio files, playback, recordings, transcription or message
content. The caller may supply a known synthetic test tone as a test oracle.

The evidence levels remain distinct:

* `modulation_hypothesis` is supplied by the caller, not independently verified.
* `demodulated_test_signal=true` means the explicit synthetic expected tone
  was recovered near its frequency with at least 70% of diagnostic-band PSD
  power. Without a test oracle the field is null.
* `protocol_verified` is always false and `protocol` remains UNKNOWN. Analog
  tone recovery, occupied bandwidth and tuning frequency establish no protocol.

AM uses the DC-removed complex magnitude. FM uses the argument of adjacent
sample conjugate products, scaled to instantaneous frequency, with median
carrier removed. CW uses an explicit local beat oscillator. Envelope variation,
frequency deviation and carrier coherence provide different signal-processing
evidence. The formulas agree with the primary
[GNU Radio quadrature-demodulation documentation](https://wiki.gnuradio.org/index.php/Quadrature_Demod)
and [AM documentation](https://wiki.gnuradio.org/index.php?title=AM_Demod).
No GNU Radio source code is incorporated and GNU Radio is not a dependency.

Default channel widths are 12 kHz AM, 16 kHz NFM, 200 kHz WFM and 3 kHz CW.
Widths must be below sample rate; WFM therefore rejects a 48 ksample/s input.
The diagnostic audio low-pass is 5 kHz, or 15 kHz for WFM. Filtering discards
256 samples at each edge. Amplitudes are normalized and uncalibrated. There is
no FM stereo/pilot/RDS verification, broadcast de-emphasis, Morse decoding,
automatic carrier tracking, fade compensation or speech recognition. FM noise
can produce discriminator output; output energy alone is not successful decode.

Five deterministic test methods verify all four synthetic modes with a 1.7 kHz
carrier offset, expected tone recovery, distinct envelope/frequency metrics,
input immutability, JSON-finite output, wrong-tone rejection, silence/noise
rejection, unsupported modes and bounded/invalid inputs. All pass on Windows.

## Preserved classifier regression

The original classifier, recovery, synthetic generators and benchmark tool are
unchanged. The existing benchmark commands were rerun with DSP only. Detailed
machine-readable outputs remain under the ignored `reports/0.6-regression/`
directory. No benchmark cases, labels or thresholds were changed.

| Corpus | Cases | 0.5 accuracy | 0.6 accuracy | Selective accuracy | Coverage | Correct and accepted / all |
|---|---:|---:|---:|---:|---:|---:|
| Original | 720 | 84.72% | 84.72% | 97.46% | 76.67% | 74.72% |
| Independent holdout | 360 | 64.17% | 64.17% | 77.38% | 70.00% | 54.17% |
| Long / 5% / 85% duty stress | 30 | 93.33% | 93.33% | 92.59% | 90.00% | 83.33% |

Accuracy here is exact-label accuracy with UNKNOWN counted correct for noise
and wrong for known modulation families. Selective accuracy counts only accepted
predictions; coverage is their fraction of all cases. Forced-choice accuracy is
not measured because the classifier can abstain and no forced labels were
collected. These values cannot be substituted for one another.

NFM and CW are each correct on all three long/duty stress cases. This small
stress suite does not remove holdout weaknesses: NFM recall is 26/36 with
precision 26/58; CW recall is 19/36 with precision 19/19. Weak phase-modulated
signals still expose frequency-only confusions and rejection: at 0 dB holdout
BPSK is correct on 5/12 and QPSK on 2/12.

A supplemental fixed-classifier check used 72 BPSK/QPSK cases across all six
existing impairment profiles and three SNRs, with each seed offset by 440000
from the original holdout namespace. No tuning or training followed. Fresh
BPSK correct counts at 0/12/28 dB were 6/12, 8/12 and 9/12; QPSK counts were
2/12, 8/12 and 10/12. These results support retaining conservative UNKNOWN
outputs and protocol-driven verification rather than promoting weak-PSK
hypotheses. DSP remains default; optional ML remains experimental and hybrid
is not promoted.

The source hashes recorded by all three preserved benchmark runs agree:

* `engine.py`: `220723ef7d9ff58a411f752b8337cd4f59d940458b98a202fa896612f52b5d40`
* `recovery.py`: `0372bf7cb59ee7a6318e2defeb5bf5a0bc922dc1840abb516cf427cacd30dc93`

Elapsed times from simultaneous runs are not controlled performance comparisons.
Related impairment cases are not independent statistical samples.
