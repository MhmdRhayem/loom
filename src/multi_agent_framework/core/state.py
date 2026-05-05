from typing import Any, Literal, TypedDict

ExecutionModel = Literal["fork", "teammate", "worktree"]


class ConversationState(TypedDict):
    messages: list[dict[str, Any]]
    conversation_id: str

    current_agent: str | None
    routing_scores: dict[str, float]
    routing_reason: str | None

    agent_response: str | None
    tool_calls: list[dict[str, Any]]

    eval_result: dict[str, Any] | None
    retry_count: int
    max_retries: int

    session_summary: str | None
    auto_memory_hints: list[str]
    memory_writes: list[dict[str, Any]]

    execution_model: ExecutionModel
    spawned_agents: list[str]
    coordinator_mode: bool
    approval_queue: list[dict[str, Any]]

    metadata: dict[str, Any]