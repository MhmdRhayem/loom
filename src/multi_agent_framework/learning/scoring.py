"""Phase 6 — per-(agent, category) performance scoring via an exponential moving average.

After each turn we fold the reward into the agent's EMA for the query category and bump its
success/failure counts (the counts feed Thompson sampling). Stored in ``agent_performance``.
Runs live, fail-soft, never blocks the response. A ``None`` reward (sampled-out turn) is a
no-op.
"""
from __future__ import annotations

import logging

from multi_agent_framework.storage.repositories.performance import PerformanceRepository

logger = logging.getLogger(__name__)

_EMA_ALPHA = 0.05      # weight on the newest reward
_DEFAULT_SCORE = 0.5   # prior before any data


async def record_score(perf_repo: PerformanceRepository, agent: str, category: str, reward: float | None) -> None:
    """Fold one reward into ``agent``'s EMA score for ``category``. Fail-silent."""
    if reward is None or not agent:
        return
    try:
        existing = await perf_repo.get_agent_performance(agent, category)
        old = float(existing.score) if existing else _DEFAULT_SCORE
        new = round((1.0 - _EMA_ALPHA) * old + _EMA_ALPHA * reward, 4)
        passed = reward >= 0.5
        await perf_repo.upsert_agent_performance(
            agent,
            category,
            new,
            success_delta=1 if passed else 0,
            failure_delta=0 if passed else 1,
        )
    except Exception:  # noqa: BLE001 - learning must never break the response path
        logger.warning("scoring failed for %s/%s", agent, category, exc_info=True)
