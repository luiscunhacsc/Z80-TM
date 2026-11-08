"""Tape-clock interrupt scheduling, acknowledgement, and HALT recovery."""
from .machine import Rule
from .layout import (IRQ_ACTIVE, IRQ_RECORD, IRQ_END, IRQ_ACTIVE_END,
                     RESPONSE_ACTIVE, RESPONSE_RECORD, RESPONSE_END, RESPONSE_ACTIVE_END)


class Interrupts:
    def __init__(self, compiler, unprefixed, decode):
        self.c, self.u, self.decode = compiler, unprefixed, decode

    def response(self):
        c = self.c
        done = c.home(c.return_state)
        advance = c.b.reserve("next IM0 byte")
        c.b.define(advance, {RESPONSE_RECORD: Rule(done, RESPONSE_ACTIVE, -1),
                             RESPONSE_END: Rule(done, RESPONSE_ACTIVE_END, -1)}, Rule(advance, None, 1))
        consumed = c.b.emit("consume IM0 cursor", {RESPONSE_ACTIVE: Rule(advance, RESPONSE_RECORD, 1)})
        consumed = c.seek(RESPONSE_ACTIVE, 1, consumed, 0)
        read = c.b.emit("read IM0 byte", {v: Rule(c.home(c.write("DATA", v, consumed)), None, -1) for v in range(256)})
        scan = c.b.reserve("seek IM0 byte")
        c.b.define(scan, {RESPONSE_ACTIVE: Rule(read, None, 1),
                          RESPONSE_ACTIVE_END: Rule(c.interrupt_error, None, 0)}, Rule(scan, None, 1))
        c.b.define(c.response, default=Rule(scan, None, 0))

    def schedule(self, next_state):
        c = self.c
        poll = c.b.reserve("poll tape interrupt clock")
        after_record = c.branch("HAS_SCRIPT", {0: next_state, 1: poll})
        load = after_record
        fields = (*[f"DUE{i}" for i in range(8)], "NEXT_KIND", "NEXT_VECTOR")
        for offset, slot in reversed(list(enumerate(fields, 1))):
            read = c.b.emit("read interrupt record field", {v: Rule(c.home(c.write(slot, v, load)), None, -1) for v in range(256)})
            for _ in range(offset - 1):
                read = c.b.emit("walk interrupt record", default=Rule(read, None, 1))
            load = c.seek(IRQ_ACTIVE, 1, read)
        advance = c.b.reserve("advance interrupt script")
        c.b.define(advance, {IRQ_RECORD: Rule(c.home(load), IRQ_ACTIVE, -1),
                             IRQ_END: Rule(c.home(c.write("HAS_SCRIPT", 0, next_state)), IRQ_ACTIVE_END, -1)}, Rule(advance, None, 1))
        consume = c.b.emit("consume interrupt record", {IRQ_ACTIVE: Rule(advance, IRQ_RECORD, 1)})
        consume = c.seek(IRQ_ACTIVE, 1, consume, 0)
        due = c.branch("NEXT_KIND", {
            0: c.write("IRQ", 0, consume),
            1: c.write("IRQ", 1, c.copy("NEXT_VECTOR", "VECTOR", consume)),
            2: c.write("NMI", 1, consume),
        })
        equal = due
        for i in range(8):
            paths = {}
            for clock in range(256):
                rules = {deadline: Rule(c.home(due if clock > deadline else next_state if clock < deadline else equal), None, -1)
                         for deadline in range(256)}
                paths[clock] = c.at(f"DUE{i}", c.b.emit("compare scheduled time byte", rules))
            equal = c.branch(f"CLOCK{i}", paths)
        c.b.define(poll, default=Rule(equal, None, 0))
        return c.branch("HAS_SCRIPT", {0: next_state, 1: poll})

    def ack(self, kind, timing, next_state):
        c = self.c
        end = c.event(kind, timing, next_state)
        end = c.transform("R", lambda r: (r & 128) | ((r + 1) & 127), end)
        return c.copy_pair("PCL", "PCH", "ADDRL", "ADDRH", end)

    def build(self, normal):
        c, u = self.c, self.u
        self.response()
        # Halted M1 cycles fetch and discard at the resume PC; PC does not move.
        idle = c.write("Q", 0, c.halted)
        idle = c.event("halt_m1", 4, idle)
        idle = c.transform("R", lambda r: (r & 128) | ((r + 1) & 127), idle)
        idle = c.memory_call(False, idle)
        idle = c.copy_pair("PCL", "PCH", "ADDRL", "ADDRH", idle)
        idle = c.write("ACTION", 4, idle)
        idle = c.write("P", 0, c.write("EI_DELAY", 0, c.write("NMI_DELAY", 0, idle)))
        no_interrupt = c.branch("HALT", {0: normal, 1: idle})

        end = c.write("PCL", 0x66, c.write("PCH", 0,
              c.write("WZL", 0x66, c.write("WZH", 0, c.interrupt_boundary))))
        nmi = self.ack("nmi_ack", 5, u.push("PCL", "PCH", end))
        nmi = c.copy_pair("PCL", "PCH", "ADDRL", "ADDRH", c.memory_call(False, nmi))
        for slot, value in (("ACTION", 1), ("NMI", 0), ("IFF1", 0), ("HALT", 0), ("Q", 0), ("P", 0), ("NMI_DELAY", 1)):
            nmi = c.write(slot, value, nmi)

        mode1 = c.write("PCL", 0x38, c.write("PCH", 0,
                c.write("WZL", 0x38, c.write("WZH", 0, c.interrupt_boundary))))
        mode1 = self.ack("int_ack", 7, u.push("PCL", "PCH", mode1))
        mode2 = c.copy_pair("TMP", "DATA", "PCL", "PCH",
                c.copy_pair("TMP", "DATA", "WZL", "WZH", c.interrupt_boundary))
        mode2 = c.memory_cycle(False, "read", 3, mode2)
        mode2 = c.copy("DATA", "TMP", c.increment_pair("ADDRL", "ADDRH", 1, mode2))
        mode2 = c.memory_cycle(False, "read", 3, mode2)
        mode2 = c.copy_pair("VECTOR", "I", "ADDRL", "ADDRH", mode2)
        mode2 = self.ack("int_ack", 7, u.push("PCL", "PCH", mode2))
        mode0 = c.copy("VECTOR", "OP", c.copy("VECTOR", "FIRSTOP", self.decode))
        mode0 = self.ack("int_ack", 6, mode0)
        mode0 = c.write("IM0", 1, c.write("ACTION", 3, mode0))
        irq = c.branch("IM", {0: mode0, 1: mode1, 2: mode2})
        irq = c.copy("VECTOR", "DATA", irq)
        for slot, value in (("ACTION", 2), ("IFF1", 0), ("IFF2", 0), ("HALT", 0), ("Q", 0), ("OLDQ", 0)):
            irq = c.write(slot, value, irq)
        # Zilog NMOS LD A,I/R P/V quirk on maskable interrupt acceptance.
        irq = c.write("P", 0, irq)
        irq = c.branch("P", {0: irq, 1: c.transform("F", lambda f: f & ~4, irq)})
        irq = c.branch("EI_DELAY", {0: irq, 1: no_interrupt})
        irq = c.branch("IFF1", {0: no_interrupt, 1: irq})
        irq = c.branch("IRQ", {0: no_interrupt, 1: irq})
        nmi_check = c.branch("NMI_DELAY", {0: nmi, 1: irq})
        select = c.branch("NMI", {0: irq, 1: nmi_check})
        return self.schedule(select)
