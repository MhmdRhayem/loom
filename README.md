# Loom

*Weave many specialist LLM agents into one answer.*

Loom is an **e-commerce AI assistant**: shoppers ask in plain language and a team of
specialist agents handle product discovery, orders, returns, checkout, styling, account,
and support — one reply, no menus. It runs on a reusable multi-agent core: an LLM
**router** picks the right agent(s) per turn, agents are **declarative YAML** (not Python
subclasses), and tools are **plain functions**. The core (`backend/`) is provider-agnostic
(Anthropic or OpenAI) and holds no shop-specific knowledge; the storefront — agents, tools,
the shop database, and a React UI — lives in `demo/shopping_assistant/` and `frontend/`.
Depth: **[ARCHITECTURE.md](ARCHITECTURE.md)**.

One `/chat` turn runs a LangGraph pipeline:
`load_memory → route → execute_agents → evaluate → save_memory`, with a
bounded retry on a failing evaluation. The router can pick more than one agent; they run in
parallel and their answers are synthesized into one reply. Any agent can also call a peer
mid-task via an auto-generated `ask_<name>` tool.

## How the assistant is built on the core

The e-commerce assistant is assembled from the reusable core through three seams — the same
seams any Loom-based assistant would use:

1. **Agents** — one YAML file per agent in a directory → `AgentRegistry.from_directory(path)`.
2. **Tools** — plain functions + a `get_tools(names) -> [callables]` map, passed as the `tool_provider`.
3. **Composition root** — `create_app(registry, tool_provider, fallback_agent=...)` → a FastAPI app.

```python
# demo/shopping_assistant/app.py — the assistant's composition root
from pathlib import Path
from backend.agents.registry import AgentRegistry
from backend.api.main import create_app
from .tools import get_tools

registry = AgentRegistry.from_directory(Path(__file__).parent / "definitions")
app = create_app(registry, get_tools, fallback_agent="support_concierge")  # the catch-all agent
```

## Layout

```
backend/                     the reusable multi-agent core (provider-agnostic; no shop knowledge)
demo/shopping_assistant/     the e-commerce assistant: 8 agents, tools, real shop DB
frontend/                    React + Vite UI (login, chat, storefront, dashboard)
scripts/init_db.py           create the database schema from the ORM models
```

Loom runs as a flat app — `backend` and `demo` import directly from the repo root, so there
is no package to build or install.

## Quickstart

Prereqs: Python 3.12+, Node.js 20+, Docker. Run from the repo root.

### 1. Backend (API on port 8000)

```bash
python -m venv .venv
.venv\Scripts\activate                 # POSIX: . .venv/bin/activate
pip install -r requirements-dev.txt

docker compose up -d                   # Postgres :5433, Redis :6379
python scripts/init_db.py              # database schema
python -m demo.shopping_assistant.seed # shop data (safe to re-run; also migrates)

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

Chat requires a login — get a token, then chat (the account from the token scopes
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
