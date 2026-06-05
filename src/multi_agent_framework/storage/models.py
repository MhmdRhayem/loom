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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active"
    )
    total_turns: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    total_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    compaction_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    turns: Mapped[list["ConversationTurn"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "turn_number", name="uq_conversation_turn"
        ),
        Index("ix_turns_conversation_id", "conversation_id"),
        Index("ix_turns_agent_name", "agent_name"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), primary_key=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(Text)
    routing_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    agent_response: Mapped[str | None] = mapped_column(Text)
    eval_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    execution_model: Mapped[str | None] = mapped_column(Text)
    model_tier: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cache_hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    conversation: Mapped[Conversation] = relationship(back_populates="turns")


class AgentPerformance(Base):
    __tablename__ = "agent_performance"
    __table_args__ = (
        PrimaryKeyConstraint(
            "agent_name", "query_category", name="pk_agent_performance"
        ),
    )

    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    query_category: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default="0.5"
    )
    successes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class AutoMemory(Base):
    __tablename__ = "auto_memory"
    __table_args__ = (
        Index("ix_auto_memory_project", "project_id"),
        Index("ix_auto_memory_project_topic", "project_id", "topic"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default="0.5"
    )
    access_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DreamRun(Base):
    __tablename__ = "dream_runs"
    __table_args__ = (Index("ix_dream_runs_project", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    sessions_consolidated: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    memories_merged: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    memories_pruned: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
