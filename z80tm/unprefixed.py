"""Generation-only routines for the documented unprefixed CPU families.

I/O consumes a tape-encoded port script through another transition routine.
No function in this module is used by the runtime machine.
"""
from functools import cache

from .machine import Rule

REGS = ("B", "C", "D", "E", "H", "L", None, "A")
PAIRS = (("C", "B"), ("E", "D"), ("L", "H"), ("SPL", "SPH"))
CONDITIONS = ((0x40, False), (0x40, True), (1, False), (1, True),
              (4, False), (4, True), (0x80, False), (0x80, True))


def szp(value):
    return (value & 0xA8) | (0x40 if value == 0 else 0) | (4 if value.bit_count() % 2 == 0 else 0)


def alu_result(kind, x, y, carry=0):
    """Compile-time truth-table generator, not a host execution path."""
    if kind in ("ADD", "ADC"):
        total = x + y + carry
        value = total & 255
        flags = ((value & 0xA8) | (0x40 if value == 0 else 0) | ((x ^ y ^ value) & 16)
                 | (4 if (~(x ^ y) & (x ^ value) & 128) else 0) | int(total > 255))
    elif kind in ("SUB", "SBC", "CP"):
        total = x - y - carry
        value = total & 255
        flags = ((value & 0xA8) | (0x40 if value == 0 else 0) | ((x ^ y ^ value) & 16)
                 | (4 if ((x ^ y) & (x ^ value) & 128) else 0) | 2 | int(total < 0))
        if kind == "CP":
            flags = (flags & ~0x28) | (y & 0x28)
    else:
        value = x & y if kind == "AND" else x ^ y if kind == "XOR" else x | y
        flags = szp(value) | (16 if kind == "AND" else 0)
    return value, flags


