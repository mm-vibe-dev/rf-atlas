# Classification metrics reconciliation

RF Atlas 0.5.0 preserves the 0.4.0 DSP results as regression references and
keeps the corpus and generator unchanged. The reporting tool now explicitly
separates exact-label accuracy, selectivity and coverage. A bounded stability
repair follows the reproduced reference results below. Protocol decoding proceeds independently of modulation accuracy;
experimental ML and hybrid backends remain optional and are not promoted.

## Definitions

An accepted prediction has a label other than UNKNOWN. For N total cases, A
accepted predictions and C correctly accepted predictions:

| Metric | Definition |
| --- | --- |
| Coverage | A / N |
| Rejection rate | (N - A) / N |
| Selective accuracy | C / A; null if A is zero |
| Correct-and-accepted fraction | C / N |
| Legacy exact-label accuracy | All exact matches / N, including correct UNKNOWN labels for NOISE |
| Per-class recall | Correct predictions of a class / all cases whose target is that class |
| Per-class precision | Correct predictions of a class / all predictions of that class |
| Forced-choice accuracy | Null: the benchmark did not collect predictions forced to choose a known family |

UNKNOWN means abstention for known modulation families but is the correct
target for noise-only test cases. Consequently, legacy accuracy is higher than
correct-and-accepted fraction when noise is correctly rejected. Treating the
legacy number as forced-choice accuracy would overstate what was measured.
An abstention cannot be replaced afterward by an unrecorded second-choice
family. The previous `accepted_prediction_accuracy` field remains as a
compatibility alias for `selective_accuracy`. `signal_rejection_rate` excludes
noise and therefore differs from all-case rejection rate.

## Reproduced 0.4.0 DSP reference results

| Corpus | Cases | Exact-label accuracy | Selective accuracy | Rejection | Coverage | Correct and accepted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Preserved original | 720 | 81.11% | 92.75% | 23.33% | 76.67% | 71.11% |
| Preserved independent holdout | 360 | 64.17% | 77.38% | 30.00% | 70.00% | 54.17% |
| Independent holdout long/duty stress | 30 | 63.33% | 59.26% | 10.00% | 90.00% | 53.33% |

Original-suite correct/accepted counts are 512/552, with 168 abstentions.
Holdout counts are 195/252, with 108 abstentions. Stress counts are 16/27, with
three abstentions. Forced-choice accuracy is null for all three corpora.
The original 81.11% and holdout 64.17% baseline results are unchanged. Reporting
changes do not constitute a classifier improvement.

Noise-only false labels and false detections are zero in 72 original, 36
holdout and three stress cases. These are bounded synthetic counts. Confidence
intervals remain descriptive because related impairment cases are correlated.

### Holdout per-class results

Each holdout class has 36 cases. UNKNOWN has the noise-only target.

| Class | Recall | Precision |
| --- | ---: | ---: |
| AM | 72.22% | 57.78% |
| NFM | 72.22% | 44.83% |
| WFM | 55.56% | 80.00% |
| OOK | 83.33% | 96.77% |
| 2FSK | 50.00% | 100.00% |
| 4FSK | 36.11% | 100.00% |
| BPSK | 63.89% | 100.00% |
| QPSK | 55.56% | 100.00% |
| CW | 52.78% | 100.00% |
| UNKNOWN | 100.00% | 33.33% |

High precision for PSK/FSK/CW reflects conservative selection; it does not
remove substantial missed classifications. Holdout exact-label accuracy at
0 dB is 40.00%, at 12 dB 69.17%, and at 28 dB 83.33%.

## Diagnosed reference weaknesses

The stress corpus has only three cases per class: 1.2-second continuous,
5%-duty and 85%-duty waveforms at 28 dB. NFM and CW each retain 1/3 recall; both
duty-cycle variations are wrongly labeled AM. Numerous other 85%-duty digital
cases are also labeled AM. All 27 non-noise stress cases are accepted, so the
poor selective accuracy exposes overacceptance rather than successful
conservative rejection. This supplied a concrete defect mechanism for the
bounded repair below: quiet recording edges were being included in intrinsic
envelope statistics.

Weak PSK and independent pulse shaping remain difficult. At 0 dB holdout BPSK
recall is 5/12 and QPSK recall is 2/12; five BPSK and four QPSK cases abstain,
with the remaining misses incorrectly accepted as other families. The existing
holdout has now been consumed as a regression reference and cannot serve as
new unseen evidence. No corpus edits or model retraining are performed.

## Bounded stability repair

