"""Summarize the labelled ablation CSVs into one comparison table.

Reads the newest run per label from benchmarks/ and prints routing, evaluation, latency
and token figures side by side, plus a LaTeX tabular body for the report.

    python scripts/summarize_ablations.py
    python scripts/summarize_ablations.py --latex
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

# Running a file puts scripts/ on sys.path, not the repo root; this is a flat app, so
# put the root back to make `scripts.benchmark` importable.
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BENCHMARKS = REPO / "benchmarks"

# Presentation order: baseline first, then one row per ablated subsystem, monolith last.
ORDER = ["baseline", "eval-off", "memory-off", "learning-off", "dreaming-off", "monolith"]


def _newest_per_label() -> dict[str, Path]:
    """The most recent CSV for each label (files are named run-<stamp>-<label>.csv)."""
    found: dict[str, Path] = {}
    for path in sorted(BENCHMARKS.glob("run-*.csv")):
        stem = path.stem
        for label in ORDER:
            if stem.endswith(f"-{label}"):
                found[label] = path  # sorted ascending, so the last one wins
    return found


def _floats(rows: list[dict], key: str) -> list[float]:
    out = []
    for row in rows:
        value = row.get(key, "")
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _multi_domain_prompts() -> set[str]:
    """The prompts written to span two domains, where fanning out is the right answer.

    Exact-match is the honest metric on a single-domain prompt and the wrong one here:
    a second agent on "track my order and tell me your damage policy" is the router
    working, not missing. Reported separately so neither number flatters the other.
    """
    from scripts.benchmark import PROMPTS

    return {case["prompt"] for case in PROMPTS[-5:]}


def summarize(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {}

    multi = _multi_domain_prompts()
    single_rows = [r for r in rows if r.get("prompt") not in multi]
    multi_rows = [r for r in rows if r.get("prompt") in multi]

    total = len(rows)
    hits = sum(1 for r in rows if r.get("routing_hit") == "True")
    exact = sum(1 for r in rows if r.get("routing_exact") == "True")
    single_exact = sum(1 for r in single_rows if r.get("routing_exact") == "True")
    fanned_out = sum(1 for r in multi_rows if "+" in (r.get("routed_agents") or ""))
    judged = [r for r in rows if r.get("eval_stage") in ("critic", "structural")]
    passes = sum(1 for r in judged if r.get("eval_pass") == "True")
    scores = _floats(judged, "eval_score")
    latencies = _floats(rows, "latency_ms")
    # Older CSVs called this column "tokens" before it was renamed for accuracy.
    tokens = _floats(rows, "agent_tokens") or _floats(rows, "tokens")
    retries = sum(int(float(r.get("retries") or 0)) for r in rows)
    confidences = _floats(rows, "confidence")

    return {
        "file": path.name,
        "turns": total,
        "recall": hits / total,
        "exact": exact / total,
        "single_exact": (single_exact / len(single_rows)) if single_rows else None,
        "single_n": len(single_rows),
        "fanned_out": fanned_out,
        "multi_n": len(multi_rows),
        "confidence": statistics.mean(confidences) if confidences else None,
        "judged": len(judged),
        "pass_rate": (passes / len(judged)) if judged else None,
        "mean_score": statistics.mean(scores) if scores else None,
        "retries": retries,
        "median_latency": statistics.median(latencies) if latencies else None,
        "mean_latency": statistics.mean(latencies) if latencies else None,
        "tokens": int(sum(tokens)),
    }


def _fmt(value, spec="", dash="-"):
    return dash if value is None else format(value, spec)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latex", action="store_true", help="emit LaTeX tabular rows")
    args = parser.parse_args()

    found = _newest_per_label()
    if not found:
        print(f"no labelled ablation CSVs in {BENCHMARKS}")
        raise SystemExit(1)

    results = {label: summarize(found[label]) for label in ORDER if label in found}

    header = (
        f"{'config':<14}{'turns':>6}{'recall':>8}{'1-agent':>9}{'fanout':>8}{'conf':>7}"
        f"{'judged':>8}{'pass':>7}{'score':>7}{'retry':>7}{'med ms':>8}{'tokens':>9}"
    )
    print(header)
    print("-" * len(header))
    for label, s in results.items():
        if not s:
            continue
        fanout = f"{s['fanned_out']}/{s['multi_n']}"
        print(
            f"{label:<14}{s['turns']:>6}{s['recall']:>7.0%}"
            f"{_fmt(s['single_exact'], '.0%'):>9}{fanout:>8}"
            f"{_fmt(s['confidence'], '.3f'):>7}{s['judged']:>8}"
            f"{_fmt(s['pass_rate'], '.0%'):>7}{_fmt(s['mean_score'], '.2f'):>7}"
            f"{s['retries']:>7}{_fmt(s['median_latency'], '.0f'):>8}{s['tokens']:>9,}"
        )
    print(
        "\n1-agent = exact match on the 35 single-domain prompts;"
        " fanout = how many of the 5 multi-domain prompts routed to >1 agent."
    )

    base = results.get("baseline")
    if base:
        print("\ndeltas vs baseline (median latency, agent tokens):")
        for label, s in results.items():
            if label == "baseline" or not s:
                continue
            dl = s["median_latency"] - base["median_latency"]
            dt = s["tokens"] - base["tokens"]
            print(
                f"  {label:<14}{dl:>+9.0f} ms  ({dl / base['median_latency']:>+6.1%})"
                f"{dt:>+10,} tok  ({dt / base['tokens']:>+6.1%})"
            )

    if args.latex:
        print("\n% --- LaTeX tabular body ---")
        for label, s in results.items():
            if not s:
                continue
            print(
                f"\\texttt{{{label}}} & {s['turns']} & {s['recall']:.0%} & "
                f"{_fmt(s['pass_rate'], '.0%')} & {_fmt(s['mean_score'], '.2f')} & "
                f"{s['retries']} & {_fmt(s['median_latency'], '.0f')} & "
                f"{s['tokens']:,} \\\\".replace("%", r"\%")
            )


if __name__ == "__main__":
    main()
