"""Transcript parser, replay and hook/replay agreement, on the probe fixtures and small synthetic transcripts."""
import asyncio
import json
from pathlib import Path

import pytest
from claude_code import normalise, transcript_calls, transcript_steps
from deny import main as hook
from replay import main as replay_cli
from replay import replay
from test_deny_hook import G_NOT, write

FIXTURES = Path(__file__).parent / "fixtures" / "probe"
TRANSCRIPTS = [FIXTURES / f"{name}.jsonl" for name in ("transcript", "subagent", "rejections")]


def steps_of(path):
    return [(phase, event["id"][-4:]) for phase, event in transcript_steps(transcript_calls(path))]


def test_parallel_batch_order_and_never_ran_calls():
    all_calls = transcript_calls(TRANSCRIPTS[0])
    calls = {c.args["command"]: c for c in all_calls if c.tool == "Bash"}
    ids = [c.id[-4:] for c in all_calls if c.tool == "Read"]  # the parallel batch: dfa.py, registry.py, protocol.py
    assert steps_of(TRANSCRIPTS[0])[:6] == [("pre", ids[0]), ("post", ids[0]), ("pre", ids[1]), ("pre", ids[2]),
                                            ("post", ids[1]), ("post", ids[2])]
    assert not calls["echo LTLF_PROBE_DENY"].ran and calls["echo LTLF_PROBE_DENY"].hook_denied
    assert not calls["sleep 30"].ran and not calls["sleep 30"].hook_denied  # Claude Code's own input check
    stepped = {i for _, i in steps_of(TRANSCRIPTS[0])}
    assert calls["echo LTLF_PROBE_DENY"].id[-4:] not in stepped and calls["sleep 30"].id[-4:] not in stepped


def test_each_call_steps_at_most_once_per_phase():
    for path in TRANSCRIPTS:
        steps = steps_of(path)
        assert len(steps) == len(set(steps))  # api-request-blob duplicates are not read


def test_async_agent_call_has_pre_and_post():
    agent = next(c for c in transcript_calls(TRANSCRIPTS[0]) if c.tool == "Agent")
    assert agent.ran and agent.event("post")["output"]["status"] == "async_launched"
    assert ("post", agent.id[-4:]) in steps_of(TRANSCRIPTS[0])


def test_rejected_calls_give_no_steps():
    calls = transcript_calls(TRANSCRIPTS[2])
    assert [c.args["command"].split()[0] for c in calls] == ["touch", "git", "ping"]
    assert not any(c.ran for c in calls)
    assert [c.hook_denied for c in calls] == [False, True, False]  # prompt rejection, our hook, prompt rejection
    assert steps_of(TRANSCRIPTS[2]) == []


def test_half_written_last_line_is_skipped(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(TRANSCRIPTS[1].read_text(encoding="utf-8") + '{"type": "assistant", "mess', encoding="utf-8")
    assert steps_of(path) == steps_of(TRANSCRIPTS[1])


def test_transcript_events_equal_hook_events():
    events = {}
    for path in TRANSCRIPTS[:2]:
        for call in transcript_calls(path):
            events[call.id, "pre"], events[call.id, "post"] = call.event("pre"), call.event("post")
    payloads = [json.loads(line)["payload"] for line in (FIXTURES / "hook_payloads.jsonl").open(encoding="utf-8")]
    for p in payloads:
        event = normalise(p)
        assert events[event["id"], event["phase"]] == event, p["tool_name"]


def hook_payload(call):
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": call.tool, "tool_use_id": call.id,
                       "tool_input": call.args, "agent_id": call.agent})


@pytest.fixture
def deny_project(tmp_path):
    write(tmp_path, {"no-force-push": ("deny", G_NOT)})
    return tmp_path


def test_hook_and_replay_agree_on_every_fixture_call(deny_project):
    from claude_code import registry
    from ltlf.protocol import DFAProtocol, load_cache

    deny = load_cache(deny_project / ".claude/ltlf")
    for path in TRANSCRIPTS:
        for call in transcript_calls(path):
            hook_denies = hook(hook_payload(call), deny_project) is not None
            _, denied_by = asyncio.run(DFAProtocol(deny, registry()).pre(call.event("pre")))
            assert hook_denies == bool(denied_by), call.args
            assert hook_denies == (call.args.get("command", "").startswith("git push --force"))


def synthetic(tmp_path, commands, hook_denied=()):
    """A transcript of Bash calls that ran (accept), except those in hook_denied (rejected by a hook)."""
    lines = []
    for n, command in enumerate(commands):
        use = {"type": "tool_use", "id": f"t{n}", "name": "Bash", "input": {"command": command}}
        lines.append({"type": "assistant", "uuid": f"a{n}", "message": {"content": [use]}})
        result = {"type": "user", "uuid": f"u{n}", "toolUseResult": {"stdout": "", "stderr": ""},
                  "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{n}", "content": ""}]},
                  "permissionDecision": {"decision": "accept", "source": "config"}}
        if n in hook_denied:
            result.update(permissionDecision={"decision": "reject", "source": "hook"}, toolDenialKind="permission-rule")
        lines.append(result)
    path = tmp_path / "synthetic.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return path


def test_log_spec_reports_violation_once(tmp_path):
    write(tmp_path, {"no-force-push": ("log", G_NOT)})
    path = synthetic(tmp_path, ["git push --force", "git status", "git push -f"])
    record = asyncio.run(replay(path, json.loads((tmp_path / ".claude/ltlf/cache/index.json").read_text())))
    assert record["steps"] == 6 and record["denied"] == [] and record["disagreements"] == []
    assert [(e["kind"], e["event_id"], e["step"], e["phase"]) for e in record["entries"]] == [
        ("violation", "t0", 0, "pre")]  # still violated at t2: no second report


def test_deny_spec_skips_call_and_flags_disagreement(deny_project):
    path = synthetic(deny_project, ["git push --force", "git status"])  # the push ran: the live hook missed it
    index = json.loads((deny_project / ".claude/ltlf/cache/index.json").read_text())
    record = asyncio.run(replay(path, index))
    assert record["denied"] == ["t0"] and record["entries"] == []
    assert [d["tool_use_id"] for d in record["disagreements"]] == ["t0"]


def test_cli_writes_report_and_exit_code(deny_project, capsys):
    assert replay_cli(["--project", str(deny_project), str(TRANSCRIPTS[2])]) == 0
    log = (deny_project / ".claude/ltlf/logs/replay.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and json.loads(log[0])["disagreements"] == []
    false_denial = synthetic(deny_project, ["touch x"], hook_denied={0})  # denied live, but no spec denies it
    assert replay_cli(["--project", str(deny_project), str(false_denial)]) == 1
    assert "1 disagreements" in capsys.readouterr().out
