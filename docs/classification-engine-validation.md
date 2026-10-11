# Classification engine completion report

## Executive summary

RF Atlas 0.4.0 adds a bounded DSP recovery engine, an optional working CPU neural experiment, disagreement-aware hybrid evaluation, independent development/validation/holdout corpora, CU8 SigMF interchange and a tested future decoder contract. The validated detector and original benchmark/generator remain unchanged. No driver, DLL, USB, VM or remote Git changes were made.

DSP is selected as the default research analysis backend: it improved every family in both the original suite and the main independent holdout, and was the strongest overall backend on validation before holdout was opened. It is not promoted to an authoritative catalog classifier. The optional model and hybrid remain explicit experiments; their distribution-shift and rejection failures prevent default promotion. High-duty and sparse-burst FM/CW remain material DSP weaknesses, with baseline replay available for comparison.

## Source code and implemented workstreams

| Workstream | Result and status |
|---|---|
| Resource research | Implemented report covering all five requested projects, real pretrained assets, dependencies, licenses and decisions |
| Recovery | Implemented channel isolation, conditional PSK carrier estimation/correction, burst refinement, envelope/frequency/phase statistics and acquisition-quality gates |
| Timing/filtering | Implemented transition-grid rate estimate and explicit known-rate rectangular matched filter/32-phase timing search; blind tracking, arbitrary pulse recovery and robust drift tracking remain partial/deferred |
| DSP classification | Implemented evidence/candidates, UNKNOWN, quality status and reproducible operations for the requested waveform families |
| Optional ML | Implemented and evaluated an actual 26-feature, 40-hidden-unit NumPy CPU MLP; no framework, external weights, paid API or GPU required |
| Hybrid | Implemented agreement-only selection and disagreement rejection; evaluated and retained opt-in because overall coverage/accuracy fall |
| Independent evaluation | Completed disjoint development/validation/holdout and supplemental long/duty stress tests; original 720-case suite preserved |
| SigMF | Implemented bounded single-capture CU8 import/export; broader formats, multi-capture and required extensions unsupported |
| Protocol preparation | Implemented/tested adapter/evidence contract; no actual radio protocol decoder is supplied |
| Portability/release | Windows and actual Ubuntu 24.04 software tests/installation; privacy scans and local packaging; Ubuntu 26.04 execution and Linux receiver hardware remain unverified |

Created: `rfatlas/recovery.py`, `engine.py`, `corpus.py`, `ml.py`, `sigmf.py`, `decoders.py`; `tools/evaluate_engine.py`, `plot_engine_results.py`; `tests/test_recovery.py`, `test_engine_cli.py`, `test_engine_benchmark.py`, `test_interop.py`; resource, benchmark, interoperability and this validation documentation. Updated: `rfatlas/iq.py`, `legacy.py`, `__init__.py`, `README.md`, architecture/license/next-phase documentation, package version, release checker, ignore and manifest rules. No existing source or private evidence was removed.

## GitHub resources and licensing

[Resource evaluation](classification-resource-evaluation.md) records primary sources and inspected revisions. liquid-dsp is an algorithm reference, with optional native integration deferred; no mandatory C dependency is added. Its optional FFTW linkage needs a separate license assessment. GNU Radio remains reference-only; no GPL source was copied. TorchSig's heavyweight stack is isolated from normal runtime and was not installed. Its capabilities informed the independent project-authored generator, which is not claimed to be TorchSig or external ground truth.

TorchSig Models has actual downloadable XCiT weights and CPU inference code. Weight-specific redistribution terms and training/input provenance were not sufficiently established, and its full dependency stack conflicts with this environment and includes separately licensed components. No weights or datasets were downloaded. SigMF's specification license is CC-BY-SA; the adapter independently implements format semantics without copying schema/prose. Project source, generated training data and locally trained experimental weights are MIT material. No new runtime dependency or external service was adopted; exact dependency notices remain in [the inventory](dependency-licenses.md).

## DSP improvements and assumptions

The engine validates finite complex input and real sample rate, bounds analysis to one million samples, retains original IQ, and logs isolation/filter/window parameters. Zero-phase Butterworth isolation reduces out-of-channel noise. PSD centroid mixing is a channel placement estimate, not a calibrated carrier correction. Weak/mixed signals can still bias bandwidth and centroid. DC is measured and retained because blind subtraction erases a real baseband carrier.

PSK hypotheses use second/fourth-power FFT carrier lines with sub-bin interpolation, phase coherence, state occupancy and plateau checks. The latter checks reject targeted sinusoidal-FM negatives that otherwise mimic PSK. These are conditional PSK estimators with alias ambiguities, not a universal PLL. Frequency-state concentration after filtering helps distinguish FSK from continuously varying FM. Filtered low/high envelope occupancy and envelope periodicity help OOK/AM separation. Near-rail occupancy is a warning; repeated actual endpoints support clipping rejection. Replay measures endpoints before frequency rotation and rejects insufficient CU8 amplitude resolution.

