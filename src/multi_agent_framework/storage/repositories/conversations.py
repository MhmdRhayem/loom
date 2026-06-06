from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import Conversation, ConversationTurn


@dataclass
class TurnRecord:
    conversation_id: UUID
    turn_number: int
    user_message: str
    agent_name: str | None = None
    routing_confidence: float | None = None
    agent_response: str | None = None
    eval_score: float | None = None
    retry_count: int = 0
    execution_model: str | None = None
    model_tier: str | None = None
    latency_ms: int | None = None
    tokens_used: int | None = None
    cost: Decimal | float | None = None
    cache_hit: bool = False


class ConversationRepository(Repository):
    """Read/write operations for conversations and their turns."""

    async def create_conversation(self, owner_id: str, status: str = "active") -> UUID:
        async with self._session() as session, session.begin():
            conv = Conversation(owner_id=owner_id, status=status)
            session.add(conv)
            await session.flush()
            return conv.id

    async def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        async with self._session() as session:
            return await session.get(Conversation, conversation_id)

    async def insert_turn(self, turn: TurnRecord) -> int:
        async with self._session() as session, session.begin():
            row = ConversationTurn(**turn.__dict__)
            session.add(row)
            await session.flush()

            tokens = turn.tokens_used or 0
            cost = Decimal(str(turn.cost)) if turn.cost is not None else Decimal("0")
            await session.execute(
                update(Conversation)
                .where(Conversation.id == turn.conversation_id)
                .values(
                    total_turns=Conversation.total_turns + 1,
                    total_tokens=Conversation.total_tokens + tokens,
                    total_cost=Conversation.total_cost + cost,
                )
            )
            return row.id

    async def get_turns(self, conversation_id: UUID) -> list[ConversationTurn]:
        async with self._session() as session:
            stmt = select(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id).order_by(ConversationTurn.turn_number.asc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_conversations(self, owner_id: str, limit: int = 50) -> list[Conversation]:
        """An owner's conversations, newest first (for history/dashboard)."""
        stmt = select(Conversation).where(Conversation.owner_id == owner_id).order_by(Conversation.created_at.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def set_conversation_status(self, conversation_id: UUID, status: str) -> None:
        """Change a conversation's status (e.g. 'active' -> 'completed')."""
        async with self._session() as session, session.begin():
            await session.execute(update(Conversation).where(Conversation.id == conversation_id).values(status=status))

    async def increment_compaction(self, conversation_id: UUID) -> None:
        """Record that the conversation's context was compacted once more (Layer 2)."""
        async with self._session() as session, session.begin():
            await session.execute(
                update(Conversation).where(Conversation.id == conversation_id).values(compaction_count=Conversation.compaction_count + 1)
            )
