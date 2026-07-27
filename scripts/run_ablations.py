"""Ablation harness: replay the benchmark prompt set under several configurations.

Each configuration gets a clean store, its own backend process, and one labelled CSV, so
the runs differ only in the thing being ablated. The flags are the mechanism: every
subsystem is gated by an ENABLE_* variable, and the roster is a directory of YAML, so a
whole configuration is an environment file plus an app module.

Usage (Docker up, .env holding a provider key):

    python scripts/run_ablations.py                      # every configuration
    python scripts/run_ablations.py baseline monolith    # only these

Writes benchmarks/run-<stamp>-<label>.csv per configuration and prints a summary table.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:8000"
EMAIL = "mohammad@example.com"
PASSWORD = "mohammad123"

# label -> (app module, ENABLE_* overrides). The empty dict is "everything on".
CONFIGS: dict[str, tuple[str, dict[str, str]]] = {
    "baseline": ("demo.shopping_assistant.app", {}),
    "eval-off": ("demo.shopping_assistant.app", {"ENABLE_EVALUATION": "false"}),
    "memory-off": ("demo.shopping_assistant.app", {"ENABLE_MEMORY": "false"}),
    "learning-off": ("demo.shopping_assistant.app", {"ENABLE_LEARNING": "false"}),
    "dreaming-off": ("demo.shopping_assistant.app", {"ENABLE_DREAMING": "false"}),
    "monolith": ("demo.shopping_assistant.app_monolith", {}),
}


def _env_file(label: str, overrides: dict[str, str]) -> Path:
    """Write .env plus this configuration's overrides to a throwaway env file.

    uvicorn's --env-file wins over the parent process environment, so an override has to
    go into the file itself rather than be exported around the subprocess.
    """
    base = (REPO / ".env").read_text(encoding="utf-8") if (REPO / ".env").exists() else ""
    kept = [
        line
        for line in base.splitlines()
        if not any(line.strip().startswith(f"{k}=") for k in overrides)
    ]
    kept += [f"{k}={v}" for k, v in overrides.items()]
    path = REPO / f".env.ablation-{label}"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return path


def _reset_store() -> None:
    """Clean slate: reseed the shop and drop the benchmark account's memories.

    Several prompts drive write tools (a second return on one order takes a different
    path, address updates persist) and auto-memory accumulates across the prompt set, so
    without this a later configuration would be measured against a different store than
    the first one saw.
    """
    subprocess.run(
        [sys.executable, "-m", "demo.shopping_assistant.seed", "--reset"],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    from sqlalchemy import create_engine, text  # imported here: only this path needs it

    from backend.core.config import Settings

    dsn = Settings.from_env().database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    with create_engine(dsn).begin() as conn:
        for table in ("auto_memory", "dream_runs"):
            conn.execute(text(f"DELETE FROM {table} WHERE owner_id = :o"), {"o": EMAIL})


def _wait_for_health(timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=5):
                return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1.0)
    raise RuntimeError("backend did not become healthy in time")


def run_one(label: str) -> Path | None:
    """Reset, boot the backend for this configuration, replay the prompt set, shut down."""
    module, overrides = CONFIGS[label]
    print(f"\n{'=' * 70}\n{label}  ({module}, {overrides or 'all flags on'})\n{'=' * 70}")

    _reset_store()
    env_path = _env_file(label, overrides)
    # scripts/serve.py, not a bare uvicorn command: uvicorn would pick Windows'
    # ProactorEventLoop, async psycopg would refuse it, and every configuration would
    # then be measured with persistence, memory and learning silently disabled.
    server = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "scripts" / "serve.py"),
            "--app",
            f"{module}:app",
            "--env-file",
            str(env_path),
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "warning",
        ],
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health()
        from scripts.benchmark import run  # imported late: needs the repo root on sys.path

        return run(BASE_URL, EMAIL, PASSWORD, repeat=1, label=label)
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
        env_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="*", default=None, help=f"subset of {list(CONFIGS)}")
    args = parser.parse_args()
    labels = args.labels or list(CONFIGS)

    unknown = [label for label in labels if label not in CONFIGS]
    if unknown:
        parser.error(f"unknown configuration(s): {unknown}; known: {list(CONFIGS)}")

    written: dict[str, Path | None] = {}
    for label in labels:
        try:
            written[label] = run_one(label)
        except Exception as exc:  # noqa: BLE001 - one bad configuration must not lose the rest
            print(f"!! {label} failed: {exc}")
            written[label] = None

    print(f"\n{'=' * 70}\nablation runs written\n{'=' * 70}")
    for label, path in written.items():
        print(f"  {label:<14} {path if path else 'FAILED'}")


if __name__ == "__main__":
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    main()
