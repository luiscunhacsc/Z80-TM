import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from z80tm.__main__ import main


class CLITests(unittest.TestCase):
    def invoke(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(args)
        return result, output.getvalue()

    def test_raw_binary_load_address_and_peek(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "raw.bin"
            binary.write_bytes(bytes.fromhex("3E A5 21 34 12 77 76"))
            result, output = self.invoke(["run", str(binary), "--load", "0x20ff", "--peek", "0x1234", "--json"])
        self.assertEqual(result, 0)
        report = json.loads(output)
        self.assertEqual((report["stop"], report["guest_instructions"], report["guest_tstates"]), ("halt", 4, 28))
        self.assertEqual(report["memory"]["1234"], 0xA5)
        self.assertFalse(report["detailed_tracing"])

    def test_budget_exit_and_instruction_stepping(self):
        result, output = self.invoke(["demo", "--budget", "1", "--json"])
        self.assertEqual(result, 3)
        self.assertEqual(json.loads(output)["tm_transitions"], 1)
        result, output = self.invoke(["demo", "--instructions", "1", "--json"])
        self.assertEqual(result, 0)
        report = json.loads(output)
        self.assertEqual((report["stop"], report["guest_instructions"], report["registers"]["PC"]), ("instruction limit", 1, 3))

    def test_undefined_ed_is_nmos_nop(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "prefix.bin"
            binary.write_bytes(bytes.fromhex("ED 00 76"))
            result, output = self.invoke(["run", str(binary), "--json"])
        self.assertEqual(result, 0)
        self.assertEqual((json.loads(output)["stop"], json.loads(output)["guest_tstates"]), ("halt", 12))