Transition-grid symbol-rate estimates return UNKNOWN without support and can lock to harmonics. Known-rate timing recovery requires an explicit rectangular-pulse assumption and refuses a flat eye metric. It is not automatically applied to arbitrary IQ and does not provide continuous clock tracking. Sample-rate metadata cannot prove the transmitter's rate or correct an operator's plausible-but-wrong acquisition setting.

Results separate signal presence, bandwidth, analog/digital hypothesis, modulation candidates/evidence, optional symbol rate, quality, protocol and protection. Scores are explicitly uncalibrated. ML scores and DSP support are not calibrated probabilities; hybrid agreement neither increases support nor verifies a waveform. All classifications remain hypotheses; catalog truth is not overwritten.

## Classification results

The baseline is `f8502e3`, including its activity selection. The preserved original suite repeats exactly the same 720 generated inputs and reproduces its **45.00%** baseline. The new corpus is independently implemented with different pulse shaping, fractional symbol timing, modulation parameters, carrier offset/drift, bursts, clipping, quantization, gain and sampling. Each main split has 360 cases; seed namespaces are disjoint. The MLP trains only on development. One bounded expansion from 360 to 1080 training cases was selected using validation; weights/decision rules were frozen before holdout. No holdout threshold tuning or repair was performed. Loader hardening and output-schema changes do not alter valid-model decisions.

| Set / engine | Accuracy | Macro-F1 | Rejected | Accuracy among accepted |
|---|---:|---:|---:|---:|
| Original 720 baseline | 45.00% | 0.460 | 49.03% | 68.66% |
| Original 720 DSP | **81.11%** | **0.829** | 23.33% | **92.75%** |
| Original 720 selected ML | 52.64% | 0.502 | 29.86% | 60.79% |
| Original 720 hybrid | 50.42% | 0.545 | 59.31% | 99.32% |
| Independent holdout baseline | 26.67% | 0.211 | 66.11% | 49.18% |
| Independent holdout DSP | **64.17%** | 0.663 | 30.00% | 77.38% |
| Independent holdout selected ML | 66.39% | 0.701 | 36.67% | 89.04% |
| Independent holdout hybrid | 53.06% | 0.596 | 56.39% | 98.73% |

The exact holdout baseline rejection/accepted figures and all metrics are generated in machine-readable results; see reproduction commands below. Rejection includes correctly rejected noise; signal-only rejection is also recorded. DSP signal-only rejection on holdout is 22.22%. Descriptive 95% Wilson accuracy intervals: DSP holdout 59.09–68.95%; ML holdout 61.36–71.07%. Related impairments are not independent trials, so these intervals are not an operational guarantee or a statistically established DSP-versus-ML difference.

| Family | Original baseline | Original DSP | Holdout baseline | Holdout DSP |
|---|---:|---:|---:|---:|
| AM | 19.44% | 86.11% | 0.00% | 72.22% |
| NFM | 58.33% | 77.78% | 41.67% | 72.22% |
| WFM | 58.33% | 75.00% | 41.67% | 55.56% |
| OOK | 70.83% | 86.11% | 61.11% | 83.33% |
| 2FSK | 19.44% | 80.56% | 0.00% | 50.00% |
| 4FSK | 19.44% | 61.11% | 2.78% | 36.11% |
| BPSK | 25.00% | 83.33% | 2.78% | 63.89% |
| QPSK | 20.83% | 83.33% | 0.00% | 55.56% |
| CW | 58.33% | 77.78% | 16.67% | 52.78% |
| Noise -> UNKNOWN | 100% | 100% | 100% | 100% |

Original DSP accuracy by SNR: noiseless 83.33%, 5 dB 68.89%, 15 dB 87.78%, 30 dB 84.44%. Nonmonotonic behavior reflects family/impairment decisions and clipping, not a calibrated SNR response. Per-class precision/recall/F1, SNR results, configurations, seeds and count confusion matrices are in the JSON/TSV outputs. Observed noise false detection/labeling is zero: 0/36 main holdout controls (descriptive 95% upper bound 9.64%) and 0/72 original controls (upper 5.07%; repeated noise conditions are correlated). Additional colored-noise/interference field negatives are still needed.

Supplemental holdout has 30 cases: 1.2-second continuous, 5%-duty and 85%-duty signals for every family. DSP accuracy is 63.33% versus baseline 40.00%; accepted accuracy is **59.26% versus 60.00%**, with rejection 10% versus 50%. NFM and CW each regress from 2/3 to 1/3 correct. Burst envelopes can be mistaken for AM/OOK. The small set is not grounds to dismiss these failures: default DSP is chosen for broad improvements in both main suites, with an explicit research-only status and a baseline fallback. It is unsuitable for unreviewed FM/CW decisions under extreme gating. This tradeoff is retained rather than retuning against the now-used holdout.

## Machine learning and hybrid selection

