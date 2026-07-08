# Loom

*Weave many specialist LLM agents into one answer.*

A reusable core for multi-agent LLM assistants: an LLM **router** picks one agent per
turn, agents are **declarative YAML** (not Python subclasses), and tools are **plain
functions**. The framework (`src/multi_agent_framework/`) imports nothing from `demo/` —
you build your own assistant by filling **three seams**. An 8-agent e-commerce demo with
a React frontend ships as a worked example. Depth: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

One `/chat` turn runs a LangGraph pipeline:
`load_memory → route → execute_agents → evaluate → save_memory`, with a
bounded retry on a failing evaluation. The router can pick more than one agent; they run in
parallel and their answers are synthesized into one reply. Any agent can also call a peer
mid-task via an auto-generated `ask_<name>` tool.

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
demo/shopping_assistant/     a worked consumer: 8-agent e-commerce assistant + real shop DB
frontend/                    React + Vite UI for the demo (login, chat, storefront, dashboard)
scripts/init_db.py           create the framework schema from the ORM models
```

## Quickstart

Prereqs: Python 3.12+, Node.js 20+, Docker. Run from the repo root.

### 1. Backend (API on port 8000)

```bash
python -m venv .venv
.venv\Scripts\activate                 # POSIX: . .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d                   # Postgres :5433, Redis :6379
python scripts/init_db.py              # framework schema
python -m demo.shopping_assistant.seed # demo "shop" data (safe to re-run; also migrates)

# provider key in .env — Anthropic is the default
#   ANTHROPIC_API_KEY=...
#   OpenAI instead: OPENAI_API_KEY=...  and  DEFAULT_PROVIDER=openai
python -m uvicorn --env-file .env demo.shopping_assistant.app:app --reload
```

### 2. Frontend (UI on port 5173)

In a second terminal:

```bash
cd frontend
npm install                            # first time only
npm run dev                            # -> http://localhost:5173
```

Open **http://localhost:5173** and sign in — the login page lists the demo accounts with
their passwords (client `mohammad@example.com`, merchant `merchant@example.com`, admin
`admin@example.com`; one click fills the form). The backend must be running on port 8000
(CORS already allows the Vite dev server). If the API runs elsewhere, set `VITE_API_URL`.

### Raw API

The demo requires a login — get a token, then chat (the account from the token scopes
orders, conversations, and memory):

```bash
curl -X POST localhost:8000/auth/login -H "content-type: application/json" \
  -d '{"email":"mohammad@example.com","password":"mohammad123"}'
# -> {"token":"...", ...}
curl -X POST localhost:8000/chat -H "content-type: application/json" \
  -H "authorization: Bearer <token>" -d '{"message":"where is my order ORD-1005?"}'
```

`GET /health` reports backend status; the app boots fail-soft even if Postgres/Redis are
down. The full manual test script (all scenarios, per-role walkthroughs) is in
[DEMO_TESTING.md](DEMO_TESTING.md).

## Status

Wired: dynamic routing + agent execution, cross-session auto-memory, conversation/turn
persistence, a sampled-critic evaluator with one bounded retry, multi-agent routing
(one-or-more agents per turn, run in parallel + synthesized) with peer delegation (agents
call each other via `ask_<name>` tools), adaptive learning (per-(agent, category)
performance scoring, Layer 4 memory consolidation), and feature flags (`ENABLE_*`
per subsystem). Reserved (built, not yet called): most of Redis and the evaluation policy
stage. See
[ARCHITECTURE.md](ARCHITECTURE.md#status) for the line-by-line list.
