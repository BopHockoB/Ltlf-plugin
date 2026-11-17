import pytest

from ltlf.dfa import letter_key
from ltlf.protocol import DFAProtocol
from ltlf.registry import LabelRegistry

pytestmark = pytest.mark.asyncio

# Hand-written tables: table[state][true propositions] -> next state.
F_X = {"alphabet": ["x"], "initial": 0, "accepting": [1], "table": [{"": 0, "x": 1}, {"": 1, "x": 1}]}
LAST_X = {"alphabet": ["x"], "initial": 0, "accepting": [1], "table": [{"": 0, "x": 1}, {"": 0, "x": 1}]}
F_A_THEN_B = {  # F(a & F(b))
    "alphabet": ["a", "b"], "initial": 0, "accepting": [2],
    "table": [{"": 0, "a": 1, "b": 0, "a,b": 2}, {"": 1, "a": 1, "b": 2, "a,b": 2}, {"": 2, "a": 2, "b": 2, "a,b": 2}],
}
# Safety specs are requirements: the same tables with the accepting set complemented.
NEVER_X = {**F_X, "accepting": [0]}  # G(!x)
NOT_LAST_X = {**LAST_X, "accepting": [0]}  # the last step is not x
NO_LEAK = {**F_A_THEN_B, "accepting": [0, 1]}  # !F(a & F(b))


def spec(table, cls="safety", enforcement="log", alphabet=None):
    entry = {**table, "class": cls, "enforcement": enforcement, "message": "m"}
    if alphabet:  # rename the propositions, in the alphabet and in every letter key
        rename = dict(zip(table["alphabet"], alphabet))
        entry["alphabet"] = alphabet
        entry["table"] = [{letter_key(rename[p] for p in k.split(",") if p): t for k, t in row.items()}
                          for row in table["table"]]
    return entry


def registry(pre=(), post=()):
    """A proposition is true iff it is in the event's 'true' set."""
    reg = LabelRegistry()
    for phase, props in (("pre", pre), ("post", post)):
        for p in props:
            reg.add(p, lambda ev, p=p: p in ev["true"], phase=phase)
    return reg


def ev(i, *true):
    return {"id": i, "true": set(true)}


def states(proto):
    return {m.name: m.state for m in proto.monitors}


async def test_deny_discards_every_candidate():
    reg = registry(pre=["x", "y"])
    proto = DFAProtocol({"deny": spec(NEVER_X, enforcement="deny"),
                         "log": spec(NEVER_X, alphabet=["y"])}, reg)
    before = states(proto)

    events, denied_by = await proto.pre(ev(1, "x", "y"))
    assert denied_by == ["deny"] and events == []
    assert states(proto) == before and proto.steps == 0  # the log monitor did not move either
    assert await proto.post(ev(1, "x", "y")) == [] and proto.steps == 0

    events, denied_by = await proto.pre(ev(2, "y"))
    assert denied_by == [] and [e.spec for e in events] == ["log"] and proto.steps == 1


async def test_violation_reported_on_entry_only():
    proto = DFAProtocol({"f": spec(NEVER_X), "last": spec(NOT_LAST_X)}, registry(pre=["x"]))
    reported = []
    for i, labels in enumerate([("x",), ("x",), (), ("x",)]):
        events, _ = await proto.pre(ev(i, *labels))
        reported += [(e.spec, e.step) for e in events]
    assert reported == [("f", 0), ("last", 0), ("last", 3)]  # f stays violated; last recovers and is violated again


async def test_pre_only_monitors_skip_post_steps():
    proto = DFAProtocol({"f": spec(NEVER_X)}, registry(pre=["x"]))
    for i in range(3):
        await proto.pre(ev(i))
    state = proto.monitors[0].state
    for i in range(3):
        await proto.post(ev(i, "x"))  # x is a pre proposition: false at post steps, and f doesn't step
    assert proto.monitors[0].state == state and proto.steps == 6


async def test_post_information_moves_monitor_at_post_step():
    proto = DFAProtocol({"leak": spec(NO_LEAK)}, registry(pre=["b"], post=["a"]))
    assert (await proto.pre(ev(1, "a")))[0] == [] and proto.monitors[0].state == 0  # a is false at a pre step
    assert await proto.post(ev(1, "a")) == [] and proto.monitors[0].state == 1
    events, _ = await proto.pre(ev(2, "b"))
    assert [(e.kind, e.step, e.phase) for e in events] == [("violation", 2, "pre")]


async def test_goal_reports_once_and_final_only_at_finish():
    goal = spec(F_X, cls="goal", enforcement=None, alphabet=["done"])
    final = spec(LAST_X, cls="final", enforcement=None, alphabet=["done"])
    proto = DFAProtocol({"g": goal, "fin": final}, registry(pre=["done"]))

    report = await proto.run([("pre", ev(1, "done")), ("pre", ev(2, "done"))])
    assert [(e.kind, e.spec, e.step) for e in report.entries] == [("goal", "g", 0)]
    assert report.final == {"fin": True}

    report = await proto.run([("pre", ev(1, "done")), ("pre", ev(2))])
    assert report.final == {"fin": False}  # a later step undid it


async def test_run_equals_incremental_and_is_repeatable():
    index = {"leak": spec(NO_LEAK), "f": spec(NEVER_X, alphabet=["b"]), "deny": spec(NEVER_X, enforcement="deny")}
    reg = registry(pre=["b", "x"], post=["a"])
    steps = [("pre", ev(1)), ("pre", ev(2, "x")), ("post", ev(1, "a")), ("post", ev(2, "a")),
             ("pre", ev(3, "b")), ("post", ev(3))]

    incremental = DFAProtocol(index, reg)
    events = []
    for phase, e in steps:
        events += (await incremental.pre(e))[0] if phase == "pre" else await incremental.post(e)

    proto = DFAProtocol(index, reg)
    first = await proto.run(steps)
    assert first.entries == events and first.denied == [2]
    assert states(proto) == states(incremental)
    assert await proto.run(steps) == first


@pytest.mark.parametrize("entry, reg, msg", [
    (spec(F_X, cls="completion"), registry(pre=["x"]), "class"),
    (spec(F_X, cls="goal", enforcement="log"), registry(pre=["x"]), "only allowed on safety"),
    (spec(F_X, enforcement=None), registry(pre=["x"]), "needs enforcement"),
    (spec(F_X), registry(pre=["y"]), "no label rule"),
    (spec(NEVER_X, enforcement="deny"), registry(post=["x"]), "post proposition"),
    (spec(NO_LEAK, enforcement="deny"), registry(pre=["a", "b"]), "single-call"),
    (spec(F_X), registry(pre=["x"]), "empty trace"),  # violation form F(x) is not a requirement
    ({**spec(F_X), "table": [{"": 0, "x": 1}, {"": 1}]}, registry(pre=["x"]), "one entry per letter"),
    ({**spec(F_X), "table": [{"": 0, "x": 2}, {"": 1, "x": 1}]}, registry(pre=["x"]), "out of range"),
])
async def test_loader_rejects(entry, reg, msg):
    with pytest.raises(ValueError, match=msg):
        DFAProtocol({"s": entry}, reg)


async def test_from_cache(tmp_path):
    import json
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"f": spec(NEVER_X, enforcement="deny")}))
    proto = DFAProtocol.from_cache(path, registry(pre=["x"]))
    assert (await proto.pre(ev(1, "x")))[1] == ["f"]
    assert (await proto.pre(ev(2)))[1] == []
