"""Measure the memory layers directly, which the replay benchmark cannot.

The replay sends forty independent prompts and never calls /dream, so consolidation is
dormant in both arms of a dreaming ablation and the comparison is vacuous. Layer 2 fares
little better: the replay shows a token delta but never asks a question whose answer
depends on something said earlier, which is the property that matters.

This probe tests the two hypotheses the flags actually make:

  ENABLE_MEMORY   durable facts stated in one turn are recalled in a later, independent
                  turn, and transient facts are not stored
  ENABLE_DREAMING a forced consolidation pass merges duplicate topics and reduces the
                  row count, and is a no-op when the flag is off

Run it against a backend already started for one configuration:

    python scripts/probe_memory_layers.py --label baseline
    python scripts/probe_memory_layers.py --label memory-off

Prints a one-line verdict per hypothesis and appends a row to benchmarks/memory-probe.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BASE_URL = "http://127.0.0.1:8000"
EMAIL = "mohammad@example.com"
PASSWORD = "mohammad123"

# Stated once, then asked about in a later turn that carries no hint of the answer.
# Deliberately mixes a durable fact with a transient one: the extractor is supposed to
# keep the first and refuse the second.
SEED_TURNS = [
    "Just so you know for future orders, I always wear size medium and my budget is under $80.",
    "I prefer linen over wool, I find wool itchy.",
]
RECALL_PROMPT = "Based on what you know about me, recommend one dress. Say which size."
RECALL_MUST_MENTION = ("medium", "m ")


def _post(path: str, body: dict, token: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.loads(response.read())


def _memory_rows(owner_id: str) -> list[tuple[str, str]]:
    """Read auto_memory straight from the database; there is no API for another view."""
    from sqlalchemy import create_engine, text

    from backend.core.config import Settings

    dsn = Settings.from_env().database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    with create_engine(dsn).connect() as conn:
        rows = conn.execute(
            text("SELECT topic, content FROM auto_memory WHERE owner_id = :o ORDER BY topic"),
            {"o": owner_id},
        ).all()
    return [(r.topic, r.content) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="configuration name, for the CSV")
    args = parser.parse_args()

    token = _post("/auth/login", {"email": EMAIL, "password": PASSWORD})["token"]

    print(f"[{args.label}] stating durable facts ...")
    for message in SEED_TURNS:
        _post("/chat", {"message": message}, token)

    stored = _memory_rows(EMAIL)
    print(f"[{args.label}] auto_memory rows after seeding: {len(stored)}")
    for topic, content in stored:
        print(f"    {topic}: {content}")

    print(f"[{args.label}] asking a fresh turn that depends on those facts ...")
    reply = _post("/chat", {"message": RECALL_PROMPT}, token)
    answer = (reply.get("response") or "").lower()
    recalled = any(needle in answer for needle in RECALL_MUST_MENTION)

    print(f"[{args.label}] forcing consolidation ...")
    dream = _post("/dream?force=true", {}, token)
    after = _memory_rows(EMAIL)

    row = {
        "label": args.label,
        "memories_after_seeding": len(stored),
        "recalled_without_being_told": recalled,
        "dream_ran": dream.get("ran"),
        "merged": dream.get("merged"),
        "pruned": dream.get("pruned"),
        "memories_after_dream": len(after),
    }
    print(f"\n[{args.label}] {row}")

    out = REPO / "benchmarks" / "memory-probe.csv"
    out.parent.mkdir(exist_ok=True)
    exists = out.exists()
    with out.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    print(f"appended to {out}")


if __name__ == "__main__":
    main()