The existing burst-window function estimates floor power at the 20th
percentile and skips cropping above 80% detected duty. For 85%-duty signals,
that percentile already measures the signal. For lower duty, filtered onset
and ending blocks can still leave enough quiet samples to mimic AM.

The repair adds a stable-transmission quiet-edge crop before the existing burst
window. It requires at least 20 ms of quiet edge, power at least 13 dB below
the signal, no quiet interior blocks, and selected-envelope CV at most 0.18,
then discards a 4 ms transition guard. Symbol OOK and weak/noisy PSK fail the
stable-envelope gate. Existing multiple-burst behavior remains with the old
window selection. When cropping succeeds, CW bandwidth evidence is recomputed
from selected **unfiltered** IQ, so filter bandwidth cannot itself invent a
carrier. Selected sample positions continue to reference the original input.

Four fresh-seed tests cover low/high-duty CW and NFM, OOK/weak-PSK exclusions,
internal gaps, continuous signals and short edges. The nine existing recovery
tests also pass. The frozen final implementation was rerun on all preserved
corpora:

| Corpus | Exact-label accuracy before / after | Selective accuracy after | Coverage after | Correct and accepted after |
| --- | ---: | ---: | ---: | ---: |
| Original 720 | 81.11% / 84.72% | 97.46% | 76.67% | 74.72% |
| Existing holdout 360 | 64.17% / 64.17% | 77.38% | 70.00% | 54.17% |
| Long/duty stress 30 | 63.33% / 93.33% | 92.59% | 90.00% | 83.33% |

No per-class recall or precision decreased on any of these three corpora.
Stress CW and NFM each improved from 1/3 to 3/3 recall. Two stress errors remain:
85%-duty AM is labeled OOK and 85%-duty BPSK is labeled AM. Existing holdout weak
PSK remains unchanged. This is a focused edge-statistics repair, not general
blind waveform recovery or calibrated identification.

Final machine-readable reports are in `reports/0.5-stability/{original,holdout,stress}`
and `reports/0.5-stability/gate-comparison.json`. The code was frozen before
evaluation on fresh seeds with newly generated acquisition parameters.

### Fresh unseen comparison after the repair was frozen

The holdout generator's seed base was increased by 7,770,000 before constructing
360 new configurations. This regenerates sample rates, durations, symbol rates,
timing, phase, carrier and waveform samples, rather than only reseeding noise.
Both the frozen 0.4.0 implementation from commit `fcf6e06` and current DSP were
evaluated on identical new IQ. Neither implementation's output was used for
further tuning. All 360 predictions were identical between the implementations:

| Metric | Frozen 0.4.0 | Current DSP |
| --- | ---: | ---: |
| Exact-label accuracy | 66.39% | 66.39% |
| Selective accuracy | 82.86% | 82.86% |
| Coverage | 68.06% | 68.06% |
| Rejection rate | 31.94% | 31.94% |
| Correct-and-accepted fraction | 56.39% | 56.39% |
| Noise false labels / detections | 0/36 / 0/36 | 0/36 / 0/36 |

No class precision or recall regressed. This new evidence supports preserving
the focused repair without claiming broader generalization gains. Results,
configurations and source hashes are recorded in
`reports/0.5-stability/fresh-results.json`; the published reproduction
script is `tools/evaluate_stability.py`. Run it against a trusted local commit:

```text
python tools/evaluate_stability.py --reference-commit fcf6e06 --seed-offset 7770000 --output reports/0.5-stability
```

It executes reference source from the local Git history in a temporary package,
cleans that package afterward, and does not modify the corpus source. Forced-choice accuracy remains
unmeasured.

## Reproduction

Run from the repository with its installed core dependencies:

```text
python tools/evaluate_engine.py --original --backends dsp --output reports/0.5-metrics/original
python tools/evaluate_engine.py --split holdout --backends dsp --output reports/0.5-metrics/holdout
python tools/evaluate_engine.py --split holdout --stress --backends dsp --output reports/0.5-metrics/stress
python -m unittest discover -s tests -p test_classification_metrics.py -v
```

Each ignored report directory contains `results.json` with source hashes,
per-case outputs, metric definitions, class precision/recall and a confusion
matrix, plus a TSV confusion matrix. Runtime was measured with simultaneous
local jobs and Python allocation tracing; it is not a controlled throughput
comparison. Original and holdout corpus generation and split seeds remain
unchanged; the focused DSP edge-cropping repair is described separately above.
Three dedicated metric tests check known noise
abstentions, empty subsets and complete abstention; no forced-choice number is
invented.
