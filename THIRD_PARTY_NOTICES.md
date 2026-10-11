# Third-party notices

RF Atlas original source, synthetic generators and locally derived ridge weights are MIT licensed. Copyright (c) 2026 mm-vibe-dev and RF Atlas contributors. The root LICENSE contains the full terms.

The only bundled third-party runtime asset is uPlot JavaScript and CSS, version 1.6.32, copyright (c) 2022 Leon Sorokin, MIT. Its complete original notice is preserved in [uPlot.LICENSE](rfatlas/web_assets/uPlot.LICENSE) and included in the wheel.

NumPy and SciPy are separately installed dependencies, primarily BSD licensed with additional component terms in their distributions. Optional Matplotlib and DuckDB retain their distribution notices. RF Atlas does not vendor their wheels or native libraries. See [the dependency inventory](docs/dependency-licenses.md).

RTL-SDR command-line tools, rtl_433 and DSD-FME are optional operator-installed programs, invoked as external processes on managed samples. Their binaries, drivers and source are not distributed here. Review the exact external distribution's GPL and component notices before redistribution. RF Atlas does not install USB drivers.

No liquid-dsp, GNU Radio, TorchSig, Hugging Face model code, downloaded checkpoints, cnn-rtlsdr source, RF datasets or sigint artwork are bundled. Research links do not grant reuse permission. The optional CPU ridge model uses original synthetic waveforms and non-executable JSON parameters; training and inference need only the existing NumPy/SciPy dependencies.
