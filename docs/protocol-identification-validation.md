# RF Atlas 0.5.0 consolidated validation

## Implemented features and integrations

The release adds a capability registry, conservative automatic selection,
immutable input-bound decoder evidence, native ADS-B DF17 decoding, optional
external rtl_433 Fineoffset-WH2 decoding, structured protection status, additive
SQLite identification history, offline reidentification and a localhost dashboard.
DSP remains the default; ML remains optional and hybrid is not promoted.

`decoders` reports requirements and actual availability. `identify` validates a
checksummed CU8 capture, runs activity/modulation analysis and attempts compatible
decoders. Frequency and rate are hard constraints. Bandwidth, activity boundaries,
modulation and previously verified protocol history contribute ranking hints;
modulation disagreement does not exclude a valid decoder. Native work has sample,
candidate and frame budgets. External processes have time and output quotas and
process-tree cleanup. Exceptions and malformed evidence become failed attempts.
Only joint synchronization and valid integrity evidence bound to the actual input
can verify a protocol. Conflicting verified protocols produce UNKNOWN.

The protocol sample budget currently examines a deterministic prefix, not every
burst. Activity boundaries inform ranking and the modulation analysis, but do not
yet schedule all decoder windows. `--max-samples` can raise this bounded budget.
No protocol result or execution success alone establishes protection status.

`identify --store` appends an idempotent identification record for a capture linked
to an existing signal. It records engine/decoder versions, source/sample hashes,
recording reference, activity, bandwidth, hypotheses, candidates, attempt evidence
and uncertainty. UNKNOWN attempts do not erase a prior verified catalog protocol.
`reanalyze` runs cataloged captures without opening hardware and supplies prior
verified protocol as a selection hint. Schema changes add tables/indexes and preserve
the existing catalog. Spectral detection observations are unaffected.

## Repositories evaluated and license decisions

The [integration matrix](protocol-decoder-evaluation.md) covers rtl_433,
DSD-FME, SDRTrunk, readsb/dump1090 and AIS-catcher, with linked primary sources,
platform/dependency details and protocol-specific evidence caveats. No third-party
decoder source or binary is redistributed. Original code remains MIT. rtl_433 is
an optional separately installed GPL tool; the adapter restricts acceptance to the
audited WH2 model and CRC marker. It trusts an operator-selected executable's
validation, rather than independently rechecking a raw frame. DSD-FME/SDRTrunk,
general Mode S, P25/DMR/NXDN and AIS are not implemented integrations. In particular,
NMEA checksums alone would not prove AIS over-air frame integrity.

## Protocol validation and false positives

Native ADS-B requires IQ near 1090 MHz at 2–10 MS/s. Only DF17 direct parity is
supported: four-pulse preamble, 112-bit PPM structure and zero CRC24 remainder,
without repair. Tests use independently authored synthetic payloads and a separate
polynomial-long-division parity generator. No real aircraft identifiers are used.

Measured native results: 20/20 noisy frames decoded across four rates and five
sample offsets; all 1,344 single-bit corruptions rejected; zero identifications in
100 noise windows and 100 valid-preamble/random-payload invalid-CRC windows.
The noise experiment totals only 0.171 seconds; 0/100 is a finite synthetic result,
not a proven field false-positive rate (one-sided 95% binomial upper bound about 3%
per independent trial). False preambles, missing/truncated frames, incorrect DF,
constant carrier and cross-transmission checksum borrowing are also rejected.
See [native validation](native-protocol-decoder.md).

The existing Windows rtl_433 25.12 executable decoded an original synthetic WH2
PWM CU8 recording offline, with CRC evidence. A corrupted CRC, false preamble and
eight noise recordings produced no verified protocol. No receiver was opened by
this adapter. Missing tools, timeout, nonzero exit, invalid JSON, undeclared evidence
and excessive output are covered separately. These small tests do not establish
general RF reliability or every rtl_433 protocol's integrity semantics.

## Private investigation and hardware compatibility

Existing private narrowband recordings and a fresh bounded two-second recording
were analyzed locally. Channel-versus-guard activity in the original recording was
about 12%; the fresh recording was about 10%. Modulation, protocol and protection
remain UNKNOWN. ADS-B was rejected by frequency/rate requirements. WH2 was attempted
and produced no validated frame. DMR/P25/NXDN remain unevaluated by an integrated
frame-validating adapter; protocol-specific research does not count as decoding.
No frequency assignment, filename, digital appearance or failed decoding was used
to infer encryption. Raw data, identifiers and detailed acquisition evidence remain
in ignored local storage. No private RF material was sent to the Ubuntu runner.

The existing Windows V4L/manual-gain/offset-tuned 250 kS/s path recorded successfully.
Drivers, DLLs, USB and VM settings were untouched. This verifies acquisition
compatibility, not live ADS-B or WH2 reception. Linux hardware was not exercised.

