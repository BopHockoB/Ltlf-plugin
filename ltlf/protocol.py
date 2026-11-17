"""DFAProtocol: owns every monitor, labels each step and steps them all (port of DFAProtocolPerStep).

The replay drives it over a whole trace; the PreToolUse hook builds a fresh instance and calls pre() once,
which is exactly the stateless deny check, so hook and replay share one code path.
"""
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ltlf.dfa import DFA
from ltlf.registry import LabelRegistry

CLASSES = ("safety", "goal", "final")
ENFORCEMENTS = ("deny", "log")


@dataclass
class Monitor:
    """One spec's DFA plus its current state."""

    name: str
    cls: str
    enforcement: str | None
    phase: str  # "pre" iff every proposition comes from a pre rule
    message: str
    dfa: DFA
    state: int = field(init=False)

    def __post_init__(self) -> None:
        self.state = self.dfa.initial

    @property
    def accepting(self) -> bool:
        """True iff the current state is accepting."""
        return self.dfa.is_accepting(self.state)


@dataclass(frozen=True)
class Entry:
    """A safety monitor leaving its accepting set (violation) or a goal monitor entering it."""

    kind: str  # "violation" (safety) or "goal"
    spec: str
    step: int  # 0-based index of the committed step
    event_id: Any
    phase: str


@dataclass
class Report:
    """Replay result: entries in step order, ids of denied events, final-spec verdicts."""

    entries: list[Entry]
    denied: list[Any]
    final: dict[str, bool]


def load_monitor(name: str, entry: Mapping, registry: LabelRegistry) -> Monitor:
    """Build a monitor from one cache entry, enforcing the loader rules.

    Rejects: unknown class or enforcement, a proposition with no label rule, a safety spec violated on
    the empty trace, and deny on a spec that has a post proposition or is not single-call.
    """
    cls, enforcement = entry.get("class"), entry.get("enforcement")
    if cls not in CLASSES:
        raise ValueError(f"{name}: class must be one of {CLASSES}, got {cls!r}")
    if cls != "safety" and enforcement is not None:
        raise ValueError(f"{name}: enforcement is only allowed on safety specs")
    if cls == "safety" and enforcement not in ENFORCEMENTS:
        raise ValueError(f"{name}: safety spec needs enforcement in {ENFORCEMENTS}")
    dfa = DFA.from_dict(entry)
    unknown = [p for p in dfa.alphabet if p not in registry]
    if unknown:
        raise ValueError(f"{name}: no label rule for {unknown}")
    phase = "post" if any(registry.phase(p) == "post" for p in dfa.alphabet) else "pre"
    # safety formulas state the requirement ("something bad never happens"): accepting = still safe
    if cls == "safety" and not dfa.is_accepting(dfa.initial):
        raise ValueError(f"{name}: violated on the empty trace (initial state is not accepting)")
    if enforcement == "deny":
        if phase == "post":
            raise ValueError(f"{name}: deny is not allowed on a spec with a post proposition")
        # single-call: a call that is not denied must leave the monitor in its initial state
        if any(t != dfa.initial and dfa.is_accepting(t) for t in dfa.table[dfa.initial].values()):
            raise ValueError(f"{name}: deny spec is not single-call")
    return Monitor(name, cls, enforcement, phase, entry.get("message", ""), dfa)


class DFAProtocol:
    """All monitors plus the label registry; steps every monitor at each pre and post step."""

    def __init__(self, index: Mapping[str, Mapping], registry: LabelRegistry) -> None:
        self.registry = registry
        self.monitors = [load_monitor(name, entry, registry) for name, entry in index.items()]
        self.reset()

    @classmethod
    def from_cache(cls, path: str | Path, registry: LabelRegistry) -> "DFAProtocol":
        """Load every spec from the compiled cache JSON."""
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), registry)

    def reset(self) -> None:
        """Return every monitor to its initial state; forget the step count and denied events."""
        for m in self.monitors:
            m.state = m.dfa.initial
        self.steps = 0
        self._denied: dict[Any, None] = {}  # ordered set of denied event ids

    async def pre(self, event: Mapping) -> tuple[list[Entry], list[str]]:
        """Pre step: every monitor steps; post propositions are false. Returns (entries, denying specs)."""
        labels = await self._label(event, self.monitors, "pre")
        candidates = [m.dfa.step(m.state, labels) for m in self.monitors]
        denied_by = [
            m.name for m, c in zip(self.monitors, candidates) if m.enforcement == "deny" and not m.dfa.is_accepting(c)
        ]
        if denied_by:  # all-or-nothing: discard every candidate
            self._denied[event["id"]] = None
            return [], denied_by
        return self._commit(self.monitors, candidates, event, "pre"), []

    async def post(self, event: Mapping) -> list[Entry]:
        """Post step: only monitors with a post proposition step; pre propositions are false."""
        if event["id"] in self._denied:
            return []
        monitors = [m for m in self.monitors if m.phase == "post"]
        labels = await self._label(event, monitors, "post")
        return self._commit(monitors, [m.dfa.step(m.state, labels) for m in monitors], event, "post")

    def finish(self) -> dict[str, bool]:
        """Verdict of each final spec: these matter only when the task is finished."""
        return {m.name: m.accepting for m in self.monitors if m.cls == "final"}

    async def run(self, steps: Iterable[tuple[str, Mapping]]) -> Report:
        """Reference rewalk: reset, then replay every step in order."""
        self.reset()
        entries: list[Entry] = []
        for phase, event in steps:
            if phase == "pre":
                entries += (await self.pre(event))[0]
            elif phase == "post":
                entries += await self.post(event)
            else:
                raise ValueError(f"unknown phase {phase!r}")
        return Report(entries, list(self._denied), self.finish())

    async def _label(self, event: Mapping, monitors: list[Monitor], phase: str) -> dict[str, bool]:
        """Label only this phase's propositions; the others are absent, so DFA.letter reads them as false."""
        props = {p for m in monitors for p in m.dfa.alphabet if self.registry.phase(p) == phase}
        return await self.registry.evaluate(event, props) if props else {}

    def _commit(self, monitors: list[Monitor], candidates: list[int], event: Mapping, phase: str) -> list[Entry]:
        """Move monitors to their candidates as one step; return safety violations and reached goals."""
        entries = []
        for m, c in zip(monitors, candidates):
            if m.cls == "safety" and m.accepting and not m.dfa.is_accepting(c):
                entries.append(Entry("violation", m.name, self.steps, event["id"], phase))
            elif m.cls == "goal" and not m.accepting and m.dfa.is_accepting(c):
                entries.append(Entry("goal", m.name, self.steps, event["id"], phase))
            m.state = c
        self.steps += 1
        return entries
