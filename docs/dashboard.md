# Local dashboard

The RF Atlas dashboard uses Python's standard HTTP server and a single HTML
asset. It needs no JavaScript framework, build service, account, or network
dependency. It binds only to `127.0.0.1`; public binding is rejected. It is a
local investigation tool, not a production multi-user service.

The spectrum overview shows cataloged frequencies, bandwidth, relative SNR,
activity fraction and recent scan coverage. The catalog supports frequency,
protocol, analog/digital, known/unknown protocol, and text filters. Catalog rows
open classification evidence, detection timestamps, event history, recording
metadata and protocol investigation results. Known protocol means the catalog's
protocol is other than UNKNOWN; modulation alone does not make a protocol known.
Relative SNR is not calibrated received power. Activity estimates retain the
meaning of their source analysis and are not independently measured by the UI.

Investigation plots are optional. Only diagnostic PNGs explicitly registered by
the local application are served. The dashboard never accepts a filesystem path
from the browser and never serves raw recordings, SQLite databases, runtime
configuration or arbitrary files. Recording metadata is limited to acquisition
and sample identity fields; device identity, tool paths and raw process output
are omitted. Additional investigation callbacks must also avoid returning raw
logs, private configuration and absolute paths.

## Application interface

`rfatlas.dashboard.create_dashboard_server(root, host='127.0.0.1', port=0,
controller=None, investigate=None, artifacts=None, inventory_view=None,
queue_view=None)` creates a server. The caller
owns `serve_forever()`, `shutdown()` and `server_close()`. Port zero selects an
unused local port. The HTML asset must be included as package data.

The optional controller provides `status()`, `start(params)`, `stop()`, `pause()`
and `resume()`, each
returning JSON-safe dictionaries. Status includes `active`; `supported=False`
disables controls. Start receives numeric `start_hz`, `stop_hz`, `bin_hz`,
`seconds`, `duration`, and numeric gain or the string `auto`. Unknown parameters,
nonfinite numbers, out-of-range settings and durations over 300 seconds are
rejected. Optional `profile` selects the existing quick, balanced or deep survey
profile. The UI fills editable resolution/integration settings for that profile;
manual gain remains the default. The application controller must enforce its configured narrower bounds,
actual supported gains, hardware availability, and the shared device lock. The
HTTP action lock prevents concurrent dashboard starts; it does not replace the
cross-process hardware lock. Missing tools and device conflicts produce bounded
errors and leave the server usable. The dashboard does not open an SDR itself.

The supplied `ScannerController` runs the existing survey command as a bounded
child process. It validates the receiver's enumerated gains, probes the shared
device lock, and leaves actual ownership to the survey child. A competing CLI
process can win the probe/start race; the child then fails safely at its own lock.
The total scan budget limits region count times duration plus estimated switching
overhead to 300 seconds; a watchdog adds 60 seconds and stops the child if needed.
Windows uses a kill-on-close Job Object and hidden process creation. Linux uses
a new process group and the existing parent-death guard. A stop forcibly ends the
owned process tree. Pause also ends the owned tree, retaining the validated
acquisition settings. Resume omits `--new`, allowing the ordinary survey command
to reuse completed matching region checkpoints and repeat only unfinished
regions. Stop clears the in-memory paused state. After a dashboard restart the
same survey parameters can be resumed through the ordinary CLI. Pausing is not
sample-continuous reception: transmissions during the pause can be missed.

Scanner status includes the selected resolution profile, completed/total region
counts, and the next unfinished region while running. This region is labeled
"next or in progress": the database checkpoint cannot provide an exact current
tuner frequency. Completed-region fraction is progress of a bounded sweep, not
completeness of signal discovery. Status reads use a fresh read-only connection.
The UI does not show subprocess output, serial numbers or configuration paths.
When no configuration filename was supplied, the controller writes a private
temporary TOML snapshot below runtime and removes it when acquisition ends.

The optional `investigate(signal_id)` callback returns identification history.
For example, `{'identifications': [...], 'artifacts': [{'id': 'spectrum',
'label': 'Spectrum'}]}`. `artifacts` maps these opaque IDs to already approved
local PNG paths. Returned images are limited to 16 MiB each.

`inventory_view()` and `queue_view()` inject the persistent inventory service
without importing hardware logic into the HTTP server. The inventory view shows
configured region profiles, coverage stages, and measurement quality. The queue
shows pending/active/completed/failed/cooldown entries, priority, attempts and
next eligibility. Full bounded service details remain available in expandable
evidence panels. These callbacks must return bounded JSON without private paths,
device identities or raw subprocess output. Missing callbacks return explicit
unavailable status while other routes remain usable. The dashboard does not
launch the autonomous inventory worker; its bounded CLI owns that scheduling.

## HTTP interface

| Method and path | Purpose |
| --- | --- |
| GET `/api/overview` | Signal count, up to 100 recent coverage regions and 200 recent spectral observations |
| GET `/api/signals` | Filtered catalog; `min_hz`, `max_hz`, `protocol`, `signal_class`, `known=true/false`, `q`, `limit`, `offset` |
| GET `/api/signals/{signal_id}` | Current classification and bounded histories; optional injected identification evidence |
| GET `/api/scanner` | Acquisition availability and status |
| GET `/api/inventory` | Region profiles, persistent staged coverage and measurement quality |
| GET `/api/investigations` | Bounded investigation queue and state counts |
| GET `/api/session` | Per-server random request token |
| POST `/api/scanner/start` | Start an approved bounded scan |
| POST `/api/scanner/stop` | Request stop; JSON body `{}` |
| POST `/api/scanner/pause` | Stop owned survey and retain checkpoint-resume parameters; `{}` |
| POST `/api/scanner/resume` | Resume matching unfinished survey without `--new`; `{}` |
| GET `/artifacts/{id}` | Explicitly allowlisted diagnostic PNG |

Mutation requests require JSON and the `X-RFAtlas-Token` header from the session
endpoint. Every request checks loopback address and an exact localhost Host with
the server's port. Supplied Origin must match the local server; cross-site fetches
are rejected. No CORS permission is issued. Responses disable caching and include
content type, frame, referrer and resource restrictions. Client-rendered evidence
uses `textContent` rather than inserting HTML.

All database reads open fresh read-only SQLite connections. Query values are
bound parameters. API pagination is capped at 1000 rows per page; the browser
shows the first 200 matching rows. History views are bounded to avoid loading a
whole catalog at once. The overview uses catalog history rather than a live FFT
stream. Live waterfall streaming and browser-triggered recording/reclassification
are not implemented. The UI displays supplied spectrum/spectrogram PNGs.

## Validation

`python -m unittest discover -s tests -p test_dashboard.py -v` exercises real
local HTTP requests against temporary synthetic SQLite catalogs. Coverage includes
search/filtering, investigation history, metadata redaction, missing catalogs,
parameterized SQL, invalid filters, origin/Host/token rejection, bounded scan
settings, concurrent start conflicts, controller device conflicts, unavailable
tools, start/stop, optional controller, artifact traversal rejection and public
binding refusal. No real hardware or captured communications are used.
`test_scanner.py` adds fixed CLI argument validation, total scan budget checks,
shared lock ownership conflicts, missing tools, private snapshot cleanup,
completion/failure status and watchdog behavior with fake child processes.
The 0.6 additions exercise injected inventory and investigation queue routes,
optional service absence, profile rejection, authenticated pause/resume controls,
exact resume settings without `--new`, paused-state cancellation, and matching-run
checkpoint progress. The tests use synthetic data and fake children only.
