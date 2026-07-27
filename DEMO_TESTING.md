# Demo Testing Guide — Shopping Assistant

A hands-on script for testing the 8-agent e-commerce demo yourself and checking the results.
Every prompt below is grounded in the seeded database, so you can verify the answers are **real
data**, not hallucinations. Work through the scenarios in order — together they cover every
feature of the system: routing, tools, multi-agent, delegation, memory, evaluation, fail-silent,
account isolation, the merchant/admin surfaces, semantic retrieval (RAG), token streaming, and
the interface features around them.

---

## 1. Start everything

```powershell
cd "C:\Mhmd\M2 AI and Data Engineering\Final Project\multi-agent-framework"

docker compose up -d                                    # Postgres :5433 + Redis :6379 + Qdrant :6333
.\.venv\Scripts\python.exe scripts\init_db.py           # database schema (idempotent, self-migrating)
.\.venv\Scripts\python.exe -m demo.shopping_assistant.seed   # shop data + semantic index (idempotent)

# Backend (keep this terminal open) — .env already has OPENAI_API_KEY + DEFAULT_PROVIDER=openai
.\.venv\Scripts\python.exe scripts\serve.py

# Frontend (second terminal)
cd frontend
npm run dev          # http://localhost:5173
```

**Sanity check before testing:** open http://localhost:8000/health — expect
`{"status":"ok","components":{"redis":"ok","postgres":"ok"}}`. (Qdrant isn't in the health
payload on purpose: if it's down, semantic retrieval silently falls back to keyword search —
Scenario M shows the difference.)

To wipe and reseed the shop data at any point:
`.\.venv\Scripts\python.exe -m demo.shopping_assistant.seed --reset`

**Sign in first.** Chat, orders, and conversations are per-account now — the app sends you
to the login page until you sign in. There are **three roles** (the login page shows the
three main accounts with their credentials — click one to fill the form). Seeded accounts:

| Email | Password | Role | Notes |
|---|---|---|---|
| `mohammad@example.com` | `mohammad123` | client | ORD-1005 (shipped, DHL), ORD-1006 (processing) |
| `merchant@example.com` | `merchant123` | **merchant** | Owns the **Atelier** shop — gets the Products page + the `shop_manager` agent (Scenario K) |
| `admin@example.com` | `admin123` | **admin** | Dashboard (admin-only), full catalog, Users page (Scenario L) |
| `alice@example.com` | `password123` | client | ORD-1001 (shipped, UPS), ORD-1004 (cancelled) |
| `bob@example.com` | `password123` | client | ORD-1002 (delivered — has the RMA) |
| `carol@example.com` | `password123` | client | ORD-1003 (processing) |

New sign-ups from the login page are always **clients**; roles are assigned by the admin
on the Users page. You can only see (and ask the assistant about) **your own** orders,
returns, and conversations — that's Scenario I below.

---

## 2. Where to check results

