from typing import Any, TypedDict


class ConversationState(TypedDict):
    messages: list[dict[str, Any]]
    conversation_id: str
    owner_id: str | None  # memory/persistence scope; None disables auto-memory for the turn

    current_agents: list[str]       # agents chosen by the router for this turn (one or more)
    routing_confidence: float | None  # overall router confidence for this turn
    routing_reason: str | None
    query_category: str | None  # coarse intent label for performance scoring

    agent_response: str | None
    tool_calls: list[dict[str, Any]]

    eval_result: dict[str, Any] | None
    eval_feedback: str | None  # critic feedback fed back to the agent on a retry
    retry_count: int
    max_retries: int

    session_summary: str | None
    auto_memory_hints: list[str]
    memory_writes: list[dict[str, Any]]

    metadata: dict[str, Any]