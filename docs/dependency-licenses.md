# Dependency license inventory

This inventory describes the development environment. Resolution on another Python/OS may select different versions and bundled components. RF Atlas does not vendor these packages or their wheels; installed distributions retain their own license/notice files. Re-audit exact artifacts before redistributing an executable bundle.

| Dependency | Installed version | License expression / declared family | Role |
|---|---|---|---|
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | core |
| scipy | 1.18.1 | BSD-3-Clause; numerical wheels include separately licensed components | core |
| matplotlib | 3.11.2 | Matplotlib license (PSF-derived) with included component notices | optional plots or transitive |
| contourpy | 1.4.0 | BSD-3-Clause | optional plots or transitive |
| cycler | 0.12.1 | BSD-3-Clause | optional plots or transitive |
| fonttools | 4.66.1 | MIT | optional plots or transitive |
| kiwisolver | 1.5.1 | BSD-3-Clause | optional plots or transitive |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | optional plots or transitive |
| pillow | 12.3.0 | MIT-CMU | optional plots or transitive |
| pyparsing | 3.3.3 | MIT | optional plots or transitive |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD-3-Clause | optional plots or transitive |
| six | 1.17.0 | MIT | optional plots or transitive |
| setuptools | 84.0.0 | MIT | build only |
| wheel | 0.48.0 | MIT | build only |

NumPy/SciPy may ship numerical libraries and compiler runtimes under additional BSD, MIT, LGPL or exception-bearing licenses depending on the wheel. The installed wheel license text, not a top-level shorthand, controls redistribution. Matplotlib and Pillow similarly include component notices. Core runtime declared dependencies are NumPy/SciPy; plots is optional. Tests use unittest from the Python standard library. Python itself is PSF licensed.

Build tooling uses setuptools; wheel is installed only in the local project virtual environment. GitHub Actions uses actions/checkout and actions/setup-python (MIT); they are CI services, not bundled runtime dependencies. CI is not required for offline operation.

External RTL tools are separately installed operator dependencies, not bundled or installed by RF Atlas. The researched Blog rtl_power source carries GPL-2.0-or-later terms. Preserve each external distribution's notices and inspect exact binary licenses if redistributing it. rtl_433 has audited optional WH2/WH5 adapters; DSD-FME remains research-only. No code/binaries from these external programs are redistributed here.

Version 0.7 bundles uPlot 1.6.32 JavaScript/CSS under MIT with its complete original license notice in `rfatlas/web_assets/uPlot.LICENSE`. DuckDB 1.5.6 was tested as an optional separately installed MIT dependency. ECharts (Apache-2.0) and inspectrum (GPL-3.0) were evaluated but are not bundled. xng (MIT/Apache-2.0), GopherTrunk (Apache-2.0 with component notices) and DSD-FME (mixed licensing including GPL-2.0 files) remain external research targets, with no bundled code. See the source-specific [decoder audit](protocol-expansion.md), [visualization decision](visualization.md) and [analytics decision](analytics.md).

Third-party research attribution and the unlicensed-source reuse restriction are in research-notes.md. No code or assets from arall/sigint were copied. Original RF Atlas source is MIT licensed.

Version 1.2 adds original NumPy/SciPy fingerprints, entropy measurements, an FM RDS decoder and optional CPU classification experiments, with no new mandatory dependencies. rtl-entropy (GPL-3.0), cnn-rtlsdr (reuse permission unverified), the gyaresu SDR examples, redsea (MIT repository license, ISC-style notices in synchronization source), and nrsc5 (GPL-3.0-or-later) were conceptual/research references only. No source, pretrained weights, binaries or historical environments from those repositories are redistributed. NRSC-5 remains unintegrated. See [1.2 research and validation](discovery-1.2.md) for source links and precise limitations.

The classification engine adds no installed dependency. Its optional NumPy MLP trains on project-authored synthetic data; no external dataset or pretrained weights are bundled. SigMF interchange is independently implemented from the specification without copying its CC-BY-SA prose/schema. liquid-dsp, TorchSig, TorchSig Models and GNU Radio were researched but are not imported, vendored or linked. See [resource and license decisions](classification-resource-evaluation.md) for optional native/FFTW, pretrained-weight provenance and GPL/AGPL constraints.

Version 1.3 adds no mandatory or bundled external dependency. GopherTrunk and DSD-FME remain optional offline metadata research references, without registered verified adapters or copied source/binaries. See [1.3 evaluation](discovery-1.3.md).

Version 1.5 adds original DMR and AIS implementations based on protocol specifications and project-authored test waveforms. No local historical script, third-party decoder implementation, RF recording or protocol payload is bundled. rtl_433 WH24 and DSD-FME remain optional operator-installed external processes. The DSD-FME diagnostic adapter uses file input and null output; it does not redistribute the executable or its mixed-license dependencies. No new mandatory dependencies are added.
