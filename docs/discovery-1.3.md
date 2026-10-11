# RF Atlas 1.3: bounded discovery and investigation yield

Version 1.3 improves capture selection, records deferrals, and exposes a campaign evidence report in the existing Discovery Console. The detector and six frozen modulation/ML modules remain unchanged. No speed or sensitivity gain is inferred from reduced coverage or dwell.

## Audit and scheduler

The 1.2 Smart campaign lasted 232.188 seconds: 157 observations, 63 distinct catalog channels, 28 above the 10 dB investigation threshold. Ten quality-qualified channels had capture disabled. The other 18 received jobs: 15 completed, three pending, zero failed. Completed-channel conversion was 23.81%; completion among enqueued jobs was 83.33%. All 15 completed analyses attempted RDS, with three verified captures (20%). Historical average latency of 56.50 seconds combines waiting, capture and analysis; separate waiting was not recorded.

These are distinct catalog keys, not demonstrated emitters. Fine-resolution fragments near FM broadcasts caused redundant captures. The three RDS recordings contain 18, 22 and 22 accepted complete groups, one unique PI, no accepted Program Service name, and one broadcast-system entity in an isolated opt-in replay catalog. Every accepted group passed all four CRC/offset checks and the adjacent same-PI group requirement. Sixty-two group observations are evidence, not 62 stations. Identical stored evidence is idempotent; distinct acquisitions remain observations. Application identifier and raw-frame retention defaults stay off.

Follow-ups now favor wider candidates and higher contrast. FM recordings use at least 1.024 MS/s; the supported 1090 MHz range uses 2.4 MS/s regardless of profile name. A pending or recently completed FM capture within 100 kHz defers another capture for one hour. This capture-budget heuristic is below normal US 200 kHz adjacent spacing; it never merges channel records or establishes identity. It may defer close/cochannel signals. Failed captures do not suppress neighboring channels. Unchanged UNKNOWN channels wait six hours for routine retries; changed evidence, failures and later burst retries remain eligible after cooldown.

Identical saved catalog state with an empty queue produces 15 jobs under 1.2 and four under 1.3, with 11 FM deferrals. Three already verified channels are excluded in both. This is eligibility replay, not new reception or proof of increased identification accuracy.

The worker retains its 100-step/1,800-second bounds, queue cap, deadlines, profile permission and single-receiver lock. Jobs older than 60 seconds gain an alternating opportunity while due surveys continue. The continuous controller waits up to 30 seconds for due work instead of launching idle children repeatedly; stop remains interruptible. Decisions record quality, permission, already queued, cooldown, unchanged, FM passband, bandwidth and queue-capacity reasons. Candidate enumeration stays bounded to at most 5,000 rows per enqueue call; unexamined candidates are not presumed eligible.

## Accounting and measurement

Schema 9 adds decision and timing tables after a consistent backup. Existing observation timestamps, identifiers, captures and classification history are preserved. Reports use a read-only snapshot and explicit UTC interval. Ambiguous legacy timestamps stay excluded from time filters.

Reports separate observations, candidate channels, investigated candidate channels, modulation hypotheses, verified analysis instances, distinct protocols, retained devices/stations, current UNKNOWN channels, valid/invalid coverage, backlog and decisions. Candidate conversion uses completed jobs intersecting the observed channel cohort; completion uses jobs created in the window and status at report time. Decoder/verification rates use distinct analyzed signals. Analysis instances may include offline replays and are not acquisition counts. Current classifications and opt-in entities can change after a campaign. Repeated decision events and latest-per-channel decisions are separate. Missing historical telemetry stays null.

The CLI `discovery-report --since <UTC> --until <UTC> --output <new-private-file>` remains available. **Campaign evidence report** in the console uses the selected UTC window across all channels/sessions and compares the preceding equal-duration window. Comparison is descriptive: ranges, antenna, gain, dwell, resolution and changing RF activity affect yield. Report generation does not open the receiver.

