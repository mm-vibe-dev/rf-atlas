# RF Atlas 1.7 prerelease

The default DSP pipeline now reports separate detection, characterization, family, modulation, protocol and encryption evidence. Structural waveform hypotheses remain tentative and uncalibrated. Only native decoder integrity can verify a protocol; encryption remains UNKNOWN without protocol-specific evidence.

## Structural analysis

`rfatlas.recognition.classify` accepts `analysis_level="fast"`, `"deep"` (default), or `"forensic"`, `max_seconds` from .001 to 2, and a cancellation event. Fast computes bounded Welch entropy, DC-relative sideband imbalance and slow keying. Deep conditionally searches cyclic prefixes and phase alphabets. Forensic grants the same finite search bank more time. The structural budget excludes the existing initial classifier and is checked between bounded operations; this is cooperative cancellation, not a hard real-time deadline. Progress and exhaustion appear in structural evidence.

CP analysis uses at most 65536 samples and FFT sizes 32 through 2048, prefix divisors 4/8/16/32, at least 32 symbols, contrast against background, and agreement in disjoint halves. It suggests OFDM_LIKE; oversampling, multipath, weak signals and non-bank numerologies may fail. No absolute CFO is inferred from ambiguous CP phase. BPSK/QPSK/8PSK hypotheses require stable upper envelope and phase moments in both halves, with line-dominance negatives. Clipped analog audio can remain ambiguous. Keyed-carrier intervals do not establish Morse/CW versus slow OOK or interference. DC-relative spectral asymmetry does not identify USB/LSB without a carrier reference. QAM, weak SSB, GFSK/MSK and arbitrary chirps remain limited.

## Protocol evidence

Existing DMR, RDS and packet integrations retain their integrity gates. Native POCSAG support is a strict tested subset: exact batch sync, all 16 BCH(31,21) and even-parity codewords, plus a 576-bit alternating preamble or two adjacent valid batches. It tries 512/1200/2400 baud and both polarities on a bounded input prefix. There is no error correction, pager address/message extraction, FLEX support or encryption assessment. Frames expose only aggregate rate/integrity metadata. Fixture validation does not establish live receiver performance. Short recordings may lack a complete batch. BCH/parity is the integrity check; it is not a CRC. The documented format source is [ITU-R M.584-2 Annex 1](https://www.itu.int/dms_pubrec/itu-r/rec/m/r-rec-m.584-2-199711-i!!pdf-e.pdf). CP design background: [van de Beek et al., time/frequency offset estimation](https://portal.research.lu.se/en/publications/ml-estimation-of-time-and-frequency-offset-in-ofdm-systems/). No reference implementation was copied.

## Dataset and evaluation tools

Run from the source checkout:

```powershell
python tools/inventory_dataset.py --help
python tools/evaluate_advanced.py results.json --split validation
python tools/experiment_structure_ml.py train --model experimental.json --output validation.json
```

Manifests validate CU8 size/SHA and declared capture/source groups. Independent ground truth needs an explicit basis, source and confidence. Prior catalog predictions are never automatically labels. Group and duplicate-content cross-split leakage are reported; undocumented session independence cannot be certified. Inventory output is exclusive-create to preserve existing manifests. Keep manifests/results private.

The 216-case challenge per split covers 24 waveform families, three rate/duration/CFO sources and three SNRs. Stochastic sources are grouped across SNR variants. New seeds are synthetic independence, not real-world emitter independence. CW uses strict ANALOG truth so an ambiguous keyed-carrier fallback counts as an error. Chirp/pulse/interference UNKNOWN truth does not establish digital content. Preserve final holdouts until source/model freeze. Report family confusion, per-modulation failures and false positives; heuristic score reliability is diagnostic, not calibrated probability.

The optional structure ridge tool is an isolated synthetic-only research experiment. It uses class-balanced regression, validation temperature fitting and rejection, with JSON parameters and bounded validation. It is not an application backend. Existing ML remains opt-in; no synthetic model is promoted to verified status.

## Investigation and history

The UNKNOWN panel adds configurable recurrence and noise weights and a read-only ranking action. The API `/api/investigation-priorities` ranks at most 10000 recent unresolved-protocol rows and returns bounded score components and limitations. Six weights are configurable from 0 to 20. Ranking starts no receiver operation. Unknown signals are not inherently suspicious. Observation hour counts and center span describe sampled catalog evidence, not occupancy, drift velocity or transmitter identity. Investigation details expose hierarchy, conflicts and structural diagnostics.

## Audio and integrity

Streaming polyphase conversion retains causal FIR history and exact output-clock phase across arbitrary blocks. Matched AM tests produce identical PCM to the previous implementation; the largest measured CPU gain is at 250 k samples/s. This does not establish intelligibility or browser continuity. Existing mode, gain, squelch and de-emphasis behavior remains.

IQ replay now hashes exactly the bounded bytes it consumes, avoiding a separate checksum/read race and redundant file pass. Metadata has a 64 KiB bound. Cancellation propagates to structural processing. Reanalysis keys include DSP, analog and POCSAG source dependencies; volatile elapsed timing is excluded from identification idempotence. No historical rows or recording formats are migrated.
