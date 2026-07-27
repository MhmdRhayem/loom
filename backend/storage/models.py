from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    # Composite, not owner_id alone: the only query here is "this owner's conversations,
    # newest first, limit N", and the second column lets Postgres walk the B-tree
    # backwards instead of sorting every row the owner has.
    __table_args__ = (Index("ix_conversations_owner_created", "owner_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)  # AI-generated after the first turn
    # Layer 3: rolling summary of the turns that no longer fit the context window,
    # and how many turns it covers (so it's only re-generated when new turns age out).
    summary: Mapped[str | None] = mapped_column(Text)
    summarized_turns: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    total_turns: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    compaction_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    turns: Mapped[list[ConversationTurn]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        # The unique constraint's index leads on conversation_id, so it also serves
        # every by-conversation lookup — no separate single-column index needed.
        # Nothing filters or groups on agent_name here either: every per-agent
        # aggregate reads turn_agents, so that column is deliberately unindexed too.
        UniqueConstraint("conversation_id", "turn_number", name="uq_conversation_turn"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(Text)
    query_category: Mapped[str | None] = mapped_column(Text)
    routing_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    agent_response: Mapped[str | None] = mapped_column(Text)
    eval_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model_tier: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    conversation: Mapped[Conversation] = relationship(back_populates="turns")
    agents: Mapped[list[TurnAgent]] = relationship(
        back_populates="turn", cascade="all, delete-orphan", passive_deletes=True
    )


class TurnAgent(Base):
    """One agent's contribution to a single turn (one row per participating agent).

    The conversation_turns row holds the user-facing reply (a single agent's answer, or the
    synthesis of several) and the overall verdict. This child table records what each agent
    that ran contributed, so telemetry and feedback can attribute per agent instead of
    collapsing a multi-agent turn onto its first agent.
    """

    __tablename__ = "turn_agents"
    __table_args__ = (
        Index("ix_turn_agents_turn_id", "turn_id"),
        Index("ix_turn_agents_agent_name", "agent_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    turn_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversation_turns.id", ondelete="CASCADE"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    model_tier: Mapped[str | None] = mapped_column(Text)
    eval_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    eval_pass: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    turn: Mapped[ConversationTurn] = relationship(back_populates="agents")


class AgentPerformance(Base):
    __tablename__ = "agent_performance"
    __table_args__ = (
        PrimaryKeyConstraint("agent_name", "query_category", name="pk_agent_performance"),
    )

    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    query_category: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, server_default="0.5")
    successes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AutoMemory(Base):
    __tablename__ = "auto_memory"
    # One row per (owner, topic) — the write path is an upsert keyed on this, and the
    # backing index (leads on owner_id) also serves owner-only lookups.
    __table_args__ = (UniqueConstraint("owner_id", "topic", name="uq_auto_memory_owner_topic"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False, server_default="0.5")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DreamRun(Base):
    __tablename__ = "dream_runs"
    __table_args__ = (Index("ix_dream_runs_owner", "owner_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    memories_merged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    memories_pruned: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
