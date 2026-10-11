# RF Atlas 0.6.0 consolidated delivery report

## Version and local commits

Version 0.6.0 continues from `8ad5e77` without replacing the application. Work is
preserved on local branch `spectrum-inventory`. Implementation commit `681e42f`
adds the inventory, investigation, protocol and analog changes. A subsequent local
documentation commit records the final validation; its hash is supplied in the
final response. Nothing is pushed or published.

## Spectrum inventory and automated investigation

Editable organizational profiles span the documented V4 tuning envelope and US
regional examples. The persistent worker schedules sequential 2 MHz-or-smaller
visits, manual gain/integration/threshold settings, coarse-to-fine detection
revisits, due times and restart recovery. Coverage distinguishes visits, valid
acquisition, detections, investigations, modulation hypotheses and protocol proof.
Overlapping profiles do not double-count the historical frequency union. Valid
coverage requires complete region checkpoints and valid, bounded raw spectral
rows spanning the requested range; a run's success label alone is insufficient.
Aligned saved spectra can be compared under compatible acquisition settings.
Original measurements remain intact. The original detector is unchanged.

The bounded investigation queue favors new, unknown, changed and intermittent
channels with sufficient measured contrast. Pending limits, a uniqueness index,
priority ordering, failed-attempt cooldowns, current profile permission checks,
cancellation, scheduler ownership and recovery after interruption are implemented.
Two-second recordings choose supported rates from estimated bandwidth and obey
existing per-file/retention budgets. The worker invokes the existing recording,
replay, DSP and decoder interfaces; source-bound evidence is stored idempotently.
Stable verified channels are not repeatedly acquired without changed evidence.
Default voice-oriented and cellular profiles do not permit live IQ capture.
See [operation and resource bounds](spectrum-inventory.md).

## Integrated protocols and verification

Implemented native integrations are ADS-B DF17 PPM/CRC24 and AX.25 Bell 202
AFSK1200 narrowband FM UI/F0 with HDLC/NRZI/bit stuffing, address structure and
CRC-16/X-25. Optional separately installed rtl_433 adapters cover audited
Fineoffset-WH2 and WH5-compatible wire formats. These four registered adapters
are implemented; other rtl_433 models are not accepted merely for printing JSON
or `mic=CRC`. Model/frequency/input requirements, compatibility, failure/time
budgets and validation references remain explicit.

New AX.25 validation: eight valid synthetic rate/offset/polarity combinations;
0/200 false verifications across valid-sync/bad-FCS, noise, CW and unrelated BPSK
inputs. Invalid addresses, control/PID, baud, truncation and oversized information
are rejected. Frame timing hypotheses are deduplicated. The earlier ADS-B tests
remain intact. Actual installed Windows rtl_433 25.12 decoded authored WH5 CU8,
rejected damaged CRC/false preamble/cross-model fixtures and eight noise inputs;
the earlier WH2 tests still pass. These finite synthetic experiments do not
establish field recall or a universal false-positive rate.

AX.25 UI/F0 does not prove APRS application semantics. New packet output excludes
addresses, message text and payloads. WH5 is an audited wire-format label with
known related-hardware ambiguity, not authenticated manufacturer identity.
[Primary-source research and license audit](packet-decoder-evaluation.md) covers
Dire Wolf/APRS, AIS-catcher and DSD-FME/P25/NXDN. AIS remains blocked on reproducible
licensed RF vectors, a trusted available binary and strict over-air CRC evidence;
NMEA sentence checksums are insufficient. DMR/P25/NXDN remain blocked on reproducible
framing/integrity output and tests. No voice transcription, decryption or key
recovery is implemented. Optional GPL tools remain separately installed; original
RF Atlas code and authored fixtures remain MIT.

## Analog characterization and classifier stability

Offline AM envelope, FM discriminator and CW beat diagnostics return aggregate
finite JSON, with no audio arrays, files or playback. Tests recover known authored
synthetic tones, reject malformed/out-of-range inputs and distinguish a supplied
mode hypothesis from a synthetic oracle result. Real recording diagnostics have
no synthetic-success claim, and protocol stays UNKNOWN. Suitable tentative analog
labels can add these diagnostics to offline identification. See
[analog evidence semantics](analog-characterization.md).

The classifier, recovery, corpus and original benchmark source remain bytewise
unchanged from 0.5.0. Original 720-case accuracy is **84.722%**, independent holdout
360-case accuracy **64.167%**, and long/duty stress 30-case accuracy **93.333%**,
exactly reproducing the prior values. Twenty saved private spectra give **20/20
identical detector decisions**. DSP remains the default; ML remains experimental
and hybrid is not promoted. Fresh weak-PSK analysis documents continuing low-SNR
weaknesses without tuning the preserved corpora. Forced-choice, selective accuracy,
coverage/rejection and correct-and-accepted metrics retain their prior definitions.

