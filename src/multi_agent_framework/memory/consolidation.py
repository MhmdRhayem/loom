"""Memory Layer 4 — consolidation ("dreaming").

Periodically tidies an owner's stored memories: merge duplicates / near-duplicates into one
clear fact, and prune stale or trivial ones. An LLM proposes the plan; the code applies it
through the memory repo. Runs on a trigger (enough memories AND long enough since the last
run), logs the outcome to ``dream_runs``, and is fully fail-soft — on any error memory is
just left as-is. It only ever reads/writes memory, never the catalog or the user.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from multi_agent_framework.core.config import Settings
from multi_agent_framework.storage.repositories.dreams import DreamRepository
from multi_agent_framework.storage.repositories.memory import MemoryRepository

logger = logging.getLogger(__name__)


class Merge(BaseModel):
    topic: str = Field(description="The single topic key to keep.")
    content: str = Field(description="The merged, deduplicated fact.")
    drop_topics: list[str] = Field(default_factory=list, description="Other topic keys now redundant, to delete.")


class DreamPlan(BaseModel):
    merges: list[Merge] = Field(default_factory=list, description="Groups of duplicate memories to merge.")
    prune_topics: list[str] = Field(default_factory=list, description="Stale/trivial topic keys to delete outright.")


async def should_dream(memory_repo: MemoryRepository, dream_repo: DreamRepository, owner_id: str, settings: Settings) -> bool:
    """True if the owner has enough memories and it has been long enough since the last run."""
    try:
        if await memory_repo.count_auto_memory(owner_id) < settings.dream_min_memories:
            return False
        last = await dream_repo.get_last_dream_run(owner_id)
        if last is None:
            return True
        return datetime.now(timezone.utc) - last.started_at >= timedelta(hours=settings.dream_interval_hours)
    except Exception:  # noqa: BLE001
        logger.warning("should_dream check failed for %s", owner_id, exc_info=True)
        return False


async def consolidate(memory_repo: MemoryRepository, dream_repo: DreamRepository, owner_id: str, settings: Settings) -> dict[str, Any]:
    """Review + tidy an owner's memories (merge + prune), log a dream run. Fail-soft."""
    started = datetime.now(timezone.utc)
    clock = time.perf_counter()
    merged = pruned = 0
    try:
        rows = await memory_repo.load_auto_memory(owner_id, limit=500)
        by_topic = {r.topic: r for r in rows}
        if rows:
            catalogue = "\n".join(f"- {r.topic}: {r.content}" for r in rows)
            model = init_chat_model(settings.model_id_for_tier("standard"), model_provider=settings.default_provider)
            system = (
                "Tidy this list of remembered facts about one user. Merge duplicates or near-duplicates "
                "into a single clear fact (and list the now-redundant topic keys to drop), and list stale, "
                "trivial, or contradictory topic keys to prune. Only use topic keys shown."
            )
            plan: DreamPlan = await model.with_structured_output(DreamPlan).ainvoke(
                [{"role": "system", "content": system}, {"role": "user", "content": catalogue}]
            )
            for merge in plan.merges:
                keep = by_topic.get(merge.topic)
                if keep is None:
                    continue
                await memory_repo.update_auto_memory(keep.id, content=merge.content)
                for drop in merge.drop_topics:
                    row = by_topic.get(drop)
                    if row is not None and drop != merge.topic:
                        await memory_repo.delete_auto_memory(row.id)
                        merged += 1
            for topic in plan.prune_topics:
                row = by_topic.get(topic)
                if row is not None:
                    await memory_repo.delete_auto_memory(row.id)
                    pruned += 1
    except Exception:  # noqa: BLE001 - dreaming is best-effort; leave memory as-is on error
        logger.warning("consolidation failed for %s", owner_id, exc_info=True)

    duration_ms = int((time.perf_counter() - clock) * 1000)
    try:
        await dream_repo.log_dream_run(
            owner_id,
            sessions_consolidated=0,
            memories_merged=merged,
            memories_pruned=pruned,
            duration_ms=duration_ms,
            started_at=started,
        )
    except Exception:  # noqa: BLE001
        logger.warning("dream_run logging failed for %s", owner_id, exc_info=True)

    return {"merged": merged, "pruned": pruned, "duration_ms": duration_ms}
