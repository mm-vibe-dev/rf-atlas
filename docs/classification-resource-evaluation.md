# Classification resource evaluation

Reviewed 2026-10-09 against the existing NumPy/SciPy classifier. These are engineering adoption decisions, not claims of locally executed third-party validation. No third-party source, pretrained weights, or external datasets were copied into RF Atlas during this review.

| Resource | Decision | Runtime strategy |
| --- | --- | --- |
| liquid-dsp | Reference; optional native experiment later | Keep NumPy/SciPy core; no mandatory C library |
| TorchSig | Experiment as an independent generator | Separate research environment, bounded in-memory samples |
| TorchSig Models | Investigate pretrained XCiT; defer distribution | Avoid installing its entire dependency stack into the application |
| GNU Radio | Reference | No source copying or mandatory runtime |
| SigMF | Adopt a bounded interoperable subset | Original Python adapter; preserve existing recordings |

## liquid-dsp

The MIT library supplies filters, resamplers, modems, oscillators and synchronizers, with corresponding examples and automated tests. Its minimal native runtime uses standard C/math libraries. CMake builds are supported upstream; Windows compilation and ABI packaging still require local validation. Ubuntu is a natural native build target, but no Ubuntu build was run here. Optional FFTW linkage changes redistribution obligations; a future permissive native build should explicitly disable FFTW and audit every linked library. Preserve copyright/license notices when redistributing MIT components. [Repository, build options and licensing](https://github.com/jgaeddert/liquid-dsp).

Relevant algorithms include complex mixing and second-order PLL carrier tracking, pulse-shape-aware polyphase symbol synchronization, and known-constellation demodulation. These address carrier rotation, timing error and noise outside the occupied channel; they do not independently establish modulation or protocol. Use synchronization only after its signal assumptions have evidence. [NCO documentation](https://liquidsdr.org/doc/nco/), [symbol synchronization](https://liquidsdr.org/doc/symsync/), [modem documentation](https://liquidsdr.org/doc/modem/).

Useful reproducible reference examples are `nco_pll_modem_example.c`, `symsync_crcf_full_example.c`, `symtrack_cccf_example.c`, `fskmodem_example.c`, and `modem_ber_example.c`. FSK tone-bank demodulation and EVM/error measurements require a proposed symbol rate, deviation or constellation. A blind classifier should retain alternative hypotheses when those parameters cannot be estimated. [Examples](https://github.com/jgaeddert/liquid-dsp/tree/master/examples).

Prefer independent, bounded Python implementations of elementary mixing/filtering/statistics already expressible with the application's dependencies. A native adapter becomes worthwhile for a measured performance or recovery deficit; it must expose absence cleanly and record its version and processing parameters.

## TorchSig

TorchSig supports configurable synthetic signal families and impairments, including analog, ASK, FSK and PSK signals. Its iterable dataset architecture supports in-memory generation; full published dataset sizes are not a requirement for a small experiment. Upstream recommends Ubuntu/Docker and documents large-resource configurations. Native Windows support should therefore be tested rather than inferred from Python packaging. Code is MIT; preserve notices for copied components and cite the project for research use. [README and license statement](https://github.com/TorchDSP/torchsig/blob/main/README.md).

The runtime dependency set includes PyTorch, torchaudio, Lightning, OpenCV, NumPy, SciPy, h5py, Numba and audio libraries. That is disproportionate for normal RF Atlas replay. Keep a generator-only experiment outside the application environment. Do not install GPU drivers or infrastructure to make it run. [Dependency manifest](https://github.com/TorchDSP/torchsig/blob/main/pyproject.toml).

For independent validation, pin the exact TorchSig revision; save class names, generator settings, random seeds, sample-rate interpretation and impairment settings. Generate one isolated signal per example, explicitly map its labels, and use disjoint development/validation/holdout seeds. Frequency offset, sample-clock error, fading, noise and burst timing must be independently configured. Never substitute a family label for a precise modulation class. [Dataset guidance](https://github.com/TorchDSP/torchsig/blob/main/examples/getting_started.md), [signal label definitions](https://github.com/TorchDSP/torchsig/blob/main/torchsig/signals/signal_lists.py).

Decision: attempt bounded generation only when dependencies are practical. An independently authored generator is an acceptable fallback, but its results must not be described as TorchSig validation.

## TorchSig Models: architectures versus actual weights

The v1.0.0 release, published 2026-07-21, contains XCiT narrowband pretrained weights and a YOLO wideband detector. EfficientNet B0/B2/B4 architectures are also present; a documented architecture does not imply a matching downloadable RF checkpoint. [Release](https://github.com/TorchDSP/torchsig-models/releases/tag/v1.0.0).

The release API was inspected without downloading binary assets:

| Asset | Bytes | SHA-256 supplied by GitHub |
| --- | ---: | --- |
| `xcit_narrowband_v1.0.0.pt` | 25471039 | `4f205eb22598ce21fe97984107c9c899c0d02589a5311a6fbf1182c884db5565` |
| `xcit_narrowband_v1.0.0.ckpt` | 76367665 | `e9b9e7b9324602a78babcbbe01e1b6d8cdb995a0a5e55cb3b7d6c23556d06fec` |
| `yolo11n_wideband_v1.0.0.pt` | 5464026 | `30d0b0d5055d806fa01bec1d5d7ca0719ad7bee3ae23662a8ec666bdf7e79194` |

[Release API with asset metadata](https://api.github.com/repos/TorchDSP/torchsig-models/releases/tags/v1.0.0).

XCiT consumes real float tensors shaped `[batch, 2, samples]` for I/Q. The inference implementation explicitly selects CPU when CUDA is unavailable and maps labels using TorchSig's class list. Correct inference additionally requires the checkpoint's exact label order, normalization, signal lengths and generator/training revision. A configurable architecture is not evidence that arbitrary acquisition rates or lengths are in distribution. [Inference implementation](https://github.com/TorchDSP/torchsig-models/blob/main/torchsig_models/models/iq_models/xcit/xcit1d_inference.py), [architecture](https://github.com/TorchDSP/torchsig-models/blob/main/torchsig_models/models/iq_models/xcit/xcit1d.py).

CPU execution is architecturally feasible on Windows and Ubuntu because PyTorch supports both. That does not establish acceptable VM latency, RAM use, model accuracy or working package imports; these remain measurements to perform. ONNX export is a possible later experiment, not a verified portable artifact. [PyTorch platform installation](https://pytorch.org/get-started/locally/).

The repository is MIT, but reviewed release text does not explicitly establish a separate pretrained-weight license, training-data license or complete model card. Weight existence is verified; redistribution clearance and exact preprocessing provenance are unresolved. Do not bundle the weights on the assumption that MIT training code licenses all artifacts. [Repository license](https://github.com/TorchDSP/torchsig-models/blob/main/LICENSE.md).

The full package pulls Ultralytics, Anomalib, timm, TorchSig and other dependencies, and constrains NumPy below 2.4. Installing it into the existing application environment would change the validated dependency set. Ultralytics is AGPL-3.0; bundled EfficientNet material has Apache-2.0 notices. The MIT umbrella therefore does not remove transitive obligations. [Manifest](https://github.com/TorchDSP/torchsig-models/blob/main/pyproject.toml), [Ultralytics license](https://github.com/ultralytics/ultralytics/blob/main/LICENSE), [EfficientNet license](https://github.com/TorchDSP/torchsig-models/blob/main/torchsig_models/models/iq_models/efficientnet/LICENSE.md).

Decision: retain XCiT as a potential isolated CPU experiment, defer pretrained distribution until provenance is explicit, and prefer a bounded original feature-model experiment if that avoids heavyweight dependencies. Do not claim external model accuracy without evaluating the actual checkpoint on untouched RF Atlas holdout data.

## GNU Radio

GNU Radio supplies mature simulation and SDR blocks. Its Costas loop documents order-specific BPSK/QPSK carrier recovery; its symbol synchronizer documents pulse-shape, nominal samples-per-symbol and timing-error-detector assumptions. These are useful constraints when deciding whether recovery is justified. [Project overview](https://www.gnuradio.org/about/), [Costas interface](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/costas_loop_cc.h), [symbol-sync interface](https://github.com/gnuradio/gnuradio/blob/main/gr-digital/include/gnuradio/digital/symbol_sync_cc.h).

The reviewed interfaces are GPL-3.0-or-later. Do not translate or copy their implementation into MIT application code. Refer to algorithm descriptions and independently implement elementary mathematics, or evaluate GNU Radio as a separately installed external tool with a deliberate license review. Its C++/Python runtime and associated native libraries are too large to require merely for classification. Windows and Ubuntu dependency/build validation are deferred; no installation was performed. [License](https://github.com/gnuradio/gnuradio/blob/main/COPYING).

## SigMF

SigMF defines paired JSON metadata and interleaved sample datasets. Relevant fields include datatype, sample rate, specification version, capture start/frequency/time, annotations and dataset hashes. `cu8` is compatible with interleaved unsigned-byte I/Q; `cf32_le` supports float interchange. Implement an explicit subset with finite/range checks, bounded reads and rejected unsupported capture layouts. Keep the existing capture format readable. [Specification](https://sigmf.org/).

The specification repository is CC-BY-SA-4.0. RF Atlas can implement interoperable field semantics in original code while linking the specification; copying its prose or schema would require a separate attribution/share-alike assessment. Recording licenses remain independent of the specification license. Pure JSON/NumPy handling introduces no mandatory native dependency and can use platform-independent paths on Windows and Ubuntu. Test both path styles and never auto-export hardware identity or personal metadata. [Specification license](https://github.com/sigmf/SigMF/blob/main/LICENSE.md).

## Revisions inspected

Repository heads observed during this review (moving links above describe these versions):

- liquid-dsp: `10041f70cebbe3b97887e75bb41e48b73dda1b23`
- TorchSig: `1197261d4f0d4c93de18757ee2ceb368ac04104c`
- TorchSig Models: `0c30fc6579e58c01428cb11f724c7412cb9b207e`; release `v1.0.0`
- GNU Radio: `7a45079b1d42ba7b94b50f23c1414c8d2f334622`
- SigMF: `60121977f5f8d1145553251a3b3d0dbe2819aa14`; published specification `1.2.6`

Adoption status and measured RF Atlas results belong in the implementation validation report. This resource review does not claim that a proposed native adapter, model backend or full SigMF compliance has been completed.