## Database and dashboard

Additive tables hold region settings, visit quality/comparisons, investigation
jobs/events and migration version. Added signal fields cover frequency uncertainty,
investigation state, classifier/decoder versions and last verification; original
power/noise/time measurements remain available. Bounded history views report
activity and energy-event duration statistics deduplicated by recording hash.
Signal entries are channel observations with compatible bandwidth association,
not inferred transmitter identities. Sharing a frequency never proves a common
sender. No original observations are discarded to manufacture deduplication.

Pre-0.6 catalogs receive a consistent private SQLite backup before migration.
The frozen original schema is tested without Git/network dependencies. Data and
indexes survive reopen; rollback uses the named backup with all processes stopped,
while retaining the newer catalog. Newer schema versions are rejected safely.

The localhost dashboard adds inventory stages/quality/profiles and investigation
priority/state/retry views, extended signal history, and survey pause/resume with
matching checkpoint settings. Scanner status exposes current region and checkpoint
progress. Inventory worker execution remains a bounded CLI action. Host/Origin/token
protections and exclusive device access remain intact. HTTP checks exercise the
actual CLI server and new endpoints. JavaScript syntax checks pass. A fresh visual
desktop/mobile check was unavailable because computer-use exposed no browser or
app; 0.5 responsive behavior is retained, but new 0.6 layouts are not visually
validated in this environment. QA servers were stopped afterward.

## Windows, Ubuntu and hardware validation

The full Windows suite runs **151 tests, all passing**, including both actual
optional rtl_433 binary tests. Subsequent targeted history/inventory/CLI tests pass.
The clean public Windows snapshot passes **138/138**. Ubuntu 24.04 runs **138**:
136 pass and the two optional rtl_433 binary integrations skip because the binary
is absent. Mandatory native decoders, scheduling, migrations, ownership, CLI and
HTTP behavior pass on both platforms.
The extracted source distribution also passes 138/138 Windows tests, including
its frozen migration fixture. The final actual-versus-requested resolution/history
changes pass another 21 targeted Windows tests.
The existing Ubuntu 24.04 runner is used without system package, VM or driver
changes. Ubuntu 26.04 remains a CI/deployment target; no 26.04 runtime is validated.

Real Windows V4 hardware acquired one bounded 200 kHz energy-only region through
the new worker with valid parsing/coverage and manual gain. Only that small region
is runtime-validated by this session; configured full-range profiles are not a
claim of a completed sweep or equally reliable reception. Live automated capture
and AX.25/WH5/ADS-B field recall are not established by this test. Drivers, DLLs,
USB, bias tee and VM configuration were unchanged.

The four existing private narrowband captures were reanalyzed locally with the
expanded registry. Modulation, protocol and protection remain UNKNOWN. Channel
activity estimates remain roughly 10–12%; both compatible audited sensor adapters
found no validated frame. ADS-B and VHF AX.25 were excluded by frequency/rate
requirements. DMR/P25/NXDN were not represented as implemented attempts. No new
private IQ/voice/message recording was made, and no private evidence was uploaded
to the Ubuntu runner or source control.

## Public readiness, limitations and next priorities

Privacy checks inspect the staged public source, wheel and source archive. Private
recordings, runtime profiles, databases/backups, logs, local paths/identifiers and
trained models remain excluded. Only authored synthetic fixtures and reviewed
documentation are distributed. Packages retain the lightweight NumPy/SciPy core
and optional plotting; no external decoder binary is bundled.
The staged 93 public files and 40 wheel members have zero publication findings.
The 98 source-distribution files also have zero findings.
All five prior commits also pass history scanning. Source packaging explicitly
includes the frozen migration fixture needed for offline archive tests. These
checks complement manual review and cannot guarantee detection of every sensitive
value.

Known limits: simple revisit/ranking heuristics; finite observation gaps; no
transmitter identity disambiguation; permission-limited capture; bounded candidate
batches and displayed history; no hard OS CPU/memory quota; admission rather than
hard wall-clock deadline; prefix-limited decoder windows; narrow protocol subsets;
unverified field recall; unavailable Ubuntu 26.04 and fresh visual browser QA.
Measurement non-detection is never a claim of unused spectrum.

Next priorities are multi-window/burst decoder scheduling, fresh licensed AIS and
digital-voice validation vectors, explicit APRS application characterization,
longer adversarial protocol false-positive tests, calibrated overload/antenna
quality diagnostics, better scheduler fairness across large edited profile sets,
actual Ubuntu 26.04/hardware validation and new desktop/mobile visual checks.
