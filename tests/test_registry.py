import asyncio
import time

import pytest

from ltlf.registry import LabelRegistry

pytestmark = pytest.mark.asyncio


async def test_tool_name_rule_and_only_requested_rules_run():
    calls = []
    reg = LabelRegistry()
    reg.add("send", "send_email", phase="pre")
    reg.add("leak", lambda ev: calls.append("leak") or "key" in ev["output"], phase="post")

    assert await reg.evaluate({"tool": "send_email"}, {"send"}) == {"send": True}
    assert await reg.evaluate({"tool": "read_file"}, ["send"]) == {"send": False}
    assert calls == []  # leak was not requested
    assert await reg.evaluate({"tool": "x", "output": "key=1"}, ["leak"]) == {"leak": True}
    assert reg.phase("leak") == "post" and "leak" in reg and "zzz" not in reg


async def test_async_rules_run_concurrently():
    async def slow(ev):
        await asyncio.sleep(0.2)
        return True

    reg = LabelRegistry()
    reg.add("a", slow, phase="pre")
    reg.add("b", slow, phase="pre")
    reg.add("c", lambda ev: 1, phase="pre")  # sync rule, truthy value coerced to bool

    start = time.perf_counter()
    assert await reg.evaluate({}, ["a", "b", "c"]) == {"a": True, "b": True, "c": True}
    assert time.perf_counter() - start < 0.35


async def test_errors():
    reg = LabelRegistry()
    reg.add("a", "tool", phase="pre")
    with pytest.raises(KeyError, match="no label rule"):
        await reg.evaluate({}, ["zzz"])
    with pytest.raises(ValueError, match="already has a rule"):
        reg.add("a", "tool", phase="pre")
    with pytest.raises(ValueError, match="phase"):
        reg.add("d", "tool", phase="during")
