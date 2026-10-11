# Radio interface

RF Atlas 1.1 keeps eight sections in a local, dependency-light page. Tuner controls submit validated runtime requests; the instrument readout and plots use completed receiver measurements. Requested span and actual bin spacing are distinct. `rtl_power` does not report an IQ sample rate, so the interface says **not reported** rather than guessing a sample rate from span.

The live waterfall retains at most 32 completed measurement rows. Peak hold resets when the frequency grid changes or when the user resets it. The horizontal axis supports click-to-tune; direct entry, preset frequencies, and tuning steps use the same backend action. Gain and receiver limits come from runtime capabilities. Relative power is not labeled dBm. The Tuner deliberately leaves protocol and modulation UNKNOWN: a spectral plot alone does not verify either.

Live exposes Auto Survey and its actual scheduler state separately from the existing bounded scan controls. Quick, Full and Smart modes share backend receiver ownership. Settings persist startup behavior, automatic-mode restoration and local evidence retention. Acquisition is off on a fresh installation. Raw frame retention can contain identifiers and message content even if parsed identifier retention is disabled.

Decoder shows recovered frame evidence and a conventional 16-byte hexadecimal/ASCII view only when actual decoder bytes were retained. Synchronization and CRC/FCS evidence are shown separately. Candidate and invalid frames have distinct labels. Frame history and original signal characteristics remain accessible. Devices and stations are separate evidence-backed lists with links to signal investigations; frequency reuse alone cannot establish identity.

Signals preserves the existing catalog and investigation details. Timeline preserves Spectrum Explorer, retained measurements, occupancy, coverage and uncertainty. The responsive layout collapses instrument metrics and panels on narrow screens; long tables and hex rows scroll horizontally. All dynamic text uses DOM text nodes or `textContent`, including decoded payloads. No remote scripts, telemetry, maps or CDNs are loaded.

Validation includes JavaScript syntax checks, dashboard HTTP/security integration tests and frontend navigation/retention contract checks. Actual browser and hardware validation must be reported separately from these checks.

Headless Edge browser QA on Windows exercised all seven navigation sections, missing-tool receiver checks, accurate direct frequency entry, decoded synthetic ADS-B and AX.25 frame rendering (including 16-byte offsets), local retention defaults and a 390-pixel mobile viewport with no horizontal page overflow. No JavaScript errors occurred. The QA catalog was isolated from user data. Screenshots remain in ignored local reports. Hardware dashboard checks are described in the consolidated release validation.

During a separate read-only browser check of the actual Windows receiver application, the live sequence increased from 10 to 12 over seven seconds, with 206 frequency bins and 12 retained waterfall rows. Frequency entry, frequency readout and gain matched runtime settings. Desktop and mobile screenshots were inspected; all seven sections rendered with no JavaScript errors. Synthetic catalog checks separately validated modulation/date signal filters, protocol/category/date entity filters, parsed fields, source-bound decoder evidence and investigation navigation.

The seven-section browser results above describe version 1.0. Version 1.1 validation and the eight-view discovery layout are documented in [Discovery console](discovery-console.md).
