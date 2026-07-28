"""Benchmark harness: replay a fixed prompt set against a running backend.

Measures the numbers the thesis's evaluation chapter needs — routing accuracy,
confidence, eval scores, latency, and token spend — and writes them to a CSV so
runs are comparable (e.g. with feature flags toggled for ablation).

Usage (backend running on :8000, DB seeded):

    python scripts/benchmark.py
    python scripts/benchmark.py --repeat 3 --label memory-off

Start the backend with a flag off for an ablation run, e.g.:
    $env:ENABLE_EVALUATION = "false"; uvicorn ... ; python scripts/benchmark.py --label eval-off

Reset between labelled runs, or the comparison is not clean: several prompts drive
write tools (a second return on the same order takes a different path, address updates
persist) and auto-memory accumulates across the set, so a later run sees a different
store than the first one did. Before each run:

    python -m demo.shopping_assistant.seed --reset
    (and clear the benchmark account's rows from auto_memory)
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Grounded in the seeded database; expected agent = the roster's intended owner
# of each request (DEMO_TESTING.md scenarios, adapted to the test account:
# mohammad@example.com owns ORD-1005/ORD-1006 and the seeded declined-payment cart).
# Five prompts per client-visible agent plus five multi-agent turns; for the
# multi-agent rows `expect` is the primary owner and the hit counts if it is
# among the routed agents.
PROMPTS = [
    # order_tracking
    {"prompt": "Where is my order ORD-1005?", "expect": "order_tracking"},
    {"prompt": "What orders do I have?", "expect": "order_tracking"},
    {"prompt": "Has ORD-1006 shipped yet?", "expect": "order_tracking"},
    {
        "prompt": "What's the tracking number for my trench coat order?",
        "expect": "order_tracking",
    },
    {"prompt": "When will ORD-1006 arrive?", "expect": "order_tracking"},
    # catalog_advisor
    {"prompt": "Do you have any dresses under $60?", "expect": "catalog_advisor"},
    {
        "prompt": "What's the price of the Merino Wool Sweater? Any deals on it?",
        "expect": "catalog_advisor",
    },
    {
        "prompt": "Compare the Linen Wrap Dress and the Aurora Midi Dress for me.",
        "expect": "catalog_advisor",
    },
    {"prompt": "Is the Canvas Tote Bag in stock?", "expect": "catalog_advisor"},
    {
        "prompt": "Is there a coupon code for the Leather Ankle Boots?",
        "expect": "catalog_advisor",
    },
    # fit_stylist
    {
        "prompt": "I'm between sizes for the Linen Wrap Dress - which size should I take?",
        "expect": "fit_stylist",
    },
    {"prompt": "What would you style with the Classic Trench Coat?", "expect": "fit_stylist"},
    {"prompt": "Do the High-Rise Slim Jeans run large or small?", "expect": "fit_stylist"},
    {
        "prompt": "Suggest an outfit for a job interview from your catalog.",
        "expect": "fit_stylist",
    },
    {
        "prompt": "I'm usually a medium - will the Merino Wool Sweater fit me?",
        "expect": "fit_stylist",
    },
    # checkout_payments
    {
        "prompt": "My payment keeps getting declined at checkout - what's wrong?",
        "expect": "checkout_payments",
    },
    {"prompt": "What's in my cart right now?", "expect": "checkout_payments"},
    {"prompt": "Does the DRESS15 coupon work on my cart?", "expect": "checkout_payments"},
    {"prompt": "Why won't my checkout go through?", "expect": "checkout_payments"},
    {
        "prompt": "Can I pay with a different card after my card was declined?",
        "expect": "checkout_payments",
    },
    # returns_refunds
    {"prompt": "I want to return my order ORD-1005.", "expect": "returns_refunds"},
    {
        "prompt": "I want to exchange the dress from ORD-1006 for a size S.",
        "expect": "returns_refunds",
    },
    {"prompt": "What's the status of my refund?", "expect": "returns_refunds"},
    {
        "prompt": "My sweater arrived damaged - I want my money back.",
        "expect": "returns_refunds",
    },
    {"prompt": "Start a return for my order ORD-1006.", "expect": "returns_refunds"},
    # account_assistant
    {"prompt": "I forgot my password - how do I reset it?", "expect": "account_assistant"},
    {"prompt": "How do I change my shipping address?", "expect": "account_assistant"},
    {"prompt": "Update my email address on my account.", "expect": "account_assistant"},
    {
        "prompt": "I think someone else logged into my account - help!",
        "expect": "account_assistant",
    },
    {"prompt": "Change the phone number on my profile.", "expect": "account_assistant"},
    # support_concierge
    {"prompt": "Which payment methods do you accept?", "expect": "support_concierge"},
    {
        "prompt": "Do you ship internationally, and how much does it cost?",
        "expect": "support_concierge",
    },
    {"prompt": "How long does standard shipping take?", "expect": "support_concierge"},
    {
        "prompt": "I have a complaint about your customer service - I want to speak to a human.",
        "expect": "support_concierge",
    },
    {
        "prompt": "Is my card information stored on your servers?",
        "expect": "support_concierge",
    },
    # multi-agent turns (expect = primary owner)
    {
        "prompt": "Where is order ORD-1006, and can I still return it if the dress doesn't fit?",
        "expect": "order_tracking",
    },
    {
        "prompt": "Is the Everyday Cotton Tee back in stock? Also, I need to update my shipping address.",
        "expect": "catalog_advisor",
    },
    {
        "prompt": "What's in my cart right now, and does the DRESS15 coupon apply to it?",
        "expect": "checkout_payments",
    },
    {
        "prompt": "Before I retry my payment, is the dress in my cart still in stock, and is there a coupon for it?",
        "expect": "checkout_payments",
    },
    {
        "prompt": "Track ORD-1005 and tell me your policy if it arrives damaged.",
        "expect": "order_tracking",
    },
]


def _post(base_url: str, path: str, body: dict, token: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


def run(base_url: str, email: str, password: str, repeat: int, label: str) -> Path:
    token = _post(base_url, "/auth/login", {"email": email, "password": password})["token"]

    rows: list[dict] = []
    for iteration in range(1, repeat + 1):
        for case in PROMPTS:
            started = time.perf_counter()
            reply = _post(base_url, "/chat", {"message": case["prompt"]}, token)
            latency_ms = int((time.perf_counter() - started) * 1000)

            agents = reply.get("current_agents") or []
            eval_result = reply.get("eval") or {}
            rows.append(
                {
                    "iteration": iteration,
                    "prompt": case["prompt"],
                    "expected_agent": case["expect"],
                    "routed_agents": "+".join(agents),
                    # Two metrics, because they answer different questions: recall is
                    # right for the 5 multi-agent rows, but on the 35 single-agent rows
                    # a router that returned every agent would score 100% on it.
                    "routing_hit": case["expect"] in agents,
                    "routing_exact": agents == [case["expect"]],
                    "confidence": reply.get("routing_confidence"),
                    "category": reply.get("query_category"),
                    "eval_stage": eval_result.get("stage"),
                    "eval_score": eval_result.get("score"),
                    "eval_pass": eval_result.get("pass"),
                    "retries": reply.get("retry_count", 0),
                    "latency_ms": latency_ms,
                    # agent_tokens is the specialists' share; the turn_* columns are
                    # the whole bill, including the router, critic, synthesizer, memory
                    # extractor and titler, which agent_runs never sees. Input and
                    # output are separated because they are priced several times apart.
                    "agent_tokens": sum(
                        r.get("tokens") or 0 for r in reply.get("agent_runs") or []
                    ),
                    "turn_tokens": (reply.get("usage") or {}).get("total_tokens"),
                    "input_tokens": (reply.get("usage") or {}).get("input_tokens"),
                    "output_tokens": (reply.get("usage") or {}).get("output_tokens"),
                    "cached_input_tokens": (reply.get("usage") or {}).get("cached_input_tokens"),
                    "model_calls": (reply.get("usage") or {}).get("model_calls"),
                }
            )
            hit = "OK  " if case["expect"] in agents else "MISS"
            print(f"  {hit} [{'+'.join(agents) or '-':<35}] {latency_ms:>6}ms  {case['prompt']}")

    if not rows:
        print("no rows collected (empty prompt set or --repeat 0); nothing written")
        raise SystemExit(1)

    out_dir = Path("benchmarks")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"run-{stamp}{'-' + label if label else ''}.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    hits = sum(1 for r in rows if r["routing_hit"])
    exact = sum(1 for r in rows if r["routing_exact"])
    latencies = [r["latency_ms"] for r in rows]
    scores = [r["eval_score"] for r in rows if isinstance(r["eval_score"], (int, float))]
    confidences = [r["confidence"] for r in rows if isinstance(r["confidence"], (int, float))]
    print()
    print(f"routing recall   : {hits}/{len(rows)} ({hits / len(rows):.0%}) expected agent present")
    print(f"routing exact    : {exact}/{len(rows)} ({exact / len(rows):.0%}) expected agent alone")
    print(f"avg confidence   : {statistics.mean(confidences):.2f}" if confidences else "")
    print(f"avg eval score   : {statistics.mean(scores):.2f}" if scores else "")
    print(f"latency (median) : {statistics.median(latencies)}ms")
    agent_tok = sum(r["agent_tokens"] for r in rows)
    turn_tok = sum(r["turn_tokens"] or 0 for r in rows)
    calls = sum(r["model_calls"] or 0 for r in rows)
    print(f"agent tokens     : {agent_tok} (the specialists' share)")
    print(f"turn tokens      : {turn_tok} across {calls} model calls (the whole bill)")
    if turn_tok:
        print(f"plumbing share   : {(turn_tok - agent_tok) / turn_tok:.0%} of tokens")
    print(f"csv              : {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the benchmark prompt set.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", default="mohammad@example.com")
    parser.add_argument("--password", default="mohammad123")
    parser.add_argument("--repeat", type=int, default=1, help="Passes over the prompt set.")
    parser.add_argument("--label", default="", help="Tag for the CSV name (e.g. 'eval-off').")
    args = parser.parse_args()
    run(args.base_url, args.email, args.password, args.repeat, args.label)


if __name__ == "__main__":
    main()
