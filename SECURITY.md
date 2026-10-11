# Security and privacy

RF Atlas serves a local loopback interface. Do not expose it to an untrusted network. It is a single-receiver, receive-only application, with no transmitter, cryptographic key recovery or encryption bypass.

Recordings, databases, diagnostics and decoded identifiers remain under the private runtime root. Raw frame and identifier retention are separate settings and default off. Raw bits/bytes may contain identifiers or content. AI models are bounded JSON weights with an adjacent SHA256; no pickle or arbitrary model code is executed. Checksums detect changes but are not signatures.

Do not put private captures or credentials into public issues. For a suspected vulnerability, use GitHub's private vulnerability reporting feature if enabled; otherwise open a minimal issue requesting a private reporting channel without publishing exploit details or private data.

Releases are experimental prereleases until hardware endurance and broad field recognition are demonstrated. Verify artifact checksums against the release manifest. The installer never changes USB drivers, global execution policy or system Python.
