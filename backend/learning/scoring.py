from __future__ import annotations

import logging

from backend.storage.repositories.performance import PerformanceRepository

logger = logging.getLogger(__name__)

_EMA_ALPHA = 0.05  # weight on the newest reward
_DEFAULT_SCORE = 0.5  # prior before any data


async def record_score(
    perf_repo: PerformanceRepository, agent: str, category: str, reward: float | None
) -> None:
    """Fold one reward into the agent's EMA score for this category. Never raises.

    The EMA math runs inside the repository's atomic upsert, so concurrent turns
    compose their rewards instead of overwriting each other.
    """
    if reward is None or not agent:
        return
    try:
        passed = reward >= 0.5
        await perf_repo.record_reward(
            agent,
            category,
            reward,
            alpha=_EMA_ALPHA,
            prior=_DEFAULT_SCORE,
            success_delta=1 if passed else 0,
            failure_delta=0 if passed else 1,
        )
    except Exception:  # noqa: BLE001 - learning must never break the response path
        logger.warning("scoring failed for %s/%s", agent, category, exc_info=True)
