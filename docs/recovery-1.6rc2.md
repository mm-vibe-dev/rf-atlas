# RF Atlas 1.6.0rc2 classification recovery

This candidate preserves the frozen DSP engine and adds evidence in the versioned recognition wrapper. It remains a prerelease. Waveform hypotheses never verify a protocol or encryption state.

## Pipeline audit and changes

The occupied-power estimator can select only a strong AM carrier when distributed audio sidebands fall below its spectrum threshold. Filtering at that inferred width removes amplitude information before classification. The recognition wrapper now measures a bounded minimum 12 kHz analysis window (limited for lower sample rates) before assessing continuous audio-band envelope variation. Original occupied bandwidth, frozen prediction and preprocessing evidence remain available. This wider window is a hypothesis and can include neighboring interference; it does not establish a single emitter.

The rc1 FM extension demanded a dominant discriminator tone. Speech and stochastic audio distribute power across frequency bins. The revised rule uses total audio-band discriminator concentration with continuous-envelope and frequency-state exclusion gates. It keeps an UNKNOWN family when FM versus digital remains unresolved. Strong out-of-audio envelope beating rejects an AM claim. Scores remain uncalibrated.

Real FM multiplexes can contain pilot and subcarrier energy outside the mono audio band. A separate wideband rule requires a narrow 19 kHz pilot-like component above neighboring spectrum, audio-band energy, a continuous envelope and weak discrete frequency-state evidence. This supports a WFM candidate only. It does not verify RDS, prove intelligible audio, or establish mixed content without decoder evidence. Two retained broadcast recordings were development references for this rule; related captures are regression evidence, not independent emitter holdouts.

An unmodulated carrier is displayed as CARRIER, without asserting CW keying. USB/LSB direction cannot generally be established without a carrier reference. Keyed CW, SSB and OFDM recognition remain incomplete. A verified RDS result establishes MIXED content; other verified native digital protocols take precedence over weaker waveform hypotheses. Existing catalog protocol conflict protection remains in effect.

Reanalysis prioritizes usable quality, unresolved modulation, potential digital evidence, absent attempts, repeated UNKNOWN results and measured spectral contrast before applying its recording budget. It preserves the recording/context/algorithm cache and append-only history. Decoder diagnostics expose selected/input/sync/framing/checksum/verification stages with null where a stage was not observed. Failing a checksum does not prove successful framing.

## Reference evaluation

`tools/evaluate_recovery.py` constructs independent FFT-domain audio, pulse-shaped symbols, Gaussian FSK, randomized OFDM blocks, slow keyed carriers, noise and two-tone artifacts. It provides all sixteen requested label examples and grouped seed provenance. All SNR variants of one generated source stay in one split. Separate seeds are synthetic source independence, not independently labeled real emitters. Existing frozen generators and rc1 holdouts are regression sets, not new independent evidence. Private manifests include sample SHA256, type, rate, offset, duration, seed, label basis and source group. IQ stays outside Git.

Real reference labels require native synchronization and integrity verification on retained/fresh RDS and DMR recordings. Frequency alone never labels live AM or weather-band reception. Unsupported or inactive transmissions remain unlabeled. No training uses held-out or private captures.

## Audio

The DSP exposes input/IF/output sample counts, rational-resampling clock error, audio duration and explicit mute/squelch/low-energy silence causes. Existing host-delivery, IQ queue, DSP timing, transport-sequence and underrun counters remain. Browser timing now records playback-gap duration and discarded PCM duration. Fixed startup buffering remains 160 ms. Poll spacing is reduced from 60 to 20 ms; steady playback latency does not increase. Server backlog remains bounded to three 100 ms blocks and browser scheduling remains bounded to 500 ms before discarding backlog. Browser output-device latency is additional.

An adaptive buffering experiment was rejected because controlled fixtures increased discarded PCM. Shorter polling reduced gaps under selected controlled transport delays, but cannot recover IQ lost by the receiver or guarantee continuity during an arbitrary VM/browser stall. Live before/after measurements and any remaining failures belong in the private consolidated campaign report.

## Optional ML

ML remains off by default. No Torch, Transformers, CUDA or downloaded checkpoint becomes a dependency. TorchSig and TorchSig Models provide RF architectures; adopting a checkpoint requires explicit input, label, weight-license and inference compatibility evidence. Hugging Face Transformers' model named RADIO takes image pixel inputs, not complex RF IQ, and is unsuitable as an RF classifier. The synthetic-only local prototype remains experimental and its rc1 results do not justify promotion.

Primary sources checked for this candidate: [TorchSig](https://github.com/TorchDSP/torchsig), [TorchSig Models](https://github.com/TorchDSP/torchsig-models), [Transformers RADIO documentation](https://huggingface.co/docs/transformers/model_doc/radio).

| RF-specific candidate | Documented contract | Adoption gap |
| --- | --- | --- |
| [CVNet-RF](https://huggingface.co/sohelimi/cvnet-rf) | 2 × 1024 IQ, 24 RadioML labels, PyTorch 2; model card states MIT | No validated SDR sample-rate/channel normalization or noise/UNKNOWN rejection contract for this application; Windows CPU inference unmeasured |
| [SignalIQ CLDNN](https://huggingface.co/alirezaaminzadeh/radio-modulation-classifier) | 2 × 1024 IQ, 11 labels; MIT metadata; possible synthetic training fallback | Fixed short window, uncertain applicable training provenance; author excludes real-time SDR deployment without domain validation |
| [MT-PreamCNN](https://huggingface.co/lkk688/mt-preamcnn) | 800 complex samples in an interleaved 1600-float vector, OFDM preambles; MIT metadata; ONNX exports | Preamble-specific input/task, not a universal blind analog/digital classifier; published CPU timings are author measurements |
| [WavesFM](https://huggingface.co/ahmedaboulfo/wavesfm) | Multimodal wireless foundation encoder; Apache-2.0 metadata | Requires task-specific fine-tuning/heads rather than directly providing the requested catalog taxonomy |

These are research candidates, not installed or benchmarked checkpoints. Card claims do not establish accuracy on RTL-SDR Blog recordings. No new weights or inference frameworks were downloaded. The unchanged local experimental models were benchmarked separately against rc2; comparable aggregate accuracy with precision/recall tradeoffs is insufficient to enable ML by default.

## Validation and release gates

Run tracked Windows and Ubuntu source tests, isolated installed-wheel tests, native protocol vectors including corrupt/no-sync cases, private retained DMR/RDS replay, targeted hardware reception, browser/control/epoch checks and preservation audits. Private RF data, catalogs, device configuration and detailed field reports are excluded from source packages. Mandatory analog/generalization/audio claims stay unproven until supported by independent measurements; retain prerelease status and list blockers explicitly.