class Unprefixed:
    def __init__(self, compiler, opcodes):
        self.c = compiler
        self.opcodes = opcodes

    def add(self, opcode, name, entry):
        if opcode not in self.opcodes:
            self.c.add_opcode(self.opcodes, opcode, name, entry)

    def condition(self, index, yes, no):
        mask, set_bit = CONDITIONS[index]
        return self.c.branch("F", {f: yes if bool(f & mask) == set_bit else no for f in range(256)})

    def indirect(self, low, high, write, next_state):
        c = self.c
        return c.copy_pair(low, high, "ADDRL", "ADDRH", c.memory_cycle(write, "write" if write else "read", 3, next_state))

    def absolute(self, next_state):
        c = self.c
        # Fetch temporarily uses ADDR; preserve the low operand elsewhere.
        end = c.copy_pair("TMP", "DATA", "ADDRL", "ADDRH", next_state)
        return c.fetch("TMP", c.fetch("DATA", end))

    def push(self, low, high, next_state):
        c = self.c
        end = self.indirect("SPL", "SPH", True, next_state)
        end = c.copy(low, "DATA", end)
        end = c.increment_pair("SPL", "SPH", -1, end)
        end = self.indirect("SPL", "SPH", True, end)
        end = c.copy(high, "DATA", end)
        return c.increment_pair("SPL", "SPH", -1, end)

    def pop(self, low, high, next_state):
        c = self.c
        if low == "PCL":
            next_state = c.copy_pair("PCL", "PCH", "WZL", "WZH", next_state)
        end = c.copy("DATA", high, c.increment_pair("SPL", "SPH", 1, next_state))
        end = self.indirect("SPL", "SPH", False, end)
        end = c.copy("DATA", low, c.increment_pair("SPL", "SPH", 1, end))
        return self.indirect("SPL", "SPH", False, end)

    def exchange(self, a, b, next_state):
        c = self.c
        return c.copy(a, "TMP", c.copy(b, a, c.copy("TMP", b, next_state)))

    @cache
    def alu_core(self, kind):
        c = self.c
        carry_paths = {}
        for carry in ((0, 1) if kind in ("ADC", "SBC") else (0,)):
            x_paths = {}
            for x in range(256):
                rules = {}
                for y in range(256):
                    value, flags = alu_result(kind, x, y, carry)
                    end = c.write("RESULT", value, c.write("FLAGS", flags, c.return_state))
                    rules[y] = Rule(c.home(end), None, -1)
                x_paths[x] = c.at("TMP2", c.b.emit(f"{kind} byte truth table", rules))
            carry_paths[carry] = c.branch("TMP", x_paths)
        if len(carry_paths) == 1:
            return carry_paths[0]
        return c.branch("FLAGS", {f: carry_paths[f & 1] for f in range(256)})

    @cache
    def alu(self, kind, next_state):
        c = self.c
        end = c.copy("FLAGS", "F", next_state)
        if kind != "CP":
            end = c.copy("RESULT", "A", end)
        end = c.call(self.alu_core(kind), end)
        if kind in ("ADC", "SBC"):
            end = c.copy("F", "FLAGS", end)
        return c.copy("A", "TMP", c.copy("DATA", "TMP2", end))

    def add_hl(self, low, high):
        c = self.c
        finish = c.event("internal", 4, c.event("internal", 3, c.retire(11)))
        flags = c.branch("FLAGS", {f: c.transform("F", lambda old, f=f: (old & 0xC4) | (f & 0x39), finish)
                                    for f in range(256)})
        end = c.copy("RESULT", "H", flags)
        end = c.call(self.alu_core("ADC"), end)
        end = c.copy("H", "TMP", c.copy(high, "TMP2", end))
        end = c.copy("RESULT", "L", end)
        end = c.call(self.alu_core("ADD"), end)
        end = c.copy("L", "TMP", c.copy(low, "TMP2", end))
        return c.copy_pair("L", "H", "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, end))

    def memptr(self, low, high, next_state, store_a=False):
        c = self.c
        if store_a:
            end = c.transform("WZL", lambda v: (v + 1) & 255, next_state)
            return c.copy_pair(low, "A", "WZL", "WZH", end)
        return c.copy_pair(low, high, "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, next_state))

    def through_carry(self, left):
        c = self.c
        paths = {}
        for a in range(256):
            rules = {}
            for f in range(256):
                value = ((a << 1) | (f & 1)) & 255 if left else (a >> 1) | ((f & 1) << 7)
                flags = (f & 0xC4) | (value & 0x28) | (a >> 7 if left else a & 1)
                end = c.write("A", value, c.retire(4))
                rules[f] = Rule(c.home(end), flags, -1)
            paths[a] = c.at("F", c.b.emit("rotate through carry", rules))
        return c.branch("A", paths)

    def daa(self):
        c = self.c
        paths = {}
        for a in range(256):
            rules = {}
            for f in range(256):
                correction = (6 if (f & 16) or (a & 15) > 9 else 0) | (0x60 if (f & 1) or a > 0x99 else 0)
                value = (a - correction if f & 2 else a + correction) & 255
                flags = szp(value) | (f & 2) | ((a ^ value) & 16) | int(bool(f & 1) or a > 0x99)
                end = c.write("A", value, c.retire(4))
                rules[f] = Rule(c.home(end), flags, -1)
            paths[a] = c.at("F", c.b.emit("decimal adjust", rules))
        return c.branch("A", paths)

    def build(self):
        c, add = self.c, self.add
        add(0x00, "NOP", c.retire(4))
        # 8-bit loads, including every register/memory combination.
        for dst, dst_name in enumerate(REGS):
            for src, src_name in enumerate(REGS):
                opcode = 0x40 + dst * 8 + src
                if opcode == 0x76 or opcode in self.opcodes:
                    continue
                end = c.retire(7 if None in (dst_name, src_name) else 4)
                if dst_name is None:
                    end = c.copy(src_name, "DATA", self.indirect("L", "H", True, end))
                elif src_name is None:
                    end = self.indirect("L", "H", False, c.copy("DATA", dst_name, end))
                else:
                    end = c.copy(src_name, dst_name, end)
                add(opcode, f"LD {dst_name or '(HL)'},{src_name or '(HL)'}", end)
            opcode = 0x06 + dst * 8
            if opcode not in self.opcodes:
                end = c.retire(10 if dst_name is None else 7)
                if dst_name is None:
                    end = c.fetch("DATA", self.indirect("L", "H", True, end))
                else:
                    end = c.fetch(dst_name, end)
                add(opcode, f"LD {dst_name or '(HL)'},n", end)
            for delta, base, name in ((1, 0x04, "INC"), (-1, 0x05, "DEC")):
                opcode = base + dst * 8
                if opcode in self.opcodes:
                    continue
                if dst_name is None:
                    end = c.memory_cycle(True, "write", 3, c.retire(11))
                    end = c.inc_dec("DATA", delta, c.event("internal", 1, end))
                    end = self.indirect("L", "H", False, end)
                else:
                    end = c.inc_dec(dst_name, delta, c.retire(4))
                add(opcode, f"{name} {dst_name or '(HL)'}", end)
        for index, (low, high) in enumerate(PAIRS):
            if 1 + 16 * index not in self.opcodes:
                add(1 + 16 * index, f"LD {('BC', 'DE', 'HL', 'SP')[index]},nn", c.fetch(low, c.fetch(high, c.retire(10))))
            for delta, base, name in ((1, 3, "INC"), (-1, 11, "DEC")):
                add(base + 16 * index, f"{name} {('BC', 'DE', 'HL', 'SP')[index]}",
                    c.increment_pair(low, high, delta, c.retire(6, 2)))
            add(9 + 16 * index, f"ADD HL,{('BC', 'DE', 'HL', 'SP')[index]}", self.add_hl(low, high))
        for index, (low, high) in enumerate(PAIRS[:2]):
            pair_name = ("BC", "DE")[index]
            end = self.memptr(low, high, c.retire(7), True)
            add(2 + 16 * index, f"LD ({pair_name}),A", c.copy("A", "DATA", self.indirect(low, high, True, end)))
            end = self.memptr(low, high, c.copy("DATA", "A", c.retire(7)))
            add(10 + 16 * index, f"LD A,({pair_name})", self.indirect(low, high, False, end))
        end = self.memptr("ADDRL", "ADDRH", c.retire(13), True)
        add(0x32, "LD (nn),A", self.absolute(c.copy("A", "DATA", c.memory_cycle(True, "write", 3, end))))
        end = self.memptr("ADDRL", "ADDRH", c.copy("DATA", "A", c.retire(13)))
        add(0x3A, "LD A,(nn)", self.absolute(c.memory_cycle(False, "read", 3, end)))
        end = c.copy("H", "DATA", c.memory_cycle(True, "write", 3, c.retire(16)))
        end = c.increment_pair("ADDRL", "ADDRH", 1, c.copy_pair("ADDRL", "ADDRH", "WZL", "WZH", end))
        end = c.copy("L", "DATA", c.memory_cycle(True, "write", 3, end))
        add(0x22, "LD (nn),HL", self.absolute(end))
        end = c.memory_cycle(False, "read", 3, c.copy("DATA", "H", c.retire(16)))
        end = c.copy("DATA", "L", c.increment_pair("ADDRL", "ADDRH", 1, c.copy_pair("ADDRL", "ADDRH", "WZL", "WZH", end)))
        add(0x2A, "LD HL,(nn)", self.absolute(c.memory_cycle(False, "read", 3, end)))
        add(0xF9, "LD SP,HL", c.copy_pair("L", "H", "SPL", "SPH", c.retire(6, 2)))

        for index, kind in enumerate(("ADD", "ADC", "SUB", "SBC", "AND", "XOR", "OR", "CP")):
            for src, src_name in enumerate(REGS):
                end = self.alu(kind, c.retire(7 if src_name is None else 4))
                end = self.indirect("L", "H", False, end) if src_name is None else c.copy(src_name, "DATA", end)
                add(0x80 + index * 8 + src, f"{kind} A,{src_name or '(HL)'}", end)
            add(0xC6 + index * 8, f"{kind} A,n", c.fetch("DATA", self.alu(kind, c.retire(7))))
        add(0x07, "RLCA", c.rotate_a("RLCA", c.retire(4)))
        add(0x17, "RLA", self.through_carry(True))
        add(0x1F, "RRA", self.through_carry(False))
        add(0x27, "DAA", self.daa())
        cpl = c.branch("A", {a: c.transform("F", lambda f, a=a: (f & 0xC5) | (a & 0x28) | 0x12, c.retire(4)) for a in range(256)})
        add(0x2F, "CPL", c.transform("A", lambda a: a ^ 255, cpl))
        for opcode, name in ((0x37, "SCF"), (0x3F, "CCF")):
            paths = {}
            xy_paths = {}
            for a in range(256):
                xy = a & 0x28
                if xy not in xy_paths:
                    q_paths = {q: c.transform("F", lambda f, xy=xy, q=q, name=name:
                        (f & 0xC4) | (((f ^ q) | xy) & 0x28) |
                        (1 if name == "SCF" else ((f & 1) << 4) | ((f & 1) ^ 1)), c.retire(4)) for q in (0, 8, 32, 40)}
                    xy_paths[xy] = c.branch("OLDQ", {q: q_paths[q & 0x28] for q in range(256)})
                paths[a] = xy_paths[xy]
            add(opcode, name, c.branch("A", paths))

        add(0xEB, "EX DE,HL", self.exchange("D", "H", self.exchange("E", "L", c.retire(4))))
        add(0x08, "EX AF,AF'", self.exchange("A", "A_", self.exchange("F", "F_", c.retire(4))))
        end = c.retire(4)
        for reg in ("B", "C", "D", "E", "H", "L"):
            end = self.exchange(reg, reg + "_", end)
        add(0xD9, "EXX", end)
        for index, (low, high) in enumerate((*PAIRS[:3], ("F", "A"))):
            name = ("BC", "DE", "HL", "AF")[index]
            add(0xC5 + 16 * index, f"PUSH {name}", c.event("internal", 1, self.push(low, high, c.retire(11))))
            add(0xC1 + 16 * index, f"POP {name}", self.pop(low, high, c.retire(10)))

        # EX (SP),HL: read low/high, write high/low; SP remains unchanged.
        end = c.copy_pair("TMP", "TMP2", "L", "H", c.copy_pair("TMP", "TMP2", "WZL", "WZH", c.retire(19, 2)))
        end = c.copy("L", "DATA", c.memory_cycle(True, "write", 3, end))
        end = c.increment_pair("ADDRL", "ADDRH", -1, end)
        end = c.copy("H", "DATA", c.memory_cycle(True, "write", 3, end))
        end = c.copy("DATA", "TMP2", c.event("internal", 1, end))
        end = c.memory_cycle(False, "read", 3, end)
        end = c.copy("DATA", "TMP", c.increment_pair("ADDRL", "ADDRH", 1, end))
        add(0xE3, "EX (SP),HL", self.indirect("SPL", "SPH", False, end))

        jump = c.copy_pair("TMP", "TMP2", "PCL", "PCH", c.copy_pair("TMP", "TMP2", "WZL", "WZH", c.retire(10)))
        add(0xC3, "JP nn", c.fetch("TMP", c.fetch("TMP2", jump)))
        add(0xE9, "JP (HL)", c.copy_pair("L", "H", "PCL", "PCH", c.retire(4)))
        add(0x18, "JR e", c.fetch("DATA", c.relative_jump(c.retire(12, 5))))
        for index in range(4):
            jump = c.relative_jump(c.retire(12, 5))
            end = self.condition(index, jump, c.retire(7))
            add(0x20 + index * 8, f"JR {('NZ', 'Z', 'NC', 'C')[index]},e", c.fetch("DATA", end))
        ret = self.pop("PCL", "PCH", c.retire(10))
        add(0xC9, "RET", ret)
        call_end = c.copy_pair("TMP", "TMP2", "PCL", "PCH", c.retire(17))
        call = c.event("internal", 1, self.push("PCL", "PCH", call_end))
        add(0xCD, "CALL nn", c.fetch("TMP", c.fetch("TMP2", c.copy_pair("TMP", "TMP2", "WZL", "WZH", call))))
        for index in range(8):
            condition_name = ("NZ", "Z", "NC", "C", "PO", "PE", "P", "M")[index]
            jump = c.copy_pair("TMP", "TMP2", "PCL", "PCH", c.retire(10))
            choose = self.condition(index, jump, c.retire(10))
            add(0xC2 + 8 * index, f"JP {condition_name},nn", c.fetch("TMP", c.fetch("TMP2", c.copy_pair("TMP", "TMP2", "WZL", "WZH", choose))))
            choose = self.condition(index, call, c.retire(10))
            add(0xC4 + 8 * index, f"CALL {condition_name},nn", c.fetch("TMP", c.fetch("TMP2", c.copy_pair("TMP", "TMP2", "WZL", "WZH", choose))))
            ret = self.pop("PCL", "PCH", c.retire(11))
            add(0xC0 + 8 * index, f"RET {condition_name}", c.event("internal", 1, self.condition(index, ret, c.retire(5))))
            end = c.write("WZL", index * 8, c.write("WZH", 0, c.retire(11)))
            end = c.write("PCL", index * 8, c.write("PCH", 0, end))
            add(0xC7 + 8 * index, f"RST ${index * 8:02X}", c.event("internal", 1, self.push("PCL", "PCH", end)))
        add(0xF3, "DI", c.write("IFF1", 0, c.write("IFF2", 0, c.write("EI_DELAY", 0, c.retire(4)))))
        add(0xFB, "EI", c.write("IFF1", 1, c.write("IFF2", 1, c.write("EI_DELAY", 1, c.retire(4)))))
        for opcode, write, name in ((0xDB, False, "IN A,(n)"), (0xD3, True, "OUT (n),A")):
            end = c.retire(11) if write else c.copy("DATA", "A", c.retire(11))
            end = c.call(c.port, c.event("io_write" if write else "io_read", 4, end))
            end = self.memptr("ADDRL", "ADDRH", end, write)
            end = c.write("MODE", int(write), end)
            if write:
                end = c.copy("A", "DATA", end)
            end = c.copy_pair("TMP", "A", "ADDRL", "ADDRH", end)
            add(opcode, name, c.fetch("TMP", end))


def extend_unprefixed(compiler, opcodes):
    Unprefixed(compiler, opcodes).build()
