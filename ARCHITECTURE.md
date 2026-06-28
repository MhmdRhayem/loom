# Architecture

The framework is the reusable core under `src/multi_agent_framework/`; it imports
**nothing** from `demo/`. An app attaches through three seams (agents, tools, composition
root) and nothing else. Agents are declarative data, not code: they name a *model tier*
(`fast`/`standard`/`deep`), never a model ID, so one setting swaps the whole fleet between
providers. Every `/chat` turn runs the same fixed pipeline; later phases fill node bodies
without changing the shape. Read the README first to run it; this is the map.

## The three reuse seams

```
  you write (demo/):   definitions/*.yaml  +  tools.py (get_tools)  +  app.py
  framework runtime:   create_app  ─▶  build_graph(...)  ─▶  LangGraph pipeline  ─▶  FastAPI /chat
```

| Seam | What you provide | Loaded by |
|------|------------------|-----------|
| **Agents** | a directory of `*.yaml` definitions | `AgentRegistry.from_directory(path)` |
| **Tools** | plain fns + `get_tools(names) -> [callables]` | passed as `tool_provider` |
| **Root** | a tiny module wiring the two | `create_app(...) -> FastAPI` |

## The pipeline (`core/graph.py`)

One `/chat` request runs a fixed linear graph; `build_graph` is dependency-injected
(`registry`, `settings`, `tool_provider`, `fallback_agent`, `memory`). The single
`ConversationState` TypedDict (`core/state.py`) is passed between nodes.

```
load_memory → route → execute_agent → evaluate → save_memory → END
```

| Node | Does | Status |
|------|------|--------|
| `load_memory`   | Read owner's stored facts → `auto_memory_hints` | **wired** |
| `route`         | Fast-tier LLM picks an agent → `{agent, confidence, reason}`; `_resolve()` applies fallback policy | **wired** |
| `execute_agent` | Build chosen agent (tier→model + prompt + tools), inject hints as `<system-reminder>`, run the tool loop | **wired** |
| `evaluate`      | Score the response | **stub** — always returns pass/1.0 |
| `save_memory`   | Extract durable facts from the turn, upsert by topic | **wired** |

Both memory nodes no-op unless a `memory` repo + `enable_auto_memory` + an `owner_id` are
all present, and are **fail-silent** — a model or store error never breaks the turn.
Routing safety (`agents/router.py`, pure `_resolve`): unknown agent → fallback;
confidence `< 0.5` → fallback; otherwise keep the model's choice.

## Agent YAML

The filename stem **must** equal `name`. Validated at load time, so a bad roster fails at
boot, not on a request.

| Field | Req | Meaning |
|-------|-----|---------|
| `name` | yes | Unique; matches filename stem |
| `description` | yes | Shown to the router |
| `capabilities` | yes | ≥1; shown to the router |
| `tools` | yes | ≥1 tool names, resolved by `get_tools` |
| `model` | yes | Tier: `fast` \| `standard` \| `deep` (never a model ID) |
| `fallback_agent` | yes | Another agent, or terminal sink `human_handoff` |
| `max_tokens` | no | Default 1024 |
| `eval_rubric`, `judge_sample_rate`, `memory_scope` | no | Parsed, not yet acted on |

## Provider swap

