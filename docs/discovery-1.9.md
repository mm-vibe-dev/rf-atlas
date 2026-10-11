# RF Atlas 1.9.0rc1

RF Atlas by mm-vibe-dev is a single-receiver, local, receive-only spectrum inventory and I/Q investigation application. This is an experimental prerelease. It does not establish universal signal recognition, general message decoding or successful hardware recovery.

## Implemented changes

The existing Explore, Signals and Settings interface remains. Settings adds an optional AI switch, off by default. Signal details show validated message metadata and separate AI hypotheses. Digital investigation can produce one bounded ASK/FSK bit candidate from a measured run-length timing hypothesis. The CLI also supports explicit OOK, 2FSK, 4FSK, BPSK and QPSK diagnostic demodulation. These paths preserve unresolved timing/polarity/phase limitations and do not verify protocols.

`rf-atlas ai-train` trains an original CPU ridge model on 216 synthetic development cases from 72 source groups. It uses 28 measured spectral, envelope, phase and structural features. Family and modulation heads share standardized features. JSON numeric parameters are bounded, non-executable and checked against an adjacent SHA256. Checksums are not signatures. Inference runs in a single optional worker with no unbounded queue, records rejection/disagreement, and cannot overwrite DSP or protocol verification. Missing, incompatible or failed models leave DSP operational.

`rf-atlas ai-infer recording.cu8` performs actual model inference on metadata-checked, frequency-translated recording samples. No private RF recordings train the model. No model download, paid API, PyTorch, pickle, custom checkpoint code or GPU is required. Training requires a complete bounded structural bank, and inference rejects budget-truncated features. Two full training runs reproduced the same JSON SHA256. An initial budget-dependent model was discarded after reproducibility checking; the corrected frozen model uses a new holdout seed range and unchanged thresholds.

Common recovered frame records now include schema, protocol, source checksum, sample rate, frequency, decoder/version, synchronization/checksum references and correction evidence. Selected already validated DMR, RDS, AX.25, AIS, ADS-B and weather frames produce structured metadata. Failed-CRC frames and arbitrary candidate bits never become messages. Raw fields/bytes respect independent retention preferences. Both preferences default off; parsed identifiers and content are removed before persistence. Selected metadata does not imply general text, APRS application semantics or voice decoding.

The FEC interface exposes the existing original DMR Golay slot and BPTC correction algorithms with exact binary input sizes and explicit limits. Golay accepts only a unique nearest codeword within three errors. BPTC uses at most five iterative syndrome passes; correction operations are not the number of channel errors. A converged codeword still requires the native CSBK CRC. CRC checking alone is never described as FEC. No generic Reed–Solomon, convolutional or universal FEC support is claimed.

Opening the receiver is now displayed as opened_unverified until actual measurement delivery is observed; enumeration alone cannot report sample-delivery success. The sanitized doctor command distinguishes tool availability, receiver sample delivery, optional decoders, model checksum, loopback-port availability and browser audio capability. Its receiver test permits at most two finite acquisitions under the shared lock, with one process restart. It exports no serials, paths, host names, raw stderr or RF content. It does not change drivers, USB passthrough, system permissions or audio infrastructure.

## Frozen ML evaluation

Development, validation and holdout each contain 216 cases from 72 source groups. The three seed ranges are disjoint. SNR variants share their source group. Generators cover selected analog/digital modulations, carriers, noise and unsupported/interference constructions. This is construction truth from one generator family, not independent real emitter sessions. The fixed synthetic-only model is intentionally conservative and uncalibrated.

| Method | Synthetic holdout family accuracy | Balanced accuracy | Macro F1 | Abstention | Accepted accuracy |
|---|---:|---:|---:|---:|---:|
| Default DSP | 41.67% | 55.19% | 0.4499 | 39.81% | 61.54% |
| Optional CPU ridge | 62.50% | 67.41% | 0.5802 | 43.06% | 82.93% |
| Agreement-only research hybrid | 46.30% | 52.41% | 0.4944 | 63.43% | 84.81% |

