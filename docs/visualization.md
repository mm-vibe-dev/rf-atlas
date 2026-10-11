# Spectrum Explorer

RF Atlas 0.7 extends the existing local dashboard with a numeric spectrum,
drag selection/zoom, resettable peak hold, estimated noise, bandwidth markers,
frequency/date selection, unknown-protocol filtering, historical SNR,
Canvas waterfall, coverage states and sampled detection occupancy. Ten-second
polling shows completed retained measurements, with manual refresh available.
This is not continuous IQ or calibrated received power.

[uPlot](https://github.com/leeoniya/uPlot) 1.6.32 is bundled locally for numeric
and time-series charts under MIT; its complete notice is packaged as
`web_assets/uPlot.LICENSE`. No CDN is contacted at runtime.
[Apache ECharts](https://github.com/apache/echarts), Apache-2.0, provides broader
charts but adds unnecessary scope here. [inspectrum](https://github.com/miek/inspectrum),
GPL-3.0, informed the desktop analysis evaluation; no code is copied or linked.
Canvas handles the bounded waterfall without GPU dependencies. This choice is
not a measured chart-library speed comparison.

`GET /api/explorer` accepts `min_hz`, `max_hz`, ISO `since`/`until` and
`unknown=true`. Omitted frequency bounds use the latest completed scan region.
Database work has a 1.5-second progress deadline and read-only connections.
Results cap at 500 detections/history/visits, 16 raw-file candidates, four MiB of
raw spectra, 32 waterfall rows and 1,024 frequency buckets. Maximum power within
each contiguous bucket preserves narrow peaks; estimated noise uses the median.
Original FFT spacing and display width are reported separately. Missing files
and over-budget arrays remain unavailable. Waterfall rows require matching grids
and recorded acquisition settings. Peak hold resets when either changes.
Markers never fabricate measured spectra.

Coverage distinguishes NOT_SCANNED, INVALID, VALID_NO_DETECTION and DETECTED.
Markers separately expose investigation status, modulation and protocol proof.
Non-detection does not establish an unused band. Coverage is time-filtered and
bounded; unreturned intervals stay unknown. Occupancy means detected visits over
valid visits, not transmitter airtime. SNR points may represent different channels
or settings: select a narrow interval and inspect metadata before comparing dates.
Channel recurrence never proves transmitter identity. Legacy timestamps without
offsets are assumed UTC for filtering; original strings remain visible.

Investigation retains observations, hypotheses, decoder attempts, protection
uncertainty, classification history, energy-event durations, metadata and plots.
Recording actions accept catalog IDs only, resolve inside the capture directory,
validate checksums, cap browser analysis at eight MiB and serialize actions.
Replay computes diagnostics; reanalysis appends history without changing original
measurements. Trusted CLI supports the larger configured recording budget.
Browser actions provide no audio playback, raw download or receiver operation.
CLI `serve` enables them; embedded servers may omit the callback.

The server binds loopback, checks Host/Origin, requires session tokens for POSTs,
serves exact asset names, caps HTTP workers at 16 and times out idle sockets after
five seconds. Offline file/sample bounds are admission limits, not hard CPU
deadlines. Existing survey and external-decoder process cancellation is retained.

Synthetic browser QA covered 1280x900 desktop and 390x844 mobile, chart rendering,
investigation navigation, replay button wiring, frequency comparison and an empty
range. No console errors or horizontal mobile overflow were observed. The browser
replay check used an explicitly synthetic callback; Python tests exercise real
checksummed CU8 replay/reanalysis. No private RF was displayed during UI QA.
