# RF Atlas 1.1 discovery console

Version 1.1 adds an original local discovery interface to the existing single-receiver application. Live shows observed activity, evidence-based categories, recent detections, receiver operations and measured host health. Tuner, recording/replay, SQLite, DuckDB and decoder evidence remain available.

## Design and changed files

The [SIGINT repository](https://github.com/arall/sigint), [reference screenshot](https://github.com/arall/sigint/blob/main/docs/images/web-ui.png), web documentation and implementation were inspected before implementation. Its compact navy header, bordered panels, status strip and dense tables informed the layout. RF Atlas code and styles are original; no reference source or assets are distributed. No top-level license was observed in the reference tree during inspection.

- `rfatlas/discovery.py`: consistent read-only SQLite snapshots, UTC/session filters, category counts, bounded events, comparisons, timeline and portable health sampling.
- `rfatlas/dashboard.py`: discovery endpoint and asset registration.
- `rfatlas/runtime.py`: application uptime/version, capabilities, retention limits and survey pause/resume; measured spectrum boundaries and one-bin tolerance for rounded outer edges.
- `rfatlas/web_assets/index.html`, `console.css`, `console.js`, `radio.js`: eight navigation views, discovery landing page, sessions, category sorting/search, recent-event filters, investigation links and responsive layout.
- `tests/test_discovery.py`, `test_runtime.py`, `test_radio_ui.py`: scope/count/health, edge-rounding and navigation checks.
- Package version, README, radio-interface documentation, manifest and documentation allowlist updated.

## Behavior and interpretation

Live defaults to a rolling 24-hour window across retained sessions. Current application session includes observations inserted after dashboard startup, using the initial observation ID as a boundary. This avoids excluding new rows because of legacy receiver clock offsets. Previous sessions select a scan run ID. Aware timestamps normalize to UTC; intervals include the start and exclude the end. Legacy naive observation timestamps are treated as UTC without conversion; if the original tool used a local wall clock, historical time windows can be offset. Current-session membership is independent of this assumption. Selection and comparison only query the database and never retune the receiver. Category search filters the category table; clicking a category filters the recent feed. Comparison reports raw count differences under the same window, without exposure normalization.

Detections mean stored observation rows; unique signals mean catalog channels, not physical transmitters. Categories use current retained protocol evidence for observations in the selected window, without reconstructing historical classification state. ADS-B maps to Aircraft, AX.25 to Packet Radio, supported Fineoffset protocols to Environmental Sensors. Other verified protocols and unknown evidence remain separate. Frequency alone never creates an identity or category. No unsupported maritime or trunked-radio decoding is claimed.

Devices and Stations retain their existing verified-identifier inventories and filters. Their header counts explicitly cover all retained identifiers independently of the observation window. Events attach identifiers only when capture and observation timestamps match; later associations remain in investigations. UNKNOWN events show measurements and hypotheses without fabricated hex. Decoder preserves retained bytes, 16-byte offsets, ASCII, synchronization and CRC/FCS evidence.

Quick, Full and Smart use the existing scheduler and receiver lock. Start/Pause/Resume/Stop invoke explicit runtime actions. Coverage and investigation counts describe current runtime state even while history is selected. Checkpoint visits do not establish continuous monitoring or silence. The recent feed contains at most 60 rows in a 480-pixel scroll area; the API permits at most 200. Visible-page snapshots refresh every three seconds with a query deadline and stale-update message on failure. Timeline shows at most 288 occupied five-minute bins; gaps are unobserved rather than zero activity.

CPU/RAM describe the host, disk describes the runtime volume and recording usage includes metadata/partial files. CPU requires two samples; missing readings remain unavailable. Recording enumeration stops at 10,000 files and reports unavailable/truncated instead of a partial total. Windows uses system APIs; Linux uses procfs. Health samples cache for three seconds. Receiver status is the last probe/acquisition result, not a continuous USB presence monitor.

Live tuning permits up to one measured FFT bin of rounding at each outer requested edge. Internal gaps and larger missing coverage still fail. Exact measured bounds are returned by the API; plots use measured frequency coordinates. Relative power is not calibrated dBm.

## Validation

Validation ran on Windows 11 and a separate Ubuntu 24.04 host using Python 3.12. Only privacy-checked public source and the wheel were sent to Ubuntu.

| Check | Result |
| --- | --- |
| Windows full suite, installed optional rtl_433 configured | 209 passed |
| Ubuntu public-source suite | 196 run: 193 passed, 3 skipped |
| Ubuntu installed 1.1 wheel | Version, bundled console assets and actual procfs/disk health passed |
| Local source and wheel publication checks | Zero findings |
| JavaScript syntax and diff whitespace checks | Passed |

Ubuntu skips are two optional rtl_433 positive cases because the executable is absent and one Windows Job assignment case. Public-source counts exclude the existing ignored local validation tests. No Ubuntu receiver or Ubuntu 26 environment was available.

Headless Edge rendered the actual Windows application at 1440 pixels and at a 390-pixel mobile viewport. Header counts were checked against the matching API scope; all eight views, current-application and previous-scan selection, comparison, category sorting/filtering, investigation navigation and timeline rendered successfully. Historical selection left acquisition idle. Mobile page width matched the viewport; long tables scroll internally. No JavaScript errors were recorded.

The actual receiver passed browser Quick Survey start, pause, resume and stop. Manual tuning produced completed live measurements and advancing sequence numbers after the outer-edge rounding correction. Cleanup returned Auto Survey and Tuner to stopped/idle. This verifies acquisition and controls, not any received protocol identity.

An isolated QA catalog used generated CRC/FCS-valid ADS-B and AX.25 frames, plus an explicitly mocked external sensor event. It verified multiple category counts, category search, verified station/device inventories, event investigation links, recovered 16-byte hex offsets and ASCII. The mocked sensor event has no raw bytes; Decoder correctly shows unavailable bytes instead of invented hex. These fixtures were never added to the operational catalog.

Desktop and mobile captures were opened and visually inspected against the SIGINT reference. The finished console uses its compact header/status/table arrangement with RF Atlas-specific receiver controls and evidence labels. Local screenshots are in ignored `reports/1.1-ui`: `live-desktop.png`, `live-mobile.png`, `timeline-desktop.png`, `scanner-running.png`, `live-tuner.png`, `synthetic-categories.png` and `synthetic-decoder.png`. They remain local because operational screenshots contain the local signal inventory.

## Limitations

One receiver only; no simultaneous full-spectrum view, transmission or decryption. This interface adds no decoder protocols. Historical categories use current catalog classification; comparisons are not dwell-time normalized. Identifier counts do not establish distinct physical transmitters. Timeline is a bounded observation summary, not a continuous spectrogram. Large catalogs may hit the query deadline and show an unavailable snapshot. Ubuntu hardware was not attached; Ubuntu 26 validation is not claimed.
