# RF Atlas 1.0 validation and completion report

Validation date: 2026-10-09. Branch: `single-receiver-release`. Implementation milestone: `c7df56c`, based on `b16677b`. Final release metadata/report is a subsequent local commit. Use `git log -2 --oneline` for exact final hashes. No remote push or publication occurred.

## Release decision

The mandatory single-receiver application capabilities are operational. Version 1.0 is justified by actual Windows receiver/application tests, actual browser tests, independent protocol fixtures, preserved regressions, Ubuntu 24.04 software tests and privacy-checked installable packages. This is a bounded validation, not a claim of prolonged unattended stability or complete spectrum discovery.

## Opening the application

On Windows, install Python 3.11+ and double-click `install-windows.cmd` once. Setup creates a project virtual environment, installs Python dependencies and optionally asks for the folder containing existing compatible RTL tools. Existing private configuration is preserved. Subsequently double-click `rf-atlas.cmd`; it launches the backend and requests the default browser at the printed localhost address. Leave its console running; Ctrl+C closes the app. The ordinary address is `http://127.0.0.1:8765`.

For an already installed environment, `rf-atlas app --open-browser` launches the application. Ubuntu installation and headless operation are documented in [install-ubuntu.md](install-ubuntu.md). No SDR drivers, DLLs, USB/VM settings or unrelated services are installed or changed. There is no PyInstaller bundle: a tested Python/source installer and console launcher are the practical release format.

A fresh installation checks the configured receiver and remains idle. Auto Survey and both content/identifier retention preferences default off. The Settings page stores startup, mode restoration and retention preferences. Chosen survey parameters persist separately; restarting resumes them only after explicit startup opt-in. One configured local receiver is supported.

## Application and spectrum inventory

Seven sections are available: Tuner, Scanner, Signals, Decoder, History, Devices and Settings. The graphite/cyan tuner supports frequency entry, steps, presets, manual gain, span/integration controls, peak hold, click-to-tune, actual spectrum and a bounded 32-row waterfall. Power is relative dB, not calibrated dBm. `rtl_power` does not provide an IQ sample rate for this view; the interface accurately says not reported. The tuner does not infer modulation or protocol from its spectral plot.

Quick Survey uses selected editable regions. Full Survey segments the documented receiver envelope into 883 default checkpoints. Smart Survey prioritizes new/unknown, intermittent and incompletely investigated activity and periodically explores less-visited regions. Settings/profile changes create appropriate independent checkpoints. Valid, invalid and unvisited coverage are distinct. Historical valid coverage does not establish silence or complete characterization.

