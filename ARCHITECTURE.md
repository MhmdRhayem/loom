# Architecture

Loom is an **e-commerce AI assistant** — a complete shopping assistant
(`demo/shopping_assistant/` + `frontend/`) built on a reusable, provider-agnostic
multi-agent core (`backend/`). The core imports **nothing** from the storefront; the
assistant attaches through a handful of explicit seams and nothing else. Agents are
declarative data, not code: they name a *model tier* (`fast`/`standard`/`deep`), never a
model ID, so one environment variable swaps the whole fleet between Anthropic, OpenAI, and
Google. Every chat turn runs the same fixed pipeline. Read the README to run it; this is
the map — everything listed here is built, wired, and verified working.

## The reuse seams

```
  the storefront (demo/):   definitions/*.yaml  +  tools.py (get_tools)  +  app.py (composition root)
  core runtime (backend/):  create_app  ─▶  build_graph(...)  ─▶  LangGraph pipeline  ─▶  FastAPI
```

| Seam | What you provide | Loaded by |
|------|------------------|-----------|
| **Agents** | a directory of `*.yaml` definitions | `AgentRegistry.from_directory(path)` |
| **Tools** | plain fns + `get_tools(names, owner_id) -> [callables]` | passed as `tool_provider`; the verified owner is bound in per turn |
| **Root** | a tiny module wiring the two | `create_app(...) -> FastAPI` |
| **Identity** (opt.) | `identity_resolver(request) -> owner_id` | scopes chat, memory, conversations; may raise 401 |
| **Visibility** (opt.) | `agent_visibility(request) -> [names] \| None` | filters the router menu, `/agents`, and peer tools per caller |
| **Analytics guard** (opt.) | `analytics_guard(request)` | gates the aggregate dashboards (e.g. admin-only) |

The assistant's composition root ([demo/shopping_assistant/app.py](demo/shopping_assistant/app.py))
is ~40 lines: registry + `get_tools` + the three hooks + three storefront routers (auth,
storefront, merchant management).

## The pipeline (`core/graph.py`)

One chat turn runs a fixed graph compiled once at startup; the single `ConversationState`
TypedDict (`core/state.py`) flows between nodes.

```
load_memory → route → execute_agents → evaluate → save_memory → END
                            ^               |
                            +--- revise ----+   (failing, retryable single-agent eval)
```

| Node | Does |
|------|------|
| `load_memory`   | Read the owner's stored facts → `auto_memory_hints`; no-op when memory is off/absent or the caller is anonymous |
| `route`         | Fast-tier LLM picks **one or more** agents → `{agents, confidence, reason, category}`; a pure `_validate()` sanitizes the picks |
| `execute_agents`| Run the routed agent(s) in parallel (tier→model, prompt, owner-bound tools, memory hints); synthesize one reply if >1; each agent may delegate to peers (depth-bounded); empty agent list → an honest "no agent available" |
| `evaluate`      | Structural gate + a *sampled* LLM critic per agent (`judge_sample_rate`) → per-agent verdicts aggregated (fail if any judged agent fails; score = min) |
| `revise`        | On a failing, retryable single-agent eval: bump `retry_count`, feed the critic's feedback back to the agent (≤ `max_retries`, default 2) |
| `save_memory`   | Extract durable facts from the turn, upsert by (owner, topic) |

