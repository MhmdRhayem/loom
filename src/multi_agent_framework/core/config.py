import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderModels:
    fast: str
    standard: str
    deep: str


@dataclass(frozen=True)
class ModelTiers:
    anthropic: ProviderModels = field(
        default_factory=lambda: ProviderModels(
            fast="claude-haiku-4-5-20251001",
            standard="claude-sonnet-4-6",
            deep="claude-opus-4-7",
        )
    )
    openai: ProviderModels = field(
        default_factory=lambda: ProviderModels(
            fast="gpt-5-mini",
            standard="gpt-5",
            deep="gpt-5-pro",
        )
    )


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    openai_api_key: str | None
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
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://postgres:postgres@localhost:5433/multi_agent",
            ),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            app_env=os.environ.get("APP_ENV", "development"),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
            default_provider=os.environ.get("DEFAULT_PROVIDER", "anthropic"),
        )