The documented V4 envelope is 500 kHz to 1.766 GHz; actual sensitivity depends on receiver behavior, antenna and environment. See the [manufacturer release description](https://www.rtl-sdr.com/rtl-sdr-blog-v4-dongle-initial-release/) and [scan design](spectrum-inventory.md). The validation did not sweep the entire envelope or prove equal usability at every frequency. The first HF checkpoint returned nonfinite driver power values and was rejected; other checkpoints continued. One invalid ISM measurement was likewise retained as invalid rather than converted into silence.

The runtime supervisor serializes manual tuning, bounded scans and automatic workers. Manual reception synchronously pauses Auto Survey and releases ownership before acquiring the tuner. Ending manual control restores surveying when enabled in Settings. Process-tree ownership, timeouts, shared locks and cancellation prevent competing cooperating acquisitions. Other unrelated receiver applications must still be closed by the operator.

Signals preserve original observations, uncertainty, measurement settings, decoder attempts, investigations and recordings. Search includes frequency range, modulation, analog/digital class, protocol status, dates and unknowns. Associated entity records link to their signal evidence. Reanalysis appends history instead of rewriting earlier measurements. Bounded investigation queues, cooldowns, recording budgets and recovery after interruption remain operational. Exhausted storage refuses new recordings; existing captures are not deleted automatically.

Devices and stations are distinct connected records. Only independently verified supported decoder fields establish a protocol identifier: ADS-B ICAO24, AX.25 source callsign/SSID or WH2/WH5 model plus sensor ID. Matching uses exact protocol/model/identifier namespaces, never frequency or FCC assignments. Protocol identifiers can collide, change or be spoofed; they do not authenticate a physical device. Category, protocol, identifier/model and UTC date filtering are supported. No live device/station identity was verified during this run; functional identity and duplicate handling were checked with generated protocol fixtures.

## Actual Windows validation

The existing receiver and existing compatible tools opened successfully. Receiver check took 2.324 seconds. Manual tuning and a real gain/frequency change produced continuing measurements. An initial 27.675-second bounded run yielded actual spectra and successful retuning.

Quick and Smart tests acquired valid selected-region measurements. Two separate Full Survey runs preserved and resumed checkpoints: the first reported two visited regions and the second progressed to three, including valid data. Manual takeover showed Auto Survey paused, then resumed after the tuner stopped. Profiles used for validation were restored afterward, and automatic acquisition was left off.

A separate **122.259-second Smart Survey soak** exercised receiver → scan → detection → queued I/Q capture → DSP classification → decoder selection/evaluation → SQLite → dashboard. It completed six automatic investigations, with finite two-second, 1,000,000-byte CU8 captures at nominal 250 ksample/s. The tool reported approximately 250000.000414 sample/s. Eighteen measurements were persisted during this window, seventeen valid, approximately 8.34 valid visits/minute including investigations. Fourteen unique regions were visited, thirteen with valid data. The catalog had 114 RF signal candidates afterward; these are spectral observations, not 114 identified physical devices. Every live protocol remained UNKNOWN, and live modulation in these captures remained UNKNOWN. No successful live decode or identity is claimed.

Replay of an actual automatically acquired capture completed through the protected dashboard endpoint in 0.400 seconds. Its two-second duration, byte count and checksum metadata were checked. Original observation counts were preserved. SQLite integrity and foreign-key checks passed.

A fresh Windows virtual environment installed the actual candidate wheel with NumPy, SciPy, Matplotlib and DuckDB. An isolated installed-package hardware smoke test reached a usable localhost service in 11.999 seconds from Python imports/schema/probe, acquired two actual live measurements/waterfall rows, and shut the receiver down in 0.0125 seconds with the child reaped. A new runtime restarted idle and reopened the receiver successfully. The actual `install-windows.cmd` was subsequently executed successfully: it installed version 1.0.0, preserved the existing configuration and launched `rf-atlas.cmd` without arguments. The localhost application reported an available receiver, idle mode and all startup/retention defaults off. This startup number describes that fresh installed service test, not a broad startup benchmark.

## Browser and protocol validation

Windows Edge desktop and 390-pixel mobile browser tests exercised all seven sections, missing-tool messages, frequency accuracy, filters, entity links and frame evidence. During an actual read-only tuner check the sequence advanced from 10 to 12 in seven seconds, with 206 bins and twelve waterfall rows. Frequency/gain matched the backend. No JavaScript errors or horizontal mobile page overflow occurred.

Generated ADS-B and AX.25 IQ fixtures recovered actual protocol frames, independently checked CRC/FCS and rendered conventional 16-byte hex rows, byte offsets, ASCII and parsed fields. Raw IQ is never shown as decoded frame bytes. Frame history distinguishes verification status and includes source hashes and timestamp basis. Default storage removes frame bytes and identifiers; retention must be explicitly enabled. Enabling raw retention can retain identifiers/message content even if parsed identifier retention is off.

Screenshots are intentionally local and ignored: `reports/ui-qa/live-tuner-desktop.png`, `live-tuner-mobile.png`, `live-signals-desktop.png`, `live-history-desktop.png` and `synthetic-decoder-desktop.png`. The live screenshots contain private observations and are not packaged or committed.

## Classification and decoder limits

Fresh DSP results exactly match every preserved 0.7 case: original 720 cases **84.7222%**, independent holdout 360 **64.1667%**, stress 30 **93.3333%**. All twenty saved detector reference rows matched `b16677b`. No threshold/corpus tuning was performed. These synthetic results do not establish equivalent real-world accuracy. Mixed, weak, noisy and unsupported signals can remain UNKNOWN. Optional ML is experimental and not required.

Four registered adapters remain supported: native ADS-B DF17, native AX.25 AFSK1200 UI/F0, and optional external rtl_433 WH2/WH5. Six external-adapter tests passed using the actual installed rtl_433 executable, including valid/corrupt/noise fixtures. Native frame evidence now retains actual recovered bytes when requested and independently rechecks their integrity. AX.25 support does not claim APRS application decoding.

xng, GopherTrunk, DSD-FME, AIS-catcher and Dire Wolf were reevaluated against primary sources; [protocol-expansion.md](protocol-expansion.md) records licensing, platform/input/output findings and integration blockers. No unaudited AIS/ACARS/VDL2/DMR/P25/NXDN verification was added. A regenerated NMEA checksum, a decoder label or failed demodulation cannot prove RF protocol integrity or encryption. No voice recording/transcription, decryption or transmission feature was added.

## Databases, tests and resource measurements

SQLite schema 7 adds recovered frames, entities and entity observations without destructive migration. The existing catalog received a consistent pre-migration backup. Reopening is idempotent; newer unsupported schemas are refused. SQLite `integrity_check` returned `ok` and `foreign_key_check` returned no violations.

Optional DuckDB keeps a detached analytical projection and bounded server-side aggregates. Identifier discovery/activity statistics contain categories/protocols/times/counts, not identifiers, decoded fields or raw payloads. Older snapshots and analytics files remain readable. Actual DuckDB tests covered snapshot/build/query/Parquet behavior and privacy constraints. Spectrum Explorer continues showing activity, noise history and coverage with explicit missing/invalid states.

The final full Windows suite passed **204/204 tests** in 72.073 seconds, including actual optional rtl_433 execution and all eleven runtime ownership/recovery tests. Ubuntu 24.04.4/Python 3.12.3 executed **191 tests: 188 passed, three expected skips** (two missing optional rtl_433 tests and one Windows Job test), 39.021 seconds. The exact candidate wheel installed and passed isolated Linux schema/assets/runtime smoke tests. Linux validation was software-only. No existing Ubuntu 26.04 environment was available; it was not installed or claimed tested.

A separate **121.099-second actual tuner soak** produced 41 measurements with no acquisition failure, approximately 0.34 measurements/second. The browser polls every 1.5 seconds; this does not increase acquisition frequency. The waterfall stayed bounded at 32 rows. Application-process CPU averaged **1.52% of one core**, steady RSS was **138.9 MiB**, peak RSS **327.8 MiB**. Median runtime API response was **27.4 ms**, maximum **67.0 ms**; catalog API median **6.89 ms**, SQLite count query median **0.808 ms**. CPU/RSS describe the application process, not aggregate CPU for external acquisition tools or the browser. No prolonged stability claim follows from these bounded soaks.

Fault-injection tests cover missing tools, invalid data, externally held device locks, blocked-child cancellation, Windows Job assignment failure, decoder timeouts and failed mode transfer. Physical unplug/replug was not performed; availability recovery was checked by actual release/reopen and synthetic acquisition failures. Long-duration operation, all antennas and every hardware frequency/rate remain outside this validation.

## Release gates and public readiness

All 24 mandatory gate categories have operational evidence: Windows installation/launch; hardware detection; tuning; live spectrum; waterfall; Quick/Full/Smart surveying; persistent signals; bounded investigations; IQ record/replay; DSP; verified protocol decoders; hex viewer; evidence-based entities; history; Explorer; analytics; safe switching; startup/shutdown; actual Windows pipeline; Ubuntu software tests; regressions; privacy/licensing. Live protocol verification and live identity discovery are explicitly not claimed; the mission permits valid fixtures for those gates.

Original source remains MIT. uPlot is bundled with its original MIT notice. Python scientific/plot/analytics dependencies are separately installed with their distribution notices. External RTL tools and decoder binaries are neither bundled nor replaced. [dependency-licenses.md](dependency-licenses.md) and the protocol audit describe third-party constraints. No external dataset or pretrained weights are redistributed.

Release checks covered 127 public working/index files, 54 wheel members, 132 source-archive files and all local Git history trees, with zero publication findings. Private RF data, configuration, databases, logs, screenshots and reports stay ignored. Final stable wheel/source-archive and commit checks are recorded locally and summarized in the completion response. Stable 1.0.0 was installed and smoke-tested on Windows and Ubuntu. Builds are local only; no remote assets were uploaded except privacy-gated public source/wheel copies to the existing Ubuntu validation host.

The highest-value follow-up work is longer unattended observation, physical disconnect/reconnect tests, stronger characterization of unreliable hardware regions, broader real-world classification validation and independently audited optional protocol adapters. None changes the one-local-receiver architecture.
