import json
from pathlib import Path
import tempfile
import unittest

from z80tm.layout import POS
from z80tm.spectrum import Spectrum, key_rows, screen_rgb
from z80tm.spectrum_keyboard import basic_keys
from tests.test_compiled_machine import require_compiled_runner


class SpectrumDisplayTests(unittest.TestCase):
    def test_nonlinear_bitmap_bright_flash_and_attributes(self):
        screen = bytearray(6912)
        screen[0x100] = 0x80  # y=1, x=0
        screen[6144] = 0xCA  # bright, flash, red ink, blue paper
        normal, flash = screen_rgb(screen), screen_rgb(screen, True)
        self.assertEqual(normal[:3], bytes((0, 0, 255)))
        self.assertEqual(normal[768:771], bytes((255, 0, 0)))
        self.assertEqual(flash[768:771], bytes((0, 0, 255)))
        self.assertEqual(len(normal), 256 * 192 * 3)

    def test_keyboard_chords_and_basic_source(self):
        self.assertEqual(key_rows(('CAPS', 'A', 'ENTER')), [30, 30, 31, 31, 31, 31, 30, 31])
        keys = basic_keys('10 PRINT 2\nRUN')
        self.assertEqual(keys, [('1',), ('0',), ('SPACE',), ('P',), ('SPACE',), ('2',), ('ENTER',), ('R',), ('ENTER',)])
        self.assertIn(('CAPS', 'H'), basic_keys('PRINT "Hi"'))
        self.assertIn(('CAPS', 'SYMBOL'), basic_keys('PRINT SIN PI'))
        self.assertIn(('G',), basic_keys('20 GOTO 10'))
        self.assertEqual(basic_keys('10PRINT 2'), basic_keys('10 PRINT 2')[:2] + basic_keys('10 PRINT 2')[3:])
        with self.assertRaisesRegex(ValueError, 'Unsupported command'):
            basic_keys('10 DEF FN x(a)=a')


class SpectrumBoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_compiled_runner()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def board(self, code, registers=None, ram=None, peripheral=None):
        path = Path(self.directory.name) / 'test.rom'
        path.write_bytes(bytes.fromhex(code).ljust(16384, b'\0'))
        board = Spectrum(path, registers=registers, ram=ram)
        self.addCleanup(board.close)
        if peripheral:
            # Test fixture initialization, before the first transition.
            tape = board.machine.export_tape()
            tape.update({POS[name]: value for name, value in peripheral.items()})
            board.machine.restore(tape, board.program.entry, 0, 0, 0)
        return board

    def test_rom_protection_ram_and_even_port_decode(self):
        # Write to ROM and RAM, read unselected keyboard rows, output border.
        board = self.board('3E 55 32 00 10 32 00 80 3E FF DB FE D3 FE 00')
        board.run(30_000_000, until_pc=15)
        self.assertEqual(board.registers['PC'], 15)
        self.assertEqual(board.memory(0x1000, 1), b'\0')
        self.assertEqual(board.memory(0x8000, 1), b'\x55')
        self.assertEqual(board.registers['A'], 0xBF)
        self.assertEqual(board.controls()[POS['ULA_OUT']], 0xBF)

    def test_frame_irq_and_keyboard_latch(self):
        # HALT continuously clocks the board. Initialize near the frame boundary.
        board = self.board('76', peripheral={'ULA_FRAME0': 0xFC, 'ULA_FRAME1': 0x10, 'ULA_FRAME2': 1})
        board.queue_key('CAPS', 'A')
        board.run(1_000_000, until_pc=1)
        c = board.controls()
        self.assertEqual(c[POS['ULA_FRAMES']], 1)
        self.assertEqual(c[POS['IRQ']], 1)
        self.assertEqual(c[POS['KEY0']], 30)
        self.assertEqual(c[POS['KEY1']], 30)
        self.assertEqual(c[POS['KEY_HOLD']], 6)
        self.assertEqual(c[POS['KEY_REQUEST']], 0)
        board.run(5_000_000)
        self.assertEqual(board.controls()[POS['IRQ']], 0)

    def test_selected_keyboard_rows_are_anded(self):
        board = self.board('01 FE FC ED 78', peripheral={'KEY0': 30, 'KEY1': 29})
        board.run(5_000_000, until_pc=5)
        self.assertEqual(board.registers['A'], 0xBC)

    def test_frame_releases_a_queued_key(self):
        board = self.board('00', peripheral={'ULA_FRAME0': 252, 'ULA_FRAME1': 16, 'ULA_FRAME2': 1,
                                             'KEY_HOLD': 4, 'KEY0': 30})
        board.run(2_000_000, until_pc=1)
        self.assertEqual(board.controls()[POS['KEY0']], 31)
        self.assertEqual(board.controls()[POS['KEY_HOLD']], 3)

    def test_tape_edges_exact_cycle_boundary_stop_and_rewind(self):
        board = self.board('00 00 00 00')
        path = Path(self.directory.name) / 'edge.tzx'
        path.write_bytes(b'ZXTape!\x1a\x01\x14\x13\x03\x04\0\x04\0\x04\0\x20\0\0')
        board.mount(path)
        board.play()
        for pc, ear in ((1, 1), (2, 0), (3, 0)):
            board.run(2_000_000, until_pc=pc)
            self.assertEqual(board.registers['PC'], pc)
            self.assertEqual(board.controls()[POS['EAR']], ear)
        self.assertEqual(board.controls()[POS['TAPE_STOP']], 1)
        self.assertEqual(board.tape_pulses, 3)
        board.mount(path)
        board.play()
        board.run(2_000_000, until_pc=4)
        self.assertEqual(board.controls()[POS['EAR']], 1)

    def test_checkpoint_resumes_half_instruction_and_external_input(self):
        board = self.board('21 00 80 34 18 FD', ram={0x8000: 7})
        media = Path(self.directory.name) / 'checkpoint.tzx'
        media.write_bytes(b'ZXTape!\x1a\x01\x14\x12\x09\0\x64\0')
        board.mount(media)
        board.play()
        board.queue_key('P')
        board.run(123456)
        path = Path(self.directory.name) / 'state.json'
        board.save_checkpoint(path)
        board.run(987654)
        expected = (board.machine.state, board.machine.head, board.machine.transitions,
                    board.machine.head_moves, board.machine.export_tape())
        board.load_checkpoint(path)
        board.run(987654)
        self.assertEqual((board.machine.state, board.machine.head, board.machine.transitions,
                          board.machine.head_moves, board.machine.export_tape()), expected)
        data = json.loads(path.read_text())
        data['rom'] = 'wrong'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            board.load_checkpoint(path)

    def test_paused_cassette_preserves_remaining_pulse(self):
        board = self.board('00 00 00 00')
        media = Path(self.directory.name) / 'pause.tzx'
        media.write_bytes(b'ZXTape!\x1a\x01\x14\x12\x0a\0\x02\0')
        board.mount(media)
        board.play()
        board.run(2_000_000, until_pc=1)
        self.assertEqual(board.controls()[POS['PULSE0']], 6)
        board.pause_tape()
        board.run(2_000_000, until_pc=2)
        self.assertEqual(board.controls()[POS['PULSE0']], 6)
        board.play()
        board.run(2_000_000, until_pc=3)
        self.assertEqual(board.controls()[POS['PULSE0']], 2)
        board.run(2_000_000, until_pc=4)
        self.assertEqual(board.controls()[POS['PULSE0']], 8)
        self.assertEqual(board.controls()[POS['EAR']], 1)

    def test_rewind_during_input_request_keeps_the_first_new_pulse(self):
        board = self.board('00 00')
        media = Path(self.directory.name) / 'mid-cycle.tzx'
        media.write_bytes(b'ZXTape!\x1a\x01\x14\x12\x0a\0\x02\0')
        board.mount(media)
        board.play()
        board.machine.run_until({board.program.pulse_request}, 2_000_000)
        board.mount(media)
        board.play()
        board.run(2_000_000, until_pc=1)
        self.assertEqual(board.tape_records, 0)
        board.run(2_000_000, until_pc=2)
        self.assertEqual(board.tape_records, 1)
        self.assertEqual(board.controls()[POS['PULSE0']], 6)

    def test_host_mailbox_cannot_write_guest_state(self):
        board = self.board('00')
        with self.assertRaisesRegex(ValueError, 'external input'):
            board._input({'PCL': 1})
