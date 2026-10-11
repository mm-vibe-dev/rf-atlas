# RF Atlas 1.8.0rc1

Explore starts the existing practical smart survey with **START EXPLORING**.
The secondary Discovery profile selector offers Full Spectrum and Quick Survey.
The application starts idle unless the operator has enabled automatic startup.
One receiver remains shared by scanning, monitoring and finite investigations.

Signals shows six columns: frequency, RF type, modulation, protocol, status and
last observed time. Search includes MHz, private names and tags; visible paging
keeps every catalog row reachable in groups of 50. Sort by recent
observation, frequency, protocol or favorites. Advanced filters and batch
reanalysis are collapsed. Click a frequency for one integrated detail with
**Tune & Monitor**, **Investigate** and **Reanalyze**. Notes, evidence, waveform
measurements, history, recipes and Signal Lab remain under advanced controls.
Settings retains receiver configuration, optional decoders and privacy controls.
Existing timeline, frame, device, station and tuner links remain available.
Legacy URL hashes continue to resolve.
Each signal selection shares one detail response across the receiver and
investigation controls. Diagnostic JSON is formatted only when opened.

## RF family and protocol evidence

Blind DSP results remain in each identification's analysis document. Verified
protocol evidence supplies a separate inventory interpretation. RDS identifies
digital metadata within analog WFM; it does not convert the RF family to purely
digital. Supported digital protocols supply their declared modulation even when
blind classification abstains. The original stored hypothesis remains available
in the API and historical rows are not rewritten. Conflicting verified protocols
retain an uncertain family requiring review. User labels never verify a protocol.

The new FM path tests one bounded 200 kHz channel when the measured occupied
bandwidth exceeds 240 kHz. Existing pilot, envelope and audio-energy gates must
also hold in two disjoint halves. No allocation, station identity or decoder
label enters this blind test. The four-ADC-count quality gate remains unchanged.
This is a multiplex waveform candidate, not RDS verification or a calibrated
probability. The structural DSP classifier version is `rf-dsp-1.8.0rc1`.

## Bounded routing and weather extension

Related weather adapters share a routing group. A small documented diversity
penalty lets the finite attempt budget cover other compatible families; all
eligible adapters and skipped attempts remain visible. Verified history and
compatible waveform evidence still influence ranking. Budget limits can leave
an eligible model untried; UNKNOWN is retained without valid evidence.

The optional WH1050 adapter uses operator-installed `rtl_433` protocol 69 with
explicit offline file input, an empty configuration, bounded output and deadline.
Only weather type 5, the exact model, upstream CRC evidence and typed plausible
weather fields are accepted. Clock packets and other models are excluded.
Author-generated positive, bad-CRC and false-preamble IQ fixtures passed against
the installed executable. This establishes fixture integration, not live sensor
reception. No upstream decoder code or executable is redistributed.
Framing and integrity facts were audited against the
[upstream WH1050 decoder](https://github.com/merbanan/rtl_433/blob/master/src/devices/fineoffset_wh1050.c).
The existing WH2, WH5, WH24, RDS, DMR CSBK, AX.25 UI/F0, AIS position, ADS-B DF17
and strict POCSAG subsets retain their existing limitations. DSD-FME remains an
optional offline diagnostic: installed P25/NXDN flags do not establish verified
support. No decryption or voice-content recovery was added.

## Local alerts and privacy

Local verification alerts default off. Enable them in Settings to show new
verified channel protocols in Explore while the page is open. The initial cursor
suppresses historical alerts, repeated verification of the same channel/protocol
is suppressed, each poll reads at most 100 results, and the display retains at
most 20 alerts. Alerts contain protocol and a local signal link, with analysis
time distinguished from acquisition time. They carry no recovered content or
decoded identifiers and contact no external notification service. Frame and
identifier retention remain separately opt-in.

## Classification evidence

Seventeen historical real recordings have independent native synchronization
and frame-integrity labels: twelve RDS/WFM and five DMR. They were evaluated
previously and are regression references, not a fresh holdout. Conservative
grouping provides two protocol/session groups and no real training partition.
No AM, AX.25, noise or artifact recording received an invented truth label.

Blind family accuracy improves from 10/17 (58.82%) to 12/17 (70.59%); balanced
accuracy from 41.67% to 50.00%; macro F1 from 0.4545 to 0.5000; abstention from
41.18% to 29.41%. The two recovered cases are WFM. All five DMR references remain
UNKNOWN at the unchanged amplitude-quality gate. No digital-to-analog error was
observed in those five examples; this is not a general false-positive bound.
Mean full replay latency was 1.832 versus 1.890 seconds in the measured VM run.
Protocol-informed inventory labels are excluded from these accuracy numbers.

A fresh 216-case synthetic challenge, 72 source groups and seed base 18190000,
produced identical baseline/current family predictions: accuracy 41.67%, balanced
accuracy 55.37%, macro F1 0.4596 and abstention 40.74%. Synthetic digital-to-analog
error was 3.70%; noise-to-analog/digital was zero in this finite set. These
construction labels are not evidence of field generalization.

The frozen research ridge reached 58.33% synthetic accuracy but only 29.41% on
the real references, with 70.59% real abstention. It remains outside the app.
Model comparisons include the measured feature pipeline; the tiny vector
assembly overhead is excluded from synthetic timing. No framework/GPU dependency
or untrusted remote inference code was installed. Rechecked CVNet-RF and TorchSig
Models revisions match the previous static audits: incompatible published weight
and architecture contracts, or missing matching checkpoints, prevented valid
inference. These candidates have unmeasured accuracy, not zero accuracy.
See [Hugging Face model loading](https://huggingface.co/docs/transformers/models)
and [official TorchSig Models](https://github.com/TorchDSP/torchsig-models).

## Spectrum acquisition recovery

Finite `rtl_power` commands now have the requested duration plus a 15-second
startup margin. A timeout preserves its command diagnostics and any partial CSV
separately, then attempts the existing two-offset 2.4 MS/s floating FFT backend.
Only independently acquired, validated rows may complete the region. Missing
hardware and receiver setup failures do not become valid coverage. This path
does not change drivers, USB settings, gain or original observations. Synthetic
timeout/partial-file tests validate the recovery and provenance contract.

Live monitoring now fails after five seconds without a complete I/Q block,
including a stream that stalls after reception began. It terminates the owned
receiver process, clears transient I/Q/audio and releases the receiver lock.
An enumerated receiver is not evidence that samples are arriving. This timeout
does not repair hardware or establish successful audio continuity.

The private consolidated validation report records campaign scope, live results,
audio measurements, package/source platform tests, preservation audit and exact
local commits. Private IQ, identifiers, machine paths and detailed observations
are excluded from the public package and Git history.
