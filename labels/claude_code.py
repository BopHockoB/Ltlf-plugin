"""Claude Code event normalisation and label rules. The only label code that knows Claude Code formats.

Standard library plus ltlf only: this runs on the hook path.
"""
import json
import re
import shlex
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class Call:
    """One tool call in a transcript: its tool_use and, once written, its tool_result line."""

    id: str
    tool: str
    args: dict
    agent: str | None
    use_at: tuple[int, int]  # (line, block) position of the tool_use
    result_at: tuple[int, int] | None = None
    result_line: dict = field(default_factory=dict)
    result_block: dict = field(default_factory=dict)

    @property
    def ran(self) -> bool:
        """False iff the transcript marks the call as never run: rejected by a hook or at the prompt, or refused
        by Claude Code's own input check. Unknown shapes count as ran (errs towards false positives)."""
        content = self.result_block.get("content")
        return not (
            (self.result_line.get("permissionDecision") or {}).get("decision") == "reject"
            or self.result_line.get("toolDenialKind")
            or (isinstance(content, str) and content.startswith("<tool_use_error>"))
        )

    @property
    def hook_denied(self) -> bool:
        """True iff a PreToolUse hook (ours or another) denied this call."""
        decision = self.result_line.get("permissionDecision") or {}
        return decision.get("decision") == "reject" and decision.get("source") == "hook"

    def event(self, phase: str) -> dict:
        """The canonical event, equal to normalise() of this call's hook payload."""
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "output": self.result_line.get("toolUseResult") if phase == "post" else None,
            "phase": phase,
            "agent": self.agent,
        }


def transcript_calls(path: str | Path) -> list[Call]:
    """Every tool call in one transcript JSONL file, in tool_use order.

    Reads only user and assistant lines with a uuid: api-request-blob lines repeat the same blocks.
    Skips unparseable lines (the last one can be mid-write). Rewound lines stay in, in file order.
    """
    calls: dict[str, Call] = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, raw in enumerate(f):
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(line, dict) or line.get("type") not in ("user", "assistant") or not line.get("uuid"):
                continue
            content = (line.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for j, block in enumerate(content):
                if block.get("type") == "tool_use":
                    calls.setdefault(block["id"], Call(block["id"], block["name"], block.get("input") or {},
                                                       line.get("agentId"), (i, j)))
                elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                    call = calls[block["tool_use_id"]]
                    if call.result_at is None:
                        call.result_at, call.result_line, call.result_block = (i, j), line, block
    return list(calls.values())


def transcript_steps(calls: list[Call]) -> list[tuple[str, dict]]:
    """(phase, event) steps in file order: a pre step at each tool_use, a post step at each tool_result.

    Calls that never ran give no step. A call with no result yet gets only its pre step.
    """
    marks = [(c.use_at, "pre", c) for c in calls if c.ran]
    marks += [(c.result_at, "post", c) for c in calls if c.ran and c.result_at is not None]
    return [(phase, call.event(phase)) for _, phase, call in sorted(marks, key=lambda m: m[0])]


_SIMPLE_COMMANDS = re.compile(r"&&|\|\||[;|\n]")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.DOTALL | re.MULTILINE)


def _simple_commands(command: str) -> list[str]:
    """Split a shell command on ; && || | and newlines, after dropping heredoc bodies (they are data, not commands).

    shortcut: separators inside quotes still split; upgrade when a transcript shows a false label from it.
    """
    return _SIMPLE_COMMANDS.split(_HEREDOC.sub("<<heredoc", command))


_SHORT_FORCE = re.compile(r"-[a-zA-Z]*f[a-zA-Z]*")  # -f, -uf; never --long options


def _program(command: str) -> list[str]:
    """Words of one simple command from its program on; the program is lowercased, without a path or `.exe`.

    Drops leading `VAR=value`, `sudo`/`env`, and a PowerShell `$x =` assignment.
    """
    try:
        words = shlex.split(command)
    except ValueError:  # unbalanced quotes
        words = command.split()
    while words and (words[0] in ("sudo", "env", "=") or words[0].startswith("$")
                     or ("=" in words[0] and not words[0].startswith("-"))):
        words = words[1:]
    if words:
        words[0] = re.split(r"[\\/]", words[0])[-1].lower().removesuffix(".exe")
    return words


