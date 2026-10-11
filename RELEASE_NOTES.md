# RF Atlas 1.9.0rc1

Experimental prerelease by mm-vibe-dev. Adds opt-in CPU RF inference, selected bounded digital bit hypotheses, common validated frame/message metadata, explicit DMR FEC diagnostics, sanitized actual-sample doctor checks and user-scope Windows installation scripts. DSP remains the default; existing verified protocol evidence is preserved.

The optional synthetic-trained model scored 62.5% family accuracy on the synthetic holdout but 0% on the narrow 17-recording historical RF reference, versus DSP's 70.6%. It is unsuitable for promotion over DSP. Scores are uncalibrated and cannot verify a protocol. Models are generated locally; no external checkpoint is bundled.

The Windows receiver currently opens but returns zero samples. Recovery, new live audio/protocol reception and endurance are unverified. Software regression and generated fixtures cannot establish hardware recovery. See docs/discovery-1.9.md for measured limits and interpretation.

Verify artifacts against SHA256SUMS. The Windows scripts archive contains install, launch, upgrade, repair and uninstall scripts; Python 3.11+ is required. Drivers and system settings are not modified. Existing runtime data remains separate. Ubuntu installs use isolated wheels or pipx. No PyPI package availability is claimed.

Original source is MIT licensed; bundled uPlot notices are preserved. External SDR binaries, drivers, RF recordings, private databases, decoded identifiers and downloaded model checkpoints are excluded. The prepared website is not advertised as live until Pages deployment is verified.
