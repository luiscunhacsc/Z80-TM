# Z80 on a single-tape Turing machine

Python 3.11+, standard library only. Milestone 1 is complete. The first extension
implements all **252 non-prefix opcodes**, including scripted 16-bit port I/O.
This is **not yet a complete Z80**: CB/ED/DD/FD, interrupt acceptance and HALT
recovery remain open. See [coverage and limitations](docs/COVERAGE.md).

## Run

From this directory with Python 3.11 or later:

```console
python -m z80tm demo
python -m z80tm demo --trace --cycles
python -m z80tm run program.bin --load 0x20ff --pc 0x20ff --budget 20000000
python -m z80tm run program.bin --register A=0xa5 --register HL=0x1234 --instructions 1
python -m z80tm coverage
python -m unittest discover -v
```

Use `python3` on Linux if that is your Python 3.11+ executable. No installation
or packages are required. The system `python` on the development machine was
3.10; a portable **CPython 3.12.10** was downloaded into the ignored `.tools`
directory. On this Windows workspace, use:

```powershell
& '.\.tools\python312\python.exe' -m z80tm demo
& '.\.tools\python312\python.exe' -m unittest discover -v
```

`--json` emits measurements and state; `--peek 0x1234` inspects memory. Load and
PC are independent; PC defaults to the load address. Binaries may wrap through
`$FFFF` to `$0000`, but may not exceed 64 KiB. Unspecified RAM and registers are
zero. A transition budget limits the whole run, including unfinished
instructions. Exit codes are 0 for HALT/instruction limit, 2 for an unsupported
opcode or CLI input error, 3 for a transition budget, and 4 for a machine/port
script error. Budget exhaustion never means successful HALT.

The demo prints `80 40 20 10 08 04 02 01` at `$4000,$4100,...,$4700`, renders
the MSB-left diagonal, and ends at `A=80 B=00 HL=4800 F=01 PC=000D` after
**36 instructions / 247 base T-states**, including HALT.

For reproducible I/O, `--ports ports.json` supplies a sequence such as:

```json
[["r", 44030, 82], ["w", 21119, 82]]
```

With initial `A=$AB`, program `DB FE D3 7F 76` reads `$52` from port `$ABFE`
and writes `$52` to `$527F`. Rules check direction, the full 16-bit address,
and expected output data, and consume each record on tape. Missing or
mismatching records produce a distinct error. `CPU.port_events` and JSON
reports expose completed port transactions.

## Tape and rules

One sparse, two-way-infinite tape holds integers from a finite alphabet. Blank
is 0. Head movement is always -1, 0 or +1. The initial populated region is:

```text
START | marker,byte for each register/scratch slot | RAM
      | PAGE,256 RAM bytes | PAGE,256 RAM bytes | ... (256 pages) | END
      | active port record | later port records | port-script end
```

Registers include the main and alternate sets, PC, SP, IX/IY, I/R, IFF1/2,
IM, EI delay and HALT. IX/IY/I/IM are preserved storage pending prefix support.
Scratch includes instruction address/opcode, address bytes, data, ALU results,
last instruction's T-states, and a finite continuation token. All state needed
at instruction boundaries is on tape. See `layout.py` for exact offsets.

`compiler.py` and `unprefixed.py` generate the fixed table without accepting a
binary, load address or initial registers. Python arithmetic computes finite
truth tables **during generation only**. Reusable routines implement byte
copies, fetch, decode, addressing, arithmetic and flags. Subroutine returns
are selected by continuation symbols written to tape; there is no runtime
Python microcode interpreter or guest call stack.

Addressing reads the high address byte, walks past that many page markers,
temporarily marks the selected page, returns to obtain the low byte, and walks
to the selected cell. Reads carry one byte back in finite control. Writes mark
the target cell, return for the data byte, and physically revisit the target.
Markers are restored before retirement. RAM writes therefore affect subsequent
fetches, including self-modifying code. All test programs share one table.

The current table has **369,059 states and 478 symbols**. Sparse rows use a
default rule plus explicit exceptions; an identity write is stored as `None`.
`Table.expanded_rule(state, symbol)` expands this notation into an ordinary
`(next_state, new_symbol, move)` triple. No rule is created during execution.
Table generation takes several seconds and consumes substantial host memory;
the table is cached for reuse within a process.

## Stepping, traces and audit

```python
from z80tm.harness import CPU

cpu = CPU(bytes.fromhex("3E A5 21 34 12 77 76"), load=0x20FF)
instruction = cpu.step_instruction(budget=2_000_000, trace_cycles=True)
print(instruction.registers, instruction.tstates, instruction.cycles)
cpu.run(budget=20_000_000)
```

`machine.py:Machine.step` has only the table, current state, current head and
tape as execution inputs. It reads `tape[head]`, looks up one rule, writes that
cell, moves the head, and changes state. Its two counters only observe the
transition and movement. It imports no CPU, opcode, addressing or ALU module.
There are no guest-address shortcuts, scan batching, JIT paths or native cores.

The harness initializes/inspects the tape, detects generated boundary/error
states, applies budgets, and collects traces. It never writes guest state
during execution. The generic runner can execute the demo without the CPU
harness; a regression test does exactly that. `CPU.halted` reads the guest
latch; `CPU.stopped_on_halt` also requires the appropriate boundary. A budget
that expires while HALT is retiring can resume and finish retirement.

Rules write per-instruction base T-states onto tape. The host totals these as
an observation. Generated event states describe M1, memory read/write, port
read/write and internal intervals, with addresses/data where applicable.
Independent fixtures check bus order, addresses, data and start times. These
are **base bus-cycle traces**, not a pin-level Spectrum contention model:
refresh phases, internal bus addresses, WAIT/BUSRQ and interrupt cycles are
still open. There is no guest-visible clock yet; future interrupt scheduling
must use a tape-resident clock updated by rules, not the harness counters.

## Verification and performance

The offline suite includes all three requested programs, explicit edge and
wraparound vectors, budget resumption, port scripts, and **520 unmodified
JSMoo vectors covering all 252 non-prefix opcodes**. Fixtures are pinned to
SingleStepTests/z80 commit `ebe1875d48f374bcfd4b505d8eb8ee751568b5f7` with blob
IDs and the upstream MIT license. This is selected coverage, not an exhaustive
Z80 conformance claim. See [verification](docs/VERIFICATION.md) for the tests
actually run, benchmark measurements and fixture comparison exclusions.

The next instruction family is **documented CB**, followed by ED and indexed
prefix handling, then interrupts/HALT recovery and a tape-driven clock. The
Spectrum board and peripherals are outside this phase.

References: [Zilog UM008011-0816, mirrored manual](https://zany80.github.io/documentation/Z80/UserManual.html),
[pinned JSMoo fixtures](https://github.com/SingleStepTests/z80/tree/ebe1875d48f374bcfd4b505d8eb8ee751568b5f7),
[redcode/Z80](https://github.com/redcode/Z80) (future oracle/exerciser integration).

