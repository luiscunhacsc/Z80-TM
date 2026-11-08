"""Host initialization, read-only observation, and stop conditions."""
from dataclasses import dataclass
from time import perf_counter

from .compiler import CPUProgram, build_cpu
from .layout import initial_tape, snapshot, byte, word, wide, memory_position
from .machine import Machine, TransitionBudget

DEMO = bytes.fromhex("21 00 40 06 08 3E 80 77 24 0F 10 FB 76")


class UnsupportedOpcode(RuntimeError):
    pass


class CPUHalted(RuntimeError):
    pass


class PortScriptError(RuntimeError):
    pass


class InterruptScriptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Cycle:
    kind: str
    address: int | None
    tstates: int
    start: int
    data: int | None = None
    refresh_address: int | None = None


@dataclass(frozen=True)
class PortEvent:
    direction: str
    address: int
    data: int
    instruction: int


@dataclass(frozen=True)
class Instruction:
    address: int
    opcode: int
    tstates: int
    registers: dict
    transitions: int
    cycles: tuple[Cycle, ...]
    kind: str = "instruction"
    family: int = 0
    final_opcode: int = 0
    reti: bool = False


class CPU:
    def __init__(self, binary=b"", load=0, pc=None, registers=None, ram=None, program: CPUProgram | None = None, ports=(),
                 interrupts=(), interrupt_data=b"", clock=0):
        self.program = program or build_cpu()
        registers = dict(registers or {})
        if pc is not None:
            registers["PC"] = pc
        elif not any(k.upper() == "PC" for k in registers):
            registers["PC"] = load
        self.machine = Machine(self.program.table, initial_tape(binary, load, registers, ram, ports, interrupts, interrupt_data, clock), self.program.entry)
        self.instructions = 0
        self.tstates = 0
        self.elapsed = 0.0
        self._pending_cycles = []
        self.port_events = []
        self.interrupts = 0
        self.halt_cycles = 0
        self.reti_events = []

    @property
    def registers(self):
        return snapshot(self.machine.tape)

    @property
    def halted(self):
        return bool(byte(self.machine.tape, "HALT"))

    @property
    def stopped_on_halt(self):
        # A budget may expire after the latch is written but before retirement.
        # Finish that instruction before applying the harness HALT stop policy.
        return self.halted and self.machine.state in (self.program.boundary, self.program.entry, self.program.halted)

    def read_memory(self, address):
        return self.machine.tape.get(memory_position(address), 0)

    def step_instruction(self, budget=20_000_000, trace_cycles=False, allow_halt=False):
        if self.stopped_on_halt and not allow_halt:
            raise CPUHalted("Guest HALT latch is set; use allow_halt=True to clock HALT and accept interrupts")
        p, m = self.program, self.machine
        if m.state == p.unsupported:
            self._unsupported()
        if m.state == p.io_error:
            raise PortScriptError("I/O script mismatch or exhaustion")
        if m.state == p.interrupt_error:
            raise InterruptScriptError("IM0 response script exhausted")
        before = m.transitions
        started = perf_counter()

        def observe(machine):
            event = p.cycles.get(machine.state)
            if event:
                kind, duration = event
                address = word(machine.tape, "BUSL", "BUSH") if kind == "internal" else word(machine.tape, "ADDRL", "ADDRH")
                at = wide(machine.tape, "CLOCK")
                data = None if kind in ("internal", "nmi_ack") else byte(machine.tape, "DATA")
                refresh = word(machine.tape, "R", "I") if kind in ("m1", "halt_m1", "int_ack", "nmi_ack") else None
                self._pending_cycles.append(Cycle(kind, address, duration, at, data, refresh))

        try:
            m.run_until(frozenset((p.boundary, p.unsupported, p.halted, p.io_error, p.interrupt_boundary, p.interrupt_error)), budget,
                        observe if trace_cycles else None)
        finally:
            self.elapsed += perf_counter() - started
        if m.state == p.unsupported:
            self._unsupported()
        if m.state == p.interrupt_error:
            raise InterruptScriptError("IM0 response script exhausted")
        if m.state == p.io_error:
            raise PortScriptError(f"I/O script mismatch or exhaustion at port ${word(m.tape, 'ADDRL', 'ADDRH'):04X}")
        timing = wide(m.tape, "TIME")
        action = byte(m.tape, "ACTION")
        kind = ("instruction", "nmi", "int", "im0", "halt_cycle")[action]
        result = Instruction(word(m.tape, "INSTL", "INSTH"), byte(m.tape, "FIRSTOP"), timing,
                             self.registers, m.transitions - before, tuple(self._pending_cycles), kind,
                             byte(m.tape, "FAMILY"), byte(m.tape, "OP"), bool(byte(m.tape, "RETI")))
        self._pending_cycles.clear()
        self.instructions += int(action in (0, 3))
        self.interrupts += int(action in (1, 2, 3))
        self.halt_cycles += int(action == 4)
        if result.reti:
            self.reti_events.append(wide(m.tape, "CLOCK"))
        self.tstates += timing
        if byte(m.tape, "IO_EVENT"):
            self.port_events.append(PortEvent("w" if byte(m.tape, "PORTMODE") else "r",
                                              word(m.tape, "PORTL", "PORTH"), byte(m.tape, "PORTDATA"), self.instructions))
        return result

    def _unsupported(self):
        tape = self.machine.tape
        raise UnsupportedOpcode(f"Unsupported opcode ${byte(tape, 'OP'):02X} at ${word(tape, 'INSTL', 'INSTH'):04X}")

    def run(self, budget=20_000_000, max_instructions=None, trace=None, trace_cycles=False,
            stop_on_halt=True, max_actions=None):
        if (budget < 0 or (max_instructions is not None and max_instructions < 0)
                or (max_actions is not None and max_actions < 0)):
            raise ValueError("Budgets must be nonnegative")
        end = self.machine.transitions + budget
        count = 0
        start_instructions = self.instructions
        while ((not stop_on_halt or not self.stopped_on_halt)
               and (max_instructions is None or self.instructions - start_instructions < max_instructions)
               and (max_actions is None or count < max_actions)):
            instruction = self.step_instruction(end - self.machine.transitions, trace_cycles, allow_halt=not stop_on_halt)
            count += 1
            if trace is not None:
                trace(instruction)
        return self
