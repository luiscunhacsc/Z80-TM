"""Cross-check real board rules against the Python single-cell reference."""
from pathlib import Path
import tempfile
import unittest

from z80tm.layout import POS
from z80tm.spectrum import Spectrum
from tests.test_compiled_machine import require_compiled_runner


class SpectrumEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_compiled_runner()

    def test_partial_transitions_peripherals_and_memory_match_python(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.rom'
            path.write_bytes(bytes.fromhex('00 3E 55 32 00 80 DB FE D3 FE 18 FE').ljust(16384, b'\0'))
            media = Path(directory) / 'test.tzx'
            media.write_bytes(b'ZXTape!\x1a\x01\x14\x12\x04\0\x08\0')
            with Spectrum(path, backend='python') as reference, Spectrum(path) as compiled:
                # Initialize both boards at the same frame edge before running.
                for name, value in {'ULA_FRAME0': 252, 'ULA_FRAME1': 16, 'ULA_FRAME2': 1}.items():
                    reference.machine.tape[POS[name]] = value
                compiled.machine.restore(reference.machine.tape, reference.machine.state, 0, 0, 0)
                for board in (reference, compiled):
                    board.mount(media)
                    board.play()
                    board.queue_key('CAPS', 'A')
                for budget in (1, 123, 50000, 100000, 500000, 1000000):
                    for board in (reference, compiled):
                        board.run(budget)
                    actual, expected = compiled.machine, reference.machine
                    self.assertEqual((actual.state, actual.head, actual.transitions, actual.head_moves),
                                     (expected.state, expected.head, expected.transitions, expected.head_moves))
                    self.assertEqual(actual.export_tape(), {k: v for k, v in expected.tape.items() if v})
                    self.assertEqual(compiled.tape_records, reference.tape_records)
