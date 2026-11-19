"""Label registry: maps each proposition to a rule over one event (port of complex_sabotage LabelRegistry).

Usage:
    reg = LabelRegistry()
    reg.add("send_email", "send_email_to_contacts", phase="pre")   # true iff the event's tool has this name
    reg.add("secret_in_output", has_secret, phase="post")          # (event) -> bool, sync or async
    labels = await reg.evaluate(event, {"send_email", "secret_in_output"})

Differences from the original: a rule sees one normalised event, not the whole trace, and every
proposition declares its phase ("pre": arguments only, "post": needs the output).
"""
import asyncio
import inspect
from collections.abc import Callable, Iterable, Mapping
from typing import Any

PHASES = ("pre", "post")

# A tool name, or a function of one event returning a bool (or an awaitable of one).
Rule = str | Callable[[Mapping], Any]

class LabelRegistry:
    """Maps LTLf atomic propositions to predicates over one tool call event."""

    def __init__(self) -> None:
        self.rules: dict[str, Rule] = {}
        self.phases: dict[str, str] = {}

    def add(self, symbol: str, extraction_rule: Rule, phase: str) -> None:
        """Register the rule for `symbol`. `phase` is required: a post rule run at a pre step sees no output."""
        if phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}, got {phase!r}")
        if symbol in self.rules:
            raise ValueError(f"proposition {symbol!r} already has a rule")
        self.rules[symbol] = extraction_rule
        self.phases[symbol] = phase

    def __contains__(self, symbol: str) -> bool:
        """True iff `symbol` has a rule."""
        return symbol in self.rules

    def phase(self, symbol: str) -> str:
        """Phase of `symbol`'s rule."""
        return self.phases[symbol]

    async def _match(self, rule: Rule, event: Mapping) -> bool:
        """A tool name matches the event's tool; a function is called, and awaited if async."""
        if isinstance(rule, str):
            return event["tool"] == rule
        result = rule(event)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def evaluate(self, event: Mapping, symbols: Iterable[str]) -> dict[str, bool]:
        """Truth value of each of `symbols` on `event`. Rules run concurrently. KeyError on an unknown symbol."""
        symbols = set(symbols)
        unknown = symbols - self.rules.keys()
        if unknown:
            raise KeyError(f"no label rule for {sorted(unknown)}")
        items = [(symbol, rule) for symbol, rule in self.rules.items() if symbol in symbols]
        results = await asyncio.gather(*(self._match(rule, event) for _, rule in items))
        return dict(zip((symbol for symbol, _ in items), results))