## Protection model

UNKNOWN is the default, including for both implemented decoders. INDICATED requires
an explicit boolean indicator with a named protocol field and reference; it is
unverified metadata and not proof of payload encryption. NOT_INDICATED requires a
validated false indicator on a verified protocol frame. VERIFIED_BY_PROTOCOL_METADATA
requires a validated true indicator on a verified protocol frame. Even the latter
describes the decoded field and does not establish encryption of every payload.
No adapter currently supplies such a field. No decryption or key extraction exists.

## Classification references

The preserved 0.4.0 reference is 81.111% on the original 720 cases, 64.167% on the
independent 360-case holdout and 63.333% on 30 long/duty stress cases. The accepted
0.5.0 repair crops only strong, stable transmissions bordered by long quiet edges
and checks raw-window PSD before narrow CW decisions. It improves the original
benchmark to 84.722% and stress to 93.333%, while the holdout remains 64.167%.
No class precision or recall regressed on these three regression sets. A frozen
repair evaluated on a fresh 360-case seed set produced 66.389%, identical to 0.4.0;
no further tuning followed that evaluation. This is a focused duty-cycle repair,
not evidence of broader unseen-distribution improvement.
[Metric reconciliation](classification-metrics.md) distinguishes exact-label
accuracy, selective accuracy, rejection, coverage, correct-and-accepted fraction
and per-class precision/recall. Forced-choice accuracy is explicitly unavailable
because the evaluation preserves abstentions rather than inventing forced labels.
The original holdout ML 66.389% and hybrid 53.056% remain historical comparisons,
not rerun evidence of improvement in this release. Stress NFM and CW each improved
from 1/3 to 3/3 recall; weak PSK and ambiguous low-SNR inputs remain weaknesses.
The original corpus and detector are retained; saved spectra give 20/20 identical
decisions against the 0.4.0 detector.

## Dashboard and platform validation

`serve` binds only to loopback. The responsive single HTML asset provides frequency
coverage/SNR/activity overview, catalog filters, detection/recording/classification
history and decoder evidence. With the optional plots extra, startup prepares
spectrum/spectrogram PNGs for up to eight latest signal-linked captures, each
using at most 262,144 samples; these are prefix views, not continuous waterfalls.
PNG routes use opaque explicit allowlist keys. No raw capture download route exists.
Scan start/stop accepts fixed approved settings, checks ownership, launches the
existing survey CLI and enforces a whole-scan watchdog. Device locking remains in
the acquisition child. Local Host/Origin checks and session tokens guard mutations.
The server is a local investigation service, not public multi-user hosting.

Tests cover registry, availability, constraints, wrong modulation, immutable
evidence, valid/invalid frames, bounded failures/process descendants, history,
migration, replay CLI, HTTP filters/history/artifact access, CSRF/local binding and
device ownership. Windows and the existing Ubuntu 24.04 runner are exercised;
Ubuntu 26.04 remains a CI target rather than a locally observed environment.

Final validation on 2026-10-09: 118/118 full Windows tests passed, including the
actual optional rtl_433 integration; the clean public Windows snapshot passed
105/105. Ubuntu 24.04 ran 105 tests: 104 passed and the optional rtl_433 binary
integration test skipped because that binary is absent. Native decoding, registry,
HTTP, subprocess cleanup, scanner ownership and offline CLI reanalysis passed on
both systems. Installed wheel smoke checks verify version 0.5.0, both registered
adapters and the packaged HTML asset. Desktop and phone browser checks passed;
actual catalog investigation served four identification histories and a validated
880 by 550 diagnostic PNG. QA servers and browser tabs were closed afterward.

## Release readiness, commits and next stage

Only public source, synthetic tests and reviewed documentation are staged.
Privacy checks run against the staged index and wheel; private runtime trees,
captures, models and databases are excluded from both Git and source/wheel bundles.
The staged 81 public files, 37 wheel members and 86 source-distribution files had
zero publication findings; all three prior commits also passed history scanning.
These pattern checks complement manual file review and do not guarantee that every
possible sensitive value would be detected.
There are no remote pushes. The baseline is `fcf6e06`. Local implementation commit
`eea6413` adds the verified protocol engine, history, dashboard and validated DSP
repair on `protocol-identification`. A subsequent documentation commit records
this validation and its implementation hash; the final response supplies that
commit's hash. After the full suites, nine targeted Windows tests also passed
for the final configured byte-budget and explicit catalog association behavior.

Recommended next stage: independently validated DMR/P25/NXDN sync plus per-frame
integrity and protection-field adapters; burst-window scheduling across a capture;
longer interference/adversarial protocol false-positive measurement; live known
ADS-B/WH2 ground-truth sessions; improved weak-PSK/duty handling evaluated on fresh
holdout seeds; and actual Ubuntu 26.04/hardware validation. Do not broaden protocol
claims solely by recognizing executable output labels.
