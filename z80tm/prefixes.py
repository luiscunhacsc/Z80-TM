"""Compile prefix decoding and CB/indexed operations into ordinary TM rules."""
from functools import cache

from .machine import Rule
from .unprefixed import REGS, PAIRS, szp


def modifies_flags(op):
    return (0x80 <= op <= 0xBF or op & 0xC7 in (0x04, 0x05, 0xC6)
            or op in (0x07, 0x0F, 0x17, 0x1F, 0x27, 0x2F, 0x37, 0x3F, 9, 25, 41, 57))


class Prefixes:
    def __init__(self, compiler, unprefixed, base):
        self.c, self.u, self.base = compiler, unprefixed, base
        self.decode = self.c.b.reserve("prefix-aware decode")

    def wrap(self, entry, family, flags):
        return self.c.write("FAMILY", family, self.c.write("MODF", int(flags), entry))

    @cache
    def cb_core(self, operation, bit, xy_slot):
        c = self.c
        end = c.return_state
        if operation in (2, 3):
            return c.transform("DATA", lambda x: x & ~(1 << bit) if operation == 2 else x | (1 << bit), end)
        targets = {}
        bit_paths = {}
        for value in range(256):
            if operation == 1:
                flags = 16 | (0 if value & (1 << bit) else 0x44) | (0x80 if bit == 7 and value & 128 else 0)
                if xy_slot is None:
                    targets[value] = c.transform("F", lambda f, v=value, b=flags: (f & 1) | (v & 0x28) | b, end)
                else:
                    if flags not in bit_paths:
                        bit_paths[flags] = c.branch(xy_slot, {
                            x: c.transform("F", lambda f, x=x, b=flags: (f & 1) | (x & 0x28) | b, end) for x in range(256)
                        })
                    targets[value] = bit_paths[flags]
                continue
            rules = {}
            for f in range(256):
                carry = value >> 7 if bit in (0, 2, 4, 6) else value & 1
                result = (
                    ((value << 1) | (value >> 7)) & 255 if bit == 0 else
                    (value >> 1) | ((value & 1) << 7) if bit == 1 else
                    ((value << 1) | (f & 1)) & 255 if bit == 2 else
                    (value >> 1) | ((f & 1) << 7) if bit == 3 else
                    (value << 1) & 255 if bit == 4 else
                    (value >> 1) | (value & 128) if bit == 5 else
                    ((value << 1) | 1) & 255 if bit == 6 else value >> 1
                )
                done = c.write("DATA", result, end)
                rules[f] = Rule(c.home(done), szp(result) | carry, -1)
            targets[value] = c.at("F", c.b.emit("CB rotate flags", rules))
        return c.branch("DATA", targets)

    @cache
    def signed_address_core(self):
        c = self.c
        done = c.return_state
        up = c.transform("EA_H", lambda h: (h + 1) & 255, done)
        down = c.transform("EA_H", lambda h: (h - 1) & 255, done)
        paths = {}
        for displacement in range(256):
            delta = displacement if displacement < 128 else displacement - 256
            paths[displacement] = c.at("EA_L", c.b.emit("add index displacement", {
                low: Rule(c.home(down if low + delta < 0 else up if low + delta > 255 else done), (low + delta) & 255, -1)
                for low in range(256)
            }))
        return c.branch("DISP", paths)

    def effective(self, index, next_state, delay=5):
        c = self.c
        end = c.copy_pair("EA_L", "EA_H", "WZL", "WZH", next_state)
        end = c.call(self.signed_address_core(), end)
        end = c.copy_pair(index + "L", index + "H", "EA_L", "EA_H", end)
        if delay:
            end = c.event("internal", delay, end)
        return c.fetch("DISP", end)

    def cb(self, indexed=False):
        c, u = self.c, self.u
        operations, names = {}, {}
        for op in range(256):
            group, bit, reg = op >> 6, (op >> 3) & 7, op & 7
            memory = indexed or reg == 6
            end = c.retire(0)
            if group != 1:
                if indexed and reg != 6:
                    end = c.copy("DATA", REGS[reg], end)
                if memory:
                    end = c.memory_cycle(True, "write", 3, end)
                else:
                    end = c.copy("DATA", REGS[reg], end)
            if memory:
                end = c.event("internal", 1, end)
            end = c.call(self.cb_core(group, bit, "WZH" if memory and group == 1 else None), end)
            if memory:
                low, high = ("EA_L", "EA_H") if indexed else ("L", "H")
                end = u.indirect(low, high, False, end)
            else:
                end = c.copy(REGS[reg], "DATA", end)
            if indexed:
                operations[op] = c.branch("INDEX", {1: self.wrap(end, 5, group < 2), 2: self.wrap(end, 6, group < 2)})
            else:
                operations[op] = self.wrap(end, 1, group < 2)
            names[op] = f"{('ROT', 'BIT', 'RES', 'SET')[group]} {bit},{'(IX/IY+d)' if indexed else REGS[reg] or '(HL)'}"
        c.families["DDCB" if indexed else "CB"] = names
        if indexed:
            c.families["FDCB"] = dict(names)
        return c.branch("OP", operations)

    def indexed(self, index):
        c, u = self.c, self.u
        low, high = index + "L", index + "H"
        ops = dict(self.base)
        names = dict(c.coverage)
        def add(op, name, entry):
            ops[op], names[op] = entry, name
        add(0x21, f"LD {index},nn", c.fetch(low, c.fetch(high, c.retire(0))))
        add(0x23, f"INC {index}", c.increment_pair(low, high, 1, c.retire(0, 2)))
        add(0x2B, f"DEC {index}", c.increment_pair(low, high, -1, c.retire(0, 2)))
        for i, pair in enumerate(PAIRS):
            source = (low, high) if i == 2 else pair
            end = c.event("internal", 4, c.event("internal", 3, c.retire(0)))
            end = c.branch("FLAGS", {f: c.transform("F", lambda old, f=f: (old & 0xC4) | (f & 0x39), end) for f in range(256)})
            end = c.copy("RESULT", high, end)
            end = c.call(u.alu_core("ADC"), end)
            end = c.copy(high, "TMP", c.copy(source[1], "TMP2", end))
            end = c.copy("RESULT", low, end)
            end = c.call(u.alu_core("ADD"), end)
            end = c.copy(low, "TMP", c.copy(source[0], "TMP2", end))
            end = c.copy_pair(low, high, "WZL", "WZH", c.increment_pair("WZL", "WZH", 1, end))
            add(9 + 16 * i, f"ADD {index},rr", end)
        end = c.copy(high, "DATA", c.memory_cycle(True, "write", 3, c.retire(0)))
        end = c.increment_pair("ADDRL", "ADDRH", 1, c.copy_pair("ADDRL", "ADDRH", "WZL", "WZH", end))
        add(0x22, f"LD (nn),{index}", u.absolute(c.copy(low, "DATA", c.memory_cycle(True, "write", 3, end))))
        end = c.memory_cycle(False, "read", 3, c.copy("DATA", high, c.retire(0)))
        end = c.increment_pair("ADDRL", "ADDRH", 1, c.copy_pair("ADDRL", "ADDRH", "WZL", "WZH", end))
        add(0x2A, f"LD {index},(nn)", u.absolute(c.memory_cycle(False, "read", 3, c.copy("DATA", low, end))))
        add(0xE5, f"PUSH {index}", c.event("internal", 1, u.push(low, high, c.retire(0))))
        add(0xE1, f"POP {index}", u.pop(low, high, c.retire(0)))
        add(0xE9, f"JP ({index})", c.copy_pair(low, high, "PCL", "PCH", c.retire(0)))
        add(0xF9, f"LD SP,{index}", c.copy_pair(low, high, "SPL", "SPH", c.retire(0, 2)))
        end = c.copy_pair("TMP", "TMP2", low, high, c.copy_pair("TMP", "TMP2", "WZL", "WZH", c.retire(0, 2)))
        end = c.copy(low, "DATA", c.memory_cycle(True, "write", 3, end))
        end = c.increment_pair("ADDRL", "ADDRH", -1, end)
        end = c.copy(high, "DATA", c.memory_cycle(True, "write", 3, end))
        end = c.copy("DATA", "TMP2", c.event("internal", 1, end))
        end = c.memory_cycle(False, "read", 3, end)
        end = c.copy("DATA", "TMP", c.increment_pair("ADDRL", "ADDRH", 1, end))
        add(0xE3, f"EX (SP),{index}", u.indirect("SPL", "SPH", False, end))
        mapped = (*REGS[:4], high, low, None, "A")
        for r in (4, 5):
            add(6 + r * 8, f"LD {mapped[r]},n", c.fetch(mapped[r], c.retire(0)))
            for delta, base in ((1, 4), (-1, 5)):
                add(base + r * 8, f"INC/DEC {mapped[r]}", c.inc_dec(mapped[r], delta, c.retire(0)))
        for op in range(0x40, 0xC0):
            if op == 0x76:
                continue
            dst, src = (op >> 3) & 7, op & 7
            if op < 0x80:
                if dst == 6:
                    end = c.copy(REGS[src], "DATA", u.indirect("EA_L", "EA_H", True, c.retire(0)))
                    end = self.effective(index, end)
                elif src == 6:
                    end = self.effective(index, u.indirect("EA_L", "EA_H", False, c.copy("DATA", REGS[dst], c.retire(0))))
                else:
                    end = c.copy(mapped[src], mapped[dst], c.retire(0))
            else:
                kind = ("ADD", "ADC", "SUB", "SBC", "AND", "XOR", "OR", "CP")[dst]
                end = u.alu(kind, c.retire(0))
                end = self.effective(index, u.indirect("EA_L", "EA_H", False, end)) if src == 6 else c.copy(mapped[src], "DATA", end)
            add(op, f"{index}: {names[op]}", end)
        for op, delta in ((0x34, 1), (0x35, -1)):
            end = c.inc_dec("DATA", delta, c.event("internal", 1, c.memory_cycle(True, "write", 3, c.retire(0))))
            add(op, f"INC/DEC ({index}+d)", self.effective(index, u.indirect("EA_L", "EA_H", False, end)))
        end = c.fetch("DATA", c.event("internal", 2, u.indirect("EA_L", "EA_H", True, c.retire(0))))
        add(0x36, f"LD ({index}+d),n", self.effective(index, end, delay=0))
        c.families["DD" if index == "IX" else "FD"] = names
        return {op: self.wrap(entry, 3 if index == "IX" else 4, modifies_flags(op)) for op, entry in ops.items()}

    def build(self):
        c = self.c
        cb = c.fetch("OP", self.cb(), opcode=True)
        ix, iy = self.indexed("IX"), self.indexed("IY")
        indexed_cb = self.cb(True)
        indexed_cb = c.fetch("OP", c.event("internal", 2, indexed_cb))
        cb_select = c.branch("INDEX", {0: cb, 1: self.effective("IX", indexed_cb, 0), 2: self.effective("IY", indexed_cb, 0)})
        from .ed import ED
        ed = c.fetch("OP", ED(c, self.u).build(), opcode=True)
        prefix_states = {0xCB: cb_select, 0xED: c.write("INDEX", 0, ed)}
        for op, index in ((0xDD, 1), (0xFD, 2)):
            prefix_states[op] = c.write("INDEX", index, c.fetch("OP", self.decode, opcode=True))
        base = {op: self.wrap(entry, 0, modifies_flags(op)) for op, entry in self.base.items()}
        for mapping in (base, ix, iy):
            mapping.update(prefix_states)
        select = c.branch("INDEX", {0: c.branch("OP", base), 1: c.branch("OP", ix), 2: c.branch("OP", iy)})
        c.b.define(self.decode, default=Rule(select, None, 0))
        return self.decode
