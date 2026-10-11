# Reproducible independent classification benchmark

The preserved `tools/evaluate_analysis.py` generates the original 720 cases.
The new `tools/evaluate_engine.py --original` repeats those same waveforms and
compares the committed Phase 1.3 classifier and activity window with the new
engine. Its baseline is `f8502e3`, including its activity selection; comparing
only its feature function against whole captures would be a different baseline.

## Independent corpus

`rfatlas.corpus` does not import the original generator. It integrates continuous
frequency trajectories for multitone FM and smooth discrete FSK; fractional
symbol positions and Hann pulse shaping generate OOK and PSK. This is a separate
MIT-authored generator, not a claim of TorchSig equivalence or independently
measured radio ground truth. The same project still designs both classifier and
generator, so shared conceptual assumptions remain a source of bias.

Each split contains 360 cases: ten families, three SNR levels (0, 12, 28 dB), six
profiles and two independent seeds. Profiles cover ordinary signals, offsets
with carrier drift, unequal bursts, low gain, clipping, and 4/8-bit quantization.
Cases also vary sample rate (128/256/384 kHz), duration, modulation depth,
frequency deviation, audio tones, symbol rate, initial phase and symbol timing.
SNR is referenced to continuous waveform power before burst gating. FM class
definitions follow the generator's deviation settings; hardware service labels
and protocol identity are never supplied to the classifier.

Development, validation and final holdout use disjoint seed namespaces. The
optional MLP trains exclusively on development. Validation evaluates candidate
generalization. Holdout is run explicitly only after implementation and ensemble
policy freeze; its output must not be used to retune a candidate being reported
as untouched. Reusing that holdout in later releases requires acknowledging it
as a regression set and creating a fresh final holdout.

The suite is bounded and streams IQ one case at a time. It is not an exhaustive
factorial RF channel model: multipath, interference, adjacent transmitters,
oscillator phase noise, frontend nonlinearities and real-world labels are not
established. Multiple cases share families and impairment designs, so reported
Wilson 95% intervals are descriptive binomial intervals, not independence claims.

The separate `--stress` suite adds 30 cases per split: all ten families at
1.2 seconds and 128 kHz, with continuous transmission, 5% late-burst duty and
85% duty. Its seed namespace is disjoint from the main corpus. These cases do
not alter the 360-case development/training generator, and stress results are
reported separately rather than silently changing the main denominator.

## Metrics and reproducibility

Reports contain all case configurations and seeds, per-case outcomes, baseline
commit, per-class accuracy/precision/recall/F1, macro-F1, count confusion matrices,
overall and signal-only rejection, accuracy among accepted non-UNKNOWN labels,
noise false labeling and false detection, accuracy by SNR, mean/p95 wall time,
and Python-traced peak allocated memory. Noise truth maps to UNKNOWN; rejection
includes correctly rejected noise, so signal-only rejection is also reported.
Missing precision denominators yield zero. Timing includes feature extraction
and optional model loading and runs under allocation tracing; it is not an
isolated inference throughput claim. Python memory tracing excludes some native
allocations and is not process RSS.

PowerShell commands reproducing the selected 1,080-example model, from the
project root (keep training and evaluation seed counts separate):

```powershell
.\.venv\Scripts\python.exe -c "from rfatlas.corpus import cases; from rfatlas.ml import train; print(train(cases('development', 6), 'reports/engine-ml-expanded/model.npz', max_seconds=90))"
.\.venv\Scripts\python.exe tools\evaluate_engine.py --split development --model reports\engine-ml-expanded\model.npz --output reports\engine-development
.\.venv\Scripts\python.exe tools\evaluate_engine.py --split validation --model reports\engine-ml-expanded\model.npz --output reports\engine-validation
# After freezing implementation and selection parameters:
.\.venv\Scripts\python.exe tools\evaluate_engine.py --split holdout --model reports\engine-ml-expanded\model.npz --output reports\engine-holdout
.\.venv\Scripts\python.exe tools\evaluate_engine.py --original --model reports\engine-ml-expanded\model.npz --output reports\engine-original
.\.venv\Scripts\python.exe tools\evaluate_engine.py --split holdout --stress --model reports\engine-ml-expanded\model.npz --output reports\engine-holdout-stress
```

