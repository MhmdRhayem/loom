from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.storage.base import Repository
from backend.storage.models import Conversation, ConversationTurn, TurnAgent


@dataclass
class TurnAgentRecord:
    """One participating agent's per-turn contribution (a turn_agents row)."""

    agent_name: str
    model_tier: str | None = None
    eval_score: float | None = None
    eval_pass: bool | None = None


class ConversationRepository(Repository):
    """Read/write operations for conversations and their turns."""

    async def get_conversation(self, conversation_id: UUID) -> Conversation | None:
        async with self._session() as session:
            return await session.get(Conversation, conversation_id)

    async def record_turn(
        self,
        *,
        conversation_id: UUID,
        owner_id: str,
        user_message: str,
        agent_name: str | None = None,
        query_category: str | None = None,
        routing_confidence: float | None = None,
        agent_response: str | None = None,
        eval_score: float | None = None,
        retry_count: int = 0,
        model_tier: str | None = None,
        latency_ms: int | None = None,
        tokens_used: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        model_calls: int | None = None,
        title: str | None = None,
        agents: list[TurnAgentRecord] | None = None,
    ) -> int:
        """Ensure the conversation exists, then append a turn with an auto-assigned number.

        One transaction end to end: the conversation row is locked while the turn
        number is computed, so two concurrent turns on the same conversation can't
        collide on uq_conversation_turn (and a failure can't leave a turn without
        its per-agent rows or counter bump). agents is the per-agent breakdown (one
        row per agent that ran); pass it for every turn so single- and multi-agent
        turns are recorded the same way. Returns the turn id.
        """
        async with self._session() as session, session.begin():
            await session.execute(
                pg_insert(Conversation)
                .values(id=conversation_id, owner_id=owner_id, status="active", title=title)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            # Serialize concurrent turns on this conversation while we number the turn.
            await session.execute(
                select(Conversation.id).where(Conversation.id == conversation_id).with_for_update()
            )
            # max + 1, not count + 1: counting reuses a number if a turn is ever deleted,
            # which uq_conversation_turn would then reject. It is also an index scan.
            highest = await session.scalar(
                select(func.max(ConversationTurn.turn_number)).where(
                    ConversationTurn.conversation_id == conversation_id
                )
            )
            row = ConversationTurn(
                conversation_id=conversation_id,
                turn_number=int(highest or 0) + 1,
                user_message=user_message,
                agent_name=agent_name,
                query_category=query_category,
                routing_confidence=routing_confidence,
                agent_response=agent_response,
                eval_score=eval_score,
                retry_count=retry_count,
                model_tier=model_tier,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                model_calls=model_calls,
            )
            session.add(row)
            await session.flush()
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    total_turns=Conversation.total_turns + 1,
                    total_tokens=Conversation.total_tokens + (tokens_used or 0),
                )
            )
            session.add_all(
                TurnAgent(
                    turn_id=row.id,
                    agent_name=a.agent_name,
                    model_tier=a.model_tier,
                    eval_score=a.eval_score,
                    eval_pass=a.eval_pass,
                )
                for a in agents or []
            )
            return row.id

    async def get_turn_agents(self, turn_id: int) -> list[TurnAgent]:
        """The per-agent rows for a turn, in insertion order (router's chosen order)."""
        async with self._session() as session:
            stmt = (
                select(TurnAgent).where(TurnAgent.turn_id == turn_id).order_by(TurnAgent.id.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_turns(self, conversation_id: UUID) -> list[ConversationTurn]:
        async with self._session() as session:
            stmt = (
                select(ConversationTurn)
                .where(ConversationTurn.conversation_id == conversation_id)
                .order_by(ConversationTurn.turn_number.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete_conversation(self, conversation_id: UUID) -> None:
        """Delete a conversation; its turns and per-agent rows cascade at the DB level."""
        async with self._session() as session, session.begin():
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))

    async def set_title(self, conversation_id: UUID, title: str) -> None:
        """Store the conversation's display title (generated after the first turn)."""
        async with self._session() as session, session.begin():
            await session.execute(
                update(Conversation).where(Conversation.id == conversation_id).values(title=title)
            )

    async def set_summary(self, conversation_id: UUID, summary: str, covered_turns: int) -> None:
        """Store the Layer-3 rolling summary and how many turns it covers; bumps compaction."""
        async with self._session() as session, session.begin():
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    summary=summary,
                    summarized_turns=covered_turns,
                    compaction_count=Conversation.compaction_count + 1,
                )
            )
