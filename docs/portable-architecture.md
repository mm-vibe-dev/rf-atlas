# Portable architecture

RF Atlas preserves the existing scanner and SQLite schema while extracting boundaries. The package is rfatlas; the installed command is rf-atlas. rf_atlas.py and diagnostics.py remain compatibility imports for existing local scripts. legacy.py currently owns CLI orchestration, observation ingestion and export until subsequent small migrations.

| Module | Responsibility | Depends on |
|---|---|---|
| config | native paths, TOML/environment and budget validation | Python standard library |
| platforms | Windows jobs/locking, POSIX locks, Linux parent-death guard | standard library, conditional OS APIs |
| hardware | receive-only backend protocol, RTL tool discovery and device ownership | config/platform adapters |
| survey | bounded region scanning/checkpoint orchestration | injected receiver backend, database |
| detection | adjacent-bin peaks and adaptive local noise | NumPy, diagnostics noise model |
| diagnostics | strict power parsing, spectra/threshold plots and IQ diagnostics | NumPy/SciPy, optional Matplotlib |
| storage | authoritative SQLite schema and additive migrations | sqlite3 |
| iq | finite recording, integrity-checked replay and hypothesis history | backend, storage, feature functions |
| features | independent waveform features and cautious heuristic labels | NumPy/SciPy |
| recovery / engine | bounded isolation, carrier hypotheses, symbol-grid diagnostics, structured DSP/optional backend selection | NumPy/SciPy |
| corpus / ml | separate synthetic splits and optional small local CPU training/inference experiment | NumPy/SciPy; model opt-in |
| sigmf / decoders | bounded CU8 interchange and future decoder evidence contract | standard library, NumPy/iq |
| legacy | CLI, catalog ingestion and CSV/JSON compatibility | portable modules |

The initial foundation described here predates the implemented decoder,
dashboard and runtime modules. Current optional adapters take bounded typed
observations and return protocol evidence. The dashboard queries storage through
bounded interfaces and delegates receiver actions to one local supervisor.
The server binds to loopback. Distributed agents, remote receiver management
and transmitter geolocation are outside the single-receiver 1.0 design.

Each RTL operation acquires a shared per-user lock outside the project root. The RTL backend conservatively serializes RTL access for that user. This preserves the single receiver's ownership across tuning, scanning and investigations. Multiple-receiver operation is outside the roadmap. Other applications do not honor this lock; the tool's device-open failure is reported and failed coverage remains resumable. Different users and older application versions require operator coordination.

Windows receivers belong to a kill-on-close Job Object, and ordinary cancellation kills/reaps them. Linux launches a small exec wrapper that establishes parent-death SIGKILL and a dedicated process group; normal cancellation kills the group. The brief Windows interval before job assignment remains a limitation. Linux behavior is covered by CI definitions but has not run on this development host.

Recordings have UUID filenames, exact finite sample counts, offset tuning and metadata-driven digital translation. Per-file and archive budgets reject work rather than silently delete evidence. Checksum/size/format/rate validation precedes replay. Small captures retain full context for recovery; larger captures select an activity window capped at two seconds and one million samples. Whole-band activity can miss weak narrow late bursts; explicit channel/guard activity is available. Recovery operations are logged without altering original IQ. SigMF interchange verifies/copies a supported single-capture CU8 pair and never overwrites existing evidence.

Classification has separate class/modulation/protocol/protection fields. These preliminary confidence values are heuristic scores, not calibrated probabilities. CW, AM, OOK and FSK labels have supporting statistics and alternative explanations; continuous FM-like variation leaves analog/digital class unresolved. Low-quality/noise input stays UNKNOWN. Protocol/protection stay UNKNOWN in every branch. --store appends a hypothesis for an associated capture and does not overwrite catalog decoder fields.

Local observation records retain acquisition settings, frequency, relative
power/noise and evidence provenance. A single receiver's power cannot establish
a transmitter location. The application implements no triangulation, remote
sensor transport or RF transmission.
