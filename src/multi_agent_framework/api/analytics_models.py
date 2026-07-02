from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OverviewResponse(BaseModel):
    conversations: int = 0
    turns: int = 0
    avg_eval_score: float | None = None
    avg_latency_ms: float | None = None
    eval_pass_rate: float | None = None
    retry_rate: float | None = None
    total_memories: int = 0
    dream_runs: int = 0


class AgentTurnStats(BaseModel):
    agent: str
    turns: int
    avg_eval_score: float | None = None
    passes: int = 0
    fails: int = 0
    avg_latency_ms: float | None = None


class AgentPerfRow(BaseModel):
    agent: str
    category: str
    score: float | None = None
    successes: int = 0
    failures: int = 0


class AgentAnalyticsResponse(BaseModel):
    agents: list[AgentTurnStats] = Field(default_factory=list)
    performance: list[AgentPerfRow] = Field(default_factory=list)


class DistributionSlice(BaseModel):
    key: str
    count: int


class RoutingAnalyticsResponse(BaseModel):
    category_distribution: list[DistributionSlice] = Field(default_factory=list)
    agent_distribution: list[DistributionSlice] = Field(default_factory=list)
    avg_routing_confidence: float | None = None


class TimePoint(BaseModel):
    bucket: str | None = None
    turns: int = 0
    avg_eval_score: float | None = None
    avg_latency_ms: float | None = None


class TimeseriesResponse(BaseModel):
    points: list[TimePoint] = Field(default_factory=list)


class MemoryCount(BaseModel):
    owner_id: str
    count: int


class DreamRunInfo(BaseModel):
    owner_id: str
    sessions_consolidated: int
    memories_merged: int
    memories_pruned: int
    duration_ms: int
    started_at: datetime


class MemoryAnalyticsResponse(BaseModel):
    memory_counts: list[MemoryCount] = Field(default_factory=list)
    dream_runs: list[DreamRunInfo] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    id: str
    owner_id: str
    created_at: datetime
    status: str
    total_turns: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary] = Field(default_factory=list)


class TurnDetail(BaseModel):
    turn_number: int
    user_message: str
    agent_name: str | None = None
    query_category: str | None = None
    routing_confidence: float | None = None
    agent_response: str | None = None
    eval_score: float | None = None
    retry_count: int = 0
    model_tier: str | None = None
    latency_ms: int | None = None
    created_at: datetime


class TurnAgentInfo(BaseModel):
    turn_number: int
    agent_name: str
    model_tier: str | None = None
    eval_score: float | None = None
    eval_pass: bool | None = None


class ConversationDetailResponse(BaseModel):
    conversation: ConversationSummary
    turns: list[TurnDetail] = Field(default_factory=list)
    turn_agents: list[TurnAgentInfo] = Field(default_factory=list)
