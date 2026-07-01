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
load_memory → route → execute_agents → evaluate → save_memory → END
                            ^                |
                            +---- revise ----+   (failing, retryable single-agent eval)
```

| Node | Does | Status |
|------|------|--------|
| `load_memory`   | Read owner's stored facts → `auto_memory_hints` | **wired** |
| `route`         | Fast-tier LLM picks one or more agents → `{agents, confidence, reason}`; `_validate()` applies fallback policy | **wired** |
| `execute_agents`| Run the routed agent(s) in parallel (tier→model + prompt + tools + memory hints); synthesize if >1; each may delegate to peers (depth-bounded) | **wired** |
| `evaluate`      | Structural check + a *sampled* LLM critic (per agent `judge_sample_rate`) → `{pass, score, feedback}` | **wired** |
| `revise`        | On a failing, retryable eval: bump `retry_count`, feed the critic's feedback back to the agent | **wired** |
| `save_memory`   | Extract durable facts from the turn, upsert by topic | **wired** |

Both memory nodes no-op unless a `memory` repo + `enable_auto_memory` + an `owner_id` are
all present, and are **fail-silent** — a model or store error never breaks the turn.
Routing safety (`agents/router.py`, pure `_validate`): unknown agent names are dropped;
if none remain (or a lone pick is below `0.5` confidence) → fallback; otherwise keep the
model's chosen agent(s).
Evaluation is fail-soft and bounded: the critic is sampled (cost), a failing turn retries
at most `max_retries` times (default 2) with feedback, then passes through flagged — it
never blocks the user.

**Multi-agent (Phase 5).** The router can pick **more than one agent** for a turn; all run
in parallel through one runner (`agents/factory.py`, `run_agent`) and their answers are
synthesized into a single reply (`_synthesize` in `core/graph.py`). Separately, any agent can
**call a peer** mid-task via auto-generated `ask_<agent>` tools — a peer call is just a tool
call that re-enters `run_agent` one level deeper. The only guard is `max_delegation_depth`,
threaded as a plain argument so nested calls can't recurse without bound (no shared context,
budget, or approval gate). All in-process; cross-process workers are not built.

**Learning (Phase 6).** Each turn's reward — the evaluator verdict, or an explicit thumbs
rating via `POST /feedback` — folds into an EMA score per `(agent, category)` in
`agent_performance` (the router emits a coarse `category`), alongside a running pass/fail
tally. That score is observability for which specialist underperforms on which category; it
does not override routing. The agents are disjoint specialists, not interchangeable arms, so
routing stays a deterministic classification by the LLM router (no bandit). **Layer 4
"dreaming"** (`memory/consolidation.py`,
`POST /dream`) periodically merges duplicate memories and prunes stale ones, logging each run
to `dream_runs`. All of it is fail-soft and never blocks a turn.

**Fail-silent + flags (Phase 7).** Every background system — memory, evaluation, learning,
dreaming, delegation, persistence — wraps its work so an error is logged and swallowed, never
breaking the turn; Postgres and Redis are optional at boot. Each subsystem is also gated by an
`ENABLE_*` flag, so any can be turned off (for ablation) and the main path still answers.

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
| `eval_rubric` | no | Criteria the LLM critic judges against (Phase 4) |
| `judge_sample_rate` | no | Fraction of turns judged (risk weight; default 1.0) |
| `memory_scope` | no | Parsed, not yet acted on |

## Provider swap

`core/config.py` (`ModelTiers`) maps provider → tier → model ID; `Settings.model_id_for_tier()`
resolves against `DEFAULT_PROVIDER`. So `DEFAULT_PROVIDER=openai` swaps the whole fleet.
Keys: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`. **Feature flags** gate each
subsystem (all default on; flip for ablation): `ENABLE_MEMORY` / `ENABLE_EVALUATION` /
`ENABLE_LEARNING` / `ENABLE_DREAMING`. Tuning: `MAX_DELEGATION_DEPTH`,
`DREAM_MIN_MEMORIES` / `DREAM_INTERVAL_HOURS`.
(Anthropic IDs are authoritative; OpenAI/Google IDs are post-cutoff placeholders — verify at
integration.)

