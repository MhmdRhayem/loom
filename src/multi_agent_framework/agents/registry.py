"""Agent registry: load YAML agent definitions and look them up by name/capability.

This is framework-level and demo-agnostic: it loads from whatever directory it is
given (the shopping-assistant demo passes ``demo/shopping_assistant/definitions``).
Definitions reference model *tier names* (fast/standard/deep), never provider model
IDs — those resolve in ``core/config.py``. The registry validates structure at load
time so a malformed roster fails fast instead of at request time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Tier names the framework understands; resolved to provider model IDs in core/config.py.
VALID_TIERS = {"fast", "standard", "deep"}

# Fallback targets that are terminal sinks rather than agents (e.g. escalate to a human).
TERMINAL_TARGETS = {"human_handoff"}

REQUIRED_FIELDS = ("name", "description", "capabilities", "tools", "model", "fallback_agent")


class RegistryError(ValueError):
    """Raised when an agent definition is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class AgentDefinition:
    """One agent's declarative config, parsed from a YAML file."""

    name: str
    description: str
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    model: str  # tier name: fast | standard | deep
    fallback_agent: str
    max_tokens: int = 1024
    eval_rubric: tuple[str, ...] = ()
    judge_sample_rate: float = 1.0
    memory_scope: str = "owner"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentDefinition":
        return cls(
            name=data["name"],
            description=str(data.get("description", "")).strip(),
            capabilities=tuple(data.get("capabilities") or ()),
            tools=tuple(data.get("tools") or ()),
            model=data["model"],
            fallback_agent=data["fallback_agent"],
            max_tokens=int(data.get("max_tokens", 1024)),
            eval_rubric=tuple(data.get("eval_rubric") or ()),
            judge_sample_rate=float(data.get("judge_sample_rate", 1.0)),
            memory_scope=str(data.get("memory_scope", "owner")),
        )


class AgentRegistry:
    """Loads agent definitions from a directory and indexes them for lookup."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}

    @classmethod
    def from_directory(
        cls, path: str | Path, terminal_targets: set[str] | None = None
    ) -> "AgentRegistry":
        registry = cls()
        registry.load_directory(path, terminal_targets)
        return registry

    def load_directory(self, path: str | Path, terminal_targets: set[str] | None = None) -> None:
        """Load every ``*.yaml`` / ``*.yml`` in ``path``, validating as we go."""
        directory = Path(path)
        if not directory.is_dir():
            raise RegistryError(f"definitions directory not found: {directory}")

        files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
        if not files:
            raise RegistryError(f"no agent definition files (*.yaml) found in {directory}")

        for file in files:
            data = yaml.safe_load(file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise RegistryError(f"{file.name}: top-level YAML must be a mapping")
            self._register(file, data)

        self._validate_references(terminal_targets or TERMINAL_TARGETS)

    def _register(self, file: Path, data: dict[str, Any]) -> None:
        missing = [k for k in REQUIRED_FIELDS if k not in data]
        if missing:
            raise RegistryError(f"{file.name}: missing required field(s): {', '.join(missing)}")

        defn = AgentDefinition.from_dict(data)

        if defn.name != file.stem:
            raise RegistryError(
                f"{file.name}: name '{defn.name}' must match the filename stem '{file.stem}'"
            )
        if defn.name in self._agents:
            raise RegistryError(f"duplicate agent name '{defn.name}' ({file.name})")
        if defn.model not in VALID_TIERS:
            raise RegistryError(
                f"{defn.name}: model tier '{defn.model}' not one of {sorted(VALID_TIERS)}"
            )
        if not defn.tools:
            raise RegistryError(f"{defn.name}: must declare at least one tool")
        if not defn.capabilities:
            raise RegistryError(f"{defn.name}: must declare at least one capability")
        if not 0.0 <= defn.judge_sample_rate <= 1.0:
            raise RegistryError(
                f"{defn.name}: judge_sample_rate {defn.judge_sample_rate} must be in [0.0, 1.0]"
            )

        self._agents[defn.name] = defn

    def _validate_references(self, terminal_targets: set[str]) -> None:
        names = set(self._agents)
        for defn in self._agents.values():
            target = defn.fallback_agent
            if target not in names and target not in terminal_targets:
                raise RegistryError(
                    f"{defn.name}: fallback_agent '{target}' is neither a known agent "
                    f"nor a terminal target {sorted(terminal_targets)}"
                )

    # ----- lookup -----

    def get(self, name: str) -> AgentDefinition:
        try:
            return self._agents[name]
        except KeyError:
            raise RegistryError(f"unknown agent: {name!r}") from None

    def names(self) -> list[str]:
        return sorted(self._agents)

    def all(self) -> list[AgentDefinition]:
        return [self._agents[name] for name in self.names()]

    def by_capability(self, capability: str) -> list[AgentDefinition]:
        return [defn for defn in self.all() if capability in defn.capabilities]

    def router_menu(self) -> list[dict[str, Any]]:
        """Compact menu the router shows the LLM: name, description, capabilities."""
        return [
            {
                "name": defn.name,
                "description": defn.description,
                "capabilities": list(defn.capabilities),
            }
            for defn in self.all()
        ]

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, name: object) -> bool:
        return name in self._agents
