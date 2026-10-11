# Optional offline analytics

SQLite remains the operational catalog. RF Atlas 0.7 adds an optional DuckDB
projection for expensive historical summaries and Parquet exports. Surveying,
identification, the dashboard and SQLite snapshots work without DuckDB.
Install the project extra with `python -m pip install ".[analytics]"`.

The [DuckDB repository](https://github.com/duckdb/duckdb) uses the
[MIT license](https://github.com/duckdb/duckdb/blob/main/LICENSE).
Its [Python client](https://duckdb.org/docs/current/clients/python/overview)
supports Windows and Linux, and its
[native Parquet writer](https://duckdb.org/docs/current/data/parquet/overview)
supports compressed local exports. DuckDB's
[SQLite extension](https://duckdb.org/docs/current/core_extensions/sqlite)
can access SQLite directly, but this implementation deliberately uses Python's
SQLite online backup and bounded batch imports. It needs no extension download
and never attaches the live catalog to DuckDB. These sources were checked for
this release. DuckDB is installed separately, not vendored into RF Atlas.

## Local workflow and Python interface

Trusted CLI workflow (all outputs stay private and local):

```text
rf-atlas analytics snapshot runtime/snapshot.sqlite
rf-atlas analytics build runtime/snapshot.sqlite runtime/analytics.duckdb
rf-atlas analytics query runtime/analytics.duckdb --start 1767225600 --end 1767312000
rf-atlas analytics parquet runtime/analytics.duckdb runtime/parquet
```

Build refuses a WAL-mode input, requiring a detached snapshot from the first
command. Output destinations must be new; retain or remove obsolete exports
explicitly under your local retention policy. Exports never delete captures.

`rfatlas.analytics.snapshot_catalog(source, destination)` creates a consistent
SQLite online backup, including committed WAL content. It refuses existing
destinations, checks integrity, and returns the SHA-256 and schema version.
It has no optional dependencies. A backup contains the complete private catalog;
keep it under the local ignored data/runtime/export directories.

`build_analytics(snapshot, destination)` creates a detached DuckDB file. The
manifest stores the source snapshot hash, schema version, DuckDB version and
projected row counts. The projection includes channel measurements, visits,
regions, internal signal join keys, structural identification counts and job
states. It excludes recording paths, settings, decoded payloads, locations and
external transmitter identifiers. Channel frequencies and internal identifiers
are still potentially sensitive local information. Nothing is uploaded.

Version 1.0 also projects entity discovery times, kind, protocol and category,
plus aggregated identifier-observation counts. Entity keys, identifiers, raw
frames and parsed fields are excluded. New-identifier statistics count the
first retained observation in the selected interval; observation statistics
include previously discovered identifiers seen again during that interval.
These are protocol addresses, not a count of authenticated physical devices.
The statistics are empty when identifier retention is disabled, and older
snapshots without entity tables remain supported. Existing detached 0.7 DuckDB
files can still be queried; rebuild a new projection to obtain entity statistics.

`query_analytics(path, start, end, bucket_s=3600, limit=500)` accepts UTC Unix
seconds and a half-open interval `[start, end)`. It returns active frequencies,
visit detection fractions, historical detection changes, noise trends,
snapshot modulation/protocol counts, bandwidth distribution, revisit counts,
union coverage, investigation states and decoder verification counts. Missing
time buckets remain absent. Valid zero-detection visits, invalid visits and
unvisited intervals must remain distinguishable. Coverage unions overlapping
regions and repeated visits. Visits are not continuous time coverage.

The time bucket width grows when needed to retain the full interval in a bounded
result. Ranked lists have at most 2,000 entries and can omit lower-ranked groups;
noise groups explicitly report truncation. Noise remains relative receiver power,
grouped by gain mode, gain and FFT resolution. It is not calibrated dBm.
Visit detection fraction is not transmitter duty cycle or protocol airtime.
Catalog classification and bandwidth summarize the snapshot's current state for
channels observed in the interval, not a reconstructed historical classification.
Job states are likewise the snapshot state of jobs created in the interval.
Decoder counts use retained identification results, never infer verification
from frequency or modulation alone. A successful job is not necessarily a
verified protocol. Invalid identification JSON contributes no verification.

`export_parquet(path, destination)` writes the projected tables and manifest to a
new local directory. It refuses existing destinations and publishes only after
all tables finish. Rebuilding from the same snapshot yields the same ordered
projection and manifest; byte-identical DuckDB or Parquet files across engine
versions are not promised.

## Resource and concurrency limits

Only the online backup briefly reads the live SQLite catalog. Imports, queries
and Parquet exports operate on detached files and never take an RTL device lock.
Snapshot and import cancellation callbacks are checked between page/batch work.
DuckDB operations use two threads, a 256 MB engine memory budget, a 512 MB spill
budget and a timer that interrupts active queries. These are not a cap on total
Python process RSS or a hard operating-system execution deadline. Closing,
commit, filesystem operations and backup integrity/hash checks can add latency.
Imports fetch at most 256 source rows per batch; coverage unions stream batches
rather than materializing every interval. Known extension auto-install and
autoload are disabled. No arbitrary SQL or remote URL interface is exposed.

Run `python tools/benchmark_analytics.py --sizes 1000 10000 100000` for a
reproducible synthetic test. It builds catalogs in temporary directories,
measures snapshot/import/query/export time, verifies aggregate counts against
SQLite, and runs a concurrent WAL writer. It reports measured writer commit
latency and process lifetime peak RSS. The simple SQLite count is a reference,
not a performance-equivalent substitute for the complete analytics result.
Peak RSS is cumulative across sizes in one process; query results and writer
latency depend on the host and current load. Synthetic catalogs contain no RF
recordings or real transmitter identities.

Operational SQLite query fallback remains available through the dashboard's
bounded views. If the optional package is missing, analytics calls produce a
clear installation hint and leave output destinations untouched. Existing
snapshots can be retained and analyzed later. Offline playback and survey
acquisition do not depend on this feature.
