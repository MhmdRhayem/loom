# multi-agent-framework

A reusable core for multi-agent LLM assistants: an LLM **router** picks one agent per
turn, agents are **declarative YAML** (not Python subclasses), and tools are **plain
functions**. The framework (`src/multi_agent_framework/`) imports nothing from `demo/` —
you build your own assistant by filling **three seams**. A 7-agent e-commerce demo ships
as a worked example. Depth: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

One `/chat` turn runs a LangGraph pipeline:
`load_memory → route → (execute_agent | coordinate) → evaluate → save_memory`, with a
bounded retry on a failing evaluation. Multi-part requests fan out to a coordinator, and
any agent can delegate to a peer mid-task.

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

Wired: dynamic routing + agent execution, cross-session auto-memory, conversation/turn
persistence, a sampled-critic evaluator with one bounded retry, a multi-agent coordinator
with guarded peer delegation + approval gates, and adaptive learning (per-agent scoring,
Thompson routing off by default, Layer 4 memory consolidation). Reserved (built, not yet
called): most of Redis and the evaluation policy stage. See
[ARCHITECTURE.md](ARCHITECTURE.md#status) for the line-by-line list.