| Surface | What you see |
|---|---|
| **Chat page** (http://localhost:5173) | The reply **streams in token by token** (with a blinking caret and a **Stop** button); before tokens arrive, the typing indicator narrates the pipeline stage. Under each answer: a collapsible **trace** — routed category + confidence, the router's reason, each agent's run with its tool calls and token count, the eval score/pass badge, a retry badge if the critic forced a retry — plus 👍/👎 feedback. The sidebar keeps a recent-chats list with AI-generated titles. |
| **Storefront** | Public catalog with filters and generated product art; signed in, the header's **Cart button (live badge)** opens a slide-over drawer: per-line **quantity steppers**, remove, subtotal, checkout with the declined-payment retry flow, and "added to cart" toasts. Order rows **expand in place** to show their line items. |
| **Conversations page** | Every persisted conversation and turn — proof that persistence works; conversations resume from the sidebar or URL. |
| **Dashboard page** | **Admin only** (`admin@example.com`) — aggregate analytics: per-agent volume and eval scores, routing distribution, timeseries, memory stats and dream runs. The `/analytics/*` endpoints answer **403** for everyone else. |
| **Products page** | Merchant/admin only — shop stats, sales rollup, product CRUD, customer orders containing the shop's items, and the **pending AI changes** queue (Scenario K). |
| **Everywhere** | A **sun/moon toggle** (sidebar footer, and top-right on the login page) switches light/dark theme — persisted, OS-preference default. On a narrow window the sidebar collapses behind a **burger button**. |
| **Raw API** | `POST http://localhost:8000/chat` returns the full trace as JSON (`current_agents`, `routing_confidence`, `routing_reason`, `query_category`, `tool_calls`, `agent_runs`, `eval`, `retry_count`); `POST /chat/stream` is the same turn as SSE (`stage`, `token`, `done`, `error` events). |

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

## 3. The 8 agents

The roster the router picks from. Each agent is defined by one YAML file in
[demo/shopping_assistant/definitions/](demo/shopping_assistant/definitions/) — the router routes on the
`description` field alone, so these descriptions *are* the routing surface.

| Agent | Tier | Tools | What it does |
|---|---|---|---|
| `order_tracking` | fast | `order_api`, `list_my_orders` | "Where is my order?" — order status, shipment tracking, and delivery estimates for already-placed orders. Read-only: never modifies anything. |
| `catalog_advisor` | standard | `product_db`, `price_api` | All pre-purchase catalog help: search, recommend, filter, and compare products, answer product questions, and find the best price including deals and coupons. |
| `fit_stylist` | standard | `style_engine`, `product_db` | Sizing and style advice tailored to the shopper: size recommendations, fit diagnosis, outfit suggestions. Carries `product_db` so its outfit advice names real catalog items (with live price/stock) rather than generic fashion tips. Kept separate from `catalog_advisor` on purpose — their boundary is the router stress test in Scenario C1. |
| `checkout_payments` | deep | `cart_api`, `payment_api`, `price_api` | Problems completing a purchase: stock checks, cart and checkout failures, payment/promo/billing issues at the point of sale (`price_api` lets it actually verify coupons). Works on the **in-flight cart**, not placed orders — that's what keeps it crisp against `order_tracking`. |
| `returns_refunds` | standard | `returns_api` | Returns, refunds, and exchanges under the store's return policy — post-purchase reversals only, not tracking or new purchases. |
| `account_assistant` | fast | `account_api` | Account and profile help: login issues, password/security resets, updating profile, addresses, and settings. |
| `support_concierge` | standard | `faq_kb`, `ticket_api` | The catch-all: policy/FAQ answers, complaints, clarifying ambiguous requests, and escalation to a human with full context. The safe destination when no specialist fits. |
| `shop_manager` | standard | `list_shop_products`, `propose_product_*`, `list_pending_changes` | **Merchant-only** — proposes catalog changes (add/update/remove products) for the merchant's own shop. Proposals are queued as pending; nothing is applied until the merchant approves it on the Products page. Hidden from clients/admins: not in their router menu, `/agents`, or `ask_*` delegation tools. |

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

**Order line items** (what the merchant's Orders view is built from):

| Order | Items | Shops involved |
|---|---|---|
| ORD-1001 | 1× SKU-1001 Aurora Midi Dress @ $49 | Atelier |
| ORD-1002 | 1× SKU-1004 High-Rise Slim Jeans @ $58 + 1× SKU-1002 Linen Wrap Dress @ $62 | **Everyday + Atelier (mixed on purpose)** — the Atelier merchant sees only the dress line, never the jeans |
| ORD-1003 | 1× SKU-1005 Merino Wool Sweater @ $89 | Atelier |
| ORD-1004 | 1× SKU-1003 Everyday Cotton Tee @ $18 | Everyday |
| ORD-1005 | 1× SKU-1006 Classic Trench Coat @ $145 | Atelier |
| ORD-1006 | 1× SKU-1002 Linen Wrap Dress @ $62 | Atelier |

**Returns:** ORD-1002 has an approved return `RMA-55872`, 12 days left in window, refund to original payment, next step = drop at FedEx with emailed label.

**Cart & payment:** carts are per-account. **mohammad@example.com**'s cart holds 1× Aurora Midi Dress (SKU-1001, $49) and his payment is **declined — card_expired** (the payment-diagnosis scenario; checkout offers "use a different card"). Other accounts start with empty carts.

**Products (highlights):** Aurora Midi Dress $49 · Linen Wrap Dress $62 · Everyday Cotton Tee $18 **(out of stock)** · High-Rise Slim Jeans $58 · Merino Wool Sweater $89 **(15% off this week)** · Classic Trench Coat $145 · Leather Ankle Boots $120 · Silk Scarf $35 · Canvas Tote Bag $28 **(out of stock)** · Lounge Jogger Pants $42 **(10% off this week)**.

**Coupons:** `SAVE10` (10% any order) · `WELCOME20` (20%, min $75) · `DRESS15` (15% on SKU-1001) · `SHOES25` (25% on SKU-1007 boots).

**Policies (FAQ):** a 14-entry handbook — free shipping over $50 (3–5 days; express $9.99, 1–2 days) · international shipping $19.99 flat, 7–14 days · 30-day returns, unworn with tags · refunds within 2 business days of inspection · free size/color exchanges · damaged items: photo within 7 days, free replacement or refund · order changes/cancellation free until shipped · one coupon per order (stacks with site-wide deals) · 7-day price adjustments · Visa/Mastercard/Amex/PayPal/Apple Pay · cards stored by the processor, never on our servers · size guide per product page · password reset via "Forgot password" link · data never sold, export/delete on request.

**Semantic retrieval (RAG):** `faq_kb` and `product_db` search **by meaning** — the corpora are embedded into **Qdrant** (`docker compose up -d` starts it; see `VECTOR_DB_CHOICE.md`), and `python -m demo.shopping_assistant.seed` indexes them (needs `OPENAI_API_KEY`; model via `EMBEDDING_MODEL`, default `openai:text-embedding-3-small`; server via `QDRANT_URL`, default `http://localhost:6333`). Try *"can I get my money back if it doesn't fit?"* (no keyword overlap with any entry — should cite `policy/returns`) or *"something elegant for a formal garden party"* (should surface dresses). With Qdrant down or no API key, both tools fall back to keyword matching.

**Fit engine (stub):** always recommends **size M**, says items "run slightly large; size down if between sizes", and suggests ankle boots + a thin belt.

---

## 5. Scenario A — one prompt per agent (routing precision)

Send each prompt in a **new chat**. Check the trace: the right agent, the right tool, and an answer matching the cheat sheet.

> Orders, carts, and tickets belong to accounts now: run **A1 signed in as alice**,
> **A4 signed in as mohammad** (his payment is the seeded declined one), and
> **A5 signed in as bob** (asking from the wrong account correctly answers "not found
> on this account" — that's Scenario I). The other prompts work from any account.
> While a turn runs, the typing indicator narrates the pipeline stage
> ("asking order_tracking…", "reviewing the answer…"), then the answer **types out live**,
> token by token — that's the /chat/stream SSE feed. **Stop** aborts mid-generation; if the
> critic forces a retry you'll see "improving the answer…" and the draft restart.

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
.\.venv\Scripts\python.exe scripts\serve.py
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
4. **Chat resume is owner-checked too:** POST `/chat` with *another account's*
   `conversation_id` (grab one as alice, replay it as bob via the raw-API snippet) — the
   response comes back with a **fresh** conversation id: the foreign history is never
   loaded into the model's context, and nothing is written into the other account's thread.
5. Sign in as **alice** and confirm the mirror image (sees ORD-1001/1004, not mohammad's).
6. **Chat rate limit:** a script hammering `/chat` gets **429 "too many messages"** after
   20 requests in a minute per account (Redis-backed, fails open if Redis is down —
   availability guards fail open, identity guards fail closed).

## 14. Scenario J — buy something (the full loop)

1. **Sign in** (or use the **Create account** tab on the login page to register a fresh user).
2. Storefront → products → **Add to cart** on anything in stock; a toast confirms it and
   the header's cart badge counts up. Open the **cart drawer** and try the **+/− steppers**.
3. Ask the assistant *"is there a coupon for the Leather Ankle Boots?"* — it answers with a
   real code (`SHOES25`). Type that code into the drawer's **promo code** field.
4. In the drawer → **Checkout**. As mohammad the first attempt fails with the seeded
   *declined — card_expired* payment; click **Use a different card** and it goes through.
5. You get a new order id (ORD-1007+) and the success line shows the discount actually
   applied (`SHOES25, -25% off $…`) — the code the assistant quoted is the price charged.
   A wrong or ineligible code fails the checkout with a reason instead of quietly charging
   full price. Check the orders tab, then ask the assistant *"where is my order ORD-100X?"*
   — the order you just placed is real, tracked data.
6. Bonus: five wrong passwords for the same email within ten minutes → login answers
   **429 too many failed attempts** (Redis-backed rate limiting).

To produce the thesis's benchmark numbers (routing accuracy, eval scores, latency, tokens):
```powershell
.\.venv\Scripts\python.exe scripts\benchmark.py                # CSV lands in benchmarks/
.\.venv\Scripts\python.exe scripts\benchmark.py --label eval-off   # after restarting with ENABLE_EVALUATION=false
```

## 15. Scenario K — merchant (own shop + AI proposals with approval)

Sign in as **merchant** (`merchant@example.com` / `merchant123`). The sidebar gains a
**Products** link; everything is scoped to the **Atelier** shop server-side.

1. **Products page:** the stats strip shows Atelier-only numbers; the catalog table lists
   only Atelier products (no shop column — merchants don't pick shops).
2. **Orders tab:** ORD-1001, 1002, 1003, 1005, 1006 appear (they contain Atelier items);
   **ORD-1002 shows only the Linen Wrap Dress line** — the Everyday jeans in the same
   order are invisible, and no customer emails are shown anywhere.
3. **Manual CRUD:** add a product with the form (it appears instantly in the Storefront
   with generated art), edit its price, then delete it. Editing an Everyday SKU via the
   API answers **404** — cross-shop existence never leaks.
4. **AI proposal loop (the headline):** in **Chat**, say
   *"Add a red summer dress for $45 to my shop."*
   - The trace shows `shop_manager` with a `propose_product_create` call, and the reply
     says the change is **pending your approval**. The catalog is untouched so far.
   - Products page → **Pending AI changes** shows the proposal with its payload;
     click **Approve** → the product appears in the catalog and the public Storefront.
     (Reject discards it without touching the catalog.)
5. **Visibility check:** sign in as **mohammad** (client) and ask the chat to
   *"add a product to the shop"* — it can never reach `shop_manager` (the agent isn't in
   the client's router menu, `/agents` roster, or any `ask_*` tool); expect
   `support_concierge` or `catalog_advisor` instead. `GET /manage/products` with a client
   token → **403**.

## 16. Scenario L — admin (dashboard, full catalog, users)

Sign in as **admin** (`admin@example.com` / `admin123`). The sidebar gains **Products**,
**Dashboard**, and **Users**.

1. **Dashboard:** loads normally as admin. As any other account,
   `GET http://localhost:8000/analytics/overview` with the Bearer token → **403**
   (and the Dashboard link isn't even rendered).
2. **Products page:** all shops, with a shop column and a shop selector when creating;
   the orders tab shows every order with all lines **and** the customer email.
3. **Users page:** every account with its role. Promote a fresh sign-up to merchant
   (a shop must be selected), demote them back, delete a test account. Your own row is
   locked — self role-change and self-delete answer **400** (no locking yourself out).
4. **Revocation is immediate:** delete a test account while it is signed in in another
   browser — its very next request answers **401 "account no longer exists"**, even though
   its token is formally valid for 24h (existence and role are read fresh per request).

## 17. Scenario M — semantic retrieval (RAG over Qdrant)

`faq_kb` (policy handbook) and `product_db` (catalog) retrieve **by meaning**: the corpora
are embedded into two Qdrant collections (`loom_faq`, `loom_products`) at seed time, each
query is embedded per call, and the closest documents ground the answer. Why Qdrant and not
Milvus/Chroma/Weaviate/pgvector is documented in [VECTOR_DB_CHOICE.md](VECTOR_DB_CHOICE.md).

1. **Paraphrase with zero keyword overlap:** ask *"can I get my money back if it doesn't
   fit?"* — the trace shows `faq_kb`, and the answer cites the 30-day returns policy
   (source `policy/returns`). No word of that question appears in the policy entry: this
   is the query keyword matching cannot answer.
2. **Descriptive product search:** *"something elegant for a formal garden party"* —
   `product_db` returns dresses ranked first (each with a similarity score in the trace),
   despite zero matching keywords in any product name.
3. **The score floor:** ask the concierge something wildly off-corpus (*"how do I fix my
   carburetor?"*) — a weak best match falls **below the floor** and the tool answers with
   the honest "couldn't find that in our help center" fallback rather than citing noise.
4. **Graceful degradation:** `docker stop maf-qdrant`, ask A7 again — it still answers
   (keyword fallback), just less clever on paraphrases. `docker start maf-qdrant` restores
   semantic search with no restart of the backend.
5. **Idempotent indexing:** re-run the seed — it prints *"Semantic index (Qdrant) up to
   date."* Edit one FAQ answer in `seed.py`, reseed — exactly the changed documents are
   re-embedded (stale-content diffing via the stored payload text).

---

## 18. Results checklist

- [ ] A1–A7: each prompt routed to the intended agent, correct tool called, answer matches the cheat sheet
- [ ] B1–B3: multiple agents in one turn, answers synthesized into a single coherent reply
- [ ] C1–C4: sensible routing reasons on ambiguous prompts; C4 falls back to `support_concierge`
- [ ] D1/D2: observed either an `ask_*` peer call in a trace or up-front multi-agent routing (note which)
- [ ] E: preferences recalled in a new conversation on the same account; never across accounts; `/dream` runs
- [ ] F: follow-up understood from conversation context
- [ ] G: eval scores visible; feedback lands in analytics
- [ ] H: Postgres down → degraded-but-alive; `ENABLE_MEMORY=false` → memory off, rest intact
- [ ] I: signed out → login page + 401s; signed in → only your own orders/conversations; the agent can't read another account's order
- [ ] J: register → add to cart → checkout (declined → new card → placed) → agent tracks the new order; benchmark CSV produced
- [ ] K: merchant sees only Atelier products/order-lines; AI proposal stays pending until approved, then goes live; clients can never reach `shop_manager`
- [ ] L: dashboard 200 as admin / 403 as anyone else; admin manages all shops; Users page promotes/deletes with self-lockout blocked; deleted account revoked on its next request
- [ ] M: paraphrase FAQ + descriptive product search answered semantically with sources/scores; off-corpus query falls back honestly; Qdrant down → keyword fallback, no crash
- [ ] UI: reply streams token-by-token with Stop; cart drawer + steppers + toasts; order rows expand to line items; theme toggle persists; sidebar collapses on narrow windows

## Troubleshooting

- **Backend starts but `/health` says `postgres: disabled`:** you ran a bare `uvicorn` command. On Windows uvicorn picks the ProactorEventLoop unless it is supervising a subprocess, and async psycopg refuses that loop, so the app boots with Postgres unreachable and serves every request with persistence, memory, learning and analytics silently off. Use `python scripts/serve.py`, which hands uvicorn a selector loop. (`uvicorn --reload` also happens to work, because `--reload` flips uvicorn's own loop choice, but do not rely on a dev flag for this.) The launcher also loads `.env` for you.
- **`401` from `/chat`, `/shop/orders`, or `/conversations`:** you're not signed in (or the token expired — they last 24h). Sign in again.
- **Provider errors in agent replies:** check `.env` has `OPENAI_API_KEY` and `DEFAULT_PROVIDER=openai`.
- **Login rejects seeded accounts after pulling new code:** run the seed once (`python -m demo.shopping_assistant.seed`) — it patches missing columns (`role`, `shop`) in place, adds the merchant/admin accounts, and backfills order line items. `--reset` is only needed for a truly broken schema.
- **Dashboard is missing / `403` from `/analytics/*`:** analytics are admin-only — sign in as `admin@example.com` / `admin123`.
- **Chat never routes to `shop_manager`:** that agent only exists for merchant accounts — sign in as `merchant@example.com`.
- **Frontend can't reach API:** backend must be on port 8000; CORS allows `http://localhost:5173` by default.
- **Semantic answers feel like plain keyword search:** the index probably isn't built — check `docker compose ps` shows `maf-qdrant` up, then re-run the seed and look for "Semantic index (Qdrant): embedded N documents". Changing `EMBEDDING_MODEL` to a different dimension requires deleting the two collections once (`curl -X DELETE http://localhost:6333/collections/loom_faq` and `.../loom_products`), then reseeding.
- **`429` from `/chat`:** you hit the per-account limit (20 messages/min) — wait a minute.
- **Weird/stale shop data:** reseed with `python -m demo.shopping_assistant.seed --reset`.
