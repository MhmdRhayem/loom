# multi-agent-framework

A reusable core for multi-agent LLM assistants: an LLM **router** picks one agent per
turn, agents are **declarative YAML** (not Python subclasses), and tools are **plain
functions**. The framework (`src/multi_agent_framework/`) imports nothing from `demo/` —
you build your own assistant by filling **three seams**. A 7-agent e-commerce demo ships
as a worked example. Depth: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

One `/chat` turn runs a fixed LangGraph pipeline:
`load_memory → route → execute_agent → evaluate → save_memory`. Today `route` +
`execute_agent` and cross-session memory (`load`/`save`) are wired; `evaluate` is a
pass-through stub.

## The three seams (how you reuse it)

1. **Agents** — one YAML file per agent in a directory → `AgentRegistry.from_directory(path)`.
2. **Tools** — plain functions + a `get_tools(names) -> [callables]` map, passed as the `tool_provider`.
3. **Composition root** — `create_app(registry, tool_provider, fallback_agent=...)` → a FastAPI app.

```python
# myapp/app.py — your whole composition root
from pathlib import Path
from multi_agent_framework.agents.registry import AgentRegistry
from multi_agent_framework.api.main import create_app
from .tools import get_tools

registry = AgentRegistry.from_directory(Path(__file__).parent / "definitions")
app = create_app(registry, get_tools, fallback_agent="concierge")  # your own catch-all agent
```

## Layout

```
src/multi_agent_framework/   the framework (reusable; no app-specific knowledge)
demo/shopping_assistant/     a worked consumer: 7-agent e-commerce assistant + real shop DB
scripts/init_db.py           create the framework schema from the ORM models
```

## Quickstart

Prereqs: Python 3.12+, Docker. Run from the repo root.

```bash
python -m venv .venv
.venv\Scripts\activate                 # POSIX: . .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d                   # Postgres :5433, Redis :6379
python scripts/init_db.py              # framework schema
python -m demo.shopping_assistant.seed # demo "shop" data (run once)

# provider key in .env — Anthropic is the default
#   ANTHROPIC_API_KEY=...
#   OpenAI instead: OPENAI_API_KEY=...  and  DEFAULT_PROVIDER=openai
python -m uvicorn --env-file .env demo.shopping_assistant.app:app --reload
```

```bash
curl -X POST localhost:8000/chat -H "content-type: application/json" \
  -d '{"message":"where is my order ORD-1001?","owner_id":"shopper:alice@example.com"}'
```

`owner_id` scopes cross-session memory (omit it and the turn just runs without memory).
`GET /health` reports backend status; the app boots fail-soft even if Postgres/Redis are down.

## Status

Wired: dynamic routing + agent execution, cross-session auto-memory, and conversation/turn
persistence. Reserved (built, not yet called): agent performance, dream consolidation,
most of Redis, and the `evaluate` node. See
[ARCHITECTURE.md](ARCHITECTURE.md#status) for the line-by-line wired-vs-reserved list.
