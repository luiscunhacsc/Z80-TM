import struct
import unittest
import zlib

from z80tm.tape import TapeError, TapeImage

HEADER = b'ZXTape!\x1a\x01\x14'


class TapeTests(unittest.TestCase):
    def tzx(self, blocks):
        return list(TapeImage.parse(HEADER + blocks, '.tzx').pulses())

    def test_tap_and_tzx_standard_waveforms_match(self):
        data = b'\x00\xa5\xa5'
        tap = list(TapeImage.parse(struct.pack('<H', len(data)) + data).pulses())
        self.assertEqual(tap, self.tzx(b'\x10' + struct.pack('<HH', 1000, len(data)) + data))
        self.assertEqual(tap[:3], [(2168, 0), (2168, 1), (2168, 0)])
        self.assertEqual(tap[8063:8067], [(667, 1), (735, 0), (855, 1), (855, 0)])
        self.assertEqual(tap[-2:], [(3500, 1), (3496500, 0)])
        self.assertEqual(len(tap), 8063 + 2 + 48 + 2)

    def test_turbo_pure_data_partial_byte_and_direct(self):
        turbo = b'\x11' + struct.pack('<6HBH', 10, 20, 30, 40, 50, 2, 2, 0) + b'\x01\0\0\x80'
        self.assertEqual(self.tzx(turbo), [(10, 0), (10, 1), (20, 0), (30, 1), (50, 0), (50, 1), (40, 0), (40, 1)])
        pure = b'\x14' + struct.pack('<HHBH', 7, 9, 1, 0) + b'\x01\0\0\x80'
        self.assertEqual(self.tzx(pure), [(9, 0), (9, 1)])
        direct = b'\x15' + struct.pack('<HHB', 10, 0, 5) + b'\x01\0\0\xc8'
        self.assertEqual(self.tzx(direct), [(20, 1), (20, 0), (10, 1)])

    def test_control_flow_metadata_stop_and_level(self):
        tone = b'\x12' + struct.pack('<HH', 12, 1)
        blocks = b'\x21\x01x\x24\x02\0' + tone + b'\x25\x22\x20\0\0\x2b\x01\0\0\0\x01' + tone
        self.assertEqual(self.tzx(blocks), [(12, 0), (12, 1), None, (12, 1)])
        # Call block 2, return to block 1, then jump to EOF.
        self.assertEqual(self.tzx(b'\x26\x01\0\x02\0\x23\x03\0' + tone + b'\x27'), [(12, 0)])

    def test_csw_compressed_uncompressed_and_last_level(self):
        for compression in (1, 2):
            payload = b'\x01\0' + struct.pack('<I', 2)
            if compression == 2:
                payload = zlib.compress(payload)
            data = struct.pack('<H', 0) + (350000).to_bytes(3, 'little') + bytes([compression]) + struct.pack('<I', 2) + payload
            tone = b'\x12' + struct.pack('<HH', 5, 1)
            self.assertEqual(self.tzx(b'\x18' + struct.pack('<I', len(data)) + data + tone), [(10, 0), (20, 1), (5, 1)])

    def test_generalized_symbols_and_polarity(self):
        # No pilot; four data symbols, 2 pulses/symbol, alphabet of two.
        data = struct.pack('<HIBBIBB', 0, 0, 0, 0, 4, 2, 2)
        data += struct.pack('<BHHBHH', 2, 10, 20, 3, 30, 0) + b'\x60'
        self.assertEqual(self.tzx(b'\x19' + struct.pack('<I', len(data)) + data),
                         [(10, 0), (20, 1), (30, 1), (30, 1), (10, 0), (20, 1)])

    def test_malformed_files_fail_clearly(self):
        for data, suffix in ((b'', '.tap'), (b'\x03\0\x00', '.tap'), (b'\x01\0\x00', '.tap'),
                             (HEADER + b'\x10\0', '.tzx'), (HEADER + b'\x28', '.tzx'), (HEADER + b'\xff', '.tzx')):
            with self.subTest(data=data), self.assertRaises(TapeError):
                TapeImage.parse(data, suffix)
        for blocks in (b'\x25', b'\x27', b'\x23\xff\xff', b'\x12\0\0\x01\0'):
            with self.subTest(blocks=blocks), self.assertRaises(TapeError):
                self.tzx(blocks)

    def test_generalized_empty_symbol_expansion_is_bounded(self):
        # A tiny file must not spend billions of iterations on empty symbols.
        data = struct.pack('<HIBBIBB', 0, 257, 0, 1, 0, 0, 0) + b'\0' + b'\0\xff\xff' * 257
        with self.assertRaisesRegex(TapeError, 'expanded symbol limit'):
            self.tzx(b'\x19' + struct.pack('<I', len(data)) + data)
