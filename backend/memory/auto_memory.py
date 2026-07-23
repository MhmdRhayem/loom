from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from backend.core.config import Settings
from backend.storage.repositories.memory import MemoryRepository

logger = logging.getLogger(__name__)

# How many stored memories to surface per turn (newest first via the repository).
_MAX_HINTS = 20

_EXTRACT_INSTRUCTIONS = (
    "You extract DURABLE facts about the user that are worth remembering in future "
    "conversations. Record only stable, reusable facts: preferences, stable identity "
    "details the user states (e.g. name, contact details), and explicit corrections "
    "the user makes.\n"
    "Do NOT record transient or one-off details: the current question, the current "
    "status of some process, or anything that will be stale next session.\n"
    "For each fact give: a short stable topic key in snake_case (e.g. 'name', "
    "'preferred_language'), the fact in one short sentence, and a confidence in [0, 1].\n"
    "If there is nothing durable worth keeping, return an empty list."
)


class _Memory(BaseModel):
    topic: str = Field(description="Short stable snake_case key, e.g. 'preferred_size'.")
    content: str = Field(description="The durable fact in one short sentence.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="0-1 confidence this is durable and correct."
    )


class _Extraction(BaseModel):
    memories: list[_Memory] = Field(
        default_factory=list, description="Durable facts; empty if none."
    )


async def load_hints(memory: MemoryRepository, owner_id: str, limit: int = _MAX_HINTS) -> list[str]:
    """Return an owner's stored memories as "topic: content" hint strings. Returns [] on error."""
    try:
        rows = await memory.load_auto_memory(owner_id, limit=limit)
        return [f"{row.topic}: {row.content}" for row in rows]
    except Exception:  # noqa: BLE001 - memory is a hint; never break the turn
        logger.warning("auto-memory load failed for owner %s", owner_id, exc_info=True)
        return []


async def extract_and_upsert(
    memory: MemoryRepository,
    settings: Settings,
    owner_id: str,
    user_message: str,
    agent_response: str,
) -> int:
    """Pull durable facts from a turn and upsert them by topic; returns the count written."""
    try:
        model = init_chat_model(
            settings.model_id_for_tier("fast"), model_provider=settings.default_provider
        )
        prompt = [
            {"role": "system", "content": _EXTRACT_INSTRUCTIONS},
            {"role": "user", "content": f"User: {user_message}\nAssistant: {agent_response}"},
        ]
        extraction: _Extraction = await model.with_structured_output(_Extraction).ainvoke(prompt)
    except Exception:  # noqa: BLE001 - extraction is best-effort
        logger.warning("auto-memory extraction failed for owner %s", owner_id, exc_info=True)
        return 0

    written = 0
    for mem in extraction.memories:
        topic = mem.topic.strip().lower()
        content = mem.content.strip()
        if not topic or not content:
            continue
        try:
            await memory.upsert_auto_memory(owner_id, topic, content, confidence=mem.confidence)
            written += 1
        except Exception:  # noqa: BLE001 - one bad write must not lose the others
            logger.warning(
                "auto-memory upsert failed (owner=%s topic=%s)", owner_id, topic, exc_info=True
            )
    return written