Use the same interpreter's `python` command on Ubuntu. CLI reports and weights
remain ignored runtime artifacts; no private recording is required or used.
Baseline comparison requires local Git history containing the recorded commit.

## Optional machine learning experiment

`rfatlas.ml` implements an actual NumPy CPU MLP: 26 engineered features, 40 tanh
hidden units and ten output classes. Development training uses deterministic
initialization, Adam updates, regularization, bounded epochs and a wall-time
budget (60 seconds by default, maximum 300). Extraction also observes the budget.
Only the small feature table is retained. No PyTorch/TensorFlow, external model,
network service, downloaded dataset or GPU is needed. The feature representation
uses existing RF Atlas statistics and therefore is complementary evidence rather
than an independent physical measurement.

The optional model is an explicitly requested local NPZ path; no weights are
bundled. Loading disables pickle, checks compressed/uncompressed size, dimensions,
finite numbers, labels and rejection parameters. Training code, synthetic data
and locally generated weights are project-authored MIT material. That statement
does not assign licenses to any third-party dataset or pretrained weight.

Outputs are uncalibrated model scores. A fixed pre-holdout top-score threshold
of 0.55 and top-two margin of 0.12 reject ambiguity, along with spectral and
acquisition quality gates. Rejected outputs retain raw candidate scores separately
from zero selected-label confidence. The hybrid agrees only when DSP and MLP
choose the same non-UNKNOWN hypothesis, never increases confidence and otherwise
rejects with candidate evidence retained. This can improve accepted accuracy at
the expense of substantial rejection and reduced overall accuracy. All backends
must be compared, including cases where hybrid is worse; synthetic training
success alone is insufficient to promote ML as the default.

## Bounded experiment and selection

The first development-only run trained on 360 examples in 12.31 seconds, yielding
a 14,817-byte model and 97.78% training accuracy. Its independent validation
accuracy fell to 57.22%, demonstrating overfit rather than generalization.
A bounded repair expanded development coverage to six seeds per cell (1,080
examples), without changing network, rejection thresholds or validation seeds.
That run took 46.58 seconds, retained a 224,640-byte feature table and produced
a 15,904-byte model with 93.80% training accuracy. Expanded-model validation
accuracy rose to 61.94%, with 84.23% accepted accuracy and 38.33% rejection.
These are measured whole-adapter figures including acquisition-quality gates.

The expanded model was selected for optional experimental use using validation
alone, then frozen before final holdout inference. DSP remained the default:
validation accuracy was 66.67%, accepted accuracy 82.26% and rejection 31.11%.
The expanded-model agreement-only hybrid reached 51.39% validation accuracy,
100% accepted accuracy and 58.61% rejection. Its much lower coverage and overall
accuracy do not justify promoting it as the default. In particular, NFM and
2-FSK remain weaker under ML than DSP. No synthetic result establishes
performance on real unlabeled captures.

The selected optional weights remain in
`reports/engine-ml-expanded/model.npz`; they are runtime artifacts rather than
bundled release files. To reproduce this expanded training experiment, call
`rfatlas.ml.train(rfatlas.corpus.cases('development', 6), model_path,
max_seconds=90)` and evaluate the default two-seed validation/holdout corpus.

## Frozen independent holdout results

After freezing DSP rules, model selection and hybrid policy, the 360-case final
holdout produced the following results. Percentages include correctly rejected
noise as correct UNKNOWN; accepted accuracy excludes all UNKNOWN predictions.

