# Foundation audit and implementation scope

The baseline is a working Windows CLI: external rtl_power scans, adaptive local-noise detection, adjacent-bin grouping, SQLite catalog and region resume, atomic CSV/JSON snapshots. Validation established that fine bins and explicit manual gain detect a known intermittent channel; automatic gain generated nonfinite output. Raw evidence and private local reports remain on disk.

Before this iteration, rf_atlas.py combines process/device management, database schema, scanning, detection and CLI. diagnostics.py contains independent spectral parsing, plots and a bounded IQ control analysis. Tests cover processing and include an external private IQ fixture. There is no package metadata or Git repository. DLL/driver configuration is working and must be preserved.

| Requirement | Baseline gap | This iteration |
|---|---|---|
| Portability | unconditional msvcrt/WinDLL, fixed paths/selector | conditional platform adapters, TOML config, executable discovery |
| Packaging | loose scripts | Python >=3.11 package and console entry point; compatibility launchers |
| Modular boundaries | combined scanner/storage/DSP | incremental extraction; stable schema and scan behavior |
| Device ownership | per-project lock | shared per-user per-device locks independent of project root |
| Public hygiene | private evidence mixed with source tree | explicit private ignores, sanitized public docs/config/tests |
| Tests/CI | Windows/private-fixture assumptions | synthetic IQ and hardware-error tests; Windows/Ubuntu CI |
| IQ lifecycle | validation script only | finite record, bounded replay, metadata/checksum and database history |
| Classification | none | conservative original heuristic estimates, evidence and UNKNOWN |
| REST/dashboard | absent | interface boundary documented; implementation deferred |
| Distributed/geolocation | absent | data contracts and calibration prerequisites documented; deferred |

Only portable foundation and the first recording/replay/classification slice are in scope. No driver changes, network publication, external decoder activation, distributed sensing or geolocation implementation is required. Linux source/CI compatibility is assessed here; Linux hardware execution requires a separate Ubuntu environment.
