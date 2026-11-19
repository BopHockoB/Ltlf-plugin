"""Replay CLI: step every spec (deny and log) over saved transcripts, and check hook/replay agreement.

Usage: python hooks/replay.py [--project DIR] TRANSCRIPT.jsonl...

Each transcript file is replayed on its own. One JSON line per file is appended to
<project>/.claude/ltlf/logs/replay.jsonl. Exit code 1 iff some call's live hook decision (from the transcript)
differs from the replay's deny decision. Standard library and ltlf only.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PLUGIN_ROOT), str(PLUGIN_ROOT / "labels")]  # -I drops the script dir and cwd

from claude_code import registry, transcript_calls, transcript_steps  # noqa: E402
from ltlf.protocol import DFAProtocol, load_cache  # noqa: E402


async def replay(path: Path, index: dict) -> dict:
    """One transcript -> report record: entries, denied ids, final verdicts and hook/replay disagreements."""
    calls = transcript_calls(path)
    steps = transcript_steps(calls)
    report = await DFAProtocol(index, registry()).run(steps)
    deny = {name: entry for name, entry in index.items() if entry.get("enforcement") == "deny"}
    disagreements = []
    for call in calls:  # every call, ran or not: what would the stateless hook have said?
        _, denied_by = await DFAProtocol(deny, registry()).pre(call.event("pre"))
        if bool(denied_by) != call.hook_denied:
            disagreements.append({"tool_use_id": call.id, "tool": call.tool, "args": call.args,
                                  "transcript_hook_denied": call.hook_denied, "replay_denied_by": denied_by})
    return {"t": time.time(), "transcript": str(path), "calls": len(calls), "steps": len(steps),
            "entries": [asdict(e) for e in report.entries], "denied": report.denied, "final": report.final,
            "disagreements": disagreements}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", type=Path, default=Path(os.environ.get("CLAUDE_PROJECT_DIR") or "."))
    parser.add_argument("transcripts", type=Path, nargs="+")
    args = parser.parse_args(argv)
    ltlf_dir = args.project / ".claude" / "ltlf"
    index = load_cache(ltlf_dir)  # a stale or missing cache is an error here, not fail-open
    log = ltlf_dir / "logs" / "replay.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    disagreed = 0
    with log.open("a", encoding="utf-8") as out:
        for path in args.transcripts:
            record = asyncio.run(replay(path, index))
            out.write(json.dumps(record) + "\n")
            disagreed += bool(record["disagreements"])
            print(f"{path.name}: {record['calls']} calls, {record['steps']} steps, {len(record['entries'])} entries, "
                  f"{len(record['denied'])} denied, {len(record['disagreements'])} disagreements")
    return 1 if disagreed else 0


if __name__ == "__main__":
    sys.exit(main())
