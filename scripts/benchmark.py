"""Benchmark harness: replay a fixed prompt set against a running backend.

Measures the numbers the thesis's evaluation chapter needs — routing accuracy,
confidence, eval scores, latency, and token spend — and writes them to a CSV so
runs are comparable (e.g. with feature flags toggled for ablation).

Usage (backend running on :8000, DB seeded):

    python scripts/benchmark.py
    python scripts/benchmark.py --repeat 3 --label memory-off

Start the backend with a flag off for an ablation run, e.g.:
    $env:ENABLE_EVALUATION = "false"; uvicorn ... ; python scripts/benchmark.py --label eval-off
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
# of each request (Scenario A of DEMO_TESTING.md, adapted to the test account).
PROMPTS = [
    {"prompt": "Where is my order ORD-1005?", "expect": "order_tracking"},
    {"prompt": "What orders do I have?", "expect": "order_tracking"},
    {"prompt": "Do you have any dresses under $60?", "expect": "catalog_advisor"},
    {
        "prompt": "I'm between sizes for the Linen Wrap Dress - which size should I take?",
        "expect": "fit_stylist",
    },
    {
        "prompt": "My payment keeps getting declined at checkout - what's wrong?",
        "expect": "checkout_payments",
    },
    {"prompt": "I want to return my order ORD-1005.", "expect": "returns_refunds"},
    {"prompt": "I forgot my password - how do I reset it?", "expect": "account_assistant"},
    {"prompt": "Which payment methods do you accept?", "expect": "support_concierge"},
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
                    "routing_hit": case["expect"] in agents,
                    "confidence": reply.get("routing_confidence"),
                    "category": reply.get("query_category"),
                    "eval_stage": eval_result.get("stage"),
                    "eval_score": eval_result.get("score"),
                    "eval_pass": eval_result.get("pass"),
                    "retries": reply.get("retry_count", 0),
                    "latency_ms": latency_ms,
                    "tokens": sum(r.get("tokens") or 0 for r in reply.get("agent_runs") or []),
                }
            )
            hit = "OK  " if case["expect"] in agents else "MISS"
            print(f"  {hit} [{'+'.join(agents) or '-':<35}] {latency_ms:>6}ms  {case['prompt']}")

    out_dir = Path("benchmarks")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"run-{stamp}{'-' + label if label else ''}.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    hits = sum(1 for r in rows if r["routing_hit"])
    latencies = [r["latency_ms"] for r in rows]
    scores = [r["eval_score"] for r in rows if isinstance(r["eval_score"], (int, float))]
    confidences = [r["confidence"] for r in rows if isinstance(r["confidence"], (int, float))]
    print()
    print(f"routing accuracy : {hits}/{len(rows)} ({hits / len(rows):.0%})")
    print(f"avg confidence   : {statistics.mean(confidences):.2f}" if confidences else "")
    print(f"avg eval score   : {statistics.mean(scores):.2f}" if scores else "")
    print(f"latency (median) : {statistics.median(latencies)}ms")
    print(f"total tokens     : {sum(r['tokens'] for r in rows)}")
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
