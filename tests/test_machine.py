import unittest

from z80tm.machine import Builder, Machine, MissingRule, Rule, TransitionBudget


class MachineTests(unittest.TestCase):
    def test_literal_single_cell_transitions_and_blank_left(self):
        b = Builder((0, 1, 2))
        b.emit("start", {1: Rule(1, 2, -1)})
        b.emit("left", {0: Rule(2, 1, 1)})
        b.emit("end")
        table = b.freeze()
        m = Machine(table, {0: 1}, 0)
        m.step()
        self.assertEqual((m.state, m.head, m.tape), (1, -1, {0: 2}))
        m.step()
        self.assertEqual((m.state, m.head, m.tape), (2, 0, {0: 2, -1: 1}))
        self.assertEqual((m.transitions, m.head_moves), (2, 2))
        with self.assertRaises(MissingRule):
            m.step()

    def test_identity_rule_expansion_and_zero_budget(self):
        b = Builder((0, 1))
        b.emit("loop", default=Rule(0, None, 0))
        table = b.freeze()
        self.assertEqual(table.expanded_rule(0, 1), (0, 1, 0))
        m = Machine(table, {0: 1}, 0)
        with self.assertRaises(TransitionBudget):
            m.run_until(frozenset(), 0)
        self.assertEqual(m.transitions, 0)
        with self.assertRaises(TransitionBudget):
            m.run_until(frozenset(), 5)
        self.assertEqual((m.transitions, m.head_moves, m.tape[0]), (5, 0, 1))

    def test_table_is_immutable_and_validated(self):
        b = Builder((0,))
        b.emit("invalid", {0: Rule(0, 0, 2)})
        with self.assertRaises(ValueError):
            b.freeze()
        b = Builder((0,))
        b.emit("ok", {0: Rule(0, 0, 1)})
        table = b.freeze()
        with self.assertRaises(TypeError):
            table.rows[0].exceptions[0] = Rule(0, 0, 0)
