# Foundation validation â€” 2026-10-08

Development host: Windows 11 VM, Python 3.13. Existing RTL-SDR Blog V4L-compatible tools and driver configuration were preserved. Private detailed RF results remain in ignored local reports; this document contains no receiver identifiers or recorded communications.

| Check | Result | Evidence / limit |
|---|---|---|
| Existing regressions | PASS | All 13 original local tests remain passing, including private offline IQ analysis |
| Public software-only tests | PASS | 18 tests use synthetic IQ, temporary storage and mocked receiver acquisition |
| Combined local suite | PASS | 31 tests; no live receiver is opened by these tests |
| Receiver availability | PASS | Installed doctor opened the existing V4L and enumerated its gains |
| Migrated scanner | PASS | Bounded ten-spectrum narrow scan: zero invalid bins, target candidate in all ten; existing stable catalog ID retained |
| New recording | PASS | Finite ten-second, 250 kS/s, manual gain, offset-tuned recording; exact 5,000,000-byte file and SHA256 metadata |
| Replay of live recording | PASS | Integrity/metadata/translation and features execute offline; ambiguity returns UNKNOWN |
| SQLite and exports | PASS | integrity_check ok; existing observations preserved and new observations appended |
| Windows process handling | PASS (bounded tests) | Arguments with spaces, failing children, timeout cleanup and lock contention tested |
| Resume | PASS (software-only) | Simulated second-region acquisition failure resumes only pending region without duplicate observations |
| Wheel packaging | PASS | Built and installed py3-none-any wheel; replay smoke test from a temporary directory imports site-packages and identifies synthetic four-level FSK; no private package findings |
| Ubuntu execution | PENDING | Conditional POSIX code and Linux parent-death adapter implemented; no Linux OS/hardware available here |
| GitHub CI | PENDING | Windows and Ubuntu 24.04/26.04, Python 3.11/3.13 matrix defined; nothing pushed or dispatched |

The synthetic classifier suite includes carrier-like, AM, OOK, two/four-level FSK and noise cases. It checks explicit heuristic evidence and separate UNKNOWN protocol/protection. These tests demonstrate representative clean examples, not real-world classifier accuracy. Initial test failures exposed locked-byte initialization on Windows and spectral/histogram boundary cases; code was corrected and the full suite rerun successfully before hardware validation.

Live replay is intentionally conservative: the representative first two seconds did not support a safe waveform label. The migrated spectral detector nevertheless detected the live channel consistently. Classification and detection remain separate. Rare bursts beyond the replay window, mixed signals, wideband noisy signals and frequency-dependent antenna response need further validation.

Packaging excludes private histories, local tool configurations, logs, captures, SQL databases, spectra and local regression fixtures. Existing private files remain untouched on disk. Public source is independently authored MIT code with dependency/research attribution. No source from the researched SIGINT project was copied.

Limits: Windows abrupt owner termination, USB removal during real acquisition, VM reboot, canonical multi-device enumeration and Linux device permissions have not been fault-injection validated. Windows has a small process/job attachment interval. Cross-user/external receiver programs need operator coordination. Do not report Ubuntu or CI as passed until those jobs actually run.

The staged public tree was exported to a clean temporary directory and all 18 public tests passed without the ignored local configuration or private fixture tests. The installed wheel was smoke-tested from outside the source checkout, confirming site-packages imports and synthetic four-level FSK replay. Wheel and source archives were built locally; no remote job or publication occurred.

## Public files created or migrated

- `.gitattributes`
- `.github/workflows/test.yml`
- `.gitignore`
- `CONTRIBUTING.md`
- `LICENSE`
- `MANIFEST.in`
- `README.md`
- `SECURITY.md`
- `config.example.toml`
- `diagnostics.py`
- `docs/dependency-licenses.md`
- `docs/foundation-gap-analysis.md`
- `docs/foundation-validation.md`
- `docs/install-ubuntu.md`
- `docs/install-windows.md`
- `docs/next-phase.md`
- `docs/portable-architecture.md`
- `docs/research-notes.md`
- `pyproject.toml`
- `rf-atlas.cmd`
- `rf_atlas.py`
- `rfatlas/__init__.py`
- `rfatlas/__main__.py`
- `rfatlas/_linux_child.py`
- `rfatlas/config.py`
- `rfatlas/detection.py`
- `rfatlas/diagnostics.py`
- `rfatlas/features.py`
- `rfatlas/hardware.py`
- `rfatlas/iq.py`
- `rfatlas/legacy.py`
- `rfatlas/platforms.py`
- `rfatlas/storage.py`
- `rfatlas/survey.py`
- `tests/test_detection.py`
- `tests/test_portable.py`
- `tools/release_check.py`
