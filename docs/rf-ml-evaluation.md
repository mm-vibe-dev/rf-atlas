# RF learning evaluation (1.6 release candidate)

DSP remains the default. No external weights, dataset, executable model code,
TensorFlow, CUDA runtime or mandatory dependency was added. The original MIT
CPU prototype is an opt-in experiment; it failed to outperform DSP.

## Primary-source review

Reviewed 2026-10-10. Repository licenses, dataset licenses and weight licenses
are separate obligations. A framework's license does not grant rights to an
arbitrary checkpoint or training corpus.

| Resource | Task and input contract | Runtime / licensing assessment | Decision |
| --- | --- | --- | --- |
| [Transformers](https://github.com/huggingface/transformers) | General model framework; no RF label set, IQ normalization, sample rate or capture duration established by the repository | Apache-2.0 code; CPU/GPU feasibility, weight license and remote-code requirements depend on the selected model | No audited RF checkpoint with a complete input/label/license contract selected; no download or execution |
| [TorchSig](https://github.com/TorchDSP/torchsig) | RF data synthesis, impairments and augmentation; configuration determines classes, bandwidth, duration and SNR | MIT code; upstream recommends Ubuntu and substantial training/data resources; inference costs depend on a separate model | Useful future independent impairment testing, not a ready-to-run receiver classifier |
| [TorchSigModels](https://github.com/TorchDSP/torchsig-models) | Narrowband XCiT classifier over 1-D IQ (24-class demonstration); wideband YOLO and anomaly adapters are different tasks | MIT repository code; checkpoint provenance, label order, normalization and sample rate must be audited individually; adapter dependencies may have different licenses | No independently audited, compatible weights selected; model size and CPU/GPU latency unmeasured, not invented |
| [cnn-rtlsdr](https://github.com/randaller/cnn-rtlsdr) | WFM / TV SECAM / DMR / Other; README describes 0.25-second, 2.4 MS/s input decimated by 48 (50 kS/s, approximately 12.5 kHz retained bandwidth) | Obsolete Python 3.6 / TensorFlow GPU 1.4 / CUDA 8 instructions; repository/weights/dataset permissions require separate review | Four labels and old runtime are unsuitable for universal discovery; no old GPU runtime or driver instructions followed |
| Original RF Atlas prototype | Fourteen bounded DSP features; ten labels AM/NFM/WFM/OOK/2FSK/4FSK/BPSK/QPSK/CW/noise; physical bandwidth features, no RF center frequency or identity | Original MIT code, NumPy CPU only; bounded JSON arrays with finite-value/shape checks; no pickle, remote code or services | Evaluated and retained only as an optional research backend |

TorchSig's large corpus/container examples are not requirements for RF Atlas.
No model-size, GPU-speed or Windows-compatibility claim is made for unexecuted
upstream models. Installing a framework alone would not constitute evaluation
of its RF inference accuracy.

## Experiment and leakage controls

The existing frozen generators and classifier were preserved. Development,
validation and holdout seed groups are recorded separately. The prototype was
trained only on 180 development cases, with fixed rejection score 0.65 and
margin 0.15. This is a diagonal prototype-distance classifier, not a neural
network or a calibrated posterior. Local model files and RF evidence remain
outside publication artifacts.

An additional generator uses stochastic filtered audio, independently written
symbol synthesis, 160/240 kS/s, 160 ms captures, SNR 0/12/28 dB, frequency
offsets, weak gain and CU8 quantization. Its 126-case holdout tests a different
synthetic distribution. It does **not** establish real-emitter/session/device
generalization. SSB, QAM, OFDM and two-tone interference have an UNKNOWN target
because these recognizers have no validated identification algorithm for them.
Thus the UNKNOWN column also measures appropriate abstention, not only errors.

Final classifier/model hashes were frozen before final holdout inspection.
An experimental strict AM rejection rule reduced independent validation
accuracy; it was kept as a diagnostic warning rather than promoted. No final
holdout threshold tuning was performed.

| Backend | Frozen holdout accuracy (360) | Independent holdout accuracy (126) | Independent abstention |
| --- | ---: | ---: | ---: |
| Frozen DSP | 52.78% | 69.05% | 41.27% |
| DSP 1.6 | 65.28% | 69.05% | 41.27% |
| Existing local ML | 52.50% | 46.83% | 20.63% |
| Existing conservative hybrid | 23.33% | 42.06% | 76.98% |
| New CPU prototype | 33.61% | 45.24% | 65.87% |

The existing ML exposes a modulation label, explicitly mapped to a family for
this comparison; it remains a hypothesis. Noise false analog/digital rate was
zero in both holdouts for all methods, but sample sizes are small (36 and 9
noise cases). Digital-to-analog errors, accepted precision, confusion matrices,
per-SNR results, timing and abstention are retained in private campaign reports.
No model was promoted on these results.

Windows CPU frozen-holdout mean/p95 classification times: frozen DSP
41.41/112.21 ms, DSP 1.6 50.68/142.09 ms, existing ML 85.89/222.84 ms,
hybrid 86.60/220.33 ms, prototype 51.68/144.13 ms. These are bounded synthetic
inputs, not live acquisition latency. The prototype parameter file is 5,833
bytes; one independent feature evaluation sampled process RSS at about 110 MB.
GPU performance was not measured; GPU inference is not used.

## Similarity result

Four physical DSP features are compared across signal IDs without RF frequency.
On 114 eligible independent holdout queries, the closest eligible neighbor
had a different constructed family in 86.84% of cases. Among 89 top scores
at least 0.9, 91.01% were different families. The same family/SNR configuration
was excluded across gain/quantization variants. This is a family-discrimination
proxy, not identity ground truth, and 0.9 is a reporting cutoff, not a tuned
acceptance threshold. Similarity remains an explicitly experimental diagnostic;
it never merges emitters or verifies protocol, identity or location.

Future priorities are independent labeled receiver sessions, wider stochastic
analog evidence, calibrated rejection, and a checkpoint with an audited RF
input and licensing contract. More optimistic labels are not a substitute.
