import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool = True) -> bool:
    """Read a boolean feature flag from the environment (defaults to ``default``)."""
    raw = os.environ.get(name, "true" if default else "false")
    return raw.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class ProviderModels:
    fast: str
    standard: str
    deep: str


@dataclass(frozen=True)
class ModelTiers:
    # Tier -> exact model ID per provider. THE single swap surface: agent YAMLs
    # reference tier names (fast/standard/deep), never model IDs, so flipping
    # default_provider swaps the whole fleet. Anthropic IDs are authoritative;
    # OpenAI/Google IDs are best-available 2026 figures and MUST be verified
    # against the live API at integration (hence the VERIFY markers).
    anthropic: ProviderModels = field(
        default_factory=lambda: ProviderModels(
            fast="claude-haiku-4-5",
            standard="claude-sonnet-4-6",
            deep="claude-opus-4-8",
        )
    )
    openai: ProviderModels = field(
        default_factory=lambda: ProviderModels(
            fast="gpt-5.4-mini",  # VERIFY (post-cutoff)
            standard="gpt-5.4",  # VERIFY (post-cutoff)
            deep="gpt-5.5",  # VERIFY (post-cutoff; replaces phantom gpt-5-pro)
        )
    )
    google: ProviderModels = field(
        default_factory=lambda: ProviderModels(
            fast="gemini-3.1-flash-lite",  # VERIFY (post-cutoff)
            standard="gemini-3.5-flash",  # VERIFY (post-cutoff)
            deep="gemini-3.1-pro",  # VERIFY (post-cutoff)
        )
    )


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    openai_api_key: str | None
    google_api_key: str | None
    database_url: str
    redis_url: str
    app_env: str = "development"
    log_level: str = "INFO"
    default_provider: str = "openai"
    # Feature flags (Phase 7) — each gates one subsystem; flip any for ablation.
    enable_memory: bool = True        # Layer 2 auto-memory (load hints + extract)
    enable_evaluation: bool = True    # structural + LLM critic (+ retry)
    enable_learning: bool = True       # per-(agent,category) EMA scoring
    enable_coordinator: bool = True    # multi-part -> coordinator path
    enable_dreaming: bool = True       # Layer 4 consolidation
    max_delegation_depth: int = 2    # how deep agent-to-agent calls may nest (1 disables delegation)
    delegation_budget: int = 6       # max total agent runs per turn (hard stop)
    dream_min_memories: int = 8      # Layer 4: min stored memories before a consolidation run
    dream_interval_hours: int = 24   # Layer 4: min hours between consolidation runs
    model_tiers: ModelTiers = field(default_factory=ModelTiers)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
            google_api_key=os.environ.get("GOOGLE_API_KEY") or None,
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5433/multi_agent",
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            app_env=os.environ.get("APP_ENV", "development"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            default_provider=os.environ.get("DEFAULT_PROVIDER", "anthropic"),
            enable_memory=_flag("ENABLE_MEMORY"),
            enable_evaluation=_flag("ENABLE_EVALUATION"),
            enable_learning=_flag("ENABLE_LEARNING"),
            enable_coordinator=_flag("ENABLE_COORDINATOR"),
            enable_dreaming=_flag("ENABLE_DREAMING"),
            max_delegation_depth=int(os.environ.get("MAX_DELEGATION_DEPTH", "2")),
            delegation_budget=int(os.environ.get("DELEGATION_BUDGET", "6")),
            dream_min_memories=int(os.environ.get("DREAM_MIN_MEMORIES", "8")),
            dream_interval_hours=int(os.environ.get("DREAM_INTERVAL_HOURS", "24")),
        )

    def model_id_for_tier(self, tier: str) -> str:
        """Resolve a tier name (fast/standard/deep) to the model ID for the active provider."""
        try:
            provider_models = getattr(self.model_tiers, self.default_provider)
        except AttributeError:
            raise ValueError(f"unknown provider {self.default_provider!r}") from None
        try:
            return getattr(provider_models, tier)
        except AttributeError:
            raise ValueError(f"unknown model tier {tier!r}; expected fast|standard|deep") from None
