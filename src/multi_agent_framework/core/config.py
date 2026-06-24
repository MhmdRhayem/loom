import os
from dataclasses import dataclass, field


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
            fast="gpt-5.4-mini",   # VERIFY (post-cutoff)
            standard="gpt-5.4",    # VERIFY (post-cutoff)
            deep="gpt-5.5",        # VERIFY (post-cutoff; replaces phantom gpt-5-pro)
        )
    )
    google: ProviderModels = field(
        default_factory=lambda: ProviderModels(
            fast="gemini-3.1-flash-lite",   # VERIFY (post-cutoff)
            standard="gemini-3.5-flash",    # VERIFY (post-cutoff)
            deep="gemini-3.1-pro",          # VERIFY (post-cutoff)
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
    default_provider: str = "anthropic"
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
        )
