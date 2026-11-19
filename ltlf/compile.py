"""Compile spec YAML files to the DFA table cache. The only module that imports logaut, pylogics or sympy.

Needs Lydia (lydia.bat -> Docker). Metadata is copied, not validated: DFAProtocol validates at load.

Usage: python -m ltlf.compile [spec_dir] [out_json]
"""
import hashlib
import json
import sys
from pathlib import Path

import yaml
from logaut import ltl2dfa
from pylogics.parsers import parse_ltl

from ltlf.dfa import all_letters, letter_key

COMPILER_VERSION = 1  # bump when to_table or the cache format changes, so cached entries are never reused
MAX_ALPHABET = 8
DEFAULT_SPECS = Path(".claude/ltlf/specs")
DEFAULT_OUT = Path(".claude/ltlf/cache/index.json")


def to_table(dfa) -> dict:
    """pythomata DFA -> {alphabet, initial, accepting, table}, states renumbered with the initial state as 0.

    Asks pythomata for the successor of every state on every letter, so guards are evaluated here once
    and never at hook time. Letters are written as in ltlf.dfa.letter_key.
    """
    # shortcut: alphabet comes from guard free_symbols as in the original; an atom simplified out of every
    # guard is dropped, which cannot affect a step.
    atoms = set()
    for q in dfa.states:                                  # every state
        for _, g, _ in dfa.get_transitions_from(q):       # every outgoing edge (src, guard, dst)
            for s in g.free_symbols:                      # every variable in that edge's guard
                atoms.add(str(s))
    alphabet = sorted(atoms)
    if len(alphabet) > MAX_ALPHABET:
        raise ValueError(f"alphabet has {len(alphabet)} propositions, max {MAX_ALPHABET}")
    order = [dfa.initial_state] + sorted(q for q in dfa.states if q != dfa.initial_state)
    index = {q: i for i, q in enumerate(order)}
    table = []
    for q in order:
        row = {}
        for letter in all_letters(alphabet):
            succ = dfa.get_successor(q, {p: p in letter for p in alphabet})
            if succ is None:
                raise ValueError(f"DFA is partial: no successor from state {q} on letter {set(letter)}")
            row[letter_key(letter)] = index[succ]
        table.append(row)
    return {
        "alphabet": alphabet,
        "initial": 0,
        "accepting": sorted(index[q] for q in dfa.accepting_states),
        "table": table,
    }


def compile_formula(formula: str) -> dict:
    """Compile one LTLf formula with Lydia into the table form."""
    return to_table(ltl2dfa(parse_ltl(formula), "lydia"))


def compile_spec(raw: bytes, cached: dict | None = None) -> tuple[str, dict, bool]:
    """One spec YAML -> (name, cache entry, reused). Reuses `cached` if source and compiler are unchanged."""
    spec = yaml.safe_load(raw)
    sha = hashlib.sha256(raw).hexdigest()
    cached = (cached or {}).get(spec["name"])
    if cached and cached.get("source_sha256") == sha and cached.get("compiler_version") == COMPILER_VERSION:
        return spec["name"], cached, True
    entry = {k: spec.get(k) for k in ("class", "enforcement", "formula", "message")}
    entry["source_sha256"] = sha
    entry["compiler_version"] = COMPILER_VERSION
    entry.update(compile_formula(spec["formula"]))
    return spec["name"], entry, False


def _load_old(out: Path) -> dict:
    """The previous cache, or {} if it is missing or unreadable (then everything recompiles)."""
    try:
        old = json.loads(out.read_text(encoding="utf-8"))
        return old if isinstance(old, dict) else {}
    except (OSError, ValueError):
        return {}


def main(spec_dir: Path = DEFAULT_SPECS, out: Path = DEFAULT_OUT) -> int:
    """Compile every spec in `spec_dir` into one cache file, reusing unchanged entries.

    The index is rebuilt from the files present, so deleted specs drop out. On any failure return 1
    and keep the old cache.
    """
    if out.is_dir():  # given the cache folder rather than the file
        out = out / "index.json"
    old, index, failed = _load_old(out), {}, 0
    for path in sorted(spec_dir.glob("*.yaml")):
        try:
            name, entry, reused = compile_spec(path.read_bytes(), old)
            if name in index:
                raise ValueError(f"duplicate spec name {name!r}")
            index[name] = entry
            how = "reused" if reused else "compiled"
            print(f"{path.name}: {how}, {len(entry['table'])} states, alphabet {entry['alphabet']}")
        except Exception as e:  # report every bad spec, then fail
            print(f"error: {path.name}: {e}", file=sys.stderr)
            failed += 1
    if failed:
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=1), encoding="utf-8")
    tmp.replace(out)  # readers never see a half-written cache
    return 0


if __name__ == "__main__":
    sys.exit(main(*map(Path, sys.argv[1:3])))
