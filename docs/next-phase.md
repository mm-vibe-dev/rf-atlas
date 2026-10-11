# Recommended next phase

1. Ubuntu 24.04 software tests now pass on an existing runner. Execute the remaining portable CI matrix, Ubuntu 26.04 and physical V4L validation. Verify cancellation, parent termination and USB removal on each OS.
2. The 0.4.0 DSP engine improves every main-suite family, but long/duty NFM/CW regressions and burst-envelope confusion remain. Fix those failures using development data and a fresh final holdout. Prioritize 4FSK discrimination, multi-window analysis, robust carrier/clock tracking, explicit pulse-shape recovery and interference negatives. Treat the current holdout as a regression set after this release. Calibrate confidence before promoting hypotheses into catalog truth.
3. Keep ML/hybrid experimental: the small model's original-generator BPSK recall is 1/72 and hybrid rejects heavily. Evaluate licensed external fixtures and distribution shifts before changing default selection.
4. Migrate remaining legacy ingestion/export orchestration where it improves maintenance. Preserve the single local receiver owner, receiver locks and additive database migrations.
5. Expand optional decoder adapters with safe fixed arguments and independently validated RF fixtures. Keep protocols/protection UNKNOWN without emitted evidence; never include key or decryption options.
6. Improve the implemented localhost radio interface and bounded catalog API using measured acquisition latency and user feedback. Keep expensive analytics detached from the operational database.

Version 1.0 is designed for one local RTL-SDR receiver. Distributed receivers,
multiple-receiver scheduling, remote receiver management, synchronization,
triangulation and transmitter geolocation are excluded from its roadmap.
No repository push or public publication is authorized.
