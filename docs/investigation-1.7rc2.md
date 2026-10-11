# RF Atlas 1.7.0rc2 investigations

Deep investigation adds measured signal diagnostics and an auditable plan around
the existing DSP classifier and audited offline decoders. It does not promote a
waveform guess to a verified protocol. Synchronization and joint frame integrity
remain necessary for verification; an unsupported or inconclusive result stays
UNKNOWN.

## Run an investigation

Select a signal in the Discovery Console or catalog. **DEEP INVESTIGATE NOW**
performs a finite, fresh acquisition, then analyzes it. Stop other acquisition
first. Choose a generic or compatible recipe and a short, medium, targeted or
burst-watch acquisition. Cancellation stops acquisition promptly and is checked
between finite decoder stages. The receiver lock is released after acquisition,
including failures and cancellation. Retained-recording reanalysis runs without
the receiver and avoids unchanged recordings and algorithms by default.

The `identify` and `reanalyze` CLI paths use the same measured investigation
pipeline. `inventory-run` and Auto Survey invoke it automatically through their
existing investigation queue. Automatic acquisitions now use transient memory;
they do not add raw recordings to the captures table. Explicit user recording
commands retain their existing behavior. Frame and identifier retention remain
independent operator settings, disabled by default.

Existing offline external adapters may create bounded temporary IQ or
demodulated WAV inputs for their tools. Their temporary directories are removed
after each attempt; these inputs are not added to the retained capture catalog.
The in-memory acquisition path does not itself write raw IQ files.

Manual investigation API: `POST /api/runtime/deep/start`, with `signal_id` and
optional `recipe`, `strategy`, `gain`, and `sample_rate`. Cancellation uses
`POST /api/runtime/deep/cancel` with `{}`. Runtime status includes progress and
the last privacy-filtered result. Existing loopback, session token and origin
checks apply. `GET /api/investigation-recipes` describes the available plans.
Browser requests supply catalog IDs, never file paths or executable commands.

## Measurements and evidence

Investigation history records acquisition quality, a finite characterization,
decoder eligibility, every attempted decoder's elapsed time, synchronization,
frame validation, failure reason, and the next evidence that could help.
Triage is an investigative status, separate from verified protocol identity.

Measurements include occupied bandwidth, centroid offset, peak-to-PSD-floor
contrast, amplitude statistics, spectral flatness/entropy, phase increment
coherence, instantaneous-frequency quantiles and block variation, and finite
capture activity/burst intervals. These are uncalibrated measurements.
Frequency variation includes modulation and noise; it is not a calibrated
carrier-drift or deviation measurement. Activity outside monitored windows is
unknown. Timing/fingerprint similarity does not establish transmitter identity.
Entropy never establishes encryption.

Clean rectangular OOK/ASK and 2/4FSK may produce an approximate run-duration
symbol-rate candidate. Its residual, transition count and resolution are shown.
Integer harmonics, shaping, noise and analog crossings remain alternatives.
No arbitrary bits or message content are stored. Generic PSK/GMSK/QAM timing,
equalization, eye diagrams and synchronized constellations are not implemented.
Existing structural PSK/OFDM hypotheses remain tentative and unchanged.

The versioned decoder registry describes actual adapter subsets, rate limits,
bandwidth context, dependencies, verification criteria, resource limits and
failure conditions. Unknown minimum-duration or metadata requirements remain
explicitly unknown. Frequency context ranks candidates; native decoders do not
require a known allocation to examine compatible IQ. Mode S offline framing now
also accepts frequencies outside its normal band hint. Existing strict DMR,
Capacity Plus CSBK metadata, RDS, AX.25 UI, AIS position, ADS-B DF17, POCSAG and
supported rtl_433 subsets remain available. Optional DSD-FME is an offline
diagnostic adapter, not a verified P25/NXDN implementation. P25, NXDN and the
other unimplemented protocols are not advertised as working decoders.

Recipes prefer compatible decoders while preserving generic fallback. Selecting
a recipe is not proof of its protocol. Operational feedback gives at most one
routing point to a modulation-compatible decoder after three historical runs
with joint frame-integrity evidence. Predictions and frequencies do not earn
that bonus. Its effective state participates in the reanalysis cache key.

## Acquisition and budgets

Default short/medium/targeted windows are 2/8/20 seconds; the maximum is 30.
Only the latest four million complex samples are retained in memory for
analysis. Acquisition duration, analyzed duration, prefix offset, and stored
duration are reported separately. Stored raw duration is zero. Decoder attempts
are sequential, normally at most four, with a cooperative 30-second decoder
budget. A running finite native attempt may finish after that budget; this is
not a hard process deadline or an operating-system memory quota. Characterization
and receiver setup add time. No indefinite acquisition or decoding is allowed.

Burst watch uses a bounded ring and a relative whole-band energy increase after
an initial 0.25-second baseline. It retains preceding samples and a bounded
post-trigger window. It can miss continuous activity, weak narrow bursts, or a
burst during the baseline. Longer observation cannot guarantee a full frame,
and a high-rate ring may contain less time than the observation window.

## Signal Lab

**SIGNAL LAB** displays a checksummed retained recording prefix: spectrum,
waterfall, mean amplitude, median instantaneous frequency, and circular mean
phase increment. At most 65,536 samples, 512 spectrum bins, 128 waterfall rows,
and 256 time blocks are plotted. The full recording digest is checked using
streaming reads without loading the whole recording for plotting. Input
representation and limitations are visible. No decorative constellation is
shown. Signals without retained IQ retain their investigation measurements;
their raw plots are explicitly unavailable. Tune & Monitor and its existing
reception preset controls remain available alongside this view. Save Preset stores
the selected investigation recipe and strategy for this signal without starting
acquisition. It is restored when that signal is opened again.

## Reproduce diagnostic validation

Run `python tools/evaluate_investigation.py OUTPUT.json` once after freezing
the implementation. It evaluates 63 fresh synthetic cases in 21 source groups
through the blind DSP classifier and timing diagnostics, including analog,
carrier and noise negatives. SNR variants share their source group. The output
records source hashes, construction truth, abstentions, timing error, latency
and Python peak allocation. This is not independent field accuracy.

The DSP classifier remains the baseline. RF model candidates must have matching
weights, architecture, documented IQ preprocessing and suitable provenance
before benchmarking. Third-party RF model cards and synthetic accuracy do not
establish field performance. Prerelease status remains appropriate until the
broader classification and operational requirements are met.
