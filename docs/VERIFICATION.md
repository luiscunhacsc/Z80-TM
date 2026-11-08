# Verification record

Run date: 2026-11-08 (Europe/Lisbon).

Environment: Windows 11, build 26300, x86-64; Intel Core i5-8350U @ 1.70 GHz;
portable CPython 3.12.10 from the official python.org embeddable distribution.
Only the Python standard library is used. The local runtime is ignored by Git.
Linux compatibility is intended; **Linux has not been tested** in this run.

## Commands

```powershell
& '.\.tools\python312\python.exe' -m unittest discover -v
& '.\.tools\python312\python.exe' -m z80tm demo --json
& '.\.tools\python312\python.exe' -m z80tm demo --trace --cycles
& '.\.tools\python312\python.exe' -m z80tm coverage --family ED --json
```

For deterministic regressions without the independent fixture files:

```console
python -m unittest tests.test_machine tests.test_milestone tests.test_extended tests.test_interrupts tests.test_cli -v
```

For a standalone offline fixture audit, optionally filtering opcode encodings:

```console
python -m tools.check_vectors
python -m tools.check_vectors --family "ed "
python -m tools.check_vectors --file tests/fixtures/singlestep.json
```

The full suite takes several minutes because each access physically traverses
the single tape, including randomly located high RAM. The final validation
partitions all discovered test IDs into three independent processes: DD/DDCB
vectors, FD/FDCB vectors, and all remaining tests. Each process uses the same
strict runner and builds its own fixed table. Every discovered test runs once.
`verification/tests-windows.txt` combines their `unittest.TextTestRunner`
outputs with aggregate counts and elapsed wall time. The normal discovery
command above runs the same tests sequentially.

**Final result: 56 tests passed, including all 1,872 independent fixture
subcases, with no failures, errors or skips.** The three batches completed in
523.982 seconds of wall time.

The demo passed with detailed tracing disabled and enabled. Its architectural
result remains A=80, B=00, HL=4800, F=01, PC=000D and the eight diagonal bytes.
The expanded suite covers CLI interrupt scripts, IM0 response files/exhaustion,
HALT recovery, action limits and transition-budget resumption during interrupts.
The IM2 wraparound test loads no default program byte, preserving its vector
high byte at address zero.

The newly enabled prefix audit exposed a mismatch in `FD 37 0000`: the decoder
retained Q across an ignored index prefix. DD/FD now clear Q and the saved flag
history before the next opcode fetch. Dedicated regressions cover both SCF/CCF,
repeated and mixed prefixes, and a preceding ALU instruction. This matches the
[published hardware observation](https://github.com/hoglet67/Z80Decoder/wiki/Undocumented-Flags#scfccf).

## Independent source and comparison contract

Source: [SingleStepTests/z80](https://github.com/SingleStepTests/z80), v1 files,
commit [`ebe1875d48f374bcfd4b505d8eb8ee751568b5f7`](https://github.com/SingleStepTests/z80/commit/ebe1875d48f374bcfd4b505d8eb8ee751568b5f7).
These are JSMoo-generated NMOS tests with simplified memory-access pulses:
an independent software oracle, not hardware measurements.

| Local file | Selection | Vectors |
| --- | --- | ---: |
| `tests/fixtures/singlestep.json` | First two per unprefixed opcode; four for each of the eight milestone opcodes | 520 |
| `tests/fixtures/prefixes.json` | First test from each prefixed v1 file: 256 CB, 80 ED, 252 DD, 252 FD, 256 DDCB, 256 FDCB | 1,352 |
| Total | Every selected object is unmodified | 1,872 |

Wrappers record the pinned commit and per-file Git blob IDs. The fixture-coverage
test checks the prefix selection against `tools/fixture-index.json`, independently
of the decoder. The upstream MIT license is included as
`tests/fixtures/LICENSE.SingleStepTests`. Fetch the corresponding v1 JSON file
at the pinned commit and take the leading array entries to reproduce selection.
All normal tests and audits run offline.

Both fixture files use the same `tools.check_vectors.check_vector` comparison:
A/F/B/C/D/E/H/L, PC/SP, IX/IY, alternate AF/BC/DE/HL, I/R, IFF1/2, IM,
**WZ/Q/P and all eight F bits**, every fixture final RAM byte, port effects,
total T-states, summed cycle durations, the tape clock, and the order,
addresses, data and start offsets of non-internal transactions.

Comparison limits:

- The selected HALT fixtures already use the same resume PC as this API;
  PC is compared directly without adjustment.
- SCF/CCF receive fixture Q and compare full F; the previous flag mask is removed.
- Fixture `ei` is not used as an oracle. EI-delay boundaries have dedicated tests.
- M1 events are mapped to reads for the fixture transaction comparison. Refresh
  pin states/addresses and internal-cycle addresses are not compared. Explicit
  refresh and IM0 tests exercise selected fetch classifications separately.
- Interrupt scenarios are local expectations, not independent interrupt vectors.

No redcode core, zexdoc/zexall or hardware oracle has been run. Selected vectors
do not establish exhaustive opcode, multi-instruction or pin-level conformance.

## Strict-runner benchmark

Command: `python -m z80tm demo --json`, tracing disabled. Table generation and
transition-loop time are measured separately; neither is a guest timing value.
This sample reused the table from one test batch while other fixture batches
were running. Its table-build field measures a cache lookup, and its execution
time includes resource competition; it is not a cold-start or isolated
throughput measurement.

| Measurement | Observed |
| --- | ---: |
| Guest instructions | 36 |
| Guest base T-states / tape clock | 247 |
| TM transitions | 2,099,921 |
| Head moves | 2,092,890 |
| Accumulated host transition-loop time | 2.483 s |
| Table lookup time (cached) | 0.000002 s |
| Table states | 218,686 |
| Alphabet symbols | 5,406 |

These are one observed run, not stable throughput guarantees. Process startup,
shutdown, tape initialization, boundary snapshot formatting and terminal output
are outside the accumulated transition-loop time. This measurement predates
the Spectrum tape slots and the optional compiled literal TM loop; its counts
describe that earlier tape layout. The rule table is shared and
cached within each process. Both demo artifacts reuse the completed batch's
table, so their reported table-build times are cache lookups.

Raw measurements are in `verification/demo-windows.json`; the rendered
instruction/bus trace is in `verification/demo-trace.txt`.
