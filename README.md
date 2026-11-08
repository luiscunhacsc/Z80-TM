# Z80 on a single-tape Turing machine

Python 3.11+, standard library only. The generated rule table implements all
**252 non-prefix opcodes**, CB/ED/DD/FD and indexed CB decoding, scripted 16-bit
port I/O, interrupts in modes 0–2, and HALT recovery. A 64-bit clock lives on
the tape and advances through transition rules. This is an incrementally
verified NMOS Z80 CPU, with selected undocumented behavior. A Spectrum 48K
board now adds the ROM, keyboard, display and cassette input, still using only
literal single-tape transitions. See [Spectrum usage](docs/SPECTRUM.md) and
[coverage and limitations](docs/COVERAGE.md).

## Spectrum 48K

With the supplied `z80tm/48.rom`, double-click `Start-Spectrum.cmd` on this
Windows workspace, or run `python -m z80tm spectrum` with a Tk-enabled Python.
The first run builds the board's finite transition table. Cold boot and input
are **much slower than real time**. The compiled option executes each TM rule
individually; there is no conventional Z80 core, ROM bypass or instant loading.

The window provides a Spectrum keyboard, a BASIC source queue, TAP/TZX mounting,
tape transport and exact-state save/resume. Wait for the ROM editor before
typing. To load a cassette, mount it, queue `LOAD ""`, then press Play.
Closing the window saves `.tools/spectrum/session.json`. The Windows launcher
automatically resumes that file, or the verified boot checkpoint when available.
To resume explicitly:

```console
python -m z80tm spectrum --resume .tools/spectrum/session.json
```

See [the detailed guide](docs/SPECTRUM.md) for keyboard behavior, dependencies,
supported tape blocks, verification and hardware limitations.

## Run

From this directory with Python 3.11 or later:

```console
python -m z80tm demo
python -m z80tm demo --trace --cycles
python -m z80tm run program.bin --load 0x20ff --pc 0x20ff --budget 20000000
python -m z80tm run program.bin --register A=0xa5 --register HL=0x1234 --instructions 1
python -m z80tm coverage
python -m z80tm coverage --family CB --json
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
instructions. Exit codes are 0 for HALT/instruction/action limit, 2 for an
unsupported opcode or CLI input error, 3 for a transition budget, and 4 for a
machine, port or interrupt-response script error. Budget exhaustion never means
successful HALT.

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

Interrupt scripts use absolute tape-clock T-states, in ascending order:

```json
[[12, "int", 255], [30, "clear", 0], [40, "nmi", 0]]
```

`int` asserts the maskable interrupt line with the supplied vector, `clear`
deasserts it, and `nmi` latches a pending NMI. Due records are consumed by rules
at instruction, interrupt-entry and HALT-cycle boundaries. INT remains asserted
until cleared. Enable reception with IFF1 and the desired IM, or execute EI.

```console
python -m z80tm run program.bin --interrupts interrupts.json --register IFF1=1 --register IM=1 --continue-halt --actions 100 --budget 20000000
```

The default stops after retiring HALT. `--continue-halt` clocks repeated HALT
cycles and allows interrupt recovery. `--actions` counts instructions, interrupt
entries and HALT cycles; `--instructions` counts retired instructions, including
IM0-supplied instructions. `--clock` sets the initial unsigned 64-bit tape clock.
`--interrupt-data response.bin` supplies subsequent IM0 opcode/operand bytes;
the initial byte comes from the vector. JSON reports include interrupt/HALT
counts, the tape clock, and RETI notification times.

## Tape and rules

One sparse, two-way-infinite tape holds integers from a finite alphabet. Blank
is 0. Head movement is always -1, 0 or +1. The initial populated region is:

```text
START | marker,byte for each register/scratch slot | RAM
      | PAGE,256 RAM bytes | PAGE,256 RAM bytes | ... (256 pages) | END
      | active port record | later port records | port-script end
      | interrupt records | interrupt-script end | IM0 response bytes | response end
```

Registers include the main and alternate sets, PC, SP, IX/IY, I/R, IFF1/2,
IM, EI delay and HALT, plus WZ/Q/P and interrupt latches.
Scratch includes instruction address/opcode, address bytes, data, ALU results,
64-bit action timing, clock and script deadlines, and finite continuation
tokens. All state needed at instruction boundaries is on tape. See `layout.py`
for exact offsets.

The compiler and instruction-family modules generate the fixed table without
accepting a binary, load address or initial registers. Python arithmetic computes finite
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

Sparse rows use a default rule plus explicit exceptions; an identity write is
stored as `None`.
`Table.expanded_rule(state, symbol)` expands this notation into an ordinary
`(next_state, new_symbol, move)` triple. No rule is created during execution.
The CLI reports the current state/alphabet sizes. Table generation takes time
and consumes substantial host memory; the table is cached for reuse within a
process.

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
There are no guest-address shortcuts, scan batching or native Z80 cores.
The optional `tm_runner.cs` implements the same generic one-cell loop in C#;
its exact tape, head, state and counters are compared with the Python runner.

The harness initializes/inspects the tape, detects generated boundary/error
states, applies budgets, and collects traces. It never writes guest state
during execution. The generic runner can execute the demo without the CPU
harness; a regression test does exactly that. `CPU.halted` reads the guest
latch; `CPU.stopped_on_halt` also requires the appropriate boundary. A budget
that expires while HALT is retiring can resume and finish retirement.

Rules advance the tape clock and per-action T-states at every generated cycle.
The host totals retired actions as an observation. Generated event states
describe M1, memory read/write, port read/write, interrupt acknowledgement,
HALT and internal intervals. Refresh addresses accompany M1/acknowledgement
events. Independent fixtures check transaction order, addresses, data and start
times. These are **base bus-cycle traces**, not a pin-level Spectrum contention
model: refresh pin phases, precise internal bus addresses and WAIT/BUSRQ remain
outside the verification contract. A budget can stop partway through an action;
the tape clock may then exceed the host's total of retired T-states.

## Verification and performance

The offline suite includes the three milestone programs, explicit edge and
wraparound vectors, budget resumption, port and interrupt scripts, and **1,872
unmodified JSMoo vectors: 520 unprefixed and 1,352 prefixed**. Fixtures are pinned
to SingleStepTests/z80 commit `ebe1875d48f374bcfd4b505d8eb8ee751568b5f7` with blob
IDs and the upstream MIT license. This is selected coverage, not an exhaustive
Z80 conformance claim. See [verification](docs/VERIFICATION.md) for the tests
actually run, benchmark measurements and fixture comparison exclusions.

Further work is to broaden independent vectors and exercisers, verify interrupt
edge timing against an independent oracle, and refine pin-level bus detail.
The Spectrum board has its own hardware rules and acceptance checks described
in [the Spectrum guide](docs/SPECTRUM.md).

References: [Zilog UM008011-0816, mirrored manual](https://zany80.github.io/documentation/Z80/UserManual.html),
[pinned JSMoo fixtures](https://github.com/SingleStepTests/z80/tree/ebe1875d48f374bcfd4b505d8eb8ee751568b5f7),
[redcode/Z80](https://github.com/redcode/Z80) (future oracle/exerciser integration).

