# Coverage and continuation plan

Target: **Zilog NMOS Z80**, documented behavior first. This is an incrementally
verified CPU implementation, not a Spectrum emulator or a complete NMOS core.

| Family | Implemented | Verification |
| --- | --- | --- |
| Milestone 1: 21,06,3E,77,24,0F,10,76 | All eight | Three requested programs; edge, page and address-wrap vectors; 32 JSMoo fixtures |
| Unprefixed loads | All register, immediate, indirect, absolute and 16-bit forms | Independent fixtures for every opcode; little-endian/wraparound tests |
| Unprefixed ALU | ADD/ADC/SUB/SBC/AND/XOR/OR/CP, INC/DEC, ADD HL,rr, pair INC/DEC, DAA/CPL/SCF/CCF, accumulator rotates | Independent fixtures for every opcode; explicit carry, half-carry, signed overflow and alias tests |
| Exchanges/stack | EX AF,AF'; EXX; EX DE,HL; EX (SP),HL; PUSH/POP | Main/alternate state, stack wrap, CALL/RET round trip |
| Control flow | JP/JR/DJNZ/CALL/RET/RST and conditional forms | Independent fixtures; all eight conditions taken and untaken; signed displacement tests |
| I/O | IN A,(n), OUT (n),A | Tape-script direction/address/data checking; full 16-bit ports and independent fixtures |
| CPU control | NOP, HALT retirement, DI/EI latch writes, EI-delay boundary state, R increment on M1 | Explicit state/retirement tests; interrupt acceptance not implemented |
| CB | None | Planned next; SLL remains separately undocumented |
| ED | None | Planned: documented instructions, block/repeat operations and mode controls |
| DD/FD, DDCB/FDCB | None | Planned: indexed displacements, prefix interactions, repeated/ignored prefixes |
| Interrupts | None | Planned: tape-scripted NMI/INT, modes 0–2, EI inhibition, RETI/RETN, HALT recovery |
| Board/peripherals | None | Deliberately outside the CPU phase |

`python -m z80tm coverage` lists the exact 252 supported bytes. CB, DD, ED and
FD currently fail clearly at the prefix address; they are not treated as NOPs.
The table already stores IX/IY, I, IFF1/2, IM and alternate registers on tape.
Storage alone does not imply the associated instruction families work.

## Timing contract

Instruction retirement includes the completed HALT instruction, with PC at its
following byte. The harness stops at that boundary. Repeated halted M1/refresh
cycles and asynchronous recovery are not implemented. The guest HALT latch is
separate from a runner stop and from transition-budget exhaustion.

Every implemented instruction writes its base timing on tape. Branch paths
choose their timing through rules. Trace events represent 4T M1, 3T memory,
4T I/O and internal intervals. Tests compare non-internal cycle addresses,
data and start offsets against the simplified-bus JSMoo fixtures. They do not
claim pin-level accuracy or internal-address/refresh contention accuracy.

The host's retired-instruction and accumulated-T-state counters are reporting
only. Before adding scheduled interrupts or WAIT, introduce a multi-byte clock
and pending-event state on the tape and update them through transition rules.

## Undocumented behavior ledger

| Behavior | Current policy / evidence |
| --- | --- |
| F bits 3/5 for implemented arithmetic, INC/DEC and accumulator rotates | Result-derived bits; CP uses operand bits. Explicit edge vectors and sampled full-F JSMoo comparisons pass. |
| SCF/CCF bits 3/5 | Currently taken from A. NMOS Q-dependent behavior is **not implemented**; fixture comparison masks these two bits only for opcodes 37/3F. No full-byte NMOS claim for these instructions. |
| WZ/MEMPTR, Q, P | Not modeled; excluded from fixture comparisons. Needed for later undocumented and interrupt-sensitive behavior. |
| DAA invalid/atypical input combinations | Finite table implements the usual NMOS correction algorithm. Two independent arbitrary-state fixtures are sampled; exhaustive hardware verification is not claimed. |
| Undocumented opcodes, SLL, index-high/index-low instructions, ED aliases | Not implemented or claimed. |
| Prefix effects, R under prefixes, NMOS LD A,I/R interrupt quirk | Not implemented or claimed. |

## Next implementation sequence

1. Add documented CB rotate/shift/BIT/RES/SET instructions using the same shared
   tape addressing and ALU machinery. Test R increments, (HL) writeback,
   preserved carry, flags and cycle placement; keep SLL outside documented coverage.
2. Add ED documented arithmetic, transfers, I/R, I/O and block repeats. Define
   WZ/Q/P policy before asserting undocumented behavior.
3. Add DD/FD/DDCB/FDCB and repeated/ignored prefix handling. Test displacement
   wrap and fetch classification independently of architectural results.
4. Add rule-driven clock and interrupt scripts, NMI, IM 0/1/2, EI inhibition,
   RETI/RETN and HALT recovery. IRQ acknowledgement must run on tape.
5. Expand selected vectors/exercisers and implement precise machine-cycle bus
   detail before integrating Spectrum contention. No such exercisers or Linux
   execution have been run in this workspace yet.
