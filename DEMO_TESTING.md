# Demo Testing Guide — Shopping Assistant

A hands-on script for testing the 7-agent e-commerce demo yourself and checking the results.
Every prompt below is grounded in the seeded database, so you can verify the answers are **real
data**, not hallucinations. Work through the scenarios in order — each one tests a different part
of the framework (routing, tools, multi-agent, delegation, memory, evaluation, fail-silent).

---

## 1. Start everything

```powershell
cd "C:\Mhmd\M2 AI and Data Engineering\Final Project\multi-agent-framework"

docker compose up -d                                    # Postgres :5433 + Redis :6379
.\.venv\Scripts\python.exe scripts\init_db.py           # framework schema (idempotent)
.\.venv\Scripts\python.exe -m demo.shopping_assistant.seed   # shop data (idempotent)

# Backend (keep this terminal open) — .env already has OPENAI_API_KEY + DEFAULT_PROVIDER=openai
.\.venv\Scripts\python.exe -m uvicorn --env-file .env demo.shopping_assistant.app:app --reload

# Frontend (second terminal)
cd frontend
npm run dev          # http://localhost:5173
```

**Sanity check before testing:** open http://localhost:8000/health — expect
`{"status":"ok","components":{"redis":"ok","postgres":"ok"}}`.

To wipe and reseed the shop data at any point:
`.\.venv\Scripts\python.exe -m demo.shopping_assistant.seed --reset`

**Sign in first.** Chat, orders, and conversations are per-account now — the app sends you
to the login page until you sign in. Seeded accounts:

| Email | Password | Their orders |
|---|---|---|
| `mohammad@example.com` | `mohammad123` | ORD-1005 (shipped, DHL), ORD-1006 (processing) |
| `alice@example.com` | `password123` | ORD-1001 (shipped, UPS), ORD-1004 (cancelled) |
| `bob@example.com` | `password123` | ORD-1002 (delivered — has the RMA) |
| `carol@example.com` | `password123` | ORD-1003 (processing) |

You can only see (and ask the assistant about) **your own** orders, returns, and
conversations — that's Scenario I below.

---

## 2. Where to check results

