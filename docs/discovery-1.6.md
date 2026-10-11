# RF Atlas 1.6.0rc1

This candidate adds conservative classification evidence, explicit pipeline
diagnostics and retained-recording reanalysis. Broad analog recognition and
browser audio continuity remain release limitations; 1.5 was not promoted to
stable, and neither is this candidate.

## Recognition and verification

The frozen engine, feature extractor, recovery code and original benchmarks
remain unchanged. A versioned wrapper adds bounded channel-relative envelope
and discriminator spectra. Corroborated continuous-envelope FM can become an
analog candidate; discrete FSK evidence, clipping and weak CU8 inputs retain
their existing guards. A narrow stable carrier is a carrier candidate, not
proof of CW keying. SSB, QAM and OFDM recognition is not claimed.

Results retain prior DSP evidence, algorithm version, timestamp, sample rate,
quality, bandwidth, latency and an explicitly uncalibrated score. Compatible
alternative modulation candidates can influence decoder ordering. Outside-band
context slightly lowers routing priority without excluding a decoder, preventing
broad sensor adapters from starving an in-context DMR probe. Frequency context
only scores routing; it cannot establish a protocol. Native sync/FEC/
checksum verification remains the protocol gate, including DMR Golay/BPTC/CRC
and Capacity Plus field validation. Optional external sync indications remain
diagnostic. Verified RDS is displayed as mixed content on an FM carrier.

On the frozen 360-case holdout, analog recall rose from 28.70% to 70.37% and
precision from 68.89% to 84.44%; total accuracy rose from 52.78% to 65.28%.
On the independently written 126-case holdout, DSP accuracy remained 69.05%,
analog recall 18.52% and analog precision 35.71%. These are synthetic results,
not general live accuracy. The tonal FM gain does not resolve the broader
analog release gate. See [model and dataset evaluation](rf-ml-evaluation.md).

Pipeline diagnostics distinguish missing recordings, no classification,
quality rejection, abstention, modulation candidates, unavailable or
incompatible decoders, unsuitable excluded inputs, exhausted attempt budgets,
sync failures and frame verification failures. Unsupported protocol is reported
only with explicit evidence; failure alone leaves support undetermined.
Legacy historical results derive diagnostic display without rewriting history.

## Historical reanalysis

The Signals page offers UNKNOWN-batch and selected-signal reanalysis, progress
and cancellation. It operates on registered retained CU8 recordings only;
spectrum history cannot reconstruct IQ. It never acquires the receiver.
Missing/corrupt recordings fail explicitly. Old sidecars without signal IDs use
their registered catalog association without modifying the sidecar.

Each local worker uses a separate process lease and persistent jobs/items.
Limits are 100 recordings and 900 seconds, default 600 seconds. Each recording
uses existing byte/sample/decoder budgets. Cancellation is checked between
bounded stages; an active decoder finishes its bounded attempt. Interrupted
jobs are marked on recovery. Schema 11 is additive and creates a migration
backup. Recording checksums, sample-rate/tuning metadata and algorithm, optional
binary and model hashes control cache reuse; even cached recordings and their
sidecars are integrity-checked against registered metadata. Changed
algorithms or an explicit force request create new provenance. Existing
identification runs and verified protocol fields are preserved. A conflicting
later verified protocol is appended with a conflict marker while the established
catalog protocol, modulation, protection and verification evidence stay intact.

CLI examples (native paths appropriate for the host):

```text
python -m rfatlas --config config.local.toml reanalyze --limit 100 --max-seconds 600
python -m rfatlas --config config.local.toml reanalyze --signal-id LOCAL_SIGNAL_ID
python -m rfatlas --config config.local.toml reanalyze --signal-id LOCAL_SIGNAL_ID --force
```

CLI output remains a list of per-recording results with run keys and explicit
item status/reason. Optional `--backend rfai --model LOCAL_MODEL.json` requires
an explicitly supplied, locally validated parameter file. The web interface
does not accept arbitrary model paths. DSP is the default. Optional model
failure falls back to DSP; no ML prediction verifies a protocol.

Frame and identifier retention defaults remain false. Reanalysis does not
enable them. Classification timestamps identify the new analysis; original
capture times remain attached to historical evidence.

## Console and operating limits

Filters include carrier candidates, unclassified signals and registered IQ.
Signal detail shows pipeline provenance and experimental feature neighbors.
High similarity frequently matches different families and has no identity
meaning. TUNE/MONITOR and the six receive modes remain available. Live DSP
classification runs on the existing decoder worker, with a one-million-sample
cap; receiver acquisition and audio threads do not run ML inference.

The application still owns exactly one receiver. No driver, USB, EEPROM,
bias-tee, VM, transmission, encryption-bypass or content-transcription changes
are part of this release. Private catalogs, recordings, model parameters and
campaign reports are excluded from source/wheel artifacts. Original code stays
MIT; no new mandatory package or paid service is required.
