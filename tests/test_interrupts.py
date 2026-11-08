import unittest

from z80tm.compiler import build_cpu
from z80tm.harness import CPU, InterruptScriptError


class InterruptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = build_cpu()

    def cpu(self, code="00", **kwargs):
        return CPU(bytes.fromhex(code), program=self.program, **kwargs)

    def test_ei_defers_int_one_complete_prefixed_instruction(self):
        cpu = self.cpu("FB DD 21 34 12 76", registers={"IM": 1, "SP": 0x1000}, interrupts=[(0, "int", 0xFF)])
        cpu.step_instruction()
        ins = cpu.step_instruction()
        self.assertEqual((ins.kind, ins.tstates, cpu.registers["IX"]), ("instruction", 14, 0x1234))
        irq = cpu.step_instruction()
        self.assertEqual((irq.kind, irq.tstates, cpu.registers["PC"], cpu.registers["SP"]), ("int", 13, 0x38, 0xFFE))
        self.assertEqual((cpu.read_memory(0xFFE), cpu.read_memory(0xFFF)), (5, 0))
        self.assertEqual((cpu.instructions, cpu.interrupts, cpu.registers["CLOCK"]), (2, 1, 31))

    def test_halt_waits_then_recovers_to_resume_pc(self):
        cpu = self.cpu("76 00", registers={"IFF1": 1, "IFF2": 1, "IM": 1, "SP": 0},
                       interrupts=[(12, "int", 0xFF)])
        cpu.run()
        self.assertEqual((cpu.instructions, cpu.registers["PC"]), (1, 1))
        cpu.run(stop_on_halt=False, max_actions=3, budget=40_000_000)
        self.assertFalse(cpu.halted)
        self.assertEqual((cpu.halt_cycles, cpu.interrupts, cpu.registers["PC"], cpu.registers["CLOCK"]), (2, 1, 0x38, 25))
        self.assertEqual((cpu.read_memory(65534), cpu.read_memory(65535)), (1, 0))
        self.assertEqual(cpu.registers["R"], 4)

    def test_im2_odd_vector_wraps_and_push_precedes_vector_read(self):
        cpu = self.cpu(registers={"PC": 0x1234, "SP": 0x9000, "IFF1": 1, "IFF2": 1, "IM": 2, "I": 0xFF, "IRQ": 1, "VECTOR": 0xFF},
                       ram={65535: 0x78, 0: 0x56})
        irq = cpu.step_instruction(trace_cycles=True)
        self.assertEqual((irq.tstates, cpu.registers["PC"], cpu.registers["WZ"]), (19, 0x5678, 0x5678))
        self.assertEqual([(c.kind, c.address, c.tstates) for c in irq.cycles],
                         [("int_ack", 0x1234, 7), ("write", 0x8FFF, 3), ("write", 0x8FFE, 3), ("read", 65535, 3), ("read", 0, 3)])

    def test_nmi_priority_iff2_and_retn(self):
        cpu = self.cpu(registers={"PC": 0x2000, "SP": 1, "IFF1": 1, "IFF2": 1, "IRQ": 1, "NMI": 1, "IM": 1},
                       ram={0x66: 0xED, 0x67: 0x45, 0x2000: 0})
        nmi = cpu.step_instruction()
        self.assertEqual((nmi.kind, nmi.tstates, cpu.registers["PC"], cpu.registers["IFF1"], cpu.registers["IFF2"]),
                         ("nmi", 11, 0x66, 0, 1))
        ret = cpu.step_instruction()
        self.assertEqual((ret.tstates, cpu.registers["PC"], cpu.registers["IFF1"], cpu.registers["SP"]), (14, 0x2000, 1, 1))
        self.assertEqual(cpu.step_instruction().kind, "instruction")
        self.assertEqual(cpu.step_instruction().kind, "int")

    def test_nested_nmi_requires_an_intervening_instruction(self):
        cpu = self.cpu(registers={"NMI": 1, "IFF2": 1, "SP": 0x9000}, interrupts=[(5, "nmi", 0)], ram={0x66: 0})
        self.assertEqual(cpu.step_instruction().kind, "nmi")
        self.assertEqual(cpu.step_instruction().kind, "instruction")
        self.assertEqual(cpu.step_instruction().kind, "nmi")
        self.assertEqual(cpu.registers["IFF2"], 1)

    def test_irq_level_clear_before_boundary_is_not_accepted(self):
        cpu = self.cpu("21 00 40 00", registers={"IFF1": 1, "IM": 1}, interrupts=[(1, "int", 255), (9, "clear", 0)])
        cpu.step_instruction()
        self.assertEqual(cpu.step_instruction().kind, "instruction")
        self.assertEqual(cpu.interrupts, 0)

    def test_im0_rst_and_multibyte_call(self):
        cpu = self.cpu(registers={"PC": 0x1234, "SP": 0x1000, "IFF1": 1, "IM": 0, "IRQ": 1, "VECTOR": 0xCF})
        ins = cpu.step_instruction()
        self.assertEqual((ins.kind, ins.tstates, cpu.registers["PC"]), ("im0", 13, 8))
        self.assertEqual((cpu.read_memory(0xFFE), cpu.read_memory(0xFFF)), (0x34, 0x12))
        cpu = self.cpu(registers={"PC": 0x1234, "SP": 0x1000, "IFF1": 1, "IRQ": 1, "VECTOR": 0xCD}, interrupt_data=bytes.fromhex("78 56"))
        ins = cpu.step_instruction()
        self.assertEqual((ins.tstates, cpu.registers["PC"]), (19, 0x5678))
        self.assertEqual((cpu.read_memory(0xFFE), cpu.read_memory(0xFFF)), (0x34, 0x12))

    def test_im0_prefix_stream_refresh_and_no_pc_advance(self):
        cpu = self.cpu(registers={"PC": 0x2345, "IFF1": 1, "IRQ": 1, "VECTOR": 0xDD, "R": 0xFE},
                       interrupt_data=bytes.fromhex("FD 21 34 12"))
        ins = cpu.step_instruction(trace_cycles=True)
        self.assertEqual((ins.tstates, cpu.registers["PC"], cpu.registers["IY"], cpu.registers["R"]), (24, 0x2345, 0x1234, 0x81))
        self.assertEqual([c.kind for c in ins.cycles], ["int_ack", "int_ack", "int_ack", "int_operand", "int_operand"])
        cpu = self.cpu(registers={"IFF1": 1, "IRQ": 1, "VECTOR": 0x3E})
        with self.assertRaises(InterruptScriptError):
            cpu.step_instruction()

    def test_ld_a_i_nmos_pv_interrupt_quirk(self):
        cpu = self.cpu("ED 57 00", registers={"I": 1, "IFF1": 1, "IFF2": 1, "IM": 1, "SP": 0x1000}, interrupts=[(9, "int", 0xFF)])
        cpu.step_instruction()
        self.assertEqual(cpu.registers["F"] & 4, 4)
        cpu.step_instruction()
        self.assertEqual(cpu.registers["F"] & 4, 0)

    def test_reti_notification_and_refresh_trace(self):
        cpu = self.cpu("ED 4D", registers={"SP": 0x1000, "I": 0xAB, "R": 0xFF}, ram={0x1000: 0x34, 0x1001: 0x12})
        ins = cpu.step_instruction(trace_cycles=True)
        self.assertTrue(ins.reti)
        self.assertEqual(cpu.reti_events, [14])
        self.assertEqual([e.refresh_address for e in ins.cycles[:2]], [0xAB80, 0xAB81])

    def test_clock_carry_and_absolute_script_time(self):
        cpu = self.cpu("00 00", clock=0xFFFFFFFC, registers={"IFF1": 1, "IM": 1}, interrupts=[(0x100000000, "int", 255)])
        cpu.step_instruction()
        self.assertEqual(cpu.registers["CLOCK"], 0x100000000)
        self.assertEqual(cpu.step_instruction().kind, "int")
        self.assertEqual(cpu.registers["CLOCK"], 0x10000000D)