| Backend | Overall accuracy | Rejection | Accepted accuracy |
| --- | ---: | ---: | ---: |
| Committed Phase 1.3 | 26.67% | 66.11% | 49.18% |
| DSP recovery | 64.17% | 30.00% | 77.38% |
| Optional expanded MLP | 66.39% | 36.67% | 89.04% |
| Agreement-only hybrid | 53.06% | 56.39% | 98.73% |

DSP macro-F1 was 0.6629 and accuracy Wilson interval 59.09–68.95%; MLP macro-F1
was 0.7013 and interval 61.36–71.07%. Signal-only rejection was 22.22% for DSP
and 29.63% for MLP. Each backend rejected all 36 noise-only cases; zero observed
false detections still has a descriptive 95% upper bound of 9.64%. Every signal
family's DSP recall exceeded the committed baseline on this holdout. Four-state
FSK remains weak (36.11% DSP recall), and low-SNR and impairment failures remain.
The preselected DSP default was retained; the small holdout advantage of MLP
was not used to change selection or tune the final model.

The separate 30-case long/duty holdout exposed a material limitation. Overall
accuracy was 40.00% baseline, 63.33% DSP, 56.67% MLP and 50.00% hybrid. Accepted
accuracy was respectively 60.00%, 59.26%, 56.00% and 85.71%, with rejection
50.00%, 10.00%, 16.67% and 53.33%. Thus DSP increased coverage and overall
accuracy while slightly reducing accepted accuracy under this shift; neither
ML nor hybrid solved long/duty generalization. There were only three noise
cases in this supplement, so its zero observed false detections provide weak
evidence (95% upper bound 56.15%). These failures were documented without
retuning against the holdout.

In that stress supplement, NFM and CW DSP recall each fell from 2/3 baseline to
1/3. Residual duty-cycle envelope changes were frequently mistaken for AM.
The per-family denominator is only three, but these observed regressions are
material and remain unresolved. Default promotion reflects consistent gains
across the original and primary independent suites, with this explicit tradeoff;
it does not make catalog hypotheses authoritative real-world identifications.

Mean DSP adapter time on the main holdout was 76.44 ms and expanded MLP adapter
time 153.05 ms under allocation tracing, with concurrent benchmark processes.
This is a reproducible local observation, not a latency guarantee. JSON results
and count matrices are stored separately in `reports/engine-holdout-dsp`,
`reports/engine-holdout-ml`, `reports/engine-holdout-stress-dsp` and
`reports/engine-holdout-stress-ml`.

## Preserved original benchmark and generator shift

The original 720-case waveform suite remained unchanged. Replaying the
committed baseline's activity window reproduces its 45.00% result exactly.
The following ML/hybrid figures use the selected expanded model; the earlier
360-training-example experiment is retained separately and is not silently
substituted for the selected model.

| Backend | Overall accuracy | Rejection | Accepted accuracy |
| --- | ---: | ---: | ---: |
| Committed Phase 1.3 | 45.00% | 49.03% | 68.66% |
| DSP recovery | 81.11% | 23.33% | 92.75% |
| Optional expanded MLP | 52.64% | 29.86% | 60.79% |
| Agreement-only hybrid | 50.42% | 59.31% | 99.32% |

The expanded MLP's original-suite BPSK recall was only 1/72 (1.39%), AM recall
38.89%, 2-FSK recall 30.56% and 4-FSK recall 29.17%. The original rectangular
symbol waveforms differ from the pulse-shaped training generator. This large
distribution shift demonstrates why a strong small synthetic holdout result
does not justify a general-purpose ML default. The hybrid also loses most
BPSK coverage; high accuracy among its accepted predictions conceals substantial
rejection if viewed alone. All backends rejected all 72 original noise cases,
whose descriptive 95% false-detection upper bound remains 5.07%.

Original baseline/DSP results are in `reports/engine-original-final/results.json`;
selected-model results are in `reports/engine-original-expanded-ml/results.json`.
The selected model SHA-256 is
`428771588aa771bb77f1016df2a521662002d453bc5492c73d1ec9e3aec38cf6`.
