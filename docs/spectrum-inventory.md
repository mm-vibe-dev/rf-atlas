# Persistent spectrum inventory

RF Atlas 0.6 schedules one receiver sequentially. The documented Blog V4 tuning
envelope is 500 kHz through 1.766 GHz; the existing validated backend enforces
these bounds. HF requires a V4-aware installed driver and its built-in upconverter.
See the manufacturer's [V4 release and design notes](https://www.rtl-sdr.com/rtl-sdr-blog-v4-dongle-initial-release/)
and [V4 user guide](https://www.rtl-sdr.com/v4/). These are supported tuning limits,
not proof of equal sensitivity or successful runtime reception at every frequency.
Only the already-installed receiver tools and drivers are used.

## Profiles and bounded visits

`inventory-init` writes an editable private `runtime/inventory.toml`. Twelve region
examples cover HF, VHF, UHF, upper receiver range, US 2 m amateur, aviation, marine,
land mobile, US 902–928 MHz ISM, ADS-B, satellite and cellular energy observation.
They are organizational examples, not allocation/legal advice or protocol evidence.
The first four enabled profiles partition the documented tuning envelope. Other
profiles are selected with repeated `--profile` arguments or enabled by editing
the file. `--profiles` selects another TOML file. Unknown fields and out-of-envelope
regions are rejected. Auto gain is rejected; manual gain defaults to 15.7 dB.

Each region is split into at most 2 MHz visits, respecting the existing survey
backend's sequential acquisition. Profiles configure coarse/fine bin widths,
integration/dwell times, manual gain, margin and revisit interval. A coarse visit
with detections schedules one finer revisit; normal revisits follow afterward.
Due fine work is prioritized without repeatedly refining the same visit. Regions
otherwise advance by frequency, reducing avoidable long retunes. This policy is
deterministic and deliberately simple; it is not an optimal RF sampling algorithm.

`inventory-run` defaults to one step and a 120-second admission budget, then exits.
It never runs indefinitely by default. Limits are 100 steps and 1,800 seconds per
invocation. Before starting each operation it reserves its bounded receiver and
analysis allowance; operations are not forcibly cut at an arbitrary halfway sample.
The existing process timeout and decoder work budgets remain the final bounds.
The admission deadline is a scheduling bound, not a hard operating-system CPU limit.
No hidden recurring job is installed. Repeat the same command to continue from
persisted due times and checkpoints. `--survey-only` suppresses acquisition jobs.

Profile edits disable old schedules while retaining their history. Previously
queued capture permission is checked against the active exact profile settings;
revoked or changed policies cannot silently reuse old capture permission.
One configuration admits at most 5,000 region chunks. The dashboard returns at
most 2,000 region rows and reports truncation; coverage unions still use all
historical rows. Queue admission examines a bounded candidate batch (at most ten
times the pending limit, capped at 5,000), so a large catalog may need later visits
to consider lower-ranked channels.

## Coverage and original measurements

The inventory distinguishes frequency visited, valid measurement, channel detected,
capture investigated, modulation hypothesis and verified protocol. Visited and
ever-valid frequency widths are unions, so overlapping organizational profiles do
not inflate spectrum coverage. Historical coverage includes retained prior settings;
an enabled flag identifies the currently scheduled regions. One valid sweep does
not establish complete coverage, continuous observation or an unused frequency.

Original spectra and acquisition settings remain referenced by survey runs and
scan regions. Visit history records success/failure, resolution, gain, integration
and detection-count changes. Identical power grids under matching gain/resolution/
integration can be compared by bounded median-bin power differences. Incompatible,
missing or oversized grids are reported as incomparable; no false resolution
normalization is implied. Antenna response, overload, DC spurs, transient occupancy
and weak transmissions remain limitations. Relative tool power and SNR are not
calibrated antenna power. The validated spectral detector is unchanged.

Signal entries are frequency-channel observations with uncertainty and bandwidth,
not transmitter identities. Association uses frequency proximity and compatible
bandwidth; individual original observations remain intact. Sharing a channel does
not establish a common transmitter. No callsigns, decoded addresses or sender
identities are merged into channel records.

## Investigation queue and privacy

Only profiles with `capture_allowed=true` enqueue live IQ investigations. Default
voice-oriented, amateur packet and cellular profiles are survey-only. The ADS-B
example is the only default profile that permits a bounded non-voice capture, and
it must be selected/enabled before its policy applies. Users should enable other
capture profiles only for appropriate, authorized non-voice experiments. RF Atlas
does not automatically record or transcribe private voice or decode message text.

Queue ordering favors new, unknown, changed and intermittent channels with at
least 10 dB observed spectral contrast. This is a quality hint, not proof of RF
decodability. Maximum pending work is 100 jobs by default (configurable 1–500 in
the API). One active/pending job per channel is enforced by the database. Stable
verified channels are skipped; coarse bandwidth/SNR/modulation/protocol changes
can justify another attempt. Failed/unknown attempts have a one-hour cooldown
before a later valid survey can enqueue reconsideration.

The worker alternates survey and ready investigation work, reserving an investigation
slot after two survey steps when both are available. Captures last two seconds,
select a supported rate sufficient for estimated bandwidth (2.4 MS/s for ADS-B),
and obey configured per-file and retention budgets. It does not silently delete
old evidence to make space. DSP/modulation, compatible bounded decoders and optional
analog diagnostics run offline; only validated protocol evidence updates authoritative
protocol fields. Payloads and voice audio are not output or stored by the new decoders.

`investigation-cancel <job-id>` cancels pending work or requests cancellation of a
running bounded operation. An in-progress capture can finish before cancellation
is observed; no further analysis is then started. Ctrl+C persists interruption;
receiver process trees are cleaned up by the existing OS boundary. A single
inventory scheduler lock prevents competing workers. Receiver operations also
take the shared RTL lock, so manual scans/recordings cannot conflict. On restart,
abandoned running jobs become pending with a short recovery cooldown; pending
cancellation becomes cancelled. Ordinary survey checkpoints preserve completed
subregions. Browser pause/resume operates the existing survey controller; inventory
worker execution remains an explicit bounded CLI action.

## Additive migrations and rollback

Before opening a pre-0.6 catalog, SQLite's backup API writes a consistent private
`data/backups/catalog-before-0.6-<random>.sqlite` snapshot. Migration adds region,
visit, queue, event and schema-version tables plus signal metadata/indexes, leaving
original measurements intact. Version 6 is recorded in `PRAGMA user_version` and
`schema_migrations`. A newer schema is rejected rather than downgraded silently.
The frozen original 0.5 schema in `tests/schema05.py` tests preservation without
requiring Git or a network connection.

To roll back, stop all RF Atlas processes, preserve the current database and WAL
state as a separate backup, restore the named pre-0.6 snapshot using SQLite's backup
API, then use the prior application version. This is an operator-controlled recovery
procedure; the application never deletes or overwrites a catalog automatically.
Rolling back to that snapshot omits later observations, so retain the newer catalog
for recovery. Test this procedure on a copy before using a valuable catalog.
# Continuous Auto Survey

Auto Survey is off on a fresh installation. Enabling it in the unified
application runs successive bounded inventory workers until stopped. One
worker admits at most three operations in 180 seconds; a 195-second watchdog
terminates its entire owned process tree if it does not finish. The supervisor
pauses and releases automatic acquisition before manual tuning. Failed workers
stop with an actionable status rather than continuously restarting a failed
receiver. Stop waits for the worker tree and background watcher to exit.

Quick Survey selects editable US-oriented organizational regions for AM/FM,
aviation, marine, amateur, land-mobile, ISM, ADS-B, satellite and weather. Their
names are frequency context, not protocol evidence. Full Spectrum selects the
four contiguous documented receiver-envelope profiles from 0.5 to 1766 MHz.
This is a nominal tuning envelope, not proof of sensitivity or valid reception
at every frequency. The [manufacturer's V4 description](https://www.rtl-sdr.com/rtl-sdr-blog-v4-dongle-initial-release/)
documents the tuning range; actual received quality is checked per measurement.
Smart Survey adds unknown modulation/protocol, incomplete investigation and
intermittent-activity priorities. Every third persisted measurement chooses
the least visited due region, so a busy known region cannot indefinitely
exclude exploratory visits. Smart defaults include full-envelope exploration
plus the ADS-B profile, whose separate explicit capture permission permits
bounded investigations. Default voice and cellular profiles remain energy-only.

The existing `runtime/inventory.toml` remains the editable profile source.
The controller resolves the selected settings into private
`runtime/auto-survey.toml`; profile identities and SQLite scheduling state
preserve coarse/fine checkpoints, revisit deadlines and investigation history
across child and application restarts. Editing acquisition settings creates a
new schedule identity while preserving the original observations. Full Spectrum
retains the canonical range boundaries even if an editable full-range profile
was narrowed. Gain, integration and revisit settings remain configurable.

Progress counts visited regions, valid regions and repeat visits separately.
Neither a completed worker nor one valid pass means fully characterized
spectrum. An unvisited region remains unobserved, and a valid measurement with
no detected activity remains only a measurement under its recorded settings.
