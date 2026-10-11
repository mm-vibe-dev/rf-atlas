# Phase 1.3 signal analysis validation

Version 0.3.0 preserves the spectral detector and acquisition rejection diagnostics. Additive observation fields retain actual FFT spacing, nominal integration, margin, threshold range, gain and tool-reported sample rate. Unreported sample rates and historical fields remain NULL rather than guessed. Raw command/parse evidence remains private.

Replay validates size, SHA256, metadata types, capture bandwidth and offset consistency. Offset translation is tested in both carrier directions and against quantized CU8. Recordings retain device selector, timestamp, nominal sample rate, gain, center, desired frequency and tuning offset; future captures also retain tool-reported sample rate separately. Nominal tuning is not an oscillator calibration. Existing metadata remains readable. Per-file/retention budgets and finite duration are preserved.

Activity uses 10 ms block energy by default, or optional channel-versus-guard PSD at 4096-sample resolution. Replay selects one activity window, limited to two seconds. The channel mode is centered on the metadata's desired frequency; channel width is explicitly supplied, not inferred from a service or protocol. It helps narrow bursts hidden in wideband noise. Whole-band activity can miss them. Events are energy estimates, not decoded airtime; guard interference, short captures, DC spurs and mixed signals remain ambiguous.

Classification separates RF detection, analog/digital hypothesis, modulation, protocol and protection. Confidence is `UNCALIBRATED_HEURISTIC_SUPPORT`, not a probability. Evidence includes spectrum, envelope, frequency histogram concentration, phase-order moments and normalized rail occupancy. Clipping evidence rejects modulation. Phase moments support tentative BPSK/QPSK. PSD mean removal is disabled so a desired carrier translated to DC is measurable; DC-spur ambiguity remains explicit. No decoder evidence is generated, so protocol/protection remain UNKNOWN.

## Synthetic evaluation

CU8 replay additionally rejects modulation when the 95th amplitude percentile is below four ADC counts. At that resolution, quantized I/Q corners can create false PSK coherence. This is a conservative engineering guard, not a calibrated sensitivity threshold. The floating-point synthetic benchmark below does not measure CU8 quantization; a dedicated quantized replay regression covers the guard.

Run from the repository with baseline commit `bd31fc8` available:

```text
python -m pip install ".[plots]"
python -m unittest discover -s tests -v
python tools/evaluate_analysis.py
python tools/release_check.py --tracked
```

The benchmark produces ignored `reports/synthetic-phase13/classification-results.json` with every case, features, evidence, confusion counts, family/SNR/variant summaries and baseline predictions. `confusion-matrix.png` visualizes counts. Baseline code is read from the trusted local commit, with identical generated samples supplied to both pipelines. The updated pipeline includes activity selection; baseline analyzes the complete fixture. This compares end-to-end behavior, not isolated classifier rules.

There are 720 cases: ten families, two fixed seeds, four SNR conditions (noiseless, 5, 15, 30 dB), and nine one-factor variants: base, +/-20 kHz offsets, low gain, clipping, 125/500 ksample/s, 25 ms duration, and a late 60 ms burst in a 300 ms capture. Default duration is 120 ms at 250 ksample/s. Noise-only SNR is inapplicable; those repeated controls are 18 distinct seed/variant cases repeated across four SNR labels. Gains are normalized amplitude multipliers, not calibrated receiver dB. SNR is total complex signal/noise power before burst gating. No exhaustive interactions, training, pulse shaping or independent corpus are claimed. Optional channel-aware replay is tested separately and is not used in this benchmark.

| Ground truth | Baseline accuracy | Updated accuracy | Updated UNKNOWN |
|---|---:|---:|---:|
| AM | 19.4% | 19.4% | 77.8% |
| NFM | 45.8% | 58.3% | 41.7% |
| WFM | 15.3% | 58.3% | 41.7% |
| OOK | 79.2% | 70.8% | 29.2% |
| 2FSK | 36.1% | 19.4% | 41.7% |
| 4FSK | 23.6% | 19.4% | 41.7% |
| BPSK | 0.0% | 25.0% | 38.9% |
| QPSK | 0.0% | 20.8% | 38.9% |
| CW | 5.6% | 58.3% | 38.9% |
| Noise -> UNKNOWN | 100% | 100% | 100% |

