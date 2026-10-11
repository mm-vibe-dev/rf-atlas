# RF Atlas

RF Atlas by **mm-vibe-dev** is a Windows-first, local, receive-only spectrum inventory and I/Q investigation application for one RTL-SDR receiver. Python 3.11+ and Ubuntu software compatibility are supported. Core operation needs no cloud account or paid API.

**1.9.0rc1 is an experimental prerelease.** The current Windows receiver opens but returns zero samples; recovery, new live protocol reception and endurance are unverified. DSP remains the default. The optional synthetic-trained CPU model performed poorly on the narrow historical RF reference and is not promoted over DSP. See [measured scope and limitations](docs/discovery-1.9.md).

Explore, Signals and Settings provide bounded discovery, a private inventory, Tune & Monitor, retained-IQ Signal Lab plots and advanced investigation. Protocol verification requires actual synchronization/framing and integrity checks. Model predictions, frequency allocations and bit candidates are hypotheses. Frame and identifier retention default off. RF Atlas does not transmit, decrypt protected traffic, replace USB drivers or infer physical transmitter identity from a frequency.

## Install from an audited local artifact

Public release upload and live documentation deployment are pending authenticated GitHub access. Do not assume PyPI availability. Build or obtain an audited wheel and verify its SHA256 against its manifest before installation.

```text
python -m venv .venv
python -m pip install path/to/verified-wheel.whl
rf-atlas --version
rf-atlas doctor --skip-receiver
rf-atlas app --open-browser
```

Use the virtual environment's Python/pip and rf-atlas executable after activation. Windows users can run the user-scope [installer](scripts/install.ps1) with a local wheel, its SHA256 and version; [launch](scripts/launch.ps1), [upgrade](scripts/upgrade.ps1), [repair](scripts/repair.ps1) and [uninstall](scripts/uninstall.ps1) scripts preserve separate runtime data. An optional desktop shortcut is available. No administrator access, bundled drivers or global execution-policy changes are required.

Ubuntu users need pipx and either python3-venv or the optional uv backend. The validated isolated Ubuntu 24.04 path uses `pipx install /path/to/verified-wheel.whl --backend uv` with uv available; upgrade with `pipx upgrade rf-atlas` only when its installation source has a newer audited artifact, or reinstall from a new verified wheel. `pipx uninstall rf-atlas` removes the application and preserves private runtime data. Configure separately installed RTL-SDR tools and appropriate USB permissions without modifying system Python. See [Windows configuration](docs/install-windows.md) and [Ubuntu configuration](docs/install-ubuntu.md).

Copy config.example.toml to private config.local.toml and configure existing compatible RTL tools. The application starts idle and binds to loopback. RF_ATLAS_HOME selects the private runtime root; RF_ATLAS_CONFIG selects a config file. Runtime recordings, databases, settings, logs and decoded identifiers must not be published. The doctor report is sanitized by default; a receiver check makes at most two finite acquisitions.

## Investigate a recording

```text
rf-atlas replay recording.cu8
rf-atlas identify recording.cu8
rf-atlas demodulate recording.cu8 --mode 4FSK --baud 4800
rf-atlas ai-train
rf-atlas ai-infer recording.cu8
```

Recording metadata supplies rate, center frequency and SHA256. Demodulation supports selected isolated rectangular OOK/2FSK/4FSK/BPSK/QPSK hypotheses with unresolved polarity/phase and no protocol proof. DMR Golay/BPTC correction remains followed by a mandatory CSBK CRC. Selected validated frames expose metadata; general payload/voice interpretation and APRS application semantics are not claimed.

Optional AI inference uses bounded non-executable local JSON weights, checked SHA256 and 28 measured RF features. It runs separately from DSP and never overwrites verified evidence. No pretrained checkpoint, GPU framework or arbitrary model code is downloaded. Enable it explicitly in Settings after training; it is off by default. Synthetic holdout family accuracy was 62.5%, versus 0% on 17 historical real recordings; DSP retained 70.6% on that same limited real set. These are not broad field accuracy estimates.

Supported decoder scopes include RDS groups, DMR base-station CSBK/selected Capacity Plus metadata, AX.25 AFSK1200 UI/F0, selected AIS position reports, ADS-B DF17 and optional rtl_433 weather adapters. Fixture, retained replay and live verification are distinct. DSD-FME is optional offline diagnostics. P25/NXDN/D-STAR/C4FM/M17/ACARS/UAT remain unsupported verification targets.

## Develop and verify

```text
python -m pip install ".[plots,analytics]" build
python -m unittest discover -s tests -v
python tools/release_check.py --tracked
python -m build
```

Test the installed wheel outside the source checkout. Public tests generate their IQ; private recordings are excluded. CI covers Windows and Ubuntu source/wheel tests, offline browser smoke, optional CPU model smoke, packaging and publication checks. Hosted CI does not establish physical receiver or live audio validation.

The prepared [documentation website](website/index.html) is local until Pages deployment is verified. Read [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md), [third-party notices](THIRD_PARTY_NOTICES.md), [dependency licenses](docs/dependency-licenses.md) and the [MIT license](LICENSE). uPlot's original MIT notice is included. External RTL tools, drivers, RF datasets and downloaded model checkpoints are not bundled.
