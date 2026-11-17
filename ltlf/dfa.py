"""Compiled DFA as a full table. Standard library only: this is on the hook path.

A letter is the set of propositions that are true at a step; every other proposition is false.
Each table row maps every possible letter (every subset of the alphabet) to the next state.
In the cache JSON a letter is written as its propositions sorted and comma-joined, "" for none:
    {"": 0, "a": 1, "b": 0, "a,b": 2}
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations


def all_letters(alphabet: Iterable[str]) -> list[frozenset[str]]:
    """Every subset of `alphabet`, smallest first: 2**n letters for n propositions."""
    props = sorted(alphabet)
    return [frozenset(c) for size in range(len(props) + 1) for c in combinations(props, size)]


def letter_key(letter: Iterable[str]) -> str:
    """How a letter is written in the cache JSON: sorted, comma-joined, "" when nothing is true."""
    return ",".join(sorted(letter))


@dataclass(frozen=True)
class DFA:
    """Complete DFA: table[state][letter] is the next state, for every state and every letter."""

    alphabet: tuple[str, ...]
    initial: int
    accepting: frozenset[int]
    table: tuple[dict[frozenset[str], int], ...]

    def letter(self, labels: Mapping[str, bool]) -> frozenset[str]:
        """The alphabet propositions that are true in `labels`. Missing ones are false; others are ignored."""
        return frozenset(p for p in self.alphabet if labels.get(p))

    def step(self, state: int, labels: Mapping[str, bool]) -> int:
        """Successor of `state` on this label vector. Pure."""
        return self.table[state][self.letter(labels)]

    def is_accepting(self, state: int) -> bool:
        """True iff `state` is in the accepting set."""
        return state in self.accepting

    @classmethod
    def from_dict(cls, d: Mapping) -> "DFA":
        """Build from the cache form {alphabet, initial, accepting, table}; reject malformed tables."""
        alphabet = tuple(d["alphabet"])
        if len(set(alphabet)) != len(alphabet):
            raise ValueError("duplicate proposition in alphabet")
        by_key = {letter_key(letter): letter for letter in all_letters(alphabet)}
        rows = d["table"]
        if not rows or any(set(row) != set(by_key) for row in rows):
            raise ValueError(f"every table row needs exactly one entry per letter: {sorted(by_key)}")
        n = len(rows)
        if any(not 0 <= t < n for row in rows for t in row.values()):
            raise ValueError("transition target out of range")
        accepting = frozenset(d["accepting"])
        if not 0 <= d["initial"] < n or any(not 0 <= s < n for s in accepting):
            raise ValueError("initial or accepting state out of range")
        table = tuple({by_key[k]: t for k, t in row.items()} for row in rows)
        return cls(alphabet, d["initial"], accepting, table)
