"""Phase 6 — Thompson-sampling agent selection.

Models each agent as ``Beta(successes + 1, failures + 1)`` for a query category, samples
once per agent, and picks the highest draw — balancing exploiting the best-known agent with
exploring uncertain ones. Off by default (``settings.routing_strategy``); when no agent has
any data for the category, :func:`pick_agent` returns ``None`` so the caller keeps the LLM
router's choice.
"""
from __future__ import annotations

import random
from collections.abc import Sequence

from multi_agent_framework.storage.repositories.performance import PerformanceRepository


def thompson_select(stats: dict[str, tuple[int, int]]) -> str | None:
    """Pick an arm by Thompson sampling. ``stats`` maps name -> (successes, failures)."""
    best: str | None = None
    best_sample = -1.0
    for name, (successes, failures) in stats.items():
        sample = random.betavariate(successes + 1, failures + 1)
        if sample > best_sample:
            best, best_sample = name, sample
    return best


async def pick_agent(perf_repo: PerformanceRepository, candidates: Sequence[str], category: str) -> str | None:
    """Thompson-pick among ``candidates`` for ``category``; ``None`` if none have any data."""
    stats: dict[str, tuple[int, int]] = {}
    has_data = False
    for name in candidates:
        row = await perf_repo.get_agent_performance(name, category)
        successes, failures = (row.successes, row.failures) if row else (0, 0)
        if successes + failures > 0:
            has_data = True
        stats[name] = (successes, failures)
    if not has_data:
        return None  # nothing learned yet -> let the caller keep the LLM router's pick
    return thompson_select(stats)
