import pytest

from ltlf.dfa import DFA, all_letters, letter_key

F_A_THEN_B = {  # F(a & F(b))
    "alphabet": ["a", "b"], "initial": 0, "accepting": [2],
    "table": [{"": 0, "a": 1, "b": 0, "a,b": 2}, {"": 1, "a": 1, "b": 2, "a,b": 2}, {"": 2, "a": 2, "b": 2, "a,b": 2}],
}


def test_all_letters_are_every_subset_once():
    assert [letter_key(x) for x in all_letters(["b", "a"])] == ["", "a", "b", "a,b"]
    assert len(set(all_letters("abcd"))) == 16


def test_letter_ignores_missing_false_and_unknown():
    dfa = DFA.from_dict(F_A_THEN_B)
    assert dfa.letter({"a": True, "b": False, "z": True}) == {"a"}
    assert dfa.letter({}) == frozenset()


def test_step():
    dfa = DFA.from_dict(F_A_THEN_B)
    assert dfa.step(1, {"b": True}) == 2
    assert dfa.step(1, {"a": True}) == 1


def test_missing_letter_rejected():
    bad = {**F_A_THEN_B, "table": [{"": 0, "a": 1, "b": 0}, *F_A_THEN_B["table"][1:]]}
    with pytest.raises(ValueError, match="one entry per letter"):
        DFA.from_dict(bad)
