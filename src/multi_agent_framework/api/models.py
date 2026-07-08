from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's message.")
    conversation_id: str | None = Field(
        default=None,
        description="Existing conversation to continue; a new one is created when omitted.",
    )
    owner_id: str | None = Field(
        default=None,
        description="Cross-session memory scope (e.g. 'shopper:42'). Omit to run the turn without memory.",
    )


class AgentRun(BaseModel):
    """One agent's contribution to a turn: its raw answer, tools called, tokens spent."""

    agent: str
    text: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    tokens: int = 0


class ChatResponse(BaseModel):
    conversation_id: str
    agent: str | None  # comma-joined agent names (kept for back-compat)
    response: str | None
    eval: dict | None = None
    # The orchestration trace, surfaced so a client can visualize the pipeline.
    current_agents: list[str] = Field(default_factory=list)
    routing_confidence: float | None = None
    routing_reason: str | None = None
    query_category: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    retry_count: int = 0


class HealthResponse(BaseModel):
    status: str
    components: dict[str, str]


class AgentInfo(BaseModel):
    """One agent's public profile, from its YAML definition (for the roster view)."""

    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    model: str  # tier: fast | standard | deep
    max_tokens: int
    eval_rubric: list[str] = Field(default_factory=list)
    judge_sample_rate: float
    fallback_agent: str
    memory_scope: str


class AgentsResponse(BaseModel):
    agents: list[AgentInfo] = Field(default_factory=list)
    fallback_agent: str | None = None  # roster-level catch-all the router falls back to


class FeedbackRequest(BaseModel):
    conversation_id: str = Field(..., description="The conversation to rate.")
    rating: str = Field(..., description="Thumbs rating: 'up' or 'down'.")
    comment: str | None = Field(default=None, description="Optional free-text feedback.")


class FeedbackResponse(BaseModel):
    recorded: bool
    agent: str | None = None


class DreamResponse(BaseModel):
    ran: bool
    merged: int
    pruned: int
