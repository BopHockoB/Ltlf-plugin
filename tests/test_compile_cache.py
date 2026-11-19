"""Incremental compile: unchanged specs reuse their cache entry. No Docker: Lydia is replaced by a counter."""
import json

import pytest

import ltlf.compile as comp

F_X = {"alphabet": ["x"], "initial": 0, "accepting": [1], "table": [{"": 0, "x": 1}, {"": 1, "x": 1}]}


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(comp, "compile_formula", lambda f: seen.append(f) or dict(F_X))
    return seen


def write_spec(d, name, formula="F(x)"):
    (d / f"{name}.yaml").write_text(f"name: {name}\nclass: goal\nformula: '{formula}'\n", encoding="utf-8")


def test_incremental(tmp_path, calls, monkeypatch, capsys):
    specs, out = tmp_path / "specs", tmp_path / "cache" / "index.json"
    specs.mkdir()
    write_spec(specs, "a")
    write_spec(specs, "b")

    assert comp.main(specs, out) == 0 and len(calls) == 2
    assert comp.main(specs, out) == 0 and len(calls) == 2  # nothing changed: no Lydia calls
    assert capsys.readouterr().out.count("reused") == 2

    write_spec(specs, "a", "F(x) | F(x)")
    assert comp.main(specs, out) == 0 and calls[2:] == ["F(x) | F(x)"]  # only the edited spec

    (specs / "b.yaml").unlink()
    assert comp.main(specs, out) == 0
    assert list(json.loads(out.read_text())) == ["a"]  # deleted spec dropped

    monkeypatch.setattr(comp, "COMPILER_VERSION", comp.COMPILER_VERSION + 1)
    assert comp.main(specs, out) == 0 and len(calls) == 4  # new compiler: recompile


def test_unreadable_old_cache_recompiles(tmp_path, calls):
    specs, out = tmp_path / "specs", tmp_path / "index.json"
    specs.mkdir()
    write_spec(specs, "a")
    out.write_text("{half", encoding="utf-8")
    assert comp.main(specs, out) == 0 and len(calls) == 1
    assert json.loads(out.read_text())["a"]["compiler_version"] == comp.COMPILER_VERSION


def test_out_may_be_the_cache_folder(tmp_path, calls):
    specs, cache = tmp_path / "specs", tmp_path / "cache"
    specs.mkdir()
    cache.mkdir()
    write_spec(specs, "a")
    assert comp.main(specs, cache) == 0 and "a" in json.loads((cache / "index.json").read_text())