`core/config.py` (`ModelTiers`) maps provider → tier → model ID; `Settings.model_id_for_tier()`
resolves against `DEFAULT_PROVIDER`. So `DEFAULT_PROVIDER=openai` swaps the whole fleet.
Keys: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`. `ENABLE_AUTO_MEMORY=0`
disables memory. (Anthropic IDs are authoritative; OpenAI/Google IDs are post-cutoff
placeholders — verify at integration.)

## File map

| File | Responsibility | Status |
|------|----------------|--------|
| `core/state.py` | `ConversationState` — the one object every node reads/writes | wired |
| `core/config.py` | `Settings` + `ModelTiers`; `model_id_for_tier`; `enable_auto_memory` | wired |
| `core/prompt_builder.py` | System prompt + `<system-reminder>` injection (memory hints) | wired |
| `core/graph.py` | The pipeline; `build_graph(...)`, `build_initial_state(...)` | wired |
| `agents/registry.py` | Load + validate YAML; lookup by name/capability; `router_menu()` | wired |
| `agents/factory.py` | One `AgentDefinition` → live LangChain `create_agent` (tier→model + prompt + tools) | wired |
| `agents/router.py` | Fast-tier routing call; pure `_resolve()` fallback policy | wired |
| `api/main.py` | `create_app(...)` factory + lifespan (opens DB/Redis fail-soft, builds graph) | wired |
| `api/routes.py` | `POST /chat` (records each turn, fail-soft) + `GET /health` | wired |
| `api/models.py` | Pydantic `ChatRequest`/`ChatResponse`/`HealthResponse` (incl. `owner_id`) | wired |
| `memory/auto_memory.py` | Layer 2: `load_hints` (read) + `extract_and_upsert` (write, dedupe by topic); fail-silent | wired |
| `storage/base.py` | `Database` — async pool + repositories; `create_all()` (Alembic-free) | wired — opened at boot |
| `storage/models.py` | ORM tables for all phases | schema only |
| `storage/repositories/memory.py` | `auto_memory` CRUD + topic upsert | **wired** (Layer 2) |
| `storage/repositories/conversations.py` | conversation + turn CRUD; `record_turn` per request | **wired** |
| `storage/repositories/{performance,dreams}.py` | EMA scores / consolidation CRUD | reserved — not yet called |
| `storage/redis_store.py` | state/routing cache, feature flags | partial — opened + `/health` ping; rest reserved |
| `_platform.py` | Forces the Windows selector event loop (async psycopg) | wired |
| `scripts/init_db.py` | Creates the schema from the models, idempotent | wired |

Demo (`demo/shopping_assistant/`): `app.py` (the composition-root template — registry +
`get_tools` → `create_app(..., fallback_agent="support_concierge")`), `tools.py` (10
e-commerce tools + `get_tools`), `db.py`/`seed.py` (a real Postgres `shop` schema behind
the tools), `definitions/*.yaml` (7 agents: catalog_advisor, order_tracking,
returns_refunds, checkout_payments, account_assistant, fit_stylist, support_concierge).

## Build a new project

Provide a `definitions/` directory and a `tools.py`, then the composition root from the
README. Plain tool functions become tool schemas automatically (type hints → schema,
docstring → description). An agent YAML:

```yaml
name: order_tracking          # MUST match the filename stem
description: Answers "where is my order" questions; read-only.
capabilities: [order_status, shipping_tracking]
tools: [order_api]            # names resolved by your get_tools
model: fast                   # tier: fast | standard | deep
fallback_agent: support_concierge   # a known agent, or human_handoff
# optional: max_tokens, eval_rubric, judge_sample_rate, memory_scope
```

Run with `uvicorn --env-file .env myapp.app:app`. Nothing in the framework changes.

## Status

**Wired:** Phase 1 foundation, Phase 2 dynamic routing + agent execution, Phase 3 Layer 2
cross-session auto-memory, and conversation/turn persistence (every `/chat` turn, fail-soft).
Verified end-to-end on a live provider.

**Reserved** (schema/CRUD present, not yet on the request path): `agent_performance`,
`dream_runs`, most of `redis_store`, and the `evaluate` node. The turn's `tokens_used` /
`cost` / `cache_hit` columns are written null until LLM-usage callbacks land. A few
state/model fields (`session_summary`, `compaction_count`, `execution_model`) back
deferred features and are currently unused.

**Deferred by design:** an agent base class and Fork/Teammate/Worktree execution models;
router fan-out / cascades / learned policies; memory Layers 1, 3, 4. (Layer 1 is
effectively met — every turn rebuilds the agent from its YAML via the factory.)