**Routing safety** (`agents/router.py`): duplicates deduped → unknown names dropped →
visibility enforced (the fallback agent is always exempt) → nothing valid = fallback if
configured, else *no* agent (never the model's raw output) → a lone low-confidence pick
(< 0.5) falls back. `with_structured_output` returning `None` is normalized, not a crash.
The whole policy is pure and covered by 13 unit tests.

**Multi-agent.** The router picks multiple agents only for genuinely multi-domain
requests; all run in parallel through one runner (`agents/factory.py::run_agent`) and a
standard-tier `_synthesize` call merges the answers into one voice. Separately, any agent
can **call a peer** mid-task via auto-generated `ask_<agent>` tools — a peer call is just a
tool call that re-enters `run_agent` one level deeper. The only guard is
`max_delegation_depth` (default 2): an agent one level deep gets no `ask_*` tools, so
chains and cycles are impossible by construction. Peer runs report their tokens and tool
calls back into the parent's trace, so delegation cost is never invisible.

**Streaming.** `POST /chat/stream` emits SSE at two granularities: `stage` events (one per
node — "routing… agents working… evaluating…") and `token` events (the answer text as the
model generates it). Token filtering: plumbing nodes (router, critic, memory) never
stream; single-agent turns stream the agent, multi-agent turns stream only the
synthesizer. A critic-forced retry emits a `revise` stage (the client clears its draft);
the final `done` payload is authoritative; a mid-stream failure emits a terminal `error`
event instead of a dead socket. The client can abort mid-generation (Stop button).

**Evaluation.** Stage 1 structural (empty answers fail instantly, no model call); stage 2 a
fast-tier critic judging against the agent's own `eval_rubric`, sampled per agent by risk
(payments 1.0, read-only order tracking 0.15). Retries are always judged. The critic is
fail-soft: if it errors, the answer passes through flagged — and crucially yields **no
learning signal** (a critic that didn't run has nothing to teach; it must never score 1.0).

**Cost accounting** (`core/usage.py`): a turn-scoped accumulator collects every model call
it makes, split into input, output and cached input. The split is the point: input and
output bill several times apart and cached input is discounted 90%, so a single
`total_tokens` figure cannot be converted into money. The accumulator lives in a
`ContextVar` because the calls needing counting are spread across the router, critic,
synthesizer, memory extractor, titler and summarizer, none of which share a call path;
asyncio hands each task a copy of the context, so a mutable accumulator stored before the
tasks spawn is the same object in all of them and parallel agents fold into one total.
The four `with_structured_output` call sites pass `include_raw`, without which they return
only the parsed object and are unbillable by construction. Measured on a live four-turn
conversation: **plumbing is 41% of a turn's tokens**, output is **4.9%** of tokens (this
workload is overwhelmingly input-bound), and prompt caching only engages from the fourth
turn, covering 8% of input — a fully warm prefix would be ~62% cheaper.

**Learning.** Rewards from eval verdicts and `POST /feedback` thumbs fold into an EMA score
per `(agent, category)` — computed *inside* the SQL upsert, so concurrent rewards compose
instead of last-writer-wins. Scores are observability (dashboards), not routing input: the
agents are disjoint specialists, so routing stays deterministic classification (the
Thompson-sampling bandit was built and deliberately removed).

**Memory — four layers.**
1. **Definitions** (policy, not code): YAML re-read every turn; an agent's identity can never be compacted away.
2. **Auto-memory**: after each turn a fast-tier extractor stores *durable* facts (preferences, identity, corrections — never order statuses, which tools fetch fresh) upserted atomically under `UNIQUE (owner_id, topic)`; up to 20 recalled per turn as an advisory hint block.
3. **Rolling summary**: a resumed conversation replays its last 10 turns verbatim; older turns fold into one stored summary, regenerated only when more turns age out.
4. **Dreaming** (`memory/consolidation.py`, `POST /dream`): when a user has ≥ `DREAM_MIN_MEMORIES` facts and `DREAM_INTERVAL_HOURS` have passed, a standard-tier pass merges related facts and prunes stale ones (overlap-safe), logged to `dream_runs`.

**Prompt assembly + cache boundary** (`core/prompt_builder.py`): static system prompt per
agent; the per-turn memory-hint block is injected as a `<system-reminder>` message placed
**immediately before the newest user message**, so the system prompt + history stay a
byte-stable prefix for provider prompt caches (hints at position 0 would invalidate the
cache every turn).

**Security & multi-tenancy.** With an identity resolver configured, client-supplied owner
ids are never trusted. Conversation ownership is enforced on list/detail/delete/feedback
*and on chat itself* — resuming someone else's conversation id silently starts a fresh one
(context replay is data access). Memory, dreams, and summaries are owner-keyed with no
sharing level. Agent visibility hides merchant-only agents from everyone else at every
surface (router menu, `/agents`, peer tools). Availability guards fail open (rate limits);
identity guards fail closed.

**Concurrency & atomicity** (hardened + live-verified): memory upserts are single
`INSERT … ON CONFLICT DO UPDATE` statements; `record_turn` is one transaction that locks
the conversation row while numbering (no silent turn loss under concurrent sends); the EMA
lives in SQL. Schema changes ship as idempotent catch-up patches run by
`Database.create_all()` — rerunning `scripts/init_db.py` converges any existing dev DB
(columns dropped, indexes replaced, memory deduplicated) without Alembic.

**Fail-silent + flags.** Every background system — memory, evaluation, learning, dreaming,
persistence, titling, summarization — wraps its work so an error is logged and swallowed,
never breaking the turn. Postgres and Redis are optional at boot (`/health` reports
degradation honestly). Each subsystem is gated by an `ENABLE_*` flag, so any can be turned
off (the ablation mechanism) and the main path still answers.

## Agent YAML

The filename stem **must** equal `name`; the roster is validated at boot (including that
`capabilities`/`tools` are real lists — a YAML scalar is rejected, not silently split).

| Field | Req | Meaning |
|-------|-----|---------|
| `name` | yes | Unique; matches filename stem |
| `description` | yes | Shown to the router — it is routing signal, capability contract, and prompt at once |
| `capabilities` | yes | ≥1; shown to the router |
| `tools` | yes | ≥1 tool names, resolved by `get_tools` |
| `model` | yes | Tier: `fast` \| `standard` \| `deep` (never a model ID) |
| `fallback_agent` | yes | Another agent, or terminal sink `human_handoff` |
| `max_tokens` | no | Default 1024 |
| `eval_rubric` | no | Criteria the LLM critic judges against |
| `judge_sample_rate` | no | Fraction of turns judged (risk pricing; default 1.0) |

## Configuration

`core/config.py` reads everything from the environment once into a frozen `Settings`.
`DEFAULT_PROVIDER` (anthropic/openai/google) resolves tiers via `ModelTiers` — one
variable swaps the fleet. Keys: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GOOGLE_API_KEY`. Flags (all default on): `ENABLE_MEMORY`, `ENABLE_EVALUATION`,
`ENABLE_LEARNING`, `ENABLE_DREAMING`, `ENABLE_ROUTING_CACHE`. Tuning: `MAX_DELEGATION_DEPTH`,
`DREAM_MIN_MEMORIES`, `DREAM_INTERVAL_HOURS`, `CORS_ALLOW_ORIGINS`, `DATABASE_URL`,
`REDIS_URL`. (Anthropic + OpenAI model IDs verified against live APIs; Google IDs are
placeholders to verify at integration.) Demo-side: `AUTH_SECRET`, `QDRANT_URL`,
`EMBEDDING_MODEL` (embeddings are a separate lane — Anthropic has no embeddings API).

## HTTP surface

Core (`backend/api/`): `GET /health` (component status, never raises), `GET /agents`
(visibility-filtered roster), `POST /chat` (blocking turn + full trace), `POST
/chat/stream` (SSE: stage/token/done/error), `POST /feedback` (owner-checked thumbs),
`POST /dream`, `GET|DELETE /conversations*` (owner-scoped list/detail/delete; 404 for
anyone else's), `GET /analytics/*` (guarded aggregates: overview, agents, routing,
timeseries, memory). Demo: `/auth/*` (login with lockout, register, me), `/shop/*` (public
catalog + generated SVG product images; token-scoped cart with quantity steppers,
checkout with declined-payment retry, orders with line items), `/manage/*`
(merchant/admin: catalog CRUD, stats, sales rollup, orders, the AI proposal queue, user
administration). 38 routes total; the role/route matrix is in the thesis appendix.

## Storage

**PostgreSQL** (async SQLAlchemy, psycopg 3, per-domain repositories) — system of record:
`conversations`, `conversation_turns` (unique per-conversation numbering),
`turn_agents` (per-agent attribution in multi-agent turns), `agent_performance` (EMA),
`auto_memory` (unique per owner+topic), `dream_runs`; plus the demo's `shop` schema
(11 tables: products, coupons, orders + items, returns, accounts, carts, payments, the
14-entry FAQ handbook, tickets, and the merchant `product_changes` approval queue).
**Redis** — the demo's login and chat rate limiters, plus the router's decision cache
(`core/routing_cache.py`). The cache exists because the accounting measured the router
as one of five model calls per turn and a storefront asks the same handful of intents
endlessly. It is safe to share between users because a routing decision is a pure
classification of the text against a fixed roster: it reads no per-user data. The key
covers the question, the roster shown to the model (so editing an agent's description
invalidates it) and the caller's visibility (so a merchant's decision is never served
to a client), and `_validate` still runs on a hit, so the fallback and visibility rules
apply either way. Gated by `ENABLE_ROUTING_CACHE`, fail-open, 1h TTL. **Qdrant** (dedicated vector database, see
[VECTOR_DB_CHOICE.md](VECTOR_DB_CHOICE.md)) — semantic-retrieval collections `loom_faq`
and `loom_products`. All three run from `docker-compose.yml` with pinned images
(postgres:18, redis:8-alpine, qdrant/qdrant:v1.18.2).

## Semantic retrieval (RAG, demo-side)

`demo/shopping_assistant/retrieval.py` upgrades `faq_kb` and `product_db` from keyword
matching to retrieval by meaning: corpora embedded into Qdrant (cosine, HNSW, stable
UUIDv5 point ids, staleness-diffed batched indexing at seed time), queries embedded per
call, FAQ answers returned **with their policy source** and a score floor (a weak match
falls back rather than citing noise). Lives entirely at the tool seam — zero
changes to the core — and degrades silently to keyword search when Qdrant or the embedding key is
absent. Try: *"can I get my money back if it doesn't fit?"* → cites `policy/returns` with
no keyword overlap.

## The assistant, end to end

Eight agents (`definitions/*.yaml`): catalog_advisor, fit_stylist, order_tracking,
checkout_payments (the one deep-tier agent, always judged), returns_refunds,
account_assistant, support_concierge (router fallback), and merchant-only shop_manager
(proposes catalog changes into an approval queue — the AI drafts, the human commits).

| Agent | Tier | What it does | Fallback |
|-------|------|--------------|----------|
| `catalog_advisor` | standard | Pre-purchase catalog help: product search, recommendation, filtering, comparison, product Q&A, and best price including deals and coupons. Merge of the former product-recommendation + price-comparison agents. | `support_concierge` |
| `fit_stylist` | standard | Sizing/fit guidance and style/outfit advice tailored to the shopper — size recommendations, fit diagnosis, styling — grounded in items the store actually sells. | `catalog_advisor` |
| `order_tracking` | fast | "Where is my order" — lists a customer's orders and gives status, shipment tracking, and delivery estimates. Read-only, so lightly judged (0.15). | `support_concierge` |
| `checkout_payments` | deep | Resolves problems completing a purchase on the in-flight cart: stock/availability, cart and checkout failures, and payment, promo-code, and billing issues. The single deep-tier agent, always judged (1.0). | `support_concierge` |
| `returns_refunds` | standard | Initiates and tracks returns, refunds, and exchanges under the store's return policy — post-purchase reversals. Irreversible money path, always judged (1.0). | `support_concierge` |
| `account_assistant` | fast | Account and profile help: login issues, password/security resets, and updating profile, addresses, and settings. | `support_concierge` |
| `support_concierge` | standard | General catch-all: policy/FAQ, complaints, clarifying ambiguous requests, and escalation to a human with full context. The router's fallback and low-confidence clarification target. | `human_handoff` (terminal sink) |
| `shop_manager` | standard | **Merchant-only** (hidden from clients/admins): review the signed-in merchant's own catalog and propose product add/update/remove. Every proposal is queued for approval, never applied directly. | `support_concierge` |

Tools are **identity-bound closures**: the verified email is baked in at bind time, so
"whose orders" is not a model-controllable parameter (merchant tools bind lazily — the
account lookup only runs when a merchant tool is requested). Auth: PBKDF2-SHA256 (600k
iterations, run in a worker thread off the event loop), HS256 JWTs, roles read fresh per
request, deleted accounts revoked immediately, login lockout (5/10min) and per-account
chat rate limit (20/min), both Redis-backed and fail-open.

**Frontend** (`frontend/`, React 19 + TypeScript + Vite, ~3k lines): storefront with
filters and a cart *drawer* (quantity steppers, inline declined-payment recovery,
add-to-cart toasts, expandable order line items); streaming chat (live token draft with
caret, stage narration, Stop button, per-turn trace panel with agents/confidence/tool
calls/eval/retries, thumbs feedback); resumable conversation history with AI-generated
titles; merchant sales dashboard + catalog management + pending-AI-changes queue; admin
analytics dashboards and user administration. Light/dark theme (CSS variables, OS-default,
persisted, no first-paint flash); dashboards code-split (main bundle 826→437 kB);
responsive off-canvas sidebar.

## File map

| Path | Responsibility |
|------|----------------|
| `core/state.py` | `ConversationState` — the one object every node reads/writes |
| `core/config.py` | `Settings` + `ModelTiers`; tier resolver; flags and knobs |
| `core/prompt_builder.py` | System prompt; cache-friendly `<system-reminder>` hint placement |
| `core/graph.py` | The pipeline: `build_graph(...)`, `build_initial_state(...)`, synthesis |
| `agents/registry.py` | Load + validate YAML; `router_menu()` |
| `agents/factory.py` | Definition → live `create_agent`; `run_agent`; `ask_<peer>` tools with cost roll-up |
| `agents/router.py` | Fast-tier routing; pure `_validate()` policy |
| `api/main.py` | `create_app(...)`: lifespan (fail-soft DB/Redis), CORS, hooks |
| `api/routes.py` | `/chat`, `/chat/stream` (SSE incl. token + error events), `/feedback`, `/dream`, `/health`, `/agents` |
| `api/analytics_routes.py`, `api/analytics_models.py` | Owner-scoped `/conversations*`; guarded `/analytics/*` |
| `service.py` | Application layer: `run_turn`/`stream_turn` (ownership guard, history replay, rolling summary), `record_feedback`, `run_dream`, titling — FastAPI-free |
| `memory/auto_memory.py` | Layer 2: hint loading + extraction (domain-neutral prompt) |
| `memory/consolidation.py` | Layer 4 dreaming: merge/prune, overlap-safe |
| `evaluation/structural.py`, `evaluation/critic.py` | The two-stage gate |
| `learning/signals.py`, `learning/scoring.py` | Rewards (no-signal on critic outage) → atomic SQL EMA |
| `storage/base.py` | `Database`: async pool, repositories, `create_all()` + idempotent schema patches |
| `storage/models.py`, `storage/repositories/*` | ORM tables; conversations (atomic `record_turn`), memory (atomic upsert), performance, dreams, analytics (5-query overview) |
| `storage/redis_store.py` | Redis client lifecycle (the demo's rate limiters use it) |
| `_platform.py` | Windows selector event loop (async psycopg) |
| `scripts/init_db.py` | Create/migrate the database schema (idempotent) |
| `core/usage.py` | Per-turn token accounting: input/output/cached split across every model call |
| `core/routing_cache.py` | Redis-backed reuse of the router's decision, keyed on question + roster + visibility |
| `scripts/benchmark.py` | Outside-in replay benchmark → CSV (routing, eval, latency, token split) |
| `scripts/serve.py` | Runs the API on a loop async psycopg accepts (a bare `uvicorn` cannot) |
| `scripts/run_ablations.py`, `summarize_ablations.py` | Flag/roster ablations → labelled CSVs → cost table |
| `scripts/probe_memory_layers.py` | The memory-layer hypotheses the replay benchmark cannot reach |
| `demo/shopping_assistant/` | `app.py` (root), `auth.py`, `tools.py`, `retrieval.py` (RAG), `db.py`/`seed.py` (shop schema + policy corpus), `shop_routes.py`, `manage_routes.py`, `definitions/` (8 agents) |
| `frontend/src/` | The React app (pages: Chat, Conversations, Storefront, Products, ShopDashboard, Dashboard, Users, Login) |

## Quality practice

CI on every push: ruff lint + format check, pytest (111 tests over the pure logic:
routing policy, the chat ownership guard, verdict aggregation, agent-visibility and
delegation bounds, registry validation against the real roster, all four memory layers,
rewards, auth primitives, prompt helpers, retrieval fallbacks), frontend typecheck +
production build. The suite needs no Postgres, no Redis and no provider key, so it costs
nothing to run anywhere. The LLM-dependent paths are covered by the replay benchmark
(`benchmarks/*.csv`, tracked in the repo) and a 13-scenario manual matrix (A-M, see
DEMO_TESTING.md). Concurrency guarantees were verified against the live database
(parallel turn numbering, upsert dedupe, EMA composition).

## Status

Everything above is **built and verified working** end-to-end on a live provider: routing
(single + multi-agent + fallback), peer delegation, all four memory layers, evaluation
with retry, learning, dreaming, token streaming, conversation resume with ownership
enforcement, semantic retrieval over Qdrant, the full storefront/merchant/admin web app,
and the benchmark + ablation harness (flags). Nothing is "reserved": the speculative
pieces that used to sit here (Redis conv-state/routing-cache/flag helpers, the
`memory_scope` YAML field, memory expiry) were deleted rather than carried, so every
symbol in the tree is on a live path.
**Deferred by design:** cross-process workers and Redis mailboxes (in-process multi-agent
covers the need), an agent base class, core-level approval gates (the storefront's
merchant queue is the domain-level version), Thompson-sampling routing (built, measured
against the problem, removed), and vector-based recall for *per-user* memory (relational
recall is exact and auditable at dozens of facts; the corpus-scale retrieval lives in the
demo where an actual corpus exists).