Scanner timing separates receiver-process wall time from parsing/detection/database work. rtl_power combines startup, tuning, settling, transfer and FFT internally; their separate durations are unavailable. Process counts are not exact tuner-hop counts. Crop remains 20%, segment width 2 MHz and padding two requested bins per edge. The detector excludes two edge bins and retains DC candidates with warnings. Manual gain remains 15.7 dB in matched tests. No overlap, dwell or resolution reduction is used to claim throughput. Nonfinite AM acquisitions remain invalid with exponential backoff.

Identical-input replay returns the same 60 candidates on 27 valid saved rows, with equal output digests. Six frozen classifier/corpus modules match the 1.2 source hashes. Existing weak-signal and between-visit burst limitations remain.

## Configurable receive profiles

Existing editable inventory files are preserved. New defaults are not silently written over user settings. All new profiles start disabled; capture permission is explicit and revalidated. Fine bins are 1 kHz; narrowband coarse bins are 5 kHz, FM/ADS-B 25 kHz.

| Profile | Search and follow-up considerations |
| --- | --- |
| AM broadcast | Padded 0.52–1.71 MHz; capture disabled. Current tool/VM nonfinite failures prevent a usable low-band performance claim. Appropriate HF antenna and overload control matter. |
| FM broadcast | 87.9–108 MHz; minimum 1.024 MS/s follow-up, native RDS. A narrow spectral fragment alone may omit multiplex evidence. |
| Aviation voice | 118–137 MHz; no verified aviation protocol decoder. Brief transmissions need revisits. NAV below 118 MHz needs a separate profile. |
| Weather | Padded 162.39–162.56 MHz; analog evidence only, no SAME verification. [NWS lists the seven channels](https://www.weather.gov/nwr/station_listing). |
| Amateur VHF/UHF | 144–148 and 420–450 MHz; AX.25 UI/F0 AFSK1200 subset where compatible, FCS required. Reception is not assumed. |
| Land mobile | 450–470 MHz; capture disabled by default. DMR/P25/NXDN remain unverified; assignment matches are context. |
| Sensors | 902–928 MHz and optional 433.05–434.79 MHz search. rtl_433 covers audited WH2/WH5 only. Search labels do not assert US ISM allocation or device authorization. Longer targeted recordings can improve burst opportunity, but decoder prefixes remain bounded. |
| ADS-B | 1089–1091 MHz; 2.4 MS/s follow-up, DF17 preamble/PPM/CRC24 required. Antenna and line of sight limit reception; spectrum alone identifies no aircraft. |
| AIS | 161.95–162.05 MHz includes 161.975/162.025 MHz; no integrated verified AIS decoder. [USCG AIS information](https://www.navcen.uscg.gov/ais-frequently-asked-questions). |
| Satellite | 137–138 MHz; antenna, pass geometry and active transmission required. No identity inferred. |

Profiles are organizational examples, not an exhaustive allocation table. Generic wide receiver-envelope profiles remain available for bounded Full Spectrum work. The receiver limit is 1766 MHz; ordinary 2.4 GHz Wi-Fi/Bluetooth is outside it.

## Digital radio and dependencies

Primary sources were rechecked. [GopherTrunk architecture](https://github.com/MattCheramie/GopherTrunk/blob/main/docs/architecture.md) describes file replay and separate DSP/framing layers. Its [hunt interface](https://github.com/MattCheramie/GopherTrunk/blob/main/docs/hunt.md) offers offline discovery; aggregate system labels do not provide the per-frame integrity evidence required here. Its [license](https://github.com/MattCheramie/GopherTrunk/blob/main/LICENSE) is Apache-2.0 with component obligations. No local executable/toolchain integration was validated and no installer was run.

Installed DSD-FME completed a five-second-budget help probe. File input requires 48/96 kHz mono discriminator WAV, not raw CU8. Filtering, discrimination, resampling and modulation recovery need independent validation. [Synchronization source](https://github.com/lwvmobile/dsd-fme/blob/audio_work/src/dsd_frame_sync.c) distinguishes NXDN sync-only states. [P25 NID](https://github.com/lwvmobile/dsd-fme/blob/audio_work/src/p25p1_check_nid.cpp), [DMR data](https://github.com/lwvmobile/dsd-fme/blob/audio_work/src/dmr_data.c) and [NXDN framing](https://github.com/lwvmobile/dsd-fme/blob/audio_work/src/nxdn_frame.c) require more than a sync match. Relaxed CRC flags are unsuitable for verification. No tested metadata-only per-frame interface plus independent positive/corrupt IQ vectors was established. DMR/P25/NXDN remain UNKNOWN; no production adapter is registered. Next gate: independent control/data frames, bad-checksum, wrong-sync, truncation and noise tests, then existing-IQ validation. No voice-content decoding, decryption or key recovery was performed.

DSD-FME's [COPYRIGHT](https://github.com/lwvmobile/dsd-fme/blob/audio_work/COPYRIGHT) records mixed permissive/GPL-2.0 components. rtl_433 remains optional and separately installed. No third-party source, binary or weights were copied or bundled. Original changes remain MIT; mandatory dependencies remain NumPy/SciPy.

## Field and release validation

The bounded field campaign ran 920.000 seconds (15 minutes 20 seconds), with Quick scheduling first and Smart thereafter. It includes cooldown waiting and the final acquisition-deadline reserve; it is not 920 seconds of continuous sampling. Across ten representative profiles, 88 measurements yielded 84 valid visits and four invalid AM measurements. All nine other profiles received valid visits; no region was starved. Gross valid coverage was 127.75 MHz including revisits, unique valid coverage 12.25 MHz. There were 272 observations, 81 catalog channels, 34 quality-qualified channels, five completed investigations, five decoder attempts, three modulation hypotheses, one acquisition-time verified RDS analysis, two verified recordings after final decoder replay, and 79 currently UNKNOWN channels. No investigation failed or remained queued. Latest decisions account for all 81 channels: 47 below quality, 21 capture-disabled, eight redundant FM passbands, four cooldowns and one enqueued decision whose job subsequently completed.

| Metric | 1.2 Smart campaign | 1.3 field campaign |
| --- | ---: | ---: |
| Wall duration | 232.19 s | 920.00 s |
| Gross valid coverage | 45.45 MHz | 127.75 MHz |
| Valid MHz/wall second | 0.1957 | 0.1389 |
| Observations per valid MHz | 3.45 | 2.13 |
| Completed investigations / enqueued | 15/18 | 5/5 |
| Candidate-to-completed conversion | 23.81% | 6.17% |
| Decoder attempt rate | 100% | 100% |
| Verified-recording rate after final decoder replay | 20% | 40% |
| Unique retained RDS PI values | 1 | 2 |
| Failed investigations | 0 | 0 |
| Mean queue-to-completion time | 56.50 s | 17.61 s |

These campaigns are not equivalent experiments: ranges, RF activity, 60 versus 120 second revisit timing, duration and coverage differ. The aviation example was corrected to the voice band. Lower raw conversion is intentional suppression of redundant/low-quality captures, not evidence of more missed or failed jobs. Final decoder replay identifies two PI values: one repeats the previous campaign and one is new. Two retained broadcast-system records and 42 group observations are supported, with no accepted PS in these field recordings. No device was identified. Later analysis is explicitly separated from the original five acquisitions; the fresh confirmation and PS recovery are described below.

Receiver processes consumed 427.04 seconds; parsing/detection/database work consumed 2.20 seconds. Requested dwell totaled 264 seconds. This supports acquisition/startup as the bottleneck, without attributing the remainder to any one internal stage. Five investigations consumed 34.09 seconds of capture/analysis work (3.71% of campaign wall time). Separate capture timing was added after this pre-release campaign process started and is unavailable for these five jobs. Mean queue waiting was 10.79 seconds, maximum 33.00 seconds. Measured process rate was 0.09565/s over the full wall interval, invalid measurement rate 4.55%. Mean host CPU was 21.02%, maximum 59.39%; mean used host RAM 10.42 GB, maximum 11.00 GB. Software tests also ran on the host, so health values are not scanner-only resource attribution. Logical database growth was 1,122,304 bytes, final size 1,351,680 bytes. No report truncation occurred.

Four additional bounded live probes used current release code: 1090 MHz at two manual gains (five seconds each), supported 433 MHz sensors (ten seconds), and the AX.25 AFSK1200 workflow (ten seconds). All four completed without acquisition errors. Native Mode S, both installed rtl_433 adapters and native AX.25 were selected correctly; no valid sync-plus-checksum frames were recovered. All remain UNKNOWN with explicit low-ADC-amplitude/no-valid-frame reasons. No aircraft, sensor or packet station is claimed. ADS-B decoding examines the bounded four-million-sample prefix (about 1.67 seconds at 2.4 MS/s), not the entire five-second recording. Longer source recordings do not guarantee a larger analyzed burst opportunity.

The main campaign started while the release was being assembled: unchanged native decoder payloads retain the prior engine version stamp. Matched scanner, targeted probe and application checks use the final 1.3 code. Identical-input replay and independently constructed valid/corrupt decoder vectors remain separate from live reception claims.

The schema migration preserved all original rows in seven audited catalog/evidence tables, including 745 signals and 1,501 observations; table-content hashes matched before/after migration and SQLite integrity was OK. Existing timestamps, private identifiers and captures were preserved. Later live application validation is an explicit addition to this preserved catalog, not a migration rewrite.

Raw IQ, detailed frequencies, identifiers, logs, snapshots and health samples remain private in ignored directories.

## Matched checks and application outcome

Final RDS decoder version 1.3 channelizes wide IQ to 228 kS/s before nonlinear FM discrimination. A target whose filtered power is over 30 dB below the wide input is rejected. Independent tests recover the target PI with a stronger adjacent interferer and reject an off-channel RDS-only recording. Replaying the three original captures yields 21, 22 and 22 independently scalar-checked groups (65 total), the same one PI and no PS. Historical retained observations remain the original 62; replay counts do not add stations. The final decoder also rechecks all five field recordings. Its main-catalog replay adds an explicit classification revision, without acquiring another recording or creating another entity.

An ABBA live comparison interleaved the original 1.2 scanner wrapper and the final 1.3 wrapper across the same four ranges, with identical gain, bins, crop, three-second dwell and one-second integration. Each wrapper attempted eight measurements. Original: eight valid, 10.30 MHz gross, 37.69 seconds, 0.2733 valid MHz/s, 24 observations. New: seven valid, 8.30 MHz gross, 40.00 seconds, 0.2075 valid MHz/s, 18 observations. One new-wrapper measurement was rejected rather than ingested. Excluding the entire affected range from both arms leaves six visits and 6.30 MHz per arm: 0.2195 versus 0.2099 MHz/s. **No scan-speed improvement was demonstrated.** The small repeated trial cannot assign the invalid measurement to the wrapper rather than RF/tool/VM variation. No missing coverage is credited as a speed gain.

Full Spectrum checkpoint/resume was exercised on a bounded 6 MHz portion: intentionally interrupt before the second acquisition, resume the same run, finish three segments, preserve the first raw checkpoint hash. Actual elapsed time was 14.43 seconds and integrity was OK. This does not claim a complete receiver-envelope sweep.

A final 16.91-second Quick operation in the main application catalog added 12 observations and one new channel, completed one RDS investigation, and left no pending/running jobs. The main catalog now has 746 channels, 1,513 observations, 21 analysis revisions (including offline revision and live confirmation), 16 captures and two verified RDS channels. Default identifier retention remains off, so its retained-station count remains zero; the isolated opt-in field catalog holds the one PI record described above. All original 1,501 observation rows, 14 captures and 18 analysis rows retained exactly the same stored values; all 745 original channel IDs remain. Editable inventory bytes match the prior backup, and original enabled-region choices were restored.

Windows passed 226 tests, including both real installed rtl_433 fixture paths. Fourteen report/HTTP/accounting tests passed after the final additional report fields. Ubuntu 24.04 ran 213 tests: 210 passed, three skipped (Windows-only job branch and two optional real rtl_433 checks without that executable). An accessible Ubuntu 26.04 environment was not available; infrastructure was unchanged. Native protocol tests independently exercise valid, corrupt, truncated and noise controls; unpredictable live silence does not invalidate those functionality checks. No optional digital-radio integration was presented as validated.

The browser checks exercise the existing console, campaign report and live tuner/waterfall. The final receiver and scheduler lock checks require both locks to be acquirable after stop. SQLite, DuckDB, SigMF, replay, launcher/runtime ownership and frozen benchmarks are covered by the regression suite. Wheel/source publication checks exclude private recordings, identifiers, machine paths and runtime data. Local commits only; no push.

Next priorities: validate a file-only AIS or digital control-channel adapter against independently authored integrity vectors; improve burst-window selection within explicit decoding budgets; investigate low-band/nonfinite receiver-tool failures without changing drivers; and investigate a persistent acquisition backend only after a matched sensitivity/coverage benchmark supports it. More spectral fragments alone are not useful new identities.

## Final decoder outcome

Channelization recovered a second RDS PI from one previously UNKNOWN field recording. Its 21 groups passed an independent scalar check of all four CRC/offset blocks. The isolated field inventory now contains two distinct PI records and 42 group observations, with idempotent re-storage verified. This is **one new broadcast-system identifier relative to the previous campaign**. Across the same five acquired field recordings, final verified-recording yield is 2/5 (40%), compared with 3/15 (20%) in the prior campaign. The original acquisition-time analysis remains one verified result; the second was recovered offline by the final decoder and is not another live acquisition. Current field UNKNOWN count is 79.

A fresh two-second main-catalog recording confirmed that new PI with 21 valid groups and a complete accepted Program Service name. The PI exactly matches the recovered field identifier; the name remains private. The original three archived RDS recordings still have no accepted PS. Main catalog totals after this confirmation are 746 channels, 1,513 observations, 16 captures, 21 analysis revisions, two verified RDS channels and 744 UNKNOWN channels. Its identifier-retention default remains off. The separate field inventory's two retained PI records are broadcast-system identifiers, not authenticated physical transmitters.

Campaign reports now separately count analysis-time verification instances and source-digest-deduplicated capture-window verification. The latter includes later successful analysis of an earlier recording without inflating acquired-recording counts. The field report consequently shows one acquisition-time verified analysis, two verified source recordings and two retained systems. One original UNKNOWN analysis and its later verified revision remain available as provenance.

One full Windows run rejected a valid rtl_433 WH2 fixture intermittently; this optional external adapter was unchanged. A prior release had seen a similar WH5 rejection. All 200 subsequent isolated WH2/WH5 repetitions passed. This does not establish a root cause or eliminate the remaining false-negative risk; unsupported or empty output remains UNKNOWN and never creates an entity. Corrupt/noise controls remain mandatory in the release run.

Final release gates passed: 226 Windows tests; 213 Ubuntu 24.04 tests run with 210 passes/three documented skips; and 36 additional tests against the installed final wheel outside the source checkout on Ubuntu. Browser QA confirms one distinct verified protocol spanning two verified main-catalog channels, report schema 2, two live tuner/waterfall updates, no JavaScript errors, final idle mode and both receiver/scheduler locks released. The wheel contains 61 files and the source distribution 149, with zero automated publication findings. Optional rtl_433's intermittent false-negative risk and inaccessible Ubuntu 26.04 remain explicit limitations.
