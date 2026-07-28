from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

# Per-turn token accounting.
#
# A single total_tokens number cannot be priced: providers charge input and output at
# very different rates (6x apart on the standard tier at the time of writing) and
# discount cached input by up to 90%. Recording the split is what turns "this turn spent
# 3,700 tokens" into a figure with a currency on it.
#
# The accumulator lives in a ContextVar rather than being threaded through every
# signature because the calls that need counting are spread across the router, the
# critic, the memory extractor, the synthesizer, the titler and the summarizer, none of
# which share a call path. asyncio hands each task a *copy of the context*, so a mutable
# object stored here before the tasks are spawned is the same object in all of them:
# parallel agents and their peer calls accumulate into one turn's total without passing
# anything down. Set it once per turn; read it once the turn is done.


@dataclass
class TokenUsage:
    """Input/output/cached token counts for one turn, across every model call it made."""

    input: int = 0
    output: int = 0
    cached_input: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output

    def add(self, other: TokenUsage) -> None:
        self.input += other.input
        self.output += other.output
        self.cached_input += other.cached_input
        self.calls += other.calls

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input,
            "output_tokens": self.output,
            "cached_input_tokens": self.cached_input,
            "total_tokens": self.total,
            "model_calls": self.calls,
        }


def from_message(message: Any) -> TokenUsage:
    """Read one model reply's usage_metadata into a TokenUsage (zeros when absent)."""
    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        return TokenUsage()
    details = usage.get("input_token_details") or {}
    return TokenUsage(
        input=int(usage.get("input_tokens") or 0),
        output=int(usage.get("output_tokens") or 0),
        # cache_read is a *subset* of input_tokens, not an addition to it: the discount
        # applies to these, the rest bill at full rate.
        cached_input=int(details.get("cache_read") or 0),
        calls=1,
    )


_current: contextvars.ContextVar[TokenUsage | None] = contextvars.ContextVar(
    "loom_token_usage", default=None
)


def start_turn() -> TokenUsage:
    """Begin accounting for a turn and return the accumulator the caller should read."""
    usage = TokenUsage()
    _current.set(usage)
    return usage


def record(message: Any) -> None:
    """Fold one model reply into the current turn's total. No-op outside a turn."""
    accumulator = _current.get()
    if accumulator is not None:
        accumulator.add(from_message(message))


async def structured(model: Any, schema: Any, prompt: Any) -> Any:
    """Run a structured-output call, count it, and return the parsed value.

    with_structured_output normally hands back only the parsed object, which carries no
    usage_metadata, so every routing, critique, extraction and consolidation call was
    unbillable by construction. include_raw keeps the underlying message alongside the
    parse. Returns None when the model answered without calling the schema tool, which
    is the same signal the plain form gives.
    """
    result = await model.with_structured_output(schema, include_raw=True).ainvoke(prompt)
    record(result.get("raw"))
    return result.get("parsed")


def record_all(messages: Any) -> TokenUsage:
    """Fold every model reply in an agent's message list in, and return their subtotal.

    Agent runs need both: the turn-level accumulator for cost, and their own subtotal
    for the per-agent trace the UI shows.
    """
    subtotal = TokenUsage()
    for message in messages or ():
        subtotal.add(from_message(message))
    accumulator = _current.get()
    if accumulator is not None:
        accumulator.add(subtotal)
    return subtotal
