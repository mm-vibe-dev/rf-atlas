# Contributing

RF Atlas is developed by mm-vibe-dev. Open an issue with reproducible steps, OS, Python version and sanitized `rf-atlas doctor --skip-receiver` output. Keep serials, paths, RF recordings, decoded identifiers and personal observations private.

Create a virtual environment and install `.[plots,analytics]`. Run `python -m unittest discover -s tests -v`, `python tools/release_check.py --tracked`, then build with `python -m build`. Test the installed wheel outside the source checkout. Hosted CI uses generated IQ and no receiver.

Preserve original captures and verified protocol history. A decoder consumes `DecoderInput` and returns `DecoderEvidence`; independent synchronization and integrity checks must validate a frame before verification. Failed CRCs, model scores, frequency allocations and installed executables cannot establish a protocol. Add positive generated fixtures and negative integrity cases. Describe available, fixture validated, replay verified and live verified evidence separately.

Keep receiver work bounded, cancellable and under the shared RTL lock. Do not transmit, replace drivers, guess encrypted content or launch competing receiver acquisition from an external decoder. Use bounded offline samples instead.

ML changes must document representation, checksum, training groups, validation selection and a frozen evaluation split. Report abstentions, per-class failures and real receiver limitations. Never tune on a holdout and then call it unseen. Changes to privacy defaults require explicit review.

Pull requests should describe the concrete problem, resulting behavior, validation and limitations. Contributions must be original or have compatible, documented permission and preserved notices.
