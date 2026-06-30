from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from multi_agent_framework.storage.base import Repository
from multi_agent_framework.storage.models import Conversation, ConversationTurn, TurnAgent


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


@dataclass
class TurnAgentRecord:
    """One participating agent's per-turn contribution (a ``turn_agents`` row)."""

    agent_name: str
    model_tier: str | None = None
    eval_score: float | None = None
    eval_pass: bool | None = None


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

    async def ensure_conversation(self, conversation_id: UUID, owner_id: str, status: str = "active") -> None:
        """Insert the conversation row under a known id if it does not exist (idempotent)."""
        stmt = pg_insert(Conversation).values(id=conversation_id, owner_id=owner_id, status=status).on_conflict_do_nothing(index_elements=["id"])
        async with self._session() as session, session.begin():
            await session.execute(stmt)

    async def next_turn_number(self, conversation_id: UUID) -> int:
        """1-based number for the next turn in this conversation."""
        async with self._session() as session:
            count = await session.scalar(select(func.count()).select_from(ConversationTurn).where(ConversationTurn.conversation_id == conversation_id))
            return int(count or 0) + 1

    async def record_turn(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        user_message: str,
        agent_name: str | None = None,
        routing_confidence: float | None = None,
        agent_response: str | None = None,
        eval_score: float | None = None,
        retry_count: int = 0,
        model_tier: str | None = None,
        latency_ms: int | None = None,
        agents: list[TurnAgentRecord] | None = None,
    ) -> int:
        """Ensure the conversation exists, then append a turn with an auto-assigned number.

        Convenience for the request path (ensure -> number -> insert); returns the turn id.
        ``agents`` is the per-agent breakdown (one row per agent that ran this turn); pass it
        for every turn so single- and multi-agent turns are recorded uniformly.
        """
        await self.ensure_conversation(conversation_id, owner_id)
        turn = TurnRecord(
            conversation_id=conversation_id,
            turn_number=await self.next_turn_number(conversation_id),
            user_message=user_message,
            agent_name=agent_name,
            routing_confidence=routing_confidence,
            agent_response=agent_response,
            eval_score=eval_score,
            retry_count=retry_count,
            model_tier=model_tier,
            latency_ms=latency_ms,
        )
        turn_id = await self.insert_turn(turn)
        if agents:
            await self.add_turn_agents(turn_id, agents)
        return turn_id

    async def add_turn_agents(self, turn_id: int, agents: list[TurnAgentRecord]) -> None:
        """Append the per-agent breakdown for a turn (one ``turn_agents`` row per agent)."""
        if not agents:
            return
        async with self._session() as session, session.begin():
            session.add_all(
                TurnAgent(
                    turn_id=turn_id,
                    agent_name=a.agent_name,
                    model_tier=a.model_tier,
                    eval_score=a.eval_score,
                    eval_pass=a.eval_pass,
                )
                for a in agents
            )

    async def get_turn_agents(self, turn_id: int) -> list[TurnAgent]:
        """The per-agent rows for a turn, in insertion order (router's chosen order)."""
        async with self._session() as session:
            stmt = select(TurnAgent).where(TurnAgent.turn_id == turn_id).order_by(TurnAgent.id.asc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

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
            await session.execute(update(Conversation).where(Conversation.id == conversation_id).values(compaction_count=Conversation.compaction_count + 1))
