"""Convert SHADE-Arena specs (`<name>_<type>.ltlf`) to spec YAML.

Formulas are copied exactly as written: a safety formula is the requirement ("something bad never
happens", e.g. G(!x)), and the protocol reports a violation when its DFA leaves the accepting set.

Usage: python -m ltlf.convert <src_dir> <out_dir>
"""
import sys
from pathlib import Path

import yaml

CLASSES = ("safety", "goal", "final")


def convert(path: Path) -> dict:
    """Turn one `<name>_<type>.ltlf` file into a spec dict; safety specs get enforcement log."""
    name, sep, kind = path.stem.partition("_")
    if not sep or "_" in kind or kind not in CLASSES:
        raise ValueError(f"{path.name}: expected <name>_<safety|goal|final>.ltlf")
    requirement = " ".join(path.read_text(encoding="utf-8").split())
    if not requirement:
        raise ValueError(f"{path.name}: empty formula")
    # keep the kind in goal/final names: some tasks have both <x>_goal and <x>_final
    spec = {"name": name if kind == "safety" else f"{name}-{kind}", "class": kind}
    if kind == "safety":
        spec["enforcement"] = "log"  # deny only after validation on honest transcripts (invariant 3)
    spec["formula"] = requirement
    spec["message"] = f"Converted from {path.name} ({kind}): {requirement}"
    return spec


def main(src: Path, out: Path) -> int:
    """Convert every *.ltlf in `src` to YAML in `out`, never overwriting. Returns 1 if any was skipped."""
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    # Same glob as the original loader, so files it never loaded are not converted either.
    for path in sorted(src.glob("*.ltlf")):
        try:
            spec = convert(path)
            target = out / f"{spec['name']}.yaml"
            if target.exists():
                raise ValueError(f"{target} exists, not overwriting")
        except ValueError as e:
            print(f"skip: {e}", file=sys.stderr)
            failed += 1
            continue
        target.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"{path.name} -> {target.name}")
    for path in sorted(p for p in src.iterdir() if p.is_file() and p.suffix not in (".ltlf", ".py")):
        print(f"skip: {path.name} is not *.ltlf", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
