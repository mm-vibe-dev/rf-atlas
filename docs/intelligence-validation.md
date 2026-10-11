# RF Atlas 0.7.0 consolidated validation

Continues the clean 0.6 baseline `1ba7391` on local branch
`spectrum-intelligence`. Implementation commit: `47d75ca`. Original application code remains MIT. No push,
driver/USB/VM change, RF transmission, decryption or private upload occurred.
The final implementation and report commit hashes are supplied in the delivery
response. The identification engine retains version 0.6.0 because its algorithm
and verification rules are unchanged; application/package version is 0.7.0.

## Delivered capabilities

The existing dashboard now contains a uPlot/Canvas Spectrum Explorer: frequency
zoom/selection, recorded relative power, noise estimates, compatible peak hold,
waterfall, bandwidth/uncertainty markers, historical SNR, coverage states,
sampled detection occupancy, dates and unknown filtering. Ten-second polling
shows completed measurements. Local replay and reanalysis use retained catalog
recordings and preserve original observations. See [visualization](visualization.md).

Optional DuckDB analytics uses consistent SQLite backups and detached structural
projections with provenance, bounded time-range aggregates and local Parquet.
SQLite remains primary; the dashboard and acquisition work without DuckDB.
WAL-mode build inputs are refused. A pinned WAL snapshot prevents incremental
backup starvation during frequent writes. See [analytics](analytics.md).

Operational schema version remains 6. Additive indexes support recent observations,
scan completions and inventory visit history. No tables or observations were
replaced. HTTP workers, socket waits, query work, raw spectrum reads, chart arrays
and offline browser actions have explicit bounds. Existing single-device ownership
and external-decoder process limits remain intact.

The FCC provider supports indexed, bounded offline normalized assignments,
source/update provenance and optional locations. Import is transactional and
separate from the catalog. Full FCC bulk ingestion is deferred with a documented
join/update/validation plan. Amateur/GMRS entitlement is not a site-frequency
assignment. Licensing matches do not prove transmitter identity. See
[FCC scope](fcc-provider.md).

## Decoder findings and verified support

xng, GopherTrunk and DSD-FME were evaluated against actual upstream source and
documented interfaces, with pinned references in the [decoder evaluation](protocol-expansion.md).
xng has real file input/structured output but no locally tested Windows build or
mode-specific evidence audit. GopherTrunk's offline hunt exports aggregate maps,
not the audited frame accounting required here. The installed DSD-FME Windows
help probe succeeded; raw complex CU8 is not discriminator WAV, and console
sync strings do not meet verification requirements. No speculative adapter is
registered, and no additional verified protocol is claimed.

Verified support remains native ADS-B DF17 and AX.25 UI/F0 AFSK1200, plus optional
rtl_433 WH2/WH5 subsets. AIS is not implemented in RF Atlas. APRS semantics,
DMR/P25/NXDN, Capacity Plus, ACARS, VDL2, HFDL and satellite verification remain
unsupported. Unknown/protection uncertainty is preserved.

## Validation and performance

The starting Windows suite passed 151/151. Final working-tree Windows tests
passed 170/170, including installed rtl_433 and DuckDB. The extracted public
source archive passed all 157 tests with the installed optional rtl_433 checks.
Ubuntu 24.04.4 / Python 3.12 ran the same 157 tests with 155 passes and the same
two skips; DuckDB 1.5.6 exercised its real snapshot/query/Parquet path. Scoped
Explorer checks passed after the final acquisition-metadata refinement.
Installed Windows and Ubuntu wheels 0.7.0 loaded all six HTML/JS/CSS/license assets. Synthetic
CLI snapshot/build/query/Parquet and unavailable FCC lookup smoke checks passed.
Ubuntu 26.04 remains a CI target, not a tested environment in this assignment.

Release checks found zero issues in 112 staged public files, 49 wheel members,
117 source-archive members and all seven pre-release Git commit trees. DuckDB
and Parquet outputs are now ignored and rejected by publication checks. Private
working-tree captures, configs, databases and logs remain local and ignored.
Source upload to the existing Ubuntu runner contained public staged files only.
Original application MIT and bundled uPlot MIT notices are preserved. No external
decoder binary is bundled. Automated checks supplement manual review; they
cannot prove the absence of every possible secret. Final report/package checks
and local commit identities are recorded in the delivery response.

Actual synthetic UI QA covered 1280x900 desktop and 390x844 mobile, plots,
navigation, action wiring, date comparison and absent spectra, with no console
errors or horizontal mobile overflow. Real checksummed replay/reanalysis tests
separately verify original measurements remain unchanged.

Preserved DSP classification results: original 720 cases 84.7222%; holdout 360
cases 64.1667%; independent stress 30 cases 93.3333%. Datasets, scoring and source
algorithms are unchanged. All 20 frozen saved detector rows matched the 0.6
baseline; an additional 62 valid saved rows also matched. These local private
fixtures remain excluded from releases. There was no new hardware acquisition:
the existing 0.6 bounded energy-only hardware validation still describes the
actual survey evidence. No complete-band sweep or new RF protocol validation is
claimed.

Windows synthetic Explorer benchmark, 20 requests with four concurrent readers
and 20 writer commits: mean read 189/179/189 ms at 1k/10k/100k observations;
maximum 252/197/212 ms. Python traced peak allocations were 1.45/1.09/1.06 MB,
excluding native allocations/RSS. Writer batches completed in 39/30/29 ms.
Native ADS-B adapter overhead on 65,536 noise samples averaged 14.8 ms across
ten calls. These are host/load-specific synthetic results, not survey throughput.

A separate retained-spectrum benchmark read 32 synthetic rows of 4,096 bins,
returned 32 rows of 1,024 peak-preserving display buckets, and retained the narrow
test peak. Cold response was 2.71 s including scientific imports; three warm
responses averaged 0.629 s. The database deadline does not cap file parsing or
first-import latency. No continuous-IQ frame-rate claim is made.

DuckDB 1.5.6 synthetic benchmark at 1k/10k/100k visits plus equal observations:
snapshot 0.038/0.048/0.243 s; build 1.46/1.25/11.54 s; aggregate query
0.059/0.141/0.118 s; Parquet 0.055/0.162/0.103 s. All aggregate counts matched
SQLite. Concurrent writer errors: zero; maximum commit latency 26/38/34 ms.
Process lifetime peak RSS was 100/104/122 MB across increasing sizes. Simple
SQLite counts were faster, but are not equivalent to the full analytical result.

## Release limits and next priorities

Large FCC imports, Ubuntu 26.04 execution, new external protocol verification,
continuous IQ streaming and calibrated power remain outside demonstrated scope.
Legacy offset-free timestamps assume UTC for filtering. Browser reanalysis uses
file/sample limits rather than a hard CPU deadline. Chart views are truncated,
historical labels reflect current catalog state, and sampled detection occupancy
is not continuous airtime. Offline exports require explicit local retention;
captures are never automatically deleted.

Next priorities are an independently tested file-only AIS adapter, mode-specific
xng evidence validation on native Windows/Linux, current-layout FCC bulk import,
explicit legacy timezone migration, detached background reanalysis cancellation,
and longer controlled survey/visualization load measurements. No labels should
be inferred from band membership or filenames.