def _git_subcommand(words: list[str]) -> tuple[str | None, list[str]]:
    """For `git [global options] <sub> args...`: (sub, args). (None, []) if not a git command."""
    if not words or words[0] != "git":
        return None, []
    i = 1
    while i < len(words) and words[i].startswith("-"):  # git's global options; -C and -c take a value
        i += 2 if words[i] in ("-C", "-c") else 1
    return (words[i], words[i + 1:]) if i < len(words) else (None, [])


def _is_force_push(command: str) -> bool:
    """True iff this simple command is `git [global options] push ...` with --force, -f or a +refspec."""
    sub, args = _git_subcommand(_program(command))
    return sub == "push" and any(
        w == "--force" or _SHORT_FORCE.fullmatch(w) or (w.startswith("+") and len(w) > 1) for w in args)


def git_force_push(event: Mapping) -> bool:
    """Pre rule: a Bash call that force-pushes. --force-with-lease and --force-if-includes are allowed.

    shortcut: splits on ; && || | without respecting quotes, and misses git aliases, scripts and sh -c strings;
    upgrade when a transcript shows one of these.
    """
    if event["tool"] != "Bash":
        return False
    command = event["args"].get("command") or ""
    return any(_is_force_push(part) for part in _simple_commands(command))


_SHELL_TOOLS = {"Bash", "PowerShell"}
_NET_TOOLS = {"WebFetch", "WebSearch"}  # the URL or query itself can carry data
_NET_PROGRAMS = {
    "curl", "wget", "http", "https", "nc", "ncat", "netcat", "telnet", "ftp", "scp", "sftp", "ssh", "rsync",
    "invoke-webrequest", "invoke-restmethod", "iwr", "irm",
}
_GH_SENDS = {"api", "create", "comment", "edit", "upload"}  # gh subcommands that send data, e.g. `gh gist create`


def network_send(event: Mapping) -> bool:
    """Pre rule: the call can send data off the machine (any request counts: a URL can carry data).

    WebFetch and WebSearch always; in Bash and PowerShell, a network program, `git push`, or a sending `gh` command.
    shortcut: misses scripts (`python -c`, `node -e`), MCP tools and full-path Windows programs with backslashes;
    upgrade when a transcript shows one of these.
    """
    if event["tool"] in _NET_TOOLS:
        return True
    if event["tool"] not in _SHELL_TOOLS:
        return False
    for part in _simple_commands(event["args"].get("command") or ""):
        words = _program(part)
        if words and (words[0] in _NET_PROGRAMS or _git_subcommand(words)[0] == "push"
                      or (words[0] == "gh" and _GH_SENDS & set(words[1:3]))):
            return True
    return False


_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{36,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{22,}"
    r"|\bsk-ant-[A-Za-z0-9_-]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
)
_ECHO_TOOLS = {"Write", "Edit", "NotebookEdit"}  # output echoes the agent's input plus originalFile the model never sees


def _strings(value) -> Iterator[str]:
    """Every string leaf of a JSON value, so JSON escaping never splits a match."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def secret_in_output(event: Mapping) -> bool:
    """Post rule: the tool's output contains a credential (private key, AWS, GitHub, Anthropic or Slack token).

    shortcut: fixed token shapes only; misses passwords, generic API keys and encoded secrets; add a shape when a
    transcript shows one.
    """
    if event["output"] is None or event["tool"] in _ECHO_TOOLS:
        return False
    return any(_SECRET.search(s) for s in _strings(event["output"]))


def registry() -> LabelRegistry:
    """Every Claude Code label rule."""
    reg = LabelRegistry()
    reg.add("git_force_push", git_force_push, phase="pre")
    reg.add("secret_in_output", secret_in_output, phase="post")
    reg.add("network_send", network_send, phase="pre")
    return reg
