import unittest

from z80tm.compiler import build_cpu
from z80tm.harness import CPU, PortScriptError
from z80tm.machine import TransitionBudget


class ExtendedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = build_cpu()

    def cpu(self, hexcode, **kwargs):
        return CPU(bytes.fromhex(hexcode), program=self.program, **kwargs)

    def test_all_unprefixed_opcodes_declared(self):
        self.assertEqual(set(range(256)) - set(self.program.coverage), {0xCB, 0xDD, 0xED, 0xFD})

    def test_load_word_wrap_and_little_endian(self):
        cpu = self.cpu("22 FF FF 21 00 00 2A FF FF 76", load=0x200,
                       registers={"HL": 0xABCD}).run()
        self.assertEqual(cpu.registers["HL"], 0xABCD)
        self.assertEqual((cpu.read_memory(65535), cpu.read_memory(0)), (0xCD, 0xAB))

    def test_stack_call_return_wrap(self):
        # Main calls a routine which pushes AF, modifies A, then restores it.
        cpu = self.cpu("3E 55 CD 08 01 76 00 00 F5 3E AA F1 C9", load=0x100,
                       registers={"SP": 1, "F": 0xA5}).run()
        self.assertEqual((cpu.registers["A"], cpu.registers["F"], cpu.registers["SP"], cpu.registers["PC"]),
                         (0x55, 0xA5, 1, 0x106))
        self.assertEqual((cpu.read_memory(0), cpu.read_memory(65535)), (1, 5))
        self.assertEqual(cpu.tstates, 7 + 17 + 11 + 7 + 10 + 10 + 4)

    def test_exchange_main_alternate_and_stack(self):
        registers = {"AF": 0x1234, "AF_": 0x5678, "BC": 0x9ABC, "BC_": 0xDEF0,
                     "DE": 0x1122, "DE_": 0x3344, "HL": 0x5566, "HL_": 0x7788, "SP": 0xFFFF}
        cpu = self.cpu("08 D9 EB E3 76", load=0x200, registers=registers, ram={65535: 0xCD, 0: 0xAB}).run()
        self.assertEqual((cpu.registers["AF"], cpu.registers["AF_"], cpu.registers["BC"], cpu.registers["BC_"]),
                         (0x5678, 0x1234, 0xDEF0, 0x9ABC))
        self.assertEqual((cpu.registers["DE"], cpu.registers["HL"], cpu.registers["SP"]), (0x7788, 0xABCD, 0xFFFF))
        self.assertEqual((cpu.read_memory(65535), cpu.read_memory(0)), (0x44, 0x33))

    def test_alu_explicit_edges(self):
        for opcode, a, operand, initial_f, result, flags in (
            (0xC6, 0x7F, 1, 0, 0x80, 0x94),  # signed overflow and half carry
            (0xCE, 0xFF, 0, 1, 0, 0x51),
            (0xD6, 0x80, 1, 0, 0x7F, 0x3E),
            (0xDE, 0, 0, 1, 0xFF, 0xBB),
            (0xE6, 0xAA, 0x55, 0xFF, 0, 0x54),
            (0xEE, 0xFF, 0xAA, 0xFF, 0x55, 4),
            (0xF6, 0x10, 0x20, 0xFF, 0x30, 0x24),
            (0xFE, 0x10, 0x28, 0, 0x10, 0xBB),
        ):
            with self.subTest(opcode=opcode):
                cpu = self.cpu(f"{opcode:02X} {operand:02X}", registers={"A": a, "F": initial_f})
                cpu.step_instruction()
                self.assertEqual((cpu.registers["A"], cpu.registers["F"]), (result, flags))

    def test_add_hl_flags_and_alias(self):
        for opcode, hl, pair, value, expected, flags in (
            (0x09, 0x0FFF, "BC", 1, 0x1000, 0xD4),
            (0x19, 0xFFFF, "DE", 1, 0, 0xD5),
            (0x29, 0x8080, "HL", 0x8080, 0x0100, 0xC5),
            (0x39, 0x2000, "SP", 0x0800, 0x2800, 0xEC),
        ):
            with self.subTest(opcode=opcode):
                cpu = self.cpu(f"{opcode:02X}", registers={"HL": hl, pair: value, "F": 0xC4})
                cpu.step_instruction()
                self.assertEqual((cpu.registers["HL"], cpu.registers["F"]), (expected, flags))

    def test_inc_dec_memory_and_pair_flags(self):
        cpu = self.cpu("34 35 23 2B 76", registers={"HL": 0xFFFF, "F": 1}, ram={65535: 0x7F})
        cpu.step_instruction()
        self.assertEqual((cpu.read_memory(65535), cpu.registers["F"]), (0x80, 0x95))
        cpu.step_instruction()
        self.assertEqual((cpu.read_memory(65535), cpu.registers["F"]), (0x7F, 0x3F))
        cpu.step_instruction()
        self.assertEqual((cpu.registers["HL"], cpu.registers["F"]), (0, 0x3F))
        cpu.run()
        self.assertEqual(cpu.registers["HL"], 0xFFFF)

    def test_all_conditions_taken_and_not_taken(self):
        masks = (0x40, 0x40, 1, 1, 4, 4, 0x80, 0x80)
        for index, mask in enumerate(masks):
            for taken in (False, True):
                flags = mask if taken == bool(index & 1) else 0
                with self.subTest(index=index, taken=taken):
                    cpu = self.cpu(f"{0xC2 + 8 * index:02X} 34 12", registers={"F": flags})
                    cpu.step_instruction()
                    self.assertEqual(cpu.registers["PC"], 0x1234 if taken else 3)
                    cpu = self.cpu(f"{0xC4 + 8 * index:02X} 34 12", registers={"F": flags, "SP": 0x1000})
                    ins = cpu.step_instruction(trace_cycles=True)
                    self.assertEqual((cpu.registers["PC"], cpu.registers["SP"], ins.tstates),
                                     (0x1234, 0xFFE, 17) if taken else (3, 0x1000, 10))
                    self.assertEqual(sum(c.tstates for c in ins.cycles), ins.tstates)
                    cpu = self.cpu(f"{0xC0 + 8 * index:02X}", registers={"F": flags, "SP": 0x1000}, ram={0x1000: 0x34, 0x1001: 0x12})
                    ins = cpu.step_instruction()
                    self.assertEqual((cpu.registers["PC"], cpu.registers["SP"], ins.tstates),
                                     (0x1234, 0x1002, 11) if taken else (1, 0x1000, 5))

    def test_ei_delay_and_di(self):
        cpu = self.cpu("FB 00 F3 76")
        cpu.step_instruction()
        self.assertEqual(tuple(cpu.registers[x] for x in ("IFF1", "IFF2", "EI_DELAY")), (1, 1, 1))
        cpu.step_instruction()
        self.assertEqual(tuple(cpu.registers[x] for x in ("IFF1", "IFF2", "EI_DELAY")), (1, 1, 0))
        cpu.step_instruction()
        self.assertEqual(tuple(cpu.registers[x] for x in ("IFF1", "IFF2", "EI_DELAY")), (0, 0, 0))

    def test_scripted_ports_full_address_and_flags(self):
        cpu = self.cpu("DB FE D3 7F 76", registers={"A": 0xAB, "F": 0xD7},
                       ports=[("r", 0xABFE, 0x52), ("w", 0x527F, 0x52)])
        trace = []
        cpu.run(trace=trace.append, trace_cycles=True)
        self.assertEqual((cpu.registers["A"], cpu.registers["F"]), (0x52, 0xD7))
        self.assertEqual([(p.direction, p.address, p.data) for p in cpu.port_events],
                         [("r", 0xABFE, 0x52), ("w", 0x527F, 0x52)])
        self.assertEqual([(c.kind, c.address, c.tstates) for c in trace[0].cycles],
                         [("m1", 0, 4), ("read", 1, 3), ("io_read", 0xABFE, 4)])
        self.assertEqual(cpu.tstates, 26)

    def test_port_script_errors_are_transition_errors(self):
        for script in ([], [("r", 0x00FE, 0x52)], [("w", 0xABFE, 0xAB)]):
            cpu = self.cpu("DB FE", registers={"A": 0xAB}, ports=script)
            with self.assertRaises(PortScriptError):
                cpu.run()
            self.assertEqual(cpu.machine.state, self.program.io_error)
            self.assertEqual(cpu.instructions, 0)
        cpu = self.cpu("D3 FE", registers={"A": 0xAB}, ports=[("w", 0xABFE, 0xAC)])
        with self.assertRaises(PortScriptError):
            cpu.run()

    def test_resume_after_transition_budget(self):
        cpu = self.cpu("3E 7F C6 01 76")
        baseline = self.cpu("3E 7F C6 01 76").run()
        while not cpu.stopped_on_halt:
            try:
                cpu.run(budget=127)
            except TransitionBudget:
                pass
        self.assertEqual(cpu.machine.tape, baseline.machine.tape)
        self.assertEqual(cpu.machine.transitions, baseline.machine.transitions)
        self.assertEqual((cpu.instructions, cpu.tstates), (3, 18))


if __name__ == "__main__":
    unittest.main()
