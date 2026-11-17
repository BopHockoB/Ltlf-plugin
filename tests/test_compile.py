"""Needs Lydia, which runs in Docker: skipped when Docker is not up."""
import random
import subprocess

import pytest

from ltlf.dfa import DFA


def _docker_up() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _docker_up(), reason="Docker (Lydia) not running")


def test_f_x_table():
    from ltlf.compile import compile_formula

    assert compile_formula("F(x)") == {"alphabet": ["x"], "initial": 0, "accepting": [1], "table": [{"": 0, "x": 1}, {"": 1, "x": 1}]}


@pytest.mark.parametrize("formula", ["F(x)", "F(a & F(b))", "!(G(!x))", "!(G !(a & F(b)))"])
def test_round_trip(formula):
    from logaut import ltl2dfa
    from pylogics.parsers import parse_ltl

    from ltlf.compile import to_table

    original = ltl2dfa(parse_ltl(formula), "lydia")
    table = DFA.from_dict(to_table(original))
    rng = random.Random(0)
    for _ in range(200):
        q, s = original.initial_state, table.initial
        for _ in range(rng.randint(0, 8)):
            labels = {p: rng.random() < 0.5 for p in table.alphabet}
            q, s = original.get_successor(q, labels), table.step(s, labels)
            assert (q in original.accepting_states) == table.is_accepting(s)