| Surface | What you see |
|---|---|
| **Chat page** (http://localhost:5173) | The reply, plus a collapsible **trace** under each answer: routed category + confidence, the router's reason, each agent's run with its tool calls, the eval score/pass badge, and a retry badge if the critic forced a retry. |
| **Conversations page** | Every persisted conversation and turn — proof that persistence works. |
| **Dashboard page** | Aggregate analytics: per-agent volume and eval scores, routing distribution, timeseries, memory stats. |
| **Raw API** | `POST http://localhost:8000/chat` returns the full trace as JSON (`current_agents`, `routing_confidence`, `routing_reason`, `query_category`, `tool_calls`, `agent_runs`, `eval`, `retry_count`). |

Raw API example (PowerShell) — log in first, then send the Bearer token; the backend takes
the owner from the token, so there is no `owner_id` in the request:

```powershell
$login = Invoke-RestMethod http://localhost:8000/auth/login -Method Post -ContentType "application/json" `
  -Body '{"email":"alice@example.com","password":"password123"}'
$headers = @{ Authorization = "Bearer $($login.token)" }
$body = @{ message = "Where is my order ORD-1001?" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/chat -Method Post -Headers $headers -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 6
```

---

## 3. The 7 agents

The roster the router picks from. Each agent is defined by one YAML file in
[demo/shopping_assistant/definitions/](demo/shopping_assistant/definitions/) — the router routes on the
`description` field alone, so these descriptions *are* the routing surface.

| Agent | Tier | Tools | What it does |
|---|---|---|---|
| `order_tracking` | fast | `order_api`, `list_my_orders` | "Where is my order?" — order status, shipment tracking, and delivery estimates for already-placed orders. Read-only: never modifies anything. |
| `catalog_advisor` | standard | `product_db`, `price_api` | All pre-purchase catalog help: search, recommend, filter, and compare products, answer product questions, and find the best price including deals and coupons. |
| `fit_stylist` | standard | `style_engine` | Sizing and style advice tailored to the shopper: size recommendations, fit diagnosis, outfit suggestions. Kept separate from `catalog_advisor` on purpose — their boundary is the router stress test in Scenario C1. |
| `checkout_payments` | deep | `cart_api`, `payment_api` | Problems completing a purchase: stock checks, cart and checkout failures, payment/promo/billing issues at the point of sale. Works on the **in-flight cart**, not placed orders — that's what keeps it crisp against `order_tracking`. |
| `returns_refunds` | standard | `returns_api` | Returns, refunds, and exchanges under the store's return policy — post-purchase reversals only, not tracking or new purchases. |
| `account_assistant` | fast | `account_api` | Account and profile help: login issues, password/security resets, updating profile, addresses, and settings. |
| `support_concierge` | standard | `faq_kb`, `ticket_api` | The catch-all: policy/FAQ answers, complaints, clarifying ambiguous requests, and escalation to a human with full context. The safe destination when no specialist fits. |

Two structural details worth knowing when reading traces:

- **Fallbacks form a DAG into a sink:** every specialist falls back to `support_concierge`, except
  `fit_stylist` (falls back to `catalog_advisor`); `support_concierge` itself falls back to the terminal
  `human_handoff` sink node (not an agent).
- **Critic sampling follows risk:** the money paths (`checkout_payments`, `returns_refunds`) and
  `support_concierge` are always judged (`judge_sample_rate: 1.0`); read-only lookups are sampled
  lightly (`order_tracking` 0.15, `account_assistant` 0.3, `catalog_advisor` 0.4, `fit_stylist` 0.5).

## 4. Cheat sheet — what's actually in the database

Use this to verify answers. If the assistant says anything that contradicts this table, that's a bug (or a hallucination worth noting for the thesis).

**Orders**

| Order | Customer | Status | Detail |
|---|---|---|---|
| ORD-1001 | alice@example.com | shipped | UPS, tracking `1Z999AA10123456784`, ETA 2026-06-29 |
| ORD-1002 | bob@example.com | delivered | FedEx, delivered 2026-06-18, total $120 |
| ORD-1003 | carol@example.com | processing | no tracking yet, ETA 2026-07-02 |
| ORD-1004 | alice@example.com | cancelled | — |
| ORD-1005 | mohammad@example.com | shipped | DHL, tracking `JD014600003RS034`, ETA 2026-07-05 |
| ORD-1006 | mohammad@example.com | processing | no tracking yet, ETA 2026-07-08 |

**Returns:** ORD-1002 has an approved return `RMA-55872`, 12 days left in window, refund to original payment, next step = drop at FedEx with emailed label.

**Cart & payment:** the current cart holds 1× Aurora Midi Dress (SKU-1001, $49). The cart's payment is **declined — card_expired**.

**Products (highlights):** Aurora Midi Dress $49 · Linen Wrap Dress $62 · Everyday Cotton Tee $18 **(out of stock)** · High-Rise Slim Jeans $58 · Merino Wool Sweater $89 **(15% off this week)** · Classic Trench Coat $145 · Leather Ankle Boots $120 · Silk Scarf $35 · Canvas Tote Bag $28 **(out of stock)** · Lounge Jogger Pants $42 **(10% off this week)**.

**Coupons:** `SAVE10` (10% any order) · `WELCOME20` (20%, min $75) · `DRESS15` (15% on SKU-1001) · `SHOES25` (25% on SKU-1007 boots).

**Policies (FAQ):** free shipping over $50 (3–5 days; express $9.99, 1–2 days) · 30-day returns, unworn with tags · one coupon per order (stacks with site-wide deals) · Visa/Mastercard/Amex/PayPal/Apple Pay · password reset via "Forgot password" link.

**Fit engine (stub):** always recommends **size M**, says items "run slightly large; size down if between sizes", and suggests ankle boots + a thin belt.

---

## 5. Scenario A — one prompt per agent (routing precision)

Send each prompt in a **new chat**. Check the trace: the right agent, the right tool, and an answer matching the cheat sheet.

> Orders belong to accounts now: run **A1 signed in as alice** and **A5 signed in as bob**
> (asking from the wrong account correctly answers "not found on this account" — that's
> Scenario I). The other prompts work from any account.

| # | Say this | Expected agent | Expected tool | Answer must contain |
|---|---|---|---|---|
| A1 | *Where is my order ORD-1001?* | `order_tracking` | `order_api` | shipped, UPS, `1Z999AA10123456784`, 2026-06-29 |
| A2 | *Do you have any dresses under $60? Any deals or coupon codes I can use?* | `catalog_advisor` | `product_db`, `price_api` | Aurora Midi Dress $49; coupon `DRESS15` and/or `SAVE10` |
| A3 | *I'm between sizes for the Linen Wrap Dress — which size should I take, and what would you style it with?* | `fit_stylist` | `style_engine` | size M / size down (runs large), boots + belt styling tips |
| A4 | *My payment keeps getting declined at checkout — what's wrong?* | `checkout_payments` | `payment_api` | card expired; suggest updating expiry or another method |
| A5 | *I want to return my order ORD-1002.* | `returns_refunds` | `returns_api` | RMA-55872, eligible/approved, 12 days left, FedEx drop-off |
| A6 | *I forgot my password — how do I reset it?* | `account_assistant` | `account_api` | reset link / reset flow |
| A7 | *Which payment methods do you accept?* | `support_concierge` | `faq_kb` | Visa, Mastercard, Amex, PayPal, Apple Pay |

Also worth checking in each trace: `routing_confidence` should be high (≳0.8) for these — they're unambiguous.

## 6. Scenario B — multi-agent turns (one-or-more routing)

The router may pick **several agents**; they run in parallel and the answers get synthesized into one reply. Check the trace for **two agents**, each with its own run and tool calls.

| # | Say this | Expected agents | Verify |
|---|---|---|---|
| B1 | *(as carol)* *Where is order ORD-1003, and can I still return it if the sweater doesn't fit?* | `order_tracking` + `returns_refunds` | one coherent reply covering status (processing, ETA 2026-07-02) **and** return policy/eligibility |
| B2 | *Is the Everyday Cotton Tee back in stock? Also, I need to update my shipping address.* | `catalog_advisor` + `account_assistant` | out-of-stock answer + address-update flow, both in one reply |
| B3 | *What's in my cart right now, and does the DRESS15 coupon apply to it?* | `checkout_payments` (+ possibly `catalog_advisor`) | Aurora Midi Dress ×1; DRESS15 applies to SKU-1001 |

## 7. Scenario C — deliberate ambiguity (the interesting cases)

These sit on agent boundaries **on purpose**. There is no single "correct" agent — what matters is that the routing reason is sensible and the answer is grounded. Note what the router does; this is thesis material.

| # | Say this | Reasonable outcomes |
|---|---|---|
| C1 | *Recommend a dress that will actually fit me — I'm usually between S and M.* | `fit_stylist`, `catalog_advisor`, or both. This is the designed catalog↔fit boundary stress test. |
| C2 | *I bought boots that don't fit. What now?* | `returns_refunds` (return them) and/or `fit_stylist` (fit diagnosis). |
| C3 | *This is unacceptable — connect me to a human right now.* | `support_concierge` with a `ticket_api` call (escalation with context). |
| C4 | *What's the meaning of life?* | `support_concierge` (fallback — no specialist fits). Watch the confidence: it should be lower than in Scenario A. |

**Metric to watch on the Dashboard over time:** how often `support_concierge` absorbs traffic. A rising fallback rate means an agent description needs sharpening.

## 8. Scenario D — peer delegation (`ask_<agent>` tools)

Any agent can call a peer mid-task via an auto-generated `ask_<name>` tool (depth-bounded by `MAX_DELEGATION_DEPTH=2`). Delegation is the *agent's* choice, so it isn't guaranteed on every run — look in the trace's per-agent tool calls for an `ask_*` entry.

| # | Say this | What to look for |
|---|---|---|
| D1 | *Before I retry my payment — is the dress in my cart even still in stock, and is there any coupon for it?* | Router likely picks `checkout_payments`; its run may contain `ask_catalog_advisor`. Alternatively the router picks both agents up front — also a valid outcome; note which happened. |
| D2 | *(as bob)* *I want to return ORD-1002 — but first, has it actually been delivered?* | `returns_refunds` may call `ask_order_tracking` (delivered 2026-06-18 → then RMA details). |

## 9. Scenario E — memory across sessions

Memory is scoped to the **signed-in account** (the owner id is the account email, taken from
your login token — there's nothing to type).

1. **Sign in as alice.** Chat 1: *"By the way — I always wear size M and I only buy natural fabrics, no polyester."* (any reply is fine)
2. **Start a new chat** (still alice): *"Recommend a top for me."*
   - **Pass:** the recommendation reflects size M and/or natural fabrics without you repeating them.
3. Check **Dashboard → memory stats** (or `GET /analytics/memory?owner_id=alice@example.com`) — stored memories should be > 0.
4. Force a consolidation run (Layer 4 "dreaming") and check it reports merged/pruned counts:
   ```powershell
   Invoke-RestMethod "http://localhost:8000/dream?owner_id=alice@example.com&force=true" -Method Post
   ```
5. **Control test:** sign out, sign in as **bob**, and repeat step 2 — the assistant should NOT know Alice's size (memory never crosses accounts).

## 10. Scenario F — conversation continuity

In the **same chat** as A1, follow up with: *"And when will it arrive?"*
**Pass:** it answers about ORD-1001 (ETA 2026-06-29) without you repeating the order number — turn history is being carried.

## 11. Scenario G — evaluation & feedback

1. Under any reply, expand the trace: you should see an **eval score** (0–1) and pass/fail. The critic is sampled per agent (`judge_sample_rate` in each YAML), so not every turn is judged — send a few messages if you don't see one.
2. If a turn ever shows **retried ×1**, that's the bounded retry loop: the critic failed the answer and the pipeline re-ran it once. Rare on these grounded prompts, but the badge is the proof it exists.
3. Click 👍/👎 on a reply (or `POST /feedback`), then check the Dashboard — the rating should land in the per-agent analytics.

## 12. Scenario H — fail-silent & feature flags

**Fail-soft check** (the app must degrade, not crash):

```powershell
docker stop maf-postgres
# http://localhost:8000/health  -> status "degraded", postgres error, but the server is UP
# Send a chat message -> still answers (no memory/persistence for that turn)
docker start maf-postgres
```

**Ablation check** (thesis-relevant — each subsystem can be switched off):
stop the backend, then restart it with a flag off, e.g.:

```powershell
$env:ENABLE_MEMORY = "false"
.\.venv\Scripts\python.exe -m uvicorn --env-file .env demo.shopping_assistant.app:app --reload
```

Re-run Scenario E — memory should no longer work, everything else unchanged. Same idea with
`ENABLE_EVALUATION` (eval badges disappear) and `ENABLE_LEARNING`. Remember to `Remove-Item Env:ENABLE_MEMORY` afterwards.

## 13. Scenario I — login & account isolation

The privacy layer: every account only ever sees its own data, and the enforcement is
**server-side** (the agent's tools are bound to your account — no prompt can cross it).

1. **Signed out:** open http://localhost:5173/chat — you land on the login page.
   `GET http://localhost:8000/shop/orders` without a token → **401**.
2. **Sign in as mohammad** (`mohammad@example.com` / `mohammad123`):
   - Storefront → orders shows **only** ORD-1005 and ORD-1006.
   - Chat: *"Where is my order ORD-1005?"* → shipped, DHL, `JD014600003RS034`.
   - Chat: *"Where is my order ORD-1001? Give me its tracking number."* (that's alice's)
     → **"not found on this account"** — the trace shows `order_api` was called, so the
     tool itself refused, not the model's goodwill.
3. **Conversations page:** shows only your own; opening another account's conversation
   URL directly returns **404**.
4. Sign in as **alice** and confirm the mirror image (sees ORD-1001/1004, not mohammad's).

---

## 14. Results checklist

- [ ] A1–A7: each prompt routed to the intended agent, correct tool called, answer matches the cheat sheet
- [ ] B1–B3: multiple agents in one turn, answers synthesized into a single coherent reply
- [ ] C1–C4: sensible routing reasons on ambiguous prompts; C4 falls back to `support_concierge`
- [ ] D1/D2: observed either an `ask_*` peer call in a trace or up-front multi-agent routing (note which)
- [ ] E: preferences recalled in a new conversation on the same account; never across accounts; `/dream` runs
- [ ] F: follow-up understood from conversation context
- [ ] G: eval scores visible; feedback lands in analytics
- [ ] H: Postgres down → degraded-but-alive; `ENABLE_MEMORY=false` → memory off, rest intact
- [ ] I: signed out → login page + 401s; signed in → only your own orders/conversations; the agent can't read another account's order

## Troubleshooting

- **Backend won't start / async errors on Windows:** always run uvicorn **with `--reload`** (works around the Proactor event-loop issue) and **with `--env-file .env`** (env vars aren't loaded automatically).
- **`401` from `/chat`, `/shop/orders`, or `/conversations`:** you're not signed in (or the token expired — they last 24h). Sign in again.
- **Provider errors in agent replies:** check `.env` has `OPENAI_API_KEY` and `DEFAULT_PROVIDER=openai`.
- **Login rejects seeded accounts after pulling new code:** the `accounts` table predates the `password_hash` column — reseed with `--reset`.
- **Frontend can't reach API:** backend must be on port 8000; CORS allows `http://localhost:5173` by default.
- **Weird/stale shop data:** reseed with `python -m demo.shopping_assistant.seed --reset`.
- **New DB columns not appearing:** `create_all` doesn't ALTER existing tables — drop the schema or use `--reset`.
