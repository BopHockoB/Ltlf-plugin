"""Claude Code event normalisation and label rules. The only label code that knows Claude Code formats.

Standard library plus ltlf only: this runs on the hook path.
"""
import re
import shlex
from collections.abc import Mapping

from ltlf.registry import LabelRegistry


def normalise(payload: Mapping) -> dict:
    """Hook payload -> canonical event. PreToolUse has no output; PostToolUse has tool_response."""
    post = payload.get("hook_event_name") == "PostToolUse"
    return {
        "id": payload.get("tool_use_id"),
        "tool": payload.get("tool_name"),
        "args": payload.get("tool_input") or {},
        "output": payload.get("tool_response") if post else None,
        "phase": "post" if post else "pre",
        "agent": payload.get("agent_id"),
    }


_SIMPLE_COMMANDS = re.compile(r"&&|\|\||[;|\n]")
_SHORT_FORCE = re.compile(r"-[a-zA-Z]*f[a-zA-Z]*")  # -f, -uf; never --long options


def _is_force_push(command: str) -> bool:
    """True iff this simple command is `git [global options] push ...` with --force, -f or a +refspec."""
    try:
        words = shlex.split(command)
    except ValueError:  # unbalanced quotes
        words = command.split()
    while words and "=" in words[0] and not words[0].startswith("-"):  # leading VAR=value assignments
        words = words[1:]
    if not words or words[0] != "git":
        return False
    i = 1
    while i < len(words) and words[i].startswith("-"):  # git's global options; -C and -c take a value
        i += 2 if words[i] in ("-C", "-c") else 1
    if i >= len(words) or words[i] != "push":
        return False
    return any(w == "--force" or _SHORT_FORCE.fullmatch(w) or (w.startswith("+") and len(w) > 1) for w in words[i + 1:])


def git_force_push(event: Mapping) -> bool:
    """Pre rule: a Bash call that force-pushes. --force-with-lease and --force-if-includes are allowed.

    shortcut: splits on ; && || | without respecting quotes, and misses git aliases, scripts and sh -c strings;
    upgrade when a transcript shows one of these.
    """
    if event["tool"] != "Bash":
        return False
    command = event["args"].get("command") or ""
    return any(_is_force_push(part) for part in _SIMPLE_COMMANDS.split(command))


def registry() -> LabelRegistry:
    """Every Claude Code label rule."""
    reg = LabelRegistry()
    reg.add("git_force_push", git_force_push, phase="pre")
    return reg