The selected experiment uses 1080 streamed development examples, 26 features, 40 tanh hidden units, Adam updates and fixed rejection parameters. Training completed 400 epochs in 46.58 seconds; its feature table is 224,640 bytes and the model 15,904 bytes. Model SHA256: `428771588aa771bb77f1016df2a521662002d453bc5492c73d1ec9e3aec38cf6`. These are local generated weights, not a pretrained TorchSig integration. No GPU, framework or cloud inference is needed. Archive loading disables pickle and bounds compressed/uncompressed data, NPY declarations, shapes and numeric values; missing/invalid models fall back to DSP explicitly.

Expanded ML validation accuracy is 61.94%, below DSP 66.67%; this determined the default before holdout. ML later reaches 66.39% on holdout but falls to 52.64% on the original generator. Original BPSK recall is only **1/72**. This generator-shift failure prevents default promotion despite the favorable holdout mean. Hybrid raises accepted accuracy by abstaining heavily and has lower overall accuracy than DSP on both main suites. It does not solve generalization; all optional backends remain experimental.

Runtime on the main holdout under Python allocation tracing: baseline mean/p95 approximately 66/142 ms; DSP 76/157 ms. Long/duty DSP stress mean/p95 is approximately 204/272 ms. Main baseline/DSP run traced peak allocations are approximately 16.2 MB; stress approximately 24.3 MB. These are process-run traced peaks, not per-backend RSS or total native memory. CPU load, model loading, tracing and concurrent bounded experiments affect timings. Each JSON records wall time, exact timing, source/model hashes and memory caveats.

## Compatibility and hardware

Windows 11: full local suite **72 tests**, including all original 41. Clean public Windows snapshot: **59 tests**. Windows wheel/source packaging and installed-wheel smoke tests pass. Actual existing Ubuntu **24.04.4**, Python **3.12.3**, NumPy **2.5.3**, SciPy **1.18.1**: package installation and **59 public tests pass**. Only a public privacy-scanned snapshot was transferred; dependencies live in a temporary project virtual environment. Missing system ensurepip was handled with a project-scoped pip wheel, without apt/system-package or infrastructure changes. The existing CI configuration remains unchanged. Ubuntu 26.04/Python matrix jobs and physical Linux receiver access remain unexecuted.

Windows V4L completed a new finite two-second CU8 capture with manual gain and offset tuning; exact sample count and checksum verified, with nominal/tool-reported rates preserved. Three existing/current captures remain readable. The new short capture did not establish RF presence, while earlier recordings contain detected RF with modulation/protocol/protection UNKNOWN. These are unlabeled evidence, not accuracy ground truth. All real recordings and hardware identifiers remain private. Baseline/current detector decisions agree on **20/20 identical saved spectra**; detection, noise-model, acquisition and OS adapter source remain unchanged. No auto-gain claim, driver, DLL, USB or VM modification occurred.

## Public release and Git

Branch: `classification-engine`, based on `f8502e3`. Local commit hash is supplied with the completion response and `git log -1`; no Git history rewrite, remote repository creation or push occurs. Public source excludes runtime files, recordings, SigMF datasets, model weights, private configs/databases and diagnostic logs. Source index, wheel and source archive checks pass; manual privacy/license review is still required. Core MIT licensing and dependency inventory are preserved.

## Reproduction and artifacts

```text
python -m unittest discover -s tests -v
python tools/evaluate_analysis.py
python tools/evaluate_engine.py --original --output reports/engine-original
python tools/evaluate_engine.py --split validation --output reports/engine-validation
python tools/evaluate_engine.py --split holdout --output reports/engine-holdout
python tools/evaluate_engine.py --split holdout --stress --output reports/engine-stress
python tools/plot_engine_results.py reports/engine-holdout/results.json
python tools/release_check.py --tracked
python -m pip wheel . --no-deps --wheel-dir dist
```

[Benchmark design](classification-benchmark-design.md) contains the exact expanded-model training and four-backend reproduction commands. Original baseline comparison requires local history containing `f8502e3`; the original older benchmark additionally uses `bd31fc8`. Plotting uses the optional plots extra. Holdout now becomes a regression set: future tuning must create a fresh final holdout.

Machine-readable evidence remains in ignored `reports`: `engine-original-final`, `engine-original-expanded-ml`, `engine-development-final`, `engine-validation-final`, `engine-ml-expanded-validation`, `engine-holdout-dsp`, `engine-holdout-ml`, and the corresponding stress directories. Each contains `results.json` and count confusion matrices. Private capture/replay/detector evidence lives separately in `engine-hardware`; nothing is uploaded to a cloud classifier.

## Limitations and next development

Prioritize burst/envelope separation, FM/CW long-duty failures, 4FSK discrimination, and noise/interference negatives. Add independent licensed external fixtures and a fresh untouched holdout after those changes. Robust clock tracking, drift-aware carrier loops, validated pulse-shape recovery, multi-window voting and score calibration remain incomplete. Current confidence is uncalibrated; OFDM, multilevel ASK, real protocol identification and protection inference are unsupported. The decoder contract is preparation, not an implemented decoder. Validate physical Ubuntu hardware and cleanup behavior before deployment. Dashboard, distributed sensors and geolocation remain deferred.
