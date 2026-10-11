# Recording interchange and decoder adapters

RF Atlas can import and export a deliberately limited SigMF recording subset without installing a SigMF library. Interchange copies original unsigned interleaved I/Q bytes; it never rewrites, resamples or deletes the source recording. Destination files must not already exist. Native SHA256 verification precedes export; SigMF SHA512 verification precedes import when supplied. The default raw-data limit is 64 MiB and the metadata limit is 1 MiB. All examples below use synthetic frequencies.

```python
from rfatlas.sigmf import export_sigmf, import_sigmf
from rfatlas.iq import replay

data_path, metadata_path = export_sigmf('example.cu8', 'export/example')
local_path, native_metadata = import_sigmf(metadata_path, 'import/example')
result, metadata = replay(local_path)
```

The supported files are a `.sigmf-data` CU8 dataset and its adjacent `.sigmf-meta` JSON file, with one channel, one explicit capture starting at sample zero, a positive RF center frequency, and a sample rate of at least 1 ksample/s. At least 4096 complex samples are required. The exporter writes SigMF version 1.2.6, standard `core:datatype`, `core:sample_rate`, `core:frequency`, `core:sample_start`, `core:sha512`, and UTC `core:datetime` when a timestamp exists. Import accepts the supported subset of version 1.x metadata. This is a subset implementation, not a claim of support for every SigMF feature.

Multichannel data, multiple capture segments, nonzero capture starts or dataset offsets, non-conforming headers/trailers, externally named datasets, metadata-only recordings, and required unknown extensions are rejected. Unsupported datatypes are rejected rather than reinterpreted. Bounds, hashes, sample-rate and center metadata, timestamp syntax and supplied target offsets are validated. Truncated, odd-sized or oversized data fail before output is created. Writes are exclusive, and incomplete new outputs are removed on failure. The reader does not fetch external paths or network resources.

The RF Atlas tuning offset is the receiver center minus the desired frequency. It is distinct from the SigMF `core:offset` sample index. RF Atlas exports its desired frequency, tuning offset, raw SHA256, and the acquisition allowlist below as JSON text prefixed `RF Atlas interchange v1: ` in the standard free-text `core:description` field. This is an application convention, not a SigMF extension or a standard meaning of that field. Other readers may ignore it; the standard RF center/sample-rate/data remain usable. Native capture identifiers, catalog links, classifications and transient `analysis_quality` are not exported. Imported annotations and the full upstream metadata are retained under `source_sigmf_metadata` in the native sidecar, solely as archival evidence; they do not verify a protocol or replace classifier output.

The acquisition allowlist preserves `gain_db` (finite number), `antenna` and `device_identity` (text), `sample_rate_reported_hz` (positive finite number or null when unavailable), `sample_rate_basis` (text), `bias_tee` (boolean), and `created`, `acquisition_start`, `acquisition_end` (timezone-aware ISO-8601 timestamps, or null when unknown). Supplied start/end times must be ordered. Existing timestamp strings retain their original timezone notation in the native roundtrip, while standard SigMF `core:datetime` uses UTC/Z. A preserved `created` timestamp must agree with the SigMF capture timestamp. The standard sample rate remains the configured/native `sample_rate`; a tool-reported estimate is retained separately without changing DSP assumptions. Invalid allowlisted values fail import and export. Unknown fields are not promoted into native acquisition metadata.

An external recording without this description uses the RF center as the desired frequency. Override it explicitly when importing a channel offset from center:

```python
local_path, metadata = import_sigmf(
    'external.sigmf-meta', 'import/channel', desired_frequency_hz=100_000_000)
```

Missing source timestamps remain absent. Missing upstream SHA512 is reported explicitly; a newly computed native SHA256 protects subsequent copies/replay but does not prove the upstream recording was intact before import. An RF Atlas roundtrip additionally verifies the preserved raw SHA256. Source metadata and arbitrary annotations are untrusted, including any labels they contain. Exports retain private acquisition information, including device identity and tuning details: keep both the data and metadata private under the project's existing ignored data locations, and choose export destinations accordingly. No capture license is invented by the exporter.

## Future protocol decoder contract

`rfatlas.decoders` supplies `DecoderInput`, `DecoderEvidence`, the structural `ProtocolDecoder` interface, and `run_decoder`. It supplies **no protocol decoder** and makes no claim to decode a live waveform. Adapter tests use an explicitly named synthetic test protocol only.

`DecoderInput.from_recording(path)` uses the existing native checksummed reader and translates IQ to the desired frequency. The adapter receives a finite, bounded, read-only complex array, sample rate, analysis frequency, start-sample offset, and raw source SHA256 when available. `sample_sha256` identifies the actual complex analysis samples separately from the original CU8 file. Input validation rejects short, nonfinite, noncomplex or invalid-rate data.

An adapter must have a name/version and return `DecoderEvidence` bound to `input_sample_sha256`. Frame counts distinguish synchronization observations, valid checksums, and the intersection of both on the same frames. The result remains protocol `UNKNOWN` until at least one frame has both synchronization and checksum verification, with text references identifying the synchronization rule and integrity rule. Counts must be internally consistent; stale input hashes, mutated inputs, malformed evidence and missing references fail. Adapter exceptions propagate as failures. Modulation confidence cannot substitute for these checks. Protection remains `UNKNOWN`, and this interface supplies no probability/confidence calibration.

This validates an evidence contract, not the correctness of a third-party decoder. A future adapter still requires protocol specifications, independently sourced vectors, wrong-protocol/noise/bit-error negatives, bounded execution, and a license review before its claims should be trusted. Tests of the current contract do not count as protocol decoding validation.

## Sources and licensing

The independently written implementation follows the [official SigMF specification](https://sigmf.org/), whose [citation metadata](https://github.com/sigmf/SigMF/blob/main/CITATION.cff) identifies version 1.2.6 and CC-BY-SA-4.0. The specification describes CU8 as interleaved unsigned I/Q components and defines capture timing, RF center and SHA512 metadata. No specification text or third-party implementation code was copied into the source. RF Atlas's implementation remains under the repository MIT license, and adds no runtime dependency. A recording's own data license is separate from the specification and application licenses.

Run the focused generated-IQ tests with:

```text
python -m unittest discover -s tests -p test_interop.py -v
```