Overall accuracy: **45.0% versus 32.5%**. UNKNOWN rate: **49.0%**. Wrong non-UNKNOWN modulation labels occur in **17.7% of non-noise cases**. Noise-only false RF detection and false modulation label rates are both **0/72**, a small correlated synthetic control set, not an operational false-alarm guarantee.

Noiseless accuracy is 81.1% versus 47.8%; 5 dB: 11.7% versus 13.9%; 15 dB: 42.2% versus 31.7%; 30 dB: 45.0% versus 36.7%. Offset accuracy is 52.5% in either direction versus 53.8% at base. Low gain retains 53.8%; short duration gives 56.3%; low/high sample rates give 55.0%/53.8%. Clipped accuracy is 10% (noise rejected correctly, every modulation rejected), versus baseline 37.5%. Late-burst accuracy remains 17.5%, with 82.5% UNKNOWN.

**Regressions are explicit:** OOK -8.3, 2FSK -16.7, 4FSK -4.2 percentage points; 5 dB aggregate also falls. Clipping rejection explains some losses; frequency concentration guards and activity selection also alter decisions. FSK often falls into the FM-like branch (28 cases per family), and noisy PSK can be labeled WFM. AM is fragile to noise, and simple phase moments lack robust carrier/symbol recovery. These rules are investigational and must not become authoritative catalog labels. OOK/ASK testing covers binary amplitude gating only; general multilevel ASK is unvalidated. OFDM is unsupported and was not evaluated.

## Execution and portability

Windows 11 local suite: **41 tests**, including the original 31; clean staged public snapshot: **28 tests**. Tests cover late/short bursts, continuous carriers including DC, noise negatives, phase/frequency modulation, clipping rejection, metadata/checksum/truncation, offset direction, path spaces/foreign-path guards, additive DB migrations, measurement persistence, missing tools and simulated USB disconnect/lock release. Missing/disconnected hardware is simulated; physical unplug/cancellation stress tests remain pending.

One optional finite live investigation was completed. Private acquisition, replay, channel statistics and diagnostic plots remain in ignored storage. A narrow intermittent signal was measurable, but its tentative waveform hypothesis has no independently verified ground truth. Live observations do not contribute to synthetic accuracy. Public documentation omits frequencies, device identifiers, recordings and paths.

WSL is absent; no existing Linux execution environment was available. Static inspection keeps OS-specific process/lock handling isolated in platforms/childguard. New DSP uses NumPy/SciPy, standard CU8/JSON and SQLite without Windows-only dependencies. Windows packaging and public-snapshot tests pass. Ubuntu execution, hardware timing, process cleanup and actual CI jobs remain **unverified**; the existing Windows/Ubuntu CI configuration is preserved.

## Publication and next phase

Changed files: `rfatlas/activity.py`, `synthetic.py`, `features.py`, `iq.py`, `legacy.py`, `storage.py`, `__init__.py`; `tests/test_analysis.py`, `test_portable.py`; `tools/evaluate_analysis.py`; `README.md`, `docs/phase13-validation.md`, `docs/next-phase.md`; `.gitignore`, `MANIFEST.in`, `pyproject.toml`. The validated `detection.py` and OS integration modules are unchanged from baseline.

No new dependency, paid API, cloud service or copied third-party source was introduced. Existing dependency license documentation applies. Private capture/config/database/log/report paths remain ignored and excluded from release packaging. Indexed source and built-wheel checks found no publication findings; manual privacy/license review is still necessary. No driver/DLL, VM, USB configuration or remote repository changes were made.

Prioritize reliable carrier/symbol recovery, pulse-shaped and mixed-signal negatives, multiple burst windows, channel filtering, and an independent validation corpus. Fix measured OOK/FSK and low-SNR weaknesses before expanding labels or calibrating confidence. Execute Ubuntu software tests and hardware cleanup tests on an authorized existing environment. Dashboard, distributed sensing and geolocation remain deferred.