## File map

| File | Responsibility | Status |
|------|----------------|--------|
| `core/state.py` | `ConversationState` — the one object every node reads/writes | wired |
| `core/config.py` | `Settings` + `ModelTiers`; tier resolver; feature flags; delegation + routing + dream knobs | wired |
| `core/prompt_builder.py` | System prompt + `<system-reminder>` injection (memory hints) | wired |
| `core/graph.py` | The pipeline; `build_graph(...)`, `build_initial_state(...)` | wired |
| `agents/registry.py` | Load + validate YAML; lookup by name/capability; `router_menu()` | wired |
| `agents/factory.py` | `build_agent` (`AgentDefinition` → live `create_agent`) + `run_agent` (run it in-process) + `ask_<agent>` peer tools (depth-bounded) | wired |
| `agents/router.py` | Fast-tier routing call; pure `_validate()` fallback policy; returns one or more agents | wired |
| `api/main.py` | `create_app(...)` factory + lifespan (opens DB/Redis fail-soft, builds graph) | wired |
| `api/routes.py` | Thin HTTP layer: pull deps off `app.state` → call `service` → map to a response model (`/chat`, `/feedback`, `/dream`, `GET /health`) | wired |
| `api/models.py` | Pydantic `ChatRequest`/`ChatResponse`/`HealthResponse` (incl. `owner_id`) | wired |
| `service.py` | Application layer: `run_turn` / `record_feedback` / `run_dream` — composes graph + persistence + learning + consolidation; FastAPI-free, so routes stay thin | wired |
| `memory/auto_memory.py` | Layer 2: `load_hints` (read) + `extract_and_upsert` (write, dedupe by topic); fail-silent | wired |
| `evaluation/structural.py` | Stage 1: deterministic checks (empty / too short) | wired |
| `evaluation/critic.py` | Stage 3: sampled LLM critic against the agent's `eval_rubric`; fail-silent | wired |
| `learning/signals.py` | Reward from the eval verdict or explicit feedback (pure) | wired |
| `learning/scoring.py` | EMA score per `(agent, category)` in `agent_performance`; fail-silent | wired |
| `memory/consolidation.py` | Layer 4 "dreaming": merge/prune memories on a trigger; logs `dream_runs` | wired |
| `storage/base.py` | `Database` — async pool + repositories; `create_all()` (Alembic-free) | wired — opened at boot |
| `storage/models.py` | ORM tables for all phases | schema only |
| `storage/repositories/memory.py` | `auto_memory` CRUD + topic upsert | **wired** (Layer 2) |
| `storage/repositories/conversations.py` | conversation + turn CRUD; `record_turn` per request | **wired** |
| `storage/repositories/{performance,dreams}.py` | EMA scores / consolidation-run log | **wired** (Phase 6) |
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
cross-session auto-memory, conversation/turn persistence (every `/chat` turn, fail-soft),
Phase 4 evaluation (structural + sampled critic + one bounded retry), and Phase 5
multi-agent (router picks one or more agents → parallel run → synthesis, plus depth-bounded
peer delegation, in-process), Phase 6
adaptive learning (per-`(agent,category)` EMA scoring from eval + `/feedback`, and Layer 4
memory "dreaming"), and Phase 7 fail-silent + feature
flags (each subsystem gated by an `ENABLE_*` flag). Verified end-to-end on a live provider:
routing, single + multi-agent, memory, evaluation, persistence, scoring, dreaming.

**Reserved** (present, not yet on the request path): most of `redis_store` and the
evaluation **policy** stage. The turn's `tokens_used` / `cost` / `cache_hit` columns are
written null until LLM-usage callbacks land. A few state/model fields (`session_summary`,
`compaction_count`, `execution_model`) back deferred features and are currently unused.

**Deferred by design:** an agent base class; the Fork/Teammate/Worktree models +
cross-process/Redis-mailbox workers + human-in-the-loop approval (in-process multi-agent
covers the need); cascades; memory **Layer 3** (context
compaction). Layer 1 is effectively met (every turn rebuilds the agent from its YAML);
Layers 2 and 4 are built.
