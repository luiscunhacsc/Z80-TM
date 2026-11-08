"""CPU-agnostic deterministic, two-way-infinite, single-tape machine.

None in a rule's write field is a compact identity-write notation. Expanding
it for each member of the finite alphabet gives ordinary TM triples.
No rule is generated or specialized while this machine executes.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Rule:
    state: int
    write: int | None
    move: int


@dataclass(frozen=True, slots=True)
class Row:
    default: Rule | None
    exceptions: Mapping[int, Rule]


@dataclass(frozen=True, slots=True)
class Table:
    rows: tuple[Row, ...]
    alphabet: frozenset[int]
    names: tuple[str, ...]

    def expanded_rule(self, state: int, symbol: int) -> tuple[int, int, int] | None:
        if symbol not in self.alphabet:
            raise ValueError("Symbol outside the finite alphabet")
        row = self.rows[state]
        rule = row.exceptions.get(symbol, row.default)
        if rule is None:
            return None
        return rule.state, symbol if rule.write is None else rule.write, rule.move

    def validate(self) -> None:
        for row in self.rows:
            if not row.exceptions.keys() <= self.alphabet:
                raise ValueError("Rule reads outside alphabet")
            for rule in (*row.exceptions.values(), row.default):
                if rule is not None and (
                    not 0 <= rule.state < len(self.rows)
                    or rule.move not in (-1, 0, 1)
                    or (rule.write is not None and rule.write not in self.alphabet)
                ):
                    raise ValueError(f"Invalid transition: {rule}")


class MissingRule(RuntimeError):
    pass


class TransitionBudget(RuntimeError):
    pass


class Machine:
    def __init__(self, table: Table, tape: Mapping[int, int], state: int, head: int = 0):
        if any(symbol not in table.alphabet for symbol in tape.values()):
            raise ValueError("Initial tape contains a symbol outside the alphabet")
        self.table = table
        self.tape = dict(tape)
        self.state = state
        self.head = head
        self.transitions = 0
        self.head_moves = 0

    def step(self) -> None:
        symbol = self.tape.get(self.head, 0)
        row = self.table.rows[self.state]
        rule = row.exceptions.get(symbol, row.default)
        if rule is None:
            raise MissingRule(f"No transition: state={self.state}, symbol={symbol}, head={self.head}")
        self.tape[self.head] = symbol if rule.write is None else rule.write
        self.head += rule.move
        self.state = rule.state
        self.transitions += 1
        self.head_moves += abs(rule.move)

    def run_until(self, stops: frozenset[int], budget: int, observer=None) -> None:
        """Execute at least one transition; budgets count actual transitions.

        observer may inspect the machine for tracing. CPU execution uses no
        observer. The loop performs precisely the same step in all modes.
        """
        if budget < 0:
            raise ValueError("Negative transition budget")
        limit = self.transitions + budget
        while self.transitions < limit:
            self.step()
            if observer is not None:
                observer(self)
            if self.state in stops:
                return
        raise TransitionBudget(f"Transition budget exhausted at {self.transitions:,} transitions")


class Builder:
    """Generation-only helper. Freeze discards all mutable rule dictionaries."""
    def __init__(self, alphabet):
        self.alphabet = set(alphabet)
        self.rows: list[Row | None] = []
        self.names: list[str] = []

    def reserve(self, name: str) -> int:
        state = len(self.rows)
        self.rows.append(None)
        self.names.append(name)
        return state

    def define(self, state: int, exceptions=None, default=None) -> int:
        if self.rows[state] is not None:
            raise ValueError(f"State already defined: {self.names[state]}")
        self.rows[state] = Row(default, MappingProxyType(dict(exceptions or {})))
        return state

    def emit(self, name: str, exceptions=None, default=None) -> int:
        return self.define(self.reserve(name), exceptions, default)

    def freeze(self) -> Table:
        if any(row is None for row in self.rows):
            raise ValueError("Undefined transition state")
        table = Table(tuple(self.rows), frozenset(self.alphabet), tuple(self.names))
        table.validate()
        return table

