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
```

For the quick deterministic regression suite without the independent fixtures:

```console
python -m unittest tests.test_machine tests.test_milestone tests.test_extended tests.test_cli -v
```

The full independent fixture suite takes several minutes because each access
physically traverses the single tape, including randomly located high RAM.

**Final result: 32 tests passed in 116.331 seconds**, including all 520 fixture
subcases, with no failures or skips. The demo passed both with detailed tracing
disabled and with instruction/bus-cycle tracing enabled. The milestone was
built and its initial 15 tests passed before the unprefixed extension was added.

## Independent source and comparison contract

Source: [SingleStepTests/z80](https://github.com/SingleStepTests/z80), v1 files,
commit [`ebe1875d48f374bcfd4b505d8eb8ee751568b5f7`](https://github.com/SingleStepTests/z80/commit/ebe1875d48f374bcfd4b505d8eb8ee751568b5f7)
(2026-04-10, “Re-generate the tests.”). The repository describes these as
JSMoo-generated tests with NMOS selected and simplified memory access pulses.
They are an independent software oracle, not hardware measurements.

`tests/fixtures/singlestep.json` contains the first two tests for each of the
252 non-prefix opcode files, except that the eight milestone files have four
tests each: **520 fixtures total**. Each original test object is unmodified;
the wrapper records the commit and per-file Git blob IDs. The upstream MIT
license is included as `tests/fixtures/LICENSE.SingleStepTests`. To reproduce
the selection, fetch `v1/XX.json` at the pinned commit and take those leading
array entries. Tests are fully offline; the normal test command fetches nothing.

Compared: A/F/B/C/D/E/H/L, PC/SP, IX/IY, alternate AF/BC/DE/HL, I/R, IFF1/2,
IM, all fixture final RAM bytes, port effects, total T-states, and the order,
addresses, data and start offsets of non-internal bus cycles.

Explicit exceptions:

- HALT: this project exposes the resume PC, one byte past the opcode, as required
  by the requested milestone. The fixture convention is normalized to this PC.
- SCF/CCF: comparison excludes F bits 3 and 5 because Q-dependent behavior is
  not implemented. Other supported opcode fixtures compare all eight flag bits.
- WZ, Q and P are not modeled. Fixture `ei` is not used as an oracle; our
  EI-delay boundary convention has dedicated EI/NOP/DI tests.
- Fixture pin states and refresh addresses are not compared; the bus-cycle
  extraction checks the memory/I/O transaction pulses and their placement.

No redcode core, zexdoc/zexall or hardware oracle has been run. Prefix and
interrupt tests are planned, not passing tests hidden behind skips.

## Strict-runner benchmark

Command: `python -m z80tm demo --json`, tracing disabled. Table generation and
transition-loop time are measured separately; neither is a guest timing value.

| Measurement | Observed |
| --- | ---: |
| Guest instructions | 36 |
| Guest base T-states | 247 |
| TM transitions | 985,127 |
| Head moves | 983,722 |
| Accumulated host transition-loop time | 0.441 s |
| Table generation time | 7.637 s |
| Table states | 369,059 |
| Alphabet symbols | 478 |

These are one observed run, not stable throughput guarantees. Process startup,
shutdown, tape initialization, boundary snapshot formatting and terminal output
are outside the accumulated transition-loop time. The strict `Machine.step`
method is the only execution path; no accelerated runner is used. Transition
counts changed from the initial milestone as tape slots and boundary routines
were extended. The final demo's architectural result remains exactly specified.

Final test results are recorded in `verification/tests-windows.txt` (PowerShell
stderr-wrapper lines removed, test output preserved); the final benchmark JSON
is in `verification/demo-windows.json`. The rendered instruction/bus trace is
in `verification/demo-trace.txt`.
