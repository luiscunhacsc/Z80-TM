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

    def test_coverage_includes_prefix_families(self):
        result, output = self.invoke(["coverage", "--json"])
        self.assertEqual(result, 0)
        families = json.loads(output)
        self.assertEqual({name: len(entries) for name, entries in families.items()},
                         {"unprefixed": 252, "CB": 256, "ED": 256, "DD": 252,
                          "FD": 252, "DDCB": 256, "FDCB": 256})
        result, output = self.invoke(["coverage", "--family", "ED", "--json"])
        self.assertEqual(list(json.loads(output)), ["ED"])
        self.assertEqual(json.loads(output)["ED"]["00"], "NOP (undefined ED)")

    def test_scripted_interrupt_recovers_from_halt(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "halt.bin"
            binary.write_bytes(bytes.fromhex("76"))
            script = Path(directory) / "interrupts.json"
            script.write_text('[[268, "int", 255]]', encoding="utf-8")
            result, output = self.invoke(["run", str(binary), "--interrupts", str(script),
                                         "--clock", "0x100", "--register", "IFF1=1", "--register", "IM=1",
                                         "--register", "SP=0x1000", "--continue-halt", "--actions", "4", "--json"])
        self.assertEqual(result, 0)
        report = json.loads(output)
        self.assertEqual((report["stop"], report["guest_instructions"], report["interrupts"], report["halt_cycles"]),
                         ("action limit", 1, 1, 2))
        self.assertEqual((report["registers"]["PC"], report["registers"]["CLOCK"], report["guest_tstates"]),
                         (0x38, 281, 25))

    def test_im0_response_file_and_exhaustion(self):
        with tempfile.TemporaryDirectory() as directory:
            response = Path(directory) / "response.bin"
            response.write_bytes(bytes([0xA5]))
            args = ["demo", "--register", "IFF1=1", "--register", "IRQ=1", "--register", "VECTOR=0x3e",
                    "--actions", "1", "--json"]
            result, output = self.invoke(args + ["--interrupt-data", str(response)])
        self.assertEqual(result, 0)
        report = json.loads(output)
        self.assertEqual((report["registers"]["A"], report["registers"]["PC"], report["interrupts"],
                          report["guest_instructions"], report["guest_tstates"]), (0xA5, 0, 1, 1, 9))
        result, output = self.invoke(args)
        self.assertEqual(result, 4)
        self.assertEqual(json.loads(output)["stop"], "IM0 response script exhausted")

    def test_continue_halt_stops_on_action_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "halt.bin"
            binary.write_bytes(bytes.fromhex("76"))
            result, output = self.invoke(["run", str(binary), "--continue-halt", "--actions", "2", "--json"])
        self.assertEqual(result, 0)
        self.assertEqual((json.loads(output)["stop"], json.loads(output)["halt_cycles"]), ("action limit", 1))

    def test_invalid_action_limit_is_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            self.invoke(["demo", "--actions", "-1"])
        self.assertEqual(raised.exception.code, 2)
