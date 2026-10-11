# Native Mode S extended-squitter decoder

`rfatlas.protocols.ModeSDecoder` independently demodulates complex IQ into
112-bit Mode S DF17 extended squitters. It supplies ADS-B protocol evidence
only after all of the following succeed on the same frame:

1. Four 0.5 microsecond preamble pulses start at 0, 1, 3.5 and 4.5 microseconds,
   with quiet gaps throughout the remainder of the eight-microsecond preamble.
2. Each of 112 one-microsecond data cells has a sufficiently distinct energetic
   half, interpreted as first-half pulse = 1 and second-half pulse = 0.
3. The downlink format is 17.
4. The entire 112-bit frame has zero CRC24 remainder, with generator 0x1FFF409.

The decoder computes fractional sample-cell energy integrals directly. It
tries four timing phases per candidate rising edge, counts each candidate once,
and skips overlapping copies of a successfully decoded frame. It does not
repair bits, guess erasures, recover overlaid addresses, or borrow checksums
from neighboring frames. Candidate and accepted-frame budgets are configurable
and exhaustion is explicitly reported. One validated frame satisfies the
existing decoder evidence contract; this is protocol identification, not
authentication of the sender or truth of the transmitted data.

## Input and compatibility

Input must satisfy `DecoderInput`: finite, bounded complex IQ. Supported sample
rates are 2 to 10 MHz and the declared reception frequency must be within
1 MHz of 1090 MHz. Offline replay requires no receiver or external executable.
Windows and Linux use the same NumPy implementation. A 2.4 MHz RTL-SDR capture
meets the nominal rate requirement; analog receiver bandwidth, antenna,
interference and pulse distortion can still prevent decoding. Existing
narrowband captures at 250 kHz cannot reconstruct half-microsecond pulses and
are rejected honestly. No live aircraft capture was needed for development.

Only DF17 is implemented. DF18, short replies and formats with overlaid parity
remain unsupported. Validated frame records contain the frame hex, relative
sample position, DF and type code. They do not resolve identity databases,
reconstruct aircraft positions or decode audio. CRC validity supplies no
encryption indication: protection remains UNKNOWN. Any stored actual frame
records must remain private runtime evidence.

## Reproducible validation

Run `python -m unittest discover -s tests -p test_protocol_native.py -v`.
The eight tests include:

| Check | Result |
| --- | --- |
| Known synthetic DF17 IQ, 4 rates and 5 sample offsets, with carrier offset and additive noise | 20/20 decoded with exact frame bytes |
| Independent integer polynomial division versus decoder shift-register CRC | 12/12 agreement |
| Single-bit changes over all 112 positions in 12 valid frames | 1,344/1,344 rejected |
| Independent Gaussian-noise IQ windows | 0/100 false protocol identifications |
| Correct preamble and PPM with random DF17-looking payload/parity | 0/100 false protocol identifications |
| Missing preamble, damaged CRC, truncation, unsupported DF18, constant carrier | All rejected |
| Truncated first transmission plus checksum in a separate unsynchronized transmission | Rejected |
| Two separately transmitted identical frames | Exactly two validated frames |

Each noise window contains 4,096 samples at 2.4 MHz: the noise experiment totals
about 0.171 seconds. This is a bounded synthetic measurement, not a field
false-alarm guarantee. Tests do not establish certified sensitivity, tolerance
to multipath, overlapping messages, sample-clock error or long RF recordings.
There is no CRC error correction or likelihood calibration.

## Sources and licensing

Original RF Atlas implementation and tests use the project's MIT license. No
third-party decoder source, libraries, binaries, captured frames or aircraft
identities are incorporated. The fixture generator authors arbitrary synthetic
payloads and independently obtains parity by polynomial long division.

The protocol facts were checked against the author's
[Mode S introduction](https://mode-s.org/1090mhz/content/introduction.html)
(preamble, PPM, DF17 structure) and
[ADS-B error-control chapter](https://mode-s.org/1090mhz/content/ads-b/8-error-control.html)
(generator and direct ADS-B parity). These sources are references, not code
dependencies; their prose, figures and example aircraft frames are not copied.
The [FAA surveillance description](https://www.faa.gov/air_traffic/publications/atpubs/aim_html/chap4_section_5.html)
provides context for 1090 MHz extended squitter as an ADS-B link.
