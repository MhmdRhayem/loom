from typing import Any

SYSTEM_REMINDER_TAG = "system-reminder"


def message_text(message: Any) -> str:
    """Coerce a model message's content to plain text (handles a string or content-block list)."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    return str(content)


def build_system_prompt(agent_definition: dict[str, Any], static_intro: str = "") -> str:
    name = agent_definition.get("name", "agent")
    description = agent_definition.get("description", "")
    capabilities = agent_definition.get("capabilities", [])

    parts: list[str] = []
    if static_intro:
        parts.append(static_intro.strip())
    parts.append(f"You are the {name} agent.")
    if description:
        parts.append(f"Description: {description}")
    if capabilities:
        cap_lines = "\n".join(f"- {cap}" for cap in capabilities)
        parts.append(f"Capabilities:\n{cap_lines}")
    parts.append(
        "Operating rules:\n"
        "- Use tools when needed; never fabricate tool results.\n"
        "- On any error, return a structured error message and continue.\n"
        "- Stay within your declared capabilities; defer out-of-scope work."
    )
    return "\n\n".join(parts)


def build_messages(
    conversation_messages: list[dict[str, Any]],
    agent_definition: dict[str, Any],
    memory_hints: list[str] | None = None,
) -> list[dict[str, Any]]:
    context_block = _build_context_block(agent_definition, memory_hints or [])
    if context_block is None:
        return list(conversation_messages)
    return [{"role": "user", "content": context_block}, *conversation_messages]


def _build_context_block(agent_definition: dict[str, Any], memory_hints: list[str]) -> str | None:
    body_parts: list[str] = []

    tools = agent_definition.get("tools", [])
    if tools:
        body_parts.append("Available tools: " + ", ".join(tools))

    if memory_hints:
        hint_lines = "\n".join(f"- {hint}" for hint in memory_hints)
        body_parts.append(
            "Memory hints (treat as hints, not ground truth — verify against live state):\n"
            + hint_lines
        )

    if not body_parts:
        return None

    body = "\n".join(body_parts)
    return f"<{SYSTEM_REMINDER_TAG}>\n{body}\n</{SYSTEM_REMINDER_TAG}>"
