"""PreToolUse deny hook (stateless): deny the pending call iff a deny spec's DFA leaves its accepting set on it.

Plugin code lives next to this file; specs, cache and logs live in the project (`$CLAUDE_PROJECT_DIR/.claude/ltlf/`).
A project without `.claude/ltlf/specs/` is not monitored: the hook allows silently and writes nothing.
Fails open: any error, a missing cache or a stale cache allows the call and is logged to
.claude/ltlf/logs/hook.jsonl. Standard library and ltlf only, so it runs under `python -I -S`.
"""
import json
import os
import sys
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PLUGIN_ROOT), str(PLUGIN_ROOT / "labels")]  # -I drops the script dir and cwd


def log(root: Path, record: dict) -> None:
    path = root / ".claude" / "ltlf" / "logs" / "hook.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), **record}) + "\n")


def main(raw: str, root: Path) -> str | None:
    """The hook's stdout for one payload, or None to allow. `root` is the project root."""
    tool_use_id = None
    if not (root / ".claude" / "ltlf" / "specs").is_dir():
        return None
    try:
        import asyncio

        from claude_code import normalise, registry
        from ltlf.protocol import DFAProtocol, load_cache

        payload = json.loads(raw)
        tool_use_id = payload.get("tool_use_id")
        index = load_cache(root / ".claude" / "ltlf")
        deny ={name: entry for name, entry in index.items() if entry.get("enforcement") == "deny"}
        if not deny:
            return None
        _, denied_by = asyncio.run(DFAProtocol(deny, registry()).pre(normalise(payload)))
        if not denied_by:
            return None
        log(root, {"tool_use_id": tool_use_id, "denied_by": denied_by, "tool_input": payload.get("tool_input")})
        return json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": " ".join(deny[name].get("message") or name for name in denied_by),
        }})
    except Exception as e:  # fail open
        try:
            log(root, {"tool_use_id": tool_use_id, "error": repr(e)})
        except Exception:
            pass
        return None


if __name__ == "__main__":
    out = main(sys.stdin.buffer.read().decode("utf-8", "replace"), Path(os.environ.get("CLAUDE_PROJECT_DIR") or "."))
    if out:
        print(out)
    sys.exit(0)
