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
import contextlib
import os
import socket
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
    "routing-cache-off": (
        "demo.shopping_assistant.app",
        {"ENABLE_ROUTING_CACHE": "false"},
    ),
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

    Bounded, because seeding re-indexes the retrieval corpus and an unreachable Qdrant or
    embedding endpoint would otherwise hang the whole harness with nothing on stdout.
    """
    print("  resetting the store ...", flush=True)
    try:
        subprocess.run(
            [sys.executable, "-m", "demo.shopping_assistant.seed", "--reset"],
            cwd=REPO,
            check=True,
            capture_output=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("seed --reset did not finish within 300s") from exc
    from sqlalchemy import create_engine, text  # imported here: only this path needs it

    from backend.core.config import Settings

    dsn = Settings.from_env().database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    with create_engine(dsn).begin() as conn:
        for table in ("auto_memory", "dream_runs"):
            conn.execute(text(f"DELETE FROM {table} WHERE owner_id = :o"), {"o": EMAIL})
    _flush_routing_cache()


def _flush_routing_cache() -> None:
    """Drop cached routing decisions between configurations.

    Without this the comparison quietly rots: the first configuration populates the
    cache, and every later one answers the same forty prompts from it, so they look
    faster and cheaper for a reason that has nothing to do with what was ablated.
    """
    import redis  # imported here: only this path needs the sync client

    from backend.core.config import Settings

    try:
        client = redis.Redis.from_url(Settings.from_env().redis_url, decode_responses=True)
        keys = list(client.scan_iter(match="route:*", count=500))
        if keys:
            client.delete(*keys)
        print(f"  flushed {len(keys)} cached routing decisions", flush=True)
    except Exception as exc:  # noqa: BLE001 - no Redis means nothing to flush
        print(f"  routing cache not flushed ({exc})", flush=True)


def _wait_for_health(server: subprocess.Popen, log: Path, timeout: float = 300.0) -> None:
    """Block until /health answers, failing fast if the server died first.

    The import tree alone takes the better part of a minute on a cold start, so the
    budget is generous; the point of watching server.poll() is that a crash (a port
    already bound, a bad app path) should surface in seconds with its log, rather than
    looking identical to a slow boot for the whole timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            tail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
            raise RuntimeError(f"backend exited with code {server.returncode}\n{tail}")
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=5) as response:
                payload = response.read().decode()
            # A degraded boot would silently invalidate the whole comparison: with
            # Postgres down the graph runs memory as a no-op and nothing persists, so
            # every configuration would look like memory-off and learning-off.
            if '"postgres":"ok"' not in payload.replace(" ", ""):
                raise RuntimeError(f"backend booted degraded, refusing to measure: {payload}")
            # Health can be answered by a survivor on the same port while our own
            # process is still on its way down from a lost bind. Only trust the reply
            # once the process we launched is still alive to have produced it.
            if server.poll() is not None:
                tail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
                raise RuntimeError(
                    "another process answered /health; ours exited with code "
                    f"{server.returncode}\n{tail}"
                )
            return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(1.0)
    raise RuntimeError(f"backend did not become healthy within {timeout:.0f}s")


def run_one(label: str) -> Path | None:
    """Reset, boot the backend for this configuration, replay the prompt set, shut down."""
    module, overrides = CONFIGS[label]
    print(f"\n{'=' * 70}\n{label}  ({module}, {overrides or 'all flags on'})\n{'=' * 70}")

    # Refuse to start on an occupied port. A leftover server answers /health perfectly
    # well, so without this the freshly launched process loses the bind, dies, and the
    # benchmark silently measures whatever was already listening — which is how an
    # earlier run produced an "evaluation off" configuration that was still evaluating.
    if _port_in_use():
        raise RuntimeError(
            "port 8000 is already in use; refusing to run, the benchmark would measure "
            "the process already listening there rather than this configuration"
        )

    _reset_store()
    env_path = _env_file(label, overrides)
    log_path = REPO / f".ablation-{label}.log"
    log_handle = log_path.open("w", encoding="utf-8")
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
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    try:
        print("  waiting for the backend ...", flush=True)
        _wait_for_health(server, log_path)
        print("  replaying the prompt set ...", flush=True)
        from scripts.benchmark import run  # imported late: needs the repo root on sys.path

        return run(BASE_URL, EMAIL, PASSWORD, repeat=1, label=label)
    finally:
        server.terminate()
        try:
            server.wait(timeout=20)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        log_handle.close()
        env_path.unlink(missing_ok=True)
        # The port has to be free before the next configuration boots, and a killed
        # server can hold it briefly.
        _wait_for_port_free()


def _port_in_use() -> bool:
    """True if something is already listening on :8000."""
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(("127.0.0.1", 8000)) == 0


def _wait_for_port_free(timeout: float = 60.0) -> None:
    """Block until :8000 is free, so the next configuration can bind it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _port_in_use():
            return
        time.sleep(1.0)
    raise RuntimeError("port 8000 never freed; refusing to start the next configuration")


@contextlib.contextmanager
def _single_instance():
    """Refuse to run while another harness is running.

    Two copies racing is not a hypothetical: they reset the store under each other and
    fight over :8000, and the symptom is a run that simply stops producing output rather
    than anything that looks like an error. The lock file records the pid so a stale one
    from a killed run can be told apart from a live instance.
    """
    lock = REPO / ".ablation.lock"
    if lock.exists():
        owner = lock.read_text(encoding="utf-8").strip()
        if _pid_alive(owner):
            raise SystemExit(
                f"another ablation run is active (pid {owner}). Wait for it, or stop it "
                f"and delete {lock.name}."
            )
        print(f"  clearing a stale lock from pid {owner}", flush=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _pid_alive(pid: str) -> bool:
    if not pid.isdigit():
        return False
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        )
        return pid in out.stdout
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", nargs="*", default=None, help=f"subset of {list(CONFIGS)}")
    args = parser.parse_args()
    labels = args.labels or list(CONFIGS)

    unknown = [label for label in labels if label not in CONFIGS]
    if unknown:
        parser.error(f"unknown configuration(s): {unknown}; known: {list(CONFIGS)}")

    written: dict[str, Path | None] = {}
    with _single_instance():
        for label in labels:
            try:
                written[label] = run_one(label)
            except Exception as exc:  # noqa: BLE001 - one bad config must not lose the rest
                print(f"!! {label} failed: {exc}", flush=True)
                written[label] = None

    print(f"\n{'=' * 70}\nablation runs written\n{'=' * 70}")
    for label, path in written.items():
        print(f"  {label:<14} {path if path else 'FAILED'}")


if __name__ == "__main__":
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    main()
