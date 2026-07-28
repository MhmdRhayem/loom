from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from backend.core import usage
from backend.core.config import Settings
from backend.storage.repositories.dreams import DreamRepository
from backend.storage.repositories.memory import MemoryRepository

logger = logging.getLogger(__name__)


class Merge(BaseModel):
    topic: str = Field(description="The single topic key to keep.")
    content: str = Field(description="The merged, deduplicated fact.")
    drop_topics: list[str] = Field(
        default_factory=list, description="Other topic keys now redundant, to delete."
    )


class DreamPlan(BaseModel):
    merges: list[Merge] = Field(
        default_factory=list, description="Groups of duplicate memories to merge."
    )
    prune_topics: list[str] = Field(
        default_factory=list, description="Stale/trivial topic keys to delete outright."
    )


async def should_dream(
    memory_repo: MemoryRepository, dream_repo: DreamRepository, owner_id: str, settings: Settings
) -> bool:
    """True if the owner has enough memories and it has been long enough since the last run."""
    try:
        if await memory_repo.count_auto_memory(owner_id) < settings.dream_min_memories:
            return False
        last = await dream_repo.get_last_dream_run(owner_id)
        if last is None:
            return True
        return datetime.now(timezone.utc) - last.started_at >= timedelta(
            hours=settings.dream_interval_hours
        )
    except Exception:  # noqa: BLE001 - a failed due-check just means "not due"
        logger.warning("should_dream check failed for %s", owner_id, exc_info=True)
        return False


async def consolidate(
    memory_repo: MemoryRepository, dream_repo: DreamRepository, owner_id: str, settings: Settings
) -> dict[str, Any]:
    """Tidy an owner's memories (merge and prune) and log a dream run. Never raises."""
    started = datetime.now(timezone.utc)
    clock = time.perf_counter()
    merged = pruned = 0
    try:
        rows = await memory_repo.load_auto_memory(owner_id, limit=500)
        by_topic = {r.topic: r for r in rows}
        if rows:
            catalogue = "\n".join(f"- {r.topic}: {r.content}" for r in rows)
            model = init_chat_model(
                settings.model_id_for_tier("standard"), model_provider=settings.default_provider
            )
            system = (
                "Tidy this list of remembered facts about one user. Merge duplicates or near-duplicates "
                "into a single clear fact (and list the now-redundant topic keys to drop), and list stale, "
                "trivial, or contradictory topic keys to prune. Only use topic keys shown."
            )
            plan: DreamPlan | None = await usage.structured(
                model,
                DreamPlan,
                [{"role": "system", "content": system}, {"role": "user", "content": catalogue}],
            )
            if plan is None:
                raise ValueError("consolidation returned no plan")
            # The plan is LLM-generated, so the same topic can appear in several merges
            # or in both a merge and the prune list — track deletions so an overlapping
            # plan neither double-counts nor merges into an already-deleted row.
            deleted: set[Any] = set()
            for merge in plan.merges:
                keep = by_topic.get(merge.topic)
                if keep is None or keep.id in deleted:
                    continue
                await memory_repo.update_auto_memory(keep.id, content=merge.content)
                for drop in merge.drop_topics:
                    row = by_topic.get(drop)
                    if row is not None and drop != merge.topic and row.id not in deleted:
                        await memory_repo.delete_auto_memory(row.id)
                        deleted.add(row.id)
                        merged += 1
            for topic in plan.prune_topics:
                row = by_topic.get(topic)
                if row is not None and row.id not in deleted:
                    await memory_repo.delete_auto_memory(row.id)
                    deleted.add(row.id)
                    pruned += 1
    except Exception:  # noqa: BLE001 - dreaming is best-effort; leave memory as-is on error
        logger.warning("consolidation failed for %s", owner_id, exc_info=True)

    duration_ms = int((time.perf_counter() - clock) * 1000)
    try:
        await dream_repo.log_dream_run(
            owner_id,
            memories_merged=merged,
            memories_pruned=pruned,
            duration_ms=duration_ms,
            started_at=started,
        )
    except Exception:  # noqa: BLE001 - the tidy-up already happened; only the log is lost
        logger.warning("dream_run logging failed for %s", owner_id, exc_info=True)

    return {"merged": merged, "pruned": pruned, "duration_ms": duration_ms}
