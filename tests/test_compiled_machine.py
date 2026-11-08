from pathlib import Path
import tempfile
import unittest
import shutil

from z80tm.compiled_machine import CompiledMachine, runner_executable, write_table
from z80tm.machine import Builder, Machine, MissingRule, Rule, TransitionBudget


def require_compiled_runner():
    try:
        command = runner_executable()
    except RuntimeError as exc:
        raise unittest.SkipTest(str(exc)) from exc
    if not shutil.which(command[0]):
        raise unittest.SkipTest('The optional compiled TM runtime is unavailable')


class CompiledMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_compiled_runner()

    def test_literal_equivalence_sparse_dense_and_leftward_growth(self):
        b = Builder(range(300))
        b.emit('left', {0: Rule(0, 1, -1), 299: Rule(1, 2, 1)})
        b.emit('right', {i: Rule(1 if i else 2, (i + 1) % 256, 1) for i in range(256)})
        b.emit('end')
        table = b.freeze()
        tape = {-2048: 299}
        reference = Machine(table, tape, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'table.bin'
            write_table(table, path)
            with CompiledMachine(path, tape, 0) as compiled:
                for budget in (0, 17, 2000, 100, 2000):
                    for machine in (reference, compiled):
                        try:
                            machine.run_until({2}, budget)
                        except TransitionBudget:
                            pass
                    self.assertEqual((compiled.state, compiled.head, compiled.transitions, compiled.head_moves),
                                     (reference.state, reference.head, reference.transitions, reference.head_moves))
                    self.assertEqual(compiled.export_tape(), {k: v for k, v in reference.tape.items() if v})
                for machine in (reference, compiled):
                    with self.assertRaises(MissingRule):
                        machine.run_until(set(), 1)
