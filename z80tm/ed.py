"""ED instruction families. All arithmetic below is table-generation only."""
from functools import cache

from .machine import Rule
from .unprefixed import PAIRS, REGS, szp


class ED:
    def __init__(self, compiler, unprefixed):
        self.c, self.u = compiler, unprefixed

    def nonzero_pair(self, low, high, yes, no):
        c = self.c
        high_test = c.branch(high, {x: yes if x else no for x in range(256)})
        return c.branch(low, {x: yes if x else high_test for x in range(256)})

    def adc_sbc(self, pair, subtract):
        c, u = self.c, self.u
        kind = "SBC" if subtract else "ADC"
        end = c.event("internal", 4, c.event("internal", 3, c.retire(0)))
        end = c.branch("L", {x: c.transform("FLAGS", lambda f: f & ~0x40, c.copy("FLAGS", "F", end))
                              if x else c.copy("FLAGS", "F", end) for x in range(256)})
        end = c.copy("RESULT", "H", end)
        end = c.call(u.alu_core(kind), end)
        end = c.copy("H", "TMP", c.copy(pair[1], "TMP2", end))
        end = c.copy("RESULT", "L", end)
        end = c.call(u.alu_core(kind), end)
        end = c.copy("L", "TMP", c.copy(pair[0], "TMP2", c.copy("F", "FLAGS", end)))
        return c.copy_pair("L", "H", "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, end))

    def input_flags(self, next_state):
        c = self.c
        return c.branch("DATA", {v: c.transform("F", lambda f, v=v: (f & 1) | szp(v), next_state) for v in range(256)})

    def port(self, write, next_state):
        c = self.c
        end = c.call(c.port, c.event("io_write" if write else "io_read", 4, next_state))
        return c.copy_pair("C", "B", "ADDRL", "ADDRH", c.write("MODE", int(write), end))

    def nibble_rotate(self, left):
        c, u = self.c, self.u
        end = c.memory_cycle(True, "write", 3, c.retire(0))
        end = c.branch("A", {a: c.transform("F", lambda f, a=a: (f & 1) | szp(a), end) for a in range(256)})
        a_paths = {n: c.transform("A", lambda a, n=n: (a & 0xF0) | n, end) for n in range(16)}
        end = c.branch("SAVE", {m: a_paths[m >> 4 if left else m & 15] for m in range(256)})
        memory_paths = {n: c.transform("DATA", lambda m, n=n: ((m << 4) | n) & 255 if left else (n << 4) | (m >> 4), end) for n in range(16)}
        end = c.branch("A", {a: memory_paths[a & 15] for a in range(256)})
        end = c.copy("DATA", "SAVE", end)
        end = c.event("internal", 4, end)
        end = u.indirect("L", "H", False, end)
        return c.copy_pair("L", "H", "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, end))

    def repeat(self, next_state, io=False):
        c = self.c
        end = c.event("internal", 5, next_state)
        end = c.copy_pair("PCL", "PCH", "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, end))
        end = c.branch("PCH", {h: c.transform("F", lambda f, h=h: (f & ~0x28) | (h & 0x28), end) for h in range(256)})
        return c.increment_pair("PCL", "PCH", -1, c.increment_pair("PCL", "PCH", -1, end))

    def transfer(self, delta, repeating):
        c, u = self.c, self.u
        end = c.retire(0)
        if repeating:
            end = self.nonzero_pair("C", "B", self.repeat(end), end)
        pv = self.nonzero_pair("C", "B", c.transform("F", lambda f: f | 4, end), c.transform("F", lambda f: f & ~4, end))
        flags = c.branch("RESULT", {v: c.transform("F", lambda f, v=v: (f & 0xC1) | (v & 8) | ((v & 2) << 4), pv) for v in range(256)})
        end = c.copy("A", "TMP", c.copy("DATA", "TMP2", c.call(u.alu_core("ADD"), flags)))
        end = c.event("internal", 2, end)
        end = c.increment_pair("C", "B", -1, end)
        end = c.increment_pair("L", "H", delta, c.increment_pair("E", "D", delta, end))
        return u.indirect("L", "H", False, u.indirect("E", "D", True, end))

    def compare_block(self, delta, repeating):
        c, u = self.c, self.u
        finish = c.increment_pair("WZL", "WZH", delta, c.retire(0))
        if repeating:
            repeat = self.repeat(c.retire(0))
            choose = c.branch("F", {f: finish if f & 0x40 else repeat for f in range(256)})
            finish = self.nonzero_pair("C", "B", choose, finish)
        pv = self.nonzero_pair("C", "B", c.transform("F", lambda f: f | 4, finish), c.transform("F", lambda f: f & ~4, finish))
        paths = {}
        flag_paths = {}
        flag_writes = {out: c.transform("F", lambda f, out=out: (f & 1) | out, pv) for out in range(256)}
        for flags in range(256):
            key = flags & 0xD2
            if key not in flag_paths:
                targets = {}
                for result in range(256):
                    adjusted = (result - ((flags >> 4) & 1)) & 255
                    out = key | (adjusted & 8) | ((adjusted & 2) << 4)
                    targets[result] = flag_writes[out]
                flag_paths[key] = c.branch("RESULT", targets)
            paths[flags] = flag_paths[key]
        end = c.call(u.alu_core("SUB"), c.branch("FLAGS", paths))
        end = c.copy("A", "TMP", c.copy("DATA", "TMP2", end))
        end = c.increment_pair("C", "B", -1, c.increment_pair("L", "H", delta, end))
        return u.indirect("L", "H", False, c.event("internal", 5, end))

    @cache
    def block_io_flags(self, repeating, next_state):
        c = self.c
        nf_paths = {}
        for nf in (0, 2):
            carry_paths = {}
            for carry in (0, 1):
                sums = {}
                for t in range(8):
                    b_paths = {}
                    for b in range(256):
                        p = t ^ b
                        flags = (b & 0xA8) | (0x40 if not b else 0) | (4 if p.bit_count() % 2 == 0 else 0) | nf | (0x11 if carry else 0)
                        if repeating and b:
                            half = (not (b & 15)) if nf else ((b & 15) == 15)
                            p ^= ((b - 1 if nf else b + 1) & 7) if carry else (b & 7)
                            flags = (b & 128) | nf | carry | (16 if carry and half else 0) | (4 if p.bit_count() % 2 == 0 else 0)
                        b_paths[b] = c.write("F", flags, next_state)
                    sums[t] = c.branch("B", b_paths)
                carry_paths[carry] = c.branch("RESULT", {v: sums[v & 7] for v in range(256)})
            nf_paths[nf] = c.branch("FLAGS", {f: carry_paths[f & 1] for f in range(256)})
        return c.branch("SAVE", {v: nf_paths[(v >> 6) & 2] for v in range(256)})

    def block_io(self, delta, write, repeating):
        c, u = self.c, self.u
        finish = c.retire(0)
        if repeating:
            finish = c.branch("B", {b: self.repeat(finish, True) if b else finish for b in range(256)})
        flags = self.block_io_flags(repeating, finish)
        end = c.call(u.alu_core("ADD"), flags)
        operand = "L" if write else "C"
        end = c.copy(operand, "TMP2", end)
        if not write:
            end = c.copy("C", "TMP2", c.transform("TMP2", lambda v: (v + delta) & 255, c.call(u.alu_core("ADD"), flags)))
        end = c.copy("SAVE", "TMP", end)
        if write:
            end = self.port(True, end)
            end = c.copy_pair("C", "B", "WZL", "WZH", c.increment_pair("WZL", "WZH", delta, end))
            end = c.transform("B", lambda b: (b - 1) & 255, end)
            end = c.increment_pair("L", "H", delta, end)
            end = c.copy("DATA", "SAVE", end)
            end = u.indirect("L", "H", False, end)
        else:
            end = c.transform("B", lambda b: (b - 1) & 255, end)
            end = c.copy_pair("C", "B", "WZL", "WZH", c.increment_pair("WZL", "WZH", delta, end))
            end = c.increment_pair("L", "H", delta, end)
            end = u.indirect("L", "H", True, end)
            end = self.port(False, c.copy("DATA", "SAVE", end))
        return c.event("internal", 1, end)

    def build(self):
        c, u = self.c, self.u
        # Undefined ED bytes perform the NMOS two-byte NOP; aliases are explicit.
        ops = {op: c.write("MODF", 0, c.retire(0)) for op in range(256)}
        names = {op: "NOP (undefined ED)" for op in range(256)}
        def add(op, name, entry, flags=False):
            ops[op], names[op] = c.write("MODF", int(flags), entry), name
        for i, pair in enumerate(PAIRS):
            for subtract, base in ((True, 0x42), (False, 0x4A)):
                add(base + i * 16, "SBC HL,rr" if subtract else "ADC HL,rr", self.adc_sbc(pair, subtract), True)
            end = c.copy(pair[1], "DATA", c.memory_cycle(True, "write", 3, c.retire(0)))
            end = c.increment_pair("ADDRL", "ADDRH", 1, c.copy_pair("ADDRL", "ADDRH", "WZL", "WZH", end))
            add(0x43 + 16 * i, "LD (nn),rr", u.absolute(c.copy(pair[0], "DATA", c.memory_cycle(True, "write", 3, end))))
            end = c.memory_cycle(False, "read", 3, c.copy("DATA", pair[1], c.retire(0)))
            end = c.copy("DATA", pair[0], c.increment_pair("ADDRL", "ADDRH", 1, c.copy_pair("ADDRL", "ADDRH", "WZL", "WZH", end)))
            add(0x4B + 16 * i, "LD rr,(nn)", u.absolute(c.memory_cycle(False, "read", 3, end)))
        for r, reg in enumerate(REGS):
            end = c.retire(0) if reg is None else c.copy("DATA", reg, c.retire(0))
            end = self.port(False, self.input_flags(end))
            end = c.copy_pair("C", "B", "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, end))
            add(0x40 + r * 8, f"IN {reg or '(discard)'},(C)", end, True)
            end = self.port(True, c.retire(0))
            end = c.copy(reg, "DATA", end) if reg else c.write("DATA", 0, end)
            end = c.copy_pair("C", "B", "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, end))
            add(0x41 + r * 8, f"OUT (C),{reg or '0'}", end)
        for op in range(0x44, 0x80, 8):
            end = c.copy("A", "DATA", c.write("A", 0, u.alu("SUB", c.retire(0))))
            add(op, "NEG", end, True)
        for op in range(0x45, 0x80, 8):
            end = c.copy_pair("PCL", "PCH", "WZL", "WZH", c.retire(0))
            end = u.pop("PCL", "PCH", end)
            if op == 0x4D:
                end = c.write("RETI", 1, end)
            end = c.copy("IFF2", "IFF1", end)
            paths = {a: c.branch("IFF2", {b: c.write("EI_DELAY", int(a != b), end) for b in (0, 1)}) for a in (0, 1)}
            add(op, "RETI" if op == 0x4D else "RETN", c.branch("IFF1", paths))
        for op, mode in ((0x46, 0), (0x4E, 0), (0x56, 1), (0x5E, 2), (0x66, 0), (0x6E, 0), (0x76, 1), (0x7E, 2)):
            add(op, f"IM {mode}", c.write("IM", mode, c.retire(0)))
        for op, reg in ((0x47, "I"), (0x4F, "R")):
            add(op, f"LD {reg},A", c.copy("A", reg, c.retire(0, 1)))
        for op, reg in ((0x57, "I"), (0x5F, "R")):
            end = c.write("P", 1, c.retire(0, 1))
            paths = {}
            for value in range(256):
                base = (value & 0xA8) | (0x40 if value == 0 else 0)
                paths[value] = c.branch("IFF2", {iff: c.transform("F", lambda f, base=base, iff=iff: (f & 1) | base | (iff << 2), end) for iff in (0, 1)})
            add(op, f"LD A,{reg}", c.copy(reg, "A", c.branch("A", paths)), True)
        add(0x67, "RRD", self.nibble_rotate(False), True)
        add(0x6F, "RLD", self.nibble_rotate(True), True)
        for delta, offset in ((1, 0), (-1, 8)):
            for repeating, base in ((False, 0xA0), (True, 0xB0)):
                add(base + offset, "LD block", self.transfer(delta, repeating), True)
                add(base + offset + 1, "CP block", self.compare_block(delta, repeating), True)
                add(base + offset + 2, "IN block", self.block_io(delta, False, repeating), True)
                add(base + offset + 3, "OUT block", self.block_io(delta, True, repeating), True)
        c.families["ED"] = names
        return c.write("FAMILY", 2, c.branch("OP", ops))