On the same holdout, digital-to-analog errors were 3.70% for DSP, 1.85% for AI and 0% for agreement-only hybrid. Noise-to-analog/digital errors were 0% for all three. These figures do not predict field performance. Hybrid is an evaluation method; the application displays AI separately rather than silently substituting it for DSP.

The historical real reference has only 17 retained recordings: 12 RDS-bearing analog FM and five DMR, grouped conservatively into two protocol/session groups. They are prior-evaluated regression references, not fresh holdout samples. Ground truth comes from independently validated decoder evidence rather than the waveform model. Weak digital recordings remain subject to the ADC quality gate.

| Method | Correct family | Accuracy | Abstention | Accepted accuracy |
|---|---:|---:|---:|---:|
| Default DSP | 12 / 17 | 70.59% | 29.41% | 100% |
| Previous research ridge | 5 / 17 | 29.41% | 70.59% | 100% |
| New optional CPU ridge | 0 / 17 | 0% | 100% | N/A |
| Agreement-only research hybrid | 0 / 17 | 0% | 100% | N/A |

The new model is unsuitable for promotion over DSP. All 17 real recordings are rejected. Synthetic gains do not establish generalization; accepted accuracy is undefined when there are no accepted real samples. More independently labeled receiver sessions and validation-only calibration are required before considering promotion. DSP remains unchanged in its classification behavior and stays the default.

## Hardware and audio limits

The current Windows receiver enumerates and opens, but finite acquisitions return zero bytes at both tested sample rates. The new doctor also fails two bounded attempts. Restart recovery and continuous sample delivery are not demonstrated. Existing software watchdogs stop stalled receiver children and release ownership; that cleanup is distinct from repairing the underlying delivery failure.

The previous long field campaign lasted about 53 minutes, with useful sample delivery ending much earlier, after about 21 minutes. It is not a successful 53-minute endurance result. A longer new endurance run with no samples would provide no RF evidence, so the recovery gate remains failed. No driver, VM, USB, firmware or Proxmox changes were made. Live audio and new protocol reception remain unverified while delivery fails. Existing generated audio and cancellation tests remain useful software evidence only.

## Installation and publication

Versioned wheels and source distributions use Python 3.11+ with NumPy/SciPy. Optional plots and analytics remain extras. The Windows scripts install a SHA256-checked local wheel or a verified release asset into user-scope application slots, preserve runtime data and provide launch/upgrade/repair/uninstall and optional desktop shortcuts. They do not modify execution policy or install drivers. Ubuntu supports isolated wheel installation and pipx; system Python remains untouched. The Ubuntu 24.04 host lacks ensurepip, so its default pipx backend failed before application installation. Installing uv only in the existing task virtual environment and using pipx's supported uv backend passed installation, CLI/doctor, reinstall and uninstall. Ubuntu 26.04 wheel testing initially exhausted the temporary-filesystem quota; the rerun used a separate task directory on the larger filesystem and passed.

Public upload and Pages deployment require authenticated access to the existing mm-vibe-dev/rf-atlas repository. Local preparation does not establish that a release or website is live. No bare PyPI package command is advertised before actual PyPI publication. Trusted Publishing configuration is prepared for manual OIDC use, with no API tokens in the repository. The static website states publication status explicitly.

Private development history is preserved and audited separately. Publication uses a clean snapshot history if historical private artifacts are found. Source, wheel, sdist, installers and website must pass privacy and license checks. Original source and locally derived weights are MIT licensed. uPlot's complete original MIT notice remains bundled; external SDR tools and third-party model code/weights are not redistributed.

See CONTRIBUTING.md for extension contracts and SECURITY.md for privacy boundaries. Generated fixtures and software CI cannot establish fresh live verification. Secondary digital protocols, encrypted traffic, generic timing/FEC and broader real RF learning remain unsupported or unverified.
