"""Generate a finite table WITHOUT accepting a binary, PC, or register input.

All Python arithmetic here runs at table-build time. Generated routines start
and finish at START. Memory subroutine continuations are finite symbols on
the tape; no Python call stack participates in guest execution.
"""
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType

from .machine import Builder, Rule, Table
from .layout import (START, RAM, PAGE, SELECTED, TARGET, END, MARK, POS, TOKEN_START,
                     PORT_ACTIVE, PORT_RECORD, PORT_END, PORT_ACTIVE_END)


@dataclass(frozen=True)
class CPUProgram:
    table: Table
    entry: int
    boundary: int
    unsupported: int
    halted: int
    io_error: int
    interrupt_boundary: int
    interrupt_error: int
    cycles: object
    coverage: object
    families: object


class Compiler:
    def __init__(self):
        self.b = Builder(range(TOKEN_START))
        self.boundary = self.b.reserve("instruction boundary")
        self.retirement = self.b.reserve("retire architectural state")
        self.unsupported = self.b.emit("unsupported opcode")
        self.halted = self.b.reserve("halt cycle boundary")
        self.interrupt_boundary = self.b.reserve("interrupt boundary")
        self.interrupt_error = self.b.emit("IM0 response script exhausted")
        self.response = self.b.reserve("IM0 response byte")
        self.io_error = self.b.emit("I/O script mismatch or exhaustion")
        self.port = self.b.reserve("scripted port transaction")
        self.entry = self.b.reserve("instruction entry")
        self.memory = self.b.reserve("memory access")
        self.return_state = self.b.reserve("memory return")
        self.returns = {}
        self.copy_entry = self.b.reserve("shared byte copy")
        self.copy_returns = {}
        self.copy_sources = set()
        self.copy_destinations = set()
        self.next_token = TOKEN_START
        self.cycles = {}
        self.coverage = {}
        self.families = {}

    def token(self):
        token = self.next_token
        self.next_token += 1
        self.b.alphabet.add(token)
        return token

    @cache
    def home(self, next_state):
        s = self.b.reserve("seek START")
        self.b.define(s, {START: Rule(next_state, None, 0)}, Rule(s, None, -1))
        return s

    @cache
    def seek(self, marker, direction, next_state, after=1):
        s = self.b.reserve(f"seek marker {marker}")
        self.b.define(s, {marker: Rule(next_state, None, after)}, Rule(s, None, direction))
        return s

    def at(self, slot, state):
        return self.seek(MARK[slot], 1, state)

    @cache
    def write(self, slot, value, next_state):
        end = self.b.emit(f"write {slot}={value}", default=Rule(self.home(next_state), value, -1))
        return self.at(slot, end)

    @cache
    def copy(self, src, dst, next_state):
        if src == dst:
            return next_state
        token = self.token()
        self.copy_returns[token] = next_state
        self.copy_sources.add(src)
        self.copy_destinations.add(dst)
        return self.write("COPYSRC", POS[src] // 2, self.write("COPYDST", POS[dst] // 2,
                          self.write("COPYCONT", token, self.copy_entry)))

    @cache
    def direct_copy(self, src, dst, next_state):
        if src == dst:
            return next_state
        direction = 1 if POS[dst] > POS[src] else -1
        rules = {}
        for value in range(256):
            end = self.b.emit(f"copy -> {dst}", default=Rule(self.home(next_state), value, -1))
            target = self.seek(MARK[dst], direction, end)
            rules[value] = Rule(target, None, direction)
        return self.at(src, self.b.emit(f"read {src}", rules))

    def build_copy(self):
        end = self.branch("COPYCONT", self.copy_returns)
        destinations = self.branch("COPYDST", {POS[dst] // 2: self.direct_copy("COPYVAL", dst, end)
                                                for dst in sorted(self.copy_destinations)})
        read = self.b.emit("capture copied byte", {
            x: Rule(self.home(self.write("COPYVAL", x, destinations)), None, -1) for x in range(256)
        })
        sources = self.branch("COPYSRC", {POS[src] // 2: self.at(src, read) for src in sorted(self.copy_sources)})
        self.b.define(self.copy_entry, default=Rule(sources, None, 0))

    def copy_pair(self, src_low, src_high, dst_low, dst_high, next_state):
        return self.copy(src_low, dst_low, self.copy(src_high, dst_high, next_state))

    @cache
    def transform_values(self, slot, values, next_state):
        return self.at(slot, self.b.emit(f"transform {slot}", {
            x: Rule(self.home(next_state), values[x], -1) for x in range(256)
        }))

    def transform(self, slot, fn, next_state):
        return self.transform_values(slot, tuple(fn(x) for x in range(256)), next_state)

    def branch(self, slot, targets):
        return self.at(slot, self.b.emit(f"branch {slot}", {
            x: Rule(self.home(target), None, -1) for x, target in targets.items()
        }))

    @cache
    def increment_pair(self, low, high, delta, next_state):
        carry = self.transform(high, lambda x: (x + delta) & 255, next_state)
        return self.at(low, self.b.emit(f"{low}:{high} {delta:+}", {
            x: Rule(self.home(carry if (x + delta) not in range(256) else next_state), (x + delta) & 255, -1)
            for x in range(256)
        }))

    @cache
    def event(self, kind, duration, next_state):
        if kind != "internal":
            low, high = ("R", "I") if kind in ("m1", "halt_m1", "int_ack", "nmi_ack") else ("ADDRL", "ADDRH")
            next_state = self.copy_pair(low, high, "BUSL", "BUSH", next_state)
        tick = self.add_constant(tuple(f"TIME{i}" for i in range(8)), duration, next_state)
        tick = self.add_constant(tuple(f"CLOCK{i}" for i in range(8)), duration, tick)
        state = self.b.emit(f"cycle {kind} {duration}", default=Rule(tick, None, 0))
        self.cycles[state] = (kind, duration)
        return state

    @cache
    def retire(self, timing, internal=0):
        end = self.retirement
        return self.event("internal", internal, end) if internal else end

    @cache
    def add_constant(self, slots, amount, next_state):
        if not slots or not amount:
            return next_state
        low, *rest = slots
        carry = self.add_constant(tuple(rest), 1, next_state)
        return self.at(low, self.b.emit("add tape counter", {
            x: Rule(self.home(carry if x + amount > 255 else next_state), (x + amount) & 255, -1)
            for x in range(256)
        }))

    def clear_time(self, next_state):
        for i in range(8):
            next_state = self.write(f"TIME{i}", 0, next_state)
        return next_state

    @cache
    def memory_call(self, write, next_state):
        return self.write("MODE", int(write), self.call(self.memory, next_state))

    @cache
    def call(self, entry, next_state):
        token = self.token()
        self.returns[token] = next_state
        return self.write("CONT", token, entry)

    @cache
    def memory_cycle(self, write, kind, duration, next_state):
        return self.memory_call(write, self.event(kind, duration, next_state))

    @cache
    def fetch(self, dst, next_state, opcode=False):
        normal = self.normal_fetch(dst, next_state, opcode)
        end = self.copy("DATA", dst, next_state)
        end = self.event("int_ack" if opcode else "int_operand", 6 if opcode else 3, end)
        if opcode:
            end = self.transform("R", lambda r: (r & 128) | ((r + 1) & 127), end)
        end = self.call(self.response, end)
        end = self.copy_pair("PCL", "PCH", "ADDRL", "ADDRH", end)
        return self.branch("IM0", {0: normal, 1: end})

    @cache
    def normal_fetch(self, dst, next_state, opcode=False):
        end = self.copy("DATA", dst, next_state)
        end = self.increment_pair("PCL", "PCH", 1, end)
        end = self.event("m1" if opcode else "read", 4 if opcode else 3, end)
        if opcode:
            end = self.transform("R", lambda r: (r & 128) | ((r + 1) & 127), end)
        end = self.memory_call(False, end)
        return self.copy_pair("PCL", "PCH", "ADDRL", "ADDRH", end)

    def build_memory(self):
        # Read and write share the page selection pass. The offset and byte
        # transfer passes keep only one bounded byte in finite control.
        read_byte = self.b.emit("RAM read", {
            value: Rule(self.home(self.write("DATA", value, self.return_state)), None, -1)
            for value in range(256)
        })
        write_targets = {}
        for value in range(256):
            end = self.b.emit("RAM write", {TARGET: Rule(self.home(self.return_state), value, -1)})
            write_targets[value] = self.seek(TARGET, 1, end, 0)
        get_write_data = self.branch("DATA", write_targets)
        write_byte = self.b.emit("mark byte", {value: Rule(self.home(get_write_data), TARGET, -1) for value in range(256)})
        modes = {}
        for mode, byte_state in enumerate((read_byte, write_byte)):
            offsets = [byte_state]
            for offset in range(1, 256):
                offsets.append(self.b.emit(f"walk offset {offset}", default=Rule(offsets[-1], None, 1)))
            lows = {}
            for low in range(256):
                page = self.b.emit("restore page", {SELECTED: Rule(offsets[low], PAGE, 1)})
                lows[low] = self.seek(SELECTED, 1, page, 0)
            modes[mode] = self.branch("ADDRL", lows)
        after_page = self.branch("MODE", modes)
        pages = [self.b.reserve(f"count pages {n}") for n in range(256)]
        for n, state in enumerate(pages):
            on_page = Rule(pages[n - 1], None, 1) if n else Rule(self.home(after_page), SELECTED, -1)
            self.b.define(state, {PAGE: on_page}, Rule(state, None, 1))
        high = self.branch("ADDRH", {n: self.seek(RAM, 1, pages[n]) for n in range(256)})
        self.b.define(self.memory, default=Rule(high, None, 0))
        dispatch = self.at("CONT", self.b.emit("return dispatch", {
            token: Rule(self.home(target), None, -1) for token, target in self.returns.items()
        }))
        self.b.define(self.return_state, default=Rule(dispatch, None, 0))

    def build_ports(self):
        def cursor(next_state, offset):
            for _ in range(offset - 1):
                next_state = self.b.emit("walk port field", default=Rule(next_state, None, 1))
            state = self.b.reserve("seek active port script record")
            self.b.define(state, {PORT_ACTIVE: Rule(next_state, None, 1),
                                  PORT_ACTIVE_END: Rule(self.io_error, None, 0)}, Rule(state, None, 1))
            return state

        def compare(slot, offset, next_state):
            paths = {}
            for value in range(256):
                check = self.b.emit(f"compare port {slot}", {value: Rule(self.home(next_state), None, -1)},
                                    Rule(self.io_error, None, 0))
                paths[value] = cursor(check, offset)
            return self.branch(slot, paths)

        done = self.copy_pair("ADDRL", "ADDRH", "PORTL", "PORTH", self.copy("DATA", "PORTDATA",
                         self.copy("MODE", "PORTMODE", self.write("IO_EVENT", 1, self.return_state))))
        done = self.home(done)
        advance = self.b.reserve("advance port cursor")
        self.b.define(advance, {PORT_RECORD: Rule(done, PORT_ACTIVE, -1),
                                PORT_END: Rule(done, PORT_ACTIVE_END, -1)}, Rule(advance, None, 1))
        consume = self.b.emit("consume port record", {PORT_ACTIVE: Rule(advance, PORT_RECORD, 1)})
        consume = self.seek(PORT_ACTIVE, 1, consume, 0)
        read = self.b.emit("read port data", {
            value: Rule(self.home(self.write("DATA", value, consume)), None, -1) for value in range(256)
        })
        access = self.branch("MODE", {0: cursor(read, 4), 1: compare("DATA", 4, consume)})
        end = compare("MODE", 1, compare("ADDRL", 2, compare("ADDRH", 3, access)))
        self.b.define(self.port, default=Rule(end, None, 0))

    @cache
    def inc_dec(self, reg, delta, next_state):
        targets = {}
        for old in range(256):
            value = (old + delta) & 255
            flags = (value & 0xA8) | (0x40 if value == 0 else 0)
            if delta == 1:
                flags |= (0x10 if (old & 15) == 15 else 0) | (4 if old == 127 else 0)
            else:
                flags |= 2 | (0x10 if (old & 15) == 0 else 0) | (4 if old == 128 else 0)
            end = self.transform("F", lambda f, flags=flags: (f & 1) | flags, next_state)
            targets[old] = self.write(reg, value, end)
        return self.branch(reg, targets)

    @cache
    def rotate_a(self, kind, next_state):
        targets = {}
        for old in range(256):
            if kind == "RRCA":
                value, carry = (old >> 1) | ((old & 1) << 7), old & 1
            else:
                value, carry = ((old << 1) | (old >> 7)) & 255, old >> 7
            end = self.transform("F", lambda f, v=value, c=carry: (f & 0xC4) | (v & 0x28) | c, next_state)
            targets[old] = self.write("A", value, end)
        return self.branch("A", targets)

    @cache
    def relative_jump(self, next_state):
        next_state = self.copy_pair("PCL", "PCH", "WZL", "WZH", next_state)
        up = self.transform("PCH", lambda h: (h + 1) & 255, next_state)
        down = self.transform("PCH", lambda h: (h - 1) & 255, next_state)
        targets = {}
        for offset in range(256):
            signed = offset if offset < 128 else offset - 256
            rules = {}
            for low in range(256):
                result = low + signed
                target = down if result < 0 else up if result > 255 else next_state
                rules[low] = Rule(self.home(target), result & 255, -1)
            targets[offset] = self.at("PCL", self.b.emit("add signed displacement", rules))
        return self.branch("DATA", targets)

    def add_opcode(self, opcodes, opcode, name, entry):
        if opcode in opcodes:
            raise ValueError(f"Duplicate opcode {opcode:02X}")
        opcodes[opcode] = entry
        self.coverage[opcode] = name

    def compile(self):
        opcodes = {}
        add = lambda op, name, state: self.add_opcode(opcodes, op, name, state)
        add(0x21, "LD HL,nn", self.fetch("L", self.fetch("H", self.retire(10))))
        add(0x06, "LD B,n", self.fetch("B", self.retire(7)))
        add(0x3E, "LD A,n", self.fetch("A", self.retire(7)))
        store = self.copy("A", "DATA", self.memory_cycle(True, "write", 3, self.retire(7)))
        add(0x77, "LD (HL),A", self.copy_pair("L", "H", "ADDRL", "ADDRH", store))
        add(0x24, "INC H", self.inc_dec("H", 1, self.retire(4)))
        add(0x0F, "RRCA", self.rotate_a("RRCA", self.retire(4)))
        taken = self.relative_jump(self.retire(13, 5))
        choose = self.branch("B", {b: taken if b else self.retire(8) for b in range(256)})
        djnz = self.transform("B", lambda b: (b - 1) & 255, self.fetch("DATA", choose))
        add(0x10, "DJNZ e", self.event("internal", 1, djnz))
        add(0x76, "HALT", self.write("HALT", 1, self.retire(4)))

        from .unprefixed import Unprefixed
        unprefixed = Unprefixed(self, opcodes)
        unprefixed.build()

        finish = self.branch("MODF", {0: self.write("Q", 0, self.boundary),
                                      1: self.copy("F", "Q", self.boundary)})
        self.b.define(self.retirement, default=Rule(self.copy("TIME0", "TSTATES", finish), None, 0))

        from .prefixes import Prefixes
        prefixes = Prefixes(self, unprefixed, opcodes)
        decode = prefixes.build()

        fetched = self.copy("OP", "FIRSTOP", decode)
        start = self.fetch("OP", fetched, opcode=True)
        start = self.write("EI_DELAY", 0, start)
        for slot in ("P", "NMI_DELAY"):
            start = self.write(slot, 0, start)
        from .interrupts import Interrupts
        interrupts = Interrupts(self, unprefixed, decode)
        start = interrupts.build(start)
        for slot in ("IO_EVENT", "RETI", "INDEX", "FAMILY", "ACTION", "IM0"):
            start = self.write(slot, 0, start)
        start = self.copy("Q", "OLDQ", self.clear_time(start))
        start = self.copy_pair("PCL", "PCH", "INSTL", "INSTH", start)
        self.b.define(self.entry, default=Rule(start, None, 0))
        self.b.define(self.boundary, default=Rule(self.entry, None, 0))
        self.b.define(self.halted, default=Rule(self.entry, None, 0))
        self.b.define(self.interrupt_boundary, default=Rule(self.entry, None, 0))
        self.build_ports()
        self.build_memory()
        self.build_copy()
        return CPUProgram(self.b.freeze(), self.entry, self.boundary, self.unsupported, self.halted,
                          self.io_error, self.interrupt_boundary, self.interrupt_error,
                          MappingProxyType(self.cycles), MappingProxyType(self.coverage), MappingProxyType(self.families))


@cache
def build_cpu() -> CPUProgram:
    """One fixed table shared by every input binary and initial CPU state."""
    return Compiler().compile()

