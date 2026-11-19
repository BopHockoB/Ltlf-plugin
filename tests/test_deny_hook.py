"""The deny hook on a tmp project root with a hand-written cache (no Docker)."""
import hashlib
import json
from pathlib import Path

import pytest
from deny import main

SPEC = b'name: no-force-push\nclass: safety\nenforcement: deny\nformula: "G(!git_force_push)"\nmessage: "No force push."\n'
G_NOT = {"alphabet": ["git_force_push"], "initial": 0, "accepting": [0],
         "table": [{"": 0, "git_force_push": 1}, {"": 1, "git_force_push": 1}]}


@pytest.fixture
def root(tmp_path):
    write(tmp_path, {"no-force-push": ("deny", G_NOT)})
    return tmp_path


def write(root, specs):
    """specs: name -> (enforcement, table dict). Writes one spec file and its cache entry per name."""
    (root / ".claude/ltlf/specs").mkdir(parents=True, exist_ok=True)
    (root / ".claude/ltlf/cache").mkdir(parents=True, exist_ok=True)
    index = {}
    for name, (enforcement, table) in specs.items():
        raw = SPEC.replace(b"no-force-push", name.encode()).replace(b"deny", enforcement.encode())
        (root / f".claude/ltlf/specs/{name}.yaml").write_bytes(raw)
        index[name] = {"class": "safety", "enforcement": enforcement, "message": "No force push.",
                       "source_sha256": hashlib.sha256(raw).hexdigest(), **table}
    (root / ".claude/ltlf/cache/index.json").write_text(json.dumps(index), encoding="utf-8")


def payload(command):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t1",
                       "tool_input": {"command": command}})


def logged(root):
    path = root / ".claude/ltlf/logs/hook.jsonl"
    return [json.loads(line) for line in path.open(encoding="utf-8")] if path.exists() else []


def test_force_push_denied_with_message(root):
    out = json.loads(main(payload("git push --force"), root))["hookSpecificOutput"]
    assert out == {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                   "permissionDecisionReason": "No force push."}
    assert logged(root)[0]["denied_by"] == ["no-force-push"]


def test_plain_push_allowed_and_not_logged(root):
    assert main(payload("git push"), root) is None
    assert logged(root) == []


def test_stale_cache_fails_open(root):
    (root / ".claude/ltlf/specs/no-force-push.yaml").write_bytes(SPEC + b"# edited\n")
    assert main(payload("git push --force"), root) is None
    assert "stale cache" in logged(root)[0]["error"]


def test_missing_cache_fails_open(root):
    (root / ".claude/ltlf/cache/index.json").unlink()
    assert main(payload("git push --force"), root) is None
    assert "FileNotFoundError" in logged(root)[0]["error"]


def test_garbage_stdin_fails_open(root):
    assert main("not json {", root) is None
    assert "JSONDecodeError" in logged(root)[0]["error"]


def test_log_spec_is_ignored(tmp_path):
    write(tmp_path, {"no-force-push": ("log", G_NOT)})
    assert main(payload("git push --force"), tmp_path) is None
    assert logged(tmp_path) == []


def test_invalid_deny_spec_fails_open(tmp_path):
    multi_step = dict(G_NOT, accepting=[0, 1])  # a force push moves to another accepting state: not single-call
    write(tmp_path, {"no-force-push": ("deny", multi_step)})
    assert main(payload("git push --force"), tmp_path) is None
    assert "not single-call" in logged(tmp_path)[0]["error"]


def test_unmonitored_project_is_silent(tmp_path):
    assert main(payload("git push --force"), tmp_path) is None  # no .claude/ltlf/specs/
    assert not (tmp_path / ".claude").exists()


def test_plugin_hook_command_points_at_this_script():
    # a missing script makes python exit 2, which Claude Code treats as "block": every call would be denied
    root = Path(__file__).resolve().parents[1]
    command = json.loads((root / "hooks/hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    script = command.split("${CLAUDE_PLUGIN_ROOT}/")[1].rstrip('"')
    assert (root / script).is_file()
