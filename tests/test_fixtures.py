"""Offline independent JSMoo vectors, including undocumented state and flags."""
import json
from pathlib import Path
import unittest

from tools.check_vectors import check_vector
from z80tm.compiler import build_cpu

FIXTURES = Path(__file__).parent / "fixtures"
COMMIT = "ebe1875d48f374bcfd4b505d8eb8ee751568b5f7"


class IndependentFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = build_cpu()

    def check_fixture_file(self, filename, prefix=""):
        data = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        self.assertEqual(data["commit"], COMMIT)
        for family in data["fixtures"]:
            if not family["opcode"].startswith(prefix):
                continue
            for vector in family["tests"]:
                with self.subTest(vector=vector["name"]):
                    self.assertEqual(check_vector(self.program, family, vector), {})

    def test_pinned_unprefixed_vectors(self):
        self.check_fixture_file("singlestep.json")

    def test_pinned_cb_vectors(self):
        self.check_fixture_file("prefixes.json", "cb ")

    def test_pinned_ed_vectors(self):
        self.check_fixture_file("prefixes.json", "ed ")

    def test_pinned_dd_vectors(self):
        self.check_fixture_file("prefixes.json", "dd ")

    def test_pinned_fd_vectors(self):
        self.check_fixture_file("prefixes.json", "fd ")

    def test_fixture_encoding_coverage(self):
        base = json.loads((FIXTURES / "singlestep.json").read_text(encoding="utf-8"))["fixtures"]
        prefixes = json.loads((FIXTURES / "prefixes.json").read_text(encoding="utf-8"))["fixtures"]
        self.assertEqual({int(f["opcode"], 16) for f in base}, set(self.program.coverage))
        self.assertEqual(sum(len(f["tests"]) for f in base), 520)
        self.assertEqual(len(prefixes), 1352)
        self.assertEqual(sum(len(f["tests"]) for f in prefixes), 1352)
        # Assert the pinned selection is complete, independently of the decoder.
        index = json.loads((FIXTURES.parents[1] / "tools" / "fixture-index.json").read_text(encoding="utf-8"))
        expected = {path[3:-5]: blob for path, blob in index.items() if " " in path}
        self.assertEqual({f["opcode"]: f["blob"] for f in prefixes}, expected)


if __name__ == "__main__":
    unittest.main()
