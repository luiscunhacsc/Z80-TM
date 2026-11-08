import unittest

from z80tm.compiler import build_cpu
from z80tm.harness import CPU, CPUHalted, DEMO, UnsupportedOpcode
from z80tm.layout import START, PAGE, RAM_START, POS, snapshot
from z80tm.machine import Machine, TransitionBudget


class MilestoneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = build_cpu()

    def cpu(self, hexcode, **kwargs):
        return CPU(bytes.fromhex(hexcode), program=self.program, **kwargs)

    def test_diagonal(self):
        cpu = CPU(DEMO, program=self.program).run()
        self.assertEqual([cpu.read_memory(0x4000 + 256 * i) for i in range(8)],
                         [0x80, 0x40, 0x20, 0x10, 8, 4, 2, 1])
        for name, value in {"A": 0x80, "B": 0, "HL": 0x4800, "F": 1, "PC": 13, "HALT": 1, "R": 36}.items():
            self.assertEqual(cpu.registers[name], value, name)
        self.assertEqual((cpu.instructions, cpu.tstates), (36, 247))
        self.assertEqual(cpu.machine.head, 0)
        self.assertEqual(cpu.machine.state, self.program.boundary)
        for page in range(256):
            self.assertEqual(cpu.machine.tape[RAM_START + 1 + 257 * page], PAGE)

    def test_same_table_relocated_across_page(self):
        cpu = self.cpu("3E A5 21 34 12 77 76", load=0x20FF).run()
        self.assertIs(cpu.machine.table, self.program.table)
        self.assertEqual(cpu.read_memory(0x1234), 0xA5)
        self.assertEqual((cpu.registers["A"], cpu.registers["HL"], cpu.registers["PC"]), (0xA5, 0x1234, 0x2106))
        self.assertEqual((cpu.instructions, cpu.tstates), (4, 28))

    def test_self_modifying_opcode(self):
        cpu = self.cpu("21 06 00 3E 76 77 3E 12 76").run()
        self.assertEqual(cpu.read_memory(6), 0x76)
        self.assertEqual((cpu.registers["A"], cpu.registers["PC"], cpu.instructions), (0x76, 7, 4))

    def test_inc_h_flags(self):
        # Explicit vectors: old H, expected H, expected F without carry.
        for old, result, flags in ((0, 1, 0), (0x0F, 0x10, 0x10), (0x7F, 0x80, 0x94),
                                   (0xFF, 0, 0x50), (0x27, 0x28, 0x28), (0x80, 0x81, 0x80)):
            for carry in (0, 1):
                with self.subTest(old=old, carry=carry):
                    cpu = self.cpu("24", registers={"H": old, "F": 0xFE | carry})
                    instruction = cpu.step_instruction()
                    self.assertEqual((cpu.registers["H"], cpu.registers["F"], instruction.tstates), (result, flags | carry, 4))

    def test_rrca_flags(self):
        for old, value, xy_c in ((0x01, 0x80, 1), (0x80, 0x40, 0), (0x51, 0xA8, 0x29), (0, 0, 0)):
            for preserved in (0, 0x80, 0x40, 4, 0xC4):
                with self.subTest(old=old, preserved=preserved):
                    cpu = self.cpu("0F", registers={"A": old, "F": preserved | 0x3B})
                    cpu.step_instruction()
                    self.assertEqual((cpu.registers["A"], cpu.registers["F"]), (value, xy_c | preserved))

    def test_djnz(self):
        for load, b, offset, expected_pc, timing in (
            (0x100, 1, 0x80, 0x102, 8), (0x100, 2, 0x80, 0x82, 13),
            (0x100, 0, 0x7F, 0x181, 13), (0, 2, 0xFD, 0xFFFF, 13),
            (0xFFFF, 2, 0xFF, 0, 13), (0xFFFF, 1, 0x7F, 1, 8),
        ):
            with self.subTest(load=load, b=b, offset=offset):
                cpu = self.cpu(f"10 {offset:02X}", load=load, registers={"B": b, "F": 0xD7})
                instruction = cpu.step_instruction()
                self.assertEqual((cpu.registers["B"], cpu.registers["PC"], cpu.registers["F"], instruction.tstates),
                                 ((b - 1) & 255, expected_pc, 0xD7, timing))

    def test_fetch_wraparound(self):
        cpu = self.cpu("21 34 12 76", load=0xFFFE).run()
        self.assertEqual((cpu.registers["HL"], cpu.registers["PC"]), (0x1234, 2))
        cpu = self.cpu("76", load=0xFFFF).run()
        self.assertEqual(cpu.registers["PC"], 0)

    def test_write_first_and_last_bytes(self):
        for address in (0, 255, 256, 65535):
            with self.subTest(address=address):
                cpu = self.cpu("77 76", load=0x1000, registers={"HL": address, "A": 0xA5}).run()
                self.assertEqual(cpu.read_memory(address), 0xA5)

    def test_unsupported_halt_and_budget_are_distinct(self):
        cpu = self.cpu("ED")
        # Every NMOS encoding now has semantics. Exercise the retained diagnostic
        # independently by initializing the generated unsupported state.
        cpu.machine.state = self.program.unsupported
        cpu.machine.tape[POS["OP"]] = 0xED
        with self.assertRaisesRegex(UnsupportedOpcode, r"\$ED at \$0000"):
            cpu.run()
        self.assertFalse(cpu.halted)
        self.assertEqual(cpu.instructions, 0)
        with self.assertRaises(UnsupportedOpcode):
            cpu.step_instruction()
        cpu = self.cpu("76")
        with self.assertRaises(TransitionBudget):
            cpu.run(budget=17)
        self.assertEqual(cpu.machine.transitions, 17)
        self.assertFalse(cpu.halted)
        cpu.run()
        self.assertEqual(cpu.instructions, 1)
        self.assertTrue(cpu.halted)
        before = cpu.machine.transitions
        cpu.run()
        self.assertEqual(cpu.machine.transitions, before)
        with self.assertRaises(CPUHalted):
            cpu.step_instruction()

    def test_machine_cycle_trace(self):
        cpu = CPU(DEMO, program=self.program)
        trace = []
        cpu.run(trace=trace.append, trace_cycles=True)
        self.assertEqual(sum(sum(c.tstates for c in i.cycles) for i in trace), 247)
        for instruction in trace:
            self.assertEqual(sum(c.tstates for c in instruction.cycles), instruction.tstates)
        self.assertEqual([(c.kind, c.address, c.tstates) for c in trace[0].cycles],
                         [("m1", 0, 4), ("read", 1, 3), ("read", 2, 3)])
        self.assertEqual([(c.kind, c.address, c.tstates) for c in trace[3].cycles],
                         [("m1", 7, 4), ("write", 0x4000, 3)])
        self.assertEqual(trace[-1].cycles[-1].start, 243)

    def test_refresh_low_seven_bits(self):
        for initial, expected in ((0x7F, 0), (0xFF, 0x80), (0xFE, 0xFF)):
            cpu = self.cpu("76", registers={"R": initial}).run()
            self.assertEqual(cpu.registers["R"], expected)

    def test_raw_machine_without_harness(self):
        cpu = CPU(DEMO, program=self.program)
        m = cpu.machine
        count = 0
        while not snapshot(m.tape)["HALT"]:
            m.run_until(frozenset((self.program.boundary,)), 2_000_000)
            count += 1
        self.assertEqual(count, 36)
        self.assertEqual(snapshot(m.tape)["HL"], 0x4800)
        self.assertEqual(m.tape[0], START)


if __name__ == "__main__":
    unittest.main()

