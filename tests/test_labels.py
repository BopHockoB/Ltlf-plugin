import json
from pathlib import Path

import pytest
from claude_code import git_force_push, normalise

FIXTURES = Path(__file__).parent / "fixtures" / "probe"


def bash(command):
    return {"tool": "Bash", "args": {"command": command}}


def test_normalise_pre_and_post_payloads():
    payloads = [json.loads(line)["payload"] for line in (FIXTURES / "hook_payloads.jsonl").open(encoding="utf-8")]
    pre, post = (p for p in payloads if p["tool_input"].get("command") == "echo before-rewind")
    assert normalise(pre) == {
        "id": pre["tool_use_id"], "tool": "Bash", "args": pre["tool_input"],
        "output": None, "phase": "pre", "agent": None,
    }
    event = normalise(post)
    assert event["phase"] == "post" and event["output"]["stdout"] == "before-rewind"
    assert event["id"] == pre["tool_use_id"]
    sub = next(p for p in payloads if p.get("agent_id"))
    assert normalise(sub)["agent"] == sub["agent_id"]


@pytest.mark.parametrize("command", [
    "git push --force",
    "git push -f origin main",
    "git push -uf origin x",
    "git push origin +main",
    "cd x && git push --force",
    "git -C repo push --force",
    "GIT_TRACE=1 git push --force",
    "git status; git push -f",
])
def test_force_push_fires(command):
    assert git_force_push(bash(command))


@pytest.mark.parametrize("command", [
    "git push",
    "git push origin main",
    "git push --force-with-lease",
    "git push --force-if-includes --force-with-lease origin main",
    "git push --follow-tags",
    'git commit -m "no git push --force"',
    "echo git push -f",
    "git fetch -f",
    "git -c push.default=current push",
])
def test_force_push_does_not_fire(command):
    assert not git_force_push(bash(command))


def test_force_push_only_on_bash():
    assert not git_force_push({"tool": "Write", "args": {"content": "git push --force"}})


def test_force_push_silent_on_probe_transcript():
    commands = [
        block["input"]["command"]
        for line in (FIXTURES / "transcript.jsonl").open(encoding="utf-8")
        for block in (json.loads(line)["message"]["content"] if isinstance(json.loads(line)["message"]["content"], list) else [])
        if block.get("type") == "tool_use" and block["name"] == "Bash"
    ]
    assert commands and not any(git_force_push(bash(c)) for c in commands)
