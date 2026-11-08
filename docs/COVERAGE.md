# Coverage and continuation plan

Target: **Zilog NMOS Z80**. This is a CPU implemented by generated single-tape
Turing-machine rules, with selected independent verification. The optional
[Spectrum 48K board](SPECTRUM.md) uses the same instruction generator. Neither
the CPU nor the board claims exhaustive hardware conformance.

| Family | Implemented | Verification |
| --- | --- | --- |
| Unprefixed | All 252 non-prefix bytes: loads, ALU, exchanges, stack, control flow, I/O and CPU control | 520 independent vectors; explicit arithmetic, branch, stack, wraparound and self-modification tests |
| CB | All 256 bytes: rotates/shifts, BIT, RES, SET, including SLL | One independent vector per byte, including memory forms and full flags |
| ED | Documented arithmetic, transfers, I/R, I/O, block/repeat operations, mode controls and returns; aliases; undefined bytes act as two-byte NOPs | 80 independent opcode vectors; undefined-ED CLI regression; interrupt/RETI tests |
| DD/FD | IX/IY substitutions, index high/low bytes, signed displacements, ignored/repeated prefixes | 252 independent vectors per prefix; prefixed EI-delay and IM0 prefix-stream tests |
| DDCB/FDCB | All 256 final bytes per family, including register copies and BIT aliases | 512 independent vectors |
| Interrupts | Tape scripts, NMI, level-sensitive INT, IM0 response streams, IM1/IM2, EI inhibition, RETI/RETN, HALT recovery | Explicit scenarios for priority, stack/vector wrap, deferred acceptance, refresh, response exhaustion and budget resumption |
| Clock | 64-bit tape clock and per-action elapsed time, advanced by generated rules | Per-fixture timing checks; carry across 32 bits; absolute script deadlines; interrupted-run equivalence |
| Board/peripherals | CPU scripted ports; optional Spectrum 48K ROM protection, ULA ports, keyboard, frame IRQ, display and cassette input | Dedicated hardware, pulse-edge, renderer and exact-runner equivalence tests; no contention, floating bus or audio output |

`python -m z80tm coverage` lists unprefixed entries and family counts.
`--family CB` (or ED/DD/FD/DDCB/FDCB) lists a single family; `--json` emits
machine-readable maps. DD/FD maps contain 252 terminal entries; the four
prefix bytes additionally dispatch through the shared prefix decoder. ED has
256 decoder entries, including undefined NOPs. Entry counts describe decoding,
not the strength of verification or the number of distinct instructions.

## Timing and interrupt contract

HALT retires with PC at the following byte. The harness stops there by default.
`CPU.run(stop_on_halt=False, max_actions=...)`, or CLI `--continue-halt --actions`,
continues with 4T halted M1 cycles and accepts eligible interrupts. HALT cycles
increment R and the tape clock without retiring another instruction. IM0
responses do count as instructions; NMI and IM1/IM2 entries do not. A repeated
block instruction retires one iteration per instruction step.

The tape holds both CLOCK and per-action TIME as unsigned 64-bit counters.
Generated cycle rules advance them; host counters only report completed work.
A transition budget can expire inside any action and execution can resume from
that exact tape, head and state, including partially completed clock updates.
Counter overflow is modulo 2^64; script timestamps do not encode a rollover era.

Interrupt records are ordered `[absolute_time, kind, vector]`, where kind is
`int`, `clear`, or `nmi`. Rules consume all due records at the next action
boundary. INT stays asserted until a clear record. NMI uses a pending latch,
so multiple edges before service can coalesce. The IM0 vector supplies its
first opcode; subsequent bytes come from the separate tape response stream.
RETI produces an observable completion time, without a peripheral daisy chain.

Bus traces include M1, memory/I/O transactions, internal intervals, interrupt
acknowledgements and HALT cycles. Their start times come from the tape clock.
Refresh addresses are reported for M1/acknowledgement events. Independent
fixtures compare non-internal transaction addresses, data and start offsets,
and total timing. Internal address accuracy, refresh pin phases, precise
interrupt sampling edges, WAIT/BUSRQ and contention are not verified here.
The interrupt scenarios are explicit regression expectations, not an independent
interrupt oracle.

## Undocumented behavior ledger

| Behavior | Current policy / evidence |
| --- | --- |
| F bits 3/5 | Full-byte comparisons for all selected vectors, including CP operand bits and BIT memory behavior |
| SCF/CCF Q dependence | Q is initialized from the fixture and updated by rules; DD/FD clear prior flag history; full F is compared without a mask |
| WZ/MEMPTR, Q, P | Tape-resident state; all three are initialized and compared in the independent fixture suite |
| DAA atypical inputs | Finite correction table; selected arbitrary-state fixtures, without exhaustive hardware verification |
| SLL, index high/low, indexed CB copies, ED aliases | Implemented; selected independent vectors |
| Repeated block flags | Implemented and compared in selected ED fixtures; broader multi-iteration coverage remains useful |
| LD A,I/R P/V interrupt behavior | Implemented; a dedicated LD A,I interrupt scenario and selected transfer fixtures |

The ignored-DD/FD SCF/CCF rule follows the hardware findings recorded in
[David Banks's undocumented-flags notes](https://github.com/hoglet67/Z80Decoder/wiki/Undocumented-Flags#scfccf).
The pinned `FD 37 0000` vector exposed the missing reset. Dedicated regressions
also cover both carry instructions, DD/FD mixtures, refresh wrap and a preceding
ALU instruction.

## Next implementation sequence

1. Expand vector sampling and add multi-instruction exercisers, including
   repeated/ignored-prefix combinations and block-operation termination paths.
2. Validate interrupt timing and undocumented edge behavior against an
   independent oracle; retain explicit differences and exclusions.
3. Refine internal bus addresses and refresh/WAIT/BUSRQ pin behavior before
   adding Spectrum contention and more detailed board timing.
4. Run the same strict runner on Linux and record reproducible measurements.

No redcode core, zexdoc/zexall, hardware oracle or Linux run is recorded yet.
