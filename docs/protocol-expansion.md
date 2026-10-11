# External decoder evaluation: 0.7.0

Reviewed primary upstream source on 2026-10-09. No external decoder, source code, private IQ or third-party capture is bundled. RF Atlas retains its MIT license. Optional subprocess tools retain their own licenses; redistribution requires a distribution-specific dependency audit.

RF Atlas currently verifies native ADS-B DF17 and the AX.25 UI/F0 AFSK1200 subset, plus optional rtl_433 WH2/WH5 subsets. AIS is **not** an existing RF Atlas implementation. No additional verified protocol is claimed in 0.7.0. Band membership, modulation hypotheses, synchronization alone, JSON presence and successful process exit remain insufficient evidence.

## xng

Pinned review: [airframesio/xng 096c805](https://github.com/airframesio/xng/tree/096c805278faa0a904de7d98066bf4cf395cb6c2). The [README](https://github.com/airframesio/xng/blob/096c805278faa0a904de7d98066bf4cf395cb6c2/README.md) documents ACARS, VDL2, HFDL, ADS-B, AIS and satellite modes, with substantially different validation maturity. It advertises Linux/macOS releases, not a Windows release. Rust and protobuf compilation are required; an offline build can disable default SDR features. Stock release binaries can still require SoapySDR libraries. Upstream performance numbers are not RF Atlas measurements. Licensing is MIT or Apache-2.0; per-core provenance and dependencies still need review before redistribution.

The [actual CLI source](https://github.com/airframesio/xng/blob/096c805278faa0a904de7d98066bf4cf395cb6c2/src/main.rs) defines file-only `decode`, explicit `--format` choices `cf32`, `cs16`, `cs8`, `cu8`, sample-rate and center-frequency options, `--mode`, channel selection and `--json`/`--jsonl`. This is a real offline interface, not a proposed stdin protocol. An eventual adapter should write a bounded temporary IQ prefix, specify every acquisition parameter explicitly, choose a single audited mode and omit every feed/network/output-server option.

The [message model](https://github.com/airframesio/xng/blob/096c805278faa0a904de7d98066bf4cf395cb6c2/crates/xng-types/src/message.rs) includes a boolean final-integrity result and optional correction/error counters. It also carries raw and decoded payloads, identities and positions. A single generic integrity field cannot establish mode-specific synchronization, correction policy or the validity of a particular protocol frame. An adapter must audit the precise mode's demodulator-to-message path, reject malformed/unrelated records and retain only structural counts. Uncalibrated received-power fields must not become dBm.

No local xng executable, Cargo toolchain or tested Windows build was found. We did not install a toolchain or system SDR libraries. A Linux-only binary probe would not close the Windows and independently generated valid/corrupt RF-vector gates. Consequently no untested adapter is registered. Highest-value next candidate: a pinned, file-only AIS subset with valid-sync/bad-FCS, wrong-mode, noise and truncation tests, followed by ACARS or VDL2. Existing ADS-B need not be duplicated. Satellite payload, credential extraction and decryption features are outside this integration.

## GopherTrunk

Pinned review: [MattCheramie/GopherTrunk 21549b3](https://github.com/MattCheramie/GopherTrunk/tree/21549b33b5ec862def717c8a8615af7daaa75108). Its [README](https://github.com/MattCheramie/GopherTrunk/blob/21549b33b5ec862def717c8a8615af7daaa75108/README.md) describes Go-based cross-platform scanner binaries, DMR/P25/NXDN and other trunking families, with audio and optional external output services. Code is Apache-2.0, subject to its component notices and separately stated terms. No executable or Go compiler was found locally. No Windows installer or bundled driver was run.

The [hunt documentation](https://github.com/MattCheramie/GopherTrunk/blob/21549b33b5ec862def717c8a8615af7daaa75108/docs/hunt.md) gives genuine offline `hunt -in ... -freq ... -format u8 -sample-rate ... -out ...` processing. Its output is a discovered system/site/channel map, with CSV import, trunk-recorder configuration and submission-oriented exports. DMR Tier III and NXDN identity learning is described; this does not establish a tested RF Atlas Capacity Plus decoder. File input can avoid direct hardware ownership, but RF Atlas would still require a finite execution/output budget and an isolated working directory. Automatic daemon launch, voice following, online lookup and submission are not part of this work.

The [bundle exporter](https://github.com/MattCheramie/GopherTrunk/blob/21549b33b5ec862def717c8a8615af7daaa75108/internal/hunt/export_bundle.go) emits aggregate metadata, sites and talkgroups. Those fields do not supply the per-frame synchronization/integrity accounting that RF Atlas's contract requires. Importing an aggregate protocol label as verified would weaken that contract. Integration is deferred until an exact, versioned evidence interface and author-generated valid/corrupt control-channel fixtures are demonstrated. No runtime or protocol-accuracy measurement is claimed.

## DSD-FME

Pinned review: [lwvmobile/dsd-fme 4fe32db](https://github.com/lwvmobile/dsd-fme/tree/4fe32db7f5affb0484aa0e4f8bbc50eabe3c8531). The [source help](https://github.com/lwvmobile/dsd-fme/blob/4fe32db7f5affb0484aa0e4f8bbc50eabe3c8531/src/dsd_main.c) describes discriminator WAV input at 48/96 kHz mono, audio/RTL/TCP input routes, P25/DMR/NXDN mode switches and optional audio/data-file outputs. Raw complex CU8 cannot simply be supplied as discriminator WAV. The same source exposes switches that relax CRC rejection; such switches must never be used for protocol verification.

The installed portable Windows executable completed an isolated five-second-budget `-h` probe; bounded help output was retained locally. This proves executable availability only. No recording, hardware input, voice output or decoder-accuracy trial was performed. Console synchronization strings and protection labels lack an audited structured, frame-bound evidence mapping. A future adapter needs independently checked IQ-to-discriminator conversion and valid/corrupt frame vectors before any DMR/P25/NXDN or protection result can be accepted.

The [COPYRIGHT](https://github.com/lwvmobile/dsd-fme/blob/4fe32db7f5affb0484aa0e4f8bbc50eabe3c8531/COPYRIGHT) identifies permissive original code, specifically GPL-2.0 files and incorporated projects. It must not be represented as uniformly MIT. No source, portable Cygwin runtime or binary is redistributed. No keys, decryption, automatic voice capture or playback are implemented.

## Resource and failure policy

The existing optional-decoder runner accepts an explicit argument vector, never shell text, limits each invocation to at most 30 seconds and aggregates stdout/stderr under one MiB. It spools output, terminates process trees and suppresses payload-bearing error text. Disk output may transiently exceed the polling quota. New integrations must use that runner, add bounded temporary input and cleanup, remain unavailable without their optional dependency and operate on retained recordings rather than acquire an independent hardware owner. These existing safeguards are prerequisites, not evidence that the deferred tools have passed integration tests.

## Release-candidate refresh: additional optional tools

Primary upstream documentation was rechecked on 2026-10-09 for the continuous
single-receiver application. The pinned xng, GopherTrunk and DSD-FME evaluation
above still describes the integration gates; no new adapter for these tools is
registered. Current [xng documentation](https://github.com/airframesio/xng)
continues to advertise Linux/macOS binaries and offline replay, and current
[GopherTrunk documentation](https://github.com/MattCheramie/GopherTrunk)
continues to state Apache-2.0 plus explicit first-run terms acceptance. Neither
README is evidence that RF Atlas has validated those protocols on Windows.

| Tool | License and real input/output route | RF Atlas decision |
| --- | --- | --- |
| rtl_433 | GPL-2.0; Windows/Linux portable C; explicit raw-file input, acquisition rate/frequency and JSON output | Existing optional WH2/WH5 adapters remain narrowly audited. Additional models require independently valid/corrupt RF vectors and model-specific integrity review. |
| AIS-catcher | GPL-3.0; cross-platform complex-file input and NMEA/JSON output | Deferred optional adapter. NMEA sentence checksum alone is not recovered RF-frame FCS evidence. |
| Dire Wolf | GPL-2.0-or-later; Windows/Linux audio modem; `atest` accepts PCM WAV offline | Existing native AX.25 remains preferred for the supported subset. Wider APRS/packet modes need a tested IQ-to-audio path and a frame-specific integrity interface. |

The [rtl_433 README](https://github.com/merbanan/rtl_433/blob/master/README.md)
documents rate selection, file processing and JSON, and describes Windows and
Linux support. RF Atlas supplies an empty configuration and explicitly selects
its audited decoder, preventing a user's default configuration from enabling
network feeds or unrelated decoder output. Its tool is optional and never owns
the receiver during file decoding.

The [AIS-catcher README](https://github.com/jvde-github/AIS-catcher/blob/master/README.md)
documents diagnostic quality bits, including checksum and length flags, and
warns that default filtering need not reject flagged messages. Plain NMEA
cannot preserve those flags when sentence checksums are regenerated. A future
adapter must pin a version, retain and validate the RF integrity indicators,
bound file input/output, disable web/network sharing, and test valid-sync/bad-FCS
frames, noise, truncation and malformed JSON. No AIS-catcher binary is bundled
or executed for this release; no AIS accuracy claim follows from this review.

Dire Wolf's [actual `atest` source](https://github.com/wb2osz/direwolf/blob/master/src/atest.c)
reads 8/16-bit PCM WAV with the file's sampling rate and exposes bit-rate,
demodulator-profile and hexadecimal-output settings. Its source initializes
bit correction off but also offers a correction option. The
[project README](https://github.com/wb2osz/direwolf)
identifies Windows/Linux support and AX.25/APRS functionality. RF Atlas would
need explicit mono audio conversion and disabled correction, without using a
soundcard, transmitter, IGate, network feed or private upstream test recording.
Console hex alone is insufficient to satisfy the decoder evidence contract.
No external Dire Wolf executable has been validated or bundled here.

These separately installed optional programs do not alter RF Atlas's MIT
original-code license. Any future redistribution must audit all binary
dependencies and provide the applicable notices and source obligations.
